"""Registros por trás de um ponto dos BIs do Dashes (TI, Viagens, Romaneios e Compras).

O "Expandir dados" do menu dos gráficos (static/js/maxisysDrill.js) chama aqui com o mesmo
recorte da página (período, empresa, frota, filial...) e mais a dimensão do visual clicado
(`dim`) e o valor do ponto (`valor`). Cada BI tem uma lista fechada de dimensões: a dimensão
vira um filtro sobre a MESMA base SQL das agregações do painel (a CTE BASE / ROMANEIOS /
ITENS ou a tabela de chamados do SM), então o total da tabela é o número da barra.

Devolve o formato que a janela desenha: título, resumo e seções com colunas tipadas e linhas
cruas (número como número, data ISO).
"""

import calendar
import re
from datetime import date, datetime, timedelta

from . import compras_bi, it_bi, romaneio_bi, travel_bi
from .bi_common import to_float as _float
from .bi_common import to_int as _int
from .bi_periods import ALL_TIME, resolve_period

# A janela lista no máximo isto; o resumo mostra o total de verdade.
DETAIL_LIMIT = 1500


class UnknownDimension(ValueError):
    """Dimensão fora da lista do BI ou valor que não dá para usar como filtro."""


def _iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if value not in (None, "") else None


def _bounds(value):
    """'2026-10' -> (1/10, 1/11); '2026-10-15' -> (15/10, 16/10). Fim exclusivo."""
    try:
        parts = [int(part) for part in str(value).strip()[:10].split("-")]
        if len(parts) == 3:
            start = date(*parts)
            return start, start + timedelta(days=1)
        if len(parts) == 2:
            year, month = parts
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]) + timedelta(days=1)
    except (TypeError, ValueError):
        pass
    raise UnknownDimension("Mês ou dia inválido.")


def _series_key(value, period, today=None):
    """Rótulo da série ("10/2026" no mensal, "15/10" no diário) de volta para AAAA-MM(-DD)."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", text):
        return text
    match = re.fullmatch(r"(\d{1,2})/(\d{4})", text)
    if match:
        return f"{match.group(2)}-{int(match.group(1)):02d}"
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        try:
            if period.get("start"):
                # "Mês anterior": mês fechado, o ano vem do início dele.
                return date(period["start"].year, month, day).isoformat()
            # "1 mês": janela até hoje; dia que cairia no futuro é do ano passado (dezembro em janeiro).
            today = today or date.today()
            candidate = date(today.year, month, day)
            return (candidate if candidate <= today else date(today.year - 1, month, day)).isoformat()
        except ValueError:
            pass
    raise UnknownDimension("Mês ou dia inválido.")


def _period_title(value):
    start, end = _bounds(value)
    if end - start == timedelta(days=1):
        return start.strftime("%d/%m/%Y")
    return f"{start:%m/%Y}"


def _bucket_case(column, buckets, inclusive_end=True):
    """O mesmo CASE das agregações (a primeira faixa que casa vence)."""
    cases = []
    for key, _label, start, end in buckets:
        if end is None:
            cases.append(f"WHEN {column} >= {start} THEN '{key}'")
        elif inclusive_end:
            cases.append(f"WHEN {column} BETWEEN {start} AND {end} THEN '{key}'")
        else:
            cases.append(f"WHEN {column} >= {start} AND {column} < {end} THEN '{key}'")
    return f"CASE {' '.join(cases)} END"


def _bucket_label(buckets, key):
    for bucket_key, label, _start, _end in buckets:
        if bucket_key == key:
            return label
    raise UnknownDimension("Faixa inválida.")


def _truncation_note(total, shown):
    return f"Mostrando os {shown} registros mais recentes de {total}." if total > shown else ""


def _fact(label, value, note=None, tone=None):
    fact = {"label": label, "value": value}
    if note:
        fact["note"] = note
    if tone:
        fact["tone"] = tone
    return fact


def _int_display(value):
    return f"{int(value or 0):,}".replace(",", ".")


def _dec_display(value, digits=1):
    if value is None:
        return "–"
    return f"{value:,.{digits}f}".replace(",", "#").replace(".", ",").replace("#", ".")


def _money_display(value):
    return f"R$ {_dec_display(value or 0, 2)}"


def _average(values):
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------------------
# BI do TI (MySQL do SM)
# ---------------------------------------------------------------------------

_IT_FROM = """
FROM helpdesk.helpdesk A
LEFT JOIN helpdesk.status B ON B.id = A.status_id
LEFT JOIN helpdesk.priority C ON C.id = A.priority_id
LEFT JOIN helpdesk.type I ON I.id = A.type_id
LEFT JOIN helpdesk.service_departament G ON G.id = A.departament_id
LEFT JOIN user D ON D.id = A.user_id_attendent
LEFT JOIN user F ON F.id = A.user_id_owner
LEFT JOIN departament H ON H.id = F.departament_id
"""

_IT_SELECT = f"""
SELECT A.id AS chamado, A.subject AS assunto, A.date_open AS abertura, A.date_close AS fechamento,
       A.status_id AS status_id, B.name AS status, C.name AS prioridade, I.name AS tipo, G.name AS setor,
       D.login AS atendente, F.fullname AS solicitante, F.login AS solicitante_login, H.name AS setor_solicitante,
       A.organization_id AS empresa_id, A.created_from AS origem,
       CASE WHEN A.date_close IS NOT NULL THEN TIMESTAMPDIFF(SECOND, A.date_open, A.date_close) END AS resolucao,
       TIMESTAMPDIFF(DAY, A.date_open, NOW()) AS dias_aberto,
       (SELECT AVG(E.rating_average) FROM helpdesk.rating E WHERE E.helpdesk_id = A.id) AS nota
{_IT_FROM}
"""


# Rótulos que o painel põe no lugar de nome nulo ou em branco (`_distribution`).
IT_EMPTY_LABELS = {"Não informado", "Não informada", "Sem status", "-"}


def _it_text(column, value):
    """Filtro por nome; "Não informado" (e parecidos) é o nome nulo ou em branco no SM."""
    if value in (None, ""):
        return f"({column} IS NULL OR {column} = '')", []
    if value in IT_EMPTY_LABELS:
        return f"({column} IS NULL OR {column} = '' OR {column} = %s)", [value]
    return f"{column} = %s", [value]


def _it_seconds_bucket(value):
    """Faixa de resolução (chave ou rótulo): mesmas fronteiras do CASE de `compute_deep_analytics`."""
    buckets = it_bi.RESOLUTION_BUCKETS
    for index, (key, label, start, end) in enumerate(buckets):
        if value not in (key, label):
            continue
        seconds = "TIMESTAMPDIFF(SECOND, A.date_open, A.date_close)"
        # BETWEEN inclusivo com a primeira faixa vencendo: a borda fica com a faixa de antes.
        low = f"{seconds} >= {start}" if index == 0 else f"{seconds} > {start}"
        high = "" if end is None else f" AND {seconds} <= {end}"
        return f"A.date_close IS NOT NULL AND {low}{high}", []
    raise UnknownDimension("Faixa de resolução inválida.")


def _it_aging(value):
    for key, label, start, end in it_bi.AGING_BUCKETS:
        if value in (key, label):
            days = "TIMESTAMPDIFF(DAY, A.date_open, NOW())"
            if end is None:
                return f"{days} >= {start}", []
            return f"{days} BETWEEN {start} AND {end}", []
    raise UnknownDimension("Faixa de dias inválida.")


def _it_month(value):
    start, end = _bounds(value)
    return "A.date_open >= %s AND A.date_open < %s", [start, end]


def _it_company(value):
    if value in (None, ""):
        return "A.organization_id IS NULL", []
    return "A.organization_id = %s", [_int(value)]


def _it_closed_in_area(value):
    sql, params = _it_text("G.name", value)
    return f"{sql} AND A.date_close IS NOT NULL", params


def _it_rating(value):
    """Nota inteira ("Nota 9" ou "9"): chamados com alguma avaliação nessa faixa."""
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        raise UnknownDimension("Nota inválida.")
    return (
        "EXISTS (SELECT 1 FROM helpdesk.rating E WHERE E.helpdesk_id = A.id "
        "AND E.rating_average IS NOT NULL AND FLOOR(E.rating_average) = %s)", [int(digits)],
    )


def _it_weekday(value):
    """Linha do mapa de calor: 0 = domingo, como o DAYOFWEEK do MySQL menos 1."""
    day = _int(value)
    if day is None or not 0 <= day <= 6:
        raise UnknownDimension("Dia da semana inválido.")
    return "DAYOFWEEK(A.date_open) - 1 = %s", [day]


IT_SLA_CONDITIONS = {
    "conclusao": "S.conclusion_time_expired_in IS NOT NULL",
    "resposta": "S.first_response_time_expired_in IS NOT NULL",
    "medido": "1 = 1",
}


def _it_sla(value):
    """Barras do quadro SLA: conclusão vencida, 1ª resposta vencida e com SLA medido."""
    if value not in IT_SLA_CONDITIONS:
        raise UnknownDimension("Opção de SLA inválida.")
    return (
        "EXISTS (SELECT 1 FROM helpdesk.helpdesk_has_sla S WHERE S.helpdesk_id = A.id "
        f"AND S.sla_conclusion_time IS NOT NULL AND {IT_SLA_CONDITIONS[value]})", [],
    )


def _it_logged(_value):
    return "(SELECT SUM(T.action_total_time) FROM helpdesk.timeline T WHERE T.helpdesk_id = A.id) > 0", []


def _it_sla_valid(value):
    """Alerta "SLA do helpdesk piorou" da Central de Decisão: chamados do período com o tempo dentro
    do SLA (sim) ou fora dele (nao), pelo mesmo filtro do `sla_valid_pct` do painel."""
    inside = f"A.time_in_sla BETWEEN {it_bi.SLA_MIN_SECONDS} AND {it_bi.SLA_MAX_SECONDS}"
    if value == "sim":
        return inside, []
    if value == "nao":
        return f"(A.time_in_sla IS NULL OR NOT ({inside}))", []
    raise UnknownDimension("Opção de SLA inválida.")


# dimensão: (rótulo, filtro(valor) -> (sql, params), base) — base "backlog" ignora o período
# e só pega chamados em aberto, como o "Backlog por status" e o aging do painel.
IT_DIMENSIONS = {
    "mes": ("Abertos no período", _it_month, "open"),
    "status": ("Backlog por status", lambda value: _it_text("B.name", value), "backlog"),
    "prioridade": ("Prioridade", lambda value: _it_text("C.name", value), "open"),
    "tipo": ("Tipo de chamado", lambda value: _it_text("I.name", value), "open"),
    "setor": ("Setor de atendimento", lambda value: _it_text("G.name", value), "open"),
    "tempo_setor": ("Fechados por setor", _it_closed_in_area, "open"),
    "setor_solicitante": ("Setor solicitante", lambda value: _it_text("H.name", value), "open"),
    "empresa": ("Empresa", _it_company, "open"),
    "origem": ("Origem", lambda value: _it_text("A.created_from", value), "open"),
    "atendente": ("Atendente", lambda value: _it_text("D.login", value), "open"),
    "solicitante": ("Solicitante", lambda value: _it_text("F.login", value), "open"),
    "nota": ("Nota da avaliação", _it_rating, "open"),
    "resolucao": ("Tempo de resolução", _it_seconds_bucket, "open"),
    "aging": ("Em aberto há", _it_aging, "backlog"),
    "aberto": ("Chamados em aberto", lambda _value: ("1 = 1", []), "backlog"),
    "dia_semana": ("Abertos no dia da semana", _it_weekday, "open"),
    "sla": ("SLA", _it_sla, "open"),
    "apontamento": ("Chamados com tempo apontado na timeline", _it_logged, "open"),
    # Central de Decisão: o card "Chamados" (todos do período) e o alerta de SLA.
    "todos": ("Chamados abertos no período", lambda _value: ("1 = 1", []), "open"),
    "sla_valido": ("Chamados do período pelo SLA", _it_sla_valid, "open"),
}

IT_COLUMNS = [
    {"key": "ticket", "label": "Chamado", "sub": "subject", "wrap": True},
    {"key": "opened", "label": "Abertura", "type": "datetime"},
    {"key": "closed", "label": "Fechamento", "type": "datetime"},
    {"key": "status", "label": "Status"},
    {"key": "priority", "label": "Prioridade"},
    {"key": "type", "label": "Tipo"},
    {"key": "attendant", "label": "Atendente"},
    {"key": "requester", "label": "Solicitante", "sub": "requester_area"},
    {"key": "area", "label": "Setor de atendimento"},
    {"key": "company", "label": "Empresa"},
    {"key": "origin", "label": "Origem"},
    {"key": "resolution_days", "label": "Resolução (dias)", "type": "dec", "digits": 1, "total": "avg"},
    {"key": "open_days", "label": "Dias em aberto", "type": "int", "total": "avg"},
    {"key": "rating", "label": "Avaliação", "type": "dec", "digits": 1, "total": "avg"},
]


def _months_back(day, months, oracle):
    """`day` menos `months` meses, como o banco conta a janela móvel do painel: o MySQL
    (`DATE_SUB(CURDATE(), INTERVAL n MONTH)`) encosta no último dia do mês de destino; o
    `ADD_MONTHS` do Oracle, além disso, leva o último dia do mês para o último dia do destino."""
    index = day.year * 12 + day.month - 1 - months
    year, month = index // 12, index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    if oracle and day.day == calendar.monthrange(day.year, day.month)[1]:
        return date(year, month, last)
    return date(year, month, min(day.day, last))


def _pinned(period, today, oracle=True):
    """Com `today` (dia do snapshot), a janela móvel vira o intervalo que o painel via naquele
    dia — [today - N meses, today + 1 dia) — em vez de contar a partir de hoje no banco."""
    if not today or not period.get("months") or period.get("start"):
        return period
    return {**period, "start": _months_back(today, period["months"], oracle), "end": today + timedelta(days=1)}


def _ref_note(today):
    """Subtítulo do detalhe que usa a janela de um snapshot (quadros de análise processada)."""
    return f" · janela da análise de {today:%d/%m/%Y}" if today else ""


def it_detail(period_key, company_key, attendant_key, dim, value, label="", today=None):
    if dim not in IT_DIMENSIONS:
        raise UnknownDimension("Este gráfico não tem detalhe por chamado.")
    title, build, base = IT_DIMENSIONS[dim]
    predicate, predicate_params = build(value)
    period = _pinned(resolve_period(period_key, today=today), today, oracle=False)
    company = it_bi.resolve_company(company_key)
    attendant = it_bi.resolve_attendant(attendant_key, it_bi.list_attendants())
    if base == "backlog":
        where, params = it_bi._scope_sql(ALL_TIME, company, attendant)
        where = f"{where} AND A.status_id <> {it_bi.CLOSED_STATUS}"
    else:
        where, params = it_bi._scope_sql(period, company, attendant)
    where = f"{where} AND {predicate}"
    all_params = tuple(params) + tuple(predicate_params)
    data = it_bi._query_many(
        {
            "rows": f"{_IT_SELECT} WHERE {where} ORDER BY A.date_open DESC LIMIT {DETAIL_LIMIT}",
            "count": f"SELECT COUNT(*) AS total, SUM(A.status_id = {it_bi.CLOSED_STATUS}) AS fechados {_IT_FROM} WHERE {where}",
        },
        {"rows": all_params, "count": all_params},
    )
    rows = []
    for row in data["rows"]:
        closed = _int(row.get("status_id")) == it_bi.CLOSED_STATUS
        seconds = row.get("resolucao")
        open_days = None if closed else _int(row.get("dias_aberto"))
        company_id = _int(row.get("empresa_id"))
        rows.append({
            "ticket": str(row.get("chamado") or ""),
            "subject": (row.get("assunto") or "").strip(),
            "opened": _iso(row.get("abertura")),
            "closed": _iso(row.get("fechamento")),
            "status": row.get("status") or "Sem status",
            "priority": row.get("prioridade") or "Não informado",
            "type": row.get("tipo") or "Não informado",
            "attendant": it_bi._attendant_name(row.get("atendente")),
            "requester": (row.get("solicitante") or row.get("solicitante_login") or "-").strip(),
            "requester_area": row.get("setor_solicitante") or "",
            "area": row.get("setor") or "Não informado",
            "company": it_bi.COMPANY_NAMES.get(company_id, it_bi.COMPANY_DEFAULT),
            "origin": row.get("origem") or "Não informada",
            "resolution_days": round(float(seconds) / 86400, 2) if seconds is not None and float(seconds) >= 0 else None,
            "open_days": open_days,
            "rating": round(float(row["nota"]), 2) if row.get("nota") is not None else None,
            "__tone": "bad" if open_days and open_days > 90 else "warn" if open_days and open_days > 30 else None,
        })
    counts = (data["count"] or [{}])[0]
    total = _int(counts.get("total"))
    closed_total = _int(counts.get("fechados"))
    resolution = _average([row["resolution_days"] for row in rows])
    rating = _average([row["rating"] for row in rows])
    scope_bits = [period["full_label"] if base == "open" else "backlog de todo o histórico", company["label"]]
    if attendant and attendant.get("login"):
        scope_bits.append(attendant["label"])
    return {
        "title": label or (_period_title(value) if dim == "mes" else str(value or "Não informado")),
        "subtitle": f"{title} · {' · '.join(scope_bits)}{_ref_note(today)}",
        "summary": [
            _fact("Chamados", _int_display(total), _truncation_note(total, len(rows))),
            _fact("Fechados", _int_display(closed_total), f"{round(closed_total / total * 100)}% do total" if total else None),
            _fact("Em aberto", _int_display(total - closed_total), tone="warn" if total - closed_total else None),
            _fact("Resolução média", f"{_dec_display(resolution)} dias" if resolution is not None else "–",
                  "nos chamados listados" if total > len(rows) else None),
            _fact("Avaliação média", _dec_display(rating, 2) if rating is not None else "–"),
        ],
        "sections": [{
            "key": "tickets", "label": "Chamados", "columns": IT_COLUMNS, "rows": rows,
            "sort": {"key": "opened", "dir": "desc"},
            "groupBy": ["status", "priority", "type", "attendant", "area", "company", "origin", "requester"],
            "empty": "Nenhum chamado neste ponto.",
            "note": " ".join(part for part in (
                _truncation_note(total, len(rows)),
                # Alguns chamados têm duas avaliações no SM: o painel conta avaliações, a lista conta chamados.
                "Cada chamado aparece uma vez, mesmo com mais de uma avaliação (o gráfico conta avaliações)." if dim == "nota" else "",
            ) if part),
        }],
    }


# ---------------------------------------------------------------------------
# BI de Viagens (Oracle, USU_TCADVIA)
# ---------------------------------------------------------------------------

def _travel_month(value):
    text = str(value or "")
    column = "DIA" if len(text) >= 10 else "COMPETENCIA"
    _bounds(text)  # valida
    return f"{column} = :valor", {"valor": text[:10] if column == "DIA" else text[:7]}


def _travel_cost_month(value):
    """Custo vem do contábil em 'MMAAAA'; a viagem tem competência 'AAAA-MM'."""
    text = str(value or "")
    if len(text) != 6 or not text.isdigit():
        raise UnknownDimension("Competência inválida.")
    return "COMPETENCIA = :valor", {"valor": f"{text[2:]}-{text[:2]}"}


def _travel_bucket(column, buckets, inclusive_end):
    def build(value):
        _bucket_label(buckets, value)
        return f"{_bucket_case(column, buckets, inclusive_end)} = :valor", {"valor": value}
    return build


def _travel_equals(column, cast=None):
    def build(value):
        if value in (None, ""):
            return f"{column} IS NULL", {}
        return f"{column} = :valor", {"valor": cast(value) if cast else value}
    return build


def _travel_case(expression, cases):
    """CASE igual ao da análise processada (a primeira condição que casa vence)."""
    def build(value):
        if value not in {key for key, _condition in cases}:
            raise UnknownDimension("Faixa inválida.")
        sql = " ".join(f"WHEN {condition} THEN '{key}'" for key, condition in cases)
        return f"{expression} IS NOT NULL AND CASE {sql} END = :valor", {"valor": value}
    return build


def _travel_fixed(options):
    """Valor fechado (chave -> condição sobre BASE)."""
    def build(value):
        if value not in options:
            raise UnknownDimension("Opção inválida.")
        return options[value], {}
    return build


def _travel_weekday(value):
    """Dia da semana da saída, 0 = segunda (TRUNC(data) - TRUNC(data, 'IW'), sem depender do NLS)."""
    day = _int(value)
    if day is None or not 0 <= day <= 6:
        raise UnknownDimension("Dia da semana inválido.")
    return "DT_SAIDA IS NOT NULL AND TRUNC(DT_SAIDA) - TRUNC(DT_SAIDA, 'IW') = :valor", {"valor": day}


def _travel_route(value):
    route = str(value or "").strip().lstrip("0")
    if not route:
        raise UnknownDimension("Rota inválida.")
    return ("EXISTS (SELECT 1 FROM ROTEIRO R WHERE R.EMPRESA = BASE.EMPRESA AND R.VIAGEM = BASE.VIAGEM "
            "AND R.ROTA = :valor)", {"valor": route})


def _travel_cargo(value):
    _bucket_label(travel_bi.CARGO_BUCKETS, value)
    case = _bucket_case("V.PESO", travel_bi.CARGO_BUCKETS, True)
    return (
        f"EXISTS (SELECT 1 FROM ROTEIRO_VIAGEM V WHERE V.EMPRESA = BASE.EMPRESA AND V.VIAGEM = BASE.VIAGEM "
        f"AND V.PESO > 0 AND {case} = :valor)", {"valor": value},
    )


# Mesmo CASE das faixas de consumo e de adiantamento da análise processada (`compute_deep_analytics`).
TRAVEL_KM_CONSUMPTION_CASES = [
    ("ate_200", "KM_CONSUMO <= 200"), ("de_201_800", "KM_CONSUMO <= 800"), ("de_801_1500", "KM_CONSUMO <= 1500"),
    ("de_1501_3000", "KM_CONSUMO <= 3000"), ("acima_3000", "1 = 1"),
]
TRAVEL_ADVANCE_CASES = [
    ("sem", "ADIANTAMENTO <= 0"), ("ate_300", "ADIANTAMENTO <= 300"), ("de_301_600", "ADIANTAMENTO <= 600"),
    ("de_601_1000", "ADIANTAMENTO <= 1000"), ("acima_1000", "1 = 1"),
]
_FORECAST = "DT_CHEGADA IS NOT NULL AND DT_PREV_CHEGADA IS NOT NULL AND TRUNC(DT_CHEGADA) {} DT_PREV_CHEGADA"
_OVERDUE = "DT_PREV_CHEGADA IS NOT NULL AND TRUNC(SYSDATE) - DT_PREV_CHEGADA {}"

# dimensão: (rótulo, filtro(valor) -> (sql, params), base). Base "trips": viagens do recorte;
# "routes": idem com o roteiro (ROTEIRO / ROTEIRO_VIAGEM); "open": a fila de viagens em aberto,
# que ignora período e situação como o painel; "catalog": placas do cadastro de veículos.
TRAVEL_DIMENSIONS = {
    "mes": ("Viagens geradas no período", _travel_month, "trips"),
    "custo_mes": ("Viagens da competência", _travel_cost_month, "trips"),
    "frota": ("Viagens da frota", _travel_equals("FROTA", _int), "trips"),
    "situacao": ("Viagens na situação", _travel_equals("SITUACAO"), "trips"),
    "empresa": ("Viagens da empresa", _travel_equals("EMPRESA", _int), "trips"),
    "erro": ("Viagens com erro de cadastro", _travel_equals("ERRO_CADASTRO"), "trips"),
    "km": ("Distância por viagem", _travel_bucket("KM_VALIDO", travel_bi.KM_BUCKETS, True), "trips"),
    "duracao": ("Duração da viagem", _travel_bucket("DURACAO_DIAS", travel_bi.DURATION_BUCKETS, False), "trips"),
    "motorista": ("Viagens do motorista", _travel_equals("CHV_MOTORISTA"), "trips"),
    "placa": ("Viagens do veículo", _travel_equals("PLACA"), "trips"),
    "km_consumo": ("Consumo por faixa de distância", _travel_case("KM_CONSUMO", TRAVEL_KM_CONSUMPTION_CASES), "trips"),
    "adiantamento": ("Faixa de adiantamento", _travel_case("ADIANTAMENTO", TRAVEL_ADVANCE_CASES), "trips"),
    "saida_dia": ("Viagens que saíram no dia da semana", _travel_weekday, "trips"),
    "previsto": ("Previsto x realizado", _travel_fixed({
        "same_date": _FORECAST.format("="), "late": _FORECAST.format(">"), "on_time": _FORECAST.format("<"),
    }), "trips"),
    "cobertura": ("Viagens com o campo preenchido", _travel_fixed({
        "km": "KM_VALIDO IS NOT NULL", "litros": "LITROS_VALIDOS IS NOT NULL",
        "adiantamento": "ADIANTAMENTO > 0", "duracao": "DURACAO_DIAS IS NOT NULL",
        "sem_km": "KM_VALIDO IS NULL",
    }), "trips"),
    # Central de Decisão: cards "Viagens" e "Em aberto" (as do recorte que não foram finalizadas).
    "todos": ("Viagens do recorte", lambda _value: ("1 = 1", {}), "trips"),
    "em_aberto": ("Viagens do recorte ainda em aberto", lambda _value: ("COD_SITUACAO <> 'F'", {}), "trips"),
    "rota": ("Viagens que passaram pela rota", _travel_route, "routes"),
    "carga": ("Carga por viagem", _travel_cargo, "routes"),
    "aberta": ("Viagens em aberto", _travel_fixed({
        "todas": "1 = 1", "no_prazo": _OVERDUE.format("<= 0"), "ate_7": _OVERDUE.format("BETWEEN 1 AND 7"),
        "de_8_a_30": _OVERDUE.format("BETWEEN 8 AND 30"), "acima_30": _OVERDUE.format("> 30"),
        "sem_previsao": "DT_PREV_CHEGADA IS NULL",
    }), "open"),
    "idade": ("Viagens dos veículos na faixa de idade", None, "catalog"),
    "modelo": ("Viagens dos veículos do modelo", None, "catalog"),
}

TRAVEL_LEG_COLUMNS = [
    {"key": "sequence", "label": "Perna", "type": "int"},
    {"key": "route", "label": "Rota"},
    {"key": "cities", "label": "Cidades", "wrap": True},
    {"key": "weight", "label": "Carga", "type": "kg", "total": True},
    {"key": "pallets", "label": "Paletes", "type": "int", "total": True},
    {"key": "deliveries", "label": "Entregas", "type": "int", "total": True},
    {"key": "manifest", "label": "Manifesto"},
    {"key": "company", "label": "Empresa", "type": "int"},
]

CITY_COLUMNS = ["USU_NOMCID"] + [f"USU_NOMCID{index}" for index in range(1, 11)]


def _travel_catalog_plates(dim, value):
    """Placas do cadastro de veículos na faixa de idade ou no modelo (o bloco "Perfil da frota")."""
    catalog = travel_bi.load_vehicles_catalog()
    if dim == "idade":
        _bucket_label(travel_bi.VEHICLE_AGE_BUCKETS, value)
        plates = sorted(plate for plate, item in catalog.items() if item.get("age_bucket") == value)
    else:
        plates = sorted(plate for plate, item in catalog.items() if item.get("model") == value)
    if not plates:
        return "1 = 0", {}
    binds = {f"placa{index}": plate for index, plate in enumerate(plates[:900])}
    return f"PLACA IN ({', '.join(':' + name for name in binds)})", binds


def travel_legs(value, label=""):
    """Roteiro de uma viagem (as pernas do USU_TROTVIA), para a fila de viagens em aberto."""
    trip = _int(value)
    if trip is None:
        raise UnknownDimension("Viagem inválida.")
    cities = ", ".join(f"T.{column}" for column in CITY_COLUMNS)
    rows = travel_bi._query(
        f"""
        SELECT T.USU_CODEMP AS EMPRESA, T.USU_SEQVIA AS SEQUENCIA, LTRIM(TRIM(T.USU_ROTCID), '0') AS ROTA,
               {cities}, T.USU_PESCAR AS PESO, T.USU_QTDPLT AS PALLETS, T.USU_QTDENT AS ENTREGAS,
               T.USU_NUMMAN AS MANIFESTO
        FROM USU_TROTVIA T
        WHERE T.USU_CODVIA = :viagem
        ORDER BY T.USU_CODEMP, T.USU_SEQVIA
        """,
        {"viagem": trip},
    )
    legs = []
    for row in rows:
        names = [str(row.get(column.lower()) or "").strip() for column in CITY_COLUMNS]
        legs.append({
            "sequence": _int(row.get("sequencia")), "route": row.get("rota") or "-",
            "cities": " > ".join(name for name in names if name) or "-",
            "weight": _float(row.get("peso")), "pallets": _int(row.get("pallets")), "deliveries": _int(row.get("entregas")),
            "manifest": str(row.get("manifesto") or "").strip() or "-", "company": _int(row.get("empresa")),
        })
    return {
        "title": label or f"Viagem {trip}",
        "subtitle": "Roteiro da viagem: as pernas lançadas no cadastro (USU_TROTVIA)",
        "summary": [
            _fact("Pernas", _int_display(len(legs))),
            _fact("Carga", f"{_int_display(sum(leg['weight'] or 0 for leg in legs))} kg"),
            _fact("Paletes", _int_display(sum(leg['pallets'] or 0 for leg in legs))),
        ],
        "sections": [{"key": "legs", "label": "Roteiro", "columns": TRAVEL_LEG_COLUMNS, "rows": legs,
                      "sort": {"key": "sequence", "dir": "asc"}, "empty": "Esta viagem ainda não tem roteiro lançado."}],
    }


TRAVEL_COLUMNS = [
    {"key": "trip", "label": "Viagem", "sub": "title"},
    {"key": "created", "label": "Gerada em", "type": "date"},
    {"key": "fleet", "label": "Frota"},
    {"key": "driver", "label": "Motorista", "wrap": True},
    {"key": "plate", "label": "Placa"},
    {"key": "situation", "label": "Situação"},
    {"key": "km", "label": "KM válido", "type": "int", "total": True},
    {"key": "liters", "label": "Litros", "type": "dec", "digits": 1, "total": True},
    {"key": "consumption", "label": "Consumo (km/l)", "type": "dec", "digits": 2, "ratio": ["km_consumo", "litros_consumo"]},
    {"key": "km_consumo", "label": "KM do consumo", "type": "int", "hidden": True},
    {"key": "litros_consumo", "label": "Litros do consumo", "type": "dec", "hidden": True},
    {"key": "advance", "label": "Adiantamento", "type": "money", "total": True},
    {"key": "departure", "label": "Saída", "type": "datetime"},
    {"key": "arrival", "label": "Chegada", "type": "datetime"},
    {"key": "duration", "label": "Duração (dias)", "type": "dec", "digits": 1, "total": "avg"},
    {"key": "error", "label": "Erro de cadastro", "wrap": True},
]


def travel_detail(period_key, carrier_key, situation_key, dim, value, label="", today=None):
    if dim == "roteiro":
        return travel_legs(value, label)
    if dim not in TRAVEL_DIMENSIONS:
        raise UnknownDimension("Este gráfico não tem detalhe por viagem.")
    title, build, kind = TRAVEL_DIMENSIONS[dim]
    predicate, predicate_params = _travel_catalog_plates(dim, value) if kind == "catalog" else build(value)
    period = _pinned(resolve_period(period_key, today=today), today)
    carriers = travel_bi.list_carriers()
    carrier = travel_bi.resolve_carrier(carrier_key, carriers)
    situation = travel_bi.resolve_situation(situation_key)
    if kind == "open":
        scope, params = travel_bi._scope_sql(period, carrier, situation, ignore_period=True, ignore_situation=True)
        scope = f"{scope} AND A.USU_SITVIA <> 'F'"
    else:
        scope, params = travel_bi._scope_sql(period, carrier, situation)
    base = travel_bi._base_with_routes(scope) if kind == "routes" else travel_bi._base(scope)
    query_params = {**params, **predicate_params}
    data = travel_bi._query_many(
        {
            "rows": f"""
                {base}
                SELECT EMPRESA, VIAGEM, TITULO, FROTA, MOTORISTA, CHV_MOTORISTA, PLACA, DATA_GERACAO, SITUACAO,
                       KM_VALIDO, LITROS, KM_CONSUMO, LITROS_CONSUMO, ADIANTAMENTO, DT_SAIDA, DT_CHEGADA,
                       DURACAO_DIAS, ERRO_CADASTRO
                FROM BASE WHERE {predicate}
                ORDER BY DATA_GERACAO DESC, VIAGEM DESC
                FETCH FIRST {DETAIL_LIMIT} ROWS ONLY
            """,
            "count": f"""
                {base}
                SELECT COUNT(*) AS TOTAL, SUM(KM_VALIDO) AS KM, SUM(ADIANTAMENTO) AS ADIANTAMENTO,
                       SUM(KM_CONSUMO) AS KM_CONSUMO, SUM(LITROS_CONSUMO) AS LITROS_CONSUMO,
                       COUNT(ERRO_CADASTRO) AS COM_ERRO
                FROM BASE WHERE {predicate}
            """,
        },
        {"rows": query_params, "count": query_params},
    )
    try:
        drivers = travel_bi.load_drivers_catalog()
    except Exception:
        drivers = {}
    fleet_labels = {str(item["key"]): item["label"] for item in carriers}
    rows = []
    for row in data["rows"]:
        fleet = _int(row.get("frota"))
        key = row.get("chv_motorista") or f"{fleet}-{_int(row.get('motorista'))}"
        rows.append({
            "trip": str(row.get("viagem") or ""),
            "title": f"título {row.get('titulo')}" if row.get("titulo") else "",
            "created": _iso(row.get("data_geracao")),
            "fleet": fleet_labels.get(str(fleet), f"Frota {fleet}"),
            "driver": travel_bi._driver_name(drivers, key, _int(row.get("motorista"))),
            "plate": row.get("placa") or "-",
            "situation": row.get("situacao") or "-",
            "km": _float(row.get("km_valido")) if row.get("km_valido") is not None else None,
            "liters": _float(row.get("litros")),
            "consumption": (_float(row.get("km_consumo")) / _float(row.get("litros_consumo"))) if row.get("litros_consumo") else None,
            "km_consumo": _float(row.get("km_consumo")) if row.get("km_consumo") is not None else None,
            "litros_consumo": _float(row.get("litros_consumo")) if row.get("litros_consumo") is not None else None,
            "advance": _float(row.get("adiantamento")),
            "departure": _iso(row.get("dt_saida")),
            "arrival": _iso(row.get("dt_chegada")),
            "duration": round(_float(row.get("duracao_dias")), 2) if row.get("duracao_dias") is not None else None,
            "error": row.get("erro_cadastro") or "",
            "__tone": "bad" if row.get("erro_cadastro") else None,
        })
    counts = (data["count"] or [{}])[0]
    total = _int(counts.get("total"))
    consumption = _float(counts.get("km_consumo")) / _float(counts.get("litros_consumo")) if counts.get("litros_consumo") else None
    return {
        "title": label or (_period_title(value) if dim == "mes" else str(value or "-")),
        "subtitle": f"{title} · {'fila completa, sem filtro de período e situação' if kind == 'open' else period['full_label']} · {carrier['label']}{_ref_note(today)}",
        "summary": [
            _fact("Viagens", _int_display(total), _truncation_note(total, len(rows))),
            _fact("KM válido", f"{_int_display(_float(counts.get('km')))} km"),
            _fact("Consumo", f"{_dec_display(consumption, 2)} km/l" if consumption else "–"),
            _fact("Adiantamento", _money_display(_float(counts.get("adiantamento")))),
            _fact("Com erro de cadastro", _int_display(counts.get("com_erro")), tone="bad" if _int(counts.get("com_erro")) else None),
        ],
        "sections": [{
            "key": "trips", "label": "Viagens", "columns": TRAVEL_COLUMNS, "rows": rows,
            "sort": {"key": "created", "dir": "desc"},
            "groupBy": ["fleet", "driver", "plate", "situation", "error", "created"],
            "empty": "Nenhuma viagem neste ponto.", "note": _truncation_note(total, len(rows)),
        }],
    }


# ---------------------------------------------------------------------------
# BI de Romaneios (Oracle, USU_TCONROM)
# ---------------------------------------------------------------------------

def _romaneio_branch(value):
    try:
        company, branch = (int(part) for part in str(value).split("-"))
    except (TypeError, ValueError):
        raise UnknownDimension("Filial inválida.")
    return "EMPRESA = :drill_empresa AND FILIAL = :drill_filial", {"drill_empresa": company, "drill_filial": branch}


def _romaneio_funnel(value):
    """Barra do funil: pallets com 1, 2 ou 3 etapas distintas, ou 4 (chegou a Carregar)."""
    stages = _int(value)
    if stages not in (1, 2, 3, 4):
        raise UnknownDimension("Etapa do funil inválida.")
    condition = "ETAPAS >= 4" if stages == 4 else f"ETAPAS = {stages}"
    return f"(EMPRESA, FILIAL, EMBALAGEM) IN (SELECT EMPRESA, FILIAL, EMBALAGEM FROM PALLETS WHERE {condition})", {}


# dimensão: (rótulo, filtro, base) — base "pallets" é o funil: olha o pallet inteiro, sem o
# filtro de etapa da tela (como `compute_deep_analytics`).
ROMANEIO_DIMENSIONS = {
    "mes": ("Registros no período", _travel_month, "records"),
    "etapa": ("Registros da etapa", _travel_equals("TIPREG", _int), "records"),
    "filial": ("Registros da filial", _romaneio_branch, "records"),
    "colaborador": ("Registros do colaborador", _travel_equals("MATRICULA", _int), "records"),
    "todos": ("Registros do recorte", lambda _value: ("1 = 1", {}), "records"),
    "sem_peso": ("Registros sem peso", lambda _value: ("(PESO IS NULL OR PESO <= 0)", {}), "records"),
    "funil": ("Registros dos pallets nesta etapa do funil", _romaneio_funnel, "pallets"),
}

ROMANEIO_COLUMNS = [
    {"key": "when", "label": "Data", "type": "date"},
    {"key": "pallet", "label": "Embalagem (pallet)"},
    {"key": "employee", "label": "Colaborador", "sub": "registration", "wrap": True},
    {"key": "stage", "label": "Etapa"},
    {"key": "branch", "label": "Filial"},
    {"key": "volumes", "label": "Volumes", "type": "int", "total": True},
    {"key": "weight", "label": "Peso", "type": "kg", "total": True},
    {"key": "address", "label": "Endereçamento"},
    {"key": "entry", "label": "Lançamento"},
]


def romaneio_detail(period_key, branch_key, stage_key, matricula_key, dim, value, label="", today=None):
    if dim not in ROMANEIO_DIMENSIONS:
        raise UnknownDimension("Este gráfico não tem detalhe por registro.")
    title, build, kind = ROMANEIO_DIMENSIONS[dim]
    predicate, predicate_params = build(value)
    period = _pinned(resolve_period(period_key, today=today), today)
    branches = romaneio_bi.list_branches()
    branch = romaneio_bi.resolve_branch(branch_key, branches)
    try:
        names = romaneio_bi._employee_names()
    except Exception:
        names = {}
    matricula = romaneio_bi.resolve_matricula(matricula_key, romaneio_bi.list_matriculas(names))
    stage = romaneio_bi.resolve_stage(stage_key)
    if kind == "pallets":
        stage = {"key": "all", "label": "Todas as etapas", "code": None}
    scope, params = romaneio_bi._scope_sql(period, branch, stage, matricula)
    base = romaneio_bi._base_with_pallets(scope) if kind == "pallets" else romaneio_bi._base(scope)
    query_params = {**params, **predicate_params}
    data = romaneio_bi._query_many(
        {
            "rows": f"""
                {base}
                SELECT EMPRESA, FILIAL, EMBALAGEM, MATRICULA, LANCAMENTO, ENDERECAMENTO, VOLUMES, PESO,
                       DATA_GERACAO, TIPREG, ESTAGIO
                FROM ROMANEIOS WHERE {predicate}
                ORDER BY DATA_GERACAO DESC
                FETCH FIRST {DETAIL_LIMIT} ROWS ONLY
            """,
            "count": f"""
                {base}
                SELECT COUNT(*) AS TOTAL, NVL(SUM(VOLUMES), 0) AS VOLUMES, NVL(SUM(PESO), 0) AS PESO,
                       COUNT(DISTINCT MATRICULA) AS COLABORADORES,
                       COUNT(DISTINCT EMPRESA || '-' || FILIAL || '-' || EMBALAGEM) AS PALLETS
                FROM ROMANEIOS WHERE {predicate}
            """,
        },
        {"rows": query_params, "count": query_params},
    )
    branch_labels = {item["key"]: item["label"] for item in branches}
    rows = []
    for row in data["rows"]:
        registration = _int(row.get("matricula"))
        key = f"{_int(row.get('empresa'))}-{_int(row.get('filial'))}"
        rows.append({
            "when": _iso(row.get("data_geracao")),
            "pallet": str(row.get("embalagem") or ""),
            "employee": names.get(registration) or f"Matrícula {registration}",
            "registration": f"matrícula {registration}" if names.get(registration) else "",
            "stage": row.get("estagio") or "-",
            "branch": branch_labels.get(key, f"Filial {_int(row.get('filial'))}"),
            "volumes": _int(row.get("volumes")) if row.get("volumes") is not None else None,
            "weight": _float(row.get("peso")) if row.get("peso") is not None else None,
            "address": str(row.get("enderecamento") or "").strip(),
            "entry": str(row.get("lancamento") or ""),
        })
    counts = (data["count"] or [{}])[0]
    total = _int(counts.get("total"))
    return {
        "title": label or (_period_title(value) if dim == "mes" else str(value)),
        "subtitle": f"{title} · {period['full_label']} · {branch['label']}{_ref_note(today)}",
        "summary": [
            _fact("Registros", _int_display(total), _truncation_note(total, len(rows))),
            _fact("Pallets", _int_display(counts.get("pallets"))),
            _fact("Volumes", _int_display(counts.get("volumes"))),
            _fact("Peso", f"{_int_display(_float(counts.get('peso')))} kg"),
            _fact("Colaboradores", _int_display(counts.get("colaboradores"))),
        ],
        "sections": [{
            "key": "records", "label": "Registros", "columns": ROMANEIO_COLUMNS, "rows": rows,
            "sort": {"key": "when", "dir": "desc"}, "groupBy": ["employee", "stage", "branch", "pallet"],
            "empty": "Nenhum registro neste ponto.", "note": _truncation_note(total, len(rows)),
        }],
    }


# ---------------------------------------------------------------------------
# BI de Compras (Oracle, ordens de compra)
# ---------------------------------------------------------------------------

COMPRAS_PRAZO_DIMENSIONS = {
    "mes": ("Itens de OC gerados no período", _travel_month),
    "familia": ("Itens da família", _travel_equals("DS_FAMILIA")),
    "origem": ("Itens da origem", _travel_equals("DS_ORIGEM")),
    "fornecedor": ("Itens do fornecedor", _travel_equals("FORNECEDOR", _int)),
    "todos": ("Itens de OC do recorte", lambda _value: ("1 = 1", {})),
}

COMPRAS_PRECO_DIMENSIONS = {
    "mes": ("Itens de OC gerados no período", _travel_month),
    "fornecedor": ("Itens do fornecedor", _travel_equals("FORNECEDOR", _int)),
    "produto": ("Itens do produto", _travel_equals("PRODUTO")),
    "todos": ("Itens de OC do recorte", lambda _value: ("1 = 1", {})),
}


def _prazo_columns(is_payment):
    days_key = "pay_days" if is_payment else "delivery_days"
    columns = [
        {"key": "order", "label": "OC", "sub": "sequence"},
        {"key": "created", "label": "Geração", "type": "date"},
        {"key": "supplier", "label": "Fornecedor", "wrap": True},
        {"key": "product", "label": "Produto", "wrap": True},
        {"key": "family", "label": "Família"},
        {"key": "origin", "label": "Origem"},
        {"key": "invoice", "label": "Nota"},
        {"key": "delivered", "label": "Entrada", "type": "date"},
        {"key": "delivery_days", "label": "Dias até a entrada", "type": "int", "total": "avg" if not is_payment else None},
        {"key": "paid", "label": "Pagamento", "type": "date"},
        {"key": "pay_days", "label": "Dias até o pagamento", "type": "int", "total": "avg" if is_payment else None},
        {"key": "contracted", "label": "Prazo contratado (dias)", "type": "int", "total": "avg"},
        {"key": "payment", "label": "Situação do pagamento"},
    ]
    for column in columns:
        if column["key"] == days_key:
            column["strong"] = True
    return columns


PRECO_COLUMNS = [
    {"key": "order", "label": "OC", "sub": "sequence"},
    {"key": "created", "label": "Geração", "type": "date"},
    {"key": "supplier", "label": "Fornecedor", "wrap": True},
    {"key": "product", "label": "Produto", "sub": "product_code", "wrap": True},
    {"key": "family", "label": "Família"},
    {"key": "quantity", "label": "Quantidade", "type": "dec", "digits": 2, "total": True},
    {"key": "unit_price", "label": "Preço unitário", "type": "money", "total": "avg", "strong": True},
    {"key": "value", "label": "Valor total", "type": "money", "total": True},
]


def compras_detail(period_key, view_key, fornecedor_key, filial_key, familia_key, dim, value, label="", today=None):
    view = compras_bi.resolve_view(view_key)
    is_price = view["key"] == "preco"
    dimensions = COMPRAS_PRECO_DIMENSIONS if is_price else COMPRAS_PRAZO_DIMENSIONS
    if dim not in dimensions:
        raise UnknownDimension("Este gráfico não tem detalhe por item de OC.")
    title, build = dimensions[dim]
    period = resolve_period(period_key or compras_bi.DEFAULT_PERIOD_KEY, today=today)
    if dim == "mes":
        value = _series_key(value, period, today)
    period = _pinned(period, today)
    predicate, predicate_params = build(value)
    fornecedor = compras_bi.resolve_fornecedor(fornecedor_key, compras_bi.list_fornecedores())
    branch = compras_bi.resolve_branch(filial_key, compras_bi.list_branches())
    familia = compras_bi.resolve_familia(familia_key, compras_bi.list_familias())
    scope_bits = [period["full_label"], fornecedor["label"], branch["label"], familia["label"]]
    if is_price:
        scope, params = compras_bi._scope_for_preco(period, fornecedor, branch, familia)
        base, table = compras_bi._itens(scope), "ITENS"
        select = ("ORDEM, SEQ_ORDEM, DS_FORNECEDOR, DS_PRODUTO, PRODUTO, DS_FAMILIA, QUANTIDADE, "
                  "VALOR_UNITARIO, VALOR_TOTAL, DATA_GERACAO")
        totals = "COUNT(*) AS TOTAL, SUM(QUANTIDADE) AS QUANTIDADE, SUM(VALOR_TOTAL) AS VALOR, AVG(VALOR_UNITARIO) AS PRECO"
    else:
        scope, params = compras_bi._scope_for_prazo(period, fornecedor, branch, familia)
        base, table = compras_bi._base(scope), "BASE"
        select = ("ORDEM, SEQ_ORDEM, NOTA, DS_FORNECEDOR, DS_PRODUTO, DS_FAMILIA, DS_ORIGEM, DATA_GERACAO, "
                  "DT_ENTREGA, DT_PAGAMENTO, DIAS_ENTREGA, DIAS_PAGAMENTO_REAL, PRAZO_CONTRATADO, SITUACAO_PAGAMENTO")
        prazo = "DIAS_PAGAMENTO_REAL" if view["key"] == "pagamento" else "DIAS_ENTREGA"
        totals = (f"COUNT(*) AS TOTAL, AVG(CASE WHEN {prazo} IS NOT NULL AND {prazo} >= 0 THEN {prazo} END) AS PRAZO, "
                  "AVG(PRAZO_CONTRATADO) AS META, COUNT(DISTINCT ORDEM) AS ORDENS")
    query_params = {**params, **predicate_params}
    data = compras_bi._query_many(
        {
            "rows": f"{base} SELECT {select} FROM {table} WHERE {predicate} ORDER BY DATA_GERACAO DESC FETCH FIRST {DETAIL_LIMIT} ROWS ONLY",
            "count": f"{base} SELECT {totals} FROM {table} WHERE {predicate}",
        },
        {"rows": query_params, "count": query_params},
    )
    counts = (data["count"] or [{}])[0]
    total = _int(counts.get("total"))
    rows = []
    for row in data["rows"]:
        item = {
            "order": str(row.get("ordem") or ""),
            "sequence": f"item {row.get('seq_ordem')}" if row.get("seq_ordem") is not None else "",
            "created": _iso(row.get("data_geracao")),
            "supplier": row.get("ds_fornecedor") or "-",
            "product": (row.get("ds_produto") or "").strip() or "-",
            "family": row.get("ds_familia") or "-",
        }
        if is_price:
            item.update({
                "product_code": str(row.get("produto") or ""),
                "quantity": _float(row.get("quantidade")),
                "unit_price": _float(row.get("valor_unitario")),
                "value": _float(row.get("valor_total")),
            })
        else:
            # O painel só faz média de prazo válido (>= 0): negativo é cadastro errado e fica em branco.
            pay_days = row.get("dias_pagamento_real")
            delivery_days = row.get("dias_entrega")
            item.update({
                "origin": row.get("ds_origem") or "-",
                "invoice": str(row.get("nota") or ""),
                "delivered": _iso(row.get("dt_entrega")),
                "delivery_days": _int(delivery_days) if delivery_days is not None and _float(delivery_days) >= 0 else None,
                "paid": _iso(row.get("dt_pagamento")),
                "pay_days": _int(pay_days) if pay_days is not None and _float(pay_days) >= 0 else None,
                "contracted": _int(row.get("prazo_contratado")) if row.get("prazo_contratado") is not None else None,
                "payment": row.get("situacao_pagamento") or "-",
            })
        rows.append(item)
    if is_price:
        summary = [
            _fact("Itens de OC", _int_display(total), _truncation_note(total, len(rows))),
            _fact("Valor total", _money_display(_float(counts.get("valor")))),
            _fact("Preço unitário médio", _money_display(_float(counts.get("preco")))),
            _fact("Quantidade", _dec_display(_float(counts.get("quantidade")), 2)),
        ]
        columns, group_by, sort = PRECO_COLUMNS, ["supplier", "product", "family", "order", "created"], {"key": "created", "dir": "desc"}
    else:
        label_prazo = "Prazo de pagamento médio" if view["key"] == "pagamento" else "Prazo de entrega médio"
        summary = [
            _fact("Itens de OC", _int_display(total), _truncation_note(total, len(rows))),
            _fact("Ordens de compra", _int_display(counts.get("ordens"))),
            _fact(label_prazo, f"{_dec_display(_float(counts.get('prazo')))} dias" if counts.get("prazo") is not None else "–"),
            _fact("Prazo contratado médio", f"{_dec_display(_float(counts.get('meta')))} dias" if counts.get("meta") is not None else "–"),
        ]
        columns = _prazo_columns(view["key"] == "pagamento")
        group_by, sort = ["supplier", "family", "origin", "product", "order", "payment"], {"key": "created", "dir": "desc"}
    return {
        "title": label or (_period_title(value) if dim == "mes" else str(value or "-")),
        "subtitle": f"{title} · {view['label']} · {' · '.join(scope_bits)}{_ref_note(today)}",
        "summary": summary,
        "sections": [{
            "key": "items", "label": "Itens de OC", "columns": columns, "rows": rows, "sort": sort, "groupBy": group_by,
            "empty": "Nenhum item neste ponto.", "note": _truncation_note(total, len(rows)),
        }],
    }
