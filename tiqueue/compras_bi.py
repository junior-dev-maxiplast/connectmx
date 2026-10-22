"""
BI de Compras — prazo de pagamento, prazo de entrega e variação de preço das
ordens de compra (E420OCP/E420IPO), servido ao ConnectMX Dashes.

As agregações rodam no Oracle do ERP Senior, não em Python, seguindo o mesmo
princípio dos BIs de Viagens e de Romaneios.

Duas coisas sobre as consultas moldam este módulo:

1. **A base de prazo (abas "Prazo de Pagamento" e "Prazo de Entrega") só
   cobre item de OC que já virou nota de entrada.** O `JOIN` com `E440NFC`
   é `INNER`, encostado num `SUB` que já exige, por sua vez, um movimento de
   estoque de entrada (`E210MVP.ESTEOS = 'E'`) casado com o item de nota
   (`E440IPC`). Ou seja: esta base não é "todas as ordens de compra", é "itens
   de ordem de compra já recebidos" — o único recorte em que prazo de entrega e
   prazo de pagamento têm o que ser medido. Ordem ainda em aberto/sem nota não
   aparece aqui; para volume de ordens em aberto seria um painel diferente.
2. **`DT_PAGAMENTO` vem de uma subconsulta correlacionada** (`E501TCP` +
   `E501MCP`, maior data de pagamento do título ligado àquela nota) — por isso
   o dia real de pagamento (`DIAS_PAGAMENTO_REAL`) precisa de uma segunda CTE
   por cima da primeira: o Oracle não deixa reaproveitar o alias de uma coluna
   de subconsulta na mesma lista de SELECT (mesmo motivo documentado em
   `travel_bi.py`). `E501MCP.DATPGT` também carrega `31/12/1900` como
   sentinela de "ainda não pago" (mesmo princípio do cadastro de viagem,
   documentado em `travel_bi.py`) — confirmado contra o Oracle de produção: o
   título ainda em aberto já tem a linha de quitação criada com essa data
   fixa. A subconsulta descarta qualquer valor `<= 1901-01-01`, senão o título
   em aberto entraria como "pago 126 anos atrás" e inflaria tanto a contagem
   de inconsistências (dia negativo) quanto, se a sentinela fosse maior que
   `DATA_GERACAO`, a própria média de prazo.

Este módulo foi validado contra o Oracle de produção (`192.168.30.2`, ERP
Senior) durante o desenvolvimento — diferente de `romaneio_bi.py`, que não
teve esse acesso. As consultas replicam fielmente as duas consultas
fornecidas para a construção deste painel (incluindo os JOINs e subselects),
só trocando o filtro fixo por uma cláusula de escopo parametrizada. Dias
negativos (entrega ou pagamento datado antes da geração da OC — impossível,
não só incomum) são sinalizados como inconsistência e excluídos das médias,
nunca descartados da contagem total de linhas.
"""

import hashlib
import json
from datetime import datetime

from .customer_dna import _oracle_connection_safe as _erp_connection
from .bi_periods import mark_series_ticks, period_choices, resolve_period, series_label
from .bi_common import (
    display_date as _display_date,
    format_decimal as _format_decimal,
    format_int as _format_int,
    format_money as _format_money,
    resolve_branch,
    share as _share,
    to_float as _float,
    to_int as _int,
    oracle_query,
    oracle_query_many,
    branches_from_rows,
)


# ----------------------------------------------------------------- acesso --

def _query(sql, params=None):
    return oracle_query(_erp_connection, sql, params)


def _query_many(statements, params):
    return oracle_query_many(_erp_connection, statements, params)


# ---------------------------------------------------------------- helpers --

def _format_quantity(value):
    """Quantidade com separador de milhar — inteira quando o ERP manda um
    número redondo (o caso comum de QTDPED), com até 2 casas quando não."""
    number = _float(value)
    if number == int(number):
        return _format_int(number)
    return _format_decimal(number, 2)


def _format_days(value):
    if value is None:
        return "-"
    return f"{_format_decimal(value, 1)} dias"


# ------------------------------------------------------------- vocabulário --

VIEW_CHOICES = [
    ("pagamento", "Prazo de Pagamento"),
    ("entrega", "Prazo de Entrega"),
    ("preco", "Variação de Preço"),
]


def resolve_view(raw_value):
    value = (raw_value or "pagamento").strip()
    for key, label in VIEW_CHOICES:
        if key == value:
            return {"key": key, "label": label}
    return {"key": "pagamento", "label": "Prazo de Pagamento"}


def view_choices():
    return [{"key": key, "label": label} for key, label in VIEW_CHOICES]


# ------------------------------------------------------------- filtros ----

# A base tem a cauda longa de um cadastro de compras de verdade: 2.677
# fornecedores distintos, a maioria com uma ou duas ordens em anos de
# histórico. Um <select> com todos travaria a tela (confirmado contra o
# Oracle de produção: a página ficou pesada o bastante para a captura de tela
# dar timeout). O filtro mostra só os mais ativos — mesmo princípio de corte
# por regularidade que `romaneio_bi.list_matriculas` usa para matrícula.
# Fora da lista ainda dá para filtrar por um fornecedor específico direto na
# URL (`resolve_fornecedor` aceita qualquer código numérico), só não aparece
# como opção pronta no filtro.
FORNECEDORES_LIMIT = 150

FORNECEDORES_SQL = f"""
SELECT D.CODFOR AS CODIGO, COUNT(*) AS TOTAL, MAX(E.NOMFOR) AS NOME
FROM E420OCP D
LEFT JOIN E095FOR E ON E.CODFOR = D.CODFOR
GROUP BY D.CODFOR
ORDER BY COUNT(*) DESC
FETCH FIRST {FORNECEDORES_LIMIT} ROWS ONLY
"""


def list_fornecedores():
    """Fornecedores mais ativos com ordem de compra, para montar o filtro."""
    rows = _query(FORNECEDORES_SQL)
    fornecedores = []
    for row in rows:
        code = _int(row.get("codigo"))
        name = (row.get("nome") or "").strip() or f"Fornecedor {code}"
        fornecedores.append(
            {
                "key": str(code),
                "code": code,
                "name": name,
                "label": f"{code} - {name}",
                "total": _int(row.get("total")),
            }
        )
    return fornecedores


def resolve_fornecedor(raw_value, available=None):
    value = (raw_value or "").strip()
    if not value or value == "all":
        return {"key": "all", "label": "Todos os fornecedores", "code": None}
    catalog = {item["key"]: item for item in (available or [])}
    item = catalog.get(value)
    if item is not None:
        return {"key": item["key"], "label": item["label"], "code": item["code"]}
    # Fornecedor fora dos mais ativos (fora de FORNECEDORES_LIMIT) ainda é um
    # código válido se vier direto na URL — aceita, só sem contar com o rótulo
    # do catálogo (mesmo princípio de `travel_bi.resolve_matricula`).
    if value.isdigit():
        return {"key": value, "label": f"Fornecedor {value}", "code": int(value)}
    return {"key": "all", "label": "Todos os fornecedores", "code": None}


def list_branches():
    """Combinações empresa+filial com ordem de compra, para montar o filtro."""
    rows = _query(
        """
        SELECT CODEMP AS EMPRESA, CODFIL AS FILIAL, COUNT(*) AS TOTAL
        FROM E420OCP
        GROUP BY CODEMP, CODFIL
        ORDER BY TOTAL DESC
        """
    )
    return branches_from_rows(rows)


def list_familias():
    """Famílias de produto com item de ordem de compra, para montar o filtro."""
    rows = _query(
        """
        SELECT I.CODFAM AS CODIGO, MAX(F.DESFAM) AS NOME, COUNT(*) AS TOTAL
        FROM E420IPO I
        LEFT JOIN E012FAM F ON F.CODFAM = I.CODFAM AND F.CODEMP = I.CODEMP
        GROUP BY I.CODFAM
        ORDER BY COUNT(*) DESC
        """
    )
    familias = []
    for row in rows:
        code = str(row.get("codigo") or "").strip()
        if not code:
            continue
        name = (row.get("nome") or "").strip() or code
        familias.append(
            {
                "key": code,
                "code": code,
                "name": name,
                "label": f"{code} - {name}",
                "total": _int(row.get("total")),
            }
        )
    return familias


def resolve_familia(raw_value, available=None):
    value = (raw_value or "").strip()
    if not value or value == "all":
        return {"key": "all", "label": "Todas as famílias", "code": None}
    catalog = {item["key"]: item for item in (available or [])}
    item = catalog.get(value)
    if item is None:
        return {"key": "all", "label": "Todas as famílias", "code": None}
    return {"key": item["key"], "label": item["label"], "code": item["code"]}


def _scope_sql(period, fornecedor, branch, familia, *, date_col, fornecedor_col, empresa_col, filial_col, familia_col):
    """Cláusulas de filtro compartilhadas por todas as agregações.

    Recebe as expressões de coluna de cada CTE porque a base de prazo
    (alias `OCP`/`IPO`) e a base de preço (alias `A`) não compartilham os
    mesmos aliases de tabela.
    """
    clauses = ["1 = 1"]
    params = {}
    if period.get("start") and period.get("end"):
        clauses.append(f"{date_col} >= :period_start AND {date_col} < :period_end")
        params["period_start"] = period["start"]
        params["period_end"] = period["end"]
    elif period.get("months"):
        clauses.append(f"{date_col} >= ADD_MONTHS(TRUNC(SYSDATE), -:months)")
        params["months"] = period["months"]
    if fornecedor["code"] is not None:
        clauses.append(f"{fornecedor_col} = :fornecedor")
        params["fornecedor"] = fornecedor["code"]
    if branch["empresa"] is not None:
        clauses.append(f"{empresa_col} = :empresa")
        params["empresa"] = branch["empresa"]
    if branch["filial"] is not None:
        clauses.append(f"{filial_col} = :filial")
        params["filial"] = branch["filial"]
    if familia["code"] is not None:
        clauses.append(f"{familia_col} = :familia")
        params["familia"] = familia["code"]
    return " AND ".join(clauses), params


# ------------------------------------------------------------- SQL da base --

# Duas CTEs porque o Oracle não deixa reaproveitar apelido de coluna no mesmo
# SELECT: a primeira traz os dados do ERP (inclusive o pagamento, por
# subconsulta correlacionada), a segunda calcula o dia real de pagamento a
# partir do que a primeira já resolveu.
_BASE_SQL = """
WITH BASE0 AS (
    SELECT DISTINCT
        OCP.CODEMP AS EMPRESA,
        OCP.CODFOR AS FORNECEDOR,
        OCP.CODFIL AS FILIAL,
        OCP.NUMOCP AS ORDEM,
        OCP.DATGER AS DATA_GERACAO,
        TO_CHAR(OCP.DATGER, 'YYYY-MM') AS COMPETENCIA,
        TO_CHAR(OCP.DATGER, 'YYYY-MM-DD') AS DIA,
        IPO.CODFAM AS FAMILIA,
        IPO.CODPRO AS PRODUTO,
        IPO.SITIPO AS SITUACAO,
        IPO.SEQIPO AS SEQ_ORDEM,
        FAM.CODORI AS ORIGEM,
        NVL(FAM.DESFAM, 'Sem família') AS DS_FAMILIA,
        NVL(ORI.DESORI, 'Sem origem') AS DS_ORIGEM,
        CPG.PRZMED AS PRAZO_CONTRATADO,
        PRO.DESPRO AS DS_PRODUTO,
        NVL(FORN.NOMFOR, 'Fornecedor ' || OCP.CODFOR) AS DS_FORNECEDOR,
        EMP.SIGEMP AS DS_EMPRESA,
        FIL.SIGFIL AS DS_FILIAL,
        SUB.NOTA AS NOTA,
        SUB.DATA_MOVIMENTO AS DT_ENTREGA,
        ROUND(SUB.DATA_MOVIMENTO - OCP.DATGER) AS DIAS_ENTREGA,
        NFC.DATEMI AS DT_EMISSAO_NOTA,
        CASE WHEN TCP.SITTIT = 'LQ' THEN 'Pago' ELSE 'Em aberto' END AS SITUACAO_PAGAMENTO,
        (
            SELECT CASE WHEN MAX(MCP.DATPGT) > DATE '1901-01-01' THEN MAX(MCP.DATPGT) END
            FROM E501TCP TCP2
            JOIN E501MCP MCP ON TCP2.NUMTIT = MCP.NUMTIT
                             AND TCP2.CODEMP = MCP.CODEMP
                             AND TCP2.CODFIL = MCP.CODFIL
            WHERE TCP2.NUMNFC = SUB.NOTA
              AND TCP2.CODFIL = SUB.FILIAL
              AND TCP2.CODEMP = SUB.EMPRESA
              AND TCP2.CODFOR = SUB.FORNECEDOR
        ) AS DT_PAGAMENTO
    FROM E420OCP OCP
    LEFT JOIN E028CPG CPG ON OCP.CODCPG = CPG.CODCPG AND OCP.CODEMP = CPG.CODEMP
    LEFT JOIN E420IPO IPO ON OCP.NUMOCP = IPO.NUMOCP AND OCP.CODEMP = IPO.CODEMP AND OCP.CODFIL = IPO.CODFIL
    LEFT JOIN E012FAM FAM ON IPO.CODFAM = FAM.CODFAM AND IPO.CODEMP = FAM.CODEMP
    LEFT JOIN E083ORI ORI ON FAM.CODORI = ORI.CODORI AND FAM.CODEMP = ORI.CODEMP
    LEFT JOIN E095FOR FORN ON OCP.CODFOR = FORN.CODFOR
    LEFT JOIN E070EMP EMP ON OCP.CODEMP = EMP.CODEMP
    LEFT JOIN E070FIL FIL ON OCP.CODEMP = FIL.CODEMP AND OCP.CODFIL = FIL.CODFIL
    LEFT JOIN E075PRO PRO ON IPO.CODPRO = PRO.CODPRO AND IPO.CODEMP = PRO.CODEMP
    LEFT JOIN (
        SELECT
            IPC.NUMNFC AS NOTA, IPC.CODEMP AS EMPRESA, IPC.CODFIL AS FILIAL,
            IPC.CODFOR AS FORNECEDOR, IPC.SEQIPO AS SEQ_ORDEM, IPC.NUMOCP AS ORDEM,
            IPC.CODPRO AS PRODUTO, MAX(MVP.DATMOV) AS DATA_MOVIMENTO
        FROM E440IPC IPC
        JOIN E210MVP MVP ON IPC.NUMNFC = MVP.NUMNFC AND IPC.CODEMP = MVP.CODEMP
                         AND IPC.CODFIL = MVP.CODFIL AND IPC.CODPRO = MVP.CODPRO
        WHERE MVP.ESTEOS = 'E'
        GROUP BY IPC.NUMNFC, IPC.CODEMP, IPC.CODFIL, IPC.CODFOR, IPC.SEQIPO, IPC.NUMOCP, IPC.CODPRO
    ) SUB ON OCP.NUMOCP = SUB.ORDEM AND OCP.CODEMP = SUB.EMPRESA AND OCP.CODFIL = SUB.FILIAL
         AND IPO.CODPRO = SUB.PRODUTO AND OCP.CODFOR = SUB.FORNECEDOR AND IPO.SEQIPO = SUB.SEQ_ORDEM
    INNER JOIN E440NFC NFC ON NFC.NUMNFC = SUB.NOTA AND NFC.CODEMP = OCP.CODEMP
                          AND NFC.CODFIL = OCP.CODFIL AND NFC.CODFOR = OCP.CODFOR
    LEFT JOIN E501TCP TCP ON TCP.NUMNFC = SUB.NOTA AND TCP.CODFIL = SUB.FILIAL
                         AND TCP.CODEMP = SUB.EMPRESA AND TCP.CODFOR = SUB.FORNECEDOR
    WHERE IPO.SITIPO <> 5 AND {scope}
),
BASE AS (
    SELECT BASE0.*, ROUND(DT_PAGAMENTO - DATA_GERACAO) AS DIAS_PAGAMENTO_REAL
    FROM BASE0
)
"""


def _base(scope_sql):
    return _BASE_SQL.format(scope=scope_sql)


# Base mais simples para a aba de preço: um item de OC por linha, sem exigir
# nota/entrada (aqui o que importa é o preço cotado, recebido ou não).
_ITENS_SQL = """
WITH ITENS AS (
    SELECT
        A.CODEMP AS EMPRESA,
        A.CODFIL AS FILIAL,
        A.NUMOCP AS ORDEM,
        A.SEQIPO AS SEQ_ORDEM,
        A.CODFAM AS FAMILIA,
        NVL(B.DESFAM, 'Sem família') AS DS_FAMILIA,
        A.CODPRO AS PRODUTO,
        NVL(C.DESPRO, 'Produto ' || A.CODPRO) AS DS_PRODUTO,
        D.CODFOR AS FORNECEDOR,
        NVL(E.NOMFOR, 'Fornecedor ' || D.CODFOR) AS DS_FORNECEDOR,
        A.QTDPED AS QUANTIDADE,
        A.PREUNI AS VALOR_UNITARIO,
        A.QTDPED * A.PREUNI AS VALOR_TOTAL,
        A.DATGER AS DATA_GERACAO,
        TO_CHAR(A.DATGER, 'YYYY-MM') AS COMPETENCIA,
        TO_CHAR(A.DATGER, 'YYYY-MM-DD') AS DIA
    FROM E420IPO A
    LEFT JOIN E012FAM B ON A.CODFAM = B.CODFAM AND A.CODEMP = B.CODEMP
    LEFT JOIN E075PRO C ON A.CODPRO = C.CODPRO AND A.CODEMP = C.CODEMP
    LEFT JOIN E420OCP D ON A.NUMOCP = D.NUMOCP AND A.CODEMP = D.CODEMP AND A.CODFIL = D.CODFIL
    LEFT JOIN E095FOR E ON D.CODFOR = E.CODFOR
    WHERE {scope}
)
"""


def _itens(scope_sql):
    return _ITENS_SQL.format(scope=scope_sql)


# ---------------------------------------------------------- agregações ----

def load_compras_dashboard(
    period_key="12",
    view_key="pagamento",
    fornecedor_key="all",
    filial_key="all",
    familia_key="all",
    fornecedores_available=None,
    branches_available=None,
    familias_available=None,
):
    period = resolve_period(period_key)
    fornecedores = fornecedores_available if fornecedores_available is not None else list_fornecedores()
    fornecedor = resolve_fornecedor(fornecedor_key, fornecedores)
    branches = branches_available if branches_available is not None else list_branches()
    branch = resolve_branch(filial_key, branches)
    familias = familias_available if familias_available is not None else list_familias()
    familia = resolve_familia(familia_key, familias)
    view = resolve_view(view_key)

    catalogs = (fornecedores, branches, familias)
    if view["key"] == "preco":
        return _load_preco_dashboard(period, fornecedor, branch, familia, view, *catalogs)
    return _load_prazo_dashboard(period, fornecedor, branch, familia, view, *catalogs)


def _scope_for_prazo(period, fornecedor, branch, familia):
    return _scope_sql(
        period, fornecedor, branch, familia,
        date_col="OCP.DATGER", fornecedor_col="OCP.CODFOR",
        empresa_col="OCP.CODEMP", filial_col="OCP.CODFIL", familia_col="IPO.CODFAM",
    )


def _scope_for_preco(period, fornecedor, branch, familia):
    return _scope_sql(
        period, fornecedor, branch, familia,
        date_col="A.DATGER", fornecedor_col="D.CODFOR",
        empresa_col="A.CODEMP", filial_col="A.CODFIL", familia_col="A.CODFAM",
    )


def _load_prazo_dashboard(period, fornecedor, branch, familia, view, fornecedores, branches, familias):
    scope, params = _scope_for_prazo(period, fornecedor, branch, familia)
    base = _base(scope)
    series_column = "DIA" if period["granularity"] == "day" else "COMPETENCIA"

    prazo_expr = "DIAS_PAGAMENTO_REAL" if view["key"] == "pagamento" else "DIAS_ENTREGA"
    prazo_valido = f"{prazo_expr} IS NOT NULL AND {prazo_expr} >= 0"

    statements = {
        "totals": f"""
            {base}
            SELECT
              COUNT(DISTINCT FORNECEDOR) AS FORNECEDORES,
              COUNT(DISTINCT NOTA) AS NOTAS,
              COUNT(DISTINCT ORDEM) AS ORDENS,
              COUNT(DISTINCT PRODUTO) AS PRODUTOS,
              COUNT(*) AS LINHAS,
              AVG(CASE WHEN {prazo_valido} THEN {prazo_expr} END) AS PRAZO_MEDIO,
              AVG(PRAZO_CONTRATADO) AS PRAZO_META,
              COUNT(CASE WHEN {prazo_valido} THEN 1 END) AS LINHAS_COM_PRAZO,
              SUM(CASE WHEN {prazo_expr} < 0 THEN 1 ELSE 0 END) AS INCONSISTENCIAS,
              SUM(CASE WHEN SITUACAO_PAGAMENTO = 'Pago' THEN 1 ELSE 0 END) AS PAGOS
            FROM BASE
        """,
        "monthly": f"""
            {base}
            SELECT
              {series_column} AS COMPETENCIA,
              COUNT(*) AS LINHAS,
              AVG(CASE WHEN {prazo_valido} THEN {prazo_expr} END) AS PRAZO_MEDIO,
              AVG(PRAZO_CONTRATADO) AS PRAZO_META
            FROM BASE
            GROUP BY {series_column}
            ORDER BY {series_column}
        """,
        "familias": f"""
            {base}
            SELECT
              DS_FAMILIA AS ROTULO,
              COUNT(*) AS LINHAS,
              AVG(CASE WHEN {prazo_valido} THEN {prazo_expr} END) AS PRAZO_MEDIO
            FROM BASE
            GROUP BY DS_FAMILIA
            ORDER BY COUNT(*) DESC
            FETCH FIRST 12 ROWS ONLY
        """,
        "origens": f"""
            {base}
            SELECT
              DS_ORIGEM AS ROTULO,
              COUNT(*) AS LINHAS,
              AVG(CASE WHEN {prazo_valido} THEN {prazo_expr} END) AS PRAZO_MEDIO
            FROM BASE
            GROUP BY DS_ORIGEM
            ORDER BY COUNT(*) DESC
        """,
        "fornecedores": f"""
            {base}
            SELECT
              FORNECEDOR AS CODIGO,
              MAX(DS_FORNECEDOR) AS NOME,
              COUNT(*) AS LINHAS,
              COUNT(DISTINCT ORDEM) AS ORDENS,
              AVG(CASE WHEN {prazo_valido} THEN {prazo_expr} END) AS PRAZO_MEDIO
            FROM BASE
            GROUP BY FORNECEDOR
            ORDER BY COUNT(*) DESC
            FETCH FIRST 15 ROWS ONLY
        """,
        "detalhe": f"""
            {base}
            SELECT
              ORDEM, NOTA, DS_FORNECEDOR, DS_FILIAL, DS_PRODUTO,
              DATA_GERACAO, DT_ENTREGA, DT_EMISSAO_NOTA, DT_PAGAMENTO,
              DIAS_ENTREGA, DIAS_PAGAMENTO_REAL, SITUACAO_PAGAMENTO
            FROM BASE
            ORDER BY DATA_GERACAO DESC
            FETCH FIRST 30 ROWS ONLY
        """,
    }
    params_by_name = {name: dict(params) for name in statements}
    data = _query_many(statements, params_by_name)
    return _build_prazo_dashboard(data, period, fornecedor, branch, familia, view, fornecedores, branches, familias)


def _load_preco_dashboard(period, fornecedor, branch, familia, view, fornecedores, branches, familias):
    scope, params = _scope_for_preco(period, fornecedor, branch, familia)
    base = _itens(scope)
    series_column = "DIA" if period["granularity"] == "day" else "COMPETENCIA"

    statements = {
        "totals": f"""
            {base}
            SELECT
              COUNT(DISTINCT FORNECEDOR) AS FORNECEDORES,
              COUNT(DISTINCT PRODUTO) AS PRODUTOS,
              COUNT(DISTINCT ORDEM) AS ORDENS,
              COUNT(*) AS SEQUENCIAS,
              SUM(QUANTIDADE) AS QTD_COTADA,
              SUM(VALOR_TOTAL) AS TOTAL_COTADO,
              MIN(VALOR_UNITARIO) AS MIN_UNIT,
              AVG(VALOR_UNITARIO) AS MEDIA_UNIT,
              MAX(VALOR_UNITARIO) AS MAX_UNIT
            FROM ITENS
        """,
        "monthly": f"""
            {base}
            SELECT
              {series_column} AS COMPETENCIA,
              AVG(VALOR_UNITARIO) AS PRECO_MEDIO,
              SUM(VALOR_TOTAL) AS TOTAL,
              SUM(QUANTIDADE) AS QUANTIDADE
            FROM ITENS
            GROUP BY {series_column}
            ORDER BY {series_column}
        """,
        "fornecedores": f"""
            {base}
            SELECT
              FORNECEDOR AS CODIGO,
              MAX(DS_FORNECEDOR) AS NOME,
              SUM(QUANTIDADE) AS QUANTIDADE,
              SUM(VALOR_TOTAL) AS TOTAL,
              AVG(VALOR_UNITARIO) AS PRECO_MEDIO
            FROM ITENS
            GROUP BY FORNECEDOR
            ORDER BY SUM(VALOR_TOTAL) DESC NULLS LAST
            FETCH FIRST 15 ROWS ONLY
        """,
        "produtos": f"""
            {base}
            SELECT
              PRODUTO AS CODIGO,
              MAX(DS_PRODUTO) AS NOME,
              SUM(QUANTIDADE) AS QUANTIDADE,
              SUM(VALOR_TOTAL) AS TOTAL,
              AVG(VALOR_UNITARIO) AS PRECO_MEDIO,
              MIN(VALOR_UNITARIO) AS MIN_UNIT,
              MAX(VALOR_UNITARIO) AS MAX_UNIT
            FROM ITENS
            GROUP BY PRODUTO
            ORDER BY SUM(VALOR_TOTAL) DESC NULLS LAST
            FETCH FIRST 15 ROWS ONLY
        """,
        "detalhe": f"""
            {base}
            SELECT
              ORDEM, SEQ_ORDEM, DS_FORNECEDOR, DS_PRODUTO,
              QUANTIDADE, VALOR_UNITARIO, VALOR_TOTAL, DATA_GERACAO
            FROM ITENS
            ORDER BY DATA_GERACAO DESC
            FETCH FIRST 30 ROWS ONLY
        """,
    }
    params_by_name = {name: dict(params) for name in statements}
    data = _query_many(statements, params_by_name)
    return _build_preco_dashboard(data, period, fornecedor, branch, familia, view, fornecedores, branches, familias)


# -------------------------------------------------------------- montagem --

def _scope_payload(period, fornecedor, branch, familia, view, fornecedores, branches, familias):
    return {
        "period": period,
        "period_choices": period_choices(),
        "fornecedor": fornecedor,
        "fornecedor_choices": [{"key": "all", "label": "Todos os fornecedores"}] + [
            {"key": item["key"], "label": item["label"]} for item in fornecedores
        ],
        "branch": branch,
        "branch_choices": [{"key": "all", "label": "Todas as filiais"}] + [
            {"key": item["key"], "label": item["label"]} for item in branches
        ],
        "familia": familia,
        "familia_choices": [{"key": "all", "label": "Todas as famílias"}] + [
            {"key": item["key"], "label": item["label"]} for item in familias
        ],
        "view": view,
        "view_choices": view_choices(),
    }


def _bucket_bars(rows, value_key="prazo_medio"):
    max_value = max([_float(row.get(value_key)) for row in rows] + [1])
    items = []
    for row in rows:
        value = _float(row.get(value_key))
        items.append(
            {
                "label": row.get("rotulo") or "-",
                "linhas": _int(row.get("linhas")),
                value_key: value,
                f"{value_key}_display": _format_days(value),
                "height_pct": round(value / max_value * 100, 1) if max_value else 0,
            }
        )
    return items


def _build_prazo_dashboard(data, period, fornecedor, branch, familia, view, fornecedores, branches, familias):
    totals = (data.get("totals") or [{}])[0]
    linhas = _int(totals.get("linhas"))
    prazo_medio = _float(totals.get("prazo_medio"))
    prazo_meta = _float(totals.get("prazo_meta"))
    linhas_com_prazo = _int(totals.get("linhas_com_prazo"))
    is_pagamento = view["key"] == "pagamento"

    metrics = {
        "fornecedores": _int(totals.get("fornecedores")),
        "fornecedores_display": _format_int(totals.get("fornecedores")),
        "notas": _int(totals.get("notas")),
        "notas_display": _format_int(totals.get("notas")),
        "ordens": _int(totals.get("ordens")),
        "ordens_display": _format_int(totals.get("ordens")),
        "produtos": _int(totals.get("produtos")),
        "produtos_display": _format_int(totals.get("produtos")),
        "linhas": linhas,
        "linhas_display": _format_int(linhas),
        "prazo_medio": prazo_medio,
        "prazo_medio_display": _format_days(prazo_medio),
        "prazo_meta": prazo_meta,
        "prazo_meta_display": _format_days(prazo_meta) if is_pagamento else None,
        "prazo_coverage_pct": _share(linhas_com_prazo, linhas),
        "variacao_dias": round(prazo_medio - prazo_meta, 1) if is_pagamento else None,
        "pagos_pct": _share(totals.get("pagos"), linhas) if is_pagamento else None,
        "inconsistencias": _int(totals.get("inconsistencias")),
        "inconsistencias_pct": _share(totals.get("inconsistencias"), linhas),
    }

    monthly = []
    for row in data.get("monthly") or []:
        prazo = _float(row.get("prazo_medio"))
        meta = _float(row.get("prazo_meta"))
        monthly.append(
            {
                "label": series_label(row.get("competencia"), period["granularity"]),
                "linhas": _int(row.get("linhas")),
                "prazo_medio": prazo,
                "prazo_medio_display": _format_days(prazo),
                "prazo_meta": meta,
                "prazo_meta_display": _format_days(meta),
            }
        )
    mark_series_ticks(monthly)
    max_prazo = max([item["prazo_medio"] for item in monthly] + [item["prazo_meta"] for item in monthly] + [1])
    for item in monthly:
        item["height_pct"] = round(item["prazo_medio"] / max_prazo * 100, 1) if max_prazo else 0
        item["meta_height_pct"] = round(item["prazo_meta"] / max_prazo * 100, 1) if max_prazo else 0

    familias_bars = _bucket_bars(data.get("familias") or [])
    origens_bars = _bucket_bars(data.get("origens") or [])

    fornecedores_rows = []
    for row in data.get("fornecedores") or []:
        prazo = _float(row.get("prazo_medio"))
        fornecedores_rows.append(
            {
                "code": row.get("codigo"),
                "name": (row.get("nome") or "").strip() or f"Fornecedor {row.get('codigo')}",
                "linhas": _int(row.get("linhas")),
                "ordens": _int(row.get("ordens")),
                "prazo_medio": prazo,
                "prazo_medio_display": _format_days(prazo),
            }
        )
    max_fornecedor_prazo = max([item["prazo_medio"] for item in fornecedores_rows] + [1])
    for item in fornecedores_rows:
        item["height_pct"] = round(item["prazo_medio"] / max_fornecedor_prazo * 100, 1) if max_fornecedor_prazo else 0

    detalhe = []
    for row in data.get("detalhe") or []:
        detalhe.append(
            {
                "ordem": row.get("ordem"),
                "nota": row.get("nota"),
                "fornecedor": row.get("ds_fornecedor"),
                "filial": row.get("ds_filial"),
                "produto": row.get("ds_produto"),
                "geracao": _display_date(row.get("data_geracao")),
                "entrega": _display_date(row.get("dt_entrega")),
                "emissao_nota": _display_date(row.get("dt_emissao_nota")),
                "pagamento": _display_date(row.get("dt_pagamento")),
                "dias_entrega": row.get("dias_entrega"),
                "dias_pagamento": row.get("dias_pagamento_real"),
                "situacao_pagamento": row.get("situacao_pagamento"),
            }
        )

    return {
        "scope": _scope_payload(period, fornecedor, branch, familia, view, fornecedores, branches, familias),
        "metrics": metrics,
        "monthly": monthly,
        "familias": familias_bars,
        "origens": origens_bars,
        "fornecedores": fornecedores_rows,
        "detalhe": detalhe,
    }


def _build_preco_dashboard(data, period, fornecedor, branch, familia, view, fornecedores, branches, familias):
    totals = (data.get("totals") or [{}])[0]
    metrics = {
        "fornecedores": _int(totals.get("fornecedores")),
        "fornecedores_display": _format_int(totals.get("fornecedores")),
        "produtos": _int(totals.get("produtos")),
        "produtos_display": _format_int(totals.get("produtos")),
        "ordens": _int(totals.get("ordens")),
        "ordens_display": _format_int(totals.get("ordens")),
        "sequencias": _int(totals.get("sequencias")),
        "sequencias_display": _format_int(totals.get("sequencias")),
        "qtd_cotada": _float(totals.get("qtd_cotada")),
        "qtd_cotada_display": _format_decimal(totals.get("qtd_cotada"), 0),
        "total_cotado": _float(totals.get("total_cotado")),
        "total_cotado_display": _format_money(totals.get("total_cotado")),
        "preco_min": _float(totals.get("min_unit")),
        "preco_min_display": _format_money(totals.get("min_unit")),
        "preco_medio": _float(totals.get("media_unit")),
        "preco_medio_display": _format_money(totals.get("media_unit")),
        "preco_max": _float(totals.get("max_unit")),
        "preco_max_display": _format_money(totals.get("max_unit")),
    }

    monthly = []
    for row in data.get("monthly") or []:
        preco = _float(row.get("preco_medio"))
        monthly.append(
            {
                "label": series_label(row.get("competencia"), period["granularity"]),
                "preco_medio": preco,
                "preco_medio_display": _format_money(preco),
                "total": _float(row.get("total")),
                "total_display": _format_money(row.get("total")),
                "quantidade": _float(row.get("quantidade")),
            }
        )
    mark_series_ticks(monthly)
    max_preco = max([item["preco_medio"] for item in monthly] + [1])
    for item in monthly:
        item["height_pct"] = round(item["preco_medio"] / max_preco * 100, 1) if max_preco else 0

    def _ranking(rows_key):
        rows = data.get(rows_key) or []
        items = []
        for row in rows:
            items.append(
                {
                    "code": row.get("codigo"),
                    "name": (row.get("nome") or "").strip() or "-",
                    "quantidade": _float(row.get("quantidade")),
                    "quantidade_display": _format_quantity(row.get("quantidade")),
                    "total": _float(row.get("total")),
                    "total_display": _format_money(row.get("total")),
                    "preco_medio": _float(row.get("preco_medio")),
                    "preco_medio_display": _format_money(row.get("preco_medio")),
                }
            )
        # A ordenação já vem do SQL por total gasto (R$) — a barra usa a mesma
        # régua, então o ranking visual bate com a ordem das linhas.
        max_total = max([item["total"] for item in items] + [1])
        for item in items:
            item["height_pct"] = round(item["total"] / max_total * 100, 1) if max_total else 0
        return items

    detalhe = []
    for row in data.get("detalhe") or []:
        detalhe.append(
            {
                "ordem": row.get("ordem"),
                "sequencia": row.get("seq_ordem"),
                "fornecedor": row.get("ds_fornecedor"),
                "produto": row.get("ds_produto"),
                "quantidade": _float(row.get("quantidade")),
                "quantidade_display": _format_quantity(row.get("quantidade")),
                "preco_unitario": _float(row.get("valor_unitario")),
                "preco_unitario_display": _format_money(row.get("valor_unitario")),
                "total": _float(row.get("valor_total")),
                "total_display": _format_money(row.get("valor_total")),
                "geracao": _display_date(row.get("data_geracao")),
            }
        )

    return {
        "scope": _scope_payload(period, fornecedor, branch, familia, view, fornecedores, branches, familias),
        "metrics": metrics,
        "monthly": monthly,
        "fornecedores": _ranking("fornecedores"),
        "produtos": _ranking("produtos"),
        "detalhe": detalhe,
    }


# -------------------------------------------------------------- IA/snapshot --

COMPRAS_BI_SYSTEM_PROMPT = (
    "Você é um analista de compras e suprimentos. Produza somente análises "
    "sustentadas pelos indicadores enviados e respeite rigorosamente o formato JSON solicitado."
)

COMPRAS_BI_RESPONSE_SCHEMA = {
    "name": "purchasing_insights",
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
                            "enum": ["positive", "attention", "risk", "cost", "supplier", "quality"],
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


def compras_dashboard_fingerprint(dashboard):
    """Impressão dos números do recorte, para saber quando a análise envelheceu."""
    scope = dashboard["scope"]
    base = json.dumps(
        {
            "scope": [
                scope["view"]["key"],
                scope["period"]["key"],
                scope["fornecedor"]["key"],
                scope["branch"]["key"],
                scope["familia"]["key"],
            ],
            "metrics": dashboard["metrics"],
        },
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def build_compras_ai_payload(dashboard, fingerprint):
    """Payload determinístico enviado à IA."""
    scope = dashboard["scope"]
    view_key = scope["view"]["key"]

    payload = {
        "schema_version": "1.0",
        "request_type": "purchasing_intelligence",
        "source_system": "ConnectMX / ERP Senior - E420OCP/E420IPO",
        "source_fingerprint": fingerprint,
        "scope": {
            "aba": scope["view"]["label"],
            "period": scope["period"]["full_label"],
            "fornecedor": scope["fornecedor"]["label"],
            "filial": scope["branch"]["label"],
            "familia": scope["familia"]["label"],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "deterministic_metrics": {
            "totals": dashboard["metrics"],
            "monthly_series": dashboard["monthly"],
        },
        "data_quality_notes": [
            "A base de prazo (pagamento/entrega) só cobre item de ordem de compra que já "
            "virou nota de entrada no estoque (movimento ESTEOS='E'); ordem ainda em aberto "
            "ou sem nota não entra nesta análise.",
            "Dias de entrega ou de pagamento negativos (data anterior à geração da ordem) "
            "são contados como inconsistência de cadastro e excluídos das médias.",
            "O prazo real de pagamento só considera itens com pagamento já baixado "
            "(`DT_PAGAMENTO` preenchido); itens em aberto entram na contagem total, não na média.",
        ],
        "analysis_instructions": [
            "Use somente os indicadores fornecidos; não invente causas para variações.",
            "Compare o prazo real com a meta contratada antes de concluir atraso ou adiantamento.",
            "Cite números em cada evidência e escreva em português do Brasil.",
        ],
        "response_format": {"type": "json_schema", "json_schema": COMPRAS_BI_RESPONSE_SCHEMA},
    }

    if view_key in ("pagamento", "entrega"):
        payload["deterministic_metrics"].update(
            {
                "by_familia": dashboard["familias"],
                "by_origem": dashboard["origens"],
                "top_fornecedores": dashboard["fornecedores"],
            }
        )
    else:
        payload["deterministic_metrics"].update(
            {
                "top_fornecedores": dashboard["fornecedores"],
                "top_produtos": dashboard["produtos"],
            }
        )
    return payload
