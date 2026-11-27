"""Linhas por trás de um ponto dos gráficos do Dashes ("Expandir dados" do botão direito).

O menu de opções dos gráficos (static/js/maxisysDrill.js) pede aqui as linhas de um mês do
DNA de Representantes, do DNA do Cliente e da Ficha do Cliente. Cada função devolve o que a
janela desenha: título, resumo e seções com colunas tipadas e linhas cruas (número como
número, data ISO), para ordenar, somar, agrupar e exportar no navegador.

As regras são as dos gráficos (commercial_dna): a mesma consulta de faturamento
(BILLING_SQL, líquido de devolução, sem clientes do grupo), o mesmo filtro de grupo de produto
e a mesma carteira (itens em aberto com entrega no mês). Assim o total da tabela é o valor da
barra que foi clicada.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from . import commercial_dna as dna

ZERO = Decimal("0")


def parse_month(raw):
    """'2026-11' -> date(2026, 11, 1); None quando não é um mês válido."""
    try:
        year, month = (int(part) for part in str(raw or "").strip().split("-"))
        return date(year, month, 1) if 2000 <= year <= 2100 else None
    except (TypeError, ValueError):
        return None


def month_title(month_start):
    return f"{dna.MONTH_NAMES[month_start.month - 1]}/{month_start.year}"


def _f(value, digits=2):
    return None if value is None else round(float(value), digits)


def _last_part(text):
    return str(text).rsplit("-", 1)[-1] if text else None


def _place(city, state):
    return "/".join(part for part in (city, state) if part)


# ---------------------------------------------------------------------------
# Colunas (formato de maxisysDrill.js)
# ---------------------------------------------------------------------------

def billing_columns(with_rep=False, with_profit=False, with_unit=False, with_customer=True):
    columns = [
        {"key": "date", "label": "Emissão", "type": "date", "nowrap": True},
        {"key": "invoice", "label": "Nota", "nowrap": True},
    ]
    if with_customer:
        columns.append({"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"})
    columns += [
        {"key": "product", "label": "Produto", "sub": "product_code"},
        {"key": "group", "label": "Grupo"},
    ]
    if with_unit:
        columns.append({"key": "unit", "label": "Unidade"})
    if with_rep:
        columns.append({"key": "rep", "label": "Representante"})
    columns += [
        {"key": "kg", "label": "Kg líquido", "type": "kg", "total": True},
        {"key": "net", "label": "Faturamento líquido", "type": "money", "total": True, "strong": True},
        {"key": "fator", "label": "R$/kg", "type": "dec", "digits": 2, "ratio": ["net", "kg"]},
    ]
    if with_profit:
        columns += [
            {"key": "profit", "label": "Lucro bruto", "type": "money", "total": True},
            {"key": "margin", "label": "Margem", "type": "pct", "ratio": ["profit", "cost_base"]},
            {"key": "cost_base", "label": "Base do custo", "type": "money", "hidden": True},
        ]
    columns.append({"key": "order", "label": "Pedido", "nowrap": True})
    return columns


BILLING_GROUP_BY = ["customer", "product", "group", "invoice", "date", "order"]

ORDER_COLUMNS = [
    {"key": "order", "label": "Pedido", "nowrap": True},
    {"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"},
    {"key": "product", "label": "Produto", "wrap": True},
    {"key": "group", "label": "Grupo"},
    {"key": "issue", "label": "Emissão", "type": "date", "nowrap": True},
    {"key": "delivery", "label": "Entrega", "type": "date", "nowrap": True, "badge": "status", "tone": "status_tone"},
    {"key": "kg", "label": "Kg a faturar", "type": "kg", "total": True},
    {"key": "value", "label": "Valor a faturar", "type": "money", "total": True, "strong": True},
    {"key": "ready", "label": "Pronto", "type": "pct", "digits": 0, "ratio": ["ready_kg", "kg"]},
    {"key": "ready_kg", "label": "Kg pronto", "type": "kg", "hidden": True},
]

GOAL_COLUMNS = [
    {"key": "family", "label": "Grupo (família da meta)", "sub": "units"},
    {"key": "goal", "label": "Meta", "type": "kg", "total": True},
    {"key": "billed", "label": "Faturado", "type": "kg", "total": True},
    {"key": "to_bill", "label": "Carteira", "type": "kg", "total": True},
    {"key": "done", "label": "Realizado", "type": "kg", "total": True, "strong": True},
    {"key": "pct", "label": "Atingido", "type": "pct", "digits": 0, "ratio": ["done", "goal"]},
    {"key": "gap", "label": "Falta no grupo", "type": "kg"},
]


# ---------------------------------------------------------------------------
# Linhas
# ---------------------------------------------------------------------------

def billing_rows(lines, customer_url=None):
    rows = []
    for line in lines:
        weight = line["net_weight"]
        profit = line["net_of_taxes"] - line["cpv"] if line["cpv"] is not None else None
        rows.append({
            "date": line["date"].isoformat() if line["date"] else None,
            "invoice": _last_part(line["invoice"]),
            "customer": line["customer_name"] or f"Cliente {line['customer']}",
            "customer_sub": " · ".join(part for part in (str(line["customer"] or ""), _place(line["city"], line["state"])) if part),
            "customer_url": customer_url(line["customer"]) if customer_url and line["customer"] else None,
            "product": line["product_name"] or line["product_code"],
            "product_code": line["product_code"],
            "group": (line["group"] or "").title(),
            "unit": dna.BUDGET_UNITS.get(line["unit"], f"Unidade {line['unit']}") if line["unit"] is not None else None,
            "rep": dna.REPRESENTATIVE_DISPLAY_NAMES.get(line["rep_primary"]) or line["rep_name"] or None,
            "kg": _f(weight, 3),
            "net": _f(line["net"]),
            "fator": _f(line["net"] / weight) if weight else None,
            "profit": _f(profit),
            "cost_base": _f(line["net_of_taxes"]) if line["cpv"] is not None else None,
            "margin": _f(profit / line["net_of_taxes"], 4) if profit is not None and line["net_of_taxes"] else None,
            "order": _last_part(line["order"]),
        })
    return rows


def order_rows(items, customer_url=None):
    rows = []
    for item in items:
        kg = item["to_bill_kg"]
        rows.append({
            "order": str(item["number"]),
            "customer": item["customer_name"] or f"Cliente {item['customer']}",
            "customer_sub": str(item["customer"] or ""),
            "customer_url": customer_url(item["customer"]) if customer_url and item["customer"] else None,
            "product": item["product_name"],
            "group": (item["group"] or "").title(),
            "issue": item["issue_date"].isoformat() if item["issue_date"] else None,
            "delivery": item["delivery_date"].isoformat() if item["delivery_date"] else None,
            "status": "atrasado" if item["is_overdue"] else None,
            "status_tone": "bad" if item["is_overdue"] else None,
            "kg": _f(kg, 3),
            "value": _f(item["to_bill_value"]),
            "ready_kg": _f(item["ready_kg"], 3),
            "ready": _f(item["ready_kg"] / kg, 4) if kg else None,
            "__tone": "bad" if item["is_overdue"] else None,
        })
    return rows


def _billing_summary(lines, previous_lines=None, previous_label=""):
    net = sum((line["net"] for line in lines), ZERO)
    weight = sum((line["net_weight"] for line in lines), ZERO)
    invoices = {line["invoice"] for line in lines}
    orders = {line["order"] for line in lines if line["order"]}
    customers = {line["customer"] for line in lines if line["customer"] is not None}
    facts = [{"label": "Faturamento líquido", "value": dna._money(net, compact=False)}]
    if previous_lines is not None:
        previous = sum((line["net"] for line in previous_lines), ZERO)
        change = dna._change(net, previous)
        facts[0]["note"] = (
            f"{dna._pct(change, signed=True)} contra {previous_label}" if change is not None
            else f"sem faturamento em {previous_label}"
        )
        facts[0]["tone"] = None if change is None else ("good" if change >= 0 else "bad")
    facts += [
        {"label": "Volume", "value": dna._kg(weight)},
        {"label": "R$/kg", "value": dna._fator(net / weight if weight else None)},
        {"label": "Notas", "value": str(len(invoices)), "note": f"{len(orders)} pedido(s)"},
        {"label": "Clientes", "value": str(len(customers))},
    ]
    return facts


def _billed(lines, month_start):
    """Linhas que entram no gráfico (VL_FAT-DEV > 0) dentro do mês, da mais recente à mais antiga."""
    month_end = dna._add_months(month_start, 1)
    chosen = [line for line in lines if line["included"] and line["date"] and month_start <= line["date"] < month_end]
    return sorted(chosen, key=lambda line: (line["date"], line["net"]), reverse=True)


# ---------------------------------------------------------------------------
# DNA de Representantes
# ---------------------------------------------------------------------------

def representative_detail(rep_code, visual, month_start, goals=None, groups=None, today=None, customer_url=None, customer_code=None):
    """Um mês do DNA de Representantes.

    visual "meta": o mês do gráfico Meta x realizado (notas faturadas, carteira com entrega no
    mês e a meta por grupo). visual "faturamento": o mês do Faturamento mensal (notas do mês
    e do mesmo mês do ano anterior). visual "ultima-compra": as notas de um cliente do card
    "Para reativar" no mês da última compra com o representante.
    """
    today = today or date.today()
    primary = dna.primary_rep(rep_code)
    codes = dna.rep_codes(primary)
    placeholders, params = dna._in_clause("r", codes)
    month_end = dna._add_months(month_start, 1)
    billing_scope = dna.BILLING_SQL.format(scope=f"N.CODREP IN ({placeholders})")

    def rep_title(*groups_of_lines):
        """Nome como no painel: o de exibição do PBIX ou o do cadastro (E090REP) que vem nas notas."""
        name = dna.REPRESENTATIVE_DISPLAY_NAMES.get(primary) or next(
            (line["rep_name"] for lines in groups_of_lines for line in lines if line["rep_primary"] == primary and line["rep_name"]), None
        )
        return f"{name or 'Representante'} ({', '.join(str(code) for code in codes)})"

    if visual == "ultima-compra":
        scope = dna.BILLING_SQL.format(scope=f"N.CODREP IN ({placeholders}) AND N.CODCLI = :cliente")
        rows = dna._run({"billing": (scope, {**params, "cliente": customer_code, "start_date": month_start, "end_date": month_end})})["billing"]
        lines = _billed(dna.only_groups([dna.billing_line(row) for row in rows], groups), month_start)
        name = next((line["customer_name"] for line in lines if line["customer_name"]), None) or f"Cliente {customer_code}"
        return {
            "title": f"{name} ({customer_code})",
            "subtitle": f"Notas de {month_title(month_start)}, o mês da última compra com {rep_title(lines)}",
            "summary": _billing_summary(lines),
            "sections": [{
                "key": "billed", "label": "Notas da última compra", "columns": billing_columns(with_unit=True, with_customer=False),
                "rows": billing_rows(lines), "groupBy": [key for key in BILLING_GROUP_BY if key != "customer"] + ["unit"],
                "empty": "Nenhuma nota com o representante neste mês.",
            }],
        }

    if visual == "faturamento":
        previous_start = dna._add_months(month_start, -12)
        sources = dna._run({
            "billing": (billing_scope, {**params, "start_date": month_start, "end_date": month_end}),
            "previous": (billing_scope, {**params, "start_date": previous_start, "end_date": dna._add_months(previous_start, 1)}),
        })
        lines = _billed(dna.only_groups([dna.billing_line(row) for row in sources["billing"]], groups), month_start)
        previous = _billed(dna.only_groups([dna.billing_line(row) for row in sources["previous"]], groups), previous_start)
        previous_label = month_title(previous_start)
        sections = [
            {"key": "billed", "label": "Notas do mês", "columns": billing_columns(with_unit=True), "rows": billing_rows(lines, customer_url),
             "groupBy": BILLING_GROUP_BY + ["unit"], "empty": "Nenhuma nota faturada neste mês."},
            {"key": "previous", "label": f"Mesmo mês de {previous_start.year}", "columns": billing_columns(with_unit=True),
             "rows": billing_rows(previous, customer_url), "groupBy": BILLING_GROUP_BY + ["unit"],
             "empty": f"Nenhuma nota faturada em {previous_label}."},
        ]
        return {
            "title": month_title(month_start),
            "subtitle": f"{rep_title(lines, previous)} · faturamento líquido de devolução",
            "summary": _billing_summary(lines, previous, previous_label),
            "sections": sections,
        }

    return representative_goal_detail(primary, month_start, month_end, month_title(month_start), goals=goals, groups=groups,
                                      today=today, customer_url=customer_url)


def representative_goal_detail(rep_code, start, end, title, goals=None, groups=None, today=None, customer_url=None, family=None, period_note=""):
    """Meta x realizado de um intervalo de meses (início no dia 1, fim exclusivo): notas faturadas,
    carteira em aberto com entrega no intervalo e a meta por grupo. `family` restringe ao grupo de
    uma linha de "Meta por família"."""
    today = today or date.today()
    primary = dna.primary_rep(rep_code)
    codes = dna.rep_codes(primary)
    placeholders, params = dna._in_clause("r", codes)
    billing_scope = dna.BILLING_SQL.format(scope=f"N.CODREP IN ({placeholders})")
    month_start, month_end = start, end

    def rep_title(*groups_of_lines):
        name = dna.REPRESENTATIVE_DISPLAY_NAMES.get(primary) or next(
            (line["rep_name"] for lines in groups_of_lines for line in lines if line["rep_primary"] == primary and line["rep_name"]), None
        )
        return f"{name or 'Representante'} ({', '.join(str(code) for code in codes)})"

    # Carteira do intervalo: só itens em aberto (situação 1 e 2) com entrega nele. A janela de
    # emissão vazia (início = fim) deixa de fora os pedidos já atendidos.
    order_scope = f"PED.CODREP IN ({placeholders}) AND ITEM.DATENT >= :delivery_start AND ITEM.DATENT < :delivery_end"
    delivery = {"delivery_start": month_start, "delivery_end": month_end}
    sources = dna._run({
        "billing": (billing_scope, {**params, "start_date": month_start, "end_date": month_end}),
        "orders": (dna.ORDERS_SQL.format(scope=order_scope), {**params, **delivery, "start_date": month_start, "end_date": month_start}),
        "production": (dna.PRODUCTION_SQL.format(scope=order_scope), {**params, **delivery}),
    })
    lines = sorted(
        (line for line in dna.only_groups([dna.billing_line(row) for row in sources["billing"]], groups)
         if line["included"] and line["date"] and month_start <= line["date"] < month_end),
        key=lambda line: (line["date"], line["net"]), reverse=True,
    )
    items = [
        item for item in dna._orders_with_production(sources["orders"], sources["production"], today)
        if item["is_open"] and item["delivery_date"] and month_start <= item["delivery_date"] < month_end
    ]
    # A família escolhida fica guardada à parte: os laços abaixo reusam o nome `family`.
    chosen_family = family
    if chosen_family is not None:
        lines = [line for line in lines if line["group"] == chosen_family]
        items = [item for item in items if item["group"] == chosen_family]
    items.sort(key=lambda item: (item["delivery_date"], -item["to_bill_kg"]))

    months = set()
    cursor = month_start
    while cursor < month_end:
        months.add((cursor.year, cursor.month))
        cursor = dna._add_months(cursor, 1)
    month_goals = [
        goal for goal in (goals or [])
        if (int(dna._goal_value(goal, "year")), int(dna._goal_value(goal, "month"))) in months
        and (chosen_family is None or dna._goal_value(goal, "family") == chosen_family)
    ]
    goal_by_family = defaultdict(lambda: ZERO)
    units_by_family = defaultdict(lambda: defaultdict(lambda: ZERO))
    for goal in month_goals:
        family = dna._goal_value(goal, "family")
        weight = dna._num(dna._goal_value(goal, "weight_kg"))
        goal_by_family[family] += weight
        units_by_family[family][int(dna._goal_value(goal, "unit_code"))] += weight
    billed_by_family = defaultdict(lambda: ZERO)
    for line in lines:
        billed_by_family[line["group"]] += line["net_weight"]
    to_bill_by_family = defaultdict(lambda: ZERO)
    for item in items:
        to_bill_by_family[item["group"]] += item["to_bill_kg"]
    goal_rows = []
    for family in sorted(set(goal_by_family) | set(billed_by_family) | set(to_bill_by_family),
                         key=lambda key: (goal_by_family.get(key, ZERO), billed_by_family.get(key, ZERO)), reverse=True):
        goal = goal_by_family.get(family, ZERO)
        done = billed_by_family.get(family, ZERO) + to_bill_by_family.get(family, ZERO)
        units = units_by_family.get(family) or {}
        goal_rows.append({
            "family": (family or "Sem grupo").title(),
            "units": " · ".join(f"{dna.BUDGET_UNITS.get(unit, f'Unidade {unit}')} {dna._kg(weight)}" for unit, weight in sorted(units.items(), key=lambda pair: pair[1], reverse=True))
            or ("sem meta" if not goal else ""),
            "goal": _f(goal, 1),
            "billed": _f(billed_by_family.get(family, ZERO), 1),
            "to_bill": _f(to_bill_by_family.get(family, ZERO), 1),
            "done": _f(done, 1),
            "pct": _f(done / goal, 4) if goal else None,
            "gap": _f(max(goal - done, ZERO), 1),
        })

    goal_total = sum(goal_by_family.values(), ZERO)
    billed_total = sum((line["net_weight"] for line in lines), ZERO)
    to_bill_total = sum((item["to_bill_kg"] for item in items), ZERO)
    done_total = billed_total + to_bill_total
    attained = done_total / goal_total if goal_total else None
    overdue = [item for item in items if item["is_overdue"]]
    single = len(months) == 1
    summary = [
        {"label": "Meta do mês" if single else "Meta do período", "value": dna._kg(goal_total) if goal_total else "sem meta"},
        {"label": "Faturado", "value": dna._kg(billed_total), "note": dna._money(sum((line["net"] for line in lines), ZERO), compact=False)},
        {"label": "Carteira a faturar", "value": dna._kg(to_bill_total),
         "note": f"{len(items)} item(ns){f' · {len(overdue)} atrasado(s)' if overdue else ''}"},
        {"label": "Realizado", "value": dna._kg(done_total), "note": f"{dna._pct(attained, decimals=0)} da meta" if attained is not None else "faturado + carteira",
         "tone": None if attained is None else ("good" if attained >= 1 else "warn" if attained >= 0.85 else "bad")},
        {"label": "Falta", "value": "–" if not goal_total else "meta batida" if done_total >= goal_total else dna._kg(goal_total - done_total),
         "tone": "good" if goal_total and done_total >= goal_total else None},
    ]
    group_note = f" · só os grupos {', '.join(group.title() for group in groups)}" if groups else ""
    family_note = f" · grupo {(chosen_family or 'Sem grupo').title()}" if chosen_family is not None else ""
    where = "no mês" if single else "no período"
    return {
        "title": title,
        "subtitle": f"{rep_title(lines)} · realizado = faturado + carteira com entrega {where}{family_note}{group_note}{period_note}",
        "summary": summary,
        "sections": [
            {"key": "billed", "label": "Faturado", "columns": billing_columns(with_unit=True), "rows": billing_rows(lines, customer_url),
             "groupBy": BILLING_GROUP_BY + ["unit"], "empty": f"Nenhuma nota faturada {where}.",
             "note": f"Notas {'do mês' if single else 'do período'}, em kg líquido de devolução" + (": a parte azul da barra." if single else ".")},
            {"key": "orders", "label": "Carteira a faturar", "columns": ORDER_COLUMNS, "rows": order_rows(items, customer_url),
             "groupBy": ["customer", "product", "group", "order", "delivery"], "empty": f"Nenhum item em aberto com entrega {where}.",
             "note": f"Itens de pedido em aberto com entrega prevista {where}" + (": a parte laranja da barra." if single else ".") + " Atrasados ficam marcados."
                     + (" A carteira não usa o filtro de grupo, como no gráfico." if groups else "")},
            {"key": "goal", "label": "Meta por grupo", "columns": GOAL_COLUMNS, "rows": goal_rows,
             "empty": f"Sem meta nem realizado {where}.",
             "note": "Meta do orçamento (PBIX) por grupo de produto, contra o faturado e a carteira do mesmo grupo."},
        ],
    }


PERIOD_FOCUS = {
    "participacao": "Participação na empresa",
    "classificacao": "Classificação Geral",
    "faturamento": "Faturamento líquido de devolução",
    "sem-impostos": "Faturamento sem impostos",
    "volume": "Volume líquido",
    "fator": "Fator kg (R$/kg)",
    "margem": "Margem bruta (CPV)",
    "pedidos": "Pedidos faturados",
    "devolucoes": "Devoluções",
    "prazo": "Prazo médio de pagamento",
}

RETURN_COLUMNS = [
    {"key": "date", "label": "Emissão", "type": "date", "nowrap": True},
    {"key": "invoice", "label": "Nota", "nowrap": True},
    {"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"},
    {"key": "product", "label": "Produto", "sub": "product_code"},
    {"key": "group", "label": "Grupo"},
    {"key": "gross", "label": "Faturamento bruto", "type": "money", "total": True},
    {"key": "returned", "label": "Devolvido", "type": "money", "total": True, "strong": True},
    {"key": "share", "label": "% devolvido", "type": "pct", "digits": 1, "ratio": ["returned", "gross"]},
    {"key": "returned_kg", "label": "Kg devolvido", "type": "kg", "total": True},
]

TERM_COLUMNS = [
    {"key": "condition", "label": "Condição de pagamento"},
    {"key": "invoices", "label": "Notas", "type": "int", "total": True},
    {"key": "net", "label": "Faturamento líquido", "type": "money", "total": True, "strong": True},
    {"key": "days", "label": "Prazo médio (dias)", "type": "dec", "digits": 0, "ratio": ["weighted", "base"]},
    {"key": "weighted", "label": "Dias × valor", "type": "dec", "hidden": True},
    {"key": "base", "label": "Base do prazo", "type": "money", "hidden": True},
]


def _delta_fact(label, now, before, formatter, previous_label, pp=False):
    if now is None:
        return {"label": label, "value": "–"}
    fact = {"label": label, "value": formatter(now)}
    if before is not None and pp:
        diff = (now - before) * 100
        fact["note"] = f"{diff:+.1f} p.p. contra {previous_label}".replace(".", ",")
        fact["tone"] = "good" if diff >= 0 else "bad"
    elif before:
        change = dna._change(now, before)
        fact["note"] = f"{dna._pct(change, signed=True)} contra {previous_label} ({formatter(before)})"
        fact["tone"] = "good" if change >= 0 else "bad"
    return fact


def representative_period_detail(rep_code, start, end, groups=None, focus="faturamento", customer_url=None):
    """Cards "Faturamento" do DNA de Representantes: as notas do período (início inclusivo, fim
    exclusivo) e do mesmo período do ano anterior, somadas com `dna.summarize` como os cards."""
    primary, codes, placeholders, params, rep_name = _rep_scope(rep_code)

    def subject(current, previous):
        rep = rep_name or next((line["rep_name"] for line in current + previous if line["rep_primary"] == primary and line["rep_name"]), None) or "Representante"
        return f"{rep} ({', '.join(str(code) for code in codes)})"

    return period_detail(f"N.CODREP IN ({placeholders})", params, start, end, groups=groups, focus=focus,
                         customer_url=customer_url, subject=subject)


def period_detail(scope_sql, params, start, end, groups=None, focus="faturamento", customer_url=None, subject=None, with_customer=True, extra=None):
    """Notas de um recorte (representante ou cliente) no período e no mesmo período do ano anterior.
    `extra(current, now)` devolve fatos e seções a mais (participação, classificação)."""
    if focus not in PERIOD_FOCUS:
        focus = "faturamento"
    scope = dna.BILLING_SQL.format(scope=scope_sql)
    previous_start, previous_end = dna._add_months(start, -12), dna._add_months(end, -12)
    sources = dna._run({
        "billing": (scope, {**params, "start_date": start, "end_date": end}),
        "previous": (scope, {**params, "start_date": previous_start, "end_date": previous_end}),
    })

    def window_lines(rows, first, last):
        return [line for line in dna.only_groups([dna.billing_line(row) for row in rows], groups)
                if line["date"] and first <= line["date"] < last]

    current = window_lines(sources["billing"], start, end)
    previous = window_lines(sources["previous"], previous_start, previous_end)
    now, before = dna.summarize(current), dna.summarize(previous)
    included = sorted((line for line in current if line["included"]), key=lambda line: (line["date"], line["net"]), reverse=True)
    previous_included = sorted((line for line in previous if line["included"]), key=lambda line: (line["date"], line["net"]), reverse=True)
    label_before = "o mesmo período do ano anterior"
    money = lambda value: dna._money(value, compact=False)  # noqa: E731
    pct = lambda value: dna._pct(value)  # noqa: E731
    facts = {
        "faturamento": _delta_fact("Faturamento líquido", now["net"], before["net"], money, label_before),
        "sem-impostos": _delta_fact("Sem impostos", now["net_of_taxes"], before["net_of_taxes"], money, label_before),
        "volume": _delta_fact("Volume líquido", now["net_weight"], before["net_weight"], dna._kg, label_before),
        "fator": _delta_fact("Fator kg", now["fator_kg"], before["fator_kg"], dna._fator, label_before),
        "margem": _delta_fact("Margem bruta", now["margin"], before["margin"], pct, label_before, pp=True),
        "pedidos": _delta_fact("Pedidos faturados", now["orders"], before["orders"], str, label_before),
    }
    facts["devolucoes"] = {"label": "Devoluções", "value": money(now["returned_value"]),
                           "note": f"{dna._pct(now['returned_share'], decimals=2)} do faturamento bruto · {dna._kg(now['returned_weight'])}"}
    facts["prazo"] = {"label": "Prazo médio de pagamento", "value": f"{float(now['payment_days']):.0f} dias" if now["payment_days"] is not None else "n/d",
                      "note": "ponderado pelo faturamento"}
    order = [focus] + [key for key in ("faturamento", "volume", "fator", "margem", "pedidos") if key != focus]
    summary = [facts[key] for key in order[:4]]
    if focus in ("faturamento", "sem-impostos"):
        summary.append({"label": "Impostos", "value": money(now["taxes"]) if "taxes" in now else money(sum((line["taxes"] for line in included), ZERO))})
    if focus == "margem":
        summary.append({"label": "Custo conhecido", "value": dna._pct(now["cost_coverage"], decimals=0), "note": "das vendas sem impostos com CPV"})
    if focus == "pedidos":
        summary.append({"label": "Notas", "value": str(now["invoices"]), "note": f"ticket {money(now['ticket'])}" if now["ticket"] else ""})
    columns = billing_columns(with_unit=True, with_profit=True, with_customer=with_customer)
    columns.insert(next(index for index, column in enumerate(columns) if column["key"] == "net") + 1,
                   {"key": "net_of_taxes", "label": "Sem impostos", "type": "money", "total": True})
    group_by = [key for key in BILLING_GROUP_BY if with_customer or key != "customer"] + ["unit"]

    def rows_of(lines):
        rows = billing_rows(lines, customer_url)
        for row, line in zip(rows, lines):
            row["net_of_taxes"] = _f(line["net_of_taxes"])
        return rows

    sections = []
    if focus == "devolucoes":
        returned = sorted((line for line in current if line["returned_value"] > 0), key=lambda line: line["returned_value"], reverse=True)
        sections.append({
            "key": "returns", "label": "Itens com devolução", "columns": RETURN_COLUMNS, "groupBy": ["customer", "product", "group", "invoice"],
            "sort": {"key": "returned", "dir": "desc"}, "empty": "Nenhuma devolução no período.",
            "rows": [{**row, "gross": _f(line["gross"]), "returned": _f(line["returned_value"]), "returned_kg": _f(line["returned_weight"], 3),
                      "share": _f(line["returned_value"] / line["gross"], 4) if line["gross"] else None}
                     for row, line in zip(billing_rows(returned, customer_url), returned)],
        })
    if focus == "prazo":
        terms = defaultdict(lambda: {"invoices": set(), "net": ZERO, "weighted": ZERO, "base": ZERO})
        for line in included:
            entry = terms[line["payment_condition"] or "Sem condição"]
            entry["invoices"].add(line["invoice"])
            entry["net"] += line["net"]
            if line["payment_days"] is not None:
                entry["weighted"] += line["payment_days"] * line["net"]
                entry["base"] += line["net"]
        sections.append({
            "key": "terms", "label": "Por condição de pagamento", "columns": TERM_COLUMNS, "sort": {"key": "net", "dir": "desc"}, "empty": "Sem notas no período.",
            "rows": [{"condition": condition, "invoices": len(entry["invoices"]), "net": _f(entry["net"]), "weighted": _f(entry["weighted"]),
                      "base": _f(entry["base"]), "days": _f(entry["weighted"] / entry["base"], 1) if entry["base"] else None}
                     for condition, entry in terms.items()],
        })
    if extra:
        more_facts, more_sections = extra(current, now)
        summary = more_facts + summary[: max(5 - len(more_facts), 2)]
        sections += more_sections
    sort = {"volume": "kg", "sem-impostos": "net_of_taxes", "margem": "profit"}.get(focus, "net")
    sections += [
        {"key": "billed", "label": "Notas do período", "columns": columns, "rows": rows_of(included), "groupBy": group_by,
         "sort": {"key": sort, "dir": "desc"}, "empty": "Nenhuma nota faturada no período."},
        {"key": "previous", "label": "Mesmo período do ano anterior", "columns": columns, "rows": rows_of(previous_included), "groupBy": group_by,
         "sort": {"key": sort, "dir": "desc"}, "empty": "Nenhuma nota no mesmo período do ano anterior."},
    ]
    who = subject(current, previous) if callable(subject) else (subject or "")
    group_note = f" · só os grupos {', '.join(group.title() for group in groups)}" if groups else ""
    return {
        "title": PERIOD_FOCUS.get(focus, "Faturamento"),
        "subtitle": f"{who} · {start:%d/%m/%Y} a {end - timedelta(days=1):%d/%m/%Y} contra {previous_start:%d/%m/%Y} a {previous_end - timedelta(days=1):%d/%m/%Y}{group_note}",
        "summary": summary,
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# DNA do Cliente: cards do período, mix, produtos e carteira
# ---------------------------------------------------------------------------

MONTH_COLUMNS = [
    {"key": "month", "label": "Mês", "nowrap": True},
    {"key": "invoices", "label": "Notas", "type": "int", "total": True},
    {"key": "kg", "label": "Kg líquido", "type": "kg", "total": True},
    {"key": "net", "label": "Faturamento líquido", "type": "money", "total": True, "strong": True},
    {"key": "fator", "label": "R$/kg", "type": "dec", "digits": 2, "ratio": ["net", "kg"]},
    {"key": "profit", "label": "Lucro bruto", "type": "money", "total": True},
    {"key": "margin", "label": "Margem", "type": "pct", "ratio": ["profit", "cost_base"]},
    {"key": "cost_base", "label": "Base do custo", "type": "money", "hidden": True},
]


def _month_rows(lines):
    months = defaultdict(lambda: {"invoices": set(), "kg": ZERO, "net": ZERO, "profit": ZERO, "cost_base": ZERO})
    for line in lines:
        if not line["included"] or not line["date"]:
            continue
        entry = months[line["date"].replace(day=1)]
        entry["invoices"].add(line["invoice"])
        entry["kg"] += line["net_weight"]
        entry["net"] += line["net"]
        if line["cpv"] is not None:
            entry["profit"] += line["net_of_taxes"] - line["cpv"]
            entry["cost_base"] += line["net_of_taxes"]
    return [{
        "month": month_title(month), "invoices": len(entry["invoices"]), "kg": _f(entry["kg"], 3), "net": _f(entry["net"]),
        "fator": _f(entry["net"] / entry["kg"]) if entry["kg"] else None,
        "profit": _f(entry["profit"]) if entry["cost_base"] else None, "cost_base": _f(entry["cost_base"]) if entry["cost_base"] else None,
        "margin": _f(entry["profit"] / entry["cost_base"], 4) if entry["cost_base"] else None,
    } for month, entry in sorted(months.items(), reverse=True)]


def customer_period_detail(codes, start, end, groups=None, focus="faturamento", customer_url=None, title_name="", scope_info=None):
    """Cards do DNA do Cliente (o cliente, o grupo de CNPJs ou a unidade). `scope_info` (o retorno de
    `_resolve_customer_scope`) liga a participação e a classificação, que usam o ranking da empresa."""
    placeholders, params = dna._in_clause("c", codes)
    several = len(set(codes)) > 1
    extra = None
    if focus in ("participacao", "classificacao"):
        def extra(current, now):
            # O mesmo recorte da página (início e fim vêm de lá): meses e ranking iguais aos dos cards.
            window = dna.resolve_window(None, start, end - timedelta(days=1))
            ranking = dna.company_ranking(window)
            abc = dna._group_abc(ranking, codes, scope_info or {})
            if focus == "participacao":
                rank = next((abc.get(code) for code in codes if abc.get(code)), None)
                facts = [
                    {"label": "Participação na empresa", "value": dna._pct(rank["share"], decimals=2) if rank else "n/d",
                     "note": f"{rank['position']}º de {ranking['customers_count']} clientes" if rank else "sem posição no período"},
                    {"label": "Curva", "value": rank["class"] if rank else "–", "note": f"{dna._pct(rank['cumulative'])} acumulado" if rank else ""},
                    {"label": "Sem impostos do cliente", "value": dna._money(now["net_of_taxes"], compact=False)},
                ]
                return facts, []
            classification = dna.classify_customer(current, window, abc)
            facts = [{"label": "Classificação", "value": classification.get("code") or "–", "note": f"nota {classification.get('score_display', '–')}"}]
            facts += [{"label": dim["label"], "value": dim["letter"] or "–", "note": dim["detail"]} for dim in classification["dimensions"]]
            return facts[:5], [{"key": "months", "label": "Por mês", "columns": MONTH_COLUMNS, "rows": _month_rows(current),
                               "empty": "Sem faturamento no período.",
                               "note": "Lote = kg ÷ meses com compra; recorrência = meses do período ÷ meses com compra."}]
    result = period_detail(f"N.CODCLI IN ({placeholders})", params, start, end, groups=groups,
                           focus="faturamento" if focus in ("participacao", "classificacao") else focus,
                           customer_url=customer_url if several else None, subject=title_name, with_customer=several, extra=extra)
    if focus == "participacao":
        result["title"] = "Participação na empresa"
    elif focus == "classificacao":
        result["title"] = "Classificação Geral"
    return result


def customer_product_detail(codes, start, end, product_code, customer_url=None, title_name=""):
    """Linha de "Principais produtos": as notas do produto no período e no ano anterior."""
    placeholders, params = dna._in_clause("c", codes)
    scope = dna.BILLING_SQL.format(scope=f"N.CODCLI IN ({placeholders})")
    previous_start, previous_end = dna._add_months(start, -12), dna._add_months(end, -12)
    sources = dna._run({
        "billing": (scope, {**params, "start_date": start, "end_date": end}),
        "previous": (scope, {**params, "start_date": previous_start, "end_date": previous_end}),
    })

    def chosen(rows, first, last):
        lines = [dna.billing_line(row) for row in rows]
        return sorted((line for line in lines if line["included"] and line["product_code"] == str(product_code) and line["date"] and first <= line["date"] < last),
                      key=lambda line: (line["date"], line["net"]), reverse=True)

    lines = chosen(sources["billing"], start, end)
    previous = chosen(sources["previous"], previous_start, previous_end)
    several = len(set(codes)) > 1
    columns = billing_columns(with_rep=True, with_profit=True, with_customer=several)
    group_by = [key for key in BILLING_GROUP_BY if several or key != "customer"] + ["rep"]
    name = next((line["product_name"] for line in lines + previous if line["product_name"]), None) or str(product_code)
    summary = [fact for fact in _billing_summary(lines, previous, "o mesmo período do ano anterior") if several or fact["label"] != "Clientes"]
    return {
        "title": name,
        "subtitle": f"{title_name} · produto {product_code} · {start:%d/%m/%Y} a {end - timedelta(days=1):%d/%m/%Y}",
        "summary": summary,
        "sections": [
            {"key": "billed", "label": "Notas do período", "columns": columns, "rows": billing_rows(lines, customer_url if several else None),
             "groupBy": group_by, "empty": "Nenhuma nota do produto no período."},
            {"key": "previous", "label": "Mesmo período do ano anterior", "columns": columns, "rows": billing_rows(previous, customer_url if several else None),
             "groupBy": group_by, "empty": "Nenhuma nota do produto no ano anterior."},
        ],
    }


def customer_orders_detail(codes, start, end, today=None, title_name=""):
    """Card "Carteira a faturar" do DNA do Cliente: todos os itens em aberto (a lista mostra 6) e os
    pedidos que entraram no período."""
    today = today or date.today()
    placeholders, params = dna._in_clause("c", codes)
    scope = f"PED.CODCLI IN ({placeholders})"
    sources = dna._run({
        "orders": (dna.ORDERS_SQL.format(scope=scope), {**params, "start_date": start, "end_date": end}),
        "production": (dna.PRODUCTION_SQL.format(scope=scope), params),
    })
    items = dna._orders_with_production(sources["orders"], sources["production"], today)
    open_items = sorted((item for item in items if item["is_open"]), key=lambda item: (item["delivery_date"] or date.max, -item["to_bill_kg"]))
    entered = sorted((item for item in items if item["issue_date"] and start <= item["issue_date"] < end), key=lambda item: item["issue_date"], reverse=True)
    kg = sum((item["to_bill_kg"] for item in open_items), ZERO)
    ready = sum((item["ready_kg"] for item in open_items), ZERO)
    overdue = [item for item in open_items if item["is_overdue"]]
    entered_rows = [{
        "order": str(item["number"]), "customer": item["customer_name"] or f"Cliente {item['customer']}", "customer_sub": str(item["customer"] or ""),
        "product": item["product_name"], "group": (item["group"] or "").title(), "issue": item["issue_date"].isoformat(),
        "delivery": item["delivery_date"].isoformat() if item["delivery_date"] else None, "kg": _f(item["ordered_kg"], 3), "value": _f(item["value"]),
        "fator": _f(item["fator"]) if item["ordered_kg"] else None, "status": "em aberto" if item["is_open"] else "atendido ou cancelado",
    } for item in entered]
    return {
        "title": "Carteira a faturar",
        "subtitle": f"{title_name} · itens em aberto pela data de entrega e pedidos que entraram de {start:%d/%m/%Y} a {end - timedelta(days=1):%d/%m/%Y}",
        "summary": [
            {"label": "A faturar", "value": dna._kg(kg), "note": f"{len(open_items)} item(ns) · {len({item['order'] for item in open_items})} pedido(s)"},
            {"label": "Valor", "value": dna._money(sum((item["to_bill_value"] for item in open_items), ZERO), compact=False)},
            {"label": "Pronto em estoque", "value": dna._pct(ready / kg) if kg else "–", "note": dna._kg(ready)},
            {"label": "Atrasados", "value": str(len(overdue)), "note": dna._kg(sum((item["to_bill_kg"] for item in overdue), ZERO)), "tone": "bad" if overdue else "good"},
            {"label": "Entrada no período", "value": dna._kg(sum((item["ordered_kg"] for item in entered), ZERO)), "note": f"{len({item['order'] for item in entered})} pedido(s)"},
        ],
        "sections": [
            {"key": "orders", "label": "Itens em aberto", "columns": ORDER_COLUMNS, "rows": order_rows(open_items), "sort": {"key": "delivery", "dir": "asc"},
             "groupBy": ["order", "product", "group", "delivery"], "empty": "Nenhum item em aberto."},
            {"key": "entered", "label": "Pedidos que entraram no período", "columns": INTAKE_COLUMNS, "rows": entered_rows, "sort": {"key": "issue", "dir": "desc"},
             "groupBy": ["order", "product", "group", "status"], "empty": "Nenhum pedido emitido no período."},
        ],
    }


def representative_mix_detail(rep_code, start, end, group, customer_url=None):
    """Linha de "Mix por grupo": as notas do grupo no período (o mix mostra todos os grupos, sem o
    filtro de grupo da página) e no mesmo período do ano anterior."""
    result = representative_period_detail(rep_code, start, end, groups=[group], focus="faturamento", customer_url=customer_url)
    result["title"] = (group or "Sem grupo").title()
    result["subtitle"] = result["subtitle"].split(" · só os grupos")[0] + " · notas do grupo"
    return result


INTAKE_COLUMNS = [
    {"key": "order", "label": "Pedido", "nowrap": True},
    {"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"},
    {"key": "product", "label": "Produto", "wrap": True},
    {"key": "group", "label": "Grupo"},
    {"key": "issue", "label": "Emissão", "type": "date", "nowrap": True},
    {"key": "delivery", "label": "Entrega", "type": "date", "nowrap": True},
    {"key": "kg", "label": "Kg pedido", "type": "kg", "total": True},
    {"key": "value", "label": "Valor", "type": "money", "total": True, "strong": True},
    {"key": "fator", "label": "R$/kg", "type": "dec", "digits": 2, "ratio": ["value", "kg"]},
    {"key": "status", "label": "Situação"},
]


def representative_intake_detail(rep_code, start, end, today=None, customer_url=None):
    """Card "Entrada de pedidos": os itens de pedido emitidos no período (kg e valor pedidos)."""
    today = today or date.today()
    _primary, codes, placeholders, params, rep_name = _rep_scope(rep_code)
    scope = f"PED.CODREP IN ({placeholders})"
    sources = dna._run({
        "orders": (dna.ORDERS_SQL.format(scope=scope), {**params, "start_date": start, "end_date": end}),
        "production": (dna.PRODUCTION_SQL.format(scope=scope), params),
    })
    items = [item for item in dna._orders_with_production(sources["orders"], sources["production"], today)
             if item["issue_date"] and start <= item["issue_date"] < end]
    items.sort(key=lambda item: (item["issue_date"], item["value"]), reverse=True)
    kg = sum((item["ordered_kg"] for item in items), ZERO)
    value = sum((item["value"] for item in items), ZERO)
    rows = [{
        "order": str(item["number"]),
        "customer": item["customer_name"] or f"Cliente {item['customer']}",
        "customer_sub": str(item["customer"] or ""),
        "customer_url": customer_url(item["customer"]) if customer_url and item["customer"] else None,
        "product": item["product_name"], "group": (item["group"] or "").title(),
        "issue": item["issue_date"].isoformat(), "delivery": item["delivery_date"].isoformat() if item["delivery_date"] else None,
        "kg": _f(item["ordered_kg"], 3), "value": _f(item["value"]), "fator": _f(item["fator"]) if item["ordered_kg"] else None,
        "status": "em aberto" if item["is_open"] else "atendido ou cancelado",
    } for item in items]
    return {
        "title": "Entrada de pedidos",
        "subtitle": f"Itens de pedido emitidos de {start:%d/%m/%Y} a {end - timedelta(days=1):%d/%m/%Y} · {rep_name or 'representante'} ({', '.join(str(code) for code in codes)})",
        "summary": [
            {"label": "Pedido", "value": dna._kg(kg), "note": f"{len({item['order'] for item in items})} pedido(s)"},
            {"label": "Valor", "value": dna._money(value, compact=False)},
            {"label": "Fator", "value": f"{dna._fator(value / kg if kg else None)}/kg"},
            {"label": "Em aberto", "value": dna._kg(sum((item["to_bill_kg"] for item in items), ZERO)), "note": "ainda a faturar"},
        ],
        "sections": [{"key": "items", "label": "Itens pedidos", "columns": INTAKE_COLUMNS, "rows": rows, "sort": {"key": "issue", "dir": "desc"},
                      "groupBy": ["customer", "product", "group", "order", "status"], "empty": "Nenhum pedido emitido no período."}],
    }


# ---------------------------------------------------------------------------
# DNA do Cliente e Ficha do Cliente
# ---------------------------------------------------------------------------

def customer_detail(customer_codes, month_start, groups=None, customer_url=None, title_name="", with_profit=True):
    """Notas de um cliente (ou do grupo de CNPJs) no mês e no mesmo mês do ano anterior."""
    placeholders, params = dna._in_clause("c", customer_codes)
    billing_scope = dna.BILLING_SQL.format(scope=f"N.CODCLI IN ({placeholders})")
    month_end = dna._add_months(month_start, 1)
    previous_start = dna._add_months(month_start, -12)
    sources = dna._run({
        "billing": (billing_scope, {**params, "start_date": month_start, "end_date": month_end}),
        "previous": (billing_scope, {**params, "start_date": previous_start, "end_date": dna._add_months(previous_start, 1)}),
    })
    lines = _billed(dna.only_groups([dna.billing_line(row) for row in sources["billing"]], groups), month_start)
    previous = _billed(dna.only_groups([dna.billing_line(row) for row in sources["previous"]], groups), previous_start)
    previous_label = month_title(previous_start)
    # Um cliente só: a coluna e o total de clientes não dizem nada (no grupo de CNPJs, sim).
    several = len(set(customer_codes)) > 1
    columns = billing_columns(with_rep=True, with_profit=with_profit, with_customer=several)
    group_by = [key for key in BILLING_GROUP_BY if several or key != "customer"] + ["rep"]
    profit = sum((line["net_of_taxes"] - line["cpv"] for line in lines if line["cpv"] is not None), ZERO)
    summary = _billing_summary(lines, previous, previous_label)
    if not several:
        summary = [fact for fact in summary if fact["label"] != "Clientes"]
    if with_profit:
        base = sum((line["net_of_taxes"] for line in lines if line["cpv"] is not None), ZERO)
        summary.insert(1, {"label": "Lucro bruto", "value": dna._money(profit, compact=False),
                           "note": f"margem {dna._pct(profit / base)}" if base else "sem custo no ERP"})
    group_note = f" · só os grupos {', '.join(group.title() for group in groups)}" if groups else ""
    if not title_name:
        sample = next((line for line in lines + previous if line["customer_name"]), None)
        title_name = f"{sample['customer_name']} ({sample['customer']})" if sample else f"Cliente {customer_codes[0]}"
    return {
        "title": month_title(month_start),
        "subtitle": f"{title_name} · notas faturadas, líquidas de devolução{group_note}",
        "summary": summary,
        "sections": [
            {"key": "billed", "label": "Notas do mês", "columns": columns, "rows": billing_rows(lines, customer_url),
             "groupBy": group_by, "empty": "Nenhuma nota faturada neste mês.",
             "note": "Lucro bruto = faturamento sem impostos − CPV (E210MVP), só nas linhas com custo." if with_profit else ""},
            {"key": "previous", "label": f"Mesmo mês de {previous_start.year}", "columns": columns,
             "rows": billing_rows(previous, customer_url), "groupBy": group_by, "empty": f"Nenhuma nota faturada em {previous_label}."},
        ],
    }


# ---------------------------------------------------------------------------
# Tabelas de registros (DNA de Representantes e Carteira de Clientes)
# ---------------------------------------------------------------------------

def _rep_scope(rep_code):
    primary = dna.primary_rep(rep_code)
    codes = dna.rep_codes(primary)
    placeholders, params = dna._in_clause("r", codes)
    name = dna.REPRESENTATIVE_DISPLAY_NAMES.get(primary)
    return primary, codes, placeholders, params, name


def representative_customer_detail(rep_code, customer_code, start, end, groups=None, period_label=""):
    """Linha de "Clientes da carteira": as notas do cliente com o representante no período da tela
    (início inclusivo, fim exclusivo) e no mesmo período do ano anterior, com o filtro de grupo."""
    primary, codes, placeholders, params, rep_name = _rep_scope(rep_code)
    scope = dna.BILLING_SQL.format(scope=f"N.CODREP IN ({placeholders}) AND N.CODCLI = :cliente")
    previous_start, previous_end = dna._add_months(start, -12), dna._add_months(end, -12)
    sources = dna._run({
        "billing": (scope, {**params, "cliente": customer_code, "start_date": start, "end_date": end}),
        "previous": (scope, {**params, "cliente": customer_code, "start_date": previous_start, "end_date": previous_end}),
    })

    def chosen(rows, first, last):
        lines = dna.only_groups([dna.billing_line(row) for row in rows], groups)
        return sorted((line for line in lines if line["included"] and line["date"] and first <= line["date"] < last),
                      key=lambda line: (line["date"], line["net"]), reverse=True)

    lines = chosen(sources["billing"], start, end)
    previous = chosen(sources["previous"], previous_start, previous_end)
    name = next((line["customer_name"] for line in lines + previous if line["customer_name"]), None) or f"Cliente {customer_code}"
    rep = rep_name or next((line["rep_name"] for line in lines + previous if line["rep_primary"] == primary and line["rep_name"]), None) or "Representante"
    previous_label = f"{previous_start:%d/%m/%Y} a {previous_end - timedelta(days=1):%d/%m/%Y}"
    columns = billing_columns(with_unit=True, with_customer=False)
    group_by = [key for key in BILLING_GROUP_BY if key != "customer"] + ["unit"]
    return {
        "title": f"{name} ({customer_code})",
        "subtitle": f"Notas com {rep} ({', '.join(str(code) for code in codes)}) · {period_label or previous_label}",
        "summary": _billing_summary(lines, previous, "o mesmo período do ano anterior"),
        "sections": [
            {"key": "billed", "label": "Notas do período", "columns": columns, "rows": billing_rows(lines), "groupBy": group_by,
             "empty": "Nenhuma nota com o representante no período."},
            {"key": "previous", "label": "Mesmo período do ano anterior", "columns": columns, "rows": billing_rows(previous),
             "groupBy": group_by, "empty": f"Nenhuma nota de {previous_label}."},
        ],
    }


def representative_customer_orders(rep_code, customer_code, today=None):
    """Linha de "Carteira a faturar": todos os itens em aberto do cliente com o representante
    (a tabela da tela mostra só os 10 primeiros da carteira inteira), com o quanto já está pronto."""
    today = today or date.today()
    _primary, codes, placeholders, params, rep_name = _rep_scope(rep_code)
    scope = f"PED.CODREP IN ({placeholders}) AND PED.CODCLI = :cliente"
    # Janela de emissão vazia (início = fim): só os itens em aberto, como na carteira da tela.
    sources = dna._run({
        "orders": (dna.ORDERS_SQL.format(scope=scope), {**params, "cliente": customer_code, "start_date": today, "end_date": today}),
        "production": (dna.PRODUCTION_SQL.format(scope=scope), {**params, "cliente": customer_code}),
    })
    items = [item for item in dna._orders_with_production(sources["orders"], sources["production"], today) if item["is_open"]]
    items.sort(key=lambda item: (item["delivery_date"] or date.max, -item["to_bill_kg"]))
    kg = sum((item["to_bill_kg"] for item in items), ZERO)
    ready = sum((item["ready_kg"] for item in items), ZERO)
    overdue = [item for item in items if item["is_overdue"]]
    name = next((item["customer_name"] for item in items if item["customer_name"]), None) or f"Cliente {customer_code}"
    return {
        "title": f"{name} ({customer_code})",
        "subtitle": f"Itens em aberto com {rep_name or 'o representante'} ({', '.join(str(code) for code in codes)}) · pela data de entrega",
        "summary": [
            {"label": "A faturar", "value": dna._kg(kg), "note": f"{len(items)} item(ns) · {len({item['order'] for item in items})} pedido(s)"},
            {"label": "Valor", "value": dna._money(sum((item["to_bill_value"] for item in items), ZERO), compact=False)},
            {"label": "Pronto", "value": dna._pct(ready / kg) if kg else "–", "note": dna._kg(ready)},
            {"label": "Atrasados", "value": str(len(overdue)), "note": dna._kg(sum((item["to_bill_kg"] for item in overdue), ZERO)),
             "tone": "bad" if overdue else "good"},
        ],
        "sections": [{"key": "orders", "label": "Carteira a faturar", "columns": ORDER_COLUMNS, "rows": order_rows(items),
                      "sort": {"key": "delivery", "dir": "asc"}, "groupBy": ["order", "product", "group", "delivery"],
                      "empty": "Nenhum item em aberto."}],
    }


PORTFOLIO_MONTH_COLUMNS = [
    {"key": "month", "label": "Mês", "nowrap": True},
    {"key": "net", "label": "Faturamento", "type": "money", "total": True, "strong": True},
    {"key": "kg", "label": "Kg", "type": "kg", "total": True},
    {"key": "fator", "label": "R$/kg", "type": "dec", "digits": 2, "ratio": ["net", "kg"]},
    {"key": "profit", "label": "Lucro bruto do mês", "type": "money", "total": True},
    {"key": "margin", "label": "Margem do mês", "type": "pct"},
    {"key": "notes", "label": "Notas", "type": "int", "total": True},
]


def portfolio_customer_detail(base, window, customer_code, history=False):
    """Linha da Carteira de Clientes: o cliente mês a mês (a mesma base mensal da tabela) e as notas
    do período. `history` (visão "Inativos para reativar") usa os 24 meses da base."""
    from . import customer_intel as intel

    today = base["today"]
    if history:
        last = dna._month_key(today)
        first = last - (intel.HISTORY_MONTHS - 1)
        start, end, label = date(first // 12, first % 12 + 1, 1), today + timedelta(days=1), f"últimos {intel.HISTORY_MONTHS} meses"
    else:
        first, last = window["first_key"], window["last_key"]
        start, end, label = window["start"], window["end"], window["label"]
    rows = base["months"].get(customer_code) or []
    total = intel._aggregate(rows, first, last)
    months = []
    for key in range(last, first - 1, -1):
        month = intel._aggregate([row for row in rows if row["month"] == key], key, key)
        if month["net"] <= 0:
            continue
        months.append({
            "month": dna._month_label(key), "net": _f(month["net"]), "kg": _f(month["kg"], 1),
            "fator": _f(month["net"] / month["kg"]) if month["kg"] else None,
            "profit": _f(month["profit"]), "margin": _f(month["margin"], 4), "notes": month["notes"],
        })
    sql = dna.BILLING_SQL.format(scope="N.CODCLI = :cliente")
    lines = [dna.billing_line(row) for row in dna._run({"billing": (sql, {"cliente": customer_code, "start_date": start, "end_date": end})})["billing"]]
    lines = sorted((line for line in lines if line["net"] > 0), key=lambda line: (line["date"], line["net"]), reverse=True)
    customer = base["customers"].get(customer_code) or {}
    margin = total["margin"]
    return {
        "title": f"{customer.get('name') or f'Cliente {customer_code}'} ({customer_code})",
        "subtitle": f"Carteira de Clientes · {label} · mês a mês e as notas do período",
        "summary": [
            {"label": "Faturamento", "value": dna._money(total["net"], compact=False), "note": dna._kg(total["kg"])},
            {"label": "Lucro bruto", "value": dna._money(total["profit"], compact=False) if total["profit"] is not None else "–"},
            {"label": "Margem", "value": dna._pct(margin) if margin is not None else "–",
             "note": f"custo conhecido em {dna._pct(total['cost_coverage'])} do valor" if total["cost_coverage"] is not None else ""},
            {"label": "Notas", "value": str(total["notes"]), "note": f"{len(total['months'])} mês(es) com compra"},
        ],
        "sections": [
            {"key": "months", "label": "Mês a mês", "columns": PORTFOLIO_MONTH_COLUMNS, "rows": months, "empty": "Sem compras no período.",
             "note": "A margem do período é medida no período todo; a soma do lucro dos meses pode diferir um pouco do total."},
            {"key": "billing", "label": "Notas do período", "columns": billing_columns(with_rep=True, with_profit=True, with_customer=False),
             "rows": billing_rows(lines), "groupBy": [key for key in BILLING_GROUP_BY if key != "customer"] + ["rep"],
             "empty": "Nenhuma nota no período."},
        ],
    }
