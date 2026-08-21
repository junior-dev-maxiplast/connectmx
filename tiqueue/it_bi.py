"""
BI do TI — indicadores do helpdesk (SM), servidos ao painel do ConnectMX Dashes.

As agregações são feitas no MySQL, não em Python: a base tem 10 mil chamados e
trazer tudo a cada request só para somar seria desperdício. A conexão usa as
mesmas variáveis de ambiente da sincronização de tarefas da fila de demandas.
"""

import json
import os
from datetime import date, datetime
from decimal import Decimal

# Periodo e vocabulario comum dos paineis do Dashes, nao regra do SM: o BI de
# Viagens usa exatamente os mesmos recortes.
from .bi_periods import (
    ALL_TIME,
    mark_series_ticks,
    period_choices,
    resolve_period,
    series_label,
)


# O CASE de nomes da consulta original vive melhor aqui: dá para acrescentar um
# atendente sem mexer em SQL, e quem não estiver no mapa aparece pelo login.
ATTENDANT_NAMES = {
    "junior.ribeiro": "Junior Ribeiro",
    "anderson.palenski": "Anderson Palenski",
    "guilherme.belo@maxiplast.com.br": "Guilherme Belo",
    "guilherme.surdi": "Guilherme Surdi",
    "gustavo.campioni": "Gustavo Campioni",
    "leonardo.goncalves": "Leonardo Gonçalves",
    "marcos.fernandes": "Marcos Fernandes",
    "evandro.bloot": "Evandro Bloot",
    "rafael.cuccarolo": "Rafael Cuccarolo",
    "luiz.vatrin": "Luiz Vatrin",
}

COMPANY_NAMES = {2: "Magnaplast", 3: "Matriz", 4: "Ráfia", 5: "Serafina"}
COMPANY_DEFAULT = "Diversos"

# Status 6 e 13 ficam de fora, como na consulta original; 5 é "Fechado".
EXCLUDED_STATUS = (6, 13)
CLOSED_STATUS = 5

# `time_in_sla` é int de segundos, mas a coluna tem lixo: 72 registros negativos
# e 485 acima de 30 dias (alguns são timestamp Unix inteiro). Fora desta faixa o
# valor é ignorado em vez de contaminar as médias.
SLA_MIN_SECONDS = 0
SLA_MAX_SECONDS = 30 * 24 * 3600

# O SLA de verdade mora em `helpdesk_has_sla`: prazo de primeira resposta e de
# conclusao por chamado. O sistema marca o estouro em `*_expired_in`, que é mais
# fiel do que recalcular na mão (pega também o que estourou e segue aberto).
RATING_BAD_MAX = 5
RATING_GOOD_MIN = 9

AGING_BUCKETS = [
    ("ate_7", "Até 7 dias", 0, 7),
    ("de_8_a_30", "8 a 30 dias", 8, 30),
    ("de_31_a_90", "31 a 90 dias", 31, 90),
    ("acima_90", "Mais de 90 dias", 91, None),
]


def _sm_connection():
    """Conexão com o banco do SM, tentando os drivers disponíveis na máquina."""
    host = os.getenv("SM_DB_HOST", "192.168.0.209")
    port = int(os.getenv("SM_DB_PORT", "3306"))
    db_name = os.getenv("SM_DB_NAME", "sm")
    db_user = os.getenv("SM_DB_USER", "sm_viewer")
    db_pass = os.getenv("SM_DB_PASSWORD", "KcyVbd66h@UnvZ")

    driver_errors = []
    try:
        import pymysql  # type: ignore

        return pymysql.connect(
            host=host, user=db_user, password=db_pass, database=db_name,
            port=port, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        ), "pymysql"
    except Exception as exc:
        driver_errors.append(f"pymysql: {exc}")

    try:
        import mysql.connector  # type: ignore

        return mysql.connector.connect(
            host=host, user=db_user, password=db_pass, database=db_name, port=port
        ), "mysql-connector"
    except Exception as exc:
        driver_errors.append(f"mysql-connector: {exc}")

    raise RuntimeError("Nao foi possivel conectar no SM. " + " | ".join(driver_errors))


def _dict_cursor(connection, driver):
    if driver == "mysql-connector":
        return connection.cursor(dictionary=True)
    return connection.cursor()


def _query(sql, params=None):
    connection, driver = _sm_connection()
    cursor = None
    try:
        cursor = _dict_cursor(connection, driver)
        cursor.execute(sql, params or ())
        return list(cursor.fetchall() or [])
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def _query_many(statements, params):
    """Roda várias agregações em uma conexão só."""
    connection, driver = _sm_connection()
    results = {}
    try:
        for name, sql in statements.items():
            cursor = _dict_cursor(connection, driver)
            try:
                cursor.execute(sql, params.get(name, ()))
                results[name] = list(cursor.fetchall() or [])
            finally:
                cursor.close()
    finally:
        connection.close()
    return results


# ------------------------------------------------------------------ helpers --

def _number(value):
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _format_int(value):
    return f"{_int(value):,}".replace(",", ".")


def _format_duration(seconds):
    """Segundos em texto curto: '3d 4h', '5h 12min', '48min'."""
    total = int(float(seconds or 0))
    if total <= 0:
        return "-"
    minutes, _ = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}min" if minutes else f"{hours}h"
    return f"{minutes}min"


def _format_decimal(value, places=1):
    return f"{float(value or 0):.{places}f}".replace(".", ",")


def _display_datetime(value):
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return "-"


def _attendant_name(login):
    login = (login or "").strip()
    if not login:
        return "Sem atendente"
    return ATTENDANT_NAMES.get(login, login)


def _share(part, total):
    return round(float(_number(part) / _number(total) * 100), 1) if _number(total) else 0.0


def resolve_company(raw_value):
    value = (raw_value or "").strip()
    if not value or value == "all":
        return {"key": "all", "label": "Todas as empresas", "id": None}
    try:
        company_id = int(value)
    except (TypeError, ValueError):
        return {"key": "all", "label": "Todas as empresas", "id": None}
    return {
        "key": str(company_id),
        "label": COMPANY_NAMES.get(company_id, COMPANY_DEFAULT),
        "id": company_id,
    }


def list_attendants():
    """Logins com chamados atribuídos, para montar o filtro de atendente."""
    rows = _query(
        f"""
        SELECT D.login AS login, COUNT(*) AS total
        FROM helpdesk.helpdesk A
        INNER JOIN user D ON D.id = A.user_id_attendent
        WHERE A.status_id NOT IN ({', '.join(str(item) for item in EXCLUDED_STATUS)})
          AND A.date_open >= DATE_SUB(CURDATE(), INTERVAL 24 MONTH)
        GROUP BY D.login
        HAVING COUNT(*) >= 5
        ORDER BY total DESC
        """
    )
    return [
        {"key": row["login"], "label": _attendant_name(row["login"]), "total": _int(row["total"])}
        for row in rows
        if (row.get("login") or "").strip()
    ]


def resolve_attendant(raw_value, available=None):
    """Normaliza o filtro de atendente. Login desconhecido cai para 'todos'."""
    value = (raw_value or "").strip()
    if not value or value == "all":
        return {"key": "all", "label": "Todos os atendentes", "login": None}
    if available is not None and value not in {item["key"] for item in available}:
        return {"key": "all", "label": "Todos os atendentes", "login": None}
    return {"key": value, "label": _attendant_name(value), "login": value}


def _scope_sql(period, company, attendant=None, date_column="A.date_open"):
    """Cláusulas de filtro compartilhadas por todas as agregações."""
    clauses = [f"A.status_id NOT IN ({', '.join(str(item) for item in EXCLUDED_STATUS)})"]
    params = []
    if period.get("start") and period.get("end"):
        # Mês fechado: intervalo com fim exclusivo, vindo pronto do Python. Faz
        # a conta no banco exigiria `DATE_FORMAT(..., '%Y-%m-01')`, e esse `%`
        # briga com o marcador de parâmetro do driver.
        clauses.append(f"{date_column} >= %s AND {date_column} < %s")
        params.extend([period["start"], period["end"]])
    elif period.get("months"):
        clauses.append(f"{date_column} >= DATE_SUB(CURDATE(), INTERVAL %s MONTH)")
        params.append(period["months"])
    if company["id"] is not None:
        clauses.append("A.organization_id = %s")
        params.append(company["id"])
    if attendant and attendant.get("login"):
        # Subquery em vez de JOIN: as agregações já têm os próprios joins e
        # acrescentar mais um mudaria a contagem em algumas delas.
        clauses.append(
            "A.user_id_attendent IN (SELECT U.id FROM user U WHERE U.login = %s)"
        )
        params.append(attendant["login"])
    return " AND ".join(clauses), params


# ------------------------------------------------------------- agregações ---

def load_it_dashboard(period_key="12", company_key="all", attendant_key="all", attendants_available=None):
    period = resolve_period(period_key)
    company = resolve_company(company_key)
    available = attendants_available if attendants_available is not None else list_attendants()
    attendant = resolve_attendant(attendant_key, available)
    where_open, params_open = _scope_sql(period, company, attendant)
    # O backlog ignora o filtro de período de propósito: um chamado aberto há
    # dois anos continua sendo backlog de hoje, e some se filtrado por data.
    where_backlog, params_backlog = _scope_sql(ALL_TIME, company, attendant)

    sla_filter = f"A.time_in_sla BETWEEN {SLA_MIN_SECONDS} AND {SLA_MAX_SECONDS}"

    # Num recorte de um mês a série vira diária: `DATE()` em vez da competência.
    # Nada de `DATE_FORMAT(..., '%Y-%m-%d')` — esse `%` briga com o marcador de
    # parâmetro do driver.
    series_expression = (
        "DATE(A.date_open)"
        if period["granularity"] == "day"
        else "CONCAT(YEAR(A.date_open), '-', LPAD(MONTH(A.date_open), 2, '0'))"
    )

    statements = {
        "totals": f"""
            SELECT
              COUNT(*) AS chamados,
              SUM(A.status_id = {CLOSED_STATUS}) AS fechados,
              SUM(A.status_id <> {CLOSED_STATUS}) AS abertos,
              AVG(CASE WHEN A.date_close IS NOT NULL
                       THEN TIMESTAMPDIFF(SECOND, A.date_open, A.date_close) END) AS tempo_resolucao,
              AVG(CASE WHEN A.total_attending_time BETWEEN 1 AND {SLA_MAX_SECONDS}
                       THEN A.total_attending_time END) AS tempo_atendimento,
              AVG(CASE WHEN {sla_filter} THEN A.time_in_sla END) AS tempo_sla,
              SUM({sla_filter}) AS sla_validos
            FROM helpdesk.helpdesk A
            WHERE {where_open}
        """,
        "rating": f"""
            SELECT COUNT(*) AS avaliacoes, AVG(E.rating_average) AS nota
            FROM helpdesk.helpdesk A
            INNER JOIN helpdesk.rating E ON E.helpdesk_id = A.id
            WHERE {where_open} AND E.rating_average IS NOT NULL
        """,
        "monthly": f"""
            SELECT {series_expression} AS competencia,
                   COUNT(*) AS abertos,
                   SUM(A.status_id = {CLOSED_STATUS}) AS fechados
            FROM helpdesk.helpdesk A
            WHERE {where_open}
            GROUP BY {series_expression}
            ORDER BY competencia
        """,
        "backlog_status": f"""
            SELECT B.name AS rotulo, COUNT(*) AS total
            FROM helpdesk.helpdesk A
            LEFT JOIN helpdesk.status B ON B.id = A.status_id
            WHERE {where_backlog} AND A.status_id <> {CLOSED_STATUS}
            GROUP BY B.name ORDER BY total DESC
        """,
        "priority": f"""
            SELECT C.name AS rotulo, COUNT(*) AS total,
                   AVG(CASE WHEN A.date_close IS NOT NULL
                            THEN TIMESTAMPDIFF(SECOND, A.date_open, A.date_close) END) AS tempo
            FROM helpdesk.helpdesk A
            LEFT JOIN helpdesk.priority C ON C.id = A.priority_id
            WHERE {where_open}
            GROUP BY C.name ORDER BY total DESC
        """,
        "attendants": f"""
            SELECT D.login AS login,
                   COUNT(*) AS total,
                   SUM(A.status_id = {CLOSED_STATUS}) AS fechados,
                   AVG(CASE WHEN A.date_close IS NOT NULL
                            THEN TIMESTAMPDIFF(SECOND, A.date_open, A.date_close) END) AS tempo,
                   SUM(COALESCE(A.total_attending_time, 0)) AS apontado
            FROM helpdesk.helpdesk A
            LEFT JOIN user D ON D.id = A.user_id_attendent
            WHERE {where_open}
            GROUP BY D.login ORDER BY total DESC
        """,
        "attendant_rating": f"""
            SELECT D.login AS login, AVG(E.rating_average) AS nota, COUNT(*) AS avaliacoes
            FROM helpdesk.helpdesk A
            INNER JOIN helpdesk.rating E ON E.helpdesk_id = A.id
            LEFT JOIN user D ON D.id = A.user_id_attendent
            WHERE {where_open} AND E.rating_average IS NOT NULL
            GROUP BY D.login
        """,
        "types": f"""
            SELECT I.name AS rotulo, COUNT(*) AS total
            FROM helpdesk.helpdesk A
            LEFT JOIN helpdesk.type I ON I.id = A.type_id
            WHERE {where_open}
            GROUP BY I.name ORDER BY total DESC LIMIT 10
        """,
        "service_areas": f"""
            SELECT G.name AS rotulo, COUNT(*) AS total
            FROM helpdesk.helpdesk A
            LEFT JOIN helpdesk.service_departament G ON G.id = A.departament_id
            WHERE {where_open}
            GROUP BY G.name ORDER BY total DESC LIMIT 8
        """,
        "requester_areas": f"""
            SELECT H.name AS rotulo, COUNT(*) AS total
            FROM helpdesk.helpdesk A
            LEFT JOIN user F ON F.id = A.user_id_owner
            LEFT JOIN departament H ON H.id = F.departament_id
            WHERE {where_open}
            GROUP BY H.name ORDER BY total DESC LIMIT 8
        """,
        "companies": f"""
            SELECT A.organization_id AS empresa_id, COUNT(*) AS total
            FROM helpdesk.helpdesk A
            WHERE {where_open}
            GROUP BY A.organization_id ORDER BY total DESC
        """,
        "origins": f"""
            SELECT A.created_from AS rotulo, COUNT(*) AS total
            FROM helpdesk.helpdesk A
            WHERE {where_open}
            GROUP BY A.created_from ORDER BY total DESC
        """,
        "sla": f"""
            SELECT
              COUNT(*) AS com_sla,
              SUM(S.conclusion_time_expired_in IS NOT NULL) AS conclusao_vencida,
              SUM(S.first_response_time_expired_in IS NOT NULL) AS resposta_vencida,
              AVG(S.sla_conclusion_time) AS prazo_conclusao,
              AVG(S.sla_first_response_time) AS prazo_resposta,
              AVG(CASE WHEN S.helpdesk_first_response_time BETWEEN 1 AND {SLA_MAX_SECONDS}
                       THEN S.helpdesk_first_response_time END) AS resposta_media
            FROM helpdesk.helpdesk A
            INNER JOIN helpdesk.helpdesk_has_sla S ON S.helpdesk_id = A.id
            WHERE {where_open} AND S.sla_conclusion_time IS NOT NULL
        """,
        "ratings": f"""
            SELECT
              COUNT(*) AS avaliados,
              SUM(E.rating_average <= {RATING_BAD_MAX}) AS ruins,
              SUM(E.rating_average >= {RATING_GOOD_MIN}) AS otimas,
              AVG(E.rating_average) AS nota
            FROM helpdesk.helpdesk A
            INNER JOIN helpdesk.rating E ON E.helpdesk_id = A.id
            WHERE {where_open} AND E.rating_average IS NOT NULL
        """,
        "rating_spread": f"""
            SELECT FLOOR(E.rating_average) AS nota, COUNT(*) AS total
            FROM helpdesk.helpdesk A
            INNER JOIN helpdesk.rating E ON E.helpdesk_id = A.id
            WHERE {where_open} AND E.rating_average IS NOT NULL
            GROUP BY FLOOR(E.rating_average) ORDER BY nota DESC
        """,
        "closed_scope": f"""
            SELECT COUNT(*) AS fechados FROM helpdesk.helpdesk A
            WHERE {where_open} AND A.status_id = {CLOSED_STATUS}
        """,
        "logged_time": f"""
            SELECT COUNT(*) AS chamados,
                   SUM(T.total IS NOT NULL AND T.total > 0) AS com_apontamento,
                   SUM(COALESCE(T.total, 0)) AS total,
                   AVG(CASE WHEN T.total > 0 THEN T.total END) AS media
            FROM helpdesk.helpdesk A
            LEFT JOIN (
                SELECT helpdesk_id, SUM(action_total_time) AS total
                FROM helpdesk.timeline GROUP BY helpdesk_id
            ) T ON T.helpdesk_id = A.id
            WHERE {where_open}
        """,
        "area_times": f"""
            SELECT G.name AS rotulo,
                   COUNT(*) AS total,
                   AVG(CASE WHEN A.date_close IS NOT NULL
                            THEN TIMESTAMPDIFF(SECOND, A.date_open, A.date_close) END) AS tempo
            FROM helpdesk.helpdesk A
            LEFT JOIN helpdesk.service_departament G ON G.id = A.departament_id
            WHERE {where_open} AND A.date_close IS NOT NULL
            GROUP BY G.name HAVING COUNT(*) > 0 ORDER BY tempo DESC LIMIT 8
        """,
        "aging_buckets": f"""
            SELECT
              SUM(TIMESTAMPDIFF(DAY, A.date_open, NOW()) <= 7) AS ate_7,
              SUM(TIMESTAMPDIFF(DAY, A.date_open, NOW()) BETWEEN 8 AND 30) AS de_8_a_30,
              SUM(TIMESTAMPDIFF(DAY, A.date_open, NOW()) BETWEEN 31 AND 90) AS de_31_a_90,
              SUM(TIMESTAMPDIFF(DAY, A.date_open, NOW()) > 90) AS acima_90
            FROM helpdesk.helpdesk A
            WHERE {where_backlog} AND A.status_id <> {CLOSED_STATUS}
        """,
        "aging": f"""
            SELECT A.id AS chamado, A.subject AS assunto, A.date_open AS abertura,
                   B.name AS status, C.name AS prioridade, D.login AS atendente,
                   A.organization_id AS empresa_id,
                   TIMESTAMPDIFF(DAY, A.date_open, NOW()) AS dias
            FROM helpdesk.helpdesk A
            LEFT JOIN helpdesk.status B ON B.id = A.status_id
            LEFT JOIN helpdesk.priority C ON C.id = A.priority_id
            LEFT JOIN user D ON D.id = A.user_id_attendent
            WHERE {where_backlog} AND A.status_id <> {CLOSED_STATUS}
            ORDER BY A.date_open ASC LIMIT 20
        """,
    }

    params = {
        "totals": tuple(params_open),
        "rating": tuple(params_open),
        "monthly": tuple(params_open),
        "backlog_status": tuple(params_backlog),
        "priority": tuple(params_open),
        "attendants": tuple(params_open),
        "attendant_rating": tuple(params_open),
        "types": tuple(params_open),
        "service_areas": tuple(params_open),
        "requester_areas": tuple(params_open),
        "companies": tuple(params_open),
        "origins": tuple(params_open),
        "aging": tuple(params_backlog),
        "aging_buckets": tuple(params_backlog),
        "sla": tuple(params_open),
        "ratings": tuple(params_open),
        "rating_spread": tuple(params_open),
        "closed_scope": tuple(params_open),
        "logged_time": tuple(params_open),
        "area_times": tuple(params_open),
    }

    data = _query_many(statements, params)
    return _build_it_dashboard(data, period, company, attendant, available)


def _distribution(rows, fallback="Não informado"):
    total = sum(_int(row["total"]) for row in rows)
    items = []
    for row in rows:
        items.append(
            {
                "label": (row.get("rotulo") or fallback) or fallback,
                "total": _int(row["total"]),
                "total_display": _format_int(row["total"]),
                "share_pct": _share(row["total"], total),
            }
        )
    return {"total": total, "items": items}


def _build_it_dashboard(data, period, company, attendant=None, attendants_available=None):
    totals = (data["totals"] or [{}])[0]
    rating = (data["rating"] or [{}])[0]

    chamados = _int(totals.get("chamados"))
    fechados = _int(totals.get("fechados"))
    abertos = _int(totals.get("abertos"))

    granularity = period["granularity"]
    monthly = []
    for row in data["monthly"]:
        competencia = str(row.get("competencia") or "")
        monthly.append(
            {
                "competencia": competencia,
                "label": series_label(competencia, granularity),
                "abertos": _int(row.get("abertos")),
                "fechados": _int(row.get("fechados")),
            }
        )
    mark_series_ticks(monthly)
    peak_month = max((item["abertos"] for item in monthly), default=0)
    for item in monthly:
        item["height_pct"] = round(item["abertos"] / peak_month * 100, 1) if peak_month else 0.0

    ratings_by_login = {
        (row.get("login") or ""): {
            "nota": float(row.get("nota") or 0),
            "avaliacoes": _int(row.get("avaliacoes")),
        }
        for row in data["attendant_rating"]
    }

    attendants = []
    for row in data["attendants"]:
        login = row.get("login") or ""
        nota = ratings_by_login.get(login, {})
        total = _int(row.get("total"))
        attendants.append(
            {
                "login": login,
                "name": _attendant_name(login),
                "total": total,
                "total_display": _format_int(total),
                "closed": _int(row.get("fechados")),
                "closed_pct": _share(row.get("fechados"), total),
                "average_seconds": float(row.get("tempo") or 0),
                "average_display": _format_duration(row.get("tempo")),
                "logged_display": _format_duration(row.get("apontado")),
                "rating": round(nota.get("nota", 0), 2),
                "rating_display": _format_decimal(nota.get("nota", 0), 2) if nota.get("avaliacoes") else "-",
                "rating_count": nota.get("avaliacoes", 0),
                "share_pct": _share(total, chamados),
            }
        )

    companies = []
    company_total = sum(_int(row["total"]) for row in data["companies"])
    for row in data["companies"]:
        empresa_id = _int(row.get("empresa_id"))
        companies.append(
            {
                "id": empresa_id,
                "label": COMPANY_NAMES.get(empresa_id, COMPANY_DEFAULT),
                "total": _int(row["total"]),
                "total_display": _format_int(row["total"]),
                "share_pct": _share(row["total"], company_total),
            }
        )

    aging_items = []
    bucket_row = (data["aging_buckets"] or [{}])[0]
    aging_counts = {key: _int(bucket_row.get(key)) for key, _, _, _ in AGING_BUCKETS}
    for row in data["aging"]:
        dias = _int(row.get("dias"))
        empresa_id = _int(row.get("empresa_id"))
        aging_items.append(
            {
                "code": row.get("chamado"),
                "subject": (row.get("assunto") or "Sem assunto").strip(),
                "opened_display": _display_datetime(row.get("abertura")),
                "status": row.get("status") or "-",
                "priority": row.get("prioridade") or "-",
                "attendant": _attendant_name(row.get("atendente")),
                "company": COMPANY_NAMES.get(empresa_id, COMPANY_DEFAULT),
                "days": dias,
                # Passar de 30 dias em aberto é o corte que separa "na fila"
                # de "esquecido"; acima de 90 vira vermelho.
                "tone": "red" if dias > 90 else "amber" if dias > 30 else "neutral",
            }
        )

    sla_row = (data["sla"] or [{}])[0]
    com_sla = _int(sla_row.get("com_sla"))
    conclusao_vencida = _int(sla_row.get("conclusao_vencida"))
    resposta_vencida = _int(sla_row.get("resposta_vencida"))
    sla = {
        "measured": com_sla,
        "measured_display": _format_int(com_sla),
        "measured_pct": _share(com_sla, chamados),
        "breached": conclusao_vencida,
        "breached_display": _format_int(conclusao_vencida),
        "breached_pct": _share(conclusao_vencida, com_sla),
        "within_pct": round(100 - _share(conclusao_vencida, com_sla), 1),
        "response_breached": resposta_vencida,
        "response_breached_display": _format_int(resposta_vencida),
        "response_breached_pct": _share(resposta_vencida, com_sla),
        "conclusion_target_display": _format_duration(sla_row.get("prazo_conclusao")),
        "response_target_display": _format_duration(sla_row.get("prazo_resposta")),
        "response_average_display": _format_duration(sla_row.get("resposta_media")),
    }

    rating_row = (data["ratings"] or [{}])[0]
    fechados_escopo = _int((data["closed_scope"] or [{}])[0].get("fechados"))
    avaliados = _int(rating_row.get("avaliados"))
    # A base de comparação é o que foi fechado: chamado aberto ainda não tem o
    # que avaliar, e incluí-lo afundaria a taxa sem significar nada.
    nao_avaliados = max(fechados_escopo - avaliados, 0)
    ratings = {
        "rated": avaliados,
        "rated_display": _format_int(avaliados),
        "rated_pct": _share(avaliados, fechados_escopo),
        "unrated": nao_avaliados,
        "unrated_display": _format_int(nao_avaliados),
        "unrated_pct": _share(nao_avaliados, fechados_escopo),
        "bad": _int(rating_row.get("ruins")),
        "bad_pct": _share(rating_row.get("ruins"), avaliados),
        "good": _int(rating_row.get("otimas")),
        "good_pct": _share(rating_row.get("otimas"), avaliados),
        "average_display": _format_decimal(rating_row.get("nota") or 0, 2),
        "bad_max": RATING_BAD_MAX,
        "good_min": RATING_GOOD_MIN,
        "spread": [
            {"label": f"Nota {_int(row['nota'])}", "total": _int(row["total"]),
             "total_display": _format_int(row["total"]), "share_pct": _share(row["total"], avaliados)}
            for row in data["rating_spread"]
        ],
    }

    logged_row = (data["logged_time"] or [{}])[0]
    com_apontamento = _int(logged_row.get("com_apontamento"))
    logged = {
        "total_seconds": float(logged_row.get("total") or 0),
        "total_display": _format_duration(logged_row.get("total")),
        "total_hours": _format_decimal(float(logged_row.get("total") or 0) / 3600, 1),
        "average_display": _format_duration(logged_row.get("media")),
        "tickets_with_log": com_apontamento,
        "tickets_with_log_display": _format_int(com_apontamento),
        "coverage_pct": _share(com_apontamento, chamados),
    }

    area_times = []
    for row in data["area_times"]:
        seconds = float(row.get("tempo") or 0)
        area_times.append(
            {
                "label": row.get("rotulo") or "Não informado",
                "total": _int(row.get("total")),
                "seconds": seconds,
                "days": round(seconds / 86400, 1),
                "days_display": _format_decimal(seconds / 86400, 1),
                "duration_display": _format_duration(seconds),
            }
        )
    peak_area = max((item["seconds"] for item in area_times), default=0)
    for item in area_times:
        item["share_pct"] = round(item["seconds"] / peak_area * 100, 1) if peak_area else 0.0

    backlog = _distribution(data["backlog_status"], fallback="Sem status")
    priorities = _distribution(data["priority"])
    for item, row in zip(priorities["items"], data["priority"]):
        item["average_display"] = _format_duration(row.get("tempo"))

    return {
        "scope": {
            "period": period,
            "company": company,
            "attendant": attendant or {"key": "all", "label": "Todos os atendentes", "login": None},
            "period_choices": period_choices(),
            "series_granularity": granularity,
            "series_label": "por dia" if granularity == "day" else "por mês",
            "company_choices": [{"key": "all", "label": "Todas as empresas"}]
            + [{"key": str(key), "label": label} for key, label in sorted(COMPANY_NAMES.items())],
            "attendant_choices": [{"key": "all", "label": "Todos"}]
            + [{"key": item["key"], "label": item["label"]} for item in (attendants_available or [])],
        },
        "metrics": {
            "tickets": chamados,
            "tickets_display": _format_int(chamados),
            "closed": fechados,
            "closed_display": _format_int(fechados),
            "closed_pct": _share(fechados, chamados),
            "open": abertos,
            "open_display": _format_int(abertos),
            "backlog": backlog["total"],
            "backlog_display": _format_int(backlog["total"]),
            "resolution_display": _format_duration(totals.get("tempo_resolucao")),
            "attending_display": _format_duration(totals.get("tempo_atendimento")),
            "sla_display": _format_duration(totals.get("tempo_sla")),
            "sla_valid": _int(totals.get("sla_validos")),
            "sla_valid_pct": _share(totals.get("sla_validos"), chamados),
            "rating_display": _format_decimal(rating.get("nota") or 0, 2),
            "rating_count": _int(rating.get("avaliacoes")),
            "rating_pct": _share(rating.get("avaliacoes"), fechados),
        },
        "sla": sla,
        "ratings": ratings,
        "logged": logged,
        "area_times": area_times,
        "monthly": monthly,
        "backlog": backlog,
        "priorities": priorities,
        "attendants": attendants,
        "types": _distribution(data["types"]),
        "service_areas": _distribution(data["service_areas"]),
        "requester_areas": _distribution(data["requester_areas"]),
        "origins": _distribution(data["origins"], fallback="Não informada"),
        "companies": companies,
        "aging": {"items": aging_items, "counts": aging_counts, "buckets": AGING_BUCKETS},
    }


# --------------------------------------------------- analises processadas ---

# Estes recortes não entram no carregamento da página: cruzam faixa de tempo,
# dia/hora e satisfação sobre a base inteira do período e custam caro para
# rodar a cada abertura da tela. São calculados quando o usuário pede os
# indicadores e ficam guardados no snapshot.

RESOLUTION_BUCKETS = [
    ("ate_1h", "Até 1h", 0, 3600),
    ("de_1h_4h", "1h a 4h", 3600, 14400),
    ("de_4h_24h", "4h a 24h", 14400, 86400),
    ("de_1d_3d", "1 a 3 dias", 86400, 259200),
    ("de_3d_7d", "3 a 7 dias", 259200, 604800),
    ("acima_7d", "Mais de 7 dias", 604800, None),
]

WEEKDAY_LABELS = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]
HOUR_BLOCKS = [
    ("madrugada", "0-6h", 0, 5),
    ("manha_cedo", "6-9h", 6, 8),
    ("manha", "9-12h", 9, 11),
    ("tarde", "12-15h", 12, 14),
    ("tarde_fim", "15-18h", 15, 17),
    ("noite", "18-24h", 18, 23),
]


def compute_deep_analytics(period_key="12", company_key="all", attendant_key="all"):
    """Cruzamentos mais caros, calculados sob demanda."""
    period = resolve_period(period_key)
    company = resolve_company(company_key)
    attendant = resolve_attendant(attendant_key, list_attendants())
    where_open, params_open = _scope_sql(period, company, attendant)

    bucket_cases = " ".join(
        f"WHEN TIMESTAMPDIFF(SECOND, A.date_open, A.date_close) "
        f"{'>=' if end is None else 'BETWEEN'} {start}{'' if end is None else f' AND {end}'} THEN '{key}'"
        for key, _label, start, end in RESOLUTION_BUCKETS
    )

    statements = {
        "resolution_spread": f"""
            SELECT CASE {bucket_cases} ELSE 'acima_7d' END AS faixa,
                   COUNT(*) AS total,
                   AVG(TIMESTAMPDIFF(SECOND, A.date_open, A.date_close)) AS tempo
            FROM helpdesk.helpdesk A
            WHERE {where_open} AND A.date_close IS NOT NULL
              AND TIMESTAMPDIFF(SECOND, A.date_open, A.date_close) >= 0
            GROUP BY faixa
        """,
        "heatmap": f"""
            SELECT DAYOFWEEK(A.date_open) AS dia, HOUR(A.date_open) AS hora, COUNT(*) AS total
            FROM helpdesk.helpdesk A
            WHERE {where_open}
            GROUP BY DAYOFWEEK(A.date_open), HOUR(A.date_open)
        """,
        "rating_vs_speed": f"""
            SELECT CASE {bucket_cases} ELSE 'acima_7d' END AS faixa,
                   AVG(E.rating_average) AS nota, COUNT(*) AS total
            FROM helpdesk.helpdesk A
            INNER JOIN helpdesk.rating E ON E.helpdesk_id = A.id
            WHERE {where_open} AND A.date_close IS NOT NULL
              AND E.rating_average IS NOT NULL
              AND TIMESTAMPDIFF(SECOND, A.date_open, A.date_close) >= 0
            GROUP BY faixa
        """,
        "recurrent_requesters": f"""
            SELECT F.login AS solicitante, H.name AS setor, COUNT(*) AS total,
                   AVG(CASE WHEN A.date_close IS NOT NULL
                            THEN TIMESTAMPDIFF(SECOND, A.date_open, A.date_close) END) AS tempo
            FROM helpdesk.helpdesk A
            LEFT JOIN user F ON F.id = A.user_id_owner
            LEFT JOIN departament H ON H.id = F.departament_id
            WHERE {where_open}
            GROUP BY F.login, H.name
            ORDER BY total DESC LIMIT 10
        """,
    }
    params = {name: tuple(params_open) for name in statements}
    data = _query_many(statements, params)

    spread_by_key = {str(row["faixa"]): row for row in data["resolution_spread"]}
    total_resolvidos = sum(_int(row["total"]) for row in data["resolution_spread"])
    resolution_spread = []
    for key, label, _start, _end in RESOLUTION_BUCKETS:
        row = spread_by_key.get(key) or {}
        total = _int(row.get("total"))
        resolution_spread.append(
            {
                "key": key,
                "label": label,
                "total": total,
                "total_display": _format_int(total),
                "share_pct": _share(total, total_resolvidos),
                "average_display": _format_duration(row.get("tempo")),
            }
        )

    rating_by_key = {str(row["faixa"]): row for row in data["rating_vs_speed"]}
    rating_vs_speed = []
    for key, label, _start, _end in RESOLUTION_BUCKETS:
        row = rating_by_key.get(key) or {}
        avaliados = _int(row.get("total"))
        nota = float(row.get("nota") or 0)
        rating_vs_speed.append(
            {
                "label": label,
                "rated": avaliados,
                "rating": round(nota, 2),
                "rating_display": _format_decimal(nota, 2) if avaliados else "-",
                # A barra é sobre 10, a escala da nota, não sobre o maior valor:
                # comparar notas entre si esconderia que todas são altas.
                "share_pct": round(nota / 10 * 100, 1) if avaliados else 0.0,
            }
        )

    # DAYOFWEEK do MySQL devolve 1=domingo.
    grid = {(dia, bloco): 0 for dia in range(7) for bloco, _, _, _ in [(b[0], 0, 0, 0) for b in HOUR_BLOCKS]}
    for row in data["heatmap"]:
        dia = _int(row.get("dia")) - 1
        hora = _int(row.get("hora"))
        for bloco, _label, inicio, fim in HOUR_BLOCKS:
            if inicio <= hora <= fim:
                grid[(dia, bloco)] = grid.get((dia, bloco), 0) + _int(row.get("total"))
                break
    peak = max(grid.values()) if grid else 0
    heatmap = {
        "hour_labels": [label for _key, label, _i, _f in HOUR_BLOCKS],
        "peak": peak,
        "rows": [
            {
                "label": WEEKDAY_LABELS[dia],
                "cells": [
                    {
                        "total": grid.get((dia, bloco), 0),
                        "intensity": round(grid.get((dia, bloco), 0) / peak, 3) if peak else 0.0,
                    }
                    for bloco, _label, _i, _f in HOUR_BLOCKS
                ],
            }
            for dia in range(7)
        ],
    }

    requesters = []
    for row in data["recurrent_requesters"]:
        login = (row.get("solicitante") or "").strip()
        if not login:
            continue
        requesters.append(
            {
                "login": login,
                "name": _attendant_name(login),
                "area": row.get("setor") or "Não informado",
                "total": _int(row.get("total")),
                "total_display": _format_int(row.get("total")),
                "average_display": _format_duration(row.get("tempo")),
            }
        )
    peak_requester = max((item["total"] for item in requesters), default=0)
    for item in requesters:
        item["share_pct"] = round(item["total"] / peak_requester * 100, 1) if peak_requester else 0.0

    return {
        "resolution_spread": resolution_spread,
        "resolution_total": total_resolvidos,
        "rating_vs_speed": rating_vs_speed,
        "heatmap": heatmap,
        "recurrent_requesters": requesters,
    }


# ------------------------------------------------------------ analise de IA --

IT_BI_SYSTEM_PROMPT = (
    "Você é um analista de operações de TI e service desk. Produza somente análises "
    "sustentadas pelos indicadores enviados e respeite rigorosamente o formato JSON solicitado."
)

IT_BI_RESPONSE_SCHEMA = {
    "name": "it_service_desk_insights",
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


def it_dashboard_fingerprint(dashboard):
    """Impressão dos números do recorte, para saber quando a análise envelheceu."""
    import hashlib

    base = json.dumps(
        {
            "scope": [
                dashboard["scope"]["period"]["key"],
                dashboard["scope"]["company"]["key"],
                dashboard["scope"]["attendant"]["key"],
            ],
            "metrics": dashboard["metrics"],
            "sla": dashboard["sla"],
            "ratings": {key: value for key, value in dashboard["ratings"].items() if key != "spread"},
            "logged": dashboard["logged"],
            "backlog": dashboard["backlog"]["total"],
        },
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def build_it_ai_payload(dashboard, fingerprint, deep=None):
    """Payload determinístico enviado à IA.

    As séries longas entram resumidas: o que a análise precisa é do formato da
    operação, não de 20 linhas de aging que já estão na tela.
    """
    scope = dashboard["scope"]
    return {
        "schema_version": "1.0",
        "request_type": "it_service_desk_intelligence",
        "source_system": "ConnectMX / SM helpdesk",
        "source_fingerprint": fingerprint,
        "scope": {
            "period": scope["period"]["full_label"],
            "company": scope["company"]["label"],
            "attendant": scope["attendant"]["label"],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "deterministic_metrics": {
            "volume": dashboard["metrics"],
            "sla": dashboard["sla"],
            "ratings": dashboard["ratings"],
            "logged_time": dashboard["logged"],
            "monthly_series": dashboard["monthly"],
            "backlog_by_status": dashboard["backlog"]["items"],
            "aging": dashboard["aging"]["counts"],
            "oldest_open_tickets": [
                {key: item[key] for key in ("code", "days", "status", "priority", "attendant", "company")}
                for item in dashboard["aging"]["items"][:10]
            ],
            "attendants": [
                {key: item[key] for key in ("name", "total", "closed_pct", "average_display", "rating", "rating_count")}
                for item in dashboard["attendants"][:12]
            ],
            "priorities": dashboard["priorities"]["items"],
            "types": dashboard["types"]["items"],
            "service_areas": dashboard["service_areas"]["items"],
            "requester_areas": dashboard["requester_areas"]["items"],
            "area_resolution_times": dashboard["area_times"],
            "origins": dashboard["origins"]["items"],
            "companies": dashboard["companies"],
            "resolution_time_spread": (deep or {}).get("resolution_spread"),
            "rating_by_resolution_speed": (deep or {}).get("rating_vs_speed"),
            "demand_heatmap": (deep or {}).get("heatmap"),
            "recurrent_requesters": (deep or {}).get("recurrent_requesters"),
        },
        "data_quality_notes": [
            "`time_in_sla` da base tem registros negativos e timestamps Unix; foi saneado na faixa de 0 a 30 dias.",
            "`total_attending_time` acompanha o tempo corrido em chamados longos; também saneado na mesma faixa.",
            f"Só {dashboard['logged']['coverage_pct']}% dos chamados têm tempo lançado na timeline: o total apontado é piso.",
            "O backlog não respeita o filtro de período — é a fila aberta de hoje, de todo o histórico.",
            *(
                []
                if scope["period"].get("end")
                else [
                    "O recorte alcança o mês corrente, que ainda não fechou: chamados abertos nos "
                    "últimos dias ainda não tiveram tempo de fechar, o que derruba a taxa de "
                    "fechamento e o tempo médio de resolução. O recorte 'Mês passado' não tem esse viés.",
                ]
            ),
        ],
        "analysis_instructions": [
            "Use somente os indicadores fornecidos; não invente causas para variações.",
            "Separe problema de capacidade (volume por atendente, tempo apontado) de problema de processo (SLA, aging, retrabalho).",
            "Trate o aging do backlog como risco: chamados acima de 90 dias merecem menção explícita.",
            "Compare a taxa de avaliação com o volume fechado antes de concluir algo sobre satisfação.",
            "Considere as notas de qualidade de dados: não tire conclusão de um campo marcado como incompleto.",
            "Use o cruzamento de nota por faixa de tempo para separar satisfação de agilidade.",
            "O mapa de calor mostra quando a demanda chega: use-o para falar de escala, não de desempenho.",
            "Cite números em cada evidência e escreva em português do Brasil.",
        ],
        "response_format": {"type": "json_schema", "json_schema": IT_BI_RESPONSE_SCHEMA},
    }
