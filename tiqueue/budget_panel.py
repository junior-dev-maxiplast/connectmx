"""
Painel "Orçamento" do Dashes: meta × faturado × previsão por unidade, família e representante.

Recria as páginas de orçamento do PBIX "2. ORÇAMENTO 2026" com as mesmas contas:

- ORÇAMENTO 2026: medidores de valor e volume faturados contra o previsto (com o ponto de
  equilíbrio), % e diferença contra a meta, atraso em R$ e kg, previsão de faturamento
  (faturado + a faturar) e o gráfico por grupo (a faturar, produzido, faturado, previsão, meta,
  % meta = previsão ÷ meta, até 100%);
- META REPRESENTANTES: meta, peso (faturado + a faturar) e % atingido por representante e família;
- PREVISÃO FATURAMENTO: em aberto, faturado e total (kg e R$) por unidade, representante e grupo;
- FATURAMENTO POR FAMILIA: peso, valor e R$/kg por unidade, grupo, representante e cliente.

Regras (as do PBIX e do DNA): faturamento de transações de venda, líquido de devolução, sem os
clientes do grupo e sem produtos ISOLAX; cada nota conta na unidade de orçamento
(`commercial_dna.budget_unit`) e na família da meta do seu grupo de produto
(`sales_budget.family_of`). A faturar = peso do item − faturado − cancelado dos itens em aberto,
no mês da previsão de entrega; atraso = item em aberto com entrega antes de hoje; produzido =
pronto em estoque (`commercial_dna.order_item`). Metas: `sales_budget.year_goals`.

O servidor manda "fatos" já somados e a tela soma de novo a cada filtro (como no Painel de
Vendas): faturamento por mês, unidade, família, representante, cliente e filial da nota;
itens de pedido em aberto; metas por unidade e por representante.
"""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from . import commercial_dna as dna
from . import panels, sales_budget, sales_goals

ZERO = Decimal("0")
CURRENT_SECONDS = 5 * 60
HISTORY_SECONDS = 60 * 60
_RATIO = dna._RETURN_RATIO_SQL
_SERAFINA_CODES = sorted(str(code) for code in dna.SERAFINA_PRODUCTS)
_SERAFINA_SQL = "CASE WHEN I.CODPRO IN ({codes}) THEN 1 ELSE 0 END".format(
    codes=", ".join(f"'{code}'" for code in _SERAFINA_CODES) or "''"
)
_ISOLAX_SQL = "UPPER(NVL(P.DESPRO, ' ')) NOT LIKE '%ISOLAX%'"

BILLING_SQL = f"""
SELECT MES, EMPRESA, FILIAL, FILIAL_PRODUCAO, CODFAM, FAMILIA, BI_LAMINADO, SERAFINA, REPRESENTANTE, CLIENTE,
       SUM(PESO) AS PESO, SUM(BRUTO) AS BRUTO, SUM(SEM_IMPOSTOS) AS SEM_IMPOSTOS
FROM (
  SELECT TRUNC(N.DATEMI, 'MM') AS MES, N.CODEMP AS EMPRESA, N.CODFIL AS FILIAL, PI.USU_FILPRD AS FILIAL_PRODUCAO,
         I.CODFAM AS CODFAM, F.DESFAM AS FAMILIA, {panels._BI_SQL} AS BI_LAMINADO, {_SERAFINA_SQL} AS SERAFINA,
         N.CODREP AS REPRESENTANTE, N.CODCLI AS CLIENTE,
         NVL(I.PESLIQ, 0) * (1 - {_RATIO}) AS PESO,
         NVL(I.VLRBRU, 0) * (1 - {_RATIO}) AS BRUTO,
         (NVL(I.VLRBRU, 0) - NVL(I.VLRICM, 0) - NVL(I.VLRPIF, 0) - NVL(I.VLRCFF, 0)) * (1 - {_RATIO}) AS SEM_IMPOSTOS
  FROM E140NFV N
  JOIN E140IPV I ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
  JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
  LEFT JOIN E012FAM F ON F.CODEMP = I.CODEMP AND F.CODFAM = I.CODFAM
  LEFT JOIN E075PRO P ON P.CODEMP = I.CODEMP AND P.CODPRO = I.CODPRO
  LEFT JOIN E120IPD PI ON PI.CODEMP = I.CODEMP AND PI.CODFIL = I.CODFIL AND PI.NUMPED = I.NUMPED AND PI.SEQIPD = I.SEQIPD
  WHERE N.SITNFV = '2'
    AND N.CODCLI NOT IN (1001, 1002, 1004)
    AND N.DATEMI >= :inicio AND N.DATEMI < :fim
    AND {_ISOLAX_SQL}
)
GROUP BY MES, EMPRESA, FILIAL, FILIAL_PRODUCAO, CODFAM, FAMILIA, BI_LAMINADO, SERAFINA, REPRESENTANTE, CLIENTE
"""

# Itens de pedido em aberto (situação 1 e 2), com o que o PBIX usa para "a faturar" e "produzido".
OPEN_ORDERS_SQL = f"""
SELECT PED.CODEMP AS EMPRESA, PED.CODFIL AS FILIAL, PED.NUMPED AS PEDIDO, ITEM.SEQIPD AS SEQUENCIA,
       PED.DATEMI AS EMISSAO, ITEM.DATENT AS ENTREGA, ITEM.SITIPD AS SITUACAO_ITEM,
       PED.CODCLI AS CLIENTE, PED.CODREP AS REPRESENTANTE, ITEM.USU_FILPRD AS FILIAL_PRODUCAO,
       ITEM.CODPRO AS PRODUTO, P.DESPRO AS DESCRICAO_PRODUTO, P.CODFAM AS CODFAM, F.DESFAM AS FAMILIA,
       NVL(ITEM.QTDPED, 0) AS QTD_PEDIDA, NVL(ITEM.QTDFAT, 0) AS QTD_FATURADA, NVL(ITEM.QTDCAN, 0) AS QTD_CANCELADA,
       NVL(ITEM.QTDABE, 0) AS QTD_ABERTA, NVL(ITEM.PREUNI, 0) AS PRECO_UNITARIO, NVL(DER.PESBRU, 0) AS PESO_UNITARIO
FROM E120PED PED
JOIN E120IPD ITEM ON ITEM.CODEMP = PED.CODEMP AND ITEM.CODFIL = PED.CODFIL AND ITEM.NUMPED = PED.NUMPED
JOIN E001TNS TNS ON TNS.CODEMP = ITEM.CODEMP AND TNS.CODTNS = ITEM.TNSPRO AND TNS.VENFAT = 'S'
LEFT JOIN E075PRO P ON P.CODEMP = ITEM.CODEMP AND P.CODPRO = ITEM.CODPRO
LEFT JOIN E012FAM F ON F.CODEMP = P.CODEMP AND F.CODFAM = P.CODFAM
LEFT JOIN E075DER DER ON DER.CODEMP = ITEM.CODEMP AND DER.CODPRO = ITEM.CODPRO AND DER.CODDER = ITEM.CODDER
WHERE PED.CODCLI NOT IN (1001, 1002, 1004)
  AND ITEM.SITIPD IN (1, 2)
  AND {_ISOLAX_SQL}
"""

FAMILY_NAMES_SQL = "SELECT CODEMP AS EMPRESA, CODFAM AS CODIGO, DESFAM AS NOME FROM E012FAM"


# ---------------------------------------------------------------------------
# Fatos
# ---------------------------------------------------------------------------

_SERAFINA_SAMPLE = next(iter(sorted(dna.SERAFINA_PRODUCTS)), None)


def _f(value, digits=2):
    return None if value is None else round(float(value), digits)


def classify(company, branch, production_branch, family_desc, group, serafina=False):
    """(unidade de orçamento, família da meta) de uma nota ou item; família None = fora do orçamento.

    Só a empresa 1 (Maxiplast) tem orçamento por unidade: as demais ficam de fora.
    """
    if dna._int(company) != 1:
        return None, None
    unit = dna.budget_unit(family_desc, group, production_branch, branch, _SERAFINA_SAMPLE if serafina else None)
    return unit, (sales_budget.family_of(unit, group) if unit is not None else None)


def billing_facts(rows):
    """Faturamento por mês, unidade, família da meta, representante, cliente e filial da nota.

    Devolve (fatos, fora): `fora` soma o que não entra no orçamento (outras empresas e grupos sem meta).
    """
    totals = defaultdict(lambda: [ZERO, ZERO, ZERO])
    outside = [ZERO, ZERO, ZERO]
    for row in rows:
        month = dna._date(row.get("mes"))
        if month is None:
            continue
        family_desc = (row.get("familia") or "").strip()
        group = dna.product_group(family_desc, "++A" if dna._int(row.get("bi_laminado")) == 1 else "")
        unit, family = classify(row.get("empresa"), row.get("filial"), row.get("filial_producao"), family_desc, group,
                                dna._int(row.get("serafina")) == 1)
        values = (dna._num(row.get("peso")), dna._num(row.get("bruto")), dna._num(row.get("sem_impostos")))
        if family is None:
            for index, value in enumerate(values):
                outside[index] += value
            continue
        key = (month.month, unit, family, dna.primary_rep(row.get("representante")), dna._int(row.get("cliente")),
               panels._branch_key(row.get("empresa"), row.get("filial")))
        for index, value in enumerate(values):
            totals[key][index] += value
    facts = [
        {"m": key[0], "u": key[1], "f": key[2], "r": key[3], "c": key[4], "b": key[5],
         "k": _f(values[0], 1), "n": _f(values[1]), "t": _f(values[2])}
        for key, values in totals.items()
        if any(values)
    ]
    return facts, {"k": _f(outside[0], 1), "n": _f(outside[1]), "t": _f(outside[2])}


def month_key(value):
    return value.year * 12 + value.month - 1 if value else None


def order_facts(order_rows, production_rows, today):
    """Itens de pedido em aberto: a faturar (kg e R$), pronto em estoque e atraso, no mês da entrega."""
    produced = {}
    for row in production_rows:
        key = (dna._int(row.get("empresa")), dna._int(row.get("filial")), dna._int(row.get("pedido")), dna._int(row.get("sequencia")))
        produced[key] = produced.get(key, ZERO) + dna._num(row.get("qtd_produzida"))
    facts = []
    for row in order_rows:
        key = (dna._int(row.get("empresa")), dna._int(row.get("filial")), dna._int(row.get("pedido")), dna._int(row.get("sequencia")))
        item = dna.order_item({**row, "qtd_produzida": produced.get(key, ZERO)}, today)
        if not item["is_open"]:
            continue
        family_desc = (row.get("familia") or "").strip()
        unit, family = classify(row.get("empresa"), row.get("filial"), row.get("filial_producao"), family_desc, item["group"],
                                dna._int(row.get("produto")) in dna.SERAFINA_PRODUCTS)
        if family is None:
            continue
        delivery = item["delivery_date"]
        facts.append({
            "mk": month_key(delivery), "u": unit, "f": family, "r": item["rep_primary"], "c": item["customer"],
            "late": 1 if item["is_overdue"] else 0,
            "k": _f(item["to_bill_kg"], 1), "v": _f(item["to_bill_value"]), "p": _f(item["ready_kg"], 1),
            "o": f"{key[0]}-{key[1]}-{key[2]}", "s": key[3],
            "d": delivery.isoformat() if delivery else None,
            "e": item["issue_date"].isoformat() if item["issue_date"] else None,
        })
    return facts


def goal_facts(goals):
    """Metas do ano como fatos: por unidade (kg e R$), por representante (kg e R$ ao preço da unidade) e ponto de equilíbrio."""
    units = []
    prices = {}
    for (company, branch, family, month), entry in sorted(goals["units"].items()):
        if company != 1:
            continue
        kg, value = entry["kg"], entry["value"]
        prices[(branch, family, month)] = (value / kg) if kg else ZERO
        units.append({"m": month, "u": branch, "f": family, "k": _f(kg, 1), "v": _f(value)})
    reps = []
    for key, kg in sorted(goals["reps"].items(), key=lambda item: tuple(str(part) for part in item[0])):
        company, branch, family, rep, month = key
        if company != 1 or not kg:
            continue
        reps.append({"m": month, "u": branch, "f": family, "r": rep, "k": _f(kg, 1), "v": _f(kg * prices.get((branch, family, month), ZERO))})
    break_even = [{"m": month, "u": branch, "v": _f(value)} for (company, branch, month), value in sorted(goals["break_even"].items()) if company == 1]
    return {"units": units, "reps": reps, "break_even": break_even}


def business_months(year, today):
    """Dias úteis de cada mês do ano e quantos já passaram (para o ritmo esperado)."""
    result = []
    for month in range(1, 13):
        days = panels.business_days(date(year, month, 1), today)
        result.append({"m": month, "total": days["total"], "elapsed": days["elapsed"]})
    return result


def _mapped_groups():
    """Grupos de produto do DNA que caem em cada família da meta (o inverso de `sales_goals.family_for`)."""
    groups = defaultdict(set)
    for group, family in sales_goals.GROUP_FAMILIES.items():
        groups[family].add(group)
    for (_unit, group), family in sales_goals.UNIT_GROUP_FAMILIES.items():
        groups[family].add(group)
    return groups


def family_labels(goals, family_names, codes=()):
    """Rótulo de cada família: o(s) grupo(s) que ela representa e o nome no ERP.

    As famílias da meta usam os grupos do orçamento; as que só aparecem no faturamento, na carteira ou
    nas metas por representante (`codes`) usam os grupos do mapeamento grupo → família.
    """
    labels = {}
    for (_branch, family), groups in goals["groups"].items():
        entry = labels.setdefault(family, {"groups": set(), "name": family_names.get(family, "")})
        for group in (groups or "").split(" + "):
            if group:
                entry["groups"].add(group.title())
    mapped = _mapped_groups()
    for family in codes:
        if family and family not in labels:
            labels[family] = {"groups": {group.title() for group in mapped.get(family, ())}, "name": family_names.get(family, "")}
    for family, entry in labels.items():
        entry["groups"] = sorted(entry["groups"])
        title = " + ".join(entry["groups"]) or entry["name"] or family
        entry["label"] = f"{title} ({family})"
    return labels


# ---------------------------------------------------------------------------
# Detalhe por nota fiscal ("Expandir dados" até a nota, como no PBIX)
# ---------------------------------------------------------------------------

INVOICE_LINES_SQL = f"""
SELECT N.CODEMP AS EMPRESA, N.CODFIL AS FILIAL, N.NUMNFV AS NOTA, N.DATEMI AS DATA, N.CODCLI AS CLIENTE,
       N.CODREP AS REPRESENTANTE, PI.USU_FILPRD AS FILIAL_PRODUCAO, I.NUMPED AS PEDIDO, I.CODPRO AS PRODUTO,
       P.DESPRO AS DESCRICAO, I.CODFAM AS CODFAM, F.DESFAM AS FAMILIA, {panels._BI_SQL} AS BI_LAMINADO, {_SERAFINA_SQL} AS SERAFINA,
       NVL(I.PESLIQ, 0) * (1 - {_RATIO}) AS PESO,
       NVL(I.VLRBRU, 0) * (1 - {_RATIO}) AS BRUTO,
       (NVL(I.VLRBRU, 0) - NVL(I.VLRICM, 0) - NVL(I.VLRPIF, 0) - NVL(I.VLRCFF, 0)) * (1 - {_RATIO}) AS SEM_IMPOSTOS
FROM E140NFV N
JOIN E140IPV I ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
LEFT JOIN E012FAM F ON F.CODEMP = I.CODEMP AND F.CODFAM = I.CODFAM
LEFT JOIN E075PRO P ON P.CODEMP = I.CODEMP AND P.CODPRO = I.CODPRO
LEFT JOIN E120IPD PI ON PI.CODEMP = I.CODEMP AND PI.CODFIL = I.CODFIL AND PI.NUMPED = I.NUMPED AND PI.SEQIPD = I.SEQIPD
WHERE N.SITNFV = '2'
  AND N.CODCLI NOT IN (1001, 1002, 1004)
  AND N.CODEMP = 1
  AND N.DATEMI >= :inicio AND N.DATEMI < :fim
  AND {_ISOLAX_SQL}
  {{extra}}
"""

MAX_INVOICE_LINES = 3000


def invoice_detail(year, months, units, families, reps, customers, *, net=False):
    """Linhas de nota fiscal por trás de um ponto do painel, com os mesmos filtros (resultado do "Expandir dados")."""
    months = sorted({month for month in months if 1 <= month <= 12}) or list(range(1, 13))
    start = datetime(year, months[0], 1)
    end = datetime(year + 1, 1, 1) if months[-1] == 12 else datetime(year, months[-1] + 1, 1)
    extra, params = [], {"inicio": start, "fim": end}
    if reps:
        codes = sorted({code for rep in reps for code in dna.rep_codes(rep)})
        placeholders, rep_params = dna._in_clause("rep", codes)
        extra.append(f"AND N.CODREP IN ({placeholders})")
        params.update(rep_params)
    if customers:
        placeholders, customer_params = dna._in_clause("cli", customers)
        extra.append(f"AND N.CODCLI IN ({placeholders})")
        params.update(customer_params)
    rows = dna._run({"lines": (INVOICE_LINES_SQL.format(extra=" ".join(extra)), params)})["lines"]
    names = panels._names()
    lines = []
    for row in rows:
        day = dna._date(row.get("data"))
        if day is None or day.month not in months:
            continue
        family_desc = (row.get("familia") or "").strip()
        group = dna.product_group(family_desc, "++A" if dna._int(row.get("bi_laminado")) == 1 else "")
        unit, family = classify(row.get("empresa"), row.get("filial"), row.get("filial_producao"), family_desc, group,
                                dna._int(row.get("serafina")) == 1)
        rep = dna.primary_rep(row.get("representante"))
        if family is None or (units and unit not in units) or (families and family not in families) or (reps and rep not in reps):
            continue
        lines.append({
            "invoice": f"{row.get('nota')}", "date": day.isoformat(), "customer": dna._int(row.get("cliente")),
            "rep": names["reps"].get(rep) or (f"Representante {rep}" if rep else "Sem representante"),
            "unit": dna.BUDGET_UNITS.get(unit, f"Unidade {unit}"), "branch": names["branches"].get(panels._branch_key(row.get("empresa"), row.get("filial")), ""),
            "family": family, "group": group.title(), "order": row.get("pedido"),
            "product": (row.get("descricao") or "").strip() or str(row.get("produto") or ""),
            "kg": _f(dna._num(row.get("peso")), 1),
            "value": _f(dna._num(row.get("sem_impostos") if net else row.get("bruto"))),
        })
    lines.sort(key=lambda line: (line["date"], line["invoice"]), reverse=True)
    customer_names = panels.customer_names({line["customer"] for line in lines[:MAX_INVOICE_LINES]})
    for line in lines:
        info = customer_names.get(line["customer"]) or {}
        line["customer_name"] = info.get("name") or f"Cliente {line['customer']}"
    return lines


def available_years(today):
    from .models import SalesBudget

    years = set(SalesBudget.objects.values_list("year", flat=True).distinct()) | {today.year}
    return sorted(year for year in years if year <= today.year + 1)


def budget_payload(year, today, *, connect=None):
    """Tudo o que a tela do Orçamento precisa para um ano (em cache: 5 min no ano corrente)."""

    def load():
        start, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
        statements = {
            "billing": (BILLING_SQL, {"inicio": start, "fim": end}),
            "previous": (BILLING_SQL, {"inicio": datetime(year - 1, 1, 1), "fim": start}),
            "families": (FAMILY_NAMES_SQL, {}),
        }
        if year >= today.year:
            statements["orders"] = (OPEN_ORDERS_SQL, {})
            statements["production"] = (dna.PRODUCTION_SQL.format(scope="PED.CODCLI NOT IN (1001, 1002, 1004)"), {})
        rows = dna._run(statements)
        billing, outside = billing_facts(rows["billing"])
        previous, _previous_outside = billing_facts(rows["previous"])
        previous_by_month = defaultdict(lambda: [0.0, 0.0, 0.0])
        for fact in previous:
            totals = previous_by_month[(fact["m"], fact["u"], fact["f"], fact["r"])]
            totals[0] += fact["k"] or 0
            totals[1] += fact["n"] or 0
            totals[2] += fact["t"] or 0
        orders = order_facts(rows.get("orders", []), rows.get("production", []), today)
        goals = sales_budget.year_goals(year, connect=connect)
        family_names = {str(row["codigo"]).strip(): (row.get("nome") or "").strip()
                        for row in rows["families"] if dna._int(row.get("empresa")) == 1}
        return {
            "billing": billing,
            "outside": outside,
            "previous": [{"m": key[0], "u": key[1], "f": key[2], "r": key[3], "k": round(values[0], 1), "n": round(values[1], 2), "t": round(values[2], 2)}
                         for key, values in previous_by_month.items()],
            "orders": orders,
            "goals": goal_facts(goals),
            "families": family_labels(goals, family_names, {fact["f"] for fact in billing} | {fact["f"] for fact in orders}
                                      | {key[2] for key in goals["reps"]} | {fact["f"] for fact in previous}),
            "basis": goals["basis"],
            "sources": goals["sources"],
        }

    seconds = CURRENT_SECONDS if year >= today.year else HISTORY_SECONDS
    data = panels.cached(f"budget:{year}:{today.isoformat()}", seconds, load)
    names = panels._names()
    customer_codes = {fact["c"] for fact in data["billing"]} | {fact["c"] for fact in data["orders"]}
    customers = panels.cached(f"budget-customers:{year}:{len(customer_codes)}", panels.HISTORY_SECONDS,
                              lambda: panels.customer_names(customer_codes))
    is_current = year == today.year
    return {
        **data,
        "year": year,
        "today": today.isoformat(),
        "current_month": today.month if is_current else (12 if year < today.year else 0),
        "is_current": is_current,
        "is_future": year > today.year,
        "business": business_months(year, today),
        "basis_label": sales_budget.BASIS_LABELS.get(data["basis"], data["basis"]),
        "names": {
            "reps": {str(key): value for key, value in names["reps"].items()},
            "branches": names["branches"],
            "units": {str(key): value for key, value in dna.BUDGET_UNITS.items()},
            "customers": {str(code): value for code, value in customers.items()},
        },
        "loaded_at": timezone.localtime().strftime("%H:%M:%S"),
    }
