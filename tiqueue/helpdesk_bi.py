"""Painel do Helpdesk, a tela inicial do Dashes: o que acontece na base de
chamados do ConnectMX, em fatos compactos para o filtro cruzado no navegador.

Lê só a base do ConnectMX (chamados do portal e o histórico importado do SM,
avaliações, conversas com o assistente de IA e base de conhecimento), nada do
ERP nem do SM ao vivo. Cada chamado vira uma linha com as dimensões que a tela
filtra (mês, empresa, setor, área, tipo, prioridade, situação, atendente,
canal, SLA, nota) e as medidas (1ª resposta e resolução em minutos). Como nos
outros painéis do Dashes, o navegador soma as linhas a cada clique e todos os
gráficos se filtram juntos. Nenhuma consulta altera dados.
"""

from datetime import datetime, time

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .models import (
    KnowledgeCategory,
    KnowledgeEntry,
    PortalAssistantConversation,
    PortalCompany,
    PortalDemand,
    PortalRequesterSector,
    TaskGroup,
    TaskType,
    userQueue,
)

MONTHS = 24
MONTH_NAMES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
TITLE_LIMIT = 140
DETAILS_LIMIT = 60

# Ordem das colunas de cada linha (`rows`); o navegador vira objeto com estas chaves.
# Protocolo e assunto ficam de fora (pesariam metade da página): a tabela de
# chamados pede os dos que mostra em `ticket_details`.
FIELDS = [
    "id",
    "m",    # mês de abertura (ano × 12 + mês − 1)
    "cm",   # mês de conclusão (vazio se não concluído)
    "w",    # dia da semana da abertura (0 = segunda)
    "h",    # hora da abertura
    "dd",   # dia do mês da abertura (com `m`, dá a data)
    "c", "s", "g", "t",  # empresa, setor, área (grupo) e tipo
    "p", "st", "a", "o",  # prioridade, situação, atendente e canal de abertura
    "sla",  # situação do prazo de resolução
    "r",    # nota da avaliação (1 a 10)
    "fr",   # minutos até a 1ª resposta pública
    "rt",   # minutos da abertura à conclusão
    "ro",   # reaberto (1/0)
    "kb",   # virou documentação na base de conhecimento (1/0)
]

SLA_STATES = [
    ("met", "Cumprido"),
    ("breached", "Violado"),
    ("running", "Em aberto, no prazo"),
    ("late", "Em aberto, atrasado"),
    ("paused", "Pausado (aguardando)"),
    ("none", "Sem SLA"),
]

STATUS_COLORS = {
    PortalDemand.STATUS_PENDING: "--mx-cat-4",
    PortalDemand.STATUS_ASSUMED: "--mx-cat-1",
    PortalDemand.STATUS_WAITING: "--mx-cat-7",
    PortalDemand.STATUS_COMPLETED: "--mx-cat-3",
    PortalDemand.STATUS_CANCELLED: "--mx-chart-3",
}
SLA_COLORS = {
    "met": "--mx-cat-3",
    "breached": "--mx-cat-8",
    "running": "--mx-cat-1",
    "late": "--mx-cat-2",
    "paused": "--mx-cat-7",
    "none": "--mx-chart-3",
}
CHANNEL_COLORS = {
    PortalDemand.OPENED_VIA_PORTAL: "--mx-cat-1",
    PortalDemand.OPENED_VIA_CHAT: "--mx-cat-2",
    PortalDemand.OPENED_VIA_ATTENDANT: "--mx-cat-7",
    PortalDemand.OPENED_VIA_SM: "--mx-chart-3",
}
OUTCOME_COLORS = {
    PortalAssistantConversation.OUTCOME_RESOLVED: "--mx-cat-3",
    PortalAssistantConversation.OUTCOME_TICKET: "--mx-cat-1",
    PortalAssistantConversation.OUTCOME_SOLUTION: "--mx-cat-4",
    PortalAssistantConversation.OUTCOME_SUMMARY: "--mx-cat-7",
    PortalAssistantConversation.OUTCOME_OPEN: "--mx-chart-3",
}
PRIORITY_COLORS = {"high": "--mx-cat-8", "medium": "--mx-cat-4", "low": "--mx-cat-3"}
# O cadastro da fila grava "Media" sem acento; no painel sai com acento.
PRIORITY_LABELS = {"high": "Alta", "medium": "Média", "low": "Baixa"}


def month_key(moment):
    return moment.year * 12 + moment.month - 1


def month_label(key):
    year, month = divmod(key, 12)
    return f"{MONTH_NAMES[month]}/{str(year)[2:]}"


def _first_month(today):
    total = month_key(today) - (MONTHS - 1)
    year, month = divmod(total, 12)
    return today.replace(year=year, month=month + 1, day=1)


def _minutes(start, end):
    if not start or not end or end < start:
        return None
    return round((end - start).total_seconds() / 60)


def _sla_state(status, due_at, completed_at, now):
    """Prazo de resolução: cumprido, violado, correndo, atrasado, pausado ou sem SLA."""
    if not due_at or status == PortalDemand.STATUS_CANCELLED:
        return "none"
    if completed_at:
        return "met" if completed_at <= due_at else "breached"
    if status == PortalDemand.STATUS_WAITING:
        return "paused"
    return "late" if now > due_at else "running"


def _user_name(user):
    return (getattr(user, "nameUser", "") or "").strip() or user.get_full_name() or user.username


def _names_with_blank(pairs, blank):
    names = {"0": blank}
    names.update({str(key): value for key, value in pairs})
    return names


def helpdesk_payload(today=None):
    """Linhas de chamados dos últimos 24 meses (e todo chamado ainda aberto,
    para o backlog), mais conversas com o assistente e registros da base."""
    now = timezone.now()
    today = today or timezone.localdate()
    start = _first_month(today)
    start_at = timezone.make_aware(datetime.combine(start, time.min))
    first_key = month_key(start)
    last_key = month_key(today)

    documented = set(
        KnowledgeEntry.objects.filter(source_demand__isnull=False).values_list("source_demand_id", flat=True)
    )
    queryset = (
        PortalDemand.objects.filter(Q(created_at__gte=start_at) | Q(status__in=PortalDemand.OPEN_STATUSES))
        .order_by("-created_at", "-id")
        .values_list(
            "id", "status", "priority_level", "opened_via",
            "company_id", "requester_sector_id", "task_group_id", "task_type_id", "assigned_to_id",
            "created_at", "first_response_at", "completed_at", "resolution_due_at",
            "feedback_rating", "reopen_count",
        )
    )

    priority_options = userQueue.default_field_options(userQueue.FIELD_PRIORITY)
    # Texto repetido em milhares de linhas vai como índice desta lista.
    enums = {
        "p": [value for value, _label, _color in priority_options],
        "st": [value for value, _label in PortalDemand.STATUS_CHOICES],
        "o": [value for value, _label in PortalDemand.OPENED_VIA_CHOICES],
        "sla": [value for value, _label in SLA_STATES],
    }
    index_of = {field: {value: position for position, value in enumerate(values)} for field, values in enums.items()}

    def enum(field, value, fallback):
        positions = index_of[field]
        if value not in positions:
            positions[value] = len(enums[field])
            enums[field].append(value)
        return positions.get(value, positions.get(fallback, 0))

    rows = []
    used = {"c": set(), "s": set(), "g": set(), "t": set(), "a": set()}
    for (
        demand_id, status, priority, channel,
        company_id, sector_id, group_id, type_id, attendant_id,
        created_at, first_response_at, completed_at, due_at,
        rating, reopen_count,
    ) in queryset.iterator(chunk_size=2000):
        opened = timezone.localtime(created_at)
        finished = timezone.localtime(completed_at) if completed_at and status == PortalDemand.STATUS_COMPLETED else None
        dims = {
            "c": company_id or 0, "s": sector_id or 0, "g": group_id or 0, "t": type_id or 0, "a": attendant_id or 0,
        }
        for key, value in dims.items():
            used[key].add(value)
        rows.append([
            demand_id,
            month_key(opened),
            month_key(finished) if finished else None,
            opened.weekday(),
            opened.hour,
            opened.day,
            dims["c"], dims["s"], dims["g"], dims["t"],
            enum("p", priority or userQueue.PRIORITY_MEDIUM, userQueue.PRIORITY_MEDIUM),
            enum("st", status, PortalDemand.STATUS_PENDING),
            dims["a"],
            enum("o", channel or PortalDemand.OPENED_VIA_PORTAL, PortalDemand.OPENED_VIA_PORTAL),
            enum("sla", _sla_state(status, due_at, finished, now), "none"),
            rating,
            _minutes(created_at, first_response_at),
            _minutes(created_at, completed_at) if finished else None,
            1 if reopen_count else 0,
            1 if demand_id in documented else 0,
        ])

    names = {
        "c": _names_with_blank(PortalCompany.objects.filter(id__in=used["c"]).values_list("id", "name"), "Sem empresa"),
        "s": _names_with_blank(PortalRequesterSector.objects.filter(id__in=used["s"]).values_list("id", "name"), "Sem setor"),
        "g": _names_with_blank(TaskGroup.objects.filter(id__in=used["g"]).values_list("id", "name"), "Sem área"),
        "t": _names_with_blank(TaskType.objects.filter(id__in=used["t"]).values_list("id", "name"), "Sem tipo"),
        "a": _names_with_blank(
            ((user.id, _user_name(user)) for user in User.objects.filter(id__in=used["a"])), "Sem atendente"
        ),
        "p": {value: PRIORITY_LABELS.get(value, label) for value, label, _color in priority_options},
        "st": dict(PortalDemand.STATUS_CHOICES),
        "o": dict(PortalDemand.OPENED_VIA_CHOICES),
        "sla": dict(SLA_STATES),
        "w": {str(index): label for index, label in enumerate(["seg", "ter", "qua", "qui", "sex", "sáb", "dom"])},
    }

    conversations = []
    for started_at, outcome, company_id, sector_id, turns, suggested in PortalAssistantConversation.objects.filter(
        started_at__gte=start_at
    ).values_list("started_at", "outcome", "company_id", "sector_id", "turns", "suggested_solution"):
        conversations.append([
            month_key(timezone.localtime(started_at)), outcome, company_id or 0, sector_id or 0, turns, 1 if suggested else 0,
        ])

    categories = dict(KnowledgeCategory.objects.values_list("id", "name"))
    knowledge = []
    for inserted_at, category_id, source_id, resolved in KnowledgeEntry.objects.values_list(
        "inserted_at", "category_id", "source_demand_id", "is_resolved"
    ):
        knowledge.append([month_key(timezone.localtime(inserted_at)), category_id, 1 if source_id else 0, 1 if resolved else 0])

    detail_url = reverse("portalDemandCodeDetailPage", args=["__code__"])
    return {
        "fields": FIELDS,
        "rows": rows,
        "enums": enums,
        "months": [{"key": key, "label": month_label(key)} for key in range(first_key, last_key + 1)],
        "names": names,
        "colors": {
            "p": PRIORITY_COLORS,
            "st": STATUS_COLORS,
            "o": CHANNEL_COLORS,
            "sla": SLA_COLORS,
            "outcome": OUTCOME_COLORS,
        },
        "order": {
            "p": [value for value, _label, _color in reversed(priority_options)],
            "st": [value for value, _label in PortalDemand.STATUS_CHOICES],
            "o": [value for value, _label in PortalDemand.OPENED_VIA_CHOICES],
            "sla": [value for value, _label in SLA_STATES],
        },
        "assistant": {
            "fields": ["m", "outcome", "c", "s", "turns", "suggested"],
            "rows": conversations,
            "outcomes": dict(PortalAssistantConversation.OUTCOME_CHOICES),
        },
        "knowledge": {
            "fields": ["m", "category", "documented", "resolved"],
            "rows": knowledge,
            "categories": {str(key): value for key, value in categories.items()},
        },
        "open_statuses": PortalDemand.OPEN_STATUSES,
        "detail_url": detail_url,
        "details_url": reverse("dashesHelpdeskTickets"),
        "loaded_at": timezone.localtime().strftime("%H:%M:%S"),
        "today": today.isoformat(),
    }


def ticket_details(ids):
    """Protocolo e assunto dos chamados que a tabela do painel mostra."""
    clean = []
    for raw in ids:
        text = str(raw).strip()
        if text.isdigit() and int(text) not in clean:
            clean.append(int(text))
    rows = PortalDemand.objects.filter(id__in=clean[:DETAILS_LIMIT]).values_list("id", "access_code", "title")
    return {str(demand_id): {"code": code or "", "title": (title or "")[:TITLE_LIMIT]} for demand_id, code, title in rows}
