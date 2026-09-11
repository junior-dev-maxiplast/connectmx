"""
BI de Romaneios — produtividade da contagem de pallets por colaborador
(USU_TCONROM), servido ao ConnectMX Dashes.

As agregações rodam no Oracle do ERP Senior, não em Python, seguindo o mesmo
princípio do BI de Viagens.

Duas coisas sobre o cadastro moldam este módulo:

1. **`USU_CODMAT` é a matrícula digitada no aparelho** no momento da leitura,
   não o login do ConnectMX (ver `hqbooking/views.py`,
   `_insert_simulation_romaneio_oracle`). O nome exibido ao lado da matrícula
   vem de `R034FUN` (cadastro de funcionários do ERP, só ativos — `SITAFA =
   1`), ligado por `NUMCAD`; isso identifica quem é quem, mas não corrige
   digitação errada — duas leituras do mesmo colaborador com matrícula
   digitada de forma diferente ainda aparecem como duas pessoas, cada uma com
   o nome de quem estiver cadastrado sob aquele número (ou nenhum nome, se a
   matrícula não bater com ninguém ativo). Essa consulta roda com uma
   credencial própria do Oracle (usuário "vetor", `_query_vetor` /
   `_oracle_connection_vetor_safe` em `customer_dna.py`) — mesmo host/porta/
   serviço do restante do módulo, usuário e senha diferentes.

2. **`USU_TIPREG` classifica o evento em 4 etapas do fluxo do pallet**: 1
   Separar, 2 Guardar, 3 Paletizar, 4 Carregar (mesma tabela usada no app
   mobile, `connectmx-mobile/src/stages.ts`, e no servidor,
   `ROMANEIO_RECORD_TYPE_LABELS`). Qualquer outro valor cai em "Outro".

Este módulo foi desenvolvido sem acesso de rede ao Oracle de produção
(`192.168.30.2`, inalcançável a partir do ambiente de build). Diferente do BI
de Viagens e do BI do TI — cujos limites de saneamento (KM, duração, SLA)
vieram de exploração direta da base —, os limites aqui são conservadores por
não terem como ser validados: peso e volume ausentes ou zerados só são
**sinalizados** (contam num indicador de qualidade de cadastro), nunca
descartados dos totais. Revisar estes limites assim que o painel rodar contra
dados de produção.
"""

import hashlib
import json
from datetime import date, datetime

from .customer_dna import _oracle_connection_safe as _erp_connection
from .customer_dna import _oracle_connection_vetor_safe as _erp_connection_vetor
from .bi_periods import mark_series_ticks, period_choices, resolve_period, series_label


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


def _query_vetor(sql, params=None):
    """Mesmo padrão de `_query`, mas com o usuário "vetor" — só o cadastro
    de funcionários (`R034FUN`) usa essa credencial, ver `_employee_names`."""
    connection = _erp_connection_vetor()
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
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value):
    return _number(value)


def _format_int(value):
    return f"{_int(value):,}".replace(",", ".")


def _format_decimal(value, places=1):
    text = f"{_float(value):,.{places}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _format_weight(value):
    return f"{_format_decimal(value, 0)} kg"


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


def _display_datetime(value):
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return "-"


def _share(part, total):
    return round(_float(part) / _float(total) * 100, 1) if _float(total) else 0.0


# --------------------------------------------------------------- vocabulário

# O CASE de rótulos da consulta original (USU_TIPREG) vive melhor aqui: dá
# para ler em SQL e em Python sem repetir os números soltos. Mesma tabela do
# app mobile (`src/stages.ts`) e do servidor de gravação (`hqbooking/views.py`,
# `ROMANEIO_RECORD_TYPE_LABELS`).
STAGE_LABELS = {1: "Separar", 2: "Guardar", 3: "Paletizar", 4: "Carregar"}
STAGE_DEFAULT_LABEL = "Outro"
STAGE_ORDER = {1: 1, 2: 2, 3: 3, 4: 4}

STAGE_CHOICES = [
    ("all", "Todas as etapas", None),
    ("1", "Separar", 1),
    ("2", "Guardar", 2),
    ("3", "Paletizar", 3),
    ("4", "Carregar", 4),
]

# Só entra no filtro quem já tem alguma regularidade nos últimos 24 meses — o
# mesmo corte de base temporal que o BI do TI usa para o filtro de atendente,
# só que aqui o piso é mais baixo porque a operação tem menos gente que abre
# chamado.
MATRICULA_MIN_RECORDS = 3

# Linhas exibidas na tabela de ranking; o restante continua nos totais do KPI,
# só não aparece linha a linha.
RANKING_LIMIT = 30


# ------------------------------------------------------------- SQL da base --

_BASE_SQL = """
WITH ROMANEIOS AS (
    SELECT
        A.USU_CODEMP AS EMPRESA,
        A.USU_CODFIL AS FILIAL,
        A.USU_NUMEMB AS EMBALAGEM,
        A.USU_CODMAT AS MATRICULA,
        A.USU_SEQCON AS LANCAMENTO,
        A.USU_CODEND AS ENDERECAMENTO,
        A.USU_QTDVOL AS VOLUMES,
        A.USU_PESROM AS PESO,
        A.USU_DATGER AS DATA_GERACAO,
        TO_CHAR(A.USU_DATGER, 'YYYY-MM') AS COMPETENCIA,
        TO_CHAR(A.USU_DATGER, 'YYYY-MM-DD') AS DIA,
        A.USU_TIPREG AS TIPREG,
        CASE
            WHEN A.USU_TIPREG = 1 THEN 'Separar'
            WHEN A.USU_TIPREG = 2 THEN 'Guardar'
            WHEN A.USU_TIPREG = 3 THEN 'Paletizar'
            WHEN A.USU_TIPREG = 4 THEN 'Carregar'
            ELSE 'Outro'
        END AS ESTAGIO
    FROM USU_TCONROM A
    WHERE {scope}
)
"""

# Uma linha por pallet (empresa + filial + embalagem), com as etapas distintas
# que ele já passou e as datas de início (Separar) e fim (Carregar) do ciclo.
# Encadeada só quando a análise processada pede — não entra na abertura normal
# da tela.
_PALLETS_CTE = """
,
PALLETS AS (
    SELECT
        EMPRESA, FILIAL, EMBALAGEM,
        COUNT(DISTINCT TIPREG) AS ETAPAS,
        MAX(CASE WHEN TIPREG = 4 THEN 1 ELSE 0 END) AS COMPLETOU,
        MIN(CASE WHEN TIPREG = 1 THEN DATA_GERACAO END) AS DT_INICIO,
        MAX(CASE WHEN TIPREG = 4 THEN DATA_GERACAO END) AS DT_FIM
    FROM ROMANEIOS
    GROUP BY EMPRESA, FILIAL, EMBALAGEM
)
"""


def _base(scope_sql):
    return _BASE_SQL.format(scope=scope_sql)


def _base_with_pallets(scope_sql):
    return _base(scope_sql) + _PALLETS_CTE


# ------------------------------------------------------------- filtros ----

def list_branches():
    """Combinações empresa+filial com registro, para montar o filtro.

    O BI de Viagens descartou `USU_CODEMP` como filtro porque a base de
    viagens tem sempre o mesmo valor — aqui não há como confirmar isso sem
    acesso à base (ver docstring do módulo), então o rótulo só mostra a
    empresa quando o catálogo trouxer mais de uma.
    """
    rows = _query(
        """
        SELECT A.USU_CODEMP AS EMPRESA, A.USU_CODFIL AS FILIAL, COUNT(*) AS TOTAL
        FROM USU_TCONROM A
        GROUP BY A.USU_CODEMP, A.USU_CODFIL
        ORDER BY TOTAL DESC
        """
    )
    single_company = len({_int(row.get("empresa")) for row in rows}) <= 1
    branches = []
    for row in rows:
        empresa = _int(row.get("empresa"))
        filial = _int(row.get("filial"))
        branches.append(
            {
                "key": f"{empresa}-{filial}",
                "empresa": empresa,
                "filial": filial,
                "label": f"Filial {filial}" if single_company else f"Empresa {empresa} / Filial {filial}",
                "total": _int(row.get("total")),
            }
        )
    return branches


def resolve_branch(raw_value, available=None):
    value = (raw_value or "all").strip()
    if not value or value == "all":
        return {"key": "all", "label": "Todas as filiais", "empresa": None, "filial": None}
    catalog = {item["key"]: item for item in (available or [])}
    item = catalog.get(value)
    if item is None:
        return {"key": "all", "label": "Todas as filiais", "empresa": None, "filial": None}
    return {"key": item["key"], "label": item["label"], "empresa": item["empresa"], "filial": item["filial"]}


def resolve_stage(raw_value):
    value = (raw_value or "all").strip()
    for key, label, code in STAGE_CHOICES:
        if key == value:
            return {"key": key, "label": label, "code": code}
    return {"key": "all", "label": "Todas as etapas", "code": None}


def _employee_names():
    """Nome por matrícula, direto do cadastro de funcionários do ERP.

    Roda com o usuário "vetor" (`_query_vetor`) — é o cadastro de
    funcionários (`R034FUN`), não a base de romaneios, e usa uma credencial
    própria (mesmo host/porta/serviço, usuário e senha diferentes do resto
    deste módulo).

    Só entram funcionários ativos (`SITAFA = 1`): quem já saiu da empresa
    continua aparecendo no ranking pela matrícula, sem nome — do jeito que já
    aparecia antes desta consulta existir.
    """
    rows = _query_vetor(
        """
        SELECT A.NUMCAD AS MATRICULA, A.NOMFUN AS NOME
        FROM R034FUN A
        WHERE A.SITAFA = 1
        """
    )
    return {_int(row.get("matricula")): (row.get("nome") or "").strip() for row in rows}


def list_matriculas(names=None):
    """Matrículas com alguma regularidade nos últimos 24 meses, para o filtro."""
    rows = _query(
        """
        SELECT A.USU_CODMAT AS MATRICULA, COUNT(*) AS TOTAL
        FROM USU_TCONROM A
        WHERE A.USU_DATGER >= ADD_MONTHS(TRUNC(SYSDATE), -24)
        GROUP BY A.USU_CODMAT
        HAVING COUNT(*) >= :minimo
        ORDER BY TOTAL DESC
        """,
        {"minimo": MATRICULA_MIN_RECORDS},
    )
    names = names if names is not None else _employee_names()
    result = []
    for row in rows:
        matricula = _int(row.get("matricula"))
        nome = names.get(matricula)
        result.append(
            {
                "key": str(matricula),
                "label": f"{matricula} - {nome}" if nome else f"Matrícula {matricula}",
                "nome": nome or "",
                "total": _int(row.get("total")),
            }
        )
    return result


def resolve_matricula(raw_value, available=None):
    value = (raw_value or "").strip()
    if not value or value == "all":
        return {"key": "all", "label": "Todos os colaboradores", "code": None}
    catalog = {item["key"]: item for item in (available or [])}
    item = catalog.get(value)
    if item is not None:
        return {"key": item["key"], "label": item["label"], "code": int(item["key"])}
    # Matrícula fora do catálogo dos "regulares" (abaixo de MATRICULA_MIN_RECORDS
    # nos últimos 24 meses) ainda é um código válido se vier direto na URL —
    # aceita, só sem contar com o rótulo do catálogo.
    if value.isdigit():
        return {"key": value, "label": f"Matrícula {value}", "code": int(value)}
    return {"key": "all", "label": "Todos os colaboradores", "code": None}


def _scope_sql(period, branch, stage, matricula, ignore_period=False):
    """Cláusulas de filtro compartilhadas por todas as agregações."""
    clauses = ["1 = 1"]
    params = {}
    if not ignore_period:
        if period.get("start") and period.get("end"):
            clauses.append("A.USU_DATGER >= :period_start AND A.USU_DATGER < :period_end")
            params["period_start"] = period["start"]
            params["period_end"] = period["end"]
        elif period.get("months"):
            clauses.append("A.USU_DATGER >= ADD_MONTHS(TRUNC(SYSDATE), -:months)")
            params["months"] = period["months"]
    if branch["empresa"] is not None:
        clauses.append("A.USU_CODEMP = :branch_empresa")
        params["branch_empresa"] = branch["empresa"]
    if branch["filial"] is not None:
        clauses.append("A.USU_CODFIL = :branch_filial")
        params["branch_filial"] = branch["filial"]
    if stage["code"] is not None:
        clauses.append("A.USU_TIPREG = :stage")
        params["stage"] = stage["code"]
    if matricula["code"] is not None:
        clauses.append("A.USU_CODMAT = :matricula")
        params["matricula"] = matricula["code"]
    return " AND ".join(clauses), params


# ---------------------------------------------------------- agregações ----

def load_romaneio_dashboard(
    period_key="12",
    branch_key="all",
    stage_key="all",
    matricula_key="all",
    branches_available=None,
    matriculas_available=None,
):
    period = resolve_period(period_key)
    branches = branches_available if branches_available is not None else list_branches()
    branch = resolve_branch(branch_key, branches)
    names = _employee_names()
    matriculas = matriculas_available if matriculas_available is not None else list_matriculas(names)
    matricula = resolve_matricula(matricula_key, matriculas)
    stage = resolve_stage(stage_key)

    scope, params = _scope_sql(period, branch, stage, matricula)
    base = _base(scope)

    # Num recorte de um mês a série vira diária: uma barra só por competência
    # não diz nada que o KPI logo acima já não diga.
    series_column = "DIA" if period["granularity"] == "day" else "COMPETENCIA"

    statements = {
        "totals": f"""
            {base}
            SELECT
                COUNT(*) AS REGISTROS,
                NVL(SUM(VOLUMES), 0) AS VOLUMES,
                NVL(SUM(PESO), 0) AS PESO,
                COUNT(DISTINCT MATRICULA) AS COLABORADORES,
                COUNT(DISTINCT EMPRESA || '-' || FILIAL || '-' || EMBALAGEM) AS PALLETS,
                SUM(CASE WHEN PESO IS NULL OR PESO <= 0 THEN 1 ELSE 0 END) AS SEM_PESO,
                SUM(CASE WHEN VOLUMES IS NULL OR VOLUMES <= 0 THEN 1 ELSE 0 END) AS SEM_VOLUME
            FROM ROMANEIOS
        """,
        "series": f"""
            {base}
            SELECT {series_column} AS COMPETENCIA, TIPREG, COUNT(*) AS TOTAL
            FROM ROMANEIOS
            GROUP BY {series_column}, TIPREG
            ORDER BY {series_column}
        """,
        "by_stage": f"""
            {base}
            SELECT TIPREG, ESTAGIO, COUNT(*) AS TOTAL,
                   NVL(SUM(VOLUMES), 0) AS VOLUMES, NVL(SUM(PESO), 0) AS PESO
            FROM ROMANEIOS
            GROUP BY TIPREG, ESTAGIO
        """,
        "by_branch": f"""
            {base}
            SELECT EMPRESA, FILIAL, COUNT(*) AS TOTAL
            FROM ROMANEIOS
            GROUP BY EMPRESA, FILIAL
            ORDER BY TOTAL DESC
        """,
        "ranking": f"""
            {base}
            SELECT
                MATRICULA,
                COUNT(*) AS REGISTROS,
                NVL(SUM(VOLUMES), 0) AS VOLUMES,
                NVL(SUM(PESO), 0) AS PESO,
                COUNT(DISTINCT DIA) AS DIAS_TRABALHADOS,
                COUNT(DISTINCT EMPRESA || '-' || FILIAL || '-' || EMBALAGEM) AS PALLETS
            FROM ROMANEIOS
            GROUP BY MATRICULA
            ORDER BY REGISTROS DESC
        """,
    }
    data = _query_many(statements, {name: params for name in statements})
    return _build_romaneio_dashboard(data, period, branch, stage, matricula, branches, matriculas, names)


def _build_romaneio_dashboard(data, period, branch, stage, matricula, branches, matriculas, names):
    totals = (data["totals"] or [{}])[0]
    registros = _int(totals.get("registros"))
    volumes = _int(totals.get("volumes"))
    peso = _float(totals.get("peso"))
    colaboradores = _int(totals.get("colaboradores"))
    pallets = _int(totals.get("pallets"))
    sem_peso = _int(totals.get("sem_peso"))
    sem_volume = _int(totals.get("sem_volume"))

    granularity = period["granularity"]

    # Série empilhada por etapa: uma barra por competência/dia, dividida nas 4
    # etapas + "outro". `stage_shares` é a participação de cada etapa dentro
    # da própria barra (soma 100%), para desenhar o empilhamento no template
    # sem repetir a conta lá.
    series_map = {}
    order = []
    for row in data["series"]:
        key = str(row.get("competencia") or "")
        if key not in series_map:
            series_map[key] = {
                "competencia": key,
                "label": series_label(key, granularity),
                "total": 0,
                "stages": {1: 0, 2: 0, 3: 0, 4: 0, "outro": 0},
            }
            order.append(key)
        bucket = series_map[key]
        total_stage = _int(row.get("total"))
        bucket["total"] += total_stage
        code = _int(row.get("tipreg")) if row.get("tipreg") is not None else None
        bucket["stages"][code if code in (1, 2, 3, 4) else "outro"] += total_stage

    series = [series_map[key] for key in order]
    mark_series_ticks(series)
    peak = max((item["total"] for item in series), default=0)
    for item in series:
        item["height_pct"] = round(item["total"] / peak * 100, 1) if peak else 0.0
        stage_total = item["total"] or 1
        item["stage_shares"] = {
            str(stage_key): round(stage_value / stage_total * 100, 1)
            for stage_key, stage_value in item["stages"].items()
        }
        # Chaves em texto: o template lê `item.stages.1` como busca de
        # dicionário, e mistura int/"outro" já bastou confundir uma vez.
        item["stages"] = {str(stage_key): stage_value for stage_key, stage_value in item["stages"].items()}

    by_stage = []
    for row in data["by_stage"]:
        code = _int(row.get("tipreg")) if row.get("tipreg") is not None else None
        total_stage = _int(row.get("total"))
        by_stage.append(
            {
                "code": code,
                "label": STAGE_LABELS.get(code, STAGE_DEFAULT_LABEL),
                "total": total_stage,
                "total_display": _format_int(total_stage),
                "volumes": _int(row.get("volumes")),
                "peso": _float(row.get("peso")),
                "peso_display": _format_weight(row.get("peso")),
                "share_pct": _share(total_stage, registros),
            }
        )
    by_stage.sort(key=lambda item: STAGE_ORDER.get(item["code"], 99))

    by_branch = []
    branch_labels = {(item["empresa"], item["filial"]): item["label"] for item in branches}
    for row in data["by_branch"]:
        empresa = _int(row.get("empresa"))
        filial = _int(row.get("filial"))
        total_branch = _int(row.get("total"))
        by_branch.append(
            {
                "key": f"{empresa}-{filial}",
                "label": branch_labels.get((empresa, filial), f"Filial {filial}"),
                "total": total_branch,
                "total_display": _format_int(total_branch),
                "share_pct": _share(total_branch, registros),
            }
        )

    ranking_rows = []
    for row in data["ranking"]:
        registros_colab = _int(row.get("registros"))
        matricula_num = _int(row.get("matricula"))
        ranking_rows.append(
            {
                "matricula": matricula_num,
                "nome": names.get(matricula_num) or "",
                "registros": registros_colab,
                "registros_display": _format_int(registros_colab),
                "volumes": _int(row.get("volumes")),
                "volumes_display": _format_int(row.get("volumes")),
                "peso": _float(row.get("peso")),
                "peso_display": _format_weight(row.get("peso")),
                "dias_trabalhados": _int(row.get("dias_trabalhados")),
                "pallets": _int(row.get("pallets")),
                "share_pct": _share(registros_colab, registros),
            }
        )

    return {
        "scope": {
            "period": period,
            "branch": branch,
            "stage": stage,
            "matricula": matricula,
            "period_choices": period_choices(),
            "series_granularity": granularity,
            "series_label": "por dia" if granularity == "day" else "por mês",
            "branch_choices": [{"key": "all", "label": "Todas as filiais"}]
            + [{"key": item["key"], "label": item["label"]} for item in branches],
            "stage_choices": [{"key": key, "label": label} for key, label, _code in STAGE_CHOICES],
            "matricula_choices": [{"key": "all", "label": "Todos os colaboradores"}]
            + [{"key": item["key"], "label": item["label"]} for item in matriculas],
        },
        "metrics": {
            "records": registros,
            "records_display": _format_int(registros),
            "volumes": volumes,
            "volumes_display": _format_int(volumes),
            "weight": peso,
            "weight_display": _format_weight(peso),
            "employees": colaboradores,
            "employees_display": _format_int(colaboradores),
            "pallets": pallets,
            "pallets_display": _format_int(pallets),
            "records_per_employee_display": (
                _format_decimal(registros / colaboradores, 1) if colaboradores else "-"
            ),
            "missing_weight": sem_peso,
            "missing_weight_pct": _share(sem_peso, registros),
            "missing_volume": sem_volume,
            "missing_volume_pct": _share(sem_volume, registros),
        },
        "series": series,
        "by_stage": by_stage,
        "by_branch": by_branch,
        "ranking": ranking_rows[:RANKING_LIMIT],
        "ranking_total_employees": len(ranking_rows),
    }


# --------------------------------------------------- análises processadas ---

# Funil de conclusão do pallet e tempo de ciclo: cruzam o histórico inteiro do
# pallet (todas as etapas, ignorando o filtro de etapa) e custam mais para
# rodar do que a abertura normal da tela. Só entram quando o usuário pede os
# indicadores, e ficam guardados no snapshot — mesmo modelo do BI do TI e do
# BI de Viagens.

def compute_deep_analytics(period_key="12", branch_key="all", stage_key="all", matricula_key="all"):
    period = resolve_period(period_key)
    branches = list_branches()
    branch = resolve_branch(branch_key, branches)
    matriculas = list_matriculas()
    matricula = resolve_matricula(matricula_key, matriculas)

    # O funil conta etapas distintas por pallet: filtrar por etapa aqui
    # quebraria essa contagem, então a análise processada sempre olha o
    # pallet inteiro, independente do filtro de etapa da tela.
    all_stages = {"key": "all", "label": "Todas as etapas", "code": None}
    scope, params = _scope_sql(period, branch, all_stages, matricula)
    base = _base_with_pallets(scope)

    statements = {
        "funnel": f"""
            {base}
            SELECT
                COUNT(*) AS TOTAL_PALLETS,
                SUM(COMPLETOU) AS COMPLETOS,
                SUM(CASE WHEN ETAPAS = 1 THEN 1 ELSE 0 END) AS ETAPA_1,
                SUM(CASE WHEN ETAPAS = 2 THEN 1 ELSE 0 END) AS ETAPA_2,
                SUM(CASE WHEN ETAPAS = 3 THEN 1 ELSE 0 END) AS ETAPA_3,
                SUM(CASE WHEN ETAPAS >= 4 THEN 1 ELSE 0 END) AS ETAPA_4,
                AVG(CASE WHEN DT_INICIO IS NOT NULL AND DT_FIM IS NOT NULL AND DT_FIM >= DT_INICIO
                         THEN DT_FIM - DT_INICIO END) AS LEAD_TIME_MEDIO,
                SUM(CASE WHEN DT_INICIO IS NOT NULL AND DT_FIM IS NOT NULL AND DT_FIM >= DT_INICIO
                         THEN 1 ELSE 0 END) AS LEAD_TIME_BASE
            FROM PALLETS
        """,
        "top_workdays": f"""
            {_base(scope)}
            SELECT MATRICULA, COUNT(DISTINCT DIA) AS DIAS, COUNT(*) AS REGISTROS
            FROM ROMANEIOS
            GROUP BY MATRICULA
            ORDER BY DIAS DESC, REGISTROS DESC
            FETCH FIRST 10 ROWS ONLY
        """,
    }
    data = _query_many(statements, {name: params for name in statements})
    names = _employee_names()
    return _build_deep_analytics(data, names)


def _build_deep_analytics(data, names):
    funnel_row = (data["funnel"] or [{}])[0]
    total_pallets = _int(funnel_row.get("total_pallets"))
    completos = _int(funnel_row.get("completos"))
    stage_counts = {
        1: _int(funnel_row.get("etapa_1")),
        2: _int(funnel_row.get("etapa_2")),
        3: _int(funnel_row.get("etapa_3")),
        4: _int(funnel_row.get("etapa_4")),
    }
    funnel = {
        "total": total_pallets,
        "total_display": _format_int(total_pallets),
        "completed": completos,
        "completed_display": _format_int(completos),
        "completed_pct": _share(completos, total_pallets),
        "stopped_stage_1": stage_counts[1],
        "stopped_stage_1_pct": _share(stage_counts[1], total_pallets),
        "stopped_stage_2": stage_counts[2],
        "stopped_stage_2_pct": _share(stage_counts[2], total_pallets),
        "stopped_stage_3": stage_counts[3],
        "stopped_stage_3_pct": _share(stage_counts[3], total_pallets),
        "reached_stage_4": stage_counts[4],
        "reached_stage_4_pct": _share(stage_counts[4], total_pallets),
        "lead_time_days": round(_float(funnel_row.get("lead_time_medio")), 2),
        "lead_time_display": _format_days(funnel_row.get("lead_time_medio")),
        "lead_time_base": _int(funnel_row.get("lead_time_base")),
    }

    top_workdays = []
    for row in data["top_workdays"]:
        matricula_num = _int(row.get("matricula"))
        top_workdays.append(
            {
                "matricula": matricula_num,
                "nome": names.get(matricula_num) or "",
                "days": _int(row.get("dias")),
                "records": _int(row.get("registros")),
                "records_display": _format_int(row.get("registros")),
            }
        )
    peak_days = max((item["days"] for item in top_workdays), default=0)
    for item in top_workdays:
        item["share_pct"] = round(item["days"] / peak_days * 100, 1) if peak_days else 0.0

    return {"funnel": funnel, "top_workdays": top_workdays}


# ------------------------------------------------------------ análise de IA --

ROMANEIO_BI_SYSTEM_PROMPT = (
    "Você é um analista de operações logísticas e produtividade de mão de obra. "
    "Produza somente análises sustentadas pelos indicadores enviados e respeite "
    "rigorosamente o formato JSON solicitado."
)

ROMANEIO_BI_RESPONSE_SCHEMA = {
    "name": "pallet_counting_productivity_insights",
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
                        "type": {"type": "string", "enum": ["positive", "attention", "risk", "capacity", "quality"]},
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


def romaneio_dashboard_fingerprint(dashboard):
    """Impressão dos números do recorte, para saber quando a análise envelheceu."""
    base = json.dumps(
        {
            "scope": [
                dashboard["scope"]["period"]["key"],
                dashboard["scope"]["branch"]["key"],
                dashboard["scope"]["stage"]["key"],
                dashboard["scope"]["matricula"]["key"],
            ],
            "metrics": dashboard["metrics"],
            "by_stage": dashboard["by_stage"],
            "by_branch": dashboard["by_branch"],
        },
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def build_romaneio_ai_payload(dashboard, fingerprint, deep=None):
    """Payload determinístico enviado à IA."""
    scope = dashboard["scope"]
    metrics = dashboard["metrics"]
    deep = deep or {}
    lead_time_base = (deep.get("funnel") or {}).get("lead_time_base", 0)

    return {
        "schema_version": "1.0",
        "request_type": "pallet_counting_productivity",
        "source_system": "ConnectMX / ERP Senior - USU_TCONROM",
        "source_fingerprint": fingerprint,
        "scope": {
            "period": scope["period"]["full_label"],
            "branch": scope["branch"]["label"],
            "stage": scope["stage"]["label"],
            "employee": scope["matricula"]["label"],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "deterministic_metrics": {
            "volume": metrics,
            "stage_breakdown": dashboard["by_stage"],
            "branch_breakdown": dashboard["by_branch"],
            "series": [{"label": item["label"], "total": item["total"]} for item in dashboard["series"]],
            "top_employees": [
                {
                    key: row[key]
                    for key in (
                        "matricula", "nome", "registros_display", "volumes_display",
                        "peso_display", "dias_trabalhados", "pallets", "share_pct",
                    )
                }
                for row in dashboard["ranking"][:15]
            ],
            "employees_ranked_total": dashboard["ranking_total_employees"],
            "pallet_funnel": deep.get("funnel"),
            "top_workdays": deep.get("top_workdays"),
        },
        "data_quality_notes": [
            "USU_CODMAT é a matrícula digitada no aparelho no momento da leitura, não o login "
            "do ConnectMX — o mesmo colaborador pode aparecer sob mais de uma matrícula se "
            "digitar errado, e isso não é corrigido aqui.",
            f"Peso do romaneio ausente ou zerado em {metrics['missing_weight_pct']}% dos registros, "
            f"e volume ausente ou zerado em {metrics['missing_volume_pct']}%. Ambos entram nos "
            "totais de registros — não há limite de peso/volume plausível validado contra a base "
            "real para descartar picos como erro de digitação.",
            "A etapa (USU_TIPREG) só tem 4 valores mapeados (Separar, Guardar, Paletizar, "
            "Carregar); qualquer outro código aparece como 'Outro'.",
            "O funil de conclusão conta etapas distintas por pallet (empresa + filial + "
            "embalagem), não a ordem em que ocorreram: um pallet com Separar e Carregar mas sem "
            "Guardar nem Paletizar registrados ainda conta como 2 etapas, não como concluído.",
            f"O tempo de ciclo (lead time) só é calculado para pallets com Separar e Carregar "
            f"registrados nesta janela — {lead_time_base} pallets.",
        ],
        "analysis_instructions": [
            "Use somente os indicadores fornecidos; não invente causas para variações.",
            "Trate matrícula como identificador operacional, não como julgamento de desempenho "
            "individual isolado — cite sempre o volume de registros por trás de uma taxa ou "
            "posição no ranking.",
            "Separe problema de processo (pallet que não completa o ciclo, etapa pulada) de "
            "problema de cadastro (peso ou volume ausente).",
            "Ao comparar colaboradores, leve em conta os dias trabalhados no período, não só o "
            "total de registros — quem trabalhou menos dias não é necessariamente menos produtivo.",
            "Cite números em cada evidência e escreva em português do Brasil.",
        ],
        "response_format": {"type": "json_schema", "json_schema": ROMANEIO_BI_RESPONSE_SCHEMA},
    }
