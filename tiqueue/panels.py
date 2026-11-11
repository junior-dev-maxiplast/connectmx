"""Painéis-padrão de mercado no Dashes: Vendas, Contas a Receber, Entregas (OTIF) e Qualidade.

Cada painel lê o ERP Senior com poucas consultas agregadas (cache curto) e entrega
à tela "fatos" compactos: linhas já somadas por dia ou mês e pelas dimensões que a
tela filtra (representante, grupo de produto, filial, UF, cliente...). O navegador
soma esses fatos a cada clique, então o filtro cruzado é instantâneo e animado.

As regras de faturamento são as do DNA (VL_FAT-DEV, transações que contam como
venda, clientes intercompany fora). Nenhuma consulta altera dados.
"""

import calendar as calendar_module
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from . import commercial_dna as dna

ZERO = Decimal("0")
VERSION_KEY = "panels:version"
CURRENT_SECONDS = 3 * 60  # o mês corrente muda o dia todo
HISTORY_SECONDS = 30 * 60
IN_FULL = Decimal("0.9")  # entrega "completa" com 90% da quantidade (tolerância usual de embalagem flexível)
OTIF_REFERENCE = 0.95
DELAY_BUCKETS = (
    ("early", "Adiantado"), ("ontime", "No dia"), ("d3", "1 a 3 dias"), ("d7", "4 a 7 dias"),
    ("d15", "8 a 15 dias"), ("d30", "16 a 30 dias"), ("d30p", "Mais de 30 dias"),
    ("short", "Entregue incompleto"), ("open", "Ainda não entregue"),
)
AGING_BUCKETS = (
    ("v90", "Vencido há mais de 90 dias", "overdue"), ("v61", "Vencido 61 a 90 dias", "overdue"),
    ("v31", "Vencido 31 a 60 dias", "overdue"), ("v1", "Vencido 1 a 30 dias", "overdue"),
    ("a7", "A vencer em até 7 dias", "due"), ("a30", "A vencer em 8 a 30 dias", "due"),
    ("a60", "A vencer em 31 a 60 dias", "due"), ("a61", "A vencer em mais de 60 dias", "due"),
)
SEVERITY = {"L": "Leve", "M": "Média", "G": "Grave"}

_RATIO = dna._RETURN_RATIO_SQL
_BI_SQL = (
    "CASE WHEN INSTR(I.CPLIPV, '+', 1, 2) > 0 AND INSTR(SUBSTR(I.CPLIPV, INSTR(I.CPLIPV, '+', 1, 2) + 1, "
    "CASE WHEN INSTR(I.CPLIPV, '+', 1, 3) > 0 THEN INSTR(I.CPLIPV, '+', 1, 3) - INSTR(I.CPLIPV, '+', 1, 2) - 1 "
    "ELSE 4000 END), 'A') > 0 THEN 1 ELSE 0 END"
)


# ---------------------------------------------------------------------------
# Cache (versão: "Reler o ERP" invalida todos os painéis de uma vez)
# ---------------------------------------------------------------------------

def _version():
    version = cache.get(VERSION_KEY)
    if version is None:
        version = 1
        cache.set(VERSION_KEY, version, None)
    return version


def cached(name, seconds, loader):
    key = f"panels:v{_version()}:{name}"
    value = cache.get(key)
    if value is None:
        value = loader()
        cache.set(key, value, seconds)
    return value


def clear_cache():
    cache.set(VERSION_KEY, _version() + 1, None)


def _iso(value):
    value = dna._date(value)
    return value.isoformat() if value else None


def _f(value, digits=2):
    return None if value is None else round(float(value), digits)


def _group(family, bi):
    return dna.product_group(family, "++A" if dna._int(bi) == 1 else "").title()


def _branch_key(company, branch):
    return f"{dna._int(company)}-{dna._int(branch)}"


# ---------------------------------------------------------------------------
# Nomes (representantes, filiais e clientes)
# ---------------------------------------------------------------------------

REPS_SQL = "SELECT CODREP AS CODIGO, NOMREP AS NOME FROM E090REP"
BRANCHES_SQL = "SELECT CODEMP AS EMPRESA, CODFIL AS FILIAL, SIGFIL AS SIGLA FROM E070FIL"
CUSTOMERS_SQL = "SELECT CODCLI AS CLIENTE, NOMCLI AS NOME, CIDCLI AS CIDADE, SIGUFS AS UF FROM E085CLI WHERE CODCLI IN ({codes})"


def rep_names(rows):
    names = {}
    for row in rows:
        code = dna._int(row.get("codigo"))
        primary = dna.primary_rep(code)
        name = (row.get("nome") or "").strip()
        if primary is not None and name and (primary not in names or code == primary):
            names[primary] = dna.REPRESENTATIVE_DISPLAY_NAMES.get(primary) or name.title()
    return names


def branch_names(rows):
    return {_branch_key(row.get("empresa"), row.get("filial")): (row.get("sigla") or "").strip().title() for row in rows}


def customer_names(codes):
    codes = sorted({code for code in codes if code is not None})
    names = {}
    for start in range(0, len(codes), 900):
        chunk = codes[start:start + 900]
        placeholders = ", ".join(f":c{index}" for index in range(len(chunk)))
        rows = dna._run({"customers": (CUSTOMERS_SQL.format(codes=placeholders), {f"c{index}": code for index, code in enumerate(chunk)})})["customers"]
        for row in rows:
            code = dna._int(row.get("cliente"))
            names[code] = {
                "name": (row.get("nome") or "").strip() or f"Cliente {code}",
                "place": f"{(row.get('cidade') or '').strip().title()}/{(row.get('uf') or '').strip()}",
            }
    return names


def _names():
    def load():
        rows = dna._run({"reps": (REPS_SQL, {}), "branches": (BRANCHES_SQL, {})})
        return {"reps": rep_names(rows["reps"]), "branches": branch_names(rows["branches"])}
    return cached("names", HISTORY_SECONDS, load)


# ---------------------------------------------------------------------------
# Vendas
# ---------------------------------------------------------------------------

SALES_DAILY_SQL = f"""
SELECT DIA, CLIENTE, REPRESENTANTE, EMPRESA, FILIAL, FAMILIA, BI_LAMINADO, UF,
       SUM(LIQUIDO) AS LIQUIDO, SUM(PESO) AS PESO
FROM (
  SELECT TRUNC(N.DATEMI) AS DIA, N.CODCLI AS CLIENTE, N.CODREP AS REPRESENTANTE, N.CODEMP AS EMPRESA, N.CODFIL AS FILIAL,
         F.DESFAM AS FAMILIA, {_BI_SQL} AS BI_LAMINADO, C.SIGUFS AS UF,
         NVL(I.VLRBRU, 0) * (1 - {_RATIO}) AS LIQUIDO,
         NVL(I.PESLIQ, 0) * (1 - {_RATIO}) AS PESO
  FROM E140NFV N
  JOIN E140IPV I ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
  JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
  LEFT JOIN E012FAM F ON F.CODEMP = I.CODEMP AND F.CODFAM = I.CODFAM
  LEFT JOIN E085CLI C ON C.CODCLI = N.CODCLI
  WHERE N.SITNFV = '2'
    AND N.CODCLI NOT IN (1001, 1002, 1004)
    AND ((N.DATEMI >= :start_1 AND N.DATEMI < :end_1) OR (N.DATEMI >= :start_2 AND N.DATEMI < :end_2))
)
WHERE LIQUIDO > 0
GROUP BY DIA, CLIENTE, REPRESENTANTE, EMPRESA, FILIAL, FAMILIA, BI_LAMINADO, UF
"""

SALES_MONTHLY_SQL = f"""
SELECT MES, REPRESENTANTE, EMPRESA, FILIAL, FAMILIA, BI_LAMINADO, SUM(LIQUIDO) AS LIQUIDO, SUM(PESO) AS PESO
FROM (
  SELECT TRUNC(N.DATEMI, 'MM') AS MES, N.CODREP AS REPRESENTANTE, N.CODEMP AS EMPRESA, N.CODFIL AS FILIAL,
         F.DESFAM AS FAMILIA, {_BI_SQL} AS BI_LAMINADO,
         NVL(I.VLRBRU, 0) * (1 - {_RATIO}) AS LIQUIDO,
         NVL(I.PESLIQ, 0) * (1 - {_RATIO}) AS PESO
  FROM E140NFV N
  JOIN E140IPV I ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
  JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
  LEFT JOIN E012FAM F ON F.CODEMP = I.CODEMP AND F.CODFAM = I.CODFAM
  WHERE N.SITNFV = '2'
    AND N.CODCLI NOT IN (1001, 1002, 1004)
    AND N.DATEMI >= :start_date
)
WHERE LIQUIDO > 0
GROUP BY MES, REPRESENTANTE, EMPRESA, FILIAL, FAMILIA, BI_LAMINADO
"""

SALES_CALENDAR_SQL = f"""
SELECT TRUNC(N.DATEMI) AS DIA, SUM(NVL(I.VLRBRU, 0) * (1 - {_RATIO})) AS LIQUIDO
FROM E140NFV N
JOIN E140IPV I ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
WHERE N.SITNFV = '2' AND N.CODCLI NOT IN (1001, 1002, 1004) AND N.DATEMI >= :start_date
GROUP BY TRUNC(N.DATEMI)
"""

# Pedidos recebidos (entrada de pedidos): quantidade pedida × preço, no dia de emissão.
SALES_INTAKE_SQL = """
SELECT TRUNC(PED.DATEMI) AS DIA, PED.CODCLI AS CLIENTE, PED.CODREP AS REPRESENTANTE, PED.CODEMP AS EMPRESA,
       PED.CODFIL AS FILIAL, C.SIGUFS AS UF,
       SUM(NVL(ITEM.QTDPED, 0) * NVL(ITEM.PREUNI, 0)) AS VALOR,
       SUM(NVL(ITEM.QTDPED, 0) * NVL(DER.PESBRU, 0)) AS PESO,
       COUNT(DISTINCT PED.CODEMP || '-' || PED.CODFIL || '-' || PED.NUMPED) AS PEDIDOS
FROM E120PED PED
JOIN E120IPD ITEM ON ITEM.CODEMP = PED.CODEMP AND ITEM.CODFIL = PED.CODFIL AND ITEM.NUMPED = PED.NUMPED
JOIN E001TNS TNS ON TNS.CODEMP = ITEM.CODEMP AND TNS.CODTNS = ITEM.TNSPRO AND TNS.VENFAT = 'S'
LEFT JOIN E075DER DER ON DER.CODEMP = ITEM.CODEMP AND DER.CODPRO = ITEM.CODPRO AND DER.CODDER = ITEM.CODDER
LEFT JOIN E085CLI C ON C.CODCLI = PED.CODCLI
WHERE PED.CODCLI NOT IN (1001, 1002, 1004)
  AND ITEM.SITIPD NOT IN (5, 9)
  AND ((PED.DATEMI >= :start_1 AND PED.DATEMI < :end_1) OR (PED.DATEMI >= :start_2 AND PED.DATEMI < :end_2))
GROUP BY TRUNC(PED.DATEMI), PED.CODCLI, PED.CODREP, PED.CODEMP, PED.CODFIL, C.SIGUFS
"""


def month_bounds(month_start):
    end = dna._add_months(month_start, 1)
    previous_start = dna._add_months(month_start, -12)
    return month_start, end, previous_start, dna._add_months(previous_start, 1)


def business_days(month_start, today):
    """Dias úteis (segunda a sexta) do mês e quantos já passaram até hoje."""
    days_in_month = calendar_module.monthrange(month_start.year, month_start.month)[1]
    days = [month_start.replace(day=day) for day in range(1, days_in_month + 1)]
    weekdays = [day for day in days if day.weekday() < 5]
    elapsed = [day for day in weekdays if day <= today]
    return {
        "total": len(weekdays),
        "elapsed": len(elapsed) if month_start <= today < dna._add_months(month_start, 1) else (len(weekdays) if today >= month_start else 0),
        "weekend": [day.day for day in days if day.weekday() >= 5],
        "days": days_in_month,
    }


def sales_facts(daily_rows, month_start):
    """Linhas do dia (mês escolhido = y 0; mesmo mês do ano anterior = y 1)."""
    current_key = dna._month_key(month_start)
    facts = []
    for row in daily_rows:
        day = dna._date(row.get("dia"))
        if day is None:
            continue
        facts.append({
            "d": day.day,
            "y": 0 if dna._month_key(day) == current_key else 1,
            "c": dna._int(row.get("cliente")),
            "r": dna.primary_rep(row.get("representante")),
            "f": _branch_key(row.get("empresa"), row.get("filial")),
            "g": _group(row.get("familia"), row.get("bi_laminado")),
            "u": (row.get("uf") or "").strip() or "EX",
            "n": _f(row.get("liquido")),
            "k": _f(row.get("peso"), 1),
        })
    return facts


def intake_facts(rows, month_start):
    current_key = dna._month_key(month_start)
    facts = []
    for row in rows:
        day = dna._date(row.get("dia"))
        if day is None:
            continue
        facts.append({
            "d": day.day, "y": 0 if dna._month_key(day) == current_key else 1,
            "c": dna._int(row.get("cliente")), "r": dna.primary_rep(row.get("representante")),
            "f": _branch_key(row.get("empresa"), row.get("filial")), "u": (row.get("uf") or "").strip() or "EX",
            "v": _f(row.get("valor")), "k": _f(row.get("peso"), 1), "o": int(row.get("pedidos") or 0),
        })
    return facts


def monthly_facts(rows):
    facts = defaultdict(lambda: [ZERO, ZERO])
    for row in rows:
        month = dna._date(row.get("mes"))
        if month is None:
            continue
        key = (dna._month_key(month), dna.primary_rep(row.get("representante")), _branch_key(row.get("empresa"), row.get("filial")),
               _group(row.get("familia"), row.get("bi_laminado")))
        facts[key][0] += dna._num(row.get("liquido"))
        facts[key][1] += dna._num(row.get("peso"))
    return [{"m": key[0], "r": key[1], "f": key[2], "g": key[3], "n": _f(values[0]), "k": _f(values[1], 1)} for key, values in facts.items()]


def month_label(key):
    year, month = divmod(key, 12)
    return f"{dna.MONTH_NAMES[month]}/{str(year)[2:]}"


MAX_SALES_MONTHS = 12


def months_label(keys):
    """Rótulo de um ou vários meses: "set/26", "set/26 a nov/26" ou "set/26, out/26 e dez/26"."""
    keys = sorted(keys)
    if len(keys) == 1:
        return month_label(keys[0])
    if keys[-1] - keys[0] == len(keys) - 1:
        return f"{month_label(keys[0])} a {month_label(keys[-1])}"
    names = [month_label(key) for key in keys]
    if len(names) > 4:
        return f"{', '.join(names[:3])} e mais {len(names) - 3}"
    return f"{', '.join(names[:-1])} e {names[-1]}"


def _sales_month(start, today):
    """Fatos do dia do mês (y 0) e do mesmo mês do ano anterior (y 1), em cache por mês."""
    start, end, previous_start, previous_end = month_bounds(start)
    is_current = start <= today < end
    params = {"start_1": start, "end_1": end, "start_2": previous_start, "end_2": previous_end}

    def load_month():
        rows = dna._run({"daily": (SALES_DAILY_SQL, params), "intake": (SALES_INTAKE_SQL, params)})
        return {"facts": sales_facts(rows["daily"], start), "intake": intake_facts(rows["intake"], start)}

    return cached(f"sales:{start.isoformat()}", CURRENT_SECONDS if is_current else HISTORY_SECONDS, load_month)


def _sales_history(today):
    def load_history():
        history_start = dna._add_months(today.replace(day=1), -24)
        rows = dna._run({
            "monthly": (SALES_MONTHLY_SQL, {"start_date": history_start}),
            "calendar": (SALES_CALENDAR_SQL, {"start_date": today - timedelta(days=7 * 26)}),
        })
        return {
            "monthly": monthly_facts(rows["monthly"]),
            "calendar": sorted(({"date": _iso(row.get("dia")), "value": _f(row.get("liquido"))} for row in rows["calendar"] if row.get("dia")), key=lambda day: day["date"]),
        }

    return cached(f"sales-history:{today.isoformat()}", HISTORY_SECONDS, load_history)


def collapse_sales(facts, month_key, cut_day=None):
    """Junta os dias de um mês para a tela de vários meses: y 0 = o mês; y 1 = o mesmo mês do ano
    anterior (até o mesmo dia, quando o mês está em curso); y 2 = o resto desse mês do ano anterior."""
    totals = defaultdict(lambda: [0.0, 0.0])
    for fact in facts:
        year = fact["y"]
        if year == 1 and cut_day is not None and fact["d"] > cut_day:
            year = 2
        key = (year, fact["c"], fact["r"], fact["f"], fact["g"], fact["u"])
        totals[key][0] += fact["n"] or 0
        totals[key][1] += fact["k"] or 0
    return [
        {"mo": month_key, "y": key[0], "c": key[1], "r": key[2], "f": key[3], "g": key[4], "u": key[5], "n": round(values[0], 2), "k": round(values[1], 1)}
        for key, values in totals.items()
    ]


def collapse_intake(facts, month_key):
    """Pedidos recebidos do mês somados sem o dia (só o período escolhido é usado)."""
    totals = defaultdict(lambda: [0.0, 0.0, 0])
    for fact in facts:
        if fact["y"] != 0:
            continue
        key = (fact["c"], fact["r"], fact["f"], fact["u"])
        totals[key][0] += fact["v"] or 0
        totals[key][1] += fact["k"] or 0
        totals[key][2] += fact["o"] or 0
    return [
        {"mo": month_key, "y": 0, "c": key[0], "r": key[1], "f": key[2], "u": key[3], "v": round(values[0], 2), "k": round(values[1], 1), "o": values[2]}
        for key, values in totals.items()
    ]


def _goals_by_rep(goals, wanted):
    totals = defaultdict(lambda: ZERO)
    for goal in goals:
        if (int(goal["year"]), int(goal["month"])) in wanted:
            totals[dna.primary_rep(goal["rep_code"])] += dna._num(goal["weight_kg"])
    return {str(rep): _f(value, 0) for rep, value in totals.items()}


def _sales_names(names, customers):
    return {
        "reps": {str(key): value for key, value in names["reps"].items()},
        "branches": names["branches"],
        "customers": {str(code): value for code, value in customers.items()},
    }


def available_months(today):
    """Os meses que a tela de Vendas abre: do mês corrente até 23 meses atrás."""
    current = dna._month_key(today.replace(day=1))
    return list(range(current - 23, current + 1))


def sales_payload(months, today, goals):
    """Tudo o que a tela de Vendas precisa: um mês (dia a dia) ou vários (somados por mês)."""
    if isinstance(months, date):
        months = [months]
    months = sorted(set(months))[-MAX_SALES_MONTHS:]
    if len(months) == 1:
        return _single_month_payload(months[0], today, goals)
    return _multi_month_payload(months, today, goals)


def _single_month_payload(month_start, today, goals):
    start, end, _previous_start, _previous_end = month_bounds(month_start)
    is_current = start <= today < end
    month = _sales_month(start, today)
    history = _sales_history(today)
    names = _names()
    codes = {fact["c"] for fact in month["facts"]} | {fact["c"] for fact in month["intake"]}
    customers = cached(f"sales-customers:{start.isoformat()}:{len(codes)}", HISTORY_SECONDS, lambda: customer_names(codes))
    month_key = dna._month_key(start)
    return {
        "month": {"key": start.strftime("%Y-%m"), "keys": [start.strftime("%Y-%m")], "multi": False,
                  "label": f"{dna.MONTH_NAMES[start.month - 1]}/{start.year}", "is_current": is_current,
                  "today": today.day if is_current else None, "previous_label": f"{dna.MONTH_NAMES[start.month - 1]}/{start.year - 1}"},
        "selected_months": [month_key],
        "available_months": available_months(today),
        "business": business_days(start, today),
        "facts": month["facts"],
        "intake": month["intake"],
        "monthly": [fact for fact in history["monthly"] if month_key - 23 <= fact["m"] <= month_key],
        "months": [{"key": key, "label": month_label(key)} for key in range(month_key - 11, month_key + 1)],
        "calendar": history["calendar"],
        "goals": _goals_by_rep(goals, {(start.year, start.month)}),
        "names": _sales_names(names, customers),
        "loaded_at": timezone.localtime().strftime("%H:%M:%S"),
    }


def _multi_month_payload(months, today, goals):
    current_key = dna._month_key(today.replace(day=1))
    keys = [dna._month_key(start) for start in months]
    facts, intake, business_months = [], [], []
    for start, key in zip(months, keys):
        month = _sales_month(start, today)
        is_current = key == current_key
        facts.extend(collapse_sales(month["facts"], key, today.day if is_current else None))
        intake.extend(collapse_intake(month["intake"], key))
        days = business_days(start, today)
        business_months.append({"key": key, "label": month_label(key), "total": days["total"], "elapsed": days["elapsed"], "is_current": is_current})
    history = _sales_history(today)
    names = _names()
    codes = {fact["c"] for fact in facts} | {fact["c"] for fact in intake}
    customers = cached(f"sales-customers:{','.join(str(key) for key in keys)}:{len(codes)}", HISTORY_SECONDS, lambda: customer_names(codes))
    last_key = keys[-1]
    includes_current = current_key in keys
    return {
        "month": {
            "key": ",".join(start.strftime("%Y-%m") for start in months),
            "keys": [start.strftime("%Y-%m") for start in months],
            "multi": True,
            "count": len(keys),
            "label": months_label(keys),
            "previous_label": months_label([key - 12 for key in keys]),
            "is_current": includes_current,
            "current_key": current_key if includes_current else None,
            "today": today.day if includes_current else None,
        },
        "selected_months": keys,
        "available_months": available_months(today),
        "business": {
            "total": sum(item["total"] for item in business_months),
            "elapsed": sum(item["elapsed"] for item in business_months),
            "months": business_months,
        },
        "facts": facts,
        "intake": intake,
        "monthly": [fact for fact in history["monthly"] if last_key - 23 <= fact["m"] <= last_key],
        "months": [{"key": key, "label": month_label(key)} for key in range(last_key - 11, last_key + 1)],
        "calendar": history["calendar"],
        "goals": _goals_by_rep(goals, {(start.year, start.month) for start in months}),
        "names": _sales_names(names, customers),
        "loaded_at": timezone.localtime().strftime("%H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Contas a receber
# ---------------------------------------------------------------------------

# Títulos de débito em aberto (nota fiscal, substituição...). Créditos do cliente
# (devolução, adiantamento, pagamento antecipado: RECSOM = 'C') ficam à parte.
OPEN_TITLES_SQL = """
SELECT T.CODEMP AS EMPRESA, T.CODFIL AS FILIAL, T.NUMTIT AS TITULO, T.CODTPT AS TIPO, T.CODCLI AS CLIENTE,
       T.CODREP AS REPRESENTANTE, T.DATEMI AS EMISSAO, NVL(T.VCTPRO, T.VCTORI) AS VENCIMENTO, T.VCTORI AS VENCIMENTO_ORIGINAL,
       T.VLRABE AS ABERTO, T.VLRORI AS ORIGINAL, T.CODPOR AS PORTADOR, P.DESPOR AS NOME_PORTADOR,
       C.NOMCLI AS NOME, C.CIDCLI AS CIDADE, C.SIGUFS AS UF
FROM E301TCR T
JOIN E002TPT TP ON TP.CODTPT = T.CODTPT AND NVL(TP.RECSOM, 'D') <> 'C'
LEFT JOIN E039POR P ON P.CODEMP = T.CODEMP AND P.CODPOR = T.CODPOR
LEFT JOIN E085CLI C ON C.CODCLI = T.CODCLI
WHERE T.SITTIT IN ('AB', 'AC', 'AP') AND T.VLRABE > 0
"""

CREDITS_SQL = """
SELECT T.CODTPT AS TIPO, MAX(TP.DESTPT) AS DESCRICAO, COUNT(*) AS TITULOS, SUM(T.VLRABE) AS ABERTO
FROM E301TCR T
JOIN E002TPT TP ON TP.CODTPT = T.CODTPT AND TP.RECSOM = 'C'
WHERE T.SITTIT IN ('AB', 'AC', 'AP') AND T.VLRABE > 0
GROUP BY T.CODTPT
"""

PAID_MONTHLY_SQL = """
SELECT TRUNC(T.ULTPGT, 'MM') AS MES, COUNT(*) AS TITULOS,
       SUM(T.VLRORI) AS PAGO,
       SUM(CASE WHEN T.ULTPGT <= NVL(T.VCTPRO, T.VCTORI) THEN T.VLRORI ELSE 0 END) AS PAGO_EM_DIA,
       SUM((T.ULTPGT - NVL(T.VCTPRO, T.VCTORI)) * T.VLRORI) AS ATRASO_PONDERADO,
       SUM((T.ULTPGT - T.DATEMI) * T.VLRORI) AS PRAZO_PONDERADO
FROM E301TCR T
JOIN E002TPT TP ON TP.CODTPT = T.CODTPT AND TP.RECSOM = 'D'
WHERE T.SITTIT = 'LQ' AND T.ULTPGT >= :start_date AND T.ULTPGT < :end_date
GROUP BY TRUNC(T.ULTPGT, 'MM')
"""

BILLED_90_SQL = """
SELECT SUM(T.VLRORI) AS EMITIDO
FROM E301TCR T
JOIN E002TPT TP ON TP.CODTPT = T.CODTPT AND TP.RECSOM = 'D'
WHERE T.DATEMI >= :start_date AND T.DATEMI < :end_date AND T.SITTIT <> 'CA'
"""


def aging_bucket(due, today):
    days = (today - due).days
    if days > 90:
        return "v90"
    if days > 60:
        return "v61"
    if days > 30:
        return "v31"
    if days > 0:
        return "v1"
    ahead = -days
    if ahead <= 7:
        return "a7"
    if ahead <= 30:
        return "a30"
    if ahead <= 60:
        return "a60"
    return "a61"


def receivable_facts(rows, today):
    facts, customers, holders = [], {}, {}
    for row in rows:
        due = dna._date(row.get("vencimento"))
        value = dna._num(row.get("aberto"))
        if due is None or value <= 0:
            continue
        customer = dna._int(row.get("cliente"))
        holder = (row.get("portador") or "").strip() or "-"
        holders[holder] = (row.get("nome_portador") or holder).strip().title()
        if customer is not None and customer not in customers:
            customers[customer] = {
                "name": (row.get("nome") or "").strip() or f"Cliente {customer}",
                "place": f"{(row.get('cidade') or '').strip().title()}/{(row.get('uf') or '').strip()}",
            }
        facts.append({
            "t": f"{dna._int(row.get('empresa'))}-{dna._int(row.get('filial'))}-{(row.get('titulo') or '').strip()}",
            "c": customer,
            "r": dna.primary_rep(row.get("representante")),
            "u": (row.get("uf") or "").strip() or "EX",
            "p": holder,
            "e": _iso(row.get("emissao")),
            "v": due.isoformat(),
            "b": aging_bucket(due, today),
            "late": max((today - due).days, 0),
            "a": _f(value),
            "o": _f(row.get("original")),
            "renegotiated": bool(row.get("vencimento_original") and dna._date(row.get("vencimento_original")) != due),
        })
    return facts, customers, holders


def paid_months(rows):
    months = []
    for row in rows:
        month = dna._date(row.get("mes"))
        paid = dna._num(row.get("pago"))
        if month is None or paid <= 0:
            continue
        months.append({
            "key": dna._month_key(month),
            "label": month_label(dna._month_key(month)),
            "paid": _f(paid, 0),
            "on_time": _f(row.get("pago_em_dia"), 0),
            "late": _f(paid - dna._num(row.get("pago_em_dia")), 0),
            "titles": int(row.get("titulos") or 0),
            "delay": _f(dna._num(row.get("atraso_ponderado")) / paid, 1),
            "term": _f(dna._num(row.get("prazo_ponderado")) / paid, 1),
        })
    return sorted(months, key=lambda month: month["key"])


def receivables_payload(today):
    def load():
        rows = dna._run({
            "titles": (OPEN_TITLES_SQL, {}),
            "credits": (CREDITS_SQL, {}),
            "paid": (PAID_MONTHLY_SQL, {"start_date": dna._add_months(today.replace(day=1), -12), "end_date": today + timedelta(days=1)}),
            "billed": (BILLED_90_SQL, {"start_date": today - timedelta(days=90), "end_date": today + timedelta(days=1)}),
        })
        facts, customers, holders = receivable_facts(rows["titles"], today)
        billed = dna._num((rows["billed"][0] if rows["billed"] else {}).get("emitido"))
        return {
            "facts": facts,
            "customers": {str(code): value for code, value in customers.items()},
            "holders": holders,
            "credits": [{"type": (row.get("tipo") or "").strip(), "label": (row.get("descricao") or "").strip().title(),
                         "titles": int(row.get("titulos") or 0), "value": _f(row.get("aberto"), 0)} for row in rows["credits"]],
            "paid": paid_months(rows["paid"]),
            "daily_billing": _f(billed / 90, 0) if billed else None,
        }
    data = cached(f"receivables:{today.isoformat()}", CURRENT_SECONDS * 5, load)
    names = _names()
    return {
        **data,
        "today": today.isoformat(),
        "buckets": [{"key": key, "label": label, "kind": kind} for key, label, kind in AGING_BUCKETS],
        "names": {"reps": {str(key): value for key, value in names["reps"].items()}},
        "loaded_at": timezone.localtime().strftime("%H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Entregas (nível de serviço / OTIF)
# ---------------------------------------------------------------------------

OTIF_SQL = """
SELECT ITEM.CODEMP AS EMPRESA, ITEM.CODFIL AS FILIAL, ITEM.NUMPED AS PEDIDO, ITEM.SEQIPD AS SEQUENCIA,
       ITEM.DATENT AS ENTREGA, PED.DATEMI AS EMISSAO, PED.CODCLI AS CLIENTE, PED.CODREP AS REPRESENTANTE,
       ITEM.SITIPD AS SITUACAO, FAM.DESFAM AS FAMILIA, PROD.DESPRO AS PRODUTO,
       NVL(ITEM.QTDPED, 0) AS QTD_PEDIDA, NVL(ITEM.QTDCAN, 0) AS QTD_CANCELADA, NVL(ITEM.QTDABE, 0) AS QTD_ABERTA,
       NVL(ITEM.PREUNI, 0) AS PRECO, NVL(DER.PESBRU, 0) AS PESO_UNITARIO,
       SUM(CASE WHEN N.NUMNFV IS NOT NULL THEN I.QTDFAT ELSE 0 END) AS QTD_FATURADA,
       SUM(CASE WHEN N.NUMNFV IS NOT NULL AND N.DATEMI <= ITEM.DATENT THEN I.QTDFAT ELSE 0 END) AS QTD_NO_PRAZO,
       MAX(N.DATEMI) AS ULTIMA_NOTA
FROM E120IPD ITEM
JOIN E120PED PED ON PED.CODEMP = ITEM.CODEMP AND PED.CODFIL = ITEM.CODFIL AND PED.NUMPED = ITEM.NUMPED
JOIN E001TNS TNS ON TNS.CODEMP = ITEM.CODEMP AND TNS.CODTNS = ITEM.TNSPRO AND TNS.VENFAT = 'S'
LEFT JOIN E075PRO PROD ON PROD.CODEMP = ITEM.CODEMP AND PROD.CODPRO = ITEM.CODPRO
LEFT JOIN E012FAM FAM ON FAM.CODEMP = PROD.CODEMP AND FAM.CODFAM = PROD.CODFAM
LEFT JOIN E075DER DER ON DER.CODEMP = ITEM.CODEMP AND DER.CODPRO = ITEM.CODPRO AND DER.CODDER = ITEM.CODDER
LEFT JOIN E140IPV I ON I.CODEMP = ITEM.CODEMP AND I.CODFIL = ITEM.CODFIL AND I.NUMPED = ITEM.NUMPED AND I.SEQIPD = ITEM.SEQIPD
LEFT JOIN E140NFV N ON N.CODEMP = I.CODEMP AND N.CODFIL = I.CODFIL AND N.CODSNF = I.CODSNF AND N.NUMNFV = I.NUMNFV AND N.SITNFV = '2'
WHERE PED.CODCLI NOT IN (1001, 1002, 1004)
  AND ITEM.SITIPD NOT IN (5, 9)
  AND ITEM.DATENT >= :start_date AND ITEM.DATENT < :end_date
GROUP BY ITEM.CODEMP, ITEM.CODFIL, ITEM.NUMPED, ITEM.SEQIPD, ITEM.DATENT, PED.DATEMI, PED.CODCLI, PED.CODREP,
         ITEM.SITIPD, FAM.DESFAM, PROD.DESPRO, ITEM.QTDPED, ITEM.QTDCAN, ITEM.QTDABE, ITEM.PREUNI, DER.PESBRU
"""


def delay_bucket(days):
    if days < 0:
        return "early"
    if days == 0:
        return "ontime"
    if days <= 3:
        return "d3"
    if days <= 7:
        return "d7"
    if days <= 15:
        return "d15"
    if days <= 30:
        return "d30"
    return "d30p"


def service_item(row, today):
    """Um item de pedido com entrega prevista: completo, no prazo, atraso e lead time."""
    ordered = dna._num(row.get("qtd_pedida")) - dna._num(row.get("qtd_cancelada"))
    billed = dna._num(row.get("qtd_faturada"))
    on_time_qty = dna._num(row.get("qtd_no_prazo"))
    due = dna._date(row.get("entrega"))
    issued = dna._date(row.get("emissao"))
    last = dna._date(row.get("ultima_nota"))
    unit = dna._num(row.get("peso_unitario"))
    if ordered <= 0 or due is None:
        return None
    full = billed >= ordered * IN_FULL
    delay = (last - due).days if (full and last) else None
    status = dna._int(row.get("situacao"))
    open_qty = dna._num(row.get("qtd_aberta"))
    pending = status in (1, 2) and open_qty > 0 and not full
    return {
        "due": due,
        "full": full,
        "otif": full and on_time_qty >= ordered * IN_FULL,
        "delay": delay,
        "lead": (last - issued).days if (full and last and issued) else None,
        "kg": ordered * unit,
        "pending": pending,
        "overdue": pending and due < today,
        "open_kg": open_qty * unit,
        "open_value": open_qty * dna._num(row.get("preco")),
    }


def service_payload(today):
    def load():
        end = today.replace(day=1)
        start = dna._add_months(end, -12)
        rows = dna._run({"items": (OTIF_SQL, {"start_date": start, "end_date": dna._add_months(end, 1)})})["items"]
        facts = defaultdict(lambda: {"items": 0, "full": 0, "otif": 0, "kg": ZERO, "kg_otif": ZERO, "late_days": 0, "late_items": 0, "lead_days": 0, "lead_items": 0})
        overdue, codes = [], set()
        for row in rows:
            item = service_item(row, today)
            if item is None:
                continue
            due = item["due"]
            key_month = dna._month_key(due)
            rep = dna.primary_rep(row.get("representante"))
            group = _group(row.get("familia"), 0)
            branch = _branch_key(row.get("empresa"), row.get("filial"))
            customer = dna._int(row.get("cliente"))
            if due >= today:
                continue  # ainda dentro do prazo: entra no indicador quando a data passar
            if item["overdue"]:
                codes.add(customer)
                overdue.append({
                    "order": f"{dna._int(row.get('empresa'))}-{dna._int(row.get('filial'))}-{row.get('pedido')}/{row.get('sequencia')}",
                    "c": customer, "r": rep, "g": group, "f": branch, "product": (row.get("produto") or "").strip()[:80],
                    "due": due.isoformat(), "late": (today - due).days, "kg": _f(item["open_kg"], 0), "value": _f(item["open_value"], 0),
                })
            # Vencido sem entrega completa conta como falha: ainda aberto ou fechado incompleto.
            if item["full"]:
                bucket = delay_bucket(item["delay"]) if item["delay"] is not None else "d30p"
            else:
                bucket = "open" if item["pending"] else "short"
            entry = facts[(key_month, rep, group, branch, bucket)]
            entry["items"] += 1
            entry["full"] += 1 if item["full"] else 0
            entry["otif"] += 1 if item["otif"] else 0
            entry["kg"] += item["kg"]
            entry["kg_otif"] += item["kg"] if item["otif"] else ZERO
            if item["delay"] and item["delay"] > 0:
                entry["late_days"] += item["delay"]
                entry["late_items"] += 1
            if item["lead"] is not None:
                entry["lead_days"] += item["lead"]
                entry["lead_items"] += 1
        overdue.sort(key=lambda row: row["late"], reverse=True)
        return {
            "facts": [
                {"m": key[0], "r": key[1], "g": key[2], "f": key[3], "b": key[4], "i": value["items"], "o": value["otif"], "fu": value["full"],
                 "k": _f(value["kg"], 0), "ko": _f(value["kg_otif"], 0), "ld": value["late_days"], "li": value["late_items"],
                 "td": value["lead_days"], "ti": value["lead_items"]}
                for key, value in facts.items()
            ],
            "overdue": overdue[:300],
            "overdue_codes": sorted(code for code in codes if code is not None),
        }
    data = cached(f"service:{today.isoformat()}", HISTORY_SECONDS, load)
    names = _names()
    customers = cached(f"service-customers:{today.isoformat()}", HISTORY_SECONDS, lambda: customer_names(data["overdue_codes"]))
    last = dna._month_key(today)
    return {
        "facts": data["facts"],
        "overdue": data["overdue"],
        "months": [{"key": key, "label": month_label(key)} for key in range(last - 12, last + 1)],
        "buckets": [{"key": key, "label": label} for key, label in DELAY_BUCKETS],
        "reference": OTIF_REFERENCE,
        "names": {
            "reps": {str(key): value for key, value in names["reps"].items()},
            "branches": names["branches"],
            "customers": {str(code): value for code, value in customers.items()},
        },
        "loaded_at": timezone.localtime().strftime("%H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Qualidade (reclamações e devoluções)
# ---------------------------------------------------------------------------

COMPLAINTS_SQL = """
SELECT A.USU_DATREC AS DATA, A.USU_CODCLI AS CLIENTE, A.USU_CLSPRO AS CLASSE, A.USU_PROESP AS PROBLEMA,
       B.DESCRE AS MAQUINA, A.USU_VOLNCO AS VOLUME, A.USU_UNIMED AS UNIDADE
FROM USU_TRECCLI A
LEFT JOIN E725CRE B ON A.USU_CODEMP = B.CODEMP AND A.USU_CODCRE = B.CODCRE
WHERE A.USU_DATREC >= :start_date AND A.USU_DATREC < :end_date
"""

RETURNS_SQL = """
SELECT A.USU_DATDEV AS DATA, A.USU_CODCLI AS CLIENTE, A.USU_TOTDEV AS VALOR, B.USU_DESPRB AS PROBLEMA,
       C.USU_DESSET AS SETOR, D.SIGFIL AS FILIAL
FROM USU_TDEVCLI A
LEFT JOIN USU_TCadPrB B ON A.USU_CODPRB = B.USU_CODPRB
LEFT JOIN USU_TCADSET C ON A.USU_SETCAU = C.USU_CODSET
LEFT JOIN E070FIL D ON A.USU_CODFIL = D.CODFIL AND A.USU_CODEMP = D.CODEMP
WHERE A.USU_DATDEV >= :start_date AND A.USU_DATDEV < :end_date
"""

REVENUE_MONTHLY_SQL = f"""
SELECT TRUNC(N.DATEMI, 'MM') AS MES, SUM(NVL(I.VLRBRU, 0) * (1 - {_RATIO})) AS LIQUIDO
FROM E140NFV N
JOIN E140IPV I ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
WHERE N.SITNFV = '2' AND N.CODCLI NOT IN (1001, 1002, 1004) AND N.DATEMI >= :start_date AND N.DATEMI < :end_date
GROUP BY TRUNC(N.DATEMI, 'MM')
"""


def _text(value, fallback="Não informado"):
    text = " ".join(str(value or "").split())
    return text[:90] if text else fallback


def quality_facts(complaint_rows, return_rows):
    complaints, returns, codes = [], [], set()
    for row in complaint_rows:
        day = dna._date(row.get("data"))
        if day is None:
            continue
        customer = dna._int(row.get("cliente"))
        codes.add(customer)
        severity = (row.get("classe") or "L").strip().upper()
        complaints.append({
            "m": dna._month_key(day), "d": day.isoformat(), "c": customer,
            "s": severity if severity in SEVERITY else "L",
            "p": _text(row.get("problema")), "q": _text(row.get("maquina"), "Sem recurso"),
        })
    for row in return_rows:
        day = dna._date(row.get("data"))
        if day is None:
            continue
        customer = dna._int(row.get("cliente"))
        codes.add(customer)
        returns.append({
            "m": dna._month_key(day), "d": day.isoformat(), "c": customer, "v": _f(row.get("valor")),
            "p": _text(row.get("problema")), "s": _text(row.get("setor"), "Sem setor"), "f": _text(row.get("filial"), "-").title(),
        })
    return complaints, returns, codes


def quality_payload(today):
    def load():
        start = dna._add_months(today.replace(day=1), -23)
        end = today + timedelta(days=1)
        rows = dna._run({
            "complaints": (COMPLAINTS_SQL, {"start_date": start, "end_date": end}),
            "returns": (RETURNS_SQL, {"start_date": start, "end_date": end}),
            "revenue": (REVENUE_MONTHLY_SQL, {"start_date": start, "end_date": end}),
        })
        complaints, returns, codes = quality_facts(rows["complaints"], rows["returns"])
        return {
            "complaints": complaints,
            "returns": returns,
            "codes": sorted(code for code in codes if code is not None),
            "revenue": {str(dna._month_key(dna._date(row.get("mes")))): _f(row.get("liquido"), 0) for row in rows["revenue"] if row.get("mes")},
        }
    data = cached(f"quality:{today.isoformat()}", HISTORY_SECONDS, load)
    customers = cached(f"quality-customers:{today.isoformat()}", HISTORY_SECONDS, lambda: customer_names(data["codes"]))
    graves = sorted((row["d"] for row in data["complaints"] if row["s"] == "G"), reverse=True)
    last = dna._month_key(today)
    return {
        "complaints": data["complaints"],
        "returns": data["returns"],
        "revenue": data["revenue"],
        "months": [{"key": key, "label": month_label(key)} for key in range(last - 23, last + 1)],
        "severity": [{"key": key, "label": label} for key, label in SEVERITY.items()],
        "days_since_grave": (today - date.fromisoformat(graves[0])).days if graves else None,
        "last_grave": graves[0] if graves else None,
        "today": today.isoformat(),
        "names": {"customers": {str(code): value for code, value in customers.items()}},
        "loaded_at": timezone.localtime().strftime("%H:%M:%S"),
    }
