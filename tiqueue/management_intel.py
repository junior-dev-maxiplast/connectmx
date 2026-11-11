"""Painel gerencial: análises de gestão da empresa inteira, em meses fechados.

Base própria (cache de 30 minutos), lida do ERP Senior:

* faturamento líquido, sem impostos, CPV e kg por cliente, representante, grupo de
  produto e mês, dos últimos 24 meses fechados e do mês corrente (regras do PBIX,
  as mesmas do DNA);
* carteira a faturar (itens de pedido em aberto) por mês de entrega.

Os cálculos são Python puro (testáveis sem Oracle):

* ponte de faturamento e de lucro bruto contra o mesmo período do ano anterior:
  clientes perdidos e novos, produtos que o cliente deixou de comprar ou passou a
  comprar, volume, mix, preço e custo (a soma dos efeitos fecha a diferença);
* retenção de receita (NRR e GRR) e o fluxo de receita por cliente;
* matriz de rentabilidade dos clientes (faturamento × margem);
* rentabilidade por grupo de produto e mapa de calor da margem mês a mês;
* sazonalidade, projeção simples dos próximos meses e carteira a faturar;
* desempenho por representante, com a meta de kg do orçamento;
* números-base do simulador de cenários (preço, volume e custo).

Meses fechados: o mês corrente fica fora das comparações (é parcial).
"""

import statistics
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from . import commercial_dna as dna
from . import customer_intel as intel

ZERO = Decimal("0")
ONE = Decimal("1")
HISTORY_MONTHS = 24  # meses fechados guardados (mais o mês corrente)
BASE_CACHE_KEY = "management-intel:base:v1"
BASE_CACHE_SECONDS = 30 * 60
FORECAST_MONTHS = 3
HEATMAP_GROUPS = 10
MATRIX_POINTS = 400

PERIOD_CHOICES = (
    ("12", "12 meses fechados"),
    ("6", "6 meses fechados"),
    ("3", "3 meses fechados"),
    ("ytd", "Ano até o último mês fechado"),
)
DEFAULT_PERIOD = "12"

QUADRANTS = {
    "stars": ("Estrelas", "Faturamento e margem acima da referência: proteger e crescer."),
    "volume": ("Volume", "Faturam muito com margem abaixo da média: rever preço, mix e custo."),
    "niche": ("Nicho", "Margem boa e faturamento pequeno: espaço para vender mais."),
    "review": ("Revisar", "Pouco faturamento e margem baixa: custo de atender pode não compensar."),
}
QUADRANT_ORDER = ("stars", "volume", "niche", "review")


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_RATIO = dna._RETURN_RATIO_SQL
# 1 quando o trecho entre o 2º e o 3º "+" do complemento tem "A" (BI LAMINADO,
# a mesma regra de `commercial_dna.product_group`): agrupar por esse indicador em
# vez do complemento inteiro deixa a consulta com ~10 mil linhas, não ~30 mil.
_BI_SQL = (
    "CASE WHEN INSTR(I.CPLIPV, '+', 1, 2) > 0 AND INSTR(SUBSTR(I.CPLIPV, INSTR(I.CPLIPV, '+', 1, 2) + 1, "
    "CASE WHEN INSTR(I.CPLIPV, '+', 1, 3) > 0 THEN INSTR(I.CPLIPV, '+', 1, 3) - INSTR(I.CPLIPV, '+', 1, 2) - 1 "
    "ELSE 4000 END), 'A') > 0 THEN 1 ELSE 0 END"
)

CELLS_SQL = f"""
SELECT CLIENTE, REPRESENTANTE, MES, FAMILIA, BI_LAMINADO,
       SUM(LIQUIDO) AS LIQUIDO,
       SUM(GREATEST(LIQUIDO - IMPOSTOS, 0)) AS SEM_IMPOSTOS,
       SUM(PESO) AS PESO,
       SUM(CPV) AS CPV,
       SUM(CASE WHEN CPV IS NOT NULL THEN GREATEST(LIQUIDO - IMPOSTOS, 0) END) AS BASE_CPV
FROM (
  SELECT N.CODCLI AS CLIENTE, N.CODREP AS REPRESENTANTE, TRUNC(N.DATEMI, 'MM') AS MES,
         F.DESFAM AS FAMILIA, {_BI_SQL} AS BI_LAMINADO,
         NVL(I.VLRBRU, 0) * (1 - {_RATIO}) AS LIQUIDO,
         NVL(I.PESLIQ, 0) * (1 - {_RATIO}) AS PESO,
         NVL(I.VLRICM, 0) + NVL(I.VLRPIF, 0) + NVL(I.VLRCFF, 0) AS IMPOSTOS,
         (SELECT SUM(M.VLRMOV) FROM E210MVP M
           WHERE M.CODEMP = I.CODEMP AND M.CODFIL = I.CODFIL AND M.CODSNF = I.CODSNF
             AND M.NUMNFV = I.NUMNFV AND M.SEQIPV = I.SEQIPV AND M.ESTEOS = 'S') * (1 - {_RATIO}) AS CPV
  FROM E140NFV N
  JOIN E140IPV I
    ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
  JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
  LEFT JOIN E012FAM F ON F.CODEMP = I.CODEMP AND F.CODFAM = I.CODFAM
  WHERE N.SITNFV = '2'
    AND N.CODCLI NOT IN (1001, 1002, 1004)
    AND N.DATEMI >= :start_date
    AND N.DATEMI < :end_date
)
WHERE LIQUIDO > 0
GROUP BY CLIENTE, REPRESENTANTE, MES, FAMILIA, BI_LAMINADO
"""

# Carteira a faturar: itens de pedido em aberto, pelo mês de entrega. O valor é a
# quantidade em aberto × preço unitário do pedido (com impostos, como o faturamento).
BACKLOG_SQL = """
SELECT TRUNC(ITEM.DATENT, 'MM') AS MES,
       SUM(NVL(ITEM.QTDABE, 0) * NVL(ITEM.PREUNI, 0)) AS VALOR,
       SUM(NVL(ITEM.QTDABE, 0) * NVL(DER.PESBRU, 0)) AS PESO,
       COUNT(*) AS ITENS,
       COUNT(DISTINCT PED.CODCLI) AS CLIENTES
FROM E120PED PED
JOIN E120IPD ITEM ON ITEM.CODEMP = PED.CODEMP AND ITEM.CODFIL = PED.CODFIL AND ITEM.NUMPED = PED.NUMPED
JOIN E001TNS TNS ON TNS.CODEMP = ITEM.CODEMP AND TNS.CODTNS = ITEM.TNSPRO AND TNS.VENFAT = 'S'
LEFT JOIN E075DER DER ON DER.CODEMP = ITEM.CODEMP AND DER.CODPRO = ITEM.CODPRO AND DER.CODDER = ITEM.CODDER
WHERE PED.CODCLI NOT IN (1001, 1002, 1004)
  AND ITEM.SITIPD IN (1, 2)
  AND NVL(ITEM.QTDABE, 0) > 0
GROUP BY TRUNC(ITEM.DATENT, 'MM')
"""


CUSTOMERS_SQL = """
SELECT C.CODCLI AS CLIENTE, C.NOMCLI AS NOME, C.CIDCLI AS CIDADE, C.SIGUFS AS UF
FROM E085CLI C
WHERE C.CODCLI IN (SELECT DISTINCT N.CODCLI FROM E140NFV N WHERE N.SITNFV = '2' AND N.DATEMI >= :start_date)
"""

REPS_SQL = "SELECT CODREP AS CODIGO, NOMREP AS NOME FROM E090REP"


# ---------------------------------------------------------------------------
# Carga e montagem da base
# ---------------------------------------------------------------------------

def load_base(force=False, today=None):
    """Base do painel, guardada por 30 minutos. `force` relê o ERP."""
    if not force:
        cached = cache.get(BASE_CACHE_KEY)
        if cached is not None:
            return cached
    today = today or date.today()
    start = dna._add_months(today.replace(day=1), -HISTORY_MONTHS)
    rows = dna._run({
        "cells": (CELLS_SQL, {"start_date": start, "end_date": today + timedelta(days=1)}),
        "backlog": (BACKLOG_SQL, {}),
        "customers": (CUSTOMERS_SQL, {"start_date": start}),
        "reps": (REPS_SQL, {}),
    })
    base = build_base(rows["cells"], rows["backlog"], today)
    base["customers"] = {
        dna._int(row.get("cliente")): {
            "name": (row.get("nome") or "").strip() or f"Cliente {row.get('cliente')}",
            "place": f"{(row.get('cidade') or '').strip().title()}/{(row.get('uf') or '').strip()}",
        }
        for row in rows["customers"]
        if dna._int(row.get("cliente")) is not None
    }
    base["rep_names"] = {}
    for row in rows["reps"]:
        code = dna._int(row.get("codigo"))
        primary = dna.primary_rep(code)
        name = (row.get("nome") or "").strip()
        if primary is not None and name and (primary not in base["rep_names"] or code == primary):
            base["rep_names"][primary] = dna.REPRESENTATIVE_DISPLAY_NAMES.get(primary) or name.title()
    cache.set(BASE_CACHE_KEY, base, BASE_CACHE_SECONDS)
    return base


def clear_cache():
    cache.delete(BASE_CACHE_KEY)


def build_base(cell_rows, backlog_rows, today):
    """Células (cliente, representante, grupo, mês) somadas; dicionários simples (pickle)."""
    cells = {}
    for row in cell_rows:
        customer = dna._int(row.get("cliente"))
        month = dna._date(row.get("mes"))
        if customer is None or month is None:
            continue
        group = dna.product_group(row.get("familia"), "++A" if dna._int(row.get("bi_laminado")) == 1 else "")
        key = (customer, dna.primary_rep(row.get("representante")), group, dna._month_key(month))
        cell = cells.setdefault(key, {"net": ZERO, "tsi": ZERO, "kg": ZERO, "cpv": ZERO, "cpv_base": ZERO, "cpv_known": False})
        cell["net"] += dna._num(row.get("liquido"))
        cell["tsi"] += dna._num(row.get("sem_impostos"))
        cell["kg"] += dna._num(row.get("peso"))
        if row.get("cpv") is not None:
            cell["cpv"] += dna._num(row.get("cpv"))
            cell["cpv_base"] += dna._num(row.get("base_cpv"))
            cell["cpv_known"] = True
    backlog = {}
    for row in backlog_rows:
        month = dna._date(row.get("mes"))
        if month is None:
            continue
        backlog[dna._month_key(month)] = {
            "value": dna._num(row.get("valor")),
            "kg": dna._num(row.get("peso")),
            "items": int(row.get("itens") or 0),
            "customers": int(row.get("clientes") or 0),
        }
    covered = sum((cell["cpv_base"] for cell in cells.values()), ZERO)
    cost_ratio = sum((cell["cpv"] for cell in cells.values()), ZERO) / covered if covered else None
    return {
        "loaded_at": timezone.now(),
        "today": today,
        "cells": cells,
        "backlog": backlog,
        # Custo ÷ faturamento sem impostos nos itens com CPV: estima o custo dos itens sem CPV.
        "cost_ratio": cost_ratio,
    }


def cell_cost(cell, cost_ratio):
    """CPV da célula; a parte sem CPV no ERP é estimada pela relação custo ÷ sem impostos da empresa."""
    uncovered = max(cell["tsi"] - cell["cpv_base"], ZERO) if cell["cpv_known"] else cell["tsi"]
    return cell["cpv"] + uncovered * (cost_ratio if cost_ratio is not None else ZERO)


# ---------------------------------------------------------------------------
# Período (meses fechados)
# ---------------------------------------------------------------------------

def closed_window(period_key=None, today=None):
    today = today or date.today()
    key = period_key if period_key in dict(PERIOD_CHOICES) else DEFAULT_PERIOD
    last = dna._month_key(today) - 1
    if key == "ytd":
        first = (last // 12) * 12
    else:
        first = last - int(key) + 1
    return {
        "key": key,
        "label": dict(PERIOD_CHOICES)[key],
        "first_key": first,
        "last_key": last,
        "previous_first_key": first - 12,
        "previous_last_key": last - 12,
        "months": last - first + 1,
        "range_label": f"{dna._month_label(first)} a {dna._month_label(last)}",
        "previous_range_label": f"{dna._month_label(first - 12)} a {dna._month_label(last - 12)}",
        "choices": [{"key": choice, "label": label} for choice, label in PERIOD_CHOICES],
    }


def filter_base(base, reps=None, groups=None):
    """Base só com as células dos representantes e grupos escolhidos (filtro por clique na tela)."""
    reps = {rep for rep in (reps or ())}
    groups = {group for group in (groups or ())}
    if not reps and not groups:
        return base
    cells = {key: cell for key, cell in base["cells"].items() if (not reps or key[1] in reps) and (not groups or key[2] in groups)}
    return {**base, "cells": cells, "filtered": True}


def restrict_months(base, month_keys):
    """Só os meses escolhidos e os mesmos meses do ano anterior: as pontes e as comparações seguem
    valendo com meses salteados (a seleção cabe em 12 meses, então os dois grupos não se misturam)."""
    allowed = set(month_keys) | {key - 12 for key in month_keys}
    return {**base, "cells": {key: cell for key, cell in base["cells"].items() if key[3] in allowed}}


def selectable_months(today=None):
    """Os 12 últimos meses fechados, que a tela deixa escolher um a um."""
    last = dna._month_key(today or date.today()) - 1
    return list(range(last - 11, last + 1))


def month_window(month_keys, today=None):
    """Janela com os meses escolhidos (dentro dos 12 últimos fechados), contra os mesmos meses do ano anterior."""
    allowed = set(selectable_months(today))
    keys = sorted({int(key) for key in month_keys if int(key) in allowed})
    if not keys:
        return closed_window(None, today)
    first, last = keys[0], keys[-1]
    window = closed_window(None, today)
    label = dna._month_label(first) if len(keys) == 1 else f"{len(keys)} meses escolhidos"
    names = ", ".join(dna._month_label(key) for key in keys)
    previous = ", ".join(dna._month_label(key - 12) for key in keys)
    contiguous = last - first == len(keys) - 1
    window.update({
        "key": "meses",
        "label": label,
        "first_key": first,
        "last_key": last,
        "previous_first_key": first - 12,
        "previous_last_key": last - 12,
        "months": len(keys),
        "month_keys": keys,
        "range_label": f"{dna._month_label(first)} a {dna._month_label(last)}" if contiguous and len(keys) > 1 else names,
        "previous_range_label": f"{dna._month_label(first - 12)} a {dna._month_label(last - 12)}" if contiguous and len(keys) > 1 else previous,
    })
    return window


def slice_cells(base, first_key, last_key, by=("customer", "group")):
    """Soma as células do intervalo pelas dimensões pedidas (customer, rep, group, month)."""
    index = {"customer": 0, "rep": 1, "group": 2, "month": 3}
    result = {}
    for key, cell in base["cells"].items():
        if not first_key <= key[3] <= last_key:
            continue
        target = tuple(key[index[name]] for name in by)
        target = target[0] if len(target) == 1 else target
        total = result.setdefault(target, {"net": ZERO, "tsi": ZERO, "kg": ZERO, "cost": ZERO})
        total["net"] += cell["net"]
        total["tsi"] += cell["tsi"]
        total["kg"] += cell["kg"]
        total["cost"] += cell_cost(cell, base["cost_ratio"])
    for total in result.values():
        total["profit"] = total["tsi"] - total["cost"]
    return result


def totals(slices):
    net = sum((item["net"] for item in slices.values()), ZERO)
    tsi = sum((item["tsi"] for item in slices.values()), ZERO)
    weight = sum((item["kg"] for item in slices.values()), ZERO)
    cost = sum((item["cost"] for item in slices.values()), ZERO)
    profit = tsi - cost
    return {
        "net": net, "tsi": tsi, "kg": weight, "cost": cost, "profit": profit,
        "margin": profit / tsi if tsi else None,
        "price_kg": net / weight if weight else None,
        "tsi_kg": tsi / weight if weight else None,
        "cost_kg": cost / weight if weight else None,
        "profit_kg": profit / weight if weight else None,
    }


# ---------------------------------------------------------------------------
# Pontes (preço, volume, mix, custo, clientes e produtos novos e perdidos)
# ---------------------------------------------------------------------------

def _bridge_parts(previous, current):
    """Separa as células (cliente, grupo) em: clientes perdidos/novos, produtos
    perdidos/novos de clientes que continuam, e as que existem nos dois períodos."""
    previous_customers = {key[0] for key, item in previous.items() if item["net"] > 0}
    current_customers = {key[0] for key, item in current.items() if item["net"] > 0}
    lost_customers = previous_customers - current_customers
    new_customers = current_customers - previous_customers
    parts = {"lost_customers": [], "new_customers": [], "lost_products": [], "new_products": [], "common": []}
    for key, item in previous.items():
        if key[0] in lost_customers:
            parts["lost_customers"].append(item)
        elif key not in current:
            parts["lost_products"].append(item)
    for key, item in current.items():
        if key[0] in new_customers:
            parts["new_customers"].append(item)
        elif key not in previous:
            parts["new_products"].append(item)
        else:
            parts["common"].append((previous[key], item))
    counts = {"lost_customers": len(lost_customers), "new_customers": len(new_customers)}
    return parts, counts


def _sum(items, field):
    return sum((item[field] for item in items), ZERO)


def revenue_bridge(previous, current):
    """Ponte do faturamento: os efeitos somados fecham a diferença entre os períodos.

    Nas células que existem nos dois períodos (mesmo cliente comprando o mesmo grupo):
    volume = (kg atual − kg anterior) × preço médio anterior;
    mix = Σ kg atual × preço anterior da célula − kg atual × preço médio anterior;
    preço = Σ kg atual × (preço atual − preço anterior) da célula.
    """
    parts, counts = _bridge_parts(previous, current)
    priced = [(old, new) for old, new in parts["common"] if old["kg"] > 0 and new["kg"] > 0]
    other = sum((new["net"] - old["net"] for old, new in parts["common"] if not (old["kg"] > 0 and new["kg"] > 0)), ZERO)
    kg_before = sum((old["kg"] for old, _new in priced), ZERO)
    kg_after = sum((new["kg"] for _old, new in priced), ZERO)
    net_before = sum((old["net"] for old, _new in priced), ZERO)
    average = net_before / kg_before if kg_before else ZERO
    at_old_prices = sum((new["kg"] * old["net"] / old["kg"] for old, new in priced), ZERO)
    effects = [
        ("lost_customers", "Clientes perdidos", -_sum(parts["lost_customers"], "net"), f"{counts['lost_customers']} cliente(s) sem compra no período"),
        ("lost_products", "Produtos que deixaram de comprar", -_sum(parts["lost_products"], "net"), "grupos de produto que clientes atuais pararam de comprar"),
        ("volume", "Volume", (kg_after - kg_before) * average, "mais ou menos kg nos mesmos clientes e produtos, ao preço anterior"),
        ("mix", "Mix", at_old_prices - kg_after * average, "troca entre produtos e clientes de preço por kg diferente"),
        ("price", "Preço", sum((new["net"] for _old, new in priced), ZERO) - at_old_prices, "mudança do preço por kg no mesmo cliente e produto"),
        ("new_products", "Produtos novos em clientes atuais", _sum(parts["new_products"], "net"), "venda cruzada: grupos que clientes atuais passaram a comprar"),
        ("new_customers", "Clientes novos", _sum(parts["new_customers"], "net"), f"{counts['new_customers']} cliente(s) que não compraram no ano anterior"),
    ]
    if other:
        effects.append(("other", "Outros", other, "itens sem peso (kg) em um dos períodos"))
    start = sum((item["net"] for item in previous.values()), ZERO)
    end = sum((item["net"] for item in current.values()), ZERO)
    return _bridge(start, end, effects, counts)


def profit_bridge(previous, current):
    """Ponte do lucro bruto: volume e mix ao lucro por kg anterior, preço (sem
    impostos por kg) e custo (CPV por kg) nas mesmas células, e clientes e
    produtos novos e perdidos."""
    parts, counts = _bridge_parts(previous, current)
    priced = [(old, new) for old, new in parts["common"] if old["kg"] > 0 and new["kg"] > 0]
    other = sum((new["profit"] - old["profit"] for old, new in parts["common"] if not (old["kg"] > 0 and new["kg"] > 0)), ZERO)
    kg_before = sum((old["kg"] for old, _new in priced), ZERO)
    kg_after = sum((new["kg"] for _old, new in priced), ZERO)
    profit_before = sum((old["profit"] for old, _new in priced), ZERO)
    average = profit_before / kg_before if kg_before else ZERO
    at_old_unit = sum((new["kg"] * old["profit"] / old["kg"] for old, new in priced), ZERO)
    price = sum((new["kg"] * (new["tsi"] / new["kg"] - old["tsi"] / old["kg"]) for old, new in priced), ZERO)
    cost = -sum((new["kg"] * (new["cost"] / new["kg"] - old["cost"] / old["kg"]) for old, new in priced), ZERO)
    effects = [
        ("lost_customers", "Clientes perdidos", -_sum(parts["lost_customers"], "profit"), f"{counts['lost_customers']} cliente(s) sem compra no período"),
        ("lost_products", "Produtos que deixaram de comprar", -_sum(parts["lost_products"], "profit"), "lucro dos grupos que clientes atuais pararam de comprar"),
        ("volume", "Volume", (kg_after - kg_before) * average, "mais ou menos kg, ao lucro por kg anterior"),
        ("mix", "Mix", at_old_unit - kg_after * average, "troca para produtos e clientes de lucro por kg diferente"),
        ("price", "Preço", price, "preço sem impostos por kg no mesmo cliente e produto"),
        ("cost", "Custo", cost, "CPV por kg no mesmo cliente e produto (custo maior reduz o lucro)"),
        ("new_products", "Produtos novos em clientes atuais", _sum(parts["new_products"], "profit"), "lucro da venda cruzada"),
        ("new_customers", "Clientes novos", _sum(parts["new_customers"], "profit"), f"{counts['new_customers']} cliente(s) novo(s)"),
    ]
    if other:
        effects.append(("other", "Outros", other, "itens sem peso (kg) em um dos períodos"))
    start = sum((item["profit"] for item in previous.values()), ZERO)
    end = sum((item["profit"] for item in current.values()), ZERO)
    return _bridge(start, end, effects, counts)


def _bridge(start, end, effects, counts):
    steps = [{"key": key, "label": label, "value": value, "detail": detail} for key, label, value, detail in effects]
    residual = end - start - sum((step["value"] for step in steps), ZERO)
    biggest = max(steps, key=lambda step: abs(step["value"])) if steps else None
    return {
        "start": start,
        "end": end,
        "change": end - start,
        "change_pct": (end - start) / start if start else None,
        "steps": steps,
        "residual": residual,  # deve ser ~0 (arredondamento)
        "biggest": biggest,
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# Retenção de receita
# ---------------------------------------------------------------------------

def retention(previous, current):
    """NRR, GRR e o fluxo de receita por cliente entre os dois períodos."""
    start = sum((value for value in previous.values() if value > 0), ZERO)
    churn = contraction = expansion = new = retained_now = retained_capped = ZERO
    retained = lost = new_count = 0
    flows = {"lost": [], "new": [], "expansion": [], "contraction": []}
    for customer, before in previous.items():
        if before <= 0:
            continue
        after = current.get(customer, ZERO)
        if after <= 0:
            lost += 1
            churn += before
            flows["lost"].append((customer, before))
            continue
        retained += 1
        retained_now += after
        retained_capped += min(after, before)
        if after >= before:
            expansion += after - before
            flows["expansion"].append((customer, after - before))
        else:
            contraction += before - after
            flows["contraction"].append((customer, before - after))
    for customer, after in current.items():
        if after > 0 and previous.get(customer, ZERO) <= 0:
            new_count += 1
            new += after
            flows["new"].append((customer, after))
    for key in flows:
        flows[key].sort(key=lambda pair: pair[1], reverse=True)
    end = start - churn - contraction + expansion + new
    return {
        "start": start,
        "end": end,
        "nrr": retained_now / start if start else None,
        "grr": retained_capped / start if start else None,
        "logo_retention": Decimal(retained) / Decimal(retained + lost) if retained + lost else None,
        "retained": retained,
        "lost": lost,
        "new": new_count,
        "churn": churn,
        "contraction": contraction,
        "expansion": expansion,
        "new_revenue": new,
        "flows": flows,
    }


# ---------------------------------------------------------------------------
# Matriz de rentabilidade, produtos, representantes
# ---------------------------------------------------------------------------

def quadrant(net, margin, net_reference, margin_reference):
    high_net = net >= net_reference
    high_margin = margin >= margin_reference
    if high_net and high_margin:
        return "stars"
    if high_net:
        return "volume"
    if high_margin:
        return "niche"
    return "review"


def profitability_matrix(customers, company_margin):
    """Clientes no plano faturamento × margem. Referências: a mediana do faturamento
    dos clientes e a margem da empresa no período."""
    points = [(code, item) for code, item in customers.items() if item["net"] > 0 and item["tsi"] > 0]
    if not points:
        return {"points": [], "quadrants": [], "net_reference": None, "margin_reference": company_margin}
    net_reference = statistics.median([item["net"] for _code, item in points])
    margin_reference = company_margin if company_margin is not None else ZERO
    summary = {key: {"count": 0, "net": ZERO, "profit": ZERO} for key in QUADRANT_ORDER}
    plotted = []
    for code, item in points:
        margin = item["profit"] / item["tsi"]
        key = quadrant(item["net"], margin, net_reference, margin_reference)
        summary[key]["count"] += 1
        summary[key]["net"] += item["net"]
        summary[key]["profit"] += item["profit"]
        plotted.append({"code": code, "net": item["net"], "margin": margin, "profit": item["profit"], "quadrant": key})
    plotted.sort(key=lambda point: point["net"], reverse=True)
    total_profit = sum((value["profit"] for value in summary.values()), ZERO)
    return {
        "points": plotted[:MATRIX_POINTS],
        "total_points": len(plotted),
        "net_reference": net_reference,
        "margin_reference": margin_reference,
        "quadrants": [
            {"key": key, "label": QUADRANTS[key][0], "description": QUADRANTS[key][1], **summary[key],
             "profit_share": summary[key]["profit"] / total_profit if total_profit else None}
            for key in QUADRANT_ORDER
        ],
    }


def group_profitability(previous_groups, current_groups):
    """Rentabilidade por grupo de produto e quanto cada um mudou o lucro."""
    rows = []
    for group in set(previous_groups) | set(current_groups):
        now = current_groups.get(group) or {"net": ZERO, "tsi": ZERO, "kg": ZERO, "cost": ZERO, "profit": ZERO}
        before = previous_groups.get(group) or {"net": ZERO, "tsi": ZERO, "kg": ZERO, "cost": ZERO, "profit": ZERO}
        if now["net"] <= 0 and before["net"] <= 0:
            continue
        rows.append({
            "group": group,
            "net": now["net"],
            "profit": now["profit"],
            "margin": now["profit"] / now["tsi"] if now["tsi"] else None,
            "kg": now["kg"],
            "price_kg": now["net"] / now["kg"] if now["kg"] else None,
            "cost_kg": now["cost"] / now["kg"] if now["kg"] else None,
            "previous_net": before["net"],
            "previous_margin": before["profit"] / before["tsi"] if before["tsi"] else None,
            "growth": dna._change(now["net"], before["net"]) if before["net"] else None,
            "profit_change": now["profit"] - before["profit"],
        })
    rows.sort(key=lambda row: row["profit"], reverse=True)
    return rows


def margin_heatmap(base, window, top_groups):
    """Margem de cada grupo (os maiores) em cada mês do período."""
    by_group_month = slice_cells(base, window["first_key"], window["last_key"], by=("group", "month"))
    months = list(window.get("month_keys") or range(window["first_key"], window["last_key"] + 1))
    rows = []
    for group in top_groups:
        cells = []
        for month in months:
            item = by_group_month.get((group, month))
            margin = item["profit"] / item["tsi"] if item and item["tsi"] else None
            cells.append({"month": month, "margin": margin, "net": item["net"] if item else ZERO})
        rows.append({"group": group, "cells": cells})
    return {"months": [dna._month_label(month) for month in months], "keys": months, "rows": rows}


def seasonality(base, today):
    """Faturamento por ano e mês (meses fechados e o mês corrente até hoje)."""
    by_month = slice_cells(base, dna._month_key(today) - HISTORY_MONTHS, dna._month_key(today), by=("month",))
    years = sorted({month // 12 for month in by_month})
    grid = []
    for year in years:
        cells = []
        for month in range(12):
            key = year * 12 + month
            item = by_month.get(key)
            cells.append({"key": key, "net": item["net"] if item else None, "partial": key == dna._month_key(today)})
        grid.append({"year": year, "cells": cells})
    return {"grid": grid, "by_month": {key: item["net"] for key, item in by_month.items()}}


def forecast(base, today, months=FORECAST_MONTHS):
    """Projeção simples dos próximos meses: o mesmo mês do ano anterior × o
    crescimento dos últimos 6 meses fechados contra os mesmos meses um ano antes.
    Ao lado, a carteira a faturar com entrega em cada mês."""
    by_month = {key: item["net"] for key, item in slice_cells(base, dna._month_key(today) - HISTORY_MONTHS, dna._month_key(today), by=("month",)).items()}
    last_closed = dna._month_key(today) - 1
    recent = sum((by_month.get(key, ZERO) for key in range(last_closed - 5, last_closed + 1)), ZERO)
    year_before = sum((by_month.get(key - 12, ZERO) for key in range(last_closed - 5, last_closed + 1)), ZERO)
    growth = recent / year_before - 1 if year_before else None
    current = dna._month_key(today)
    overdue = sum((item["value"] for key, item in base["backlog"].items() if key < current), ZERO)
    rows = []
    for key in range(current, current + months):
        reference = by_month.get(key - 12)
        projected = reference * (1 + growth) if reference is not None and growth is not None else None
        backlog = base["backlog"].get(key, {}).get("value", ZERO)
        if key == current:
            backlog += overdue  # pedido atrasado entra no mês corrente
        rows.append({
            "key": key,
            "label": dna._month_label(key),
            "projected": projected,
            "last_year": reference,
            "backlog": backlog,
            "billed": by_month.get(key) if key == current else None,
            "coverage": backlog / projected if projected else None,
        })
    return {"growth": growth, "rows": rows, "overdue_backlog": overdue,
            "backlog_total": sum((item["value"] for item in base["backlog"].values()), ZERO)}


def rep_performance(base, window, goals, names):
    """Por representante: resultado, crescimento, clientes perdidos e a meta de kg do ano."""
    current = slice_cells(base, window["first_key"], window["last_key"], by=("rep", "customer"))
    previous = slice_cells(base, window["previous_first_key"], window["previous_last_key"], by=("rep", "customer"))
    reps = defaultdict(lambda: {"net": ZERO, "tsi": ZERO, "profit": ZERO, "kg": ZERO, "customers": set(), "previous_net": ZERO, "previous_customers": set()})
    for (rep, customer), item in current.items():
        entry = reps[rep]
        entry["net"] += item["net"]
        entry["tsi"] += item["tsi"]
        entry["profit"] += item["profit"]
        entry["kg"] += item["kg"]
        if item["net"] > 0:
            entry["customers"].add(customer)
    for (rep, customer), item in previous.items():
        entry = reps[rep]
        entry["previous_net"] += item["net"]
        if item["net"] > 0:
            entry["previous_customers"].add(customer)
    year = window["last_key"] // 12
    last_month = window["last_key"] % 12 + 1
    chosen = [key for key in (window.get("month_keys") or ()) if key // 12 == year]
    if chosen:
        ytd = slice_cells(base, min(chosen), max(chosen), by=("rep",))
        wanted = {key % 12 + 1 for key in chosen}
    else:
        ytd = slice_cells(base, year * 12, window["last_key"], by=("rep",))
        wanted = set(range(1, last_month + 1))
    goal_by_rep = defaultdict(lambda: ZERO)
    for goal in goals:
        if int(goal["year"]) == year and int(goal["month"]) in wanted:
            goal_by_rep[dna.primary_rep(goal["rep_code"])] += dna._num(goal["weight_kg"])
    total_net = sum((entry["net"] for entry in reps.values()), ZERO)
    rows = []
    for rep, entry in reps.items():
        if entry["net"] <= 0 and entry["previous_net"] <= 0:
            continue
        goal = goal_by_rep.get(rep, ZERO)
        billed = ytd.get(rep, {}).get("kg", ZERO)
        rows.append({
            "code": rep,
            "name": names.get(rep) or (f"Representante {rep}" if rep is not None else "Sem representante"),
            "net": entry["net"],
            "profit": entry["profit"],
            "margin": entry["profit"] / entry["tsi"] if entry["tsi"] else None,
            "growth": dna._change(entry["net"], entry["previous_net"]) if entry["previous_net"] else None,
            "share": entry["net"] / total_net if total_net else None,
            "customers": len(entry["customers"]),
            "lost_customers": len(entry["previous_customers"] - entry["customers"]),
            "new_customers": len(entry["customers"] - entry["previous_customers"]),
            "goal_kg": goal,
            "billed_kg": billed,
            "attainment": billed / goal if goal else None,
        })
    rows.sort(key=lambda row: row["net"], reverse=True)
    goal_label = f"jan a {dna._month_label(window['last_key'])}" if not chosen else ", ".join(dna._month_label(key) for key in chosen)
    return {"rows": rows, "goal_year": year, "goal_months": last_month, "goal_label": goal_label}


def concentration(customers):
    """Dependência dos maiores clientes: participação dos 1, 5 e 10 maiores no lucro."""
    profits = sorted((item["profit"] for item in customers.values() if item["profit"] > 0), reverse=True)
    total = sum(profits, ZERO)
    if not total:
        return {"top1": None, "top5": None, "top10": None, "hhi": None}
    share = lambda count: sum(profits[:count], ZERO) / total  # noqa: E731
    return {
        "top1": share(1),
        "top5": share(5),
        "top10": share(10),
        "hhi": sum(((value / total) ** 2 for value in profits), ZERO) * 10000,
    }


# ---------------------------------------------------------------------------
# Tudo junto (o que a tela usa)
# ---------------------------------------------------------------------------

def analyze(base, window, goals=(), names=None, customer_names=None, full_base=None):
    """`base` pode vir filtrada por representante e grupo; `full_base` (a empresa toda) alimenta a
    projeção, porque a carteira a faturar não se divide por essas dimensões."""
    names = names or {}
    customer_names = customer_names or {}
    history = base
    if window.get("month_keys"):
        base = restrict_months(base, window["month_keys"])
    current = slice_cells(base, window["first_key"], window["last_key"], by=("customer", "group"))
    previous = slice_cells(base, window["previous_first_key"], window["previous_last_key"], by=("customer", "group"))
    current_customers = slice_cells(base, window["first_key"], window["last_key"], by=("customer",))
    previous_customers = slice_cells(base, window["previous_first_key"], window["previous_last_key"], by=("customer",))
    current_groups = slice_cells(base, window["first_key"], window["last_key"], by=("group",))
    previous_groups = slice_cells(base, window["previous_first_key"], window["previous_last_key"], by=("group",))
    now = totals(current_customers)
    before = totals(previous_customers)
    groups = group_profitability(previous_groups, current_groups)
    top_groups = [row["group"] for row in sorted(groups, key=lambda row: row["net"], reverse=True)[:HEATMAP_GROUPS]]
    retention_data = retention(
        {code: item["net"] for code, item in previous_customers.items()},
        {code: item["net"] for code, item in current_customers.items()},
    )
    return {
        "window": window,
        "now": now,
        "before": before,
        "customers_now": sum(1 for item in current_customers.values() if item["net"] > 0),
        "customers_before": sum(1 for item in previous_customers.values() if item["net"] > 0),
        "revenue_bridge": revenue_bridge(previous, current),
        "profit_bridge": profit_bridge(previous, current),
        "retention": retention_data,
        "matrix": profitability_matrix(current_customers, now["margin"]),
        "groups": groups,
        "heatmap": margin_heatmap(base, window, top_groups),
        "seasonality": seasonality(history, history["today"]),
        "forecast": forecast(full_base or history, history["today"]),
        "filtered": bool(history.get("filtered")),
        "reps": rep_performance(base, window, goals, names),
        "concentration": concentration(current_customers),
        "customer_names": customer_names,
    }


# ---------------------------------------------------------------------------
# Textos, gráficos e IA
# ---------------------------------------------------------------------------

def _f(value, digits=2):
    return None if value is None else round(float(value), digits)


def money(value):
    return intel.money(value)


def pct(value, signed=False):
    return intel.pct(value, signed)


def pp(now, before):
    """Diferença em pontos percentuais (margem)."""
    if now is None or before is None:
        return None
    return (now - before) * 100


def _delta(now, before, good_when_up=True, kind="pct"):
    if kind == "pp":
        value = pp(now, before)
        if value is None:
            return {"display": "–", "tone": ""}
        tone = "is-up" if (value >= 0) == good_when_up else "is-down"
        return {"display": f"{value:+.1f}".replace(".", ",") + " p.p.", "tone": tone if abs(value) >= Decimal("0.05") else ""}
    change = dna._change(now, before) if before else None
    if change is None:
        return {"display": "–", "tone": ""}
    tone = "is-up" if (change >= 0) == good_when_up else "is-down"
    return {"display": pct(change, signed=True), "tone": tone if abs(change) >= Decimal("0.001") else ""}


def kpis(analysis):
    now, before = analysis["now"], analysis["before"]
    retention_data = analysis["retention"]
    concentration_data = analysis["concentration"]
    return [
        {"label": "Faturamento líquido", "value": money(now["net"]), "delta": _delta(now["net"], before["net"]),
         "foot": f"antes {money(before['net'])}"},
        {"label": "Lucro bruto", "value": money(now["profit"]), "delta": _delta(now["profit"], before["profit"]),
         "foot": f"antes {money(before['profit'])}"},
        {"label": "Margem bruta", "value": pct(now["margin"]), "delta": _delta(now["margin"], before["margin"], kind="pp"),
         "foot": f"antes {pct(before['margin'])}"},
        {"label": "Volume", "value": intel.kg(now["kg"]), "delta": _delta(now["kg"], before["kg"]),
         "foot": f"antes {intel.kg(before['kg'])}"},
        {"label": "Preço médio", "value": f"{money(now['price_kg'])}/kg", "delta": _delta(now["price_kg"], before["price_kg"]),
         "foot": "faturamento ÷ kg"},
        {"label": "Custo médio (CPV)", "value": f"{money(now['cost_kg'])}/kg",
         "delta": _delta(now["cost_kg"], before["cost_kg"], good_when_up=False), "foot": "custo do produto vendido ÷ kg"},
        {"label": "Lucro bruto por kg", "value": f"{money(now['profit_kg'])}/kg", "delta": _delta(now["profit_kg"], before["profit_kg"]),
         "foot": f"antes {money(before['profit_kg'])}/kg"},
        {"label": "Retenção de receita (NRR)", "value": pct(retention_data["nrr"]),
         "delta": {"display": f"GRR {pct(retention_data['grr'])}", "tone": ""}, "foot": "receita de hoje dos clientes do ano anterior"},
        {"label": "Clientes ativos", "value": str(analysis["customers_now"]),
         "delta": _delta(Decimal(analysis["customers_now"]), Decimal(analysis["customers_before"])),
         "foot": f"{retention_data['new']} novos · {retention_data['lost']} perdidos"},
        {"label": "Dependência dos 10 maiores", "value": pct(concentration_data["top10"]),
         "delta": {"display": f"maior: {pct(concentration_data['top1'])}", "tone": ""}, "foot": "participação no lucro bruto"},
    ]


def bridge_chart(bridge):
    return {
        "start": _f(bridge["start"], 0),
        "end": _f(bridge["end"], 0),
        "steps": [{"label": step["label"], "value": _f(step["value"], 0), "detail": step["detail"]} for step in bridge["steps"]],
    }


def bridge_rows(bridge):
    rows = []
    for step in bridge["steps"]:
        share = step["value"] / abs(bridge["change"]) if bridge["change"] else None
        rows.append({
            "label": step["label"],
            "detail": step["detail"],
            "value_display": ("+" if step["value"] > 0 else "") + money(step["value"]),
            "tone": "is-up" if step["value"] > 0 else "is-down" if step["value"] < 0 else "",
            "share_display": pct(share, signed=True) if share is not None else "–",
        })
    return rows


def _seq_classes(values, classes=5):
    """Faixa (1..5) por quantil, para o mapa de calor de magnitude."""
    ordered = sorted(value for value in values if value is not None and value > 0)
    if not ordered:
        return lambda value: ""
    breaks = [ordered[min(len(ordered) - 1, (index * len(ordered)) // classes)] for index in range(1, classes)]

    def classify(value):
        if value is None or value <= 0:
            return ""
        position = sum(1 for edge in breaks if value > edge)
        return f"is-seq-{position + 1}"
    return classify


def _div_class(value, reference, step=Decimal("0.025")):
    if value is None or reference is None:
        return ""
    delta = value - reference
    if delta <= -3 * step:
        return "is-div-neg-2"
    if delta < -step:
        return "is-div-neg-1"
    if delta <= step:
        return "is-div-mid"
    if delta < 3 * step:
        return "is-div-pos-1"
    return "is-div-pos-2"


def heatmap_display(heatmap, reference):
    rows = []
    for row in heatmap["rows"]:
        cells = []
        for cell, label in zip(row["cells"], heatmap["months"]):
            cells.append({
                "value": pct(cell["margin"]) if cell["margin"] is not None else "–",
                "css": _div_class(cell["margin"], reference),
                "title": f"{row['group'].title()} · {label}: margem {pct(cell['margin'])} · faturamento {money(cell['net'])}",
            })
        rows.append({"key": row["group"], "group": row["group"].title(), "cells": cells})
    months = [
        {"label": label, "key": key, "param": f"{key // 12}-{key % 12 + 1:02d}" if key is not None else ""}
        for label, key in zip(heatmap["months"], heatmap.get("keys") or [None] * len(heatmap["months"]))
    ]
    return {"months": months, "rows": rows, "reference": pct(reference)}


def seasonality_display(seasonality_data):
    classify = _seq_classes([cell["net"] for row in seasonality_data["grid"] for cell in row["cells"] if not cell["partial"]])
    rows = []
    for row in seasonality_data["grid"]:
        cells = []
        for cell in row["cells"]:
            if cell["net"] is None:
                cells.append({"value": "", "css": "", "title": ""})
                continue
            suffix = " (mês em andamento)" if cell["partial"] else ""
            cells.append({
                # Em R$ milhões, com uma casa: 12 meses cabem na largura do card.
                "value": f"{cell['net'] / 1000000:.1f}".replace(".", ","),
                "css": "is-partial" if cell["partial"] else classify(cell["net"]),
                "title": f"{dna._month_label(cell['key'])}: {money(cell['net'])}{suffix}",
            })
        rows.append({"year": row["year"], "cells": cells})
    return {"months": list(dna.MONTH_NAMES), "rows": rows}


def forecast_display(forecast_data):
    return {
        "growth": pct(forecast_data["growth"], signed=True) if forecast_data["growth"] is not None else "–",
        "backlog_total": money(forecast_data["backlog_total"]),
        "overdue": money(forecast_data["overdue_backlog"]),
        "has_overdue": forecast_data["overdue_backlog"] > 0,
        "rows": [
            {"label": row["label"], "projected": money(row["projected"]), "last_year": money(row["last_year"]),
             "backlog": money(row["backlog"]), "billed": money(row["billed"]) if row["billed"] is not None else None,
             "coverage": pct(row["coverage"]) if row["coverage"] is not None else "–",
             "coverage_width": min(round(float(row["coverage"] or 0) * 100), 100)}
            for row in forecast_data["rows"]
        ],
        "chart": {
            "labels": [row["label"] for row in forecast_data["rows"]],
            "projected": [_f(row["projected"], 0) for row in forecast_data["rows"]],
            "backlog": [_f(row["backlog"], 0) for row in forecast_data["rows"]],
            "billed": [_f(row["billed"], 0) if row["billed"] is not None else None for row in forecast_data["rows"]],
        },
    }


def matrix_display(matrix, customers):
    return {
        "net_reference": money(matrix["net_reference"]),
        "margin_reference": pct(matrix["margin_reference"]),
        "total_points": matrix.get("total_points", 0),
        "quadrants": [
            {**row, "net_display": money(row["net"]), "profit_display": money(row["profit"]),
             "profit_share_display": pct(row["profit_share"]) if row["profit_share"] is not None else "–"}
            for row in matrix["quadrants"]
        ],
        "chart": {
            "net_reference": _f(matrix["net_reference"], 0),
            "margin_reference": _f(matrix["margin_reference"], 4),
            "points": [
                {"code": point["code"], "name": (customers.get(point["code"]) or {}).get("name", f"Cliente {point['code']}"),
                 "place": (customers.get(point["code"]) or {}).get("place", ""),
                 "x": _f(point["net"], 0), "y": _f(point["margin"], 4), "size": _f(max(point["profit"], ZERO), 0),
                 "quadrant": point["quadrant"]}
                for point in matrix["points"]
            ],
        },
    }


def groups_display(groups):
    largest = max((abs(row["profit_change"]) for row in groups), default=ZERO) or ONE
    rows = []
    for row in groups:
        rows.append({
            "key": row["group"],
            "group": row["group"].title(),
            "net": money(row["net"]),
            "profit": money(row["profit"]),
            "margin": pct(row["margin"]),
            "margin_delta": _delta(row["margin"], row["previous_margin"], kind="pp"),
            "price_kg": money(row["price_kg"]) if row["price_kg"] is not None else "n/d",
            "cost_kg": money(row["cost_kg"]) if row["cost_kg"] is not None else "n/d",
            "growth": pct(row["growth"], signed=True) if row["growth"] is not None else "novo",
            "growth_tone": "is-up" if row["growth"] and row["growth"] > 0 else "is-down" if row["growth"] and row["growth"] < 0 else "",
            "profit_change": ("+" if row["profit_change"] > 0 else "") + money(row["profit_change"]),
            "profit_change_tone": "is-up" if row["profit_change"] > 0 else "is-down" if row["profit_change"] < 0 else "",
            "bar": round(float(abs(row["profit_change"]) / largest) * 50, 1),
            "bar_side": "pos" if row["profit_change"] >= 0 else "neg",
        })
    return rows


def reps_display(reps):
    rows = []
    for row in reps["rows"]:
        attainment = row["attainment"]
        rows.append({
            "code": row["code"],
            "name": row["name"],
            "net": money(row["net"]),
            "profit": money(row["profit"]),
            "margin": pct(row["margin"]),
            "share": pct(row["share"]),
            "growth": pct(row["growth"], signed=True) if row["growth"] is not None else "–",
            "growth_tone": "is-up" if row["growth"] and row["growth"] > 0 else "is-down" if row["growth"] and row["growth"] < 0 else "",
            "customers": row["customers"],
            "new_customers": row["new_customers"],
            "lost_customers": row["lost_customers"],
            "goal": intel.kg(row["goal_kg"]) if row["goal_kg"] else "sem meta",
            "billed": intel.kg(row["billed_kg"]),
            "attainment": pct(attainment) if attainment is not None else "–",
            "attainment_width": min(round(float(attainment or 0) * 100), 100),
            "attainment_tone": "" if attainment is None else "is-good" if attainment >= 1 else "is-warn" if attainment >= Decimal("0.8") else "is-bad",
        })
    return rows


def flows_display(retention_data, customers, limit=6):
    def rows(key):
        return [
            {"code": code, "name": (customers.get(code) or {}).get("name", f"Cliente {code}"),
             "place": (customers.get(code) or {}).get("place", ""), "value": money(value)}
            for code, value in retention_data["flows"][key][:limit]
        ]
    return {"lost": rows("lost"), "new": rows("new"), "expansion": rows("expansion"), "contraction": rows("contraction")}


def simulator_data(analysis):
    now = analysis["now"]
    return {
        "net": _f(now["net"], 0), "tsi": _f(now["tsi"], 0), "cost": _f(now["cost"], 0), "kg": _f(now["kg"], 0),
        "profit": _f(now["profit"], 0), "months": analysis["window"]["months"],
    }


def management_ai_data(analysis):
    """O que vai para a OpenAI: só os números agregados do painel."""
    now, before = analysis["now"], analysis["before"]
    customers = analysis["customer_names"]

    def bridge(data):
        return {
            "inicio": _f(data["start"], 0),
            "fim": _f(data["end"], 0),
            "variacao_pct": _f(data["change_pct"] * 100, 1) if data["change_pct"] is not None else None,
            "efeitos": [{"efeito": step["label"], "valor": _f(step["value"], 0)} for step in data["steps"]],
        }

    def name(code):
        return (customers.get(code) or {}).get("name", f"Cliente {code}")

    retention_data = analysis["retention"]
    forecast_data = analysis["forecast"]
    return {
        "periodo": f"{analysis['window']['label']} ({analysis['window']['range_label']}) contra {analysis['window']['previous_range_label']}",
        "indicadores": {
            "faturamento": _f(now["net"], 0), "faturamento_anterior": _f(before["net"], 0),
            "lucro_bruto": _f(now["profit"], 0), "lucro_bruto_anterior": _f(before["profit"], 0),
            "margem_pct": _f((now["margin"] or 0) * 100, 1), "margem_anterior_pct": _f((before["margin"] or 0) * 100, 1),
            "kg": _f(now["kg"], 0), "kg_anterior": _f(before["kg"], 0),
            "preco_kg": _f(now["price_kg"]), "preco_kg_anterior": _f(before["price_kg"]),
            "custo_kg": _f(now["cost_kg"]), "custo_kg_anterior": _f(before["cost_kg"]),
        },
        "ponte_faturamento": bridge(analysis["revenue_bridge"]),
        "ponte_lucro_bruto": bridge(analysis["profit_bridge"]),
        "retencao": {
            "nrr_pct": _f((retention_data["nrr"] or 0) * 100, 1),
            "grr_pct": _f((retention_data["grr"] or 0) * 100, 1),
            "clientes_retidos": retention_data["retained"],
            "clientes_perdidos": retention_data["lost"],
            "clientes_novos": retention_data["new"],
            "receita_perdida": _f(retention_data["churn"], 0),
            "receita_nova": _f(retention_data["new_revenue"], 0),
            "maiores_perdidos": [{"cliente": name(code), "faturamento_anterior": _f(value, 0)} for code, value in retention_data["flows"]["lost"][:5]],
        },
        "matriz_rentabilidade": [
            {"quadrante": row["label"], "clientes": row["count"], "faturamento": _f(row["net"], 0), "lucro_bruto": _f(row["profit"], 0)}
            for row in analysis["matrix"]["quadrants"]
        ],
        "grupos_de_produto": [
            {"grupo": row["group"].title(), "faturamento": _f(row["net"], 0), "lucro_bruto": _f(row["profit"], 0),
             "margem_pct": _f((row["margin"] or 0) * 100, 1),
             "crescimento_pct": _f(row["growth"] * 100, 1) if row["growth"] is not None else None,
             "variacao_lucro": _f(row["profit_change"], 0)}
            for row in analysis["groups"][:8]
        ],
        "representantes": [
            {"representante": row["name"], "faturamento": _f(row["net"], 0), "margem_pct": _f((row["margin"] or 0) * 100, 1),
             "crescimento_pct": _f(row["growth"] * 100, 1) if row["growth"] is not None else None,
             "meta_kg_atingida_pct": _f(row["attainment"] * 100, 1) if row["attainment"] is not None else None,
             "clientes_perdidos": row["lost_customers"]}
            for row in analysis["reps"]["rows"][:10]
        ],
        "concentracao": {
            "maior_cliente_pct_lucro": _f((analysis["concentration"]["top1"] or 0) * 100, 1),
            "dez_maiores_pct_lucro": _f((analysis["concentration"]["top10"] or 0) * 100, 1),
        },
        "projecao": {
            "crescimento_recente_pct": _f(forecast_data["growth"] * 100, 1) if forecast_data["growth"] is not None else None,
            "carteira_a_faturar": _f(forecast_data["backlog_total"], 0),
            "carteira_atrasada": _f(forecast_data["overdue_backlog"], 0),
            "meses": [{"mes": row["label"], "projetado": _f(row["projected"], 0), "carteira": _f(row["backlog"], 0)} for row in forecast_data["rows"]],
        },
    }
