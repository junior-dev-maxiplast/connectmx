"""
BI de Viagens — indicadores da frota (USU_TCADVIA), servidos ao ConnectMX Dashes.

As agregações rodam no Oracle do ERP Senior, não em Python: a base já passa de
4,5 mil viagens e vai crescer conforme novas consultas entrarem no painel.

O cadastro de viagem tem duas armadilhas que moldam todo este módulo:

1. **Data não preenchida é `1900-12-31`, não NULL.** Viagem aberta chega com
   saída e chegada nessa sentinela; comparar com NULL não filtra nada e a média
   de duração desaba para -44 dias. Toda data passa por `> DATE '1901-01-01'`.

2. **KM e litros têm digitação errada de verdade.** Na base inteira há 42
   viagens com quilometragem de chegada menor que a de saída, 48 com as duas
   iguais e 19 acima de 10.000 km (a maior marca 528 mil km — hodômetro digitado
   por engano). Os indicadores de distância e consumo só usam a faixa saneada;
   o que ficou de fora vira o painel de qualidade de cadastro, em vez de
   contaminar as médias em silêncio.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

# Uma fábrica de conexão só para o ERP: o DNA do Cliente já resolve o driver
# (oracledb com fallback para cx_Oracle) e as variáveis de ambiente.
from .customer_dna import _oracle_connection_safe as _erp_connection

# Período é vocabulário comum dos painéis do Dashes, não regra da frota: o BI do
# TI usa exatamente os mesmos recortes.
from .bi_periods import mark_series_ticks, period_choices, resolve_period, series_label


# ------------------------------------------------------------- saneamento --

# `1900-12-31` é o "não informado" do cadastro de viagem. O corte fica em
# 1901-01-01 para pegar a sentinela sem descartar data real nenhuma.
DATE_FLOOR_SQL = "DATE '1901-01-01'"

# Faixa de quilometragem aceita como real. Abaixo de 1 km a viagem não saiu do
# lugar (ou o KM de chegada foi digitado menor); acima de 10 mil o número é
# hodômetro inteiro no lugar da diferença.
KM_MIN = 1
KM_MAX = 10000

# Viagem de 60 dias já é exceção; acima disso o que existe na base é ano
# digitado errado (saída em 2023, chegada em 1923).
DURATION_MAX_DAYS = 60

# Consumo plausível para caminhão pesado. A frota roda em torno de 3,4 km/l;
# fora desta faixa o par KM/litros não fecha e não entra na média.
CONSUMPTION_MIN = 0.5
CONSUMPTION_MAX = 15.0


SITUATION_CHOICES = [
    ("all", "Todas", None),
    ("F", "Finalizadas", "F"),
    ("A", "Abertas", "A"),
]

KM_BUCKETS = [
    ("ate_200", "Até 200 km", 1, 200),
    ("de_201_800", "201 a 800 km", 201, 800),
    ("de_801_1500", "801 a 1.500 km", 801, 1500),
    ("de_1501_3000", "1.501 a 3.000 km", 1501, 3000),
    ("acima_3000", "Mais de 3.000 km", 3001, None),
]

DURATION_BUCKETS = [
    ("ate_1d", "Até 1 dia", 0, 1),
    ("de_1_3d", "1 a 3 dias", 1, 3),
    ("de_3_7d", "3 a 7 dias", 3, 7),
    ("de_7_15d", "7 a 15 dias", 7, 15),
    ("acima_15d", "Mais de 15 dias", 15, None),
]

# Aging da viagem aberta: conta da chegada prevista, que é o único prazo que o
# cadastro guarda. Viagem aberta cuja previsão já venceu é problema de hoje.
OPEN_AGING_BUCKETS = [
    ("no_prazo", "Ainda no prazo", None, 0),
    ("ate_7", "Até 7 dias vencida", 1, 7),
    ("de_8_a_30", "8 a 30 dias vencida", 8, 30),
    ("acima_30", "Mais de 30 dias vencida", 31, None),
    ("sem_previsao", "Sem chegada prevista", None, None),
]

# Peso máximo aceito numa perna do roteiro. A base tem uma perna com 720.398 kg
# para 18 paletes — a ~537 kg por palete que a própria base pratica, a carga real
# era ~10 t, então o número está errado por um fator de setenta. Nenhum conjunto
# rodoviário no país carrega 720 t.
CARGO_MAX_KG = 50000

# Faixas de carga por viagem, em quilos.
CARGO_BUCKETS = [
    ("ate_5t", "Até 5 t", 1, 5000),
    ("de_5_15t", "5 a 15 t", 5001, 15000),
    ("de_15_30t", "15 a 30 t", 15001, 30000),
    ("acima_30t", "Mais de 30 t", 30001, None),
]

# Rotas 6, 7 e 11 são de aproveitamento, como no relatório de origem: carga que
# volta ou completa o caminhão, não a rota que motivou a viagem.
OPPORTUNISTIC_ROUTES = {"6", "7", "11"}

# Faixas de idade do veículo, como no relatório de origem. "Não determinado"
# recebe também o ano impossível: o cadastro tem um veículo com ANOVEI 2029, e
# a conta original o classificava como "Menos de 3 anos" — um erro de digitação
# entrando na frota nova em silêncio.
VEHICLE_AGE_BUCKETS = [
    ("ate_3", "Menos de 3 anos", 0, 3),
    ("de_3_5", "Entre 3 e 5 anos", 4, 5),
    ("de_5_10", "Entre 5 e 10 anos", 6, 10),
    ("de_10_15", "Entre 10 e 15 anos", 11, 15),
    ("acima_15", "Mais de 15 anos", 16, None),
    ("indefinido", "Não determinado", None, None),
]

WEEKDAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
HOUR_BLOCKS = [
    ("madrugada", "0-6h", 0, 5),
    ("manha_cedo", "6-9h", 6, 8),
    ("manha", "9-12h", 9, 11),
    ("tarde", "12-15h", 12, 14),
    ("tarde_fim", "15-18h", 15, 17),
    ("noite", "18-24h", 18, 23),
]


# ----------------------------------------------------------------- acesso --

def _query(sql, params=None):
    connection = _erp_connection()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(sql, params or {})
        columns = [column[0].lower() for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def _query_many(statements, params):
    """Roda várias agregações em uma conexão só."""
    connection = _erp_connection()
    results = {}
    try:
        for name, sql in statements.items():
            cursor = connection.cursor()
            try:
                cursor.execute(sql, params.get(name, {}))
                columns = [column[0].lower() for column in cursor.description]
                results[name] = [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                cursor.close()
    finally:
        connection.close()
    return results


# ---------------------------------------------------------------- helpers --

def _number(value):
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _format_int(value):
    return f"{_int(value):,}".replace(",", ".")


def _format_decimal(value, places=1):
    text = f"{_float(value):,.{places}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _format_money(value):
    return f"R$ {_format_decimal(value, 2)}"


def _format_km(value):
    return f"{_format_int(value)} km"


def _format_liters(value):
    return f"{_format_decimal(value, 0)} L"


def _format_days(value):
    """Dias fracionários em texto curto: '3d 4h', '18h 30min', '45min'."""
    total_minutes = int(round(_float(value) * 24 * 60))
    if total_minutes <= 0:
        return "-"
    hours, minutes = divmod(total_minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}min" if minutes else f"{hours}h"
    return f"{minutes}min"


def _display_date(value):
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return "-"


def _display_datetime(value):
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return "-"


def _share(part, total):
    return round(float(_number(part) / _number(total) * 100), 1) if _number(total) else 0.0


def _ratio(part, total):
    return round(_float(part) / _float(total), 2) if _float(total) else 0.0


def format_cnpj(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if not digits:
        return ""
    digits = digits.zfill(14)
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


# ------------------------------------------------------------- SQL da base --

# Dois CTEs porque o Oracle não deixa reaproveitar apelido de coluna no mesmo
# SELECT: o primeiro normaliza o cadastro, o segundo classifica o que sobrou.
_BASE_SQL = f"""
WITH VIAGENS AS (
    SELECT
        A.USU_CODEMP AS EMPRESA,
        A.USU_CODVIA AS VIAGEM,
        A.USU_CODTRA AS FROTA,
        A.USU_CODMOT AS MOTORISTA,
        A.USU_CODTRA || '-' || A.USU_CODMOT AS CHV_MOTORISTA,
        A.USU_CODFOR AS FORNECEDOR,
        A.USU_NUMTIT AS TITULO,
        NVL(A.USU_VLRADI, 0) AS ADIANTAMENTO,
        REPLACE(REPLACE(UPPER(A.USU_PLAVEI), '-', ''), ' ', '') AS PLACA,
        A.USU_DATGER AS DATA_GERACAO,
        TO_CHAR(A.USU_DATGER, 'YYYY-MM') AS COMPETENCIA,
        TO_CHAR(A.USU_DATGER, 'YYYY-MM-DD') AS DIA,
        A.USU_NKMSAI AS KM_SAIDA,
        A.USU_NKMCHE AS KM_CHEGADA,
        A.USU_NKMCHE - A.USU_NKMSAI AS KM_RODADO,
        NVL(A.USU_QTDLTR, 0) AS LITROS,
        A.USU_SITVIA AS COD_SITUACAO,
        CASE WHEN A.USU_SITVIA = 'F' THEN 'Finalizada' ELSE 'Aberta' END AS SITUACAO,
        CASE WHEN A.USU_DATSAI > {DATE_FLOOR_SQL}
             THEN A.USU_DATSAI + NVL(A.USU_HORSAI, 0) / 1440 END AS DT_SAIDA,
        CASE WHEN A.USU_DATCHE > {DATE_FLOOR_SQL}
             THEN A.USU_DATCHE + NVL(A.USU_HORCHE, 0) / 1440 END AS DT_CHEGADA,
        CASE WHEN A.USU_DTPSAI > {DATE_FLOOR_SQL} THEN A.USU_DTPSAI END AS DT_PREV_SAIDA,
        CASE WHEN A.USU_DTPCHE > {DATE_FLOOR_SQL} THEN A.USU_DTPCHE END AS DT_PREV_CHEGADA
    FROM USU_TCADVIA A
    WHERE {{scope}}
),
BASE AS (
    SELECT
        V.*,
        CASE WHEN V.KM_RODADO BETWEEN {KM_MIN} AND {KM_MAX} THEN V.KM_RODADO END AS KM_VALIDO,
        CASE WHEN V.KM_RODADO BETWEEN {KM_MIN} AND {KM_MAX} AND V.LITROS > 0
             THEN V.LITROS END AS LITROS_VALIDOS,
        CASE WHEN V.KM_RODADO BETWEEN {KM_MIN} AND {KM_MAX} AND V.LITROS > 0
              AND V.KM_RODADO / V.LITROS BETWEEN {CONSUMPTION_MIN} AND {CONSUMPTION_MAX}
             THEN V.KM_RODADO END AS KM_CONSUMO,
        CASE WHEN V.KM_RODADO BETWEEN {KM_MIN} AND {KM_MAX} AND V.LITROS > 0
              AND V.KM_RODADO / V.LITROS BETWEEN {CONSUMPTION_MIN} AND {CONSUMPTION_MAX}
             THEN V.LITROS END AS LITROS_CONSUMO,
        CASE WHEN V.DT_SAIDA IS NOT NULL AND V.DT_CHEGADA IS NOT NULL
              AND V.DT_CHEGADA - V.DT_SAIDA BETWEEN 0 AND {DURATION_MAX_DAYS}
             THEN V.DT_CHEGADA - V.DT_SAIDA END AS DURACAO_DIAS,
        CASE
            WHEN TRIM(V.PLACA) IS NULL THEN 'Sem placa informada'
            WHEN V.KM_RODADO < 0 THEN 'KM de chegada menor que o de saída'
            WHEN V.KM_RODADO = 0 AND V.COD_SITUACAO = 'F' THEN 'KM de chegada igual ao de saída'
            WHEN V.KM_RODADO > {KM_MAX} THEN 'KM rodado acima de {KM_MAX}'
            WHEN V.DT_SAIDA IS NOT NULL AND V.DT_CHEGADA IS NOT NULL
                 AND V.DT_CHEGADA < V.DT_SAIDA THEN 'Chegada anterior à saída'
            WHEN V.DT_SAIDA IS NOT NULL AND V.DT_CHEGADA IS NOT NULL
                 AND V.DT_CHEGADA - V.DT_SAIDA > {DURATION_MAX_DAYS} THEN 'Duração acima de {DURATION_MAX_DAYS} dias'
            WHEN V.COD_SITUACAO = 'F' AND V.DT_SAIDA IS NULL THEN 'Finalizada sem data de saída'
            WHEN V.COD_SITUACAO = 'F' AND V.DT_CHEGADA IS NULL THEN 'Finalizada sem data de chegada'
            ELSE NULL
        END AS ERRO_CADASTRO
    FROM VIAGENS V
)
"""


def _base(scope_sql):
    return _BASE_SQL.format(scope=scope_sql)


# O roteiro tem várias linhas por viagem (até dez) e às vezes mais de uma rota.
# Juntar direto multiplicaria a viagem, então ele é agregado antes: uma linha por
# viagem e rota em ROTEIRO, uma linha por viagem em ROTEIRO_VIAGEM. É essa
# segunda que pode encostar em BASE sem inflar contagem nenhuma.
_ROUTES_CTE = f"""
, ROTEIRO AS (
    SELECT
        T.USU_CODEMP AS EMPRESA,
        T.USU_CODVIA AS VIAGEM,
        LTRIM(TRIM(T.USU_ROTCID), '0') AS ROTA,
        SUM(CASE WHEN T.USU_PESCAR BETWEEN 0 AND {CARGO_MAX_KG}
                 THEN T.USU_PESCAR ELSE 0 END) AS PESO,
        SUM(CASE WHEN T.USU_PESCAR > {CARGO_MAX_KG} THEN 1 ELSE 0 END) AS PERNAS_INVALIDAS,
        SUM(NVL(T.USU_QTDPLT, 0)) AS PALLETS,
        COUNT(*) AS PERNAS
    FROM USU_TROTVIA T
    WHERE T.USU_ROTCID <> ' '
      AND (T.USU_PESCAR <> 0 OR T.USU_QTDPLT <> 0)
    GROUP BY T.USU_CODEMP, T.USU_CODVIA, LTRIM(TRIM(T.USU_ROTCID), '0')
),
ROTEIRO_VIAGEM AS (
    SELECT
        EMPRESA, VIAGEM,
        SUM(PESO) AS PESO,
        SUM(PALLETS) AS PALLETS,
        SUM(PERNAS) AS PERNAS,
        SUM(PERNAS_INVALIDAS) AS PERNAS_INVALIDAS,
        COUNT(DISTINCT ROTA) AS ROTAS
    FROM ROTEIRO
    GROUP BY EMPRESA, VIAGEM
)
"""


def _base_with_routes(scope_sql):
    return _base(scope_sql) + _ROUTES_CTE


# ----------------------------------------------------------- cadastros de apoio

# O nome do motorista mora em E073MOT, com a mesma chave frota + código que a
# viagem usa. São 116 linhas, chave única: cabe inteiro em memória, e enriquecer
# em Python evita pendurar mais um JOIN nas agregações.
DRIVERS_SQL = """
SELECT
  A.CODTRA AS FROTA,
  A.CODMTR AS CODIGO,
  A.CODTRA || '-' || A.CODMTR AS CHAVE,
  A.NOMMOT AS NOME
FROM E073MOT A
"""

# O cadastro de veículo repete a mesma placa: uma vez por transportadora e, às
# vezes, duas vezes na mesma (uma grafia com hífen, outra sem). Juntar pela placa
# crua inflava 4.596 viagens para 8.509 linhas. `ROW_NUMBER` deixa uma linha por
# placa normalizada, preferindo o registro ativo e, no empate, o mais recente.
VEHICLES_SQL = """
SELECT PLACA, FROTA, MODELO, VEICULO, ANO
FROM (
  SELECT
    REPLACE(REPLACE(UPPER(A.PLAVEI), '-', ''), ' ', '') AS PLACA,
    A.CODTRA AS FROTA,
    A.CODMOD AS MODELO,
    B.DESMOD AS VEICULO,
    A.ANOVEI AS ANO,
    ROW_NUMBER() OVER (
      PARTITION BY REPLACE(REPLACE(UPPER(A.PLAVEI), '-', ''), ' ', '')
      ORDER BY CASE WHEN A.SITVEI = 'A' THEN 0 ELSE 1 END, A.DATGER DESC NULLS LAST
    ) AS RN
  FROM E073VEI A
  LEFT JOIN E073MOD B ON A.CODMOD = B.CODMOD
)
WHERE RN = 1
"""

# Custo contábil dos centros de custo da frota, por competência e por CNPJ.
#
# A consulta de origem montava o texto do lançamento com três `REGEXP_REPLACE`
# aninhados só para agrupar por ele; como o texto é função de NUMLCT, tirá-lo do
# GROUP BY não muda agrupamento nenhum. Conferido contra a consulta original nas
# 89 competências: mesmo valor até o centavo (R$ 27.699.376,76), em 0,5s no
# lugar de 1,8s. O filtro `CODHPD <> 356` continua, e continua sendo um JOIN
# obrigatório — na consulta original o `WHERE` sobre a tabela do LEFT JOIN já
# descartava os lançamentos sem histórico.
COST_SQL = """
SELECT
  EMP_TRANSP,
  COMPT AS COMPETENCIA,
  SUM(VALOR) AS VALOR
FROM (
  SELECT
    CASE WHEN A.CODEMP || '-' || A.FILRAT = '1-4' THEN '1-1004' ELSE '1-1001' END AS EMP_TRANSP,
    TO_CHAR(A.DATLCT, 'MMYYYY') AS COMPT,
    SUM(CASE A.DEBCRE WHEN 'D' THEN A.VLRRAT WHEN 'C' THEN -A.VLRRAT ELSE 0 END) AS VALOR
  FROM E640RAT A
  JOIN E043PCM B ON A.CTARED = B.CTARED
  JOIN E640LCT L ON L.CODEMP = A.CODEMP AND L.CODFIL = A.FILRAT AND L.NUMLCT = A.NUMLCT
  JOIN E046HPD H ON H.CODHPD = L.CODHPD
  WHERE A.SITRAT = '2'
    AND B.CODMPC = 10
    AND H.CODHPD <> 356
    AND A.DATLCT > DATE '2023-01-01'
    AND A.DATLCT <= TRUNC(SYSDATE)
    AND A.CODCCU IN (1112205, 1212205, 1412205)
    AND A.CTARED NOT IN (
      '5325','5301','4115','4125','5185','5165','5170','5150','5095','5140','5175',
      '5180','5155','5160','5328','4210','5120','5245','2215','5121','5246','5235')
    {scope}
  GROUP BY A.CODEMP, A.FILRAT, A.CTARED, A.CODCCU, A.NUMLCT, A.DATLCT
)
GROUP BY EMP_TRANSP, COMPT
ORDER BY COMPT
"""


# O cadastro de rota guarda o número dentro do nome ("ROTA 07"): `SUBSTR` na
# posição 6 é o que o relatório de origem faz, e `LTRIM` tira o zero à esquerda.
# O roteiro grava o mesmo número, mas às vezes com zero ('06'), então os dois
# lados passam pela mesma normalização — sem isso a rota 6 aparecia partida em
# duas, uma delas "sem cadastro".
ROUTES_SQL = """
SELECT
  LTRIM(TRIM(SUBSTR(A.DESROE, 6, 2)), '0') AS ROTA,
  A.DESROE AS NOME
FROM E062ROE A
"""


def load_routes_catalog():
    """Nome e tipo de cada rota, pela chave numérica."""
    catalog = {}
    for row in _query(ROUTES_SQL):
        key = (row.get("rota") or "").strip()
        if not key:
            continue
        catalog[key] = {
            "key": key,
            "name": (row.get("nome") or "").strip() or f"Rota {key}",
            "opportunistic": key in OPPORTUNISTIC_ROUTES,
            "type": "Rotas de Aproveitamento" if key in OPPORTUNISTIC_ROUTES else "Rotas Principais",
        }
    return catalog


def load_drivers_catalog():
    """Nome do motorista por chave frota-código."""
    return {
        str(row["chave"]): (row["nome"] or "").strip()
        for row in _query(DRIVERS_SQL)
        if row.get("chave")
    }


def _vehicle_age(year, today=None):
    """Idade do veículo e a faixa em que ela cai.

    `ANOVEI` é VARCHAR2: um valor não numérico não pode derrubar o painel
    inteiro, e um ano no futuro é erro de cadastro, não frota zero-quilômetro.
    """
    reference = (today or date.today()).year
    try:
        model_year = int(str(year).strip())
    except (TypeError, ValueError):
        return None, "indefinido"
    if model_year < 1900 or model_year > reference:
        return None, "indefinido"
    age = reference - model_year
    for key, _label, start, end in VEHICLE_AGE_BUCKETS:
        if start is None:
            continue
        if age >= start and (end is None or age <= end):
            return age, key
    return age, "indefinido"


def load_vehicles_catalog(today=None):
    """Modelo e idade por placa normalizada."""
    catalog = {}
    for row in _query(VEHICLES_SQL):
        plate = (row.get("placa") or "").strip()
        if not plate:
            continue
        age, bucket = _vehicle_age(row.get("ano"), today)
        catalog[plate] = {
            "fleet": _int(row.get("frota")),
            "model_code": _int(row.get("modelo")),
            "model": (row.get("veiculo") or "").strip() or "Modelo não cadastrado",
            "year": (str(row.get("ano") or "").strip() or "-"),
            "age": age,
            "age_bucket": bucket,
            "age_label": dict((key, label) for key, label, _s, _e in VEHICLE_AGE_BUCKETS)[bucket],
        }
    return catalog


def _cost_scope_sql(period, carrier):
    """Filtro do custo contábil, no mesmo recorte do painel.

    A data do custo é a do lançamento (`DATLCT`), não a da viagem: são medidas
    diferentes com a mesma competência, e é assim que elas se encontram.
    """
    clauses = []
    params = {}
    if period.get("start") and period.get("end"):
        clauses.append("AND A.DATLCT >= :cost_start AND A.DATLCT < :cost_end")
        params["cost_start"] = period["start"]
        params["cost_end"] = period["end"]
    elif period.get("months"):
        clauses.append("AND A.DATLCT >= ADD_MONTHS(TRUNC(SYSDATE), -:cost_months)")
        params["cost_months"] = period["months"]
    if carrier["code"] is not None:
        # O CNPJ da frota vem da filial do rateio: 1-4 é a 1004, o resto é 1001.
        operator = "=" if carrier["code"] == 1004 else "<>"
        clauses.append(f"AND A.CODEMP || '-' || A.FILRAT {operator} '1-4'")
    return " ".join(clauses), params


def load_fleet_cost(period, carrier):
    """Custo contábil por competência e frota, já no recorte da tela."""
    scope, params = _cost_scope_sql(period, carrier)
    return _query(COST_SQL.format(scope=scope), params)


# ------------------------------------------------------------- filtros ----

def resolve_situation(raw_value):
    value = (raw_value or "all").strip()
    for key, label, code in SITUATION_CHOICES:
        if key == value:
            return {"key": key, "label": label, "code": code}
    return {"key": "all", "label": "Todas", "code": None}


CARRIERS_SQL = """
SELECT
  A.USU_CODTRA AS CODIGO,
  COUNT(*) AS TOTAL,
  MAX(F.NOMFOR) AS NOME,
  MAX(F.CGCCPF) AS CNPJ
FROM USU_TCADVIA A
LEFT JOIN E095FOR F ON F.CODFOR = A.USU_CODTRA
GROUP BY A.USU_CODTRA
ORDER BY COUNT(*) DESC
"""


def list_carriers():
    """Frotas/transportadoras com viagens, para montar o filtro.

    O código vem de `USU_CODTRA` e bate com `E095FOR`. Hoje são dois CNPJs da
    própria Maxiplast, então o nome se repete: é o CNPJ que diferencia um do
    outro na tela.
    """
    rows = _query(CARRIERS_SQL)
    names = [(row.get("nome") or "").strip() for row in rows]
    ambiguous = len(names) != len(set(names))
    carriers = []
    for row in rows:
        code = _int(row.get("codigo"))
        name = (row.get("nome") or "").strip() or f"Frota {code}"
        cnpj = format_cnpj(row.get("cnpj"))
        carriers.append(
            {
                "key": str(code),
                "code": code,
                "name": name,
                "cnpj": cnpj,
                # Nome repetido não identifica nada: nesse caso o rótulo curto do
                # filtro passa a ser o código, e o CNPJ vai no detalhe.
                "label": f"Frota {code}" if ambiguous else name,
                "total": _int(row.get("total")),
            }
        )
    return carriers


def resolve_carrier(raw_value, available=None):
    value = (raw_value or "").strip()
    if not value or value == "all":
        return {"key": "all", "label": "Todas as frotas", "code": None, "cnpj": ""}
    catalog = {item["key"]: item for item in (available or [])}
    item = catalog.get(value)
    if item is None:
        return {"key": "all", "label": "Todas as frotas", "code": None, "cnpj": ""}
    return {"key": item["key"], "label": item["label"], "code": item["code"], "cnpj": item["cnpj"]}


def _scope_sql(period, carrier, situation, ignore_period=False, ignore_situation=False):
    """Cláusulas de filtro compartilhadas por todas as agregações."""
    clauses = ["1 = 1"]
    params = {}
    if not ignore_period:
        if period.get("start") and period.get("end"):
            # Mês fechado: intervalo com fim exclusivo, vindo pronto do Python —
            # a mesma regra que o BI do TI aplica no MySQL.
            clauses.append("A.USU_DATGER >= :period_start AND A.USU_DATGER < :period_end")
            params["period_start"] = period["start"]
            params["period_end"] = period["end"]
        elif period.get("months"):
            clauses.append("A.USU_DATGER >= ADD_MONTHS(TRUNC(SYSDATE), -:months)")
            params["months"] = period["months"]
    if carrier["code"] is not None:
        clauses.append("A.USU_CODTRA = :carrier")
        params["carrier"] = carrier["code"]
    if situation["code"] is not None and not ignore_situation:
        clauses.append("A.USU_SITVIA = :situation")
        params["situation"] = situation["code"]
    return " AND ".join(clauses), params


# ---------------------------------------------------------- agregações ----

def load_travel_dashboard(
    period_key="12",
    carrier_key="all",
    situation_key="all",
    carriers_available=None,
    drivers_catalog=None,
    vehicles_catalog=None,
    routes_catalog=None,
    cost_rows=None,
):
    period = resolve_period(period_key)
    available = carriers_available if carriers_available is not None else list_carriers()
    carrier = resolve_carrier(carrier_key, available)
    situation = resolve_situation(situation_key)

    scope, params = _scope_sql(period, carrier, situation)
    # A fila de viagens abertas ignora período e situação de propósito: uma
    # viagem que saiu há oito meses e não voltou continua aberta hoje, e some
    # da tela se o recorte de data ou de situação for aplicado nela.
    open_scope, open_params = _scope_sql(
        period, carrier, situation, ignore_period=True, ignore_situation=True
    )
    open_scope = f"{open_scope} AND A.USU_SITVIA <> 'F'"

    base = _base(scope)
    open_base = _base(open_scope)
    cargo_base = _base_with_routes(scope)

    # Num recorte de um mês a série vira diária: uma barra só por competência
    # não diz nada que o KPI logo acima já não diga.
    series_column = "DIA" if period["granularity"] == "day" else "COMPETENCIA"

    km_bucket_cases = " ".join(
        f"WHEN KM_VALIDO {'>=' if end is None else 'BETWEEN'} {start}"
        f"{'' if end is None else f' AND {end}'} THEN '{key}'"
        for key, _label, start, end in KM_BUCKETS
    )
    duration_bucket_cases = " ".join(
        f"WHEN DURACAO_DIAS {'>' if end is None else '>='} {start}"
        f"{'' if end is None else f' AND DURACAO_DIAS < {end}'} THEN '{key}'"
        for key, _label, start, end in DURATION_BUCKETS
    )

    cargo_bucket_cases = " ".join(
        f"WHEN V.PESO {'>=' if end is None else 'BETWEEN'} {start}"
        f"{'' if end is None else f' AND {end}'} THEN '{key}'"
        for key, _label, start, end in CARGO_BUCKETS
    )

    statements = {
        "totals": f"""
            {base}
            SELECT
              COUNT(*) AS VIAGENS,
              SUM(CASE WHEN COD_SITUACAO = 'F' THEN 1 ELSE 0 END) AS FINALIZADAS,
              SUM(CASE WHEN COD_SITUACAO <> 'F' THEN 1 ELSE 0 END) AS ABERTAS,
              COUNT(DISTINCT PLACA) AS PLACAS,
              COUNT(DISTINCT CHV_MOTORISTA) AS MOTORISTAS,
              COUNT(DISTINCT FROTA) AS FROTAS,
              SUM(KM_VALIDO) AS KM_TOTAL,
              AVG(KM_VALIDO) AS KM_MEDIO,
              COUNT(KM_VALIDO) AS VIAGENS_COM_KM,
              SUM(LITROS_VALIDOS) AS LITROS_TOTAL,
              COUNT(LITROS_VALIDOS) AS VIAGENS_COM_LITROS,
              SUM(KM_CONSUMO) AS KM_CONSUMO,
              SUM(LITROS_CONSUMO) AS LITROS_CONSUMO,
              COUNT(KM_CONSUMO) AS VIAGENS_CONSUMO,
              SUM(ADIANTAMENTO) AS ADIANTAMENTO_TOTAL,
              AVG(CASE WHEN ADIANTAMENTO > 0 THEN ADIANTAMENTO END) AS ADIANTAMENTO_MEDIO,
              COUNT(CASE WHEN ADIANTAMENTO > 0 THEN 1 END) AS VIAGENS_COM_ADIANTAMENTO,
              AVG(DURACAO_DIAS) AS DURACAO_MEDIA,
              COUNT(DURACAO_DIAS) AS VIAGENS_COM_DURACAO,
              COUNT(ERRO_CADASTRO) AS COM_ERRO
            FROM BASE
        """,
        "monthly": f"""
            {base}
            SELECT
              {series_column} AS COMPETENCIA,
              COUNT(*) AS VIAGENS,
              SUM(CASE WHEN COD_SITUACAO = 'F' THEN 1 ELSE 0 END) AS FINALIZADAS,
              SUM(KM_VALIDO) AS KM,
              SUM(LITROS_VALIDOS) AS LITROS,
              SUM(ADIANTAMENTO) AS ADIANTAMENTO,
              COUNT(ERRO_CADASTRO) AS COM_ERRO
            FROM BASE
            GROUP BY {series_column}
            ORDER BY {series_column}
        """,
        "validation": f"""
            {base}
            SELECT NVL(ERRO_CADASTRO, 'Cadastro correto') AS ROTULO, COUNT(*) AS TOTAL
            FROM BASE
            GROUP BY NVL(ERRO_CADASTRO, 'Cadastro correto')
            ORDER BY COUNT(*) DESC
        """,
        "carriers": f"""
            {base}
            SELECT
              FROTA AS CODIGO,
              COUNT(*) AS VIAGENS,
              SUM(KM_VALIDO) AS KM,
              SUM(KM_CONSUMO) AS KM_CONSUMO,
              SUM(LITROS_CONSUMO) AS LITROS_CONSUMO,
              SUM(ADIANTAMENTO) AS ADIANTAMENTO,
              AVG(DURACAO_DIAS) AS DURACAO,
              COUNT(ERRO_CADASTRO) AS COM_ERRO,
              COUNT(DISTINCT PLACA) AS PLACAS,
              COUNT(DISTINCT CHV_MOTORISTA) AS MOTORISTAS
            FROM BASE
            GROUP BY FROTA
            ORDER BY COUNT(*) DESC
        """,
        "drivers": f"""
            {base}
            SELECT
              CHV_MOTORISTA AS CHAVE,
              FROTA,
              MOTORISTA,
              COUNT(*) AS VIAGENS,
              SUM(KM_VALIDO) AS KM,
              SUM(KM_CONSUMO) AS KM_CONSUMO,
              SUM(LITROS_CONSUMO) AS LITROS_CONSUMO,
              SUM(ADIANTAMENTO) AS ADIANTAMENTO,
              AVG(DURACAO_DIAS) AS DURACAO,
              COUNT(ERRO_CADASTRO) AS COM_ERRO
            FROM BASE
            GROUP BY CHV_MOTORISTA, FROTA, MOTORISTA
            ORDER BY COUNT(*) DESC
            FETCH FIRST 20 ROWS ONLY
        """,
        "vehicles": f"""
            {base}
            SELECT
              PLACA,
              MAX(FROTA) AS FROTA,
              COUNT(*) AS VIAGENS,
              SUM(KM_VALIDO) AS KM,
              SUM(KM_CONSUMO) AS KM_CONSUMO,
              SUM(LITROS_CONSUMO) AS LITROS_CONSUMO,
              AVG(KM_VALIDO) AS KM_MEDIO,
              COUNT(ERRO_CADASTRO) AS COM_ERRO,
              MAX(KM_CHEGADA) AS HODOMETRO
            FROM BASE
            WHERE PLACA IS NOT NULL
            GROUP BY PLACA
            ORDER BY SUM(KM_VALIDO) DESC NULLS LAST
        """,
        "companies": f"""
            {base}
            SELECT EMPRESA AS CODIGO, COUNT(*) AS TOTAL, SUM(KM_VALIDO) AS KM
            FROM BASE GROUP BY EMPRESA ORDER BY COUNT(*) DESC
        """,
        "situations": f"""
            {base}
            SELECT SITUACAO AS ROTULO, COUNT(*) AS TOTAL FROM BASE
            GROUP BY SITUACAO ORDER BY COUNT(*) DESC
        """,
        "km_buckets": f"""
            {base}
            SELECT
              CASE {km_bucket_cases} END AS FAIXA,
              COUNT(*) AS TOTAL,
              AVG(KM_VALIDO) AS MEDIA,
              SUM(KM_VALIDO) AS KM
            FROM BASE WHERE KM_VALIDO IS NOT NULL
            GROUP BY CASE {km_bucket_cases} END
        """,
        "duration_buckets": f"""
            {base}
            SELECT
              CASE {duration_bucket_cases} END AS FAIXA,
              COUNT(*) AS TOTAL,
              AVG(DURACAO_DIAS) AS MEDIA,
              AVG(KM_VALIDO) AS KM_MEDIO
            FROM BASE WHERE DURACAO_DIAS IS NOT NULL
            GROUP BY CASE {duration_bucket_cases} END
        """,
        "forecast": f"""
            {base}
            SELECT
              COUNT(*) AS COM_PREVISAO,
              SUM(CASE WHEN TRUNC(DT_CHEGADA) > DT_PREV_CHEGADA THEN 1 ELSE 0 END) AS ATRASADAS,
              SUM(CASE WHEN TRUNC(DT_CHEGADA) <= DT_PREV_CHEGADA THEN 1 ELSE 0 END) AS NO_PRAZO,
              SUM(CASE WHEN TRUNC(DT_CHEGADA) = DT_PREV_CHEGADA THEN 1 ELSE 0 END) AS IGUAIS,
              AVG(CASE WHEN TRUNC(DT_CHEGADA) > DT_PREV_CHEGADA
                       THEN TRUNC(DT_CHEGADA) - DT_PREV_CHEGADA END) AS ATRASO_MEDIO
            FROM BASE
            WHERE DT_CHEGADA IS NOT NULL AND DT_PREV_CHEGADA IS NOT NULL
        """,
        "cargo_totals": f"""
            {cargo_base}
            SELECT
              COUNT(*) AS VIAGENS_COM_CARGA,
              SUM(V.PESO) AS PESO,
              SUM(V.PALLETS) AS PALLETS,
              SUM(V.PERNAS) AS PERNAS,
              SUM(V.PERNAS_INVALIDAS) AS PERNAS_INVALIDAS,
              SUM(CASE WHEN V.ROTAS > 1 THEN 1 ELSE 0 END) AS VIAGENS_MULTIROTA,
              SUM(CASE WHEN B.KM_VALIDO IS NOT NULL AND V.PESO > 0 THEN B.KM_VALIDO END) AS KM_COM_CARGA,
              SUM(CASE WHEN B.KM_VALIDO IS NOT NULL AND V.PESO > 0 THEN V.PESO END) AS PESO_COM_KM
            FROM BASE B
            JOIN ROTEIRO_VIAGEM V ON V.EMPRESA = B.EMPRESA AND V.VIAGEM = B.VIAGEM
        """,
        "cargo_bands": f"""
            {cargo_base}
            SELECT
              CASE {cargo_bucket_cases} END AS FAIXA,
              COUNT(*) AS TOTAL,
              AVG(V.PESO) AS MEDIA,
              AVG(V.PALLETS) AS PALLETS,
              AVG(B.KM_VALIDO) AS KM_MEDIO
            FROM BASE B
            JOIN ROTEIRO_VIAGEM V ON V.EMPRESA = B.EMPRESA AND V.VIAGEM = B.VIAGEM
            WHERE V.PESO > 0
            GROUP BY CASE {cargo_bucket_cases} END
        """,
        "cargo_monthly": f"""
            {cargo_base}
            SELECT
              B.COMPETENCIA,
              COUNT(*) AS VIAGENS,
              SUM(V.PESO) AS PESO,
              SUM(V.PALLETS) AS PALLETS
            FROM BASE B
            JOIN ROTEIRO_VIAGEM V ON V.EMPRESA = B.EMPRESA AND V.VIAGEM = B.VIAGEM
            GROUP BY B.COMPETENCIA ORDER BY B.COMPETENCIA
        """,
        "routes": f"""
            {cargo_base}
            SELECT
              R.ROTA,
              COUNT(DISTINCT B.EMPRESA || '-' || B.VIAGEM) AS VIAGENS,
              SUM(R.PESO) AS PESO,
              SUM(R.PALLETS) AS PALLETS,
              SUM(R.PERNAS) AS PERNAS,
              COUNT(DISTINCT B.PLACA) AS PLACAS,
              COUNT(DISTINCT B.CHV_MOTORISTA) AS MOTORISTAS
            FROM BASE B
            JOIN ROTEIRO R ON R.EMPRESA = B.EMPRESA AND R.VIAGEM = B.VIAGEM
            GROUP BY R.ROTA
            ORDER BY SUM(R.PESO) DESC NULLS LAST
        """,
        "open_totals": f"""
            {open_base}
            SELECT
              COUNT(*) AS ABERTAS,
              SUM(CASE WHEN DT_PREV_CHEGADA IS NOT NULL
                        AND TRUNC(SYSDATE) - DT_PREV_CHEGADA <= 0 THEN 1 ELSE 0 END) AS NO_PRAZO,
              SUM(CASE WHEN DT_PREV_CHEGADA IS NOT NULL
                        AND TRUNC(SYSDATE) - DT_PREV_CHEGADA BETWEEN 1 AND 7 THEN 1 ELSE 0 END) AS ATE_7,
              SUM(CASE WHEN DT_PREV_CHEGADA IS NOT NULL
                        AND TRUNC(SYSDATE) - DT_PREV_CHEGADA BETWEEN 8 AND 30 THEN 1 ELSE 0 END) AS DE_8_A_30,
              SUM(CASE WHEN DT_PREV_CHEGADA IS NOT NULL
                        AND TRUNC(SYSDATE) - DT_PREV_CHEGADA > 30 THEN 1 ELSE 0 END) AS ACIMA_30,
              SUM(CASE WHEN DT_PREV_CHEGADA IS NULL THEN 1 ELSE 0 END) AS SEM_PREVISAO,
              SUM(ADIANTAMENTO) AS ADIANTAMENTO
            FROM BASE
        """,
        "open_trips": f"""
            {open_base}
            SELECT
              EMPRESA, VIAGEM, FROTA, MOTORISTA, CHV_MOTORISTA, PLACA, TITULO,
              DATA_GERACAO, DT_SAIDA, DT_PREV_SAIDA, DT_PREV_CHEGADA, ADIANTAMENTO,
              TRUNC(SYSDATE) - NVL(DT_PREV_CHEGADA, TRUNC(DATA_GERACAO)) AS DIAS_VENCIDA,
              TRUNC(SYSDATE) - TRUNC(DATA_GERACAO) AS DIAS_ABERTA
            FROM BASE
            ORDER BY NVL(DT_PREV_CHEGADA, DATA_GERACAO) ASC
            FETCH FIRST 20 ROWS ONLY
        """,
    }

    params_by_name = {name: dict(params) for name in statements}
    for name in ("open_totals", "open_trips"):
        params_by_name[name] = dict(open_params)

    data = _query_many(statements, params_by_name)
    drivers = load_drivers_catalog() if drivers_catalog is None else drivers_catalog
    vehicles = load_vehicles_catalog() if vehicles_catalog is None else vehicles_catalog
    routes = load_routes_catalog() if routes_catalog is None else routes_catalog
    cost = load_fleet_cost(period, carrier) if cost_rows is None else cost_rows
    return _build_travel_dashboard(
        data, period, carrier, situation, available,
        drivers_catalog=drivers, vehicles_catalog=vehicles,
        routes_catalog=routes, cost_rows=cost,
    )


def _bucket_rows(rows, buckets, extra=None):
    by_key = {str(row.get("faixa")): row for row in rows if row.get("faixa")}
    total = sum(_int(row.get("total")) for row in rows)
    items = []
    for key, label, _start, _end in buckets:
        row = by_key.get(key) or {}
        count = _int(row.get("total"))
        item = {
            "key": key,
            "label": label,
            "total": count,
            "total_display": _format_int(count),
            "share_pct": _share(count, total),
        }
        if extra:
            item.update(extra(row))
        items.append(item)
    return {"total": total, "items": items}


def _build_travel_dashboard(
    data, period, carrier, situation, carriers_available,
    drivers_catalog=None, vehicles_catalog=None, routes_catalog=None, cost_rows=None,
):
    drivers_catalog = drivers_catalog or {}
    vehicles_catalog = vehicles_catalog or {}
    routes_catalog = routes_catalog or {}
    totals = (data["totals"] or [{}])[0]

    trips = _int(totals.get("viagens"))
    finished = _int(totals.get("finalizadas"))
    open_count = _int(totals.get("abertas"))
    km_total = _float(totals.get("km_total"))
    liters_total = _float(totals.get("litros_total"))
    km_consumption = _float(totals.get("km_consumo"))
    liters_consumption = _float(totals.get("litros_consumo"))
    advance_total = _float(totals.get("adiantamento_total"))
    trips_with_km = _int(totals.get("viagens_com_km"))
    trips_with_liters = _int(totals.get("viagens_com_litros"))
    trips_consumption = _int(totals.get("viagens_consumo"))
    with_error = _int(totals.get("com_erro"))

    consumption = _ratio(km_consumption, liters_consumption)
    advance_per_km = round(advance_total / km_total, 3) if km_total else 0.0
    liters_per_100km = round(liters_consumption / km_consumption * 100, 1) if km_consumption else 0.0

    granularity = period["granularity"]
    monthly = []
    for row in data["monthly"]:
        competencia = str(row.get("competencia") or "")
        monthly.append(
            {
                "competencia": competencia,
                "label": series_label(competencia, granularity, year_digits=2),
                "trips": _int(row.get("viagens")),
                "finished": _int(row.get("finalizadas")),
                "km": _float(row.get("km")),
                "km_display": _format_km(row.get("km")),
                "liters": _float(row.get("litros")),
                "advance": _float(row.get("adiantamento")),
                "advance_display": _format_money(row.get("adiantamento")),
                "errors": _int(row.get("com_erro")),
            }
        )
    mark_series_ticks(monthly)
    peak_trips = max((item["trips"] for item in monthly), default=0)
    for item in monthly:
        item["height_pct"] = round(item["trips"] / peak_trips * 100, 1) if peak_trips else 0.0

    validation_rows = data["validation"]
    correct = next(
        (_int(row["total"]) for row in validation_rows if row.get("rotulo") == "Cadastro correto"), 0
    )
    validation = {
        "correct": correct,
        "correct_display": _format_int(correct),
        "correct_pct": _share(correct, trips),
        "wrong": with_error,
        "wrong_display": _format_int(with_error),
        "wrong_pct": _share(with_error, trips),
        "reasons": [
            {
                "label": row.get("rotulo"),
                "total": _int(row.get("total")),
                "total_display": _format_int(row.get("total")),
                "share_pct": _share(row.get("total"), with_error),
            }
            for row in validation_rows
            if row.get("rotulo") != "Cadastro correto"
        ],
    }

    carrier_names = {item["key"]: item for item in (carriers_available or [])}
    carriers = []
    for row in data["carriers"]:
        code = _int(row.get("codigo"))
        meta = carrier_names.get(str(code), {})
        row_trips = _int(row.get("viagens"))
        carriers.append(
            {
                "code": code,
                "label": meta.get("label") or f"Frota {code}",
                "name": meta.get("name") or f"Frota {code}",
                "cnpj": meta.get("cnpj") or "",
                "trips": row_trips,
                "trips_display": _format_int(row_trips),
                "share_pct": _share(row_trips, trips),
                "km": _float(row.get("km")),
                "km_display": _format_km(row.get("km")),
                "consumption": _ratio(row.get("km_consumo"), row.get("litros_consumo")),
                "consumption_display": _format_decimal(
                    _ratio(row.get("km_consumo"), row.get("litros_consumo")), 2
                ),
                "advance_display": _format_money(row.get("adiantamento")),
                "duration_display": _format_days(row.get("duracao")),
                "errors": _int(row.get("com_erro")),
                "error_pct": _share(row.get("com_erro"), row_trips),
                "vehicles": _int(row.get("placas")),
                "drivers": _int(row.get("motoristas")),
            }
        )

    drivers = []
    for row in data["drivers"]:
        row_trips = _int(row.get("viagens"))
        code = _int(row.get("motorista"))
        fleet = _int(row.get("frota"))
        key = row.get("chave") or f"{fleet}-{code}"
        drivers.append(
            {
                "key": key,
                "label": _driver_name(drivers_catalog, key, code),
                "code": code,
                "fleet": fleet,
                "fleet_label": (carrier_names.get(str(fleet)) or {}).get("label") or f"Frota {fleet}",
                "trips": row_trips,
                "trips_display": _format_int(row_trips),
                "share_pct": _share(row_trips, trips),
                "km": _float(row.get("km")),
                "km_display": _format_km(row.get("km")),
                "consumption": _ratio(row.get("km_consumo"), row.get("litros_consumo")),
                "consumption_display": _format_decimal(
                    _ratio(row.get("km_consumo"), row.get("litros_consumo")), 2
                ),
                "advance_display": _format_money(row.get("adiantamento")),
                "duration_display": _format_days(row.get("duracao")),
                "errors": _int(row.get("com_erro")),
                "error_pct": _share(row.get("com_erro"), row_trips),
            }
        )

    vehicles = []
    for row in data["vehicles"]:
        row_trips = _int(row.get("viagens"))
        vehicle_consumption = _ratio(row.get("km_consumo"), row.get("litros_consumo"))
        plate = row.get("placa") or "-"
        registration = vehicles_catalog.get(plate)
        vehicles.append(
            {
                "plate": plate,
                "registered": registration is not None,
                "model": (registration or {}).get("model", "Placa sem cadastro"),
                "model_year": (registration or {}).get("year", "-"),
                "age": (registration or {}).get("age"),
                "age_bucket": (registration or {}).get("age_bucket", "indefinido"),
                "age_label": (registration or {}).get("age_label", "Sem cadastro"),
                "fleet": _int(row.get("frota")),
                "fleet_label": (carrier_names.get(str(_int(row.get("frota")))) or {}).get("label")
                or f"Frota {_int(row.get('frota'))}",
                "trips": row_trips,
                "trips_display": _format_int(row_trips),
                "km": _float(row.get("km")),
                "km_display": _format_km(row.get("km")),
                "km_average_display": _format_km(row.get("km_medio")),
                # Km do subconjunto que tem litros coerentes — o par que fecha
                # com `liters`. Somar `km` contra `liters` mistura bases.
                "km_consumption": _float(row.get("km_consumo")),
                "liters": _float(row.get("litros_consumo")),
                "liters_display": _format_liters(row.get("litros_consumo")),
                "consumption": vehicle_consumption,
                "consumption_display": _format_decimal(vehicle_consumption, 2),
                "odometer_display": _format_int(row.get("hodometro")),
                "errors": _int(row.get("com_erro")),
                "error_pct": _share(row.get("com_erro"), row_trips),
            }
        )
    peak_vehicle_km = max((item["km"] for item in vehicles), default=0)
    for item in vehicles:
        item["share_pct"] = round(item["km"] / peak_vehicle_km * 100, 1) if peak_vehicle_km else 0.0

    outliers = _consumption_outliers(vehicles, consumption)
    fleet_profile = _fleet_profile(vehicles, trips)
    cargo = _cargo(data, trips, routes_catalog)
    cost = _fleet_cost(
        cost_rows or [], monthly, km_total, advance_total, carrier_names,
        cargo_monthly=data.get("cargo_monthly") or [],
    )

    km_bands = _bucket_rows(
        data["km_buckets"],
        KM_BUCKETS,
        extra=lambda row: {
            "average_display": _format_km(row.get("media")),
            "km_display": _format_km(row.get("km")),
        },
    )
    duration_bands = _bucket_rows(
        data["duration_buckets"],
        DURATION_BUCKETS,
        extra=lambda row: {
            "average_display": _format_days(row.get("media")),
            "km_average_display": _format_km(row.get("km_medio")),
        },
    )

    forecast_row = (data["forecast"] or [{}])[0]
    with_forecast = _int(forecast_row.get("com_previsao"))
    same_date = _int(forecast_row.get("iguais"))
    late = _int(forecast_row.get("atrasadas"))
    # Em 3 de cada 4 viagens a chegada prevista é idêntica à realizada: o campo
    # é reescrito no fechamento. O indicador só diz alguma coisa sobre o resto,
    # e é por isso que a comparação aparece com a base explícita.
    comparable = max(with_forecast - same_date, 0)
    forecast = {
        "measured": with_forecast,
        "measured_display": _format_int(with_forecast),
        "same_date": same_date,
        "same_date_display": _format_int(same_date),
        "same_date_pct": _share(same_date, with_forecast),
        "comparable": comparable,
        "comparable_display": _format_int(comparable),
        "late": late,
        "late_display": _format_int(late),
        "late_pct": _share(late, comparable),
        "on_time": max(comparable - late, 0),
        "on_time_pct": round(100 - _share(late, comparable), 1),
        "delay_average": round(_float(forecast_row.get("atraso_medio")), 1),
        "delay_average_display": f"{_format_decimal(forecast_row.get('atraso_medio'), 1)} dias",
    }

    open_row = (data["open_totals"] or [{}])[0]
    open_counts = {
        "no_prazo": _int(open_row.get("no_prazo")),
        "ate_7": _int(open_row.get("ate_7")),
        "de_8_a_30": _int(open_row.get("de_8_a_30")),
        "acima_30": _int(open_row.get("acima_30")),
        # Sem chegada prevista não dá para dizer se atrasou: fica em uma faixa
        # própria, senão os baldes não fecham com o total de viagens abertas.
        "sem_previsao": _int(open_row.get("sem_previsao")),
    }
    open_items = []
    for row in data["open_trips"]:
        overdue = _int(row.get("dias_vencida"))
        open_items.append(
            {
                "trip": _int(row.get("viagem")),
                "fleet": _int(row.get("frota")),
                "driver": _driver_name(
                    drivers_catalog,
                    row.get("chv_motorista") or f"{_int(row.get('frota'))}-{_int(row.get('motorista'))}",
                    _int(row.get("motorista")),
                ),
                "plate": row.get("placa") or "-",
                "title": (row.get("titulo") or "-").strip(),
                "created_display": _display_date(row.get("data_geracao")),
                "departure_display": _display_datetime(row.get("dt_saida")),
                "forecast_display": _display_date(row.get("dt_prev_chegada")),
                "advance_display": _format_money(row.get("adiantamento")),
                "days_open": _int(row.get("dias_aberta")),
                "days_overdue": overdue,
                # A viagem só é problema depois da chegada prevista: até lá está
                # apenas em curso. 30 dias vencida vira vermelho.
                "tone": "red" if overdue > 30 else "amber" if overdue > 0 else "neutral",
            }
        )

    companies = []
    company_total = sum(_int(row["total"]) for row in data["companies"])
    for row in data["companies"]:
        companies.append(
            {
                "code": _int(row.get("codigo")),
                "label": f"Empresa {_int(row.get('codigo'))}",
                "total": _int(row.get("total")),
                "total_display": _format_int(row.get("total")),
                "km_display": _format_km(row.get("km")),
                "share_pct": _share(row.get("total"), company_total),
            }
        )

    situations = []
    for row in data["situations"]:
        situations.append(
            {
                "label": row.get("rotulo") or "-",
                "total": _int(row.get("total")),
                "total_display": _format_int(row.get("total")),
                "share_pct": _share(row.get("total"), trips),
            }
        )

    return {
        "scope": {
            "period": period,
            "carrier": carrier,
            "situation": situation,
            "period_choices": period_choices(),
            "series_granularity": granularity,
            "is_closed_period": bool(period.get("end")),
            "series_label": "por dia" if granularity == "day" else "por mês",
            "situation_choices": [{"key": key, "label": label} for key, label, _ in SITUATION_CHOICES],
            "carrier_choices": [{"key": "all", "label": "Todas"}]
            + [{"key": item["key"], "label": item["label"]} for item in (carriers_available or [])],
            "show_company_filter": len(companies) > 1,
        },
        "metrics": {
            "trips": trips,
            "trips_display": _format_int(trips),
            "finished": finished,
            "finished_display": _format_int(finished),
            "finished_pct": _share(finished, trips),
            "open": open_count,
            "open_display": _format_int(open_count),
            "open_pct": _share(open_count, trips),
            "km_total": km_total,
            "km_total_display": _format_km(km_total),
            "km_average_display": _format_km(totals.get("km_medio")),
            "trips_with_km": trips_with_km,
            "km_coverage_pct": _share(trips_with_km, trips),
            "liters_total": liters_total,
            "liters_total_display": _format_liters(liters_total),
            "trips_with_liters": trips_with_liters,
            "liters_coverage_pct": _share(trips_with_liters, trips),
            "consumption": consumption,
            "consumption_display": _format_decimal(consumption, 2),
            "consumption_base": trips_consumption,
            "consumption_base_pct": _share(trips_consumption, trips),
            "liters_per_100km_display": _format_decimal(liters_per_100km, 1),
            "advance_total": advance_total,
            "advance_total_display": _format_money(advance_total),
            "advance_average_display": _format_money(totals.get("adiantamento_medio")),
            "trips_with_advance": _int(totals.get("viagens_com_adiantamento")),
            "advance_coverage_pct": _share(totals.get("viagens_com_adiantamento"), trips),
            "advance_per_km": advance_per_km,
            "advance_per_km_display": _format_money(advance_per_km),
            "duration_average": _float(totals.get("duracao_media")),
            "duration_average_display": _format_days(totals.get("duracao_media")),
            "trips_with_duration": _int(totals.get("viagens_com_duracao")),
            "duration_coverage_pct": _share(totals.get("viagens_com_duracao"), trips),
            "vehicles": _int(totals.get("placas")),
            "drivers": _int(totals.get("motoristas")),
            "fleets": _int(totals.get("frotas")),
            "error_pct": _share(with_error, trips),
        },
        "validation": validation,
        "monthly": monthly,
        "carriers": carriers,
        "drivers": drivers,
        "vehicles": vehicles,
        "outliers": outliers,
        "fleet_profile": fleet_profile,
        "cargo": cargo,
        "cost": cost,
        "km_bands": km_bands,
        "duration_bands": duration_bands,
        "forecast": forecast,
        "companies": companies,
        "situations": situations,
        "open_backlog": {
            "total": _int(open_row.get("abertas")),
            "total_display": _format_int(open_row.get("abertas")),
            "advance_display": _format_money(open_row.get("adiantamento")),
            "counts": open_counts,
            "buckets": OPEN_AGING_BUCKETS,
            "items": open_items,
        },
        "limits": {
            "km_min": KM_MIN,
            "km_max": KM_MAX,
            "duration_max": DURATION_MAX_DAYS,
            "consumption_min": CONSUMPTION_MIN,
            "consumption_max": CONSUMPTION_MAX,
            "cargo_max_kg": CARGO_MAX_KG,
        },
    }


def _driver_name(catalog, key, code):
    """Nome do motorista, com o código como rede de segurança.

    O cadastro cobre hoje as 50 chaves que aparecem nas viagens, mas um
    motorista novo pode rodar antes de ser cadastrado — e aí o painel mostra o
    código em vez de uma linha em branco.
    """
    name = ((catalog or {}).get(str(key)) or "").strip()
    return name or f"Motorista {code}"


def _fleet_profile(vehicles, trips):
    """Idade e modelo da frota que rodou, e o consumo de cada faixa.

    A pergunta que este bloco responde é se caminhão velho está custando mais
    combustível. Por isso a faixa de idade carrega o consumo dela, não só a
    contagem de placas.
    """
    buckets = {key: {"key": key, "label": label, "plates": 0, "trips": 0,
                     "km": 0.0, "km_consumption": 0.0, "liters": 0.0}
               for key, label, _start, _end in VEHICLE_AGE_BUCKETS}
    unregistered_trips = 0
    unregistered_plates = 0
    models = {}

    for vehicle in vehicles:
        if not vehicle["registered"]:
            unregistered_trips += vehicle["trips"]
            unregistered_plates += 1
            continue
        bucket = buckets[vehicle["age_bucket"]]
        bucket["plates"] += 1
        bucket["trips"] += vehicle["trips"]
        bucket["km"] += vehicle["km"]
        bucket["km_consumption"] += vehicle.get("km_consumption", 0.0)
        bucket["liters"] += vehicle["liters"]

        model = vehicle["model"]
        entry = models.setdefault(model, {"label": model, "plates": 0, "trips": 0, "km": 0.0})
        entry["plates"] += 1
        entry["trips"] += vehicle["trips"]
        entry["km"] += vehicle["km"]

    ages = []
    total_plates = sum(item["plates"] for item in buckets.values())
    for key, label, _start, _end in VEHICLE_AGE_BUCKETS:
        item = buckets[key]
        if not item["plates"]:
            continue
        item_consumption = _ratio(item["km_consumption"], item["liters"])
        ages.append(
            {
                "key": key,
                "label": label,
                "plates": item["plates"],
                "plates_pct": _share(item["plates"], total_plates),
                "trips": item["trips"],
                "trips_display": _format_int(item["trips"]),
                "trips_pct": _share(item["trips"], trips),
                "km_display": _format_km(item["km"]),
                "consumption": item_consumption,
                "consumption_display": _format_decimal(item_consumption, 2) if item_consumption else "-",
            }
        )
    peak_consumption = max((item["consumption"] for item in ages), default=0)
    for item in ages:
        item["share_pct"] = (
            round(item["consumption"] / peak_consumption * 100, 1) if peak_consumption else 0.0
        )

    ranking = sorted(models.values(), key=lambda item: item["km"], reverse=True)[:8]
    peak_model_km = max((item["km"] for item in ranking), default=0)
    for item in ranking:
        item["km_display"] = _format_km(item["km"])
        item["trips_display"] = _format_int(item["trips"])
        item["share_pct"] = round(item["km"] / peak_model_km * 100, 1) if peak_model_km else 0.0

    return {
        "ages": ages,
        "models": ranking,
        "plates": total_plates,
        "unregistered_plates": unregistered_plates,
        "unregistered_trips": unregistered_trips,
        "unregistered_pct": _share(unregistered_trips, trips),
        "registered_pct": round(100 - _share(unregistered_trips, trips), 1),
    }


def _format_tons(value):
    return f"{_format_decimal(_float(value) / 1000, 1)} t"


def _cargo(data, trips, routes_catalog):
    """Carga transportada e rotas percorridas.

    Duas ressalvas moldam este bloco e aparecem na tela:

    O roteiro só existe a partir de setembro de 2025. Antes disso a tabela está
    vazia, então um recorte longo mostra queda de volume onde houve, na verdade,
    ausência de cadastro — por isso a cobertura anda junto de todo número daqui.

    Uma viagem pode passar por mais de uma rota. Peso e paletes são aditivos e
    somam certo por rota, mas a contagem de viagens não: uma viagem de duas
    rotas conta em cada uma. Quilometragem por rota nem é tentada — o km é da
    viagem inteira e não se reparte entre as pernas.
    """
    totals = (data.get("cargo_totals") or [{}])[0]
    with_cargo = _int(totals.get("viagens_com_carga"))
    weight = _float(totals.get("peso"))
    pallets = _int(totals.get("pallets"))
    invalid_legs = _int(totals.get("pernas_invalidas"))

    bands = _bucket_rows(
        data.get("cargo_bands") or [],
        CARGO_BUCKETS,
        extra=lambda row: {
            "average_display": _format_tons(row.get("media")),
            "pallets_display": _format_decimal(row.get("pallets"), 1),
            "km_average_display": _format_km(row.get("km_medio")),
        },
    )

    routes = []
    principal = {"trips": 0, "weight": 0.0}
    opportunistic = {"trips": 0, "weight": 0.0}
    for row in data.get("routes") or []:
        key = (row.get("rota") or "").strip()
        meta = routes_catalog.get(key) or {
            "name": f"Rota {key}" if key else "Rota não informada",
            "opportunistic": key in OPPORTUNISTIC_ROUTES,
            "type": "Rotas de Aproveitamento" if key in OPPORTUNISTIC_ROUTES else "Rotas Principais",
        }
        row_weight = _float(row.get("peso"))
        row_trips = _int(row.get("viagens"))
        bucket = opportunistic if meta["opportunistic"] else principal
        bucket["trips"] += row_trips
        bucket["weight"] += row_weight
        routes.append(
            {
                "key": key,
                "name": meta["name"],
                "type": meta["type"],
                "opportunistic": meta["opportunistic"],
                "registered": key in routes_catalog,
                "trips": row_trips,
                "trips_display": _format_int(row_trips),
                "weight": row_weight,
                "weight_display": _format_tons(row_weight),
                "weight_per_trip_display": _format_tons(row_weight / row_trips) if row_trips else "-",
                "pallets": _int(row.get("pallets")),
                "pallets_display": _format_int(row.get("pallets")),
                "legs": _int(row.get("pernas")),
                "vehicles": _int(row.get("placas")),
                "drivers": _int(row.get("motoristas")),
            }
        )
    total_weight_routes = sum(item["weight"] for item in routes)
    for item in routes:
        item["share_pct"] = _share(item["weight"], total_weight_routes)

    kinds = []
    for label, bucket in (("Rotas Principais", principal), ("Rotas de Aproveitamento", opportunistic)):
        kinds.append(
            {
                "label": label,
                "trips": bucket["trips"],
                "trips_display": _format_int(bucket["trips"]),
                "weight": bucket["weight"],
                "weight_display": _format_tons(bucket["weight"]),
                "share_pct": _share(bucket["weight"], total_weight_routes),
            }
        )

    return {
        "has_data": bool(with_cargo),
        "trips_with_cargo": with_cargo,
        "trips_with_cargo_display": _format_int(with_cargo),
        "coverage_pct": _share(with_cargo, trips),
        "weight": weight,
        "weight_display": _format_tons(weight),
        "weight_per_trip_display": _format_tons(weight / with_cargo) if with_cargo else "-",
        "pallets": pallets,
        "pallets_display": _format_int(pallets),
        "pallets_per_trip_display": _format_decimal(pallets / with_cargo, 1) if with_cargo else "-",
        "kg_per_pallet_display": _format_decimal(weight / pallets, 0) if pallets else "-",
        "legs": _int(totals.get("pernas")),
        "legs_per_trip_display": _format_decimal(_int(totals.get("pernas")) / with_cargo, 1) if with_cargo else "-",
        "invalid_legs": invalid_legs,
        "multi_route_trips": _int(totals.get("viagens_multirota")),
        "bands": bands,
        "routes": routes,
        "kinds": kinds,
        "route_count": len(routes),
        "unregistered_routes": [item["key"] for item in routes if not item["registered"]],
    }


def _fleet_cost(cost_rows, monthly, km_total, advance_total, carrier_names,
                today=None, cargo_monthly=None):
    """Custo contábil da frota, cruzado com a quilometragem do mesmo recorte.

    O adiantamento que o painel já mostrava é dinheiro de viagem, não o custo da
    frota: aqui ele aparece como fatia do custo contábil, que é onde combustível,
    manutenção e folha realmente entram.

    O custo por km sai só das competências fechadas. O mês corrente ainda não
    recebeu o lançamento de fechamento — em 21/08/2026 tinha R$ 23 mil contra
    R$ 780 mil de um mês fechado — e entraria na média puxando tudo para baixo.
    """
    current = (today or date.today()).strftime("%m%Y")
    # `EMP_TRANSP` é '1-1001' / '1-1004'; o código da frota é o que vem depois do
    # traço, e é ele que casa com a viagem.
    by_month = {}
    by_fleet = {}
    total = 0.0
    for row in cost_rows:
        value = _float(row.get("valor"))
        competencia = str(row.get("competencia") or "")
        fleet = str(row.get("emp_transp") or "").split("-")[-1]
        total += value
        by_month[competencia] = by_month.get(competencia, 0.0) + value
        by_fleet[fleet] = by_fleet.get(fleet, 0.0) + value

    # A série do painel pode estar em dia; o custo só existe por competência.
    km_by_competencia = {}
    for item in monthly:
        key = str(item["competencia"])[:7].replace("-", "")
        # 'YYYY-MM' e 'YYYY-MM-DD' viram 'MMYYYY', que é a chave do contábil.
        if len(key) >= 6:
            competencia = f"{key[4:6]}{key[0:4]}"
            km_by_competencia[competencia] = km_by_competencia.get(competencia, 0.0) + item["km"]

    # A tonelagem tem cobertura própria: o roteiro só existe desde setembro de
    # 2025, então o custo por tonelada só pode olhar as competências em que
    # existe carga registrada — nas outras o denominador seria zero, não baixo.
    weight_by_competencia = {}
    for item in cargo_monthly or []:
        key = str(item.get("competencia") or "")[:7].replace("-", "")
        if len(key) >= 6:
            competencia = f"{key[4:6]}{key[0:4]}"
            weight_by_competencia[competencia] = (
                weight_by_competencia.get(competencia, 0.0) + _float(item.get("peso"))
            )

    months = []
    closed_cost = 0.0
    closed_km = 0.0
    cost_with_cargo = 0.0
    closed_weight = 0.0
    for competencia in sorted(by_month, key=lambda value: (value[2:], value[:2])):
        value = by_month[competencia]
        km = km_by_competencia.get(competencia, 0.0)
        per_km = round(value / km, 2) if km else 0.0
        is_open = competencia == current
        weight = weight_by_competencia.get(competencia, 0.0)
        if not is_open:
            closed_cost += value
            closed_km += km
            if weight > 0:
                cost_with_cargo += value
                closed_weight += weight
        months.append(
            {
                "competencia": competencia,
                "label": f"{competencia[:2]}/{competencia[2:]}",
                "cost": value,
                "cost_display": _format_money(value),
                "km": km,
                "km_display": _format_km(km),
                "cost_per_km": per_km,
                "cost_per_km_display": _format_money(per_km) if km else "-",
                "weight": weight,
                "weight_display": _format_tons(weight),
                "is_open": is_open,
            }
        )
    peak_cost = max((item["cost"] for item in months), default=0)
    for item in months:
        item["height_pct"] = round(item["cost"] / peak_cost * 100, 1) if peak_cost else 0.0
    mark_series_ticks(months)

    fleets = []
    for code, value in sorted(by_fleet.items(), key=lambda pair: pair[1], reverse=True):
        meta = (carrier_names or {}).get(code, {})
        fleets.append(
            {
                "code": code,
                "label": meta.get("label") or f"Frota {code}",
                "cost": value,
                "cost_display": _format_money(value),
                "share_pct": _share(value, total),
            }
        )

    per_km = round(closed_cost / closed_km, 2) if closed_km else 0.0
    per_ton = round(cost_with_cargo / (closed_weight / 1000), 2) if closed_weight else 0.0
    open_months = [item for item in months if item["is_open"]]
    cargo_months = sum(1 for item in months if not item["is_open"] and item["weight"] > 0)
    return {
        "total": total,
        "total_display": _format_money(total),
        "closed_total": closed_cost,
        "closed_total_display": _format_money(closed_cost),
        "closed_km_display": _format_km(closed_km),
        "per_km": per_km,
        "per_km_display": _format_money(per_km) if closed_km else "-",
        "advance_total_display": _format_money(advance_total),
        # A fatia compara adiantamento com custo fechado: o mês aberto tem a
        # viagem lançada e o custo ainda não, e inflaria a participação.
        "advance_share_pct": _share(advance_total, closed_cost),
        "per_ton": per_ton,
        "per_ton_display": _format_money(per_ton) if closed_weight else "-",
        "cargo_months": cargo_months,
        "cargo_weight_display": _format_tons(closed_weight),
        "has_cargo": bool(closed_weight),
        "months": months,
        "closed_months": len(months) - len(open_months),
        "open_month": open_months[0]["label"] if open_months else "",
        "fleets": fleets,
        "has_data": bool(months),
        "has_closed_month": bool(closed_km),
    }


# O desvio que interessa é o que muda a conta de combustível, não qualquer
# diferença: 12% para menos em um veículo que roda 300 mil km/ano é dinheiro.
OUTLIER_THRESHOLD_PCT = 12.0
OUTLIER_MIN_TRIPS = 5


def _consumption_outliers(vehicles, fleet_consumption):
    """Placas que consomem fora do padrão da frota.

    Só entram veículos com histórico suficiente: uma placa com duas viagens tem
    média instável e apareceria no topo da lista sem significar nada.
    """
    if not fleet_consumption:
        return {"reference": 0.0, "reference_display": "-", "threshold_pct": OUTLIER_THRESHOLD_PCT, "items": []}

    items = []
    for vehicle in vehicles:
        if vehicle["trips"] < OUTLIER_MIN_TRIPS or not vehicle["consumption"]:
            continue
        deviation = round((vehicle["consumption"] - fleet_consumption) / fleet_consumption * 100, 1)
        if abs(deviation) < OUTLIER_THRESHOLD_PCT:
            continue
        items.append(
            {
                "plate": vehicle["plate"],
                "fleet_label": vehicle["fleet_label"],
                "trips": vehicle["trips"],
                "km_display": vehicle["km_display"],
                "liters_display": vehicle["liters_display"],
                "consumption": vehicle["consumption"],
                "consumption_display": vehicle["consumption_display"],
                "deviation_pct": deviation,
                "deviation_display": f"{'+' if deviation > 0 else ''}{_format_decimal(deviation, 1)}%",
                "tone": "green" if deviation > 0 else "red",
                # A barra compara o módulo do desvio entre os destacados.
                "share_pct": min(abs(deviation) * 2, 100),
            }
        )
    items.sort(key=lambda item: item["deviation_pct"])
    return {
        "reference": fleet_consumption,
        "reference_display": _format_decimal(fleet_consumption, 2),
        "threshold_pct": OUTLIER_THRESHOLD_PCT,
        "min_trips": OUTLIER_MIN_TRIPS,
        "items": items,
    }


# --------------------------------------------------- analises processadas ---

# Estes cruzamentos não entram na abertura da tela: varrem a base inteira do
# recorte e só fazem sentido quando o usuário pede os indicadores.

def compute_deep_analytics(period_key="12", carrier_key="all", situation_key="all"):
    period = resolve_period(period_key)
    carrier = resolve_carrier(carrier_key, list_carriers())
    situation = resolve_situation(situation_key)
    scope, params = _scope_sql(period, carrier, situation)
    base = _base(scope)
    series_column = "DIA" if period["granularity"] == "day" else "COMPETENCIA"

    statements = {
        "departure_heat": f"""
            {base}
            SELECT
              TRUNC(DT_SAIDA) - TRUNC(DT_SAIDA, 'IW') AS DIA,
              TO_NUMBER(TO_CHAR(DT_SAIDA, 'HH24')) AS HORA,
              COUNT(*) AS TOTAL
            FROM BASE WHERE DT_SAIDA IS NOT NULL
            GROUP BY TRUNC(DT_SAIDA) - TRUNC(DT_SAIDA, 'IW'), TO_NUMBER(TO_CHAR(DT_SAIDA, 'HH24'))
        """,
        "monthly_efficiency": f"""
            {base}
            SELECT
              {series_column} AS COMPETENCIA,
              SUM(KM_CONSUMO) AS KM_CONSUMO,
              SUM(LITROS_CONSUMO) AS LITROS_CONSUMO,
              SUM(KM_VALIDO) AS KM,
              SUM(ADIANTAMENTO) AS ADIANTAMENTO,
              COUNT(*) AS VIAGENS
            FROM BASE
            GROUP BY {series_column} ORDER BY {series_column}
        """,
        "km_band_consumption": f"""
            {base}
            SELECT
              CASE
                WHEN KM_CONSUMO <= 200 THEN 'ate_200'
                WHEN KM_CONSUMO <= 800 THEN 'de_201_800'
                WHEN KM_CONSUMO <= 1500 THEN 'de_801_1500'
                WHEN KM_CONSUMO <= 3000 THEN 'de_1501_3000'
                ELSE 'acima_3000'
              END AS FAIXA,
              COUNT(*) AS TOTAL,
              SUM(KM_CONSUMO) AS KM,
              SUM(LITROS_CONSUMO) AS LITROS
            FROM BASE WHERE KM_CONSUMO IS NOT NULL
            GROUP BY CASE
                WHEN KM_CONSUMO <= 200 THEN 'ate_200'
                WHEN KM_CONSUMO <= 800 THEN 'de_201_800'
                WHEN KM_CONSUMO <= 1500 THEN 'de_801_1500'
                WHEN KM_CONSUMO <= 3000 THEN 'de_1501_3000'
                ELSE 'acima_3000'
              END
        """,
        "driver_efficiency": f"""
            {base}
            SELECT
              CHV_MOTORISTA AS CHAVE, FROTA, MOTORISTA,
              COUNT(*) AS VIAGENS,
              SUM(KM_CONSUMO) AS KM_CONSUMO,
              SUM(LITROS_CONSUMO) AS LITROS_CONSUMO,
              SUM(ADIANTAMENTO) AS ADIANTAMENTO,
              SUM(KM_VALIDO) AS KM
            FROM BASE
            GROUP BY CHV_MOTORISTA, FROTA, MOTORISTA
            HAVING COUNT(KM_CONSUMO) >= {OUTLIER_MIN_TRIPS}
        """,
        "advance_bands": f"""
            {base}
            SELECT
              CASE
                WHEN ADIANTAMENTO <= 0 THEN 'sem'
                WHEN ADIANTAMENTO <= 300 THEN 'ate_300'
                WHEN ADIANTAMENTO <= 600 THEN 'de_301_600'
                WHEN ADIANTAMENTO <= 1000 THEN 'de_601_1000'
                ELSE 'acima_1000'
              END AS FAIXA,
              COUNT(*) AS TOTAL,
              AVG(KM_VALIDO) AS KM_MEDIO,
              SUM(ADIANTAMENTO) AS VALOR
            FROM BASE
            GROUP BY CASE
                WHEN ADIANTAMENTO <= 0 THEN 'sem'
                WHEN ADIANTAMENTO <= 300 THEN 'ate_300'
                WHEN ADIANTAMENTO <= 600 THEN 'de_301_600'
                WHEN ADIANTAMENTO <= 1000 THEN 'de_601_1000'
                ELSE 'acima_1000'
              END
        """,
    }
    data = _query_many(statements, {name: dict(params) for name in statements})
    return _build_deep_analytics(data, period["granularity"])


ADVANCE_BANDS = [
    ("sem", "Sem adiantamento"),
    ("ate_300", "Até R$ 300"),
    ("de_301_600", "R$ 301 a 600"),
    ("de_601_1000", "R$ 601 a 1.000"),
    ("acima_1000", "Acima de R$ 1.000"),
]


def _build_deep_analytics(data, granularity="month"):
    # `TRUNC(data) - TRUNC(data, 'IW')` devolve 0 para segunda-feira, sem
    # depender do NLS do banco — que é o problema do TO_CHAR(data, 'D').
    grid = {(day, block): 0 for day in range(7) for block, _l, _i, _f in HOUR_BLOCKS}
    for row in data["departure_heat"]:
        day = _int(row.get("dia"))
        hour = _int(row.get("hora"))
        if not 0 <= day <= 6:
            continue
        for block, _label, start, end in HOUR_BLOCKS:
            if start <= hour <= end:
                grid[(day, block)] = grid.get((day, block), 0) + _int(row.get("total"))
                break
    peak = max(grid.values()) if grid else 0
    heatmap = {
        "hour_labels": [label for _key, label, _i, _f in HOUR_BLOCKS],
        "peak": peak,
        "rows": [
            {
                "label": WEEKDAY_LABELS[day],
                "cells": [
                    {
                        "total": grid.get((day, block), 0),
                        "intensity": round(grid.get((day, block), 0) / peak, 3) if peak else 0.0,
                    }
                    for block, _label, _i, _f in HOUR_BLOCKS
                ],
            }
            for day in range(7)
        ],
    }

    efficiency = []
    for row in data["monthly_efficiency"]:
        competencia = str(row.get("competencia") or "")
        km = _float(row.get("km"))
        consumption = _ratio(row.get("km_consumo"), row.get("litros_consumo"))
        cost = round(_float(row.get("adiantamento")) / km, 3) if km else 0.0
        efficiency.append(
            {
                "competencia": competencia,
                "label": series_label(competencia, granularity, year_digits=2),
                "consumption": consumption,
                "consumption_display": _format_decimal(consumption, 2),
                "advance_per_km": cost,
                "advance_per_km_display": _format_money(cost),
                "trips": _int(row.get("viagens")),
                "km_display": _format_km(km),
            }
        )
    mark_series_ticks(efficiency)
    peak_consumption = max((item["consumption"] for item in efficiency), default=0)
    for item in efficiency:
        item["height_pct"] = (
            round(item["consumption"] / peak_consumption * 100, 1) if peak_consumption else 0.0
        )

    band_rows = {str(row.get("faixa")): row for row in data["km_band_consumption"]}
    km_band_consumption = []
    for key, label, _start, _end in KM_BUCKETS:
        row = band_rows.get(key) or {}
        consumption = _ratio(row.get("km"), row.get("litros"))
        km_band_consumption.append(
            {
                "key": key,
                "label": label,
                "trips": _int(row.get("total")),
                "trips_display": _format_int(row.get("total")),
                "consumption": consumption,
                "consumption_display": _format_decimal(consumption, 2) if consumption else "-",
                "km_display": _format_km(row.get("km")),
            }
        )
    peak_band = max((item["consumption"] for item in km_band_consumption), default=0)
    for item in km_band_consumption:
        item["share_pct"] = round(item["consumption"] / peak_band * 100, 1) if peak_band else 0.0

    drivers = []
    for row in data["driver_efficiency"]:
        consumption = _ratio(row.get("km_consumo"), row.get("litros_consumo"))
        if not consumption:
            continue
        km = _float(row.get("km"))
        drivers.append(
            {
                "key": row.get("chave"),
                "label": f"Motorista {_int(row.get('motorista'))}",
                "fleet": _int(row.get("frota")),
                "trips": _int(row.get("viagens")),
                "consumption": consumption,
                "consumption_display": _format_decimal(consumption, 2),
                "km_display": _format_km(km),
                "advance_per_km_display": _format_money(
                    round(_float(row.get("adiantamento")) / km, 3) if km else 0
                ),
            }
        )
    drivers.sort(key=lambda item: item["consumption"], reverse=True)
    peak_driver = max((item["consumption"] for item in drivers), default=0)
    for item in drivers:
        item["share_pct"] = round(item["consumption"] / peak_driver * 100, 1) if peak_driver else 0.0

    advance_rows = {str(row.get("faixa")): row for row in data["advance_bands"]}
    advance_total = sum(_int(row.get("total")) for row in data["advance_bands"])
    advance_bands = []
    for key, label in ADVANCE_BANDS:
        row = advance_rows.get(key) or {}
        count = _int(row.get("total"))
        advance_bands.append(
            {
                "key": key,
                "label": label,
                "total": count,
                "total_display": _format_int(count),
                "share_pct": _share(count, advance_total),
                "km_average_display": _format_km(row.get("km_medio")),
                "value_display": _format_money(row.get("valor")),
            }
        )

    return {
        "heatmap": heatmap,
        "monthly_efficiency": efficiency,
        "km_band_consumption": km_band_consumption,
        "driver_efficiency": {
            "best": drivers[:8],
            "worst": list(reversed(drivers[-8:])) if len(drivers) > 8 else [],
            "measured": len(drivers),
        },
        "advance_bands": advance_bands,
    }


# ------------------------------------------------------------ analise de IA --

TRAVEL_BI_SYSTEM_PROMPT = (
    "Você é um analista de logística e gestão de frota rodoviária. Produza somente análises "
    "sustentadas pelos indicadores enviados e respeite rigorosamente o formato JSON solicitado."
)

TRAVEL_BI_RESPONSE_SCHEMA = {
    "name": "fleet_travel_insights",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "executive_summary": {"type": "string"},
            "health": {"type": "string", "enum": ["saudavel", "atencao", "critico"]},
            "principal_risk": {"type": "string"},
            "principal_opportunity": {"type": "string"},
            "insights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["positive", "attention", "risk", "cost", "fleet", "quality"],
                        },
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "recommended_action": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["type", "title", "summary", "evidence", "recommended_action", "confidence"],
                },
            },
            "recommended_actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "executive_summary", "health", "principal_risk",
            "principal_opportunity", "insights", "recommended_actions",
        ],
    },
}


def travel_dashboard_fingerprint(dashboard):
    """Impressão dos números do recorte, para saber quando a análise envelheceu."""
    base = json.dumps(
        {
            "scope": [
                dashboard["scope"]["period"]["key"],
                dashboard["scope"]["carrier"]["key"],
                dashboard["scope"]["situation"]["key"],
            ],
            "metrics": dashboard["metrics"],
            "validation": {key: value for key, value in dashboard["validation"].items() if key != "reasons"},
            "forecast": dashboard["forecast"],
            "open_backlog": dashboard["open_backlog"]["counts"],
        },
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def build_travel_ai_payload(dashboard, fingerprint, deep=None):
    """Payload determinístico enviado à IA.

    As séries longas entram resumidas: a análise precisa do formato da operação,
    não das 135 placas que já estão na tela.
    """
    scope = dashboard["scope"]
    metrics = dashboard["metrics"]
    limits = dashboard["limits"]
    return {
        "schema_version": "1.0",
        "request_type": "fleet_travel_intelligence",
        "source_system": "ConnectMX / ERP Senior - USU_TCADVIA",
        "source_fingerprint": fingerprint,
        "scope": {
            "period": scope["period"]["full_label"],
            "carrier": scope["carrier"]["label"],
            "situation": scope["situation"]["label"],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "deterministic_metrics": {
            "volume": metrics,
            "registration_quality": dashboard["validation"],
            "monthly_series": dashboard["monthly"],
            "carriers": dashboard["carriers"],
            "top_drivers": [
                {
                    key: driver[key]
                    for key in ("label", "fleet", "trips", "km_display", "consumption",
                                "advance_display", "duration_display", "error_pct")
                }
                for driver in dashboard["drivers"][:12]
            ],
            "top_vehicles": [
                {
                    key: vehicle[key]
                    for key in ("plate", "trips", "km_display", "consumption", "error_pct")
                }
                for vehicle in dashboard["vehicles"][:15]
            ],
            "consumption_outliers": dashboard["outliers"],
            "fleet_cost": {
                key: dashboard["cost"][key]
                for key in ("closed_total_display", "per_km_display", "closed_months",
                            "advance_total_display", "advance_share_pct", "open_month")
            },
            "cost_by_competencia": [
                {key: item[key] for key in ("label", "cost_display", "km_display",
                                            "cost_per_km", "is_open")}
                for item in dashboard["cost"]["months"]
            ],
            "cost_by_fleet": dashboard["cost"]["fleets"],
            "cargo": {
                key: dashboard["cargo"][key]
                for key in ("trips_with_cargo", "coverage_pct", "weight_display",
                            "weight_per_trip_display", "pallets", "pallets_per_trip_display",
                            "kg_per_pallet_display", "legs_per_trip_display",
                            "multi_route_trips", "invalid_legs")
            },
            "cargo_bands": dashboard["cargo"]["bands"]["items"],
            "routes": [
                {key: item[key] for key in ("name", "type", "trips", "weight_display",
                                            "weight_per_trip_display", "pallets", "share_pct")}
                for item in dashboard["cargo"]["routes"]
            ],
            "route_kinds": dashboard["cargo"]["kinds"],
            "cost_per_ton": {
                key: dashboard["cost"][key]
                for key in ("per_ton_display", "cargo_months", "cargo_weight_display")
            },
            "fleet_age": dashboard["fleet_profile"]["ages"],
            "fleet_models": dashboard["fleet_profile"]["models"],
            "km_bands": dashboard["km_bands"]["items"],
            "duration_bands": dashboard["duration_bands"]["items"],
            "forecast_vs_actual": dashboard["forecast"],
            "open_backlog": {
                "total": dashboard["open_backlog"]["total"],
                "counts": dashboard["open_backlog"]["counts"],
                "advance_at_risk": dashboard["open_backlog"]["advance_display"],
                "oldest": [
                    {
                        key: item[key]
                        for key in ("trip", "plate", "driver", "forecast_display",
                                    "days_overdue", "advance_display")
                    }
                    for item in dashboard["open_backlog"]["items"][:10]
                ],
            },
            "companies": dashboard["companies"],
            "departure_heatmap": (deep or {}).get("heatmap"),
            "monthly_efficiency": (deep or {}).get("monthly_efficiency"),
            "consumption_by_distance_band": (deep or {}).get("km_band_consumption"),
            "driver_efficiency": (deep or {}).get("driver_efficiency"),
            "advance_bands": (deep or {}).get("advance_bands"),
        },
        "data_quality_notes": [
            f"Data em branco no cadastro é gravada como 1900-12-31, não como nulo; tudo antes de "
            f"1901 foi tratado como não informado.",
            f"KM rodado só entra nos indicadores entre {limits['km_min']} e {limits['km_max']} km: "
            f"fora disso a base tem chegada menor que saída e hodômetro digitado no lugar da diferença.",
            f"Duração só é considerada entre 0 e {limits['duration_max']} dias — acima disso o que "
            f"existe na base é ano digitado errado (saída em 2023, chegada em 1923).",
            f"O consumo usa apenas viagens com KM e litros coerentes, entre "
            f"{limits['consumption_min']} e {limits['consumption_max']} km/l: são "
            f"{metrics['consumption_base']} viagens ({metrics['consumption_base_pct']}% do recorte).",
            f"Litros estão preenchidos em {metrics['liters_coverage_pct']}% das viagens: o total "
            f"abastecido é piso, não medida fechada.",
            "A chegada prevista é reescrita no fechamento da viagem e fica igual à realizada em boa "
            "parte dos registros; a comparação de prazo só considera onde as duas diferem.",
            "O nome do motorista vem de E073MOT pela chave frota + código, que cobre hoje todas "
            "as viagens do recorte.",
            "O custo é contábil, dos centros de custo da frota, e casa com a viagem por "
            "competência — o custo tem data de lançamento e a viagem tem data de geração, então "
            "o cruzamento é mensal, nunca diário.",
            "O custo por km usa só competências fechadas: o mês corrente ainda não recebeu o "
            "lançamento de fechamento e entraria na média com uma fração do valor real.",
            "O adiantamento já está dentro do custo contábil; ele aparece como fatia, não somado.",
            "A idade do veículo não isola desgaste: a frota mistura carreta, caminhão leve e "
            "utilitário, e o porte pesa mais no consumo do que o ano de fabricação.",
            "O roteiro (carga e rota) só passou a ser preenchido em setembro de 2025: antes disso "
            f"a tabela está vazia. No recorte atual ele cobre {dashboard['cargo']['coverage_pct']}% "
            "das viagens. Num recorte que alcance datas anteriores, a queda de carga é ausência de "
            "cadastro, não redução de volume.",
            "Uma viagem pode passar por mais de uma rota. Carga e paletes são aditivos e somam "
            "certo por rota; a contagem de viagens por rota conta a mesma viagem em cada rota, "
            "então a soma das rotas passa do total de viagens.",
            "Não existe quilometragem por rota: o km é medido na viagem inteira e não se reparte "
            "entre as pernas do roteiro.",
            "O custo por tonelada usa só as competências fechadas que também têm roteiro; nas "
            "demais o denominador seria zero, não baixo.",
            "A fila de viagens abertas ignora o filtro de período e de situação — é o que está em "
            "aberto hoje, de todo o histórico.",
            "A placa é normalizada (maiúsculas, sem hífen e sem espaço) antes de agrupar: no cadastro "
            "as mesmas 91 placas aparecem sob 135 grafias diferentes.",
            *(
                []
                if scope["period"].get("end")
                else [
                    "O recorte alcança o mês corrente, que ainda não fechou: o adiantamento já foi "
                    "pago e a quilometragem só entra quando a viagem volta, então o custo por km do "
                    "último mês da série sai inflado e a taxa de finalização sai baixa. O recorte "
                    "'Mês passado' não tem esse viés.",
                ]
            ),
        ],
        "analysis_instructions": [
            "Use somente os indicadores fornecidos; não invente causas para variações.",
            "Separe problema de custo (adiantamento por km, consumo) de problema de processo "
            "(qualidade de cadastro, viagem aberta sem baixa).",
            "Trate viagem aberta com chegada prevista vencida como risco financeiro: há "
            "adiantamento pago sem prestação de contas.",
            "Ao comentar consumo, cite a base de viagens consideradas antes de concluir.",
            "Compare o consumo de cada placa com a referência da frota, não entre placas soltas.",
            "Considere as notas de qualidade de dados: não tire conclusão de um campo marcado "
            "como incompleto ou reescrito.",
            "Trate erro de cadastro como perda de informação gerencial, indicando o motivo mais "
            "frequente e o que ele impede de medir.",
            "Ao falar de custo, use o custo contábil por km e a série de competências fechadas; "
            "não compare uma competência aberta com uma fechada.",
            "Antes de atribuir consumo à idade do veículo, verifique se as faixas comparadas têm "
            "o mesmo tipo de veículo.",
            "Ao comparar rotas, use carga e carga por viagem; não conclua nada sobre distância ou "
            "custo por rota, que não são medidos nesse nível.",
            "Separe rota principal de rota de aproveitamento antes de comparar volume: a segunda "
            "é carga de retorno ou de complemento, com natureza diferente.",
            "Cheque a cobertura do roteiro antes de comentar tendência de carga.",
            "Cite números em cada evidência e escreva em português do Brasil.",
        ],
        "response_format": {"type": "json_schema", "json_schema": TRAVEL_BI_RESPONSE_SCHEMA},
    }
