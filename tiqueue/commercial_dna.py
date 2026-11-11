"""
DNA comercial v2: DNA do Cliente (nova versão) e DNA de Representantes.

As regras vêm dos PBIX da área comercial, extraídos em
`C:\\ConnectMX\\extracao_pbix` ("1. VENDAS0" e "2. ORÇAMENTO 2026"), e usam o
mesmo Oracle do ERP Senior do DNA do Cliente original.

Faturamento (consulta FATURAMENTO do PBIX)
  * Entra só transação que conta como venda (E001TNS.VENFAT = 'S') em nota
    emitida (E140NFV.SITNFV = 2). Clientes intercompany (1001, 1002, 1004)
    ficam de fora, como no DNA original.
  * VL_VALOR DEVOLVIDO = VL_PRODUTO / QTD_PRODUTO x QTD_DEVOLVIDA e
    VL_FAT-DEV = VL_PRODUTO - devolvido. O peso (PESLIQ) segue a mesma regra
    (VL_PESO -DEV). Linhas com VL_FAT-DEV <= 0 saem da conta.
  * TOTAL S/IMPOSTO = VL_FAT-DEV - (ICMS + PIS + COFINS), zerado se negativo.
  * FATOR KG = VL_FAT-DEV / kg líquido; FATOR KG LIMPO = TOTAL S/IMPOSTO / kg.
  * Grupo de produto (GRUPO 4.1) e unidade do orçamento (FILIAL ORÇ) seguem as
    colunas condicionais do PBIX: ver `product_group` e `budget_unit`.

Classificação Geral (medidas "Curva ABC", "ABC %LO", "FAIXAS LOTE",
"Classificação ABC - Média de meses entre compras", "RANK", "Orientação ABCD")
  * Faturamento: Pareto do TOTAL S/IMPOSTO de todos os clientes no período,
    acumulado (incluindo o próprio cliente) <= 80% A, <= 95% B, resto C.
  * Margem: 1 - CPV / TOTAL S/IMPOSTO; <= 5% C, <= 15% B, > 15% A. O PBIX lia o
    CPV de uma tabela CUSTOS que não veio na extração; aqui ele é o custo do
    movimento de estoque de saída da própria nota (E210MVP, ESTEOS = 'S'),
    proporcional ao que não foi devolvido.
  * Lote: kg líquido médio por mês com faturamento; <= 1.000 C, <= 2.000 B, > 2.000 A.
  * Recorrência: meses do período / meses com compra; <= 3 A, <= 6 B, resto C.
  * RANK: A = 2,50, B = 1,66, C = 0,83 por dimensão (nota de 3,32 a 10,00).

Carteira (ENTRADA DE PEDIDOS / PEDIDOS SEM NF)
  * A FATURAR = kg pedido - kg faturado - kg cancelado, nunca negativo;
    VALOR A FATURAR = A FATURAR x fator kg do item (VL_PRODUTO / VL_PESO).
  * Pronto em estoque (SALDO) = kg produzido nas OPs do item - kg já atendido.

Metas (META REPRESENTANTE CONSOLIDADO)
  * % META ATINGIDA = (kg faturado líquido + kg a faturar com entrega no mês) / meta kg.
    As metas são as tabelas digitadas no PBIX (ver `representative_goals.py`).

Clientes (CLIENTES NOVOS / CLIENTES INATIVOS)
  * Novo: primeira compra de toda a história dentro do período.
  * Inativo: faixas de dias desde a última compra (até 60, 60-90, 90-120,
    120-180, acima de 180).
"""

import calendar
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.cache import cache

from . import customer_dna as legacy
from . import econodata


ZERO = Decimal("0")
ONE = Decimal("1")
INTERCOMPANY_CUSTOMERS = (1001, 1002, 1004)
RANKING_CACHE_SECONDS = 600

# Códigos de representante que o PBIX ("NOME REPRESENTANTE") consolida em um só.
# A chave é o código secundário; o valor, o código principal (o das metas).
REPRESENTATIVE_ALIASES = {
    10042: 10048,
    10037: 10054,
    10055: 10071,
    1002: 1001,
    1004: 1001,
    10002: 1001,
    10007: 1001,
    10044: 1001,
    10057: 1001,
    10058: 1001,
    10059: 1001,
    10070: 1001,
    10050: 10001,
    10046: 10038,
    10034: 10067,
}
# Nome de exibição de alguns representantes (o apelido do PBIX). Fica num arquivo local, fora do
# repositório, porque tem nomes de pessoas; sem o arquivo vale o nome do cadastro do ERP (E090REP).
REPRESENTATIVE_NAMES_PATH = os.path.join(os.path.dirname(__file__), "data", "representative_names.json")


def _load_display_names(path=REPRESENTATIVE_NAMES_PATH):
    try:
        with open(path, encoding="utf-8") as handle:
            return {int(code): str(name) for code, name in json.load(handle).items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


REPRESENTATIVE_DISPLAY_NAMES = _load_display_names()
# Coluna "EQUIPE INTERNAXEXTERNA" do PBIX.
INTERNAL_TEAM = {1001, 10065}

BUDGET_UNITS = {1: "Matriz", 2: "Ráfia", 3: "Big Bag", 4: "Serafina", 5: "Adesivado", 7: "São Paulo"}
# Produtos que o PBIX manda para a Serafina mesmo sem "LAMINADO" no grupo.
SERAFINA_PRODUCTS = {
    80000101227, 80000101228, 80000300080, 80000300129, 80000300153, 80000302199, 80000302223,
    80000302267, 80000302278, 80000302504, 80000302688, 80000302902, 80000303131, 80000400094,
    80000400096, 80000400098, 80000400216, 80000400308, 80000400325, 80000400359,
}

ABC_WEIGHTS = {"A": Decimal("2.50"), "B": Decimal("1.66"), "C": Decimal("0.83")}
ABC_DIMENSIONS = (
    ("revenue", "Faturamento"),
    ("margin", "Margem"),
    ("lot", "Lote"),
    ("recurrence", "Recorrência"),
)

# Medida "Orientação ABCD": letras na ordem faturamento, margem, lote, recorrência.
ORIENTATION = {
    "AAAA": "Cliente ideal — fidelizar, premiar e ampliar parcerias.",
    "AAAB": "Excelente estabilidade; ativar estímulos para aumentar frequência.",
    "AAAC": "Alto risco de rotatividade; ação imediata de retenção.",
    "AABA": "Expansão de volume e mix — excelente oportunidade.",
    "AABB": "Incentivar aumento gradual de volume.",
    "AABC": "Reengajar ativamente para elevar a recorrência.",
    "AACA": "Oferecer pacotes para aumentar ticket médio.",
    "AACB": "Trabalhar campanhas de frequência e cross-sell.",
    "AACC": "Baixa recorrência — agir para recuperar cliente.",
    "ABAA": "Elevar margem via ajustes de preço e política comercial.",
    "ABAB": "Margem apertada — otimização gradativa necessária.",
    "ABAC": "Atuar rápido para reter e evitar queda de volume.",
    "ABBA": "Consolidar relação e buscar ofertas mais lucrativas.",
    "ABBB": "Manter acompanhamento constante; cliente sólido.",
    "ABBC": "Investigar queda e recuperar relacionamento.",
    "ABCA": "Estimular aumento de volume por pedido.",
    "ABCB": "Incentivar recompras com ofertas direcionadas.",
    "ABCC": "Risco de churn — atuar imediatamente.",
    "ACAA": "Alto volume com baixa margem — revisar preços e custos.",
    "ACAB": "Corrigir preços e reduzir incentivos que comprimem lucro.",
    "ACAC": "Alto esforço com retorno baixo — reavaliar carteira.",
    "ACBA": "Corrigir margem urgentemente — risco financeiro.",
    "ACBB": "Rever políticas comerciais e estrutura de custos.",
    "ACBC": "Avaliar se vale manter na carteira.",
    "ACCA": "Renegociar urgente — recorrente porém pouco rentável.",
    "ACCB": "Baixa prioridade até recuperar margem.",
    "ACCC": "Cliente crítico — considerar descontinuação.",
    "BAAA": "Escalar vendas mantendo alta margem — excelente perfil.",
    "BAAB": "Incentivar frequência e aumentar faturamento.",
    "BAAC": "Criar mecanismos de fidelização e reativação.",
    "BABA": "Ampliar mix e fortalecer relação com o cliente.",
    "BABB": "Crescimento controlado com foco em estabilidade.",
    "BABC": "Reativar cliente e estimular frequência.",
    "BACA": "Aumentar ticket médio — alta margem e boa recorrência.",
    "BACB": "Ativar cross-sell, pacotes e programas de recorrência.",
    "BACC": "Reavaliar esforço comercial — baixa recorrência.",
    "BBAA": "Melhorar margem em cliente de alto volume e recorrência.",
    "BBAB": "Incrementar eficiência operacional.",
    "BBAC": "Estimular recompra para justificar esforço operacional.",
    "BBBA": "Foco em retenção e eficiência — cliente estável.",
    "BBBB": "Estável — manter e monitorar desempenho.",
    "BBBC": "Baixa recorrência — ativar relacionamento.",
    "BBCA": "Aumentar volume por compra — recorrência alta.",
    "BBCB": "Cross-sell e campanhas de reativação.",
    "BBCC": "Baixa prioridade — manter apenas se houver sinergia.",
    "BCAA": "Revisar custos — alto volume e baixa margem.",
    "BCAB": "Revisar preços e descontos.",
    "BCAC": "Risco de baixa rentabilidade — reavaliar foco.",
    "BCBA": "Ajustar preços para corrigir margem.",
    "BCBB": "Reduzir incentivos e recalibrar retorno.",
    "BCBC": "Esforço não compatível com retorno — repensar carteira.",
    "BCCA": "Cliente frequente porém rentabilidade ruim — agir urgente.",
    "BCCB": "Avaliar retorno antes de investir mais.",
    "BCCC": "Cliente de baixa prioridade — considerar saída.",
    "CAAA": "Nicho lucrativo — escalar seletivamente.",
    "CAAB": "Expandir canais e exposição comercial.",
    "CAAC": "Trabalhar frequência — retorno unitário excelente.",
    "CABA": "Crescimento controlado — cliente promissor.",
    "CABB": "Aumentar awareness e presença comercial.",
    "CABC": "Produto de nicho — reavaliar investimento.",
    "CACA": "Excelente margem por unidade — buscar upsell.",
    "CACB": "Cross-sell e programas de relacionamento.",
    "CACC": "Manter como premium — não priorizar.",
    "CBAA": "Aumentar preço gradualmente para melhorar margem.",
    "CBAB": "Otimizar margem via eficiência operacional.",
    "CBAC": "Reavaliar esforço versus retorno.",
    "CBBA": "Manter com baixo custo operacional.",
    "CBBB": "Monitorar e buscar eficiência.",
    "CBBC": "Baixa prioridade — investir somente com sinergia.",
    "CBCA": "Aumentar ticket por compra.",
    "CBCB": "Ações leves de cross-sell para elevar faturamento.",
    "CBCC": "Manter apenas se houver necessidade estratégica.",
    "CCAA": "Alto esforço e baixa rentabilidade — corrigir ou encerrar.",
    "CCAB": "Revisar contratos e reduzir custos de atendimento.",
    "CCAC": "Descontinuação recomendada — retorno insuficiente.",
    "CCBA": "Cortar incentivos — pouco retorno para alto esforço.",
    "CCBB": "Minimizar custos e automatizar atendimento.",
    "CCBC": "Descontinuar se não houver plano estratégico.",
    "CCCA": "Baixa rentabilidade apesar de recorrência — reduzir suporte.",
    "CCCB": "Evitar investimentos — manter apenas o essencial.",
    "CCCC": "Descontinuação total recomendada — baixo retorno em tudo.",
}

INACTIVITY_BANDS = (
    (60, "ate-60", "Até 60 dias"),
    (90, "60-90", "60 a 90 dias"),
    (120, "90-120", "90 a 120 dias"),
    (180, "120-180", "120 a 180 dias"),
    (None, "180+", "Acima de 180 dias"),
)

PERIOD_CHOICES = (
    ("12", "12 meses"),
    ("ytd", "Ano atual"),
    ("ano-anterior", "Ano anterior"),
    ("6", "6 meses"),
    ("3", "3 meses"),
    ("24", "24 meses"),
)
DEFAULT_PERIOD = "12"
MONTH_NAMES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_RETURN_RATIO_SQL = (
    "CASE WHEN NVL(I.QTDFAT, 0) > 0 "
    "THEN LEAST(GREATEST(NVL(I.QTDDEV, 0) / I.QTDFAT, 0), 1) ELSE 0 END"
)

BILLING_SQL = """
SELECT
  N.CODEMP AS EMPRESA,
  N.CODFIL AS FILIAL,
  I.NUMNFV AS NOTA,
  I.SEQIPV AS SEQUENCIA,
  N.DATEMI AS DATA,
  N.CODCLI AS CLIENTE,
  C.NOMCLI AS NOME_CLIENTE,
  C.CGCCPF AS CNPJ,
  C.CIDCLI AS CIDADE,
  C.SIGUFS AS UF,
  C.TIPMER AS MERCADO,
  N.CODREP AS REPRESENTANTE,
  R.NOMREP AS NOME_REPRESENTANTE,
  R.APEREP AS APELIDO_REPRESENTANTE,
  I.CODPRO AS PRODUTO,
  P.DESPRO AS DESCRICAO_PRODUTO,
  I.CPLIPV AS COMPLEMENTO,
  F.DESFAM AS FAMILIA,
  NVL(I.QTDFAT, 0) AS QTD,
  NVL(I.QTDDEV, 0) AS QTD_DEV,
  NVL(I.PESLIQ, 0) AS PESO,
  NVL(I.VLRBRU, 0) AS VALOR,
  NVL(I.VLRICM, 0) AS ICMS,
  NVL(I.VLRPIF, 0) AS PIS,
  NVL(I.VLRCFF, 0) AS COFINS,
  I.NUMPED AS PEDIDO,
  PI.USU_FILPRD AS FILIAL_PRODUCAO,
  CP.DESCPG AS CONDICAO_PAGAMENTO,
  CP.PRZMED AS PRAZO_MEDIO,
  (SELECT SUM(M.VLRMOV) FROM E210MVP M
    WHERE M.CODEMP = I.CODEMP AND M.CODFIL = I.CODFIL AND M.CODSNF = I.CODSNF
      AND M.NUMNFV = I.NUMNFV AND M.SEQIPV = I.SEQIPV AND M.ESTEOS = 'S') AS CPV
FROM E140NFV N
JOIN E140IPV I
  ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
LEFT JOIN E075PRO P ON P.CODEMP = I.CODEMP AND P.CODPRO = I.CODPRO
LEFT JOIN E012FAM F ON F.CODEMP = I.CODEMP AND F.CODFAM = I.CODFAM
LEFT JOIN E085CLI C ON C.CODCLI = N.CODCLI
LEFT JOIN E090REP R ON R.CODREP = N.CODREP
LEFT JOIN E120IPD PI
  ON PI.CODEMP = I.CODEMP AND PI.CODFIL = I.CODFIL AND PI.NUMPED = I.NUMPED AND PI.SEQIPD = I.SEQIPD
LEFT JOIN E120PED PE ON PE.CODEMP = I.CODEMP AND PE.CODFIL = I.CODFIL AND PE.NUMPED = I.NUMPED
LEFT JOIN E028CPG CP ON CP.CODEMP = PE.CODEMP AND CP.CODCPG = PE.CODCPG
WHERE N.SITNFV = '2'
  AND N.CODCLI NOT IN (1001, 1002, 1004)
  AND N.DATEMI >= :start_date
  AND N.DATEMI < :end_date
  AND {scope}
"""

ORDERS_SQL = """
SELECT
  PED.CODEMP AS EMPRESA,
  PED.CODFIL AS FILIAL,
  PED.NUMPED AS PEDIDO,
  ITEM.SEQIPD AS SEQUENCIA,
  PED.DATEMI AS EMISSAO,
  ITEM.DATENT AS ENTREGA,
  ITEM.SITIPD AS SITUACAO_ITEM,
  PED.CODCLI AS CLIENTE,
  C.NOMCLI AS NOME_CLIENTE,
  PED.CODREP AS REPRESENTANTE,
  ITEM.CODPRO AS PRODUTO,
  PROD.DESPRO AS DESCRICAO_PRODUTO,
  FAM.DESFAM AS FAMILIA,
  NVL(ITEM.QTDPED, 0) AS QTD_PEDIDA,
  NVL(ITEM.QTDFAT, 0) AS QTD_FATURADA,
  NVL(ITEM.QTDCAN, 0) AS QTD_CANCELADA,
  NVL(ITEM.QTDABE, 0) AS QTD_ABERTA,
  NVL(ITEM.PREUNI, 0) AS PRECO_UNITARIO,
  NVL(DER.PESBRU, 0) AS PESO_UNITARIO
FROM E120PED PED
JOIN E120IPD ITEM ON ITEM.CODEMP = PED.CODEMP AND ITEM.CODFIL = PED.CODFIL AND ITEM.NUMPED = PED.NUMPED
JOIN E001TNS TNS ON TNS.CODEMP = ITEM.CODEMP AND TNS.CODTNS = ITEM.TNSPRO AND TNS.VENFAT = 'S'
LEFT JOIN E075PRO PROD ON PROD.CODEMP = ITEM.CODEMP AND PROD.CODPRO = ITEM.CODPRO
LEFT JOIN E012FAM FAM ON FAM.CODEMP = PROD.CODEMP AND FAM.CODFAM = PROD.CODFAM
LEFT JOIN E075DER DER ON DER.CODEMP = ITEM.CODEMP AND DER.CODPRO = ITEM.CODPRO AND DER.CODDER = ITEM.CODDER
LEFT JOIN E085CLI C ON C.CODCLI = PED.CODCLI
WHERE PED.CODCLI NOT IN (1001, 1002, 1004)
  AND ITEM.SITIPD NOT IN (5, 9)
  AND (ITEM.SITIPD IN (1, 2) OR (PED.DATEMI >= :start_date AND PED.DATEMI < :end_date))
  AND {scope}
"""

# Quantidade produzida nas OPs (origem 800) dos itens em aberto do escopo. É a
# subconsulta "P" do PBIX; correlacionada item a item ela custava ~0,1 s por
# item (sem índice por pedido no E900COP), agregada de uma vez custa menos de 1 s.
PRODUCTION_SQL = """
SELECT COP.CODEMP AS EMPRESA, COP.CODFIL AS FILIAL, COP.NUMPED AS PEDIDO, QDO.SEQIPD AS SEQUENCIA,
       SUM(EOQ.QTDRE1) AS QTD_PRODUZIDA
FROM E900COP COP
JOIN E900QDO QDO ON QDO.CODEMP = COP.CODEMP AND QDO.CODORI = COP.CODORI AND QDO.NUMORP = COP.NUMORP
JOIN E900EOQ EOQ ON EOQ.CODEMP = COP.CODEMP AND EOQ.CODORI = COP.CODORI AND EOQ.NUMORP = COP.NUMORP
WHERE COP.CODORI = '800'
  AND COP.SITORP NOT IN ('C', 'S')
  AND EOQ.DATREA <> TO_DATE('31/12/1900', 'DD/MM/YYYY')
  AND (COP.CODEMP, COP.CODFIL, COP.NUMPED, QDO.SEQIPD) IN (
    SELECT ITEM.CODEMP, ITEM.CODFIL, ITEM.NUMPED, ITEM.SEQIPD
    FROM E120PED PED
    JOIN E120IPD ITEM ON ITEM.CODEMP = PED.CODEMP AND ITEM.CODFIL = PED.CODFIL AND ITEM.NUMPED = PED.NUMPED
    WHERE ITEM.SITIPD IN (1, 2) AND {scope}
  )
GROUP BY COP.CODEMP, COP.CODFIL, COP.NUMPED, QDO.SEQIPD
"""

RANKING_SQL = f"""
SELECT
  CLIENTE,
  REPRESENTANTE,
  MAX(NOME_REPRESENTANTE) AS NOME_REPRESENTANTE,
  MAX(APELIDO_REPRESENTANTE) AS APELIDO_REPRESENTANTE,
  SUM(LIQUIDO) AS LIQUIDO,
  SUM(GREATEST(LIQUIDO - IMPOSTOS, 0)) AS SEM_IMPOSTOS,
  SUM(PESO_LIQUIDO) AS PESO
FROM (
  SELECT
    N.CODCLI AS CLIENTE,
    N.CODREP AS REPRESENTANTE,
    R.NOMREP AS NOME_REPRESENTANTE,
    R.APEREP AS APELIDO_REPRESENTANTE,
    NVL(I.VLRBRU, 0) * (1 - {_RETURN_RATIO_SQL}) AS LIQUIDO,
    NVL(I.PESLIQ, 0) * (1 - {_RETURN_RATIO_SQL}) AS PESO_LIQUIDO,
    NVL(I.VLRICM, 0) + NVL(I.VLRPIF, 0) + NVL(I.VLRCFF, 0) AS IMPOSTOS
  FROM E140NFV N
  JOIN E140IPV I
    ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
  JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
  LEFT JOIN E090REP R ON R.CODREP = N.CODREP
  WHERE N.SITNFV = '2'
    AND N.CODCLI NOT IN (1001, 1002, 1004)
    AND N.DATEMI >= :start_date
    AND N.DATEMI < :end_date
)
WHERE LIQUIDO > 0
GROUP BY CLIENTE, REPRESENTANTE
"""

FIRST_LAST_SQL = """
SELECT N.CODCLI AS CLIENTE, MIN(N.DATEMI) AS PRIMEIRA_COMPRA, MAX(N.DATEMI) AS ULTIMA_COMPRA
FROM E140NFV N
WHERE N.SITNFV = '2'
  AND N.CODCLI IN ({codes})
  AND EXISTS (
    SELECT 1 FROM E140IPV I
    JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
    WHERE I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
  )
GROUP BY N.CODCLI
"""


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def _num(value):
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _add_months(value, delta):
    total = value.year * 12 + (value.month - 1) + delta
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _month_key(value):
    return value.year * 12 + value.month - 1


def _month_label(key):
    year, month = divmod(key, 12)
    return f"{MONTH_NAMES[month]}/{str(year)[2:]}"


def _ratio(numerator, denominator):
    numerator, denominator = _num(numerator), _num(denominator)
    return numerator / denominator if denominator else None


def _pct(value, decimals=1, signed=False):
    if value is None:
        return "n/d"
    number = float(value) * 100
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:.{decimals}f}%".replace(".", ",")


def _change(current, previous):
    current, previous = _num(current), _num(previous)
    if previous == 0:
        return None
    return (current - previous) / previous


def _money(value, compact=True):
    return legacy._format_money(value, compact=compact)


def _kg(value):
    return legacy._format_weight(value)


def _fator(value):
    return "n/d" if value is None else legacy._format_money(value)


def _display_date(value):
    return legacy._display_date(value)


def primary_rep(code):
    code = _int(code)
    if code is None:
        return None
    return REPRESENTATIVE_ALIASES.get(code, code)


def rep_codes(primary):
    return sorted({primary, *[alias for alias, target in REPRESENTATIVE_ALIASES.items() if target == primary]})


def rep_team(primary):
    return "Equipe interna" if primary in INTERNAL_TEAM else "Equipe externa"


def _in_clause(prefix, values):
    values = sorted({int(value) for value in values})
    params = {f"{prefix}{index}": value for index, value in enumerate(values)}
    return ", ".join(f":{prefix}{index}" for index in range(len(values))), params


def _run(statements):
    """Executa {nome: (sql, params)} em uma conexão só, devolvendo linhas como dicts."""
    connection = legacy._oracle_connection_safe()
    results = {}
    try:
        for name, (sql, params) in statements.items():
            cursor = connection.cursor()
            try:
                cursor.execute(sql, params)
                columns = [column[0].lower() for column in cursor.description]
                results[name] = [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                cursor.close()
    finally:
        connection.close()
    return results


# ---------------------------------------------------------------------------
# Regras do PBIX (puras, testáveis sem Oracle)
# ---------------------------------------------------------------------------

def product_group(family, complement):
    """Coluna GRUPO 4.1 do PBIX: GRUPO1 + GRUPO3 + GRUPO2 (ex.: BOBINA MONO IMPRESSO)."""
    family = (family or "").upper()
    if "FILME" in family or "TECIDO" in family:
        first = "BOBINA"
    elif "SACO" in family:
        first = "SACO"
    elif "FOLHA" in family:
        first = "FOLHA"
    elif "REFUGO" in family:
        first = "REFUGO"
    else:
        first = "OUTROS"
    finish = "LISO" if "LISO" in family else "IMPRESSO" if "IMPRESSO" in family else "LISO"
    # Text.BetweenDelimiters(DS_PRODUTO, "+", "+", 1, 0): o trecho entre o 2º e o 3º "+".
    parts = (complement or "").split("+")
    between = parts[2] if len(parts) >= 3 else ""
    if "A" in between:
        structure = "BI LAMINADO"
    elif "LAMINADO" in family:
        structure = "LAMINADO"
    elif "CONVENCIONAL " in family:
        structure = "CONVENCIONAL"
    elif "ADESIVADO" in family:
        structure = "ADESIVADO"
    else:
        structure = "MONO"
    return f"{first} {structure} {finish}"


def budget_unit(family, group, production_branch, branch, product_code):
    """Coluna FILIAL ORÇ do PBIX: em qual unidade do orçamento a venda conta."""
    family = (family or "").upper()
    production_branch = _int(production_branch)
    branch = _int(branch)
    if "BIG BAG" in family:
        return 3
    if "ADESIVADO" in group:
        return 5
    if production_branch in (1, 2, 3, 4):
        return production_branch
    if branch == 2:
        return 2
    if "LAMINADO" in group or _int(product_code) in SERAFINA_PRODUCTS:
        return 4
    return branch


def billing_line(row):
    quantity = _num(row.get("qtd"))
    ratio = ZERO
    if quantity > 0:
        ratio = min(max(_num(row.get("qtd_dev")) / quantity, ZERO), ONE)
    gross = _num(row.get("valor"))
    weight = _num(row.get("peso"))
    returned_value = gross * ratio
    returned_weight = weight * ratio
    net = gross - returned_value
    taxes = _num(row.get("icms")) + _num(row.get("pis")) + _num(row.get("cofins"))
    net_of_taxes = max(net - taxes, ZERO)
    cpv = None if row.get("cpv") is None else _num(row.get("cpv")) * (ONE - ratio)
    family = (row.get("familia") or "").strip()
    group = product_group(family, row.get("complemento"))
    company, branch = row.get("empresa"), row.get("filial")
    order = row.get("pedido")
    return {
        "date": _date(row.get("data")),
        "customer": _int(row.get("cliente")),
        "customer_name": (row.get("nome_cliente") or "").strip(),
        "cnpj": row.get("cnpj"),
        "city": (row.get("cidade") or "").strip(),
        "state": (row.get("uf") or "").strip(),
        "market": row.get("mercado"),
        "rep_code": _int(row.get("representante")),
        "rep_primary": primary_rep(row.get("representante")),
        "rep_name": (row.get("nome_representante") or "").strip(),
        "invoice": f"{company}-{branch}-{row.get('nota')}",
        "order": f"{company}-{branch}-{order}" if order else None,
        "product_code": str(row.get("produto") or ""),
        "product_name": (row.get("descricao_produto") or row.get("complemento") or "").strip(),
        "family": family,
        "group": group,
        "unit": budget_unit(family, group, row.get("filial_producao"), branch, row.get("produto")),
        "gross": gross,
        "returned_value": returned_value,
        "returned_weight": returned_weight,
        "net": net,
        "weight": weight,
        "net_weight": weight - returned_weight,
        "taxes": taxes,
        "net_of_taxes": net_of_taxes,
        "cpv": cpv,
        "payment_condition": (row.get("condicao_pagamento") or "").strip() or None,
        "payment_days": _num(row.get("prazo_medio")) if row.get("prazo_medio") is not None else None,
        "included": net > 0,
    }


def order_item(row, today=None):
    today = today or date.today()
    unit_weight = _num(row.get("peso_unitario"))
    ordered_qty = _num(row.get("qtd_pedida"))
    billed_qty = _num(row.get("qtd_faturada"))
    canceled_qty = _num(row.get("qtd_cancelada"))
    open_qty = _num(row.get("qtd_aberta"))
    ordered_kg = unit_weight * ordered_qty
    value = ordered_qty * _num(row.get("preco_unitario"))
    fator = value / ordered_kg if ordered_kg else ZERO
    to_bill_kg = max(ordered_kg - unit_weight * billed_qty - unit_weight * canceled_qty, ZERO)
    attended_kg = unit_weight * (ordered_qty - open_qty)
    produced_kg = unit_weight * _num(row.get("qtd_produzida"))
    ready_kg = min(max(produced_kg - attended_kg, ZERO), to_bill_kg)
    situation = _int(row.get("situacao_item"))
    delivery = _date(row.get("entrega"))
    is_open = situation in (1, 2) and to_bill_kg > 0
    company, branch = row.get("empresa"), row.get("filial")
    return {
        "order": f"{company}-{branch}-{row.get('pedido')}",
        "number": row.get("pedido"),
        "sequence": row.get("sequencia"),
        "issue_date": _date(row.get("emissao")),
        "delivery_date": delivery,
        "customer": _int(row.get("cliente")),
        "customer_name": (row.get("nome_cliente") or "").strip(),
        "rep_primary": primary_rep(row.get("representante")),
        "product_name": (row.get("descricao_produto") or "").strip() or str(row.get("produto") or ""),
        # Na carteira o PBIX lê o "+...+" da descrição do produto (E075PRO.DESPRO).
        "group": product_group(row.get("familia"), row.get("descricao_produto")),
        "ordered_kg": ordered_kg,
        "value": value,
        "fator": fator,
        "to_bill_kg": to_bill_kg if is_open else ZERO,
        "to_bill_value": to_bill_kg * fator if is_open else ZERO,
        "ready_kg": ready_kg if is_open else ZERO,
        "is_open": is_open,
        "is_overdue": bool(is_open and delivery and delivery < today),
    }


def summarize(lines):
    """Medidas do PBIX sobre as linhas que entram na conta (VL_FAT-DEV > 0)."""
    total = defaultdict(lambda: ZERO)
    invoices, orders, customers, months, products = set(), set(), set(), set(), set()
    cpv_known = False
    for line in lines:
        total["returned_value"] += line["returned_value"]
        total["returned_weight"] += line["returned_weight"]
        total["gross"] += line["gross"]
        if not line["included"]:
            continue
        total["net"] += line["net"]
        total["net_of_taxes"] += line["net_of_taxes"]
        total["net_weight"] += line["net_weight"]
        total["taxes"] += line["taxes"]
        if line["cpv"] is not None:
            cpv_known = True
            total["cpv"] += line["cpv"]
            total["cpv_base"] += line["net_of_taxes"]
        if line["payment_days"] is not None:
            total["payment_weighted"] += line["payment_days"] * line["net"]
            total["payment_base"] += line["net"]
        invoices.add(line["invoice"])
        if line["order"]:
            orders.add(line["order"])
        if line["customer"] is not None:
            customers.add(line["customer"])
        if line["date"]:
            months.add(_month_key(line["date"]))
        products.add(line["product_code"])
    margin = None
    if cpv_known and total["cpv_base"] > 0:
        margin = ONE - total["cpv"] / total["cpv_base"]
    return {
        "net": total["net"],
        "net_of_taxes": total["net_of_taxes"],
        "net_weight": total["net_weight"],
        "gross": total["gross"],
        "returned_value": total["returned_value"],
        "returned_weight": total["returned_weight"],
        "returned_share": _ratio(total["returned_value"], total["gross"]),
        "cpv": total["cpv"] if cpv_known else None,
        "margin": margin,
        "cost_coverage": _ratio(total["cpv_base"], total["net_of_taxes"]),
        "fator_kg": _ratio(total["net"], total["net_weight"]),
        "fator_kg_clean": _ratio(total["net_of_taxes"], total["net_weight"]),
        "payment_days": _ratio(total["payment_weighted"], total["payment_base"]),
        "invoices": len(invoices),
        "orders": len(orders),
        "customers": len(customers),
        "active_months": len(months),
        "products": len(products),
        "ticket": _ratio(total["net"], len(orders)) if orders else None,
    }


def pareto(amounts):
    """Curva ABC do PBIX: posição densa, acumulado inclui o próprio item."""
    positive = {key: _num(value) for key, value in amounts.items() if _num(value) > 0}
    total = sum(positive.values(), ZERO)
    distinct = sorted(set(positive.values()), reverse=True)
    cumulative_by_value = {}
    running = ZERO
    for value in distinct:
        running += value * sum(1 for amount in positive.values() if amount == value)
        cumulative_by_value[value] = running
    position_by_value = {value: index + 1 for index, value in enumerate(distinct)}
    result = {}
    for key, value in positive.items():
        cumulative = cumulative_by_value[value] / total if total else ZERO
        result[key] = {
            "class": "A" if cumulative <= Decimal("0.8") else "B" if cumulative <= Decimal("0.95") else "C",
            "position": position_by_value[value],
            "share": value / total if total else ZERO,
            "cumulative": cumulative,
            "amount": value,
        }
    return result


def margin_class(margin):
    if margin is None:
        return None
    if margin <= Decimal("0.05"):
        return "C"
    if margin <= Decimal("0.15"):
        return "B"
    return "A"


def lot_class(monthly_kg):
    if monthly_kg is None:
        return None
    if monthly_kg <= 1000:
        return "C"
    if monthly_kg <= 2000:
        return "B"
    return "A"


def recurrence(window_months, active_months):
    if not active_months:
        return None, None, None
    average = Decimal(window_months) / Decimal(active_months)
    if average <= 1:
        label = "Compra mensal"
    elif average <= 2:
        label = "Compra bimestral"
    elif average <= 3:
        label = "Compra trimestral"
    elif average <= 6:
        label = "Compra sazonal (até 6 meses)"
    elif average <= 12:
        label = "Compra anual"
    else:
        label = "Muito esporádica"
    letter = "A" if average <= 3 else "B" if average <= 6 else "C"
    return letter, label, average


def classify(letters):
    """Classificação Geral + RANK + Orientação ABCD a partir das 4 letras."""
    code = "".join(letters.get(key) or "–" for key, _label in ABC_DIMENSIONS)
    complete = all(letters.get(key) for key, _label in ABC_DIMENSIONS)
    score = sum((ABC_WEIGHTS[letters[key]] for key, _label in ABC_DIMENSIONS), ZERO) if complete else None
    if complete:
        orientation = ORIENTATION.get(code, "Sem orientação definida")
    else:
        missing = [label.lower() for key, label in ABC_DIMENSIONS if not letters.get(key)]
        orientation = f"Classificação incompleta: sem base para {', '.join(missing)} no período."
    return {
        "code": code,
        "complete": complete,
        "score": score,
        "score_display": f"{float(score):.2f}".replace(".", ",") if score is not None else "n/d",
        "orientation": orientation,
    }


def classify_customer(lines, window, company_abc):
    """Letras, nota e orientação de um cliente a partir das linhas do período."""
    totals = summarize(lines)
    customer_code = next((line["customer"] for line in lines if line["customer"] is not None), None)
    revenue = company_abc.get(customer_code) if customer_code is not None else None
    monthly_kg = totals["net_weight"] / totals["active_months"] if totals["active_months"] else None
    rec_letter, rec_label, rec_average = recurrence(window["months"], totals["active_months"])
    letters = {
        "revenue": revenue["class"] if revenue else None,
        "margin": margin_class(totals["margin"]),
        "lot": lot_class(monthly_kg),
        "recurrence": rec_letter,
    }
    result = classify(letters)
    result.update(
        {
            "letters": letters,
            "dimensions": [
                {
                    "key": "revenue",
                    "label": "Faturamento",
                    "letter": letters["revenue"],
                    "detail": (
                        f"{revenue['position']}º no ranking · {_pct(revenue['cumulative'])} acumulado"
                        if revenue else "Sem faturamento no período"
                    ),
                },
                {
                    "key": "margin",
                    "label": "Margem",
                    "letter": letters["margin"],
                    "detail": f"{_pct(totals['margin'])} sobre o faturamento sem impostos" if totals["margin"] is not None else "Sem custo (CPV) no período",
                },
                {
                    "key": "lot",
                    "label": "Lote",
                    "letter": letters["lot"],
                    "detail": f"{_kg(monthly_kg)} por mês com compra" if monthly_kg is not None else "Sem volume no período",
                },
                {
                    "key": "recurrence",
                    "label": "Recorrência",
                    "letter": letters["recurrence"],
                    "detail": (
                        f"{rec_label} · {totals['active_months']} de {window['months']} meses"
                        if rec_letter else "Sem compras no período"
                    ),
                },
            ],
            "monthly_kg": monthly_kg,
            "recurrence_label": rec_label,
            "recurrence_average": rec_average,
            "revenue_rank": revenue,
            "totals": totals,
        }
    )
    return result


def inactivity_band(days):
    if days is None:
        return None
    for limit, key, label in INACTIVITY_BANDS:
        if limit is None or days <= limit:
            return {"key": key, "label": label}
    return None


# ---------------------------------------------------------------------------
# Período
# ---------------------------------------------------------------------------

def resolve_window(period_key=None, range_start=None, range_end=None, today=None):
    """Recorte do painel: início inclusivo, fim exclusivo e quantos meses ele cobre."""
    today = today or date.today()
    tomorrow = today + timedelta(days=1)
    month_start = today.replace(day=1)
    labels = dict(PERIOD_CHOICES)
    key = (period_key or DEFAULT_PERIOD).strip()
    if range_start and range_end:
        key = "range"
        start, end = range_start, range_end + timedelta(days=1)
        label = f"{range_start:%d/%m/%Y} a {range_end:%d/%m/%Y}"
    elif key == "ytd":
        start, end, label = date(today.year, 1, 1), tomorrow, f"Ano atual ({today.year})"
    elif key == "ano-anterior":
        start, end, label = date(today.year - 1, 1, 1), date(today.year, 1, 1), f"Ano anterior ({today.year - 1})"
    elif key in ("3", "6", "24"):
        months = int(key)
        start, end, label = _add_months(month_start, -(months - 1)), tomorrow, labels[key]
    else:
        key = DEFAULT_PERIOD
        start, end, label = _add_months(month_start, -11), tomorrow, labels[DEFAULT_PERIOD]
    last_day = end - timedelta(days=1)
    months = _month_key(last_day) - _month_key(start) + 1
    query = (
        f"inicio={range_start.isoformat()}&fim={range_end.isoformat()}"
        if key == "range" else f"periodo={key}"
    )
    return {
        "key": key,
        "label": label,
        "start": start,
        "end": end,
        "months": max(months, 1),
        "range_label": f"{start:%d/%m/%Y} a {last_day:%d/%m/%Y}",
        "previous_start": _add_months(start, -12),
        "previous_end": _add_months(end, -12),
        "query": query,
        "choices": [{"key": choice, "label": text} for choice, text in PERIOD_CHOICES],
    }


def _load_bounds(window, today=None):
    """O que buscar no Oracle: o período, o mesmo período do ano anterior e 24 meses de série."""
    today = today or date.today()
    start = min(window["previous_start"], _add_months(today.replace(day=1), -23))
    return start, max(window["end"], today + timedelta(days=1))


def _in_window(value, start, end):
    return bool(value and start <= value < end)


# ---------------------------------------------------------------------------
# Ranking da empresa (curva ABC de clientes e de representantes)
# ---------------------------------------------------------------------------

def company_ranking(window):
    cache_key = f"commercial-dna:ranking:{window['start']}:{window['end']}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    rows = _run({"ranking": (RANKING_SQL, {"start_date": window["start"], "end_date": window["end"]})})["ranking"]
    customers = defaultdict(lambda: ZERO)
    reps = {}
    for row in rows:
        customer = _int(row.get("cliente"))
        primary = primary_rep(row.get("representante"))
        amount = _num(row.get("sem_impostos"))
        if customer is not None:
            customers[customer] += amount
        if primary is None:
            continue
        rep = reps.setdefault(
            primary,
            {"code": primary, "name": "", "nickname": "", "net_of_taxes": ZERO, "net": ZERO, "weight": ZERO, "customers": set()},
        )
        rep["net_of_taxes"] += amount
        rep["net"] += _num(row.get("liquido"))
        rep["weight"] += _num(row.get("peso"))
        if customer is not None:
            rep["customers"].add(customer)
        if _int(row.get("representante")) == primary or not rep["name"]:
            rep["name"] = (row.get("nome_representante") or "").strip() or rep["name"]
            rep["nickname"] = (row.get("apelido_representante") or "").strip() or rep["nickname"]
    for rep in reps.values():
        rep["customers"] = len(rep["customers"])
        rep["display_name"] = REPRESENTATIVE_DISPLAY_NAMES.get(rep["code"]) or rep["name"] or f"Representante {rep['code']}"
    result = {
        "customer_abc": pareto(customers),
        "rep_abc": pareto({code: rep["net_of_taxes"] for code, rep in reps.items()}),
        "reps": reps,
        "total_net_of_taxes": sum(customers.values(), ZERO),
        "customers_count": len(customers),
    }
    cache.set(cache_key, result, RANKING_CACHE_SECONDS)
    return result


def representative_choices(ranking):
    abc = ranking["rep_abc"]
    items = []
    for code, rep in ranking["reps"].items():
        position = abc.get(code)
        items.append(
            {
                "code": code,
                "name": rep["display_name"],
                "nickname": rep["nickname"] if rep["nickname"] and rep["nickname"] != rep["name"] else "",
                "class": position["class"] if position else "–",
                "position": position["position"] if position else None,
                "revenue_display": _money(rep["net"]),
                "customers": rep["customers"],
            }
        )
    items.sort(key=lambda item: (item["position"] is None, item["position"] or 0, item["name"]))
    return items


# ---------------------------------------------------------------------------
# Blocos comuns às duas telas
# ---------------------------------------------------------------------------

def _monthly_series(lines, today, months=24):
    last = _month_key(today)
    first = last - months + 1
    buckets = defaultdict(lambda: {"net": ZERO, "weight": ZERO, "net_of_taxes": ZERO, "orders": set()})
    for line in lines:
        if not line["included"] or not line["date"]:
            continue
        key = _month_key(line["date"])
        if first - 12 <= key <= last:
            bucket = buckets[key]
            bucket["net"] += line["net"]
            bucket["weight"] += line["net_weight"]
            bucket["net_of_taxes"] += line["net_of_taxes"]
            if line["order"]:
                bucket["orders"].add(line["order"])
    series = []
    for key in range(first, last + 1):
        bucket = buckets.get(key)
        previous = buckets.get(key - 12)
        net = bucket["net"] if bucket else ZERO
        weight = bucket["weight"] if bucket else ZERO
        series.append(
            {
                "label": _month_label(key),
                "revenue": float(net),
                "weight": float(weight),
                "fator": float(net / weight) if weight else None,
                "orders": len(bucket["orders"]) if bucket else 0,
                "previous_revenue": float(previous["net"]) if previous else None,
                "tick": (key - first) % 3 == 0 or key == last,
            }
        )
    return series


def _mix(lines, limit=8):
    groups = defaultdict(lambda: {"net": ZERO, "weight": ZERO})
    products = defaultdict(lambda: {"net": ZERO, "weight": ZERO, "name": "", "group": ""})
    total = ZERO
    for line in lines:
        if not line["included"]:
            continue
        total += line["net"]
        groups[line["group"]]["net"] += line["net"]
        groups[line["group"]]["weight"] += line["net_weight"]
        product = products[line["product_code"]]
        product["net"] += line["net"]
        product["weight"] += line["net_weight"]
        product["name"] = product["name"] or line["product_name"] or line["product_code"]
        product["group"] = line["group"]

    def rows(source, with_name=False):
        ordered = sorted(source.items(), key=lambda item: item[1]["net"], reverse=True)[:limit]
        result = []
        for key, values in ordered:
            share = values["net"] / total if total else ZERO
            result.append(
                {
                    "key": key,
                    "name": values["name"] if with_name else key.title(),
                    "group": values.get("group", "").title() if with_name else "",
                    "revenue_display": _money(values["net"]),
                    "weight_display": _kg(values["weight"]),
                    "fator_display": _fator(_ratio(values["net"], values["weight"])),
                    "share": round(float(share) * 100, 1),
                    "share_display": _pct(share),
                }
            )
        return result

    return {"groups": rows(groups), "products": rows(products, with_name=True), "groups_count": len(groups)}


def _portfolio(orders, window, limit=10):
    open_items = [item for item in orders if item["is_open"]]
    to_bill_kg = sum((item["to_bill_kg"] for item in open_items), ZERO)
    to_bill_value = sum((item["to_bill_value"] for item in open_items), ZERO)
    ready_kg = sum((item["ready_kg"] for item in open_items), ZERO)
    overdue = [item for item in open_items if item["is_overdue"]]
    entered = [
        item for item in orders
        if _in_window(item["issue_date"], window["start"], window["end"])
    ]
    entered_kg = sum((item["ordered_kg"] for item in entered), ZERO)
    entered_value = sum((item["value"] for item in entered), ZERO)
    ordered = sorted(open_items, key=lambda item: (item["delivery_date"] or date.max, -item["to_bill_kg"]))
    return {
        "open_items": len(open_items),
        "open_orders": len({item["order"] for item in open_items}),
        "to_bill_kg": to_bill_kg,
        "to_bill_kg_display": _kg(to_bill_kg),
        "to_bill_value_display": _money(to_bill_value),
        "ready_kg_display": _kg(ready_kg),
        "ready_share": _ratio(ready_kg, to_bill_kg),
        "ready_share_display": _pct(_ratio(ready_kg, to_bill_kg)),
        "overdue_items": len(overdue),
        "overdue_kg_display": _kg(sum((item["to_bill_kg"] for item in overdue), ZERO)),
        "entered_orders": len({item["order"] for item in entered}),
        "entered_kg_display": _kg(entered_kg),
        "entered_value_display": _money(entered_value),
        "entered_fator_display": _fator(_ratio(entered_value, entered_kg)),
        "items": [
            {
                "number": item["number"],
                "customer": item["customer"],
                "customer_name": item["customer_name"],
                "product_name": item["product_name"],
                "delivery_display": _display_date(item["delivery_date"]),
                "to_bill_kg_display": _kg(item["to_bill_kg"]),
                "to_bill_value_display": _money(item["to_bill_value"]),
                "ready_pct": round(float(_ratio(item["ready_kg"], item["to_bill_kg"]) or 0) * 100),
                "is_overdue": item["is_overdue"],
            }
            for item in ordered[:limit]
        ],
    }


def _kpis(current, previous):
    def delta(key):
        change = _change(current[key], previous[key]) if current[key] is not None and previous[key] is not None else None
        return {"value": change, "display": _pct(change, signed=True), "tone": "neutral" if change is None else "up" if change >= 0 else "down"}

    return {
        "net_display": _money(current["net"]),
        "net_of_taxes_display": _money(current["net_of_taxes"]),
        "weight_display": _kg(current["net_weight"]),
        "fator_display": _fator(current["fator_kg"]),
        "fator_clean_display": _fator(current["fator_kg_clean"]),
        "margin_display": _pct(current["margin"]),
        "cost_coverage_display": _pct(current["cost_coverage"], decimals=0),
        "cost_partial": current["cost_coverage"] is not None and current["cost_coverage"] < Decimal("0.95"),
        "orders": current["orders"],
        "invoices": current["invoices"],
        "ticket_display": _money(current["ticket"]) if current["ticket"] is not None else "n/d",
        "returned_display": _money(current["returned_value"]),
        "returned_share_display": _pct(current["returned_share"], decimals=2),
        "payment_days_display": f"{float(current['payment_days']):.0f} dias" if current["payment_days"] is not None else "n/d",
        "products": current["products"],
        "deltas": {
            "net": delta("net"),
            "weight": delta("net_weight"),
            "fator": delta("fator_kg"),
            "net_of_taxes": delta("net_of_taxes"),
            "orders": delta("orders"),
        },
        "previous_net_display": _money(previous["net"]),
        "previous_weight_display": _kg(previous["net_weight"]),
    }


def _split_by_window(lines, window):
    current = [line for line in lines if _in_window(line["date"], window["start"], window["end"])]
    previous = [line for line in lines if _in_window(line["date"], window["previous_start"], window["previous_end"])]
    return current, previous


def _period_context(window):
    return {key: window[key] for key in ("key", "label", "range_label", "months", "query", "choices")} | {
        "start_iso": window["start"].isoformat(),
        "end_iso": (window["end"] - timedelta(days=1)).isoformat(),
        "previous_label": f"{window['previous_start']:%d/%m/%Y} a {window['previous_end'] - timedelta(days=1):%d/%m/%Y}",
    }


def _orders_with_production(order_rows, production_rows, today):
    produced = {
        (row.get("empresa"), row.get("filial"), row.get("pedido"), row.get("sequencia")): row.get("qtd_produzida")
        for row in production_rows
    }
    items = []
    for row in order_rows:
        key = (row.get("empresa"), row.get("filial"), row.get("pedido"), row.get("sequencia"))
        items.append(order_item({**row, "qtd_produzida": produced.get(key)}, today))
    return items


def _first_last(customers):
    if not customers:
        return {}
    placeholders, params = _in_clause("c", customers)
    rows = _run({"first_last": (FIRST_LAST_SQL.format(codes=placeholders), params)})["first_last"]
    return {
        _int(row["cliente"]): {"first": _date(row.get("primeira_compra")), "last": _date(row.get("ultima_compra"))}
        for row in rows
    }


# ---------------------------------------------------------------------------
# DNA do Cliente v2
# ---------------------------------------------------------------------------

def only_groups(lines, groups):
    """Linhas só dos grupos de produto escolhidos (filtro por clique no mix do DNA)."""
    if not groups:
        return lines
    wanted = {str(group) for group in groups}
    return [line for line in lines if str(line["group"]) in wanted]


def load_customer_dna_v2(customer_id, view_mode="individual", member_customer_id=None,
                         period_key=None, range_start=None, range_end=None, today=None, groups=None):
    today = today or date.today()
    window = resolve_window(period_key, range_start, range_end, today=today)
    scope = legacy._resolve_customer_scope(customer_id, view_mode, member_customer_id)
    codes = scope["customer_codes"]
    load_start, load_end = _load_bounds(window, today)
    placeholders, params = _in_clause("c", codes)
    dated = {**params, "start_date": load_start, "end_date": load_end}
    quality_scope = {
        "complaints": legacy.CUSTOMER_COMPLAINTS_SQL.replace("A.USU_CODCLI = :customer_id", f"A.USU_CODCLI IN ({placeholders})"),
        "returns": legacy.CUSTOMER_RETURNS_SQL.replace("A.USU_CODCLI = :customer_id", f"A.USU_CODCLI IN ({placeholders})"),
        "cliches": legacy.CUSTOMER_CLICHES_SQL.replace("A.USU_CODCLI = :customer_id", f"A.USU_CODCLI IN ({placeholders})"),
    }
    sources = _run(
        {
            "billing": (BILLING_SQL.format(scope=f"N.CODCLI IN ({placeholders})"), dated),
            "orders": (ORDERS_SQL.format(scope=f"PED.CODCLI IN ({placeholders})"), dated),
            "production": (PRODUCTION_SQL.format(scope=f"PED.CODCLI IN ({placeholders})"), params),
            "first_last": (FIRST_LAST_SQL.format(codes=placeholders), params),
            **{name: (sql, params) for name, sql in quality_scope.items()},
        }
    )
    all_lines = [billing_line(row) for row in sources["billing"]]
    lines = only_groups(all_lines, groups)
    first_last = {
        _int(row["cliente"]): {"first": _date(row.get("primeira_compra")), "last": _date(row.get("ultima_compra"))}
        for row in sources["first_last"]
    }
    if not lines and not first_last:
        return None

    ranking = company_ranking(window)
    current, previous = _split_by_window(lines, window)
    totals = summarize(current)
    previous_totals = summarize(previous)
    classification = classify_customer(current, window, _group_abc(ranking, codes, scope))
    identity = max(lines, key=lambda line: line["date"] or date.min) if lines else {}
    reps = defaultdict(lambda: ZERO)
    for line in current or lines:
        if line["rep_primary"] is not None:
            reps[line["rep_primary"]] += line["net"]
    main_rep = max(reps, key=reps.get) if reps else None
    rep_info = ranking["reps"].get(main_rep) if main_rep else None

    first_purchase = min((value["first"] for value in first_last.values() if value["first"]), default=None)
    last_purchase = max((value["last"] for value in first_last.values() if value["last"]), default=None)
    days_since = (today - last_purchase).days if last_purchase else None
    orders = _orders_with_production(sources["orders"], sources["production"], today)
    operational = legacy._build_operational_data(
        _filter_events(sources["complaints"], "data_reclamacao", window),
        _filter_events(sources["returns"], "data_devolucao", window),
        _filter_events(sources["cliches"], "data_despache", window),
        totals["net"],
    )
    customer_name = identity.get("customer_name") or f"Cliente {customer_id}"
    if scope["mode"] == "group" and not scope["selected_member_id"] and scope["members"]:
        anchor = next((member for member in scope["members"] if member["code"] == customer_id), scope["members"][0])
        customer_name = anchor["name"]

    return {
        "customer": {
            "code": customer_id,
            "name": customer_name,
            "cnpj": legacy.format_cnpj(identity.get("cnpj")) or "Não informado",
            "city": identity.get("city") or "-",
            "state": identity.get("state") or "-",
            "market": identity.get("market") or "Não informado",
            "rep_code": main_rep,
            "rep_name": (rep_info or {}).get("display_name") or (identity.get("rep_name") or "Não informado"),
            "rep_team": rep_team(main_rep) if main_rep else "",
        },
        "group": {
            "is_group_view": scope["mode"] == "group",
            "root_display": scope.get("root_display") or "",
            "members": scope["members"],
            "members_count": len(scope["members"]),
            "selected_member_id": scope["selected_member_id"],
        },
        "period": _period_context(window),
        "kpis": _kpis(totals, previous_totals),
        "has_sales": totals["net"] > 0,
        "classification": classification,
        "ranking": {
            "customers_count": ranking["customers_count"],
            "share_display": _pct(classification["revenue_rank"]["share"], decimals=2) if classification["revenue_rank"] else "n/d",
        },
        "relationship": {
            "first_purchase": _display_date(first_purchase),
            "last_purchase": _display_date(last_purchase),
            "days_since": days_since,
            "band": inactivity_band(days_since),
            "is_new": bool(first_purchase and _in_window(first_purchase, window["start"], window["end"])),
            "years": (
                f"{(today - first_purchase).days / 365.25:.1f}".replace(".", ",") if first_purchase else None
            ),
        },
        "series": _monthly_series(lines, today),
        "mix": _mix(_split_by_window(all_lines, window)[0] if groups else current),
        "portfolio": _portfolio(orders, window),
        "operational": operational,
        # Dados da Econodata já comprados (só cache: abrir a tela nunca consulta a API).
        # A participação usa o faturamento do período anualizado, para comparar
        # com o faturamento anual estimado do grupo.
        "market": _market_display(econodata.market_view(
            _scope_cnpjs(scope, identity.get("cnpj")), totals["net"] * 12 / window["months"], today
        )),
    }


UNIT_TYPES = {"M": "Matriz", "F": "Filial", "MATRIZ": "Matriz", "FILIAL": "Filial"}
COMPANY_SIZES = {"MICRO": "Micro", "PEQUENA": "Pequena", "MEDIA": "Média", "MÉDIA": "Média", "GRANDE": "Grande"}


def int_display(value):
    """Inteiro com ponto de milhar (9.260)."""
    return f"{int(value or 0):,}".replace(",", ".")


def _share_display(share):
    """Fatia em %: com duas casas quando é pequena, para não virar "0,0%"."""
    if share is None:
        return None
    if share < Decimal("0.01"):
        return "< 0,01%"
    return (f"{share:.2f}%" if share < 1 else f"{share:.1f}%").replace(".", ",")


def _market_display(market):
    """Textos prontos para o card de mercado (só dados já guardados da Econodata)."""
    for profile in market["profiles"]:
        profile["approx_display"] = int_display(profile["approx_tokens"])
    for card in filter(None, [market["main"], *market["cards"]]):
        size = (card["porte"] or "").strip().upper()
        card["size_label"] = COMPANY_SIZES.get(size, size.capitalize()) or card["enquadramento_porte"]
        card["capital_display"] = _money(card["capital_social"]) if card["capital_social"] is not None else None
        card["revenue_display"] = _money(card["faturamento_grupo"]) if card["faturamento_grupo"] is not None else None
        card["opened_display"] = _display_date(card["data_abertura"]) if card["data_abertura"] else None
        card["years_display"] = f"{card['anos_abertura']:.1f}".replace(".", ",") if card["anos_abertura"] is not None else None
        card["consulted_display"] = card["consultado_em"].strftime("%d/%m/%Y") if card["consultado_em"] else None
        card["headcount_display"] = (
            legacy._format_number(card["funcionarios_grupo"], 0) if card["funcionarios_grupo"] is not None else None
        )
        card["unit_type_label"] = UNIT_TYPES.get((card["tipo_unidade"] or "").upper(), card["tipo_unidade"])
    market["group_revenue_display"] = _money(market["group_revenue"]) if market["group_revenue"] else None
    market["share_display"] = _share_display(market["share_pct"])
    return market


# Usado também pela ficha do cliente (inteligência de clientes).
market_display = _market_display


def _scope_cnpjs(scope, identity_cnpj=None):
    """CNPJs do que está na tela: a unidade escolhida, todas as do grupo ou o cliente sozinho."""
    if scope["mode"] == "group":
        selected = scope.get("selected_member_id")
        members = [member for member in scope["members"] if not selected or member["code"] == selected]
        return [member["cnpj"] for member in members if member.get("cnpj")]
    own = next((member["cnpj"] for member in scope["members"] if member["code"] == scope.get("selected_member_id")), None)
    return [own or identity_cnpj] if (own or identity_cnpj) else []


def resolve_customer_cnpjs(customer_id, view_mode="individual", member_customer_id=None):
    """Mesmos CNPJs do card de mercado, sem montar o DNA inteiro (usado pela consulta à Econodata)."""
    scope = legacy._resolve_customer_scope(customer_id, view_mode, member_customer_id)
    cnpjs = _scope_cnpjs(scope)
    if not cnpjs:
        rows = legacy._query("SELECT CGCCPF AS CNPJ FROM E085CLI WHERE CODCLI = :customer_id", {"customer_id": customer_id})
        cnpjs = [row["cnpj"] for row in rows if row.get("cnpj")]
    return cnpjs


def _group_abc(ranking, codes, scope):
    """Na visão de grupo, a curva ABC considera o grupo como um cliente só."""
    abc = ranking["customer_abc"]
    if len(codes) <= 1:
        return abc
    # O grupo entra no Pareto no lugar das filiais: soma das filiais contra o resto da base.
    amounts = {code: values["amount"] for code, values in abc.items() if code not in codes}
    group_amount = sum((abc[code]["amount"] for code in codes if code in abc), ZERO)
    anchor = codes[0]
    amounts[anchor] = group_amount
    regrouped = pareto(amounts)
    return {code: regrouped[anchor] for code in codes if anchor in regrouped} or {}


def _filter_events(rows, field, window):
    return [row for row in rows if _in_window(_date(row.get(field)), window["start"], window["end"])]


# ---------------------------------------------------------------------------
# DNA de Representantes
# ---------------------------------------------------------------------------

def load_representative_dna(rep_code, period_key=None, range_start=None, range_end=None, today=None, goals=None, groups=None):
    """Painel do representante. `goals` é a lista de RepresentativeGoal (ou dicts) do ano corrente."""
    today = today or date.today()
    window = resolve_window(period_key, range_start, range_end, today=today)
    ranking = company_ranking(window)
    primary = primary_rep(rep_code)
    codes = rep_codes(primary)
    load_start, load_end = _load_bounds(window, today)
    placeholders, params = _in_clause("r", codes)
    dated = {**params, "start_date": load_start, "end_date": load_end}
    sources = _run(
        {
            "billing": (BILLING_SQL.format(scope=f"N.CODREP IN ({placeholders})"), dated),
            "orders": (ORDERS_SQL.format(scope=f"PED.CODREP IN ({placeholders})"), dated),
            "production": (PRODUCTION_SQL.format(scope=f"PED.CODREP IN ({placeholders})"), params),
        }
    )
    all_lines = [billing_line(row) for row in sources["billing"]]
    lines = only_groups(all_lines, groups)
    orders = _orders_with_production(sources["orders"], sources["production"], today)
    rep_info = ranking["reps"].get(primary) or {}
    sample = next((line for line in lines if line["rep_code"] == primary), lines[0] if lines else None)
    name = REPRESENTATIVE_DISPLAY_NAMES.get(primary) or rep_info.get("name") or (sample or {}).get("rep_name") or ""
    if not lines and not orders and not goals:
        return None

    current, previous = _split_by_window(lines, window)
    totals = summarize(current)
    previous_totals = summarize(previous)
    history_customers = {line["customer"] for line in lines if line["customer"] is not None}
    first_last = _first_last(history_customers)

    customers = _representative_customers(current, window, ranking, first_last, today)
    active_codes = {row["code"] for row in customers}
    new_customers = [row for row in customers if row["is_new"]]
    inactive = _inactive_customers(lines, active_codes, first_last, today)
    rep_rank = ranking["rep_abc"].get(primary)
    company_total = ranking["total_net_of_taxes"]

    return {
        "rep": {
            "code": primary,
            "codes": codes,
            "codes_display": ", ".join(str(code) for code in codes),
            "name": name or f"Representante {primary}",
            "nickname": rep_info.get("nickname") or "",
            "team": rep_team(primary),
            "initial": (name or "R")[:1],
        },
        "period": _period_context(window),
        "kpis": _kpis(totals, previous_totals),
        "has_sales": totals["net"] > 0,
        "ranking": {
            "class": rep_rank["class"] if rep_rank else "–",
            "position": rep_rank["position"] if rep_rank else None,
            "reps_count": len(ranking["rep_abc"]),
            "share_display": _pct(_ratio(totals["net_of_taxes"], company_total), decimals=2),
            "cumulative_display": _pct(rep_rank["cumulative"]) if rep_rank else "n/d",
        },
        "customers_summary": {
            "active": len(customers),
            "new": len(new_customers),
            "inactive": len(inactive["items"]),
            "class_counts": _class_counts(customers),
            "concentration": _concentration(customers),
        },
        "customers": customers,
        "new_customers": new_customers[:12],
        "inactive": inactive,
        "goals": _goal_tracking(goals or [], lines, orders, today),
        "series": _monthly_series(lines, today),
        "mix": _mix(_split_by_window(all_lines, window)[0] if groups else current),
        "portfolio": _portfolio(orders, window),
    }


def _representative_customers(lines, window, ranking, first_last, today):
    by_customer = defaultdict(list)
    for line in lines:
        if line["customer"] is not None:
            by_customer[line["customer"]].append(line)
    total = sum((line["net"] for line in lines if line["included"]), ZERO)
    rows = []
    for code, customer_lines in by_customer.items():
        classification = classify_customer(customer_lines, window, ranking["customer_abc"])
        customer_totals = classification["totals"]
        if customer_totals["net"] <= 0:
            continue
        sample = customer_lines[-1]
        dates = first_last.get(code, {})
        last_purchase = dates.get("last") or max((line["date"] for line in customer_lines if line["date"]), default=None)
        first_purchase = dates.get("first")
        rows.append(
            {
                "code": code,
                "name": sample["customer_name"] or f"Cliente {code}",
                "location": "/".join(part for part in (sample["city"], sample["state"]) if part),
                "net": customer_totals["net"],
                "revenue_display": _money(customer_totals["net"]),
                "weight_display": _kg(customer_totals["net_weight"]),
                "fator_display": _fator(customer_totals["fator_kg"]),
                "margin_display": _pct(customer_totals["margin"]),
                "share": round(float(customer_totals["net"] / total) * 100, 1) if total else 0.0,
                "share_display": _pct(customer_totals["net"] / total) if total else "0,0%",
                "classification": classification["code"],
                "letters": [classification["letters"][key] for key, _label in ABC_DIMENSIONS],
                "score": float(classification["score"]) if classification["score"] is not None else None,
                "score_display": classification["score_display"],
                "orientation": classification["orientation"],
                "recurrence_label": classification["recurrence_label"] or "-",
                "last_purchase_display": _display_date(last_purchase),
                "days_since": (today - last_purchase).days if last_purchase else None,
                "is_new": bool(first_purchase and _in_window(first_purchase, window["start"], window["end"])),
                "first_purchase_display": _display_date(first_purchase),
            }
        )
    rows.sort(key=lambda row: row["net"], reverse=True)
    running = 0.0
    for row in rows:
        running += row["share"]
        row["cumulative_share"] = round(running, 1)
    return rows


def _class_counts(customers):
    counts = {"A": 0, "B": 0, "C": 0}
    for row in customers:
        letter = row["letters"][0]
        if letter in counts:
            counts[letter] += 1
    return counts


def _concentration(customers):
    """Quantos clientes fazem 80% da venda do representante (risco de dependência)."""
    if not customers:
        return {"count": 0, "share_top1": "n/d", "top_name": ""}
    needed = next((index + 1 for index, row in enumerate(customers) if row["cumulative_share"] >= 80), len(customers))
    return {
        "count": needed,
        "total": len(customers),
        "share_top1": f"{customers[0]['share']:.1f}%".replace(".", ","),
        "top_name": customers[0]["name"],
    }


def _inactive_customers(lines, active_codes, first_last, today):
    """Clientes que compraram com o representante nos últimos 24 meses, mas não no período."""
    names = {}
    for line in lines:
        if line["customer"] is not None:
            names[line["customer"]] = (line["customer_name"], "/".join(p for p in (line["city"], line["state"]) if p))
    items = []
    bands = defaultdict(int)
    for code, (name, location) in names.items():
        if code in active_codes:
            continue
        last = (first_last.get(code) or {}).get("last")
        if not last:
            continue
        days = (today - last).days
        band = inactivity_band(days)
        bands[band["key"]] += 1
        items.append(
            {
                "code": code,
                "name": name or f"Cliente {code}",
                "location": location,
                "last_purchase_display": _display_date(last),
                "days": days,
                "band": band["label"],
            }
        )
    items.sort(key=lambda item: item["days"])
    return {
        "items": items,
        "bands": [{"key": key, "label": label, "count": bands.get(key, 0)} for _limit, key, label in INACTIVITY_BANDS],
    }


def _goal_value(goal, field):
    return getattr(goal, field) if not isinstance(goal, dict) else goal[field]


def _goal_tracking(goals, lines, orders, today):
    year = today.year
    goals = [goal for goal in goals if int(_goal_value(goal, "year")) == year]
    monthly_goal = defaultdict(lambda: ZERO)
    family_goal = defaultdict(lambda: ZERO)
    unit_goal = defaultdict(lambda: ZERO)
    for goal in goals:
        weight = _num(_goal_value(goal, "weight_kg"))
        monthly_goal[int(_goal_value(goal, "month"))] += weight
        family_goal[_goal_value(goal, "family")] += weight
        unit_goal[int(_goal_value(goal, "unit_code"))] += weight

    billed = defaultdict(lambda: ZERO)
    billed_family = defaultdict(lambda: ZERO)
    billed_unit = defaultdict(lambda: ZERO)
    for line in lines:
        if line["included"] and line["date"] and line["date"].year == year:
            billed[line["date"].month] += line["net_weight"]
            billed_family[line["group"]] += line["net_weight"]
            if line["unit"] is not None:
                billed_unit[line["unit"]] += line["net_weight"]
    to_bill = defaultdict(lambda: ZERO)
    to_bill_family = defaultdict(lambda: ZERO)
    overdue_to_bill = ZERO
    for item in orders:
        if not item["is_open"]:
            continue
        delivery = item["delivery_date"]
        if delivery and delivery.year == year:
            to_bill[delivery.month] += item["to_bill_kg"]
            to_bill_family[item["group"]] += item["to_bill_kg"]
        if item["is_overdue"]:
            overdue_to_bill += item["to_bill_kg"]

    months = []
    for month in range(1, 13):
        goal = monthly_goal.get(month, ZERO)
        done = billed.get(month, ZERO)
        pending = to_bill.get(month, ZERO)
        months.append(
            {
                "label": MONTH_NAMES[month - 1],
                "goal": float(goal),
                "billed": float(done),
                "to_bill": float(pending),
                "pct": float((done + pending) / goal) if goal else None,
                "pct_display": _pct(_ratio(done + pending, goal), decimals=0) if goal else "–",
                "is_current": month == today.month,
                "is_future": month > today.month,
                "tick": True,
            }
        )
    current_month = today.month
    ytd_goal = sum((monthly_goal.get(month, ZERO) for month in range(1, current_month + 1)), ZERO)
    ytd_billed = sum((billed.get(month, ZERO) for month in range(1, current_month + 1)), ZERO)
    ytd_to_bill = sum((to_bill.get(month, ZERO) for month in range(1, current_month + 1)), ZERO)
    year_goal = sum(monthly_goal.values(), ZERO)
    month_goal = monthly_goal.get(current_month, ZERO)
    month_done = billed.get(current_month, ZERO) + to_bill.get(current_month, ZERO)

    families = []
    for family in sorted(set(family_goal) | set(billed_family), key=lambda key: family_goal.get(key, ZERO), reverse=True):
        goal = family_goal.get(family, ZERO)
        done = billed_family.get(family, ZERO)
        pending = to_bill_family.get(family, ZERO)
        families.append(
            {
                "name": family.title(),
                "goal_display": _kg(goal) if goal else "Sem meta",
                "billed_display": _kg(done),
                "to_bill_display": _kg(pending),
                "pct": min(round(float((done + pending) / goal) * 100), 100) if goal else None,
                "pct_display": _pct(_ratio(done + pending, goal), decimals=0) if goal else "–",
                "without_goal": not goal,
            }
        )
    units = [
        {
            "name": BUDGET_UNITS.get(unit, f"Unidade {unit}"),
            "goal_display": _kg(goal),
            "billed_display": _kg(billed_unit.get(unit, ZERO)),
            "pct_display": _pct(_ratio(billed_unit.get(unit, ZERO), goal), decimals=0),
        }
        for unit, goal in sorted(unit_goal.items(), key=lambda item: item[1], reverse=True)
    ]
    expected_share = Decimal(current_month) / Decimal(12)
    return {
        "year": year,
        "has_goals": bool(goals),
        "months": months,
        "ytd": {
            "goal_display": _kg(ytd_goal),
            "billed_display": _kg(ytd_billed),
            "to_bill_display": _kg(ytd_to_bill),
            "pct": _ratio(ytd_billed + ytd_to_bill, ytd_goal),
            "pct_display": _pct(_ratio(ytd_billed + ytd_to_bill, ytd_goal)),
            "billed_pct_display": _pct(_ratio(ytd_billed, ytd_goal)),
            "gap_display": _kg(max(ytd_goal - ytd_billed - ytd_to_bill, ZERO)),
            "bar_pct": min(round(float(_ratio(ytd_billed + ytd_to_bill, ytd_goal) or 0) * 100), 100),
        },
        "month": {
            "label": MONTH_NAMES[current_month - 1],
            "goal_display": _kg(month_goal),
            "done_display": _kg(month_done),
            "pct_display": _pct(_ratio(month_done, month_goal), decimals=0) if month_goal else "–",
            "bar_pct": min(round(float(_ratio(month_done, month_goal) or 0) * 100), 100),
        },
        "year_total": {
            "goal_display": _kg(year_goal),
            "pct_display": _pct(_ratio(ytd_billed + ytd_to_bill, year_goal)),
            "expected_display": _pct(expected_share, decimals=0),
        },
        "has_overdue": overdue_to_bill > 0,
        "overdue_to_bill_display": _kg(overdue_to_bill),
        "families": families,
        "units": units,
    }
