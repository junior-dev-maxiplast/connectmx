"""Inteligência de clientes: mapa do Brasil, carteira (os mais lucrativos) e ficha do cliente.

Uma base só, lida do ERP Senior e guardada em cache por 30 minutos
(`load_base`), alimenta as três telas:

* faturamento líquido, sem impostos, CPV e kg por cliente, representante e mês,
  nos últimos 24 meses, com as regras do PBIX que o DNA já usa (VL_FAT-DEV,
  TOTAL S/IMPOSTO e o CPV do movimento de saída do estoque, E210MVP);
* cadastro: cidade, UF, município pelo código IBGE (pelo CEP, E008CEP) e ramo
  de atividade (E026RAM);
* mix por grupo de produto nos últimos 12 meses, para a venda cruzada.

O resto é cálculo em Python, testável sem Oracle: lucro bruto e margem,
crescimento, RFM, ritmo de compra e risco de parada, curva de Pareto do lucro,
geografia (região, UF e município), comparação com o segmento, venda cruzada
e reativação.
"""

import bisect
import math
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from . import commercial_dna as dna
from . import econodata

ZERO = Decimal("0")
ONE = Decimal("1")
HISTORY_MONTHS = 24
MIX_MONTHS = 12
BASE_CACHE_KEY = "customer-intel:base:v1"
BASE_CACHE_SECONDS = 30 * 60
CITY_CACHE_KEY = "customer-intel:cities:v1"
CITY_CACHE_SECONDS = 24 * 3600
INACTIVE_DAYS = 180
NEW_CUSTOMER_DAYS = 90
LOW_MARGIN = Decimal("0.05")
# Margem a partir daqui quase sempre é CPV zerado no ERP (item sem custo), não lucro.
SUSPECT_MARGIN = Decimal("0.95")
CROSS_SELL_MIN_PEERS = 5
CROSS_SELL_MIN_ADOPTION = Decimal("0.30")
# Grupos que não são oportunidade de venda (sobra de produção e sem classificação).
CROSS_SELL_IGNORED = ("REFUGO", "OUTROS")
OTHER_SEGMENT = "OUTROS"  # a IA não achou um ramo do ERP que sirva

PERIOD_CHOICES = (("3", "3 meses"), ("6", "6 meses"), ("12", "12 meses"), ("ytd", "Ano atual"))
DEFAULT_PERIOD = "12"

REGIONS = {"N": "Norte", "NE": "Nordeste", "CO": "Centro-Oeste", "SE": "Sudeste", "S": "Sul"}
REGION_ORDER = ("S", "SE", "CO", "NE", "N")
# Sigla: (código IBGE, nome, região).
STATES = {
    "RO": (11, "Rondônia", "N"), "AC": (12, "Acre", "N"), "AM": (13, "Amazonas", "N"),
    "RR": (14, "Roraima", "N"), "PA": (15, "Pará", "N"), "AP": (16, "Amapá", "N"),
    "TO": (17, "Tocantins", "N"), "MA": (21, "Maranhão", "NE"), "PI": (22, "Piauí", "NE"),
    "CE": (23, "Ceará", "NE"), "RN": (24, "Rio Grande do Norte", "NE"), "PB": (25, "Paraíba", "NE"),
    "PE": (26, "Pernambuco", "NE"), "AL": (27, "Alagoas", "NE"), "SE": (28, "Sergipe", "NE"),
    "BA": (29, "Bahia", "NE"), "MG": (31, "Minas Gerais", "SE"), "ES": (32, "Espírito Santo", "SE"),
    "RJ": (33, "Rio de Janeiro", "SE"), "SP": (35, "São Paulo", "SE"), "PR": (41, "Paraná", "S"),
    "SC": (42, "Santa Catarina", "S"), "RS": (43, "Rio Grande do Sul", "S"),
    "MS": (50, "Mato Grosso do Sul", "CO"), "MT": (51, "Mato Grosso", "CO"), "GO": (52, "Goiás", "CO"),
    "DF": (53, "Distrito Federal", "CO"),
}
STATE_BY_IBGE = {code: sigla for sigla, (code, _name, _region) in STATES.items()}
FOREIGN = "EX"

# Prefixos de CNAE (formato da Econodata, "C-1012") de cada ramo do ERP, para
# contar o mercado e buscar empresas parecidas. Ponto de partida revisável: quando
# houver clientes do ramo enriquecidos, valem os CNAEs reais deles (`sector_cnaes`).
SECTOR_CNAE_PREFIXES = {
    "001": ["C-1093", "C-1091"],                     # achocolatados e bolos
    "002": ["G-4622", "G-4623", "G-4632", "C-1061", "H-5211"],  # cereais
    "003": ["C-1071", "C-1072", "B-0892"],           # açúcar e sal
    "004": ["C-111", "C-112"],                       # bebidas
    "005": ["C-1096", "C-1032", "C-1020"],           # congelados
    "006": ["C-2320", "C-2330"],                     # construção civil
    "007": ["C-1093", "C-1031"],                     # doces
    "008": ["C-2222", "G-4686"],                     # embalagens
    "009": ["C-1062", "C-1063", "C-1064", "C-1065"],  # farináceos
    "010": ["C-2013", "G-4683"],                     # fertilizantes
    "011": ["C-1011", "C-1012", "C-1013"],           # frigoríficos
    "012": ["C-2061", "C-2062", "C-2063", "C-1742"],  # higiene
    "013": ["C-1051", "C-1052", "C-1053"],           # lácteos
    "014": ["C-161", "C-162"],                       # madeireira
    "015": ["C-1094"],                               # massas
    "016": ["C-1081", "C-1082", "C-1099-6/05"],      # mate, chá e café
    "017": ["C-24", "C-25"],                         # metalúrgica
    "018": ["C-1066"],                               # nutrição animal
    "019": ["C-1091"],                               # pães
    "020": ["C-1066"],                               # pet food
    "021": ["C-20"],                                 # químicos
    "022": ["C-1066"],                               # rações
    "023": ["A-0141", "G-4623"],                     # sementes
    "024": ["C-1066"],                               # suplemento mineral
    "025": ["C-13"],                                 # têxtil
    "026": ["C-1031", "C-1032", "G-4633"],           # vegetais
    "027": ["C-1041", "C-1042", "C-1043"],           # óleos
    "028": ["C-1064", "C-1099"],                     # salgadinhos
    "029": ["C-1031", "C-1033"],                     # polpas
    "030": ["C-17"],                                 # papel
    "031": ["C-207"],                                # tintas
    "032": ["C-1095"],                               # molhos
    "033": ["C-153"],                                # calçados
}

RFM_SEGMENTS = {
    "champions": ("Campeões", "is-good", "Compram muito, com frequência e há pouco tempo."),
    "loyal": ("Fiéis", "is-good", "Compram com regularidade e bom volume."),
    "promising": ("Promissores", "is-info", "Compraram há pouco tempo, ainda com volume baixo."),
    "new": ("Novos", "is-info", "Primeira compra nos últimos 90 dias."),
    "attention": ("Precisam de atenção", "is-warn", "Recência e volume medianos: tendem a esfriar."),
    "at_risk": ("Em risco", "is-warn", "Compravam bem e estão sumindo."),
    "cant_lose": ("Não pode perder", "is-bad", "Estavam entre os maiores e pararam de comprar."),
    "hibernating": ("Hibernando", "", "Compra rara, pequena e antiga."),
    "lost": ("Quase perdidos", "is-bad", "Última compra antiga, pouca frequência e pouco volume."),
}
RFM_ORDER = ("champions", "loyal", "promising", "new", "attention", "at_risk", "cant_lose", "hibernating", "lost")

RHYTHM_STATUS = {
    "on_track": ("Em dia", "is-good"),
    "late": ("Atrasado", "is-warn"),
    "at_risk": ("Risco de parada", "is-bad"),
    "inactive": ("Inativo", "is-bad"),
    "single": ("Compra única", "is-info"),
    "none": ("Sem compra", ""),
}


# ---------------------------------------------------------------------------
# SQL (ERP Senior)
# ---------------------------------------------------------------------------

_RATIO = dna._RETURN_RATIO_SQL

MONTHLY_SQL = f"""
SELECT CLIENTE, REPRESENTANTE, MES,
       MAX(NOME_REPRESENTANTE) AS NOME_REPRESENTANTE,
       SUM(LIQUIDO) AS LIQUIDO,
       SUM(CASE WHEN EXTRACT(DAY FROM DATA) <= :today_day THEN LIQUIDO ELSE 0 END) AS LIQUIDO_ATE_DIA,
       SUM(GREATEST(LIQUIDO - IMPOSTOS, 0)) AS SEM_IMPOSTOS,
       SUM(PESO) AS PESO,
       SUM(CPV) AS CPV,
       SUM(CASE WHEN CPV IS NOT NULL THEN GREATEST(LIQUIDO - IMPOSTOS, 0) END) AS BASE_CPV,
       COUNT(DISTINCT NOTA) AS NOTAS,
       MIN(DATA) AS PRIMEIRA,
       MAX(DATA) AS ULTIMA
FROM (
  SELECT N.CODCLI AS CLIENTE, N.CODREP AS REPRESENTANTE, R.NOMREP AS NOME_REPRESENTANTE,
         TRUNC(N.DATEMI, 'MM') AS MES, N.DATEMI AS DATA,
         N.CODEMP || '-' || N.CODFIL || '-' || N.CODSNF || '-' || N.NUMNFV AS NOTA,
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
  LEFT JOIN E090REP R ON R.CODREP = N.CODREP
  WHERE N.SITNFV = '2'
    AND N.CODCLI NOT IN (1001, 1002, 1004)
    AND N.DATEMI >= :start_date
)
WHERE LIQUIDO > 0
GROUP BY CLIENTE, REPRESENTANTE, MES
"""

# O CEP do cliente casa direto com o E008CEP (quase todo CEP é uma linha própria);
# a busca por faixa (CEPINI..CEPFIM) não usa índice e levava um minuto.
INFO_SQL = """
SELECT C.CODCLI AS CLIENTE, C.NOMCLI AS NOME, C.APECLI AS FANTASIA, C.CGCCPF AS CNPJ, C.TIPCLI AS TIPO,
       C.CIDCLI AS CIDADE, C.SIGUFS AS UF, TRIM(C.CODRAM) AS RAMO, R.DESRAM AS RAMO_NOME, E.CODIBG AS IBGE
FROM E085CLI C
LEFT JOIN E026RAM R ON R.CODRAM = C.CODRAM
LEFT JOIN E008CEP E ON E.CEPINI = C.CEPCLI
WHERE C.CODCLI IN (
  SELECT DISTINCT N.CODCLI FROM E140NFV N WHERE N.SITNFV = '2' AND N.DATEMI >= :start_date
)
"""

MIX_SQL = f"""
SELECT N.CODCLI AS CLIENTE, F.DESFAM AS FAMILIA, I.CPLIPV AS COMPLEMENTO,
       SUM(NVL(I.VLRBRU, 0) * (1 - {_RATIO})) AS LIQUIDO,
       SUM(NVL(I.PESLIQ, 0) * (1 - {_RATIO})) AS PESO
FROM E140NFV N
JOIN E140IPV I
  ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
LEFT JOIN E012FAM F ON F.CODEMP = I.CODEMP AND F.CODFAM = I.CODFAM
WHERE N.SITNFV = '2'
  AND N.CODCLI NOT IN (1001, 1002, 1004)
  AND N.DATEMI >= :start_date
GROUP BY N.CODCLI, F.DESFAM, I.CPLIPV
"""

SECTORS_SQL = "SELECT CODRAM AS CODIGO, DESRAM AS NOME FROM E026RAM ORDER BY CODRAM"

# Reserva para cliente sem CEP no cadastro de CEPs: o município pelo nome da cidade.
CITY_SQL = "SELECT SIGUFS AS UF, NOMCID AS CIDADE, MAX(CODIBG) AS IBGE FROM E008CEP WHERE CODIBG > 0 GROUP BY SIGUFS, NOMCID"


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def normalize_text(value):
    """Maiúsculas, sem acento e sem espaço repetido (para casar nomes de cidade)."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.upper().split())


def _f(value, digits=2):
    """Número para JSON (float) ou None."""
    if value is None:
        return None
    return round(float(value), digits)


def money(value):
    return "n/d" if value is None else dna._money(value)


def pct(value, signed=False):
    return dna._pct(value, 1, signed)


def kg(value):
    return dna._kg(value or ZERO)


def rep_display_name(code, base):
    if code is None:
        return "Sem representante"
    return dna.REPRESENTATIVE_DISPLAY_NAMES.get(code) or base["rep_names"].get(code) or f"Representante {code}"


# ---------------------------------------------------------------------------
# Carga (Oracle) e montagem da base
# ---------------------------------------------------------------------------

def load_base(force=False, today=None):
    """Base das telas, guardada por 30 minutos. `force` relê o ERP."""
    if not force:
        cached = cache.get(BASE_CACHE_KEY)
        if cached is not None:
            return cached
    today = today or date.today()
    month_start = today.replace(day=1)
    start = dna._add_months(month_start, -(HISTORY_MONTHS - 1))
    mix_start = dna._add_months(month_start, -(MIX_MONTHS - 1))
    rows = dna._run({
        "monthly": (MONTHLY_SQL, {"start_date": start, "today_day": today.day}),
        "info": (INFO_SQL, {"start_date": start}),
        "mix": (MIX_SQL, {"start_date": mix_start}),
        "sectors": (SECTORS_SQL, {}),
    })
    base = build_base(rows["monthly"], rows["info"], rows["mix"], rows["sectors"], today)
    if any(customer["ibge"] is None and customer["state"] in STATES for customer in base["customers"].values()):
        fill_ibge_by_city(base, _city_codes())
    cache.set(BASE_CACHE_KEY, base, BASE_CACHE_SECONDS)
    return base


def clear_cache():
    cache.delete(BASE_CACHE_KEY)


def _city_codes():
    cached = cache.get(CITY_CACHE_KEY)
    if cached is None:
        rows = dna._run({"cities": (CITY_SQL, {})})["cities"]
        cached = {}
        for row in rows:
            key = ((row.get("uf") or "").strip().upper(), normalize_text(row.get("cidade")))
            code = dna._int(row.get("ibge"))
            if code and key not in cached:
                cached[key] = code
        cache.set(CITY_CACHE_KEY, cached, CITY_CACHE_SECONDS)
    return cached


def fill_ibge_by_city(base, city_codes):
    for customer in base["customers"].values():
        if customer["ibge"] is None:
            customer["ibge"] = city_codes.get((customer["state"], normalize_text(customer["city"])))


def build_base(monthly_rows, info_rows, mix_rows, sector_rows, today):
    """Junta as consultas em dicionários simples (o cache serializa com pickle)."""
    customers = {}
    for row in info_rows:
        code = dna._int(row.get("cliente"))
        if code is None or code in customers:
            continue
        customers[code] = _customer_record(code, row)

    months = defaultdict(list)
    rep_names = {}
    for row in monthly_rows:
        code = dna._int(row.get("cliente"))
        month = dna._date(row.get("mes"))
        if code is None or month is None:
            continue
        raw_rep = dna._int(row.get("representante"))
        rep = dna.primary_rep(raw_rep)
        name = (row.get("nome_representante") or "").strip()
        if rep is not None and name and (rep not in rep_names or raw_rep == rep):
            rep_names[rep] = name
        cpv = row.get("cpv")
        months[code].append({
            "month": dna._month_key(month),
            "rep": rep,
            "net": dna._num(row.get("liquido")),
            "net_to_day": dna._num(row.get("liquido_ate_dia")),
            "tsi": dna._num(row.get("sem_impostos")),
            "cpv": None if cpv is None else dna._num(cpv),
            "cpv_base": dna._num(row.get("base_cpv")),
            "kg": dna._num(row.get("peso")),
            "notes": int(row.get("notas") or 0),
            "first": dna._date(row.get("primeira")),
            "last": dna._date(row.get("ultima")),
        })
        if code not in customers:
            customers[code] = _customer_record(code, {})

    mix = {}
    for row in mix_rows:
        code = dna._int(row.get("cliente"))
        net = dna._num(row.get("liquido"))
        if code is None or net <= 0:
            continue
        group = dna.product_group(row.get("familia"), row.get("complemento"))
        bucket = mix.setdefault(code, {}).setdefault(group, {"net": ZERO, "kg": ZERO})
        bucket["net"] += net
        bucket["kg"] += dna._num(row.get("peso"))

    sectors = [
        {"code": (row.get("codigo") or "").strip(), "name": (row.get("nome") or "").strip()}
        for row in sector_rows
        if (row.get("codigo") or "").strip()
    ]
    return {
        "loaded_at": timezone.now(),
        "today": today,
        "customers": customers,
        "months": dict(months),
        "mix": mix,
        "rep_names": rep_names,
        "sectors": sectors,
    }


def _customer_record(code, row):
    state = (row.get("uf") or "").strip().upper()
    ibge = dna._int(row.get("ibge"))
    return {
        "code": code,
        "name": (row.get("nome") or "").strip() or f"Cliente {code}",
        "trade_name": (row.get("fantasia") or "").strip(),
        "cnpj": econodata.normalize_cnpj(row.get("cnpj")),
        "person": (row.get("tipo") or "").strip().upper() == "F",
        "city": (row.get("cidade") or "").strip().title(),
        "state": state if state in STATES else FOREIGN if state else "",
        "ibge": ibge if ibge and ibge >= 1000000 else None,
        "sector_code": (row.get("ramo") or "").strip() or None,
        "sector_name": (row.get("ramo_nome") or "").strip() or None,
    }


# ---------------------------------------------------------------------------
# Período
# ---------------------------------------------------------------------------

def resolve_window(period_key=None, today=None):
    """Recorte por mês fechado (a base é mensal): 3, 6, 12 meses ou o ano atual."""
    key = period_key if period_key in dict(PERIOD_CHOICES) else DEFAULT_PERIOD
    window = dna.resolve_window(key, today=today)
    window["first_key"] = dna._month_key(window["start"])
    window["last_key"] = dna._month_key(window["end"] - timedelta(days=1))
    window["previous_first_key"] = window["first_key"] - 12
    window["previous_last_key"] = window["last_key"] - 12
    window["choices"] = [{"key": choice, "label": label} for choice, label in PERIOD_CHOICES]
    return window


# ---------------------------------------------------------------------------
# Indicadores por cliente
# ---------------------------------------------------------------------------

def _aggregate(rows, first_key, last_key):
    net = tsi = cpv = cpv_base = weight = ZERO
    notes = 0
    months = set()
    reps = defaultdict(lambda: ZERO)
    cpv_known = False
    for row in rows:
        if not first_key <= row["month"] <= last_key:
            continue
        net += row["net"]
        tsi += row["tsi"]
        weight += row["kg"]
        notes += row["notes"]
        months.add(row["month"])
        if row["rep"] is not None:
            reps[row["rep"]] += row["net"]
        if row["cpv"] is not None:
            cpv_known = True
            cpv += row["cpv"]
            cpv_base += row["cpv_base"]
    margin = ONE - cpv / cpv_base if cpv_known and cpv_base > 0 else None
    return {
        "net": net,
        "tsi": tsi,
        "kg": weight,
        "notes": notes,
        "months": months,
        "reps": dict(reps),
        "margin": margin,
        # Lucro bruto = sem impostos × margem: a margem é medida nos itens com CPV
        # e vale para o total, como no DNA (a cobertura de custo vai junto).
        "profit": tsi * margin if margin is not None else None,
        "cost_coverage": cpv_base / tsi if tsi else None,
    }


def _previous_net(rows, window):
    """Faturamento do mesmo recorte um ano antes, com o último mês cortado no mesmo dia."""
    total = ZERO
    for row in rows:
        if window["previous_first_key"] <= row["month"] < window["previous_last_key"]:
            total += row["net"]
        elif row["month"] == window["previous_last_key"]:
            total += row["net_to_day"]
    return total


def purchase_rhythm(purchase_months, last_purchase, today):
    """Intervalo típico entre compras (mediana, em meses) e quando a próxima é esperada."""
    if not last_purchase:
        label, tone = RHYTHM_STATUS["none"]
        return {"status": "none", "label": label, "tone": tone, "days_since": None,
                "typical_days": None, "next_purchase": None, "overdue_days": None}
    days_since = (today - last_purchase).days
    gaps = [later - earlier for earlier, later in zip(purchase_months, purchase_months[1:]) if later > earlier]
    typical_days = max(round(statistics.median(gaps) * 30.4), 30) if gaps else None
    next_purchase = last_purchase + timedelta(days=typical_days) if typical_days else None
    if days_since > INACTIVE_DAYS:
        status = "inactive"
    elif typical_days is None:
        status = "single"
    else:
        ratio = days_since / typical_days
        status = "on_track" if ratio <= 1.25 else "late" if ratio <= 2 else "at_risk"
    label, tone = RHYTHM_STATUS[status]
    overdue = (today - next_purchase).days if next_purchase and today > next_purchase else 0
    return {
        "status": status,
        "label": label,
        "tone": tone,
        "days_since": days_since,
        "typical_days": typical_days,
        "next_purchase": next_purchase,
        "overdue_days": overdue if next_purchase else None,
    }


def momentum(rows, today):
    """Últimos 3 meses fechados contra os 3 anteriores (o mês corrente fica de fora)."""
    current_key = dna._month_key(today)
    by_month = defaultdict(lambda: ZERO)
    for row in rows:
        by_month[row["month"]] += row["net"]
    recent = sum((by_month[key] for key in range(current_key - 3, current_key)), ZERO)
    before = sum((by_month[key] for key in range(current_key - 6, current_key - 3)), ZERO)
    change = dna._change(recent, before) if before > 0 else None
    if change is None:
        label, arrow = ("Sem base", "")
    elif change > Decimal("0.15"):
        label, arrow = ("Acelerando", "up")
    elif change < Decimal("-0.15"):
        label, arrow = ("Desacelerando", "down")
    else:
        label, arrow = ("Estável", "flat")
    return {"change": change, "label": label, "arrow": arrow}


def customer_metrics(base, window, today=None):
    """Indicadores de todos os clientes da base no recorte (ativos ou não)."""
    today = today or base["today"]
    today_key = dna._month_key(today)
    history_first = today_key - (HISTORY_MONTHS - 1)
    growth_ok = window["previous_first_key"] >= history_first
    result = {}
    for code, rows in base["months"].items():
        customer = base["customers"].get(code) or _customer_record(code, {})
        current = _aggregate(rows, window["first_key"], window["last_key"])
        history = _aggregate(rows, history_first, today_key)
        previous_net = _previous_net(rows, window) if growth_ok else None
        purchase_months = sorted({row["month"] for row in rows})
        last = max((row["last"] for row in rows if row["last"]), default=None)
        first = min((row["first"] for row in rows if row["first"]), default=None)
        reps = current["reps"] or history["reps"]
        rep = max(reps.items(), key=lambda item: item[1])[0] if reps else None
        result[code] = {
            **customer,
            "net": current["net"],
            "tsi": current["tsi"],
            "kg": current["kg"],
            "notes": current["notes"],
            "active_months": len(current["months"]),
            "margin": current["margin"],
            "profit": current["profit"],
            "cost_coverage": current["cost_coverage"],
            "fator": current["net"] / current["kg"] if current["kg"] else None,
            "previous_net": previous_net,
            "growth": dna._change(current["net"], previous_net) if previous_net else None,
            "history_net": history["net"],
            "history_profit": history["profit"],
            "purchase_months": len(purchase_months),
            # Faturamento típico de um mês com compra (24 meses): o tamanho esperado do próximo pedido.
            "typical_value": history["net"] / len(purchase_months) if purchase_months else ZERO,
            "first_purchase": first,
            "last_purchase": last,
            "days_since": (today - last).days if last else None,
            "is_new": bool(first and (today - first).days <= NEW_CUSTOMER_DAYS),
            "rhythm": purchase_rhythm(purchase_months, last, today),
            "momentum": momentum(rows, today),
            "rep_code": rep,
            "rep_name": rep_display_name(rep, base),
        }
    return result


# ---------------------------------------------------------------------------
# RFM
# ---------------------------------------------------------------------------

def percentile_scores(values):
    """Nota de 1 a 5 pelo percentil do valor (empates recebem a mesma nota)."""
    ordered = sorted(values.values())
    total = len(ordered)
    scores = {}
    for key, value in values.items():
        below = bisect.bisect_left(ordered, value)
        equal = bisect.bisect_right(ordered, value) - below
        percentile = (below + equal / 2) / total
        scores[key] = min(5, int(percentile * 5) + 1)
    return scores


def rfm_segment(recency, frequency, monetary, is_new=False):
    value = (frequency + monetary) / 2
    if is_new:
        return "new"
    if recency >= 4 and value >= 4:
        return "champions"
    if recency >= 3 and value >= 3:
        return "loyal"
    if recency >= 4:
        return "promising"
    if recency == 3:
        return "attention"
    if value >= 4:
        return "cant_lose"
    if value >= 3:
        return "at_risk"
    if recency == 2:
        return "hibernating"
    return "lost"


def apply_rfm(metrics):
    """Notas e segmento RFM dos clientes com compra no recorte (sobre a base inteira,
    para o segmento não mudar com o filtro da tela)."""
    active = [item for item in metrics.values() if item["net"] > 0]
    if not active:
        return
    recency = percentile_scores({item["code"]: -(item["days_since"] or 0) for item in active})
    frequency = percentile_scores({item["code"]: item["notes"] for item in active})
    monetary = percentile_scores({item["code"]: item["net"] for item in active})
    for item in active:
        code = item["code"]
        key = rfm_segment(recency[code], frequency[code], monetary[code], item["is_new"])
        label, tone, description = RFM_SEGMENTS[key]
        item["rfm"] = {
            "r": recency[code], "f": frequency[code], "m": monetary[code], "key": key,
            "label": label, "tone": tone, "description": description,
            "code": f"{recency[code]}{frequency[code]}{monetary[code]}",
        }


# ---------------------------------------------------------------------------
# Segmento (ramo do ERP, ou a classificação da IA para quem está sem ramo)
# ---------------------------------------------------------------------------

def apply_segments(metrics, base, guesses):
    """`guesses`: {código do cliente: {"code", "name", "confidence"}} vindos da IA."""
    names = {sector["code"]: sector["name"] for sector in base["sectors"]}
    for item in metrics.values():
        if item["sector_code"]:
            item["segment"] = {"code": item["sector_code"], "name": item["sector_name"] or names.get(item["sector_code"], item["sector_code"]),
                               "source": "erp", "source_label": "ERP", "confidence": None}
        elif item["code"] in guesses:
            guess = guesses[item["code"]]
            code = guess["code"] or OTHER_SEGMENT
            item["segment"] = {"code": code, "name": guess["name"] or names.get(code, code),
                               "source": "ia", "source_label": "IA", "confidence": guess.get("confidence")}
        else:
            item["segment"] = None


def segment_label(item):
    segment = item.get("segment")
    return segment["name"].title() if segment else "Sem segmento"


# ---------------------------------------------------------------------------
# Carteira: os mais lucrativos, risco e concentração
# ---------------------------------------------------------------------------

def _as_set(value):
    if value is None or value == "":
        return set()
    if isinstance(value, (list, tuple, set)):
        return set(value)
    return {value}


def filter_rows(rows, state=None, rep=None, segment=None):
    """Clientes da UF, do representante e do segmento escolhidos (cada filtro aceita um valor ou vários)."""
    states, reps, segments = _as_set(state), _as_set(rep), _as_set(segment)
    selected = []
    for item in rows:
        if states and item["state"] not in states:
            continue
        if reps and item["rep_code"] not in reps:
            continue
        if segments:
            code = item["segment"]["code"] if item.get("segment") else "none"
            if code not in segments:
                continue
        selected.append(item)
    return selected


def filter_rfm(rows, keys):
    """Só os clientes dos segmentos RFM escolhidos (Campeões, Hibernando...)."""
    wanted = set(keys or ())
    if not wanted:
        return rows
    return [item for item in rows if item.get("rfm") and item["rfm"]["key"] in wanted]


def rank_by_profit(rows):
    """Ordem do ranking: lucro bruto; sem custo conhecido, entra pelo faturamento sem impostos × margem média."""
    known = [item for item in rows if item["margin"] is not None and item["tsi"] > 0]
    average = sum((item["profit"] for item in known), ZERO) / sum((item["tsi"] for item in known), ZERO) if known else ZERO
    for item in rows:
        item["profit_rank_value"] = item["profit"] if item["profit"] is not None else item["tsi"] * average
    ordered = sorted(rows, key=lambda item: item["profit_rank_value"], reverse=True)
    for position, item in enumerate(ordered, start=1):
        item["profit_position"] = position
    return ordered


def pareto_stats(values):
    """Concentração do lucro: quanto os 20% maiores geram, quantos fazem 80% e a curva."""
    positive = sorted((value for value in values if value and value > 0), reverse=True)
    total = sum(positive, ZERO)
    count = len(positive)
    if not count or total <= 0:
        return {"count": 0, "top20_count": 0, "top20_share": None, "count_for_80": None, "curve": [], "hhi": None}
    prefix = []
    running = ZERO
    for value in positive:
        running += value
        prefix.append(running)
    top = max(1, round(count * 0.2))
    count_for_80 = next(index + 1 for index, value in enumerate(prefix) if value / total >= Decimal("0.8"))
    steps = min(count, 40)
    curve = []
    for step in range(1, steps + 1):
        size = math.ceil(step * count / steps)
        curve.append({"customers_pct": round(size / count * 100, 1), "profit_pct": _f(prefix[size - 1] / total * 100, 1)})
    hhi = sum(((value / total) ** 2 for value in positive), ZERO) * 10000
    return {
        "count": count,
        "top20_count": top,
        "top20_share": prefix[top - 1] / total,
        "count_for_80": count_for_80,
        "curve": curve,
        "hhi": hhi,
    }


def portfolio_summary(rows):
    """KPIs e distribuições da carteira filtrada (só quem comprou no recorte)."""
    active = [item for item in rows if item["net"] > 0]
    net = sum((item["net"] for item in active), ZERO)
    known = [item for item in active if item["margin"] is not None]
    profit = sum((item["profit"] for item in known), ZERO)
    tsi_known = sum((item["tsi"] for item in known), ZERO)
    # O ano anterior soma todos os clientes, inclusive quem parou de comprar: sem
    # eles, o crescimento da carteira sairia inflado.
    previous_net = sum((item["previous_net"] for item in rows if item["previous_net"] is not None), ZERO)
    negative = [item for item in known if item["margin"] < 0]
    low = [item for item in known if ZERO <= item["margin"] < LOW_MARGIN]
    suspect = [item for item in known if item["margin"] >= SUSPECT_MARGIN]
    segments = Counter(item["rfm"]["key"] for item in active if item.get("rfm"))
    segment_net = defaultdict(lambda: ZERO)
    for item in active:
        if item.get("rfm"):
            segment_net[item["rfm"]["key"]] += item["net"]
    rhythm = Counter(item["rhythm"]["status"] for item in active)
    return {
        "customers": len(active),
        "net": net,
        "profit": profit if known else None,
        "margin": profit / tsi_known if tsi_known else None,
        "growth": dna._change(net, previous_net) if previous_net else None,
        "kg": sum((item["kg"] for item in active), ZERO),
        "pareto": pareto_stats([item["profit"] for item in known]),
        "negative_count": len(negative),
        "negative_net": sum((item["net"] for item in negative), ZERO),
        "low_count": len(low),
        "low_net": sum((item["net"] for item in low), ZERO),
        "suspect_count": len(suspect),
        "no_cost_count": len(active) - len(known),
        "rfm": [
            {"key": key, "label": RFM_SEGMENTS[key][0], "tone": RFM_SEGMENTS[key][1],
             "description": RFM_SEGMENTS[key][2], "count": segments.get(key, 0), "net": segment_net.get(key, ZERO)}
            for key in RFM_ORDER
        ],
        "late_count": rhythm.get("late", 0),
        "at_risk_count": rhythm.get("at_risk", 0),
    }


def reactivation_rows(metrics):
    """Clientes sem compra há mais de 180 dias (com compra nos 24 meses), pelo lucro da base."""
    rows = [
        item for item in metrics.values()
        if item["days_since"] is not None and item["days_since"] > INACTIVE_DAYS and item["history_net"] > 0
    ]
    return sorted(rows, key=lambda item: item["history_profit"] if item["history_profit"] is not None else item["history_net"] * Decimal("0.1"), reverse=True)


# ---------------------------------------------------------------------------
# Geografia
# ---------------------------------------------------------------------------

def _area():
    return {"customers": 0, "net": ZERO, "tsi": ZERO, "profit": ZERO, "tsi_known": ZERO, "kg": ZERO,
            "previous_net": ZERO, "has_previous": False, "top": []}


def _add(area, item):
    """Soma o cliente na área: o recorte atual só se comprou nele; o ano anterior sempre."""
    if item["previous_net"] is not None:
        area["previous_net"] += item["previous_net"]
        area["has_previous"] = True
    if item["net"] <= 0:
        return
    area["customers"] += 1
    area["net"] += item["net"]
    area["tsi"] += item["tsi"]
    area["kg"] += item["kg"]
    if item["margin"] is not None:
        area["profit"] += item["profit"]
        area["tsi_known"] += item["tsi"]
    area["top"].append(item)


def _finish(area, total_net, top=5):
    area["margin"] = area["profit"] / area["tsi_known"] if area["tsi_known"] else None
    area["profit"] = area["profit"] if area["tsi_known"] else None
    area["growth"] = dna._change(area["net"], area["previous_net"]) if area["has_previous"] and area["previous_net"] else None
    area["share"] = area["net"] / total_net if total_net else None
    area["fator"] = area["net"] / area["kg"] if area["kg"] else None
    area["top"] = sorted(area["top"], key=lambda item: item.get("profit_rank_value", item["net"]), reverse=True)[:top]
    return area


def geography(rows):
    """Clientes somados por região, UF e município (código IBGE).

    Entram no recorte só os que compraram nele; no ano anterior entram todos, para
    o crescimento de cada área contar também os clientes que pararam de comprar.
    Uma área sem compra no recorte, mas com compra no ano anterior, aparece com
    zero cliente: é onde a carteira foi perdida.
    """
    active = [item for item in rows if item["net"] > 0]
    total_net = sum((item["net"] for item in active), ZERO)
    regions, states, cities = {}, {}, {}
    for item in rows:
        if item["net"] <= 0 and not item["previous_net"]:
            continue
        state = item["state"] if item["state"] in STATES else FOREIGN
        region = STATES[state][2] if state in STATES else FOREIGN
        _add(regions.setdefault(region, _area()), item)
        _add(states.setdefault(state, _area()), item)
        if state in STATES:
            key = str(item["ibge"]) if item["ibge"] else f"{state}:{normalize_text(item['city'])}"
            city = cities.setdefault(key, {**_area(), "name": item["city"] or "Cidade não informada", "state": state,
                                           "ibge": item["ibge"]})
            _add(city, item)
    for area in regions.values():
        _finish(area, total_net)
    for area in states.values():
        _finish(area, total_net)
    for area in cities.values():
        _finish(area, total_net, top=8)
    return {"regions": regions, "states": states, "cities": cities, "total_net": total_net}


# ---------------------------------------------------------------------------
# Comparação com o segmento e venda cruzada
# ---------------------------------------------------------------------------

def segment_benchmarks(metrics):
    """Mediana de margem e de preço por kg de cada segmento (só quem comprou no recorte)."""
    margins, prices, counts = defaultdict(list), defaultdict(list), Counter()
    for item in metrics.values():
        if item["net"] <= 0 or not item.get("segment"):
            continue
        key = item["segment"]["code"]
        counts[key] += 1
        if item["margin"] is not None:
            margins[key].append(item["margin"])
        if item["fator"] is not None:
            prices[key].append(item["fator"])
    return {
        key: {
            "customers": counts[key],
            "margin": statistics.median(margins[key]) if margins[key] else None,
            "fator": statistics.median(prices[key]) if prices[key] else None,
        }
        for key in counts
    }


def cross_sell_model(base, metrics, min_peers=CROSS_SELL_MIN_PEERS):
    """Para cada segmento: que fração dos clientes compra cada grupo de produto (12 meses)."""
    members = defaultdict(list)
    for code, groups in base["mix"].items():
        item = metrics.get(code)
        if item and item.get("segment") and groups and item["segment"]["code"] != OTHER_SEGMENT:
            members[item["segment"]["code"]].append(code)
    model = {}
    for sector, codes in members.items():
        if len(codes) < min_peers:
            continue
        buyers = defaultdict(list)
        for code in codes:
            for group, values in base["mix"][code].items():
                buyers[group].append(values["net"])
        model[sector] = {
            "peers": len(codes),
            "groups": {
                group: {
                    "buyers": len(values),
                    "adoption": Decimal(len(values)) / Decimal(len(codes)),
                    "median_net": statistics.median(values),
                }
                for group, values in buyers.items()
            },
        }
    return model


def cross_sell_for(item, base, model, limit=4, min_adoption=CROSS_SELL_MIN_ADOPTION):
    """Grupos que boa parte do segmento compra e este cliente não."""
    segment = item.get("segment")
    info = model.get(segment["code"]) if segment else None
    if not info:
        return {"segment": segment, "peers": info["peers"] if info else 0, "items": []}
    owned = set((base["mix"].get(item["code"]) or {}).keys())
    candidates = [
        (group, data) for group, data in info["groups"].items()
        if group not in owned and data["adoption"] >= min_adoption
        and not any(group.startswith(prefix) for prefix in CROSS_SELL_IGNORED)
    ]
    candidates.sort(key=lambda pair: (pair[1]["adoption"], pair[1]["median_net"]), reverse=True)
    return {
        "segment": segment,
        "peers": info["peers"],
        "items": [
            {"group": group.title(), "adoption": data["adoption"], "adoption_display": pct(data["adoption"]),
             "buyers": data["buyers"], "median_net": data["median_net"], "median_display": money(data["median_net"])}
            for group, data in candidates[:limit]
        ],
    }


def sector_cnaes(sector_code, cached_records):
    """CNAEs para a Econodata: os dos clientes do ramo já enriquecidos; sem eles, a tabela fixa."""
    found = Counter()
    for record in cached_records:
        code = (record.cnae_codigo or "")
        if code:
            found[code[:6]] += 1  # "C-1012"
    if found:
        return [code for code, _count in found.most_common(4)]
    return SECTOR_CNAE_PREFIXES.get(sector_code, [])


# ---------------------------------------------------------------------------
# Textos e JSON para as telas
# ---------------------------------------------------------------------------

def _margin_tone(margin):
    if margin is None:
        return ""
    if margin < 0:
        return "is-bad"
    if margin < LOW_MARGIN or margin >= SUSPECT_MARGIN:
        return "is-warn"
    return ""


def row_display(item):
    """Campos de exibição de um cliente (tabela, ranking e ficha)."""
    rhythm = item["rhythm"]
    return {
        "code": item["code"],
        "name": item["name"],
        "trade_name": item["trade_name"],
        "city": item["city"],
        "state": item["state"],
        "place": f"{item['city']}/{item['state']}" if item["city"] else item["state"] or "-",
        "rep_code": item["rep_code"],
        "rep_name": item["rep_name"],
        "segment": item.get("segment"),
        "segment_label": segment_label(item),
        "net": _f(item["net"]),
        "net_display": money(item["net"]),
        "profit": _f(item["profit"]),
        "profit_display": money(item["profit"]),
        "margin": _f(item["margin"], 4),
        "margin_display": pct(item["margin"]),
        "margin_tone": _margin_tone(item["margin"]),
        "margin_note": "custo zerado no ERP? confira o CPV" if item["margin"] is not None and item["margin"] >= SUSPECT_MARGIN else "",
        "growth": _f(item["growth"], 4),
        "growth_display": pct(item["growth"], signed=True) if item["growth"] is not None else "–",
        "growth_tone": "is-up" if item["growth"] is not None and item["growth"] > 0 else "is-down" if item["growth"] is not None and item["growth"] < 0 else "",
        "kg": _f(item["kg"], 0),
        "kg_display": kg(item["kg"]),
        "fator_display": money(item["fator"]) if item["fator"] is not None else "n/d",
        "history_net_display": money(item["history_net"]),
        "history_profit_display": money(item["history_profit"]),
        "history_profit": _f(item["history_profit"] if item["history_profit"] is not None else item["history_net"] * Decimal("0.1")),
        "last_purchase_display": dna._display_date(item["last_purchase"]) if item["last_purchase"] else "-",
        "days_since": item["days_since"],
        "rhythm": {**rhythm, "next_purchase_display": dna._display_date(rhythm["next_purchase"]) if rhythm["next_purchase"] else None},
        "momentum": item["momentum"],
        "rfm": item.get("rfm"),
        "profit_position": item.get("profit_position"),
        "cnpj": econodata.format_cnpj(item["cnpj"]) if item["cnpj"] else "",
    }


def area_json(area, name=None):
    return {
        "name": name or area.get("name"),
        "customers": area["customers"],
        "net": _f(area["net"]),
        "profit": _f(area["profit"]),
        "margin": _f(area["margin"], 4),
        "growth": _f(area["growth"], 4),
        "share": _f(area["share"], 4),
        "kg": _f(area["kg"], 0),
        "fator": _f(area["fator"]),
        "top": [
            {"code": item["code"], "name": item["name"], "city": item["city"], "net": _f(item["net"]),
             "profit": _f(item["profit"]), "margin": _f(item["margin"], 4)}
            for item in area["top"]
        ],
    }


def monthly_series(base, code, today, months=HISTORY_MONTHS):
    """Faturamento e lucro bruto mês a mês do cliente (24 meses)."""
    last = dna._month_key(today)
    first = last - months + 1
    by_month = defaultdict(list)
    for row in base["months"].get(code, []):
        by_month[row["month"]].append(row)
    series = []
    for key in range(first, last + 1):
        aggregate = _aggregate(by_month.get(key, []), key, key)
        series.append({
            "label": dna._month_label(key),
            "revenue": float(aggregate["net"]),
            "profit": _f(aggregate["profit"]) if aggregate["net"] else None,
            "margin": _f(aggregate["margin"], 4),
        })
    return series


def mix_rows(base, code, limit=8):
    groups = base["mix"].get(code) or {}
    total = sum((values["net"] for values in groups.values()), ZERO)
    ordered = sorted(groups.items(), key=lambda pair: pair[1]["net"], reverse=True)
    return [
        {"group": group.title(), "net_display": money(values["net"]), "kg_display": kg(values["kg"]),
         "share": _f(values["net"] / total * 100, 1) if total else 0, "share_display": pct(values["net"] / total) if total else "-"}
        for group, values in ordered[:limit]
    ]


# ---------------------------------------------------------------------------
# Dados enviados à IA (compactos: só o que a leitura precisa)
# ---------------------------------------------------------------------------

def _ai_area(area, name):
    return {
        "nome": name,
        "clientes": area["customers"],
        "faturamento": _f(area["net"], 0),
        "lucro_bruto": _f(area["profit"], 0),
        "margem_pct": _f(area["margin"] * 100, 1) if area["margin"] is not None else None,
        "crescimento_pct": _f(area["growth"] * 100, 1) if area["growth"] is not None else None,
        "participacao_pct": _f(area["share"] * 100, 1) if area["share"] is not None else None,
        "preco_medio_kg": _f(area["fator"]),
    }


def _ai_customer(item):
    return {
        "cliente": item["name"],
        "cidade": f"{item['city']}/{item['state']}",
        "segmento": segment_label(item),
        "representante": item["rep_name"],
        "faturamento": _f(item["net"], 0),
        "lucro_bruto": _f(item["profit"], 0),
        "margem_pct": _f(item["margin"] * 100, 1) if item["margin"] is not None else None,
        "crescimento_pct": _f(item["growth"] * 100, 1) if item["growth"] is not None else None,
        "ritmo": item["rhythm"]["label"],
        "rfm": item["rfm"]["label"] if item.get("rfm") else None,
    }


def map_ai_data(geo, summary, window, filters):
    states = sorted(
        ((sigla, area) for sigla, area in geo["states"].items() if sigla in STATES),
        key=lambda pair: pair[1]["net"], reverse=True,
    )
    cities = sorted(geo["cities"].values(), key=lambda area: area["net"], reverse=True)
    return {
        "periodo": window["label"],
        "filtros": filters,
        "total": {
            "clientes": summary["customers"],
            "faturamento": _f(summary["net"], 0),
            "lucro_bruto": _f(summary["profit"], 0),
            "margem_pct": _f(summary["margin"] * 100, 1) if summary["margin"] is not None else None,
            "crescimento_pct": _f(summary["growth"] * 100, 1) if summary["growth"] is not None else None,
        },
        "regioes": [_ai_area(geo["regions"][key], REGIONS[key]) for key in REGION_ORDER if key in geo["regions"]],
        "ufs": [_ai_area(area, STATES[sigla][1]) for sigla, area in states[:12]],
        "cidades": [_ai_area(area, f"{area['name']}/{area['state']}") for area in cities[:12]],
        "exterior": _ai_area(geo["states"][FOREIGN], "Exterior") if FOREIGN in geo["states"] else None,
    }


def portfolio_ai_data(summary, ranked, at_risk, window, filters):
    pareto = summary["pareto"]
    return {
        "periodo": window["label"],
        "filtros": filters,
        "total": {
            "clientes_ativos": summary["customers"],
            "faturamento": _f(summary["net"], 0),
            "lucro_bruto": _f(summary["profit"], 0),
            "margem_pct": _f(summary["margin"] * 100, 1) if summary["margin"] is not None else None,
            "crescimento_pct": _f(summary["growth"] * 100, 1) if summary["growth"] is not None else None,
        },
        "concentracao": {
            "top20_pct_clientes_geram_pct_lucro": _f(pareto["top20_share"] * 100, 1) if pareto["top20_share"] is not None else None,
            "clientes_para_80pct_do_lucro": pareto["count_for_80"],
        },
        "margem_negativa": {"clientes": summary["negative_count"], "faturamento": _f(summary["negative_net"], 0)},
        "margem_abaixo_5pct": {"clientes": summary["low_count"], "faturamento": _f(summary["low_net"], 0)},
        "segmentos_rfm": [{"segmento": row["label"], "clientes": row["count"], "faturamento": _f(row["net"], 0)} for row in summary["rfm"] if row["count"]],
        "mais_lucrativos": [_ai_customer(item) for item in ranked[:15]],
        "risco_de_parada": [_ai_customer(item) for item in at_risk[:10]],
    }


def customer_ai_data(item, window, benchmark, cross_sell, mix, series, market):
    main = (market or {}).get("main") or {}
    return {
        "periodo": window["label"],
        "cliente": {
            **_ai_customer(item),
            "posicao_no_ranking_de_lucro": item.get("profit_position"),
            "kg": _f(item["kg"], 0),
            "preco_medio_kg": _f(item["fator"]),
            "notas_no_periodo": item["notes"],
            "ultima_compra_dias": item["days_since"],
            "intervalo_tipico_dias": item["rhythm"]["typical_days"],
            "tendencia_3_meses": item["momentum"]["label"],
            "cliente_desde": item["first_purchase"].isoformat() if item["first_purchase"] else None,
        },
        "segmento_mediana": {
            "margem_pct": _f(benchmark["margin"] * 100, 1) if benchmark and benchmark.get("margin") is not None else None,
            "preco_medio_kg": _f(benchmark["fator"]) if benchmark else None,
            "clientes": benchmark["customers"] if benchmark else None,
        },
        "mix_12_meses": [{"grupo": row["group"], "participacao": row["share_display"]} for row in mix[:6]],
        "venda_cruzada": [{"grupo": row["group"], "compram_no_segmento": row["adoption_display"]} for row in cross_sell["items"]],
        "faturamento_mensal_ultimos_12": [{"mes": row["label"], "faturamento": round(row["revenue"])} for row in series[-12:]],
        "mercado_econodata": {
            "cnae": main.get("cnae_descricao") or None,
            "situacao_receita": main.get("situacao") or None,
            "porte": main.get("porte") or main.get("enquadramento_porte") or None,
            "faturamento_estimado_grupo": _f(main.get("faturamento_grupo"), 0),
        } if main.get("found") else None,
    }
