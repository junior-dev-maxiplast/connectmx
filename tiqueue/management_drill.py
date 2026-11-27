"""Detalhe ("Expandir dados") dos gráficos e mapas do Painel Gerencial.

Cada ponto abre as linhas por trás dele, montadas das mesmas células (cliente × representante ×
grupo × mês) que o painel usa (`management_intel`, base em cache): as somas do detalhe fecham com
o número do gráfico. A projeção e as notas da bolha da matriz consultam o ERP (só leitura).
"""

from collections import defaultdict
from datetime import date, timedelta

from . import commercial_dna as dna
from . import management_intel as mgmt
from .chart_drill import BILLING_GROUP_BY, billing_columns, billing_rows

ZERO = dna.ZERO


class UnknownPoint(ValueError):
    """Gráfico ou ponto que o detalhe do Painel Gerencial não conhece."""


def _out(value, digits=2):
    return None if value is None else round(float(value), digits)


def _money(value):
    return dna._money(value, compact=False)


def _month_start(key):
    return date(key // 12, key % 12 + 1, 1)


def _customer(customers, code, customer_url):
    info = customers.get(code) or {}
    place = info.get("place") or ""
    return {
        "customer": info.get("name") or f"Cliente {code}",
        "customer_sub": " · ".join(part for part in (str(code), place if place != "/" else "") if part),
        "customer_url": customer_url(code) if customer_url and code else None,
    }


CUSTOMER_COLUMN = {"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"}
EMPTY_CELL = {"net": ZERO, "tsi": ZERO, "kg": ZERO, "cost": ZERO, "profit": ZERO}


def _result_columns(with_group=False, with_before=False):
    """Colunas das listas de clientes (ou cliente × grupo) com faturamento, lucro e margem."""
    columns = [CUSTOMER_COLUMN]
    if with_group:
        columns.append({"key": "group", "label": "Grupo"})
    columns += [
        {"key": "net", "label": "Faturamento", "type": "money", "total": True, "strong": True},
        {"key": "kg", "label": "Kg", "type": "kg", "total": True},
        {"key": "price", "label": "R$/kg", "type": "dec", "digits": 2, "ratio": ["net", "kg"]},
        {"key": "profit", "label": "Lucro bruto", "type": "money", "total": True},
        {"key": "margin", "label": "Margem", "type": "pct", "ratio": ["profit", "tsi"]},
        {"key": "tsi", "label": "Sem impostos", "type": "money", "hidden": True},
    ]
    if with_before:
        columns += [
            {"key": "net_before", "label": "Faturamento antes", "type": "money", "total": True},
            {"key": "growth", "label": "Cresc.", "type": "pct", "digits": 0},
        ]
    return columns


def _with_before(row, before_net):
    """Faturamento do mesmo recorte no ano anterior e o crescimento da linha."""
    row["net_before"] = _out(before_net) if before_net else None
    row["growth"] = _out(row["net"] / float(before_net) - 1, 4) if before_net and row["net"] is not None else None
    return row


def _result_row(item, base_row=None):
    row = dict(base_row or {})
    row.update({
        "net": _out(item["net"]), "kg": _out(item["kg"], 1), "price": _out(item["net"] / item["kg"]) if item["kg"] else None,
        "profit": _out(item["profit"]), "tsi": _out(item["tsi"]),
        "margin": _out(item["profit"] / item["tsi"], 4) if item["tsi"] else None,
        "__tone": "bad" if item["profit"] < 0 else None,
    })
    return row


def _totals_facts(items, label="Faturamento"):
    net = sum((item["net"] for item in items), ZERO)
    tsi = sum((item["tsi"] for item in items), ZERO)
    profit = sum((item["profit"] for item in items), ZERO)
    kg = sum((item["kg"] for item in items), ZERO)
    margin = profit / tsi if tsi else None
    return [
        {"label": label, "value": _money(net), "note": dna._kg(kg)},
        {"label": "Lucro bruto", "value": _money(profit), "tone": "bad" if profit < 0 else None},
        {"label": "Margem", "value": mgmt.pct(margin) if margin is not None else "–"},
    ]


def _window_bases(base, window):
    """Base do período (só os meses escolhidos, se houver) e as fatias cliente × grupo dos dois anos."""
    scoped = mgmt.restrict_months(base, window["month_keys"]) if window.get("month_keys") else base
    current = mgmt.slice_cells(scoped, window["first_key"], window["last_key"], by=("customer", "group"))
    previous = mgmt.slice_cells(scoped, window["previous_first_key"], window["previous_last_key"], by=("customer", "group"))
    return scoped, current, previous


def _ranges(window):
    return f"antes = {window['previous_range_label']} · agora = {window['range_label']}"


# ---------------------------------------------------------------------------
# Pontes do faturamento e do lucro
# ---------------------------------------------------------------------------

def bridge_effects(previous, current, field):
    """Contribuição de cada célula (cliente, grupo) a cada etapa da ponte. As somas por etapa fecham
    `management_intel.revenue_bridge` (field "net") e `profit_bridge` (field "profit"): mesma divisão
    das células (`_bridge_parts`) e as mesmas fórmulas, aplicadas célula a célula."""
    previous_customers = {key[0] for key, item in previous.items() if item["net"] > 0}
    current_customers = {key[0] for key, item in current.items() if item["net"] > 0}
    lost = previous_customers - current_customers
    new = current_customers - previous_customers
    effects = defaultdict(list)
    common = []
    for key, item in previous.items():
        if key[0] in lost:
            effects["lost_customers"].append((key, item, None, -item[field]))
        elif key not in current:
            effects["lost_products"].append((key, item, None, -item[field]))
    for key, item in current.items():
        if key[0] in new:
            effects["new_customers"].append((key, None, item, item[field]))
        elif key not in previous:
            effects["new_products"].append((key, None, item, item[field]))
        else:
            common.append((key, previous[key], item))
    priced = [(key, old, now) for key, old, now in common if old["kg"] > 0 and now["kg"] > 0]
    for key, old, now in common:
        if not (old["kg"] > 0 and now["kg"] > 0):
            effects["other"].append((key, old, now, now[field] - old[field]))
    kg_before = sum((old["kg"] for _key, old, _now in priced), ZERO)
    average = sum((old[field] for _key, old, _now in priced), ZERO) / kg_before if kg_before else ZERO
    for key, old, now in priced:
        unit_before = old[field] / old["kg"]
        effects["volume"].append((key, old, now, (now["kg"] - old["kg"]) * average))
        effects["mix"].append((key, old, now, now["kg"] * unit_before - now["kg"] * average))
        if field == "net":
            effects["price"].append((key, old, now, now["net"] - now["kg"] * unit_before))
        else:
            effects["price"].append((key, old, now, now["kg"] * (now["tsi"] / now["kg"] - old["tsi"] / old["kg"])))
            effects["cost"].append((key, old, now, -now["kg"] * (now["cost"] / now["kg"] - old["cost"] / old["kg"])))
    return effects, average


def _bridge_detail(customers, window, previous, current, field, point, customer_url):
    measure = "Faturamento" if field == "net" else "Lucro bruto"
    unit_label = "R$/kg" if field == "net" else "Lucro/kg"
    if point in ("inicio", "fim"):
        cells = previous if point == "inicio" else current
        label = window["previous_range_label"] if point == "inicio" else window["range_label"]
        items = [(key, item) for key, item in cells.items() if item["net"] > 0]
        rows = [_result_row(item, {**_customer(customers, key[0], customer_url), "group": (key[1] or "Sem grupo").title()}) for key, item in items]
        return {
            "title": f"{measure} {label}",
            "subtitle": "Cada cliente × grupo de produto do período (a ponte compara cada um com ele mesmo no outro ano)",
            "summary": _totals_facts([item for _key, item in items]) + [{"label": "Clientes", "value": str(len({key[0] for key, _item in items}))}],
            "sections": [{"key": "cells", "label": "Cliente × grupo", "columns": _result_columns(with_group=True), "rows": rows,
                          "sort": {"key": "net" if field == "net" else "profit", "dir": "desc"}, "groupBy": ["customer", "group"],
                          "empty": "Nenhum faturamento no período."}],
        }
    bridge = (mgmt.revenue_bridge if field == "net" else mgmt.profit_bridge)(previous, current)
    step = next((item for item in bridge["steps"] if item["key"] == point), None)
    if step is None:
        raise UnknownPoint("Etapa da ponte desconhecida.")
    effects, average = bridge_effects(previous, current, field)
    chosen = [(key, old, now, value) for key, old, now, value in effects.get(point, []) if value]
    rows = []
    for key, old, now, value in chosen:
        rows.append({
            **_customer(customers, key[0], customer_url),
            "group": (key[1] or "Sem grupo").title(),
            "before": _out(old[field]) if old else None,
            "after": _out(now[field]) if now else None,
            "kg_before": _out(old["kg"], 1) if old else None,
            "kg_after": _out(now["kg"], 1) if now else None,
            "unit_before": _out(old[field] / old["kg"]) if old and old["kg"] else None,
            "unit_after": _out(now[field] / now["kg"]) if now and now["kg"] else None,
            "effect": _out(value),
            "__tone": "bad" if value < 0 else None,
        })
    total = sum((value for _key, _old, _now, value in chosen), ZERO)
    summary = [
        {"label": step["label"], "value": ("+" if total > 0 else "") + _money(total), "note": step["detail"], "tone": "good" if total > 0 else "bad" if total < 0 else None},
        {"label": "Clientes", "value": str(len({key[0] for key, _old, _now, _value in chosen}))},
        {"label": "Cliente × grupo", "value": str(len(rows))},
    ]
    if point in ("volume", "mix"):
        summary.append({"label": f"{unit_label} médio antes", "value": dna._fator(average), "note": "a régua do volume e do mix"})
    return {
        "title": step["label"],
        "subtitle": f"{'Ponte do faturamento' if field == 'net' else 'Ponte do lucro bruto'} · {_ranges(window)}",
        "summary": summary,
        "sections": [{
            "key": "effect", "label": "Cliente × grupo", "rows": rows, "groupBy": ["customer", "group"],
            "sort": {"key": "effect", "dir": "asc" if total < 0 else "desc"}, "empty": "Nenhuma célula nesta etapa.",
            "columns": [
                CUSTOMER_COLUMN,
                {"key": "group", "label": "Grupo"},
                {"key": "before", "label": f"{measure} antes", "type": "money", "total": True},
                {"key": "after", "label": f"{measure} agora", "type": "money", "total": True},
                {"key": "kg_before", "label": "Kg antes", "type": "kg", "total": True},
                {"key": "kg_after", "label": "Kg agora", "type": "kg", "total": True},
                {"key": "unit_before", "label": f"{unit_label} antes", "type": "dec", "digits": 2, "ratio": ["before", "kg_before"]},
                {"key": "unit_after", "label": f"{unit_label} agora", "type": "dec", "digits": 2, "ratio": ["after", "kg_after"]},
                {"key": "effect", "label": "Efeito na ponte", "type": "money", "total": True, "strong": True},
            ],
        }],
    }


# ---------------------------------------------------------------------------
# Retenção (fluxo de receita por cliente)
# ---------------------------------------------------------------------------

RETENTION_STEPS = {
    "lost": "Clientes perdidos",
    "contraction": "Compraram menos",
    "expansion": "Compraram mais",
    "new": "Clientes novos",
}


def _retention_detail(customers, window, scoped, point, customer_url):
    before = {code: item["net"] for code, item in mgmt.slice_cells(scoped, window["previous_first_key"], window["previous_last_key"], by=("customer",)).items()}
    after = {code: item["net"] for code, item in mgmt.slice_cells(scoped, window["first_key"], window["last_key"], by=("customer",)).items()}
    if point == "inicio":
        codes, title = [code for code, value in before.items() if value > 0], f"Faturamento {window['previous_range_label']}"
    elif point == "fim":
        codes, title = [code for code, value in after.items() if value > 0], f"Faturamento {window['range_label']}"
    elif point in RETENTION_STEPS:
        codes, title = [code for code, _value in mgmt.retention(before, after)["flows"][point]], RETENTION_STEPS[point]
    else:
        raise UnknownPoint("Etapa da retenção desconhecida.")
    rows = []
    for code in codes:
        old, now = before.get(code, ZERO), after.get(code, ZERO)
        change = now - old if point in ("contraction", "expansion") else (-old if point == "lost" else now if point == "new" else None)
        rows.append({
            **_customer(customers, code, customer_url),
            "before": _out(old) if old > 0 else None, "after": _out(now) if now > 0 else None,
            "change": _out(change), "__tone": "bad" if change is not None and change < 0 else None,
        })
    total = sum((row["change"] or 0 for row in rows), 0) if point in RETENTION_STEPS else None
    columns = [CUSTOMER_COLUMN, {"key": "before", "label": "Faturamento antes", "type": "money", "total": True},
               {"key": "after", "label": "Faturamento agora", "type": "money", "total": True}]
    if point in RETENTION_STEPS:
        columns.append({"key": "change", "label": "Efeito na receita", "type": "money", "total": True, "strong": True})
    summary = [{"label": "Clientes", "value": str(len(rows))}]
    if total is not None:
        summary.insert(0, {"label": title, "value": ("+" if total > 0 else "") + _money(total), "tone": "good" if total > 0 else "bad"})
    else:
        summary.insert(0, {"label": "Faturamento", "value": _money(sum((before if point == "inicio" else after).get(code, ZERO) for code in codes))})
    sort_key = "change" if point in RETENTION_STEPS else ("before" if point == "inicio" else "after")
    return {
        "title": title,
        "subtitle": f"Retenção de receita por cliente · {_ranges(window)}",
        "summary": summary,
        "sections": [{"key": "customers", "label": "Clientes", "columns": columns, "rows": rows,
                      "sort": {"key": sort_key, "dir": "asc" if total is not None and total < 0 else "desc"}, "empty": "Nenhum cliente nesta etapa."}],
    }


# ---------------------------------------------------------------------------
# Matriz (bolha = cliente) e quadrantes
# ---------------------------------------------------------------------------

def _matrix_detail(customers, window, current, previous, code, customer_url, reps=None, groups=None):
    mine = {key[1]: item for key, item in current.items() if key[0] == code}
    before = {key[1]: item for key, item in previous.items() if key[0] == code}
    if not mine and not before:
        raise UnknownPoint("Cliente sem faturamento no período.")
    rows = []
    for group in sorted(set(mine) | set(before), key=lambda name: (mine.get(name) or {}).get("net", ZERO), reverse=True):
        item = mine.get(group) or EMPTY_CELL
        rows.append(_with_before(_result_row(item, {"group": (group or "Sem grupo").title()}), (before.get(group) or {}).get("net")))
    group_columns = [column for column in _result_columns(with_before=True) if column["key"] != "customer"]
    group_columns.insert(0, {"key": "group", "label": "Grupo"})
    # Notas do período (mesmo recorte de representante e grupo da página).
    months = window.get("month_keys") or list(range(window["first_key"], window["last_key"] + 1))
    start, end = _month_start(months[0]), _month_start(months[-1] + 1)
    sql = dna.BILLING_SQL.format(scope="N.CODCLI = :cliente")
    lines = [dna.billing_line(row) for row in dna._run({"billing": (sql, {"cliente": code, "start_date": start, "end_date": end})})["billing"]]
    wanted = set(months)
    lines = [
        line for line in lines
        if line["net"] > 0 and line["date"] and dna._month_key(line["date"]) in wanted
        and (not reps or line["rep_primary"] in reps) and (not groups or line["group"] in groups)
    ]
    lines.sort(key=lambda line: (line["date"], line["net"]), reverse=True)
    name = _customer(customers, code, None)["customer"]
    return {
        "title": name,
        "subtitle": f"Matriz de rentabilidade · {window['range_label']} · por grupo e as notas do período",
        "summary": _totals_facts(list(mine.values())) + [{"label": "Notas", "value": str(len({line['invoice'] for line in lines}))}],
        "sections": [
            {"key": "groups", "label": "Por grupo", "columns": group_columns, "rows": rows, "sort": {"key": "net", "dir": "desc"}, "empty": "Sem faturamento."},
            {"key": "billing", "label": "Notas do período", "columns": billing_columns(with_profit=True, with_customer=False),
             "rows": billing_rows(lines), "groupBy": [key for key in BILLING_GROUP_BY if key != "customer"], "empty": "Nenhuma nota no período."},
        ],
    }


def _quadrant_detail(customers, window, scoped, point, customer_url):
    if point not in mgmt.QUADRANTS:
        raise UnknownPoint("Quadrante desconhecido.")
    by_customer = mgmt.slice_cells(scoped, window["first_key"], window["last_key"], by=("customer",))
    company = mgmt.totals(by_customer)
    matrix = mgmt.profitability_matrix(by_customer, company["margin"])
    codes = [item["code"] for item in _all_points(by_customer, matrix) if item["quadrant"] == point]
    rows = [_result_row(by_customer[code], _customer(customers, code, customer_url)) for code in codes]
    label, description = mgmt.QUADRANTS[point]
    return {
        "title": label,
        "subtitle": f"{description} · {window['range_label']} · referências: faturamento mediano {mgmt.money(matrix['net_reference'])} e margem da empresa {mgmt.pct(matrix['margin_reference'])}",
        "summary": _totals_facts([by_customer[code] for code in codes]) + [{"label": "Clientes", "value": str(len(codes))}],
        "sections": [{"key": "customers", "label": "Clientes", "columns": _result_columns(), "rows": rows, "sort": {"key": "net", "dir": "desc"}, "empty": "Nenhum cliente neste quadrante."}],
    }


def _all_points(by_customer, matrix):
    """Quadrante de todos os clientes (a matriz desenha só os maiores)."""
    if matrix["net_reference"] is None:
        return []
    return [
        {"code": code, "quadrant": mgmt.quadrant(item["net"], item["profit"] / item["tsi"], matrix["net_reference"], matrix["margin_reference"])}
        for code, item in by_customer.items() if item["net"] > 0 and item["tsi"] > 0
    ]


# ---------------------------------------------------------------------------
# Grupos, mapa de calor da margem, sazonalidade e representantes
# ---------------------------------------------------------------------------

def _cells_by_customer(customers, cells, customer_url, title, subtitle, with_before=None):
    """Lista de clientes; com `with_before`, entram também os que só compraram no ano anterior."""
    rows = []
    codes = list(cells) + [code for code in (with_before or {}) if code not in cells]
    for code in codes:
        item = cells.get(code) or EMPTY_CELL
        before = (with_before or {}).get(code) or {}
        if item["net"] <= 0 and not before.get("net"):
            continue
        row = _result_row(item, _customer(customers, code, customer_url))
        if with_before is not None:
            _with_before(row, before.get("net"))
        rows.append(row)
    columns = _result_columns(with_before=with_before is not None)
    return {
        "title": title,
        "subtitle": subtitle,
        "summary": _totals_facts(list(cells.values())) + [{"label": "Clientes", "value": str(sum(1 for item in cells.values() if item["net"] > 0))}],
        "sections": [{"key": "customers", "label": "Clientes", "columns": columns, "rows": rows, "sort": {"key": "net", "dir": "desc"}, "empty": "Nenhum cliente."}],
    }


def _group_detail(customers, window, scoped, group, customer_url, month=None):
    first, last = (month, month) if month is not None else (window["first_key"], window["last_key"])
    cells = {key[0]: item for key, item in mgmt.slice_cells(scoped, first, last, by=("customer", "group")).items() if key[1] == group}
    if month is not None:
        return _cells_by_customer(customers, cells, customer_url, f"{group.title()} · {dna._month_label(month)}", "Mapa de calor da margem · clientes do grupo no mês")
    before = {key[0]: item for key, item in mgmt.slice_cells(scoped, window["previous_first_key"], window["previous_last_key"], by=("customer", "group")).items() if key[1] == group}
    return _cells_by_customer(customers, cells, customer_url, group.title(), f"Clientes do grupo · {_ranges(window)}", with_before=before)


def _rep_detail(customers, window, scoped, rep, names, customer_url):
    cells = {key[0]: item for key, item in mgmt.slice_cells(scoped, window["first_key"], window["last_key"], by=("customer", "rep")).items() if key[1] == rep}
    before = {key[0]: item for key, item in mgmt.slice_cells(scoped, window["previous_first_key"], window["previous_last_key"], by=("customer", "rep")).items() if key[1] == rep}
    name = names.get(rep) or (f"Representante {rep}" if rep is not None else "Sem representante")
    return _cells_by_customer(customers, cells, customer_url, name, f"Clientes do representante · {_ranges(window)}", with_before=before)


def _season_detail(customers, base, month, customer_url):
    cells = mgmt.slice_cells(base, month, month, by=("customer",))
    groups = mgmt.slice_cells(base, month, month, by=("group",))
    result = _cells_by_customer(customers, cells, customer_url, dna._month_label(month), "Faturamento do mês por cliente e por grupo")
    group_columns = [column for column in _result_columns() if column["key"] != "customer"]
    group_columns.insert(0, {"key": "group", "label": "Grupo"})
    result["sections"].append({
        "key": "groups", "label": "Por grupo", "columns": group_columns, "sort": {"key": "net", "dir": "desc"}, "empty": "Sem faturamento.",
        "rows": [_result_row(item, {"group": (group or "Sem grupo").title()}) for group, item in groups.items() if item["net"] > 0],
    })
    return result


# ---------------------------------------------------------------------------
# Projeção dos próximos meses (carteira a faturar e o já faturado do mês corrente)
# ---------------------------------------------------------------------------

# Itens da carteira como o BACKLOG_SQL do painel os soma (quantidade em aberto × preço do pedido).
FORECAST_ITEMS_SQL = """
SELECT PED.CODEMP AS EMPRESA, PED.CODFIL AS FILIAL, PED.NUMPED AS PEDIDO, ITEM.SEQIPD AS SEQUENCIA,
       PED.DATEMI AS EMISSAO, ITEM.DATENT AS ENTREGA, PED.CODCLI AS CLIENTE, C.NOMCLI AS NOME_CLIENTE,
       C.CIDCLI AS CIDADE, C.SIGUFS AS UF, PED.CODREP AS REPRESENTANTE,
       ITEM.CODPRO AS PRODUTO, PROD.DESPRO AS DESCRICAO_PRODUTO, FAM.DESFAM AS FAMILIA,
       NVL(ITEM.QTDABE, 0) * NVL(ITEM.PREUNI, 0) AS VALOR,
       NVL(ITEM.QTDABE, 0) * NVL(DER.PESBRU, 0) AS PESO
FROM E120PED PED
JOIN E120IPD ITEM ON ITEM.CODEMP = PED.CODEMP AND ITEM.CODFIL = PED.CODFIL AND ITEM.NUMPED = PED.NUMPED
JOIN E001TNS TNS ON TNS.CODEMP = ITEM.CODEMP AND TNS.CODTNS = ITEM.TNSPRO AND TNS.VENFAT = 'S'
LEFT JOIN E075PRO PROD ON PROD.CODEMP = ITEM.CODEMP AND PROD.CODPRO = ITEM.CODPRO
LEFT JOIN E012FAM FAM ON FAM.CODEMP = PROD.CODEMP AND FAM.CODFAM = PROD.CODFAM
LEFT JOIN E075DER DER ON DER.CODEMP = ITEM.CODEMP AND DER.CODPRO = ITEM.CODPRO AND DER.CODDER = ITEM.CODDER
LEFT JOIN E085CLI C ON C.CODCLI = PED.CODCLI
WHERE PED.CODCLI NOT IN (1001, 1002, 1004)
  AND ITEM.SITIPD IN (1, 2)
  AND NVL(ITEM.QTDABE, 0) > 0
  AND ITEM.DATENT >= :start_date AND ITEM.DATENT < :end_date
"""

FORECAST_COLUMNS = [
    {"key": "order", "label": "Pedido/item", "nowrap": True},
    CUSTOMER_COLUMN,
    {"key": "product", "label": "Produto", "wrap": True},
    {"key": "group", "label": "Grupo"},
    {"key": "rep", "label": "Representante"},
    {"key": "issue", "label": "Emissão", "type": "date", "nowrap": True},
    {"key": "delivery", "label": "Entrega", "type": "date", "nowrap": True, "badge": "status", "tone": "status_tone"},
    {"key": "kg", "label": "Kg em aberto", "type": "kg", "total": True},
    {"key": "value", "label": "Valor em aberto", "type": "money", "total": True, "strong": True},
]


def _forecast_detail(full, month, customer_url, today):
    current = dna._month_key(today)
    forecast = mgmt.forecast(full, today)
    row = next((item for item in forecast["rows"] if item["key"] == month), None)
    if row is None:
        raise UnknownPoint("Mês fora da projeção.")
    # O mês corrente leva também a carteira atrasada (como no gráfico).
    start = date(1900, 1, 1) if month == current else _month_start(month)
    items = dna._run({"items": (FORECAST_ITEMS_SQL, {"start_date": start, "end_date": _month_start(month + 1)})})["items"]
    rows = []
    total = ZERO
    for item in items:
        delivery = dna._date(item.get("entrega"))
        issue = dna._date(item.get("emissao"))
        code = dna._int(item.get("cliente"))
        rep = dna.primary_rep(item.get("representante"))
        value = dna._num(item.get("valor"))
        total += value
        late = bool(delivery and delivery < today)
        place = _place(item.get("cidade"), item.get("uf"))
        rows.append({
            "order": f"{dna._int(item.get('empresa'))}-{dna._int(item.get('filial'))}-{item.get('pedido')}/{item.get('sequencia')}",
            "customer": (item.get("nome_cliente") or "").strip() or f"Cliente {code}",
            "customer_sub": " · ".join(part for part in (str(code or ""), place) if part),
            "customer_url": customer_url(code) if customer_url and code else None,
            "product": (item.get("descricao_produto") or "").strip() or str(item.get("produto") or ""),
            "group": (dna.product_group(item.get("familia"), item.get("descricao_produto")) or "Sem grupo").title(),
            "rep": dna.REPRESENTATIVE_DISPLAY_NAMES.get(rep) or full["rep_names"].get(rep) or (f"Representante {rep}" if rep else "-"),
            "issue": issue.isoformat() if issue else None,
            "delivery": delivery.isoformat() if delivery else None,
            "status": "atrasado" if late else None, "status_tone": "bad" if late else None,
            "kg": _out(dna._num(item.get("peso")), 1), "value": _out(value),
            "__tone": "bad" if late else None,
        })
    sections = [{"key": "backlog", "label": "Carteira a faturar", "columns": FORECAST_COLUMNS, "rows": rows,
                 "sort": {"key": "delivery", "dir": "asc"}, "groupBy": ["customer", "group", "rep", "delivery"],
                 "empty": "Nenhum item em aberto com entrega neste mês."}]
    summary = [
        {"label": "Projeção", "value": _money(row["projected"]) if row["projected"] is not None else "–", "note": f"ano anterior {_money(row['last_year'])}" if row["last_year"] is not None else ""},
        {"label": "Carteira a faturar", "value": _money(total), "note": f"{len(rows)} item(ns){' · inclui a carteira atrasada' if month == current else ''}"},
    ]
    if month == current:
        sql = dna.BILLING_SQL.format(scope="1 = 1")
        lines = [dna.billing_line(item) for item in dna._run({"billing": (sql, {"start_date": _month_start(month), "end_date": today + timedelta(days=1)})})["billing"]]
        lines = sorted((line for line in lines if line["net"] > 0), key=lambda line: (line["date"], line["net"]), reverse=True)
        billed = sum((line["net"] for line in lines), ZERO)
        summary.insert(1, {"label": "Já faturado", "value": _money(billed), "note": "no mês, até hoje"})
        sections.append({"key": "billed", "label": "Já faturado no mês", "columns": billing_columns(with_rep=True), "rows": billing_rows(lines, customer_url),
                         "groupBy": BILLING_GROUP_BY + ["rep"], "empty": "Nenhuma nota no mês."})
    if row["projected"]:
        summary.append({"label": "Coberto pela carteira", "value": mgmt.pct(total / row["projected"])})
    return {
        "title": dna._month_label(month),
        "subtitle": "Projeção dos próximos meses · empresa toda, como o gráfico (a carteira não se divide por representante ou grupo)",
        "summary": summary,
        "sections": sections,
    }


def _place(city, state):
    city = (city or "").strip().title()
    state = (state or "").strip()
    return f"{city}/{state}" if city and state else city or state


# ---------------------------------------------------------------------------
# Cards de indicador (a fila "Indicadores" do topo)
# ---------------------------------------------------------------------------

# ponto -> (rótulo do card, campo de `mgmt.totals`, chave de ordenação da lista de clientes)
KPI_POINTS = {
    "faturamento": ("Faturamento líquido", "net", "net"),
    "lucro": ("Lucro bruto", "profit", "profit"),
    "margem": ("Margem bruta", "margin", "net"),
    "volume": ("Volume", "kg", "kg"),
    "preco": ("Preço médio", "price_kg", "net"),
    "custo": ("Custo médio (CPV)", "cost_kg", "cost"),
    "lucro-kg": ("Lucro bruto por kg", "profit_kg", "profit"),
    "nrr": ("Retenção de receita (NRR)", None, None),
    "clientes": ("Clientes ativos", None, None),
    "dependencia": ("Dependência dos 10 maiores", None, None),
}


def _kpi_value(field, totals):
    value = totals.get(field)
    if value is None:
        return "–"
    if field == "margin":
        return mgmt.pct(value)
    if field == "kg":
        return dna._kg(value)
    if field in ("price_kg", "cost_kg", "profit_kg"):
        return f"{dna._money(value, compact=False)}/kg"
    return _money(value)


def _kpi_columns():
    columns = [column for column in _result_columns(with_before=True)]
    at = next(index for index, column in enumerate(columns) if column["key"] == "profit")
    columns[at:at] = [
        {"key": "cost", "label": "CPV", "type": "money", "total": True},
        {"key": "cost_kg", "label": "CPV/kg", "type": "dec", "digits": 2, "ratio": ["cost", "kg"]},
    ]
    at = next(index for index, column in enumerate(columns) if column["key"] == "margin")
    columns.insert(at, {"key": "profit_kg", "label": "Lucro/kg", "type": "dec", "digits": 2, "ratio": ["profit", "kg"]})
    return columns


def _kpi_row(item, base_row):
    row = _result_row(item, base_row)
    row["cost"] = _out(item["cost"])
    row["cost_kg"] = _out(item["cost"] / item["kg"]) if item["kg"] else None
    row["profit_kg"] = _out(item["profit"] / item["kg"]) if item["kg"] else None
    return row


def _kpi_detail(customers, window, scoped, point, customer_url):
    if point not in KPI_POINTS:
        raise UnknownPoint("Indicador desconhecido.")
    label, field, sort_key = KPI_POINTS[point]
    now_cells = mgmt.slice_cells(scoped, window["first_key"], window["last_key"], by=("customer",))
    before_cells = mgmt.slice_cells(scoped, window["previous_first_key"], window["previous_last_key"], by=("customer",))
    if point == "nrr":
        return _nrr_detail(customers, window, now_cells, before_cells, customer_url)
    if point == "clientes":
        return _active_detail(customers, window, now_cells, before_cells, customer_url)
    if point == "dependencia":
        return _dependency_detail(customers, window, now_cells, customer_url)
    now, before = mgmt.totals(now_cells), mgmt.totals(before_cells)
    rows = []
    for code in list(now_cells) + [code for code in before_cells if code not in now_cells]:
        item = now_cells.get(code) or EMPTY_CELL
        old = before_cells.get(code) or {}
        if not item["net"] and not item["kg"] and not old.get("net"):
            continue
        rows.append(_with_before(_kpi_row(item, _customer(customers, code, customer_url)), old.get("net")))
    group_now = mgmt.slice_cells(scoped, window["first_key"], window["last_key"], by=("group",))
    group_before = mgmt.slice_cells(scoped, window["previous_first_key"], window["previous_last_key"], by=("group",))
    group_columns = [column for column in _kpi_columns() if column["key"] != "customer"]
    group_columns.insert(0, {"key": "group", "label": "Grupo"})
    group_rows = [
        _with_before(_kpi_row(item, {"group": (group or "Sem grupo").title()}), (group_before.get(group) or {}).get("net"))
        for group, item in group_now.items() if item["net"] or item["kg"]
    ]
    if field == "margin":
        change = mgmt.pp(now["margin"], before["margin"])
        note = f"antes {_kpi_value(field, before)}" + (f" · {change:+.1f} p.p.".replace(".", ",") if change is not None else "")
    else:
        change = dna._change(now[field], before[field]) if before.get(field) else None
        note = f"antes {_kpi_value(field, before)}" + (f" · {mgmt.pct(change, signed=True)}" if change is not None else "")
    summary = [{"label": label, "value": _kpi_value(field, now), "note": note}]
    same = {"net": "Faturamento", "profit": "Lucro bruto", "margin": "Margem"}.get(field)
    summary += [fact for fact in _totals_facts(list(now_cells.values())) if fact["label"] != same]
    summary.append({"label": "Clientes", "value": str(sum(1 for item in now_cells.values() if item["net"] > 0))})
    return {
        "title": label,
        "subtitle": f"Clientes e grupos por trás do card · {_ranges(window)}",
        "summary": summary[:5],
        "sections": [
            {"key": "customers", "label": "Clientes", "columns": _kpi_columns(), "rows": rows, "sort": {"key": sort_key, "dir": "desc"}, "empty": "Nenhum cliente."},
            {"key": "groups", "label": "Por grupo", "columns": group_columns, "rows": group_rows, "sort": {"key": sort_key, "dir": "desc"}, "empty": "Sem faturamento."},
        ],
    }


def _nrr_detail(customers, window, now_cells, before_cells, customer_url):
    """NRR = faturamento de hoje dos clientes do ano anterior ÷ o faturamento deles antes; GRR limita
    cada cliente ao que comprava (só perdas)."""
    before = {code: item["net"] for code, item in before_cells.items()}
    after = {code: item["net"] for code, item in now_cells.items()}
    data = mgmt.retention(before, after)
    rows = []
    for code, old in before.items():
        if old <= 0:
            continue
        kept = after.get(code, ZERO)
        kept = kept if kept > 0 else ZERO
        rows.append({
            **_customer(customers, code, customer_url),
            "status": "Perdido" if kept <= 0 else "Comprou mais" if kept >= old else "Comprou menos",
            "before": _out(old), "after": _out(kept), "capped": _out(min(kept, old)), "change": _out(kept - old),
            "nrr": _out(kept / old, 4), "grr": _out(min(kept, old) / old, 4),
            "__tone": "bad" if kept <= 0 else "warn" if kept < old else None,
        })
    new_rows = [{**_customer(customers, code, customer_url), "after": _out(value)} for code, value in data["flows"]["new"]]
    columns = [
        CUSTOMER_COLUMN, {"key": "status", "label": "Situação"},
        {"key": "before", "label": "Faturamento antes", "type": "money", "total": True},
        {"key": "after", "label": "Faturamento agora", "type": "money", "total": True, "strong": True},
        {"key": "change", "label": "Diferença", "type": "money", "total": True},
        {"key": "nrr", "label": "NRR", "type": "pct", "ratio": ["after", "before"]},
        {"key": "capped", "label": "Retido (até o que comprava)", "type": "money", "total": True, "hidden": True},
        {"key": "grr", "label": "GRR", "type": "pct", "ratio": ["capped", "before"]},
    ]
    return {
        "title": "Retenção de receita (NRR)",
        "subtitle": f"Clientes que compraram no período anterior: quanto compram agora · {_ranges(window)}",
        "summary": [
            {"label": "NRR", "value": mgmt.pct(data["nrr"]) if data["nrr"] is not None else "–", "note": "receita de hoje dos clientes do ano anterior"},
            {"label": "GRR", "value": mgmt.pct(data["grr"]) if data["grr"] is not None else "–", "note": "só as perdas (sem a expansão)"},
            {"label": "Clientes do período anterior", "value": str(len(rows)), "note": f"{data['lost']} perdidos"},
            {"label": "Clientes novos", "value": str(data["new"]), "note": f"{_money(data['new_revenue'])} (fora do NRR)"},
        ],
        "sections": [
            {"key": "base", "label": "Clientes do período anterior", "columns": columns, "rows": rows, "sort": {"key": "before", "dir": "desc"},
             "groupBy": ["status"], "empty": "Nenhum cliente no período anterior."},
            {"key": "new", "label": "Clientes novos (fora do NRR)", "columns": [CUSTOMER_COLUMN, {"key": "after", "label": "Faturamento agora", "type": "money", "total": True}],
             "rows": new_rows, "sort": {"key": "after", "dir": "desc"}, "empty": "Nenhum cliente novo."},
        ],
    }


def _active_detail(customers, window, now_cells, before_cells, customer_url):
    active = {code: item for code, item in now_cells.items() if item["net"] > 0}
    data = mgmt.retention({code: item["net"] for code, item in before_cells.items()}, {code: item["net"] for code, item in now_cells.items()})
    new_codes = {code for code, _value in data["flows"]["new"]}
    rows = [_with_before(_result_row(item, _customer(customers, code, customer_url)), (before_cells.get(code) or {}).get("net")) for code, item in active.items()]
    for row, code in zip(rows, active):
        row["status"] = "Novo" if code in new_codes else "Já comprava"
    lost_rows = [{**_customer(customers, code, customer_url), "net_before": _out(value)} for code, value in data["flows"]["lost"]]
    columns = _result_columns(with_before=True)
    columns.insert(1, {"key": "status", "label": "Situação"})
    before_count = sum(1 for item in before_cells.values() if item["net"] > 0)
    return {
        "title": "Clientes ativos",
        "subtitle": f"Clientes com faturamento no período · {_ranges(window)}",
        "summary": [
            {"label": "Clientes ativos", "value": str(len(active)), "note": f"antes {before_count}"},
            {"label": "Novos", "value": str(data["new"]), "tone": "good" if data["new"] else None},
            {"label": "Perdidos", "value": str(data["lost"]), "tone": "bad" if data["lost"] else None},
        ] + _totals_facts(list(active.values()))[:1],
        "sections": [
            {"key": "active", "label": "Ativos", "columns": columns, "rows": rows, "sort": {"key": "net", "dir": "desc"}, "groupBy": ["status"], "empty": "Nenhum cliente ativo."},
            {"key": "lost", "label": "Perdidos", "columns": [CUSTOMER_COLUMN, {"key": "net_before", "label": "Faturamento antes", "type": "money", "total": True}],
             "rows": lost_rows, "sort": {"key": "net_before", "dir": "desc"}, "empty": "Nenhum cliente perdido."},
        ],
    }


def _dependency_detail(customers, window, now_cells, customer_url):
    ranked = sorted(((code, item) for code, item in now_cells.items() if item["profit"] > 0), key=lambda pair: pair[1]["profit"], reverse=True)
    total = sum((item["profit"] for _code, item in ranked), ZERO)
    rows, running = [], ZERO
    for position, (code, item) in enumerate(ranked, start=1):
        running += item["profit"]
        row = _result_row(item, _customer(customers, code, customer_url))
        row.update({"position": position, "share": _out(item["profit"] / total, 4) if total else None,
                    "cumulative": _out(running / total, 4) if total else None, "__tone": "warn" if position <= 10 else None})
        rows.append(row)
    concentration = mgmt.concentration(now_cells)
    columns = [{"key": "position", "label": "Posição", "type": "int"}, CUSTOMER_COLUMN,
               {"key": "profit", "label": "Lucro bruto", "type": "money", "total": True, "strong": True},
               {"key": "share", "label": "Participação no lucro", "type": "pct", "total": True},
               {"key": "cumulative", "label": "Acumulado", "type": "pct"},
               {"key": "net", "label": "Faturamento", "type": "money", "total": True},
               {"key": "margin", "label": "Margem", "type": "pct", "ratio": ["profit", "tsi"]},
               {"key": "tsi", "label": "Sem impostos", "type": "money", "hidden": True}]
    share = lambda key: mgmt.pct(concentration[key]) if concentration[key] is not None else "–"  # noqa: E731
    return {
        "title": "Dependência dos 10 maiores",
        "subtitle": f"Participação de cada cliente no lucro bruto dos clientes com lucro · {window['range_label']}",
        "summary": [
            {"label": "10 maiores", "value": share("top10"), "note": "do lucro bruto"},
            {"label": "5 maiores", "value": share("top5")},
            {"label": "Maior cliente", "value": share("top1"), "note": rows[0]["customer"] if rows else ""},
            {"label": "Lucro bruto dos clientes com lucro", "value": _money(total), "note": f"{len(rows)} cliente(s)"},
        ],
        "sections": [
            {"key": "top", "label": "Os 10 maiores", "columns": columns, "rows": rows[:10], "sort": {"key": "position", "dir": "asc"}, "empty": "Nenhum cliente com lucro."},
            {"key": "all", "label": "Todos com lucro", "columns": columns, "rows": rows, "sort": {"key": "position", "dir": "asc"}, "empty": "Nenhum cliente com lucro."},
        ],
    }


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def management_detail(full, base, window, visual, point, filters=None, customer_url=None, today=None):
    """`full`: base da empresa; `base`: com os filtros de representante e grupo da página."""
    today = today or full.get("today") or date.today()
    filters = filters or {}
    customers = full["customers"]
    scoped, current, previous = _window_bases(base, window)
    if visual in ("ponte-receita", "ponte-lucro"):
        return _bridge_detail(customers, window, previous, current, "net" if visual == "ponte-receita" else "profit", point, customer_url)
    if visual == "retencao":
        return _retention_detail(customers, window, scoped, point, customer_url)
    if visual == "indicador":
        return _kpi_detail(customers, window, scoped, point, customer_url)
    if visual == "matriz":
        if not point.isdigit():
            raise UnknownPoint("Cliente inválido.")
        return _matrix_detail(customers, window, current, previous, int(point), customer_url,
                              reps=set(filters.get("reps") or ()), groups=set(filters.get("groups") or ()))
    if visual == "quadrante":
        return _quadrant_detail(customers, window, scoped, point, customer_url)
    if visual == "grupo":
        return _group_detail(customers, window, scoped, point, customer_url)
    if visual == "calor-margem":
        group, _, month = point.rpartition("|")
        if not group or not month.lstrip("-").isdigit():
            raise UnknownPoint("Célula do mapa de calor inválida.")
        return _group_detail(customers, window, scoped, group, customer_url, month=int(month))
    if visual == "sazonalidade":
        if not point.isdigit():
            raise UnknownPoint("Mês inválido.")
        return _season_detail(customers, base, int(point), customer_url)
    if visual == "rep":
        rep = None if point in ("", "none") else int(point) if point.lstrip("-").isdigit() else "?"
        if rep == "?":
            raise UnknownPoint("Representante inválido.")
        return _rep_detail(customers, window, scoped, rep, full.get("rep_names") or {}, customer_url)
    if visual == "projecao":
        if not point.isdigit():
            raise UnknownPoint("Mês inválido.")
        return _forecast_detail(full, int(point), customer_url, today)
    raise UnknownPoint("Este gráfico não tem detalhe.")
