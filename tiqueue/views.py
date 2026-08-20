from django.shortcuts import render, redirect, get_object_or_404
from .forms import (
    UserQueueCreateForm,
    UserQueueUpdateForm,
    TaskGroupForm,
    TaskTypeForm,
    ChecklistTemplateForm,
    ChecklistSectionForm,
    ChecklistFieldForm,
    ChecklistChoiceGroupForm,
    ChecklistChoiceOptionForm,
    ProjectForm,
    ProjectRoadmapItemForm,
    ProjectKanbanColumnForm,
    ProjectKanbanCardForm,
    HubToolCategoryForm,
    HubToolForm,
    HubUserToolForm,
    HubUserToolCategoryForm,
    KnowledgeCategoryForm,
    KnowledgeEntryForm,
    DemandTemplateForm,
    DemandTemplateDetailForm,
    MaintenanceTypeForm,
    MaintenanceSituationForm,
    MaintenanceIndicatorForm,
    MaintenanceSystemGroupForm,
    MaintenanceSystemForm,
    MaintenanceEventForm,
    MyAgendaReminderForm,
)
from .portal_forms import (
    PortalDemandForm,
    PortalDemandCustomFieldCreateForm,
    PortalDemandCustomFieldOptionForm,
    PortalDemandFeedbackForm,
    PortalDemandReplyForm,
    PortalRequesterAccountForm,
    PortalRequesterCollaboratorForm,
    PortalRequesterSectorForm,
    PortalDemandTransferForm,
    PortalDemandSlaPolicyForm,
    PortalCannedResponseForm,
)
from .models import (
    userQueue,
    concludedTasks,
    SeniorSystemUpdate,
    QueueTaskDetail,
    ChecklistTemplate,
    ChecklistSection,
    ChecklistField,
    ChecklistChoiceGroup,
    ChecklistChoiceOption,
    ChecklistEntry,
    ChecklistAnswer,
    Project,
    ProjectRoadmapItem,
    ProjectRoadmapSubtask,
    ProjectMilestone,
    ProjectKanbanColumn,
    ProjectKanbanCard,
    WifiVoucherGroup,
    WifiVoucher,
    HubToolCategory,
    HubTool,
    HubUserTool,
    HubUserToolCategory,
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeEntryAttachment,
    UserQueueKanbanColumn,
    UserQueueCustomColumn,
    UserQueueCustomColumnOption,
    UserQueueCustomValue,
    UserQueueFieldOption,
    UserQueueSavedView,
    SystemConfig,
    DemandTemplate,
    DemandTemplateDetail,
    PortalDemand,
    PortalDemandCustomField,
    PortalDemandCustomFieldOption,
    PortalDemandCustomValue,
    PortalDemandMessage,
    PortalDemandLog,
    PortalDemandAttachment,
    PortalDemandSlaPolicy,
    PortalCannedResponse,
    PortalRequesterSector,
    PortalRequesterCollaborator,
    PortalRequesterAccount,
    MaintenanceType,
    MaintenanceSituation,
    MaintenanceIndicator,
    MaintenanceSystemGroup,
    MaintenanceSystem,
    MaintenanceEvent,
    MyAgendaReminder,
    MaxiTetrisHighScore,
    PomodoroSession,
    ContractRecord,
    SystemNotification,
    DataModelLaunch,
    DataModelTable,
    DataModelField,
    DataModelRelation,
)
from accounts.models import User, DashesAiUsage
from .models import TaskType, TaskGroup
from . import services as service
from django.http import JsonResponse, HttpResponse
from django.contrib.auth import REDIRECT_FIELD_NAME, authenticate, login
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseBadRequest
from django.utils import timezone
import json
import unicodedata
import hmac
import threading
from django.db import transaction, models, IntegrityError
from django.db.models import Avg, Case, When, IntegerField, Value, Count, Q, Prefetch, Sum, DecimalField, Exists, OuterRef
from django.db.models.functions import Coalesce
from django.views.decorators.http import require_GET
from django import forms
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta, datetime
from django.utils import timezone
from django.utils.text import slugify
import io
import re
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
import os
from urllib import request as urllib_request, parse as urllib_parse, error as urllib_error
from xml.sax.saxutils import escape as xml_escape
import calendar as month_calendar
from functools import wraps
from .it_bi import (
    load_it_dashboard,
    build_it_ai_payload,
    compute_deep_analytics,
    it_dashboard_fingerprint,
    list_attendants,
    IT_BI_SYSTEM_PROMPT,
)
from .customer_dna import load_customer_dna, prepare_customer_insights, search_customers
from .models import CustomerInsightSnapshot, ItBiInsightSnapshot
from .models import Dashboard, DashboardAccess
from .ai_config import (
    ALLOWED_REASONING_EFFORTS,
    encrypt_secret,
    get_openai_runtime_config,
    public_openai_runtime_config,
)
from .openai_insights import OpenAIInsightError, generate_customer_insights, test_openai_connection
from .customer_dna_pdf import build_customer_dna_pdf
from .it_bi_pdf import build_it_bi_pdf

def _project_dashboard_deadline_meta(target_date, today):
    if not target_date:
        return {
            "label": "Sem prazo definido",
            "short_label": "Sem prazo",
            "css": "is-neutral",
            "sort_weight": 999999,
        }

    delta = (target_date - today).days
    if delta < 0:
        return {
            "label": f"{abs(delta)} dia(s) em atraso",
            "short_label": "Atrasado",
            "css": "is-overdue",
            "sort_weight": delta,
        }
    if delta == 0:
        return {
            "label": "Vence hoje",
            "short_label": "Hoje",
            "css": "is-today",
            "sort_weight": 0,
        }
    if delta == 1:
        return {
            "label": "Vence amanhã",
            "short_label": "Amanhã",
            "css": "is-soon",
            "sort_weight": 1,
        }
    if delta <= 7:
        return {
            "label": f"Vence em {delta} dia(s)",
            "short_label": f"{delta} dias",
            "css": "is-soon",
            "sort_weight": delta,
        }
    return {
        "label": target_date.strftime("%d/%m/%Y"),
        "short_label": target_date.strftime("%d/%m"),
        "css": "is-future",
        "sort_weight": delta,
    }


def _build_project_dashboard_bars(rows, total, color_map):
    if not rows:
        return []
    max_value = max((row["value"] for row in rows), default=0)
    safe_total = max(int(total or 0), 1)
    safe_max = max(int(max_value or 0), 1)
    for row in rows:
        value = int(row["value"] or 0)
        row["share_pct"] = int(round((value / safe_total) * 100)) if value else 0
        row["width_pct"] = int(round((value / safe_max) * 100)) if value else 0
        row["color"] = color_map.get(row["key"], "#61688c")
    return rows


def _dashboard_month_start(raw_value, today):
    raw_value = (raw_value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", raw_value or ""):
        try:
            parsed = datetime.strptime(raw_value, "%Y-%m").date()
            return parsed.replace(day=1)
        except ValueError:
            pass
    return today.replace(day=1)


def _shift_dashboard_month(month_start, delta):
    absolute_month = (month_start.year * 12) + (month_start.month - 1) + int(delta or 0)
    year = absolute_month // 12
    month = (absolute_month % 12) + 1
    return date(year, month, 1)


def _build_dashboard_delivery_calendar(today, open_projects, open_items, reference_month=None):
    display_month = reference_month or today.replace(day=1)
    month_names = [
        "Janeiro",
        "Fevereiro",
        "Março",
        "Abril",
        "Maio",
        "Junho",
        "Julho",
        "Agosto",
        "Setembro",
        "Outubro",
        "Novembro",
        "Dezembro",
    ]
    week_labels = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]
    is_current_month_view = display_month.year == today.year and display_month.month == today.month
    prev_month = _shift_dashboard_month(display_month, -1)
    next_month = _shift_dashboard_month(display_month, 1)
    base_payload = {
        "month_label": f"{month_names[display_month.month - 1]} {display_month.year}",
        "month_key": display_month.strftime("%Y-%m"),
        "prev_month_key": prev_month.strftime("%Y-%m"),
        "next_month_key": next_month.strftime("%Y-%m"),
        "today_month_key": today.strftime("%Y-%m"),
        "is_current_month_view": is_current_month_view,
        "week_labels": week_labels,
        "weeks": [],
        "busy_days": 0,
        "total_items": 0,
        "project_deliveries": 0,
        "step_deliveries": 0,
        "featured_day": {
            "key": today.isoformat(),
            "label": today.strftime("%d/%m/%Y"),
            "headline": "Nenhuma entrega programada.",
            "item_count": 0,
            "items": [],
        },
        "day_payloads": {},
    }

    day_map = {}

    def push_event(target_day, payload):
        if not target_day:
            return
        if target_day.year != display_month.year or target_day.month != display_month.month:
            return
        day_map.setdefault(target_day, []).append(payload)

    for project in open_projects:
        target_day = getattr(project, "end_date", None)
        if not target_day:
            continue
        accent = _normalize_hex_color(getattr(project, "color", ""), "#343955")
        project_responsible = getattr(project, "developer", None)
        project_responsible_name = (
            getattr(project_responsible, "nameUser", "")
            or getattr(project_responsible, "username", "")
            or "Sem responsável"
        )
        push_event(
            target_day,
            {
                "sort_rank": 0,
                "kind": "Projeto",
                "title": project.name,
                "subtitle": "Entrega principal do projeto",
                "meta": getattr(project, "dashboard_deadline", {}).get("label", target_day.strftime("%d/%m/%Y")),
                "responsible_name": project_responsible_name,
                "color": accent,
                "url": reverse("projectRoadmapView", args=[project.id]),
                "secondary_url": reverse("projectBoard", args=[project.id]),
                "primary_label": "Abrir roadmap",
                "secondary_label": "Kanban",
            },
        )
        base_payload["project_deliveries"] += 1

    for item in open_items:
        target_day = getattr(item, "end_date", None)
        if not target_day:
            continue
        project = getattr(item, "project", None)
        accent = _normalize_hex_color(getattr(project, "color", ""), "#4d77d9")
        responsible = getattr(item, "responsible", None)
        responsible_name = (
            getattr(responsible, "nameUser", "")
            or getattr(responsible, "username", "")
            or "Sem responsável"
        )
        push_event(
            target_day,
            {
                "sort_rank": 1,
                "kind": "Etapa",
                "title": item.title,
                "subtitle": getattr(project, "name", "Projeto"),
                "meta": item.get_status_display(),
                "responsible_name": responsible_name,
                "color": accent,
                "url": f"{reverse('projectRoadmapView', args=[project.id])}#roadmap-item-{item.id}",
                "secondary_url": reverse("projectBoard", args=[project.id]),
                "primary_label": "Ver etapa",
                "secondary_label": "Kanban",
            },
        )
        base_payload["step_deliveries"] += 1

    all_busy_days = sorted(day_map.keys())
    if all_busy_days and is_current_month_view:
        featured_day = next((candidate for candidate in all_busy_days if candidate >= today), all_busy_days[0])
    elif all_busy_days:
        featured_day = all_busy_days[0]
    else:
        featured_day = today if is_current_month_view else display_month

    calendar_weeks = []
    calendar_rows = month_calendar.Calendar(firstweekday=0).monthdatescalendar(display_month.year, display_month.month)
    for week in calendar_rows:
        week_cells = []
        for current_day in week:
            raw_items = sorted(
                day_map.get(current_day, []),
                key=lambda row: (row["sort_rank"], row["title"].lower()),
            )
            items = [{key: value for key, value in row.items() if key != "sort_rank"} for row in raw_items]
            item_count = len(items)
            headline = (
                "Nenhuma entrega programada."
                if item_count == 0
                else ("1 entrega programada." if item_count == 1 else f"{item_count} entregas programadas.")
            )
            day_payload = {
                "key": current_day.isoformat(),
                "label": current_day.strftime("%d/%m/%Y"),
                "headline": headline,
                "item_count": item_count,
                "items": items,
            }
            base_payload["day_payloads"][current_day.isoformat()] = day_payload
            week_cells.append(
                {
                    "key": current_day.isoformat(),
                    "day_number": current_day.day,
                    "is_current_month": current_day.month == display_month.month,
                    "is_today": current_day == today,
                    "is_past": current_day < today,
                    "is_sunday": current_day.weekday() == 6,
                    "is_selected": current_day == featured_day,
                    "has_items": item_count > 0,
                    "item_count": item_count,
                    "items": items,
                    "preview_limit": 2,
                    "preview_items": items[:2],
                    "more_count": max(0, item_count - 2),
                    "has_overflow": item_count > 2,
                }
            )
        calendar_weeks.append(week_cells)

    base_payload["weeks"] = calendar_weeks
    base_payload["busy_days"] = len(all_busy_days)
    base_payload["total_items"] = base_payload["project_deliveries"] + base_payload["step_deliveries"]
    base_payload["featured_day"] = base_payload["day_payloads"].get(featured_day.isoformat(), base_payload["featured_day"])
    return base_payload


def _build_user_project_dashboard(user, calendar_month=None):
    today = timezone.localdate()
    base = {
        "user_name": "Usuário",
        "today_label": today.strftime("%d/%m/%Y"),
        "summary": {
            "total_projects": 0,
            "open_projects": 0,
            "done_projects": 0,
            "responsible_projects": 0,
            "participant_projects": 0,
            "completion_pct": 0,
            "overdue_projects": 0,
            "overdue_steps": 0,
            "steps_assigned": 0,
            "steps_done": 0,
            "due_this_week": 0,
            "active_share_pct": 0,
            "risk_share_pct": 0,
        },
        "metric_cards": [],
        "focus_projects": [],
        "upcoming_steps": [],
        "project_status_rows": [],
        "roadmap_status_rows": [],
        "project_progress_rows": [],
        "progress_bucket_rows": [],
        "delivery_calendar": {
            "month_label": today.strftime("%m/%Y"),
            "week_labels": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"],
            "weeks": [],
            "busy_days": 0,
            "total_items": 0,
            "project_deliveries": 0,
            "step_deliveries": 0,
            "featured_day": {
                "key": today.isoformat(),
                "label": today.strftime("%d/%m/%Y"),
                "headline": "Nenhuma entrega programada.",
                "item_count": 0,
                "items": [],
            },
            "day_payloads": {},
        },
        "hero_notice": "",
    }

    if not getattr(user, "is_authenticated", False):
        return base

    user_name = getattr(user, "nameUser", "") or getattr(user, "username", "") or "Usuário"
    base["user_name"] = user_name

    project_qs = (
        Project.objects.select_related("developer")
        .prefetch_related("participants")
        .filter(Q(developer=user) | Q(participants=user))
        .distinct()
        .annotate(
            roadmap_total=Count("roadmap_items", distinct=True),
            roadmap_done=Count("roadmap_items", filter=Q(roadmap_items__status="done"), distinct=True),
            roadmap_doing=Count("roadmap_items", filter=Q(roadmap_items__status="doing"), distinct=True),
            roadmap_blocked=Count("roadmap_items", filter=Q(roadmap_items__status="blocked"), distinct=True),
            roadmap_overdue=Count(
                "roadmap_items",
                filter=Q(roadmap_items__end_date__lt=today) & ~Q(roadmap_items__status="done"),
                distinct=True,
            ),
            kanban_cards_total=Count("kanban_cards", distinct=True),
        )
        .order_by(
            Case(
                When(status="active", then=Value(0)),
                When(status="planned", then=Value(1)),
                When(status="paused", then=Value(2)),
                When(status="done", then=Value(3)),
                default=Value(4),
                output_field=IntegerField(),
            ),
            "end_date",
            "name",
        )
    )
    projects = list(project_qs)
    _decorate_project_catalog_items(projects)

    project_ids = [project.id for project in projects]
    open_projects = [project for project in projects if project.status != "done"]
    done_projects = [project for project in projects if project.status == "done"]
    responsible_projects = [project for project in projects if project.developer_id == user.id]
    participant_projects = [
        project for project in projects if project.developer_id != user.id and any(part.id == user.id for part in project.participants.all())
    ]
    overdue_projects = [project for project in open_projects if project.end_date and project.end_date < today]
    due_this_week = [
        project
        for project in open_projects
        if project.end_date and today <= project.end_date <= (today + timedelta(days=7))
    ]

    roadmap_items = []
    if project_ids:
        roadmap_items = list(
            ProjectRoadmapItem.objects.filter(project_id__in=project_ids)
            .select_related("project", "responsible")
            .order_by("end_date", "sort_order", "id")
        )

    steps_assigned = [item for item in roadmap_items if item.responsible_id == user.id]
    open_assigned_steps = [item for item in steps_assigned if item.status != "done"]
    done_assigned_steps = [item for item in steps_assigned if item.status == "done"]
    open_items = [item for item in roadmap_items if item.status != "done"]

    total_steps = sum(int(getattr(project, "roadmap_total", 0) or 0) for project in projects)
    done_steps = sum(int(getattr(project, "roadmap_done", 0) or 0) for project in projects)
    doing_steps = sum(int(getattr(project, "roadmap_doing", 0) or 0) for project in projects)
    blocked_steps = sum(int(getattr(project, "roadmap_blocked", 0) or 0) for project in projects)
    overdue_steps = sum(int(getattr(project, "roadmap_overdue", 0) or 0) for project in projects)
    planned_steps = max(total_steps - done_steps - doing_steps - blocked_steps, 0)
    completion_pct = int(round((done_steps / total_steps) * 100)) if total_steps else 0
    active_share_pct = int(round((len(open_projects) / len(projects)) * 100)) if projects else 0
    risk_share_pct = int(round((overdue_steps / total_steps) * 100)) if total_steps else 0

    for project in open_projects:
        deadline = _project_dashboard_deadline_meta(project.end_date, today)
        project.dashboard_deadline = deadline
        project.dashboard_role = "Responsável" if project.developer_id == user.id else "Participante"
        project.dashboard_role_css = "is-owner" if project.developer_id == user.id else "is-participant"
        project.dashboard_remaining_steps = max(int(getattr(project, "roadmap_total", 0) or 0) - int(getattr(project, "roadmap_done", 0) or 0), 0)
        project.dashboard_people = []
        if project.developer_id:
            project.dashboard_people.append(getattr(project.developer, "nameUser", "") or getattr(project.developer, "username", ""))
        for participant in project.participants.all():
            label = getattr(participant, "nameUser", "") or getattr(participant, "username", "")
            if label and label not in project.dashboard_people:
                project.dashboard_people.append(label)

    focus_projects = sorted(
        open_projects,
        key=lambda project: (
            0 if project.dashboard_deadline["css"] == "is-overdue" else 1,
            project.dashboard_deadline["sort_weight"],
            int(getattr(project, "roadmap_progress_pct", 0) or 0),
            project.name.lower(),
        ),
    )[:6]

    preferred_steps = open_assigned_steps or open_items
    upcoming_steps = []
    for item in sorted(
        preferred_steps,
        key=lambda roadmap_item: (
            _project_dashboard_deadline_meta(roadmap_item.end_date, today)["sort_weight"],
            getattr(roadmap_item.project, "name", "").lower(),
            roadmap_item.sort_order,
            roadmap_item.id,
        ),
    )[:7]:
        item.dashboard_deadline = _project_dashboard_deadline_meta(item.end_date, today)
        item.dashboard_project_style = f"--project-accent: {_normalize_hex_color(getattr(item.project, 'color', '#343955'))};"
        upcoming_steps.append(item)

    project_status_rows = _build_project_dashboard_bars(
        [
            {"key": "active", "label": "Em andamento", "value": sum(1 for project in projects if project.status == "active")},
            {"key": "planned", "label": "Planejados", "value": sum(1 for project in projects if project.status == "planned")},
            {"key": "paused", "label": "Pausados", "value": sum(1 for project in projects if project.status == "paused")},
            {"key": "done", "label": "Concluídos", "value": len(done_projects)},
        ],
        len(projects),
        {
            "active": "#4d77d9",
            "planned": "#6f84bb",
            "paused": "#f0a74d",
            "done": "#2fbf84",
        },
    )
    roadmap_status_rows = _build_project_dashboard_bars(
        [
            {"key": "planned", "label": "Planejadas", "value": planned_steps},
            {"key": "doing", "label": "Em execução", "value": doing_steps},
            {"key": "blocked", "label": "Bloqueadas", "value": blocked_steps},
            {"key": "done", "label": "Concluídas", "value": done_steps},
        ],
        total_steps,
        {
            "planned": "#6f84bb",
            "doing": "#4d77d9",
            "blocked": "#f0a74d",
            "done": "#2fbf84",
        },
    )

    project_progress_rows = []
    for project in focus_projects[:5]:
        project_progress_rows.append(
            {
                "name": project.name,
                "progress_pct": int(getattr(project, "roadmap_progress_pct", 0) or 0),
                "done": int(getattr(project, "roadmap_done", 0) or 0),
                "total": int(getattr(project, "roadmap_total", 0) or 0),
                "remaining": project.dashboard_remaining_steps,
                "color": _normalize_hex_color(project.color, "#343955"),
                "deadline_label": project.dashboard_deadline["short_label"],
            }
        )

    progress_bucket_specs = [
        ("bucket-start", "0-25%", "In\u00edcio", lambda pct: pct <= 25, "#7A8DBB"),
        ("bucket-build", "26-50%", "Avan\u00e7o", lambda pct: 26 <= pct <= 50, "#4D77D9"),
        ("bucket-grow", "51-75%", "Ritmo", lambda pct: 51 <= pct <= 75, "#00BF63"),
        ("bucket-final", "76-100%", "Reta final", lambda pct: pct >= 76, "#F0A74D"),
    ]
    progress_bucket_rows = []
    for key, range_label, short_label, matcher, color in progress_bucket_specs:
        count = sum(1 for project in open_projects if matcher(int(getattr(project, "roadmap_progress_pct", 0) or 0)))
        progress_bucket_rows.append(
            {
                "key": key,
                "label": range_label,
                "short_label": short_label,
                "value": count,
                "color": color,
            }
        )
    progress_bucket_rows = _build_project_dashboard_bars(progress_bucket_rows, len(open_projects), {row["key"]: row["color"] for row in progress_bucket_rows})
    delivery_calendar = _build_dashboard_delivery_calendar(today, open_projects, open_items, reference_month=calendar_month)

    base["summary"] = {
        "total_projects": len(projects),
        "open_projects": len(open_projects),
        "done_projects": len(done_projects),
        "responsible_projects": len(responsible_projects),
        "participant_projects": len(participant_projects),
        "completion_pct": completion_pct,
        "overdue_projects": len(overdue_projects),
        "overdue_steps": overdue_steps,
        "steps_assigned": len(open_assigned_steps),
        "steps_done": len(done_assigned_steps),
        "due_this_week": len(due_this_week),
        "active_share_pct": active_share_pct,
        "risk_share_pct": risk_share_pct,
    }
    base["metric_cards"] = [
        {"label": "Projetos ativos", "value": len(open_projects), "detail": f"{len(projects)} no total", "tone": "primary"},
        {"label": "Etapas concluídas", "value": done_steps, "detail": f"{completion_pct}% do roadmap", "tone": "success"},
        {"label": "Alertas de prazo", "value": overdue_steps, "detail": f"{len(overdue_projects)} projeto(s) em risco", "tone": "warning"},
        {"label": "Responsabilidades", "value": len(open_assigned_steps), "detail": "Etapas sob sua condução", "tone": "neutral"},
    ]
    base["focus_projects"] = focus_projects
    base["upcoming_steps"] = upcoming_steps
    base["project_status_rows"] = project_status_rows
    base["roadmap_status_rows"] = roadmap_status_rows
    base["project_progress_rows"] = project_progress_rows
    base["progress_bucket_rows"] = progress_bucket_rows
    base["delivery_calendar"] = delivery_calendar

    if overdue_steps:
        base["hero_notice"] = f"Você tem {overdue_steps} etapa(s) atrasada(s) para revisar."
    elif due_this_week:
        base["hero_notice"] = f"Há {len(due_this_week)} projeto(s) com entrega prevista para os próximos 7 dias."
    elif open_projects:
        base["hero_notice"] = "Seus projetos estão organizados e sem alertas imediatos."
    else:
        base["hero_notice"] = "Nenhum projeto vinculado ao seu usuário ainda."

    return base


def _build_hub_context(user):
    categories = (
        HubToolCategory.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "tools",
                queryset=HubTool.objects.filter(is_active=True).order_by("sort_order", "name", "id"),
                to_attr="active_tools",
            )
        )
        .order_by("sort_order", "name", "id")
    )
    my_tools_grouped = []
    if getattr(user, "is_authenticated", False):
        my_categories = list(
            HubUserToolCategory.objects.filter(user=user, is_active=True).order_by("sort_order", "name", "id")
        )
        my_tools = (
            HubUserTool.objects.filter(user=user, is_active=True)
            .select_related("category")
            .order_by("category__sort_order", "category__name", "category_name", "sort_order", "name", "id")
        )
        grouped_map = {}
        ordered_keys = []

        for cat in my_categories:
            key = (cat.name or "").strip()
            if not key:
                continue
            grouped_map[key] = {"name": key, "category_id": cat.id, "tools": []}
            ordered_keys.append(key)

        for tool in my_tools:
            key = (tool.category.name if tool.category_id else tool.category_name or "Meu HUB").strip() or "Meu HUB"
            if key not in grouped_map:
                grouped_map[key] = {"name": key, "category_id": tool.category_id, "tools": []}
                ordered_keys.append(key)
            grouped_map[key]["tools"].append(tool)
        my_tools_grouped = [grouped_map[key] for key in ordered_keys]

    return {
        "categories": categories,
        "my_tools_grouped": my_tools_grouped,
        "active_notifications": SystemNotification.objects.filter(is_active=True)[:8],
        "tetris_highscores": _get_tetris_highscores(user),
        "tetris_personal_best": _get_tetris_personal_best(user),
    }


def _timeline_initials(name):
    parts = [part for part in (name or "").strip().split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _pack_timeline_lanes(entries):
    lanes = []
    for entry in sorted(entries, key=lambda row: (row["start_col"], -row["duration"])):
        placed_lane = next((lane for lane in lanes if lane[-1]["end_col"] < entry["start_col"]), None)
        if placed_lane is not None:
            placed_lane.append(entry)
        else:
            lanes.append([entry])
    return lanes


def _build_project_timeline(today, projects, roadmap_items, months_before=2, months_after=3):
    """Builds a flat, row-based timeline grid spanning several months in a row
    (continuous horizontal scroll instead of page-by-page month navigation).
    One shared CSS grid: every day column, month/weekday header, row label and
    entry is placed on that same grid so everything lines up by construction."""
    anchor_month = today.replace(day=1)
    range_start = _shift_dashboard_month(anchor_month, -months_before)
    range_end_month = _shift_dashboard_month(anchor_month, months_after)
    days_in_last_month = month_calendar.monthrange(range_end_month.year, range_end_month.month)[1]
    range_end = range_end_month.replace(day=days_in_last_month)
    total_days = (range_end - range_start).days + 1

    def clip_range(raw_start, raw_end):
        if not raw_end:
            return None
        start = raw_start or raw_end
        if start > raw_end:
            start = raw_end
        if raw_end < range_start or start > range_end:
            return None
        clipped_start = max(start, range_start)
        clipped_end = min(raw_end, range_end)
        start_col = (clipped_start - range_start).days + 1
        end_col = (clipped_end - range_start).days + 1
        duration = end_col - start_col + 1
        return {
            "start_col": start_col,
            "end_col": end_col,
            "duration": duration,
            "grid_column_start": start_col + 1,
            "overflow_start": start < range_start,
            "overflow_end": raw_end > range_end,
            "is_milestone": duration == 1,
        }

    items_by_project = {}
    for item in roadmap_items:
        items_by_project.setdefault(item.project_id, []).append(item)

    by_responsible = {}

    def bucket(name):
        return by_responsible.setdefault(name, [])

    # A project and its own roadmap steps share a single lane: the project's
    # period always widens to fully cover its steps, and the steps render as
    # smaller segments layered on top of that same bar instead of on lanes
    # of their own — so they never get visually split apart from it.
    for project in projects:
        project_items = items_by_project.get(project.id, [])

        candidate_starts = []
        candidate_ends = []
        if getattr(project, "start_date", None):
            candidate_starts.append(project.start_date)
        if getattr(project, "end_date", None):
            candidate_ends.append(project.end_date)
        for item in project_items:
            if item.end_date:
                candidate_starts.append(item.start_date or item.end_date)
                candidate_ends.append(item.end_date)

        if not candidate_ends:
            continue
        overall_end = max(candidate_ends)
        overall_start = min(candidate_starts) if candidate_starts else overall_end
        if overall_start > overall_end:
            overall_start = overall_end

        clipped = clip_range(overall_start, overall_end)
        if not clipped:
            continue

        responsible = getattr(project, "developer", None)
        name = (
            getattr(responsible, "nameUser", "")
            or getattr(responsible, "username", "")
            or "Sem responsável"
        )
        accent = _normalize_hex_color(getattr(project, "color", ""), "#343955")

        steps = []
        for item in sorted(project_items, key=lambda row: (row.end_date or overall_end, row.sort_order, row.id)):
            if not item.end_date:
                continue
            step_clip = clip_range(item.start_date or item.end_date, item.end_date)
            if not step_clip:
                continue
            item_responsible = getattr(item, "responsible", None)
            step = {
                "title": item.title,
                "status": item.get_status_display(),
                "color": accent,
                "responsible_name": (
                    getattr(item_responsible, "nameUser", "")
                    or getattr(item_responsible, "username", "")
                    or "Sem responsável"
                ),
                "url": f"{reverse('projectRoadmapView', args=[project.id])}#roadmap-item-{item.id}",
            }
            step.update(step_clip)
            steps.append(step)

        # Position each step as a percentage of the parent bar's own width,
        # so it renders nested inside the project's bar (not on the shared
        # day grid) and can never drift out of alignment with it.
        parent_start_col = clipped["start_col"]
        parent_duration = clipped["duration"]
        for step in steps:
            local_start = step["start_col"] - parent_start_col
            step["pct_left"] = round((local_start / parent_duration) * 100, 3)
            step["pct_width"] = max(round((step["duration"] / parent_duration) * 100, 3), 4)

        project_entry = {
            "kind": "Projeto",
            "title": project.name,
            "project_name": project.name,
            "status": project.get_status_display(),
            "color": accent,
            "url": reverse("projectRoadmapView", args=[project.id]),
            "steps": steps,
        }
        project_entry.update(clipped)
        bucket(name).append(project_entry)

    people = []
    for name in sorted(by_responsible.keys(), key=lambda value: value.lower()):
        sub_lanes = _pack_timeline_lanes(by_responsible[name])
        people.append({"name": name, "sub_lanes": sub_lanes, "item_count": len(by_responsible[name])})
    people.sort(key=lambda person: -person["item_count"])

    rows = []
    current_row = 4  # rows 1-3 are reserved for the month / weekday / day-number header
    for person in people:
        label_row_start = current_row
        for sub_index, sub_lane in enumerate(person["sub_lanes"]):
            rows.append(
                {
                    "row_number": current_row,
                    "label": person["name"] if sub_index == 0 else "",
                    "initials": _timeline_initials(person["name"]) if sub_index == 0 else "",
                    "person_item_count": person["item_count"] if sub_index == 0 else 0,
                    "label_row_start": label_row_start,
                    "label_rowspan": len(person["sub_lanes"]),
                    "is_first_sub_lane": sub_index == 0,
                    "is_last_sub_lane": sub_index == len(person["sub_lanes"]) - 1,
                    "entries": sub_lane,
                }
            )
            current_row += 1
    row_count = current_row - 1

    month_names = [
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
    ]
    weekday_labels = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    day_headers = []
    month_headers = []
    today_grid_column = None
    current_month_key = None
    month_start_col = None
    for offset in range(total_days):
        day_date = range_start + timedelta(days=offset)
        grid_column = offset + 2
        is_today = day_date == today
        if is_today:
            today_grid_column = grid_column

        month_key = (day_date.year, day_date.month)
        if month_key != current_month_key:
            if current_month_key is not None:
                month_headers.append(
                    {
                        "label": f"{month_names[current_month_key[1] - 1]} {current_month_key[0]}",
                        "key": f"{current_month_key[0]}-{current_month_key[1]:02d}",
                        "grid_column_start": month_start_col,
                        "span": grid_column - month_start_col,
                    }
                )
            current_month_key = month_key
            month_start_col = grid_column

        day_headers.append(
            {
                "day_number": day_date.day,
                "weekday_label": weekday_labels[day_date.weekday()],
                "grid_column": grid_column,
                "is_today": is_today,
                "is_weekend": day_date.weekday() >= 5,
            }
        )
    if current_month_key is not None:
        month_headers.append(
            {
                "label": f"{month_names[current_month_key[1] - 1]} {current_month_key[0]}",
                "key": f"{current_month_key[0]}-{current_month_key[1]:02d}",
                "grid_column_start": month_start_col,
                "span": (total_days + 2) - month_start_col,
            }
        )

    return {
        "total_days": total_days,
        "day_headers": day_headers,
        "month_headers": month_headers,
        "weekend_columns": [day["grid_column"] for day in day_headers if day["is_weekend"]],
        "today_grid_column": today_grid_column,
        "today_month_key": today.strftime("%Y-%m"),
        "rows": rows,
        "row_count": row_count,
        "responsible_count": len(people),
        "has_data": bool(rows),
    }


def _load_global_project_delivery_scope(calendar_user_id=None):
    """Shared data scope (open projects + active roadmap items across the whole
    team, optionally filtered by responsible user) used by both the global
    calendar and the global timeline screens."""
    today = timezone.localdate()
    try:
        selected_user_id = int(calendar_user_id or 0) or None
    except (TypeError, ValueError):
        selected_user_id = None

    all_projects = list(
        Project.objects.select_related("developer")
        .prefetch_related("participants")
        .exclude(status="done")
        .annotate(
            roadmap_total=Count("roadmap_items", distinct=True),
            roadmap_done=Count("roadmap_items", filter=Q(roadmap_items__status="done"), distinct=True),
        )
        .order_by("end_date", "name")
    )
    _decorate_project_catalog_items(all_projects)

    for project in all_projects:
        project.dashboard_deadline = _project_dashboard_deadline_meta(project.end_date, today)

    all_project_ids = [project.id for project in all_projects]
    all_roadmap_items = []
    if all_project_ids:
        all_roadmap_items = list(
            ProjectRoadmapItem.objects.filter(project_id__in=all_project_ids)
            .exclude(status="done")
            .select_related("project", "responsible")
            .order_by("end_date", "sort_order", "id")
        )

    responsible_ids = set()
    for project in all_projects:
        if project.developer_id:
            responsible_ids.add(project.developer_id)
    for item in all_roadmap_items:
        if item.responsible_id:
            responsible_ids.add(item.responsible_id)

    calendar_user_options = []
    if responsible_ids:
        users_queryset = User.objects.filter(id__in=responsible_ids, is_active=True).order_by("nameUser", "username")
        calendar_user_options = [
            {
                "id": user.id,
                "label": getattr(user, "nameUser", "") or getattr(user, "username", "") or f"Usuario {user.id}",
            }
            for user in users_queryset
        ]

    if selected_user_id:
        projects = [project for project in all_projects if project.developer_id == selected_user_id]
        roadmap_items = [item for item in all_roadmap_items if item.responsible_id == selected_user_id]
    else:
        projects = list(all_projects)
        roadmap_items = list(all_roadmap_items)

    return {
        "today": today,
        "projects": projects,
        "roadmap_items": roadmap_items,
        "calendar_user_options": calendar_user_options,
        "selected_user_id": selected_user_id,
    }


def _build_global_project_calendar_context(calendar_month=None, calendar_user_id=None):
    scope = _load_global_project_delivery_scope(calendar_user_id)
    today = scope["today"]
    calendar_month = calendar_month or today.replace(day=1)
    projects = scope["projects"]
    roadmap_items = scope["roadmap_items"]

    context = {
        "today_label": today.strftime("%d/%m/%Y"),
        "metric_cards": [],
        "dashboard": {
            "delivery_calendar": _build_dashboard_delivery_calendar(today, [], [], reference_month=calendar_month),
        },
        "calendar_title": "Calendário geral de projetos",
        "calendar_subtitle": "Visual mensal dos projetos e etapas de toda a equipe",
        "calendar_detail_title": "Detalhes do dia",
        "calendar_detail_subtitle": "Abra o roadmap ou o Kanban de qualquer projeto a partir da agenda global",
        "calendar_user_options": scope["calendar_user_options"],
        "calendar_user_id": scope["selected_user_id"],
    }

    overdue_steps = sum(1 for item in roadmap_items if item.end_date and item.end_date < today)
    due_next_week = sum(1 for item in roadmap_items if item.end_date and today <= item.end_date <= (today + timedelta(days=7)))
    calendar_data = _build_dashboard_delivery_calendar(today, projects, roadmap_items, reference_month=calendar_month)

    context["metric_cards"] = [
        {
            "label": "Projetos em aberto",
            "value": len(projects),
            "detail": f"{len(context['calendar_user_options'])} responsável(eis) mapeado(s)",
            "tone": "primary",
        },
        {
            "label": "Etapas ativas",
            "value": len(roadmap_items),
            "detail": f"{calendar_data['step_deliveries']} etapa(s) com prazo no mês",
            "tone": "neutral",
        },
        {
            "label": "Entregas do mês",
            "value": calendar_data["total_items"],
            "detail": f"{calendar_data['busy_days']} dia(s) com movimentação",
            "tone": "success",
        },
        {
            "label": "Alertas de prazo",
            "value": overdue_steps,
            "detail": f"{due_next_week} entrega(s) nos próximos 7 dias",
            "tone": "warning",
        },
    ]
    context["dashboard"]["delivery_calendar"] = calendar_data
    return context


def _build_project_timeline_context(calendar_user_id=None):
    scope = _load_global_project_delivery_scope(calendar_user_id)
    today = scope["today"]
    projects = scope["projects"]
    roadmap_items = scope["roadmap_items"]

    timeline_data = _build_project_timeline(today, projects, roadmap_items)

    overdue_steps = sum(1 for item in roadmap_items if item.end_date and item.end_date < today)
    due_next_week = sum(1 for item in roadmap_items if item.end_date and today <= item.end_date <= (today + timedelta(days=7)))

    return {
        "today_label": today.strftime("%d/%m/%Y"),
        "timeline_title": "Timeline geral de projetos",
        "timeline_subtitle": "Uma linha por responsável, barras pela duração real de cada entrega",
        "calendar_user_options": scope["calendar_user_options"],
        "calendar_user_id": scope["selected_user_id"],
        "timeline": timeline_data,
        "metric_cards": [
            {
                "label": "Projetos em aberto",
                "value": len(projects),
                "detail": f"{len(scope['calendar_user_options'])} responsável(eis) mapeado(s)",
                "tone": "primary",
            },
            {
                "label": "Etapas ativas",
                "value": len(roadmap_items),
                "detail": f"{timeline_data['responsible_count']} responsável(eis) com entregas no período",
                "tone": "neutral",
            },
            {
                "label": "Alertas de prazo",
                "value": overdue_steps,
                "detail": f"{due_next_week} entrega(s) nos próximos 7 dias",
                "tone": "warning",
            },
        ],
    }


def index(request):
    _sync_service_notifications()
    _sync_portal_critical_notifications()
    dashboard = _build_user_project_dashboard(
        request.user,
        calendar_month=_dashboard_month_start(request.GET.get("calendar_month"), timezone.localdate()),
    )
    if request.GET.get("calendar_partial") == "1":
        return render(
            request,
            "partials/dashboard_delivery_calendar.html",
            {
                "dashboard": dashboard,
            },
        )

    return render(
        request,
        "tiqueue/index.html",
        {
            "dashboard": dashboard,
            "active_notifications": SystemNotification.objects.filter(is_active=True)[:8],
        },
    )


def allowed_dashboards(user):
    """Painéis ativos que este usuário pode abrir.

    A permissão é sempre explícita: sem registro em DashboardAccess não há
    acesso. A única exceção é o administrador do sistema, que enxerga o
    catálogo inteiro — é quem concede acesso aos demais.
    """
    if not user or not user.is_authenticated:
        return Dashboard.objects.none()

    catalog = Dashboard.objects.filter(is_active=True)
    if getattr(user, "is_system_admin", False) or getattr(user, "is_superuser", False):
        return catalog
    return catalog.filter(accesses__user=user).distinct()


def _dashes_access_required(slug):
    """Exige sessão do Dashes e permissão no painel indicado."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not (request.user.is_authenticated and request.session.get("dashes_authenticated")):
                query = urllib_parse.urlencode({"next": request.get_full_path()})
                return redirect(f"{reverse('dashesLoginPage')}?{query}")

            dashboards = allowed_dashboards(request.user)
            if not dashboards.filter(slug=slug).exists():
                return render(
                    request,
                    "tiqueue/dashes_denied.html",
                    {"dashboards": dashboards, "requested_slug": slug},
                    status=403,
                )

            request.dashes_menu = dashboards
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def dashesHomePage(request):
    """Manda para o primeiro painel liberado, em vez de um painel fixo."""
    if not (request.user.is_authenticated and request.session.get("dashes_authenticated")):
        return redirect("dashesLoginPage")

    first = allowed_dashboards(request.user).first()
    if not first:
        return render(request, "tiqueue/dashes_denied.html", {"dashboards": []}, status=403)
    return redirect(first.url_name)


def dashesLoginPage(request):
    next_url = (request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME) or "").strip()
    if request.user.is_authenticated and request.session.get("dashes_authenticated"):
        return redirect(next_url or "dashesHome")

    # Quem já entrou pela plataforma principal e tem painel liberado não precisa
    # digitar a senha de novo: a permissão é que decide o acesso, não a porta.
    if request.user.is_authenticated and allowed_dashboards(request.user).exists():
        request.session["dashes_authenticated"] = True
        request.session["dashes_authenticated_at"] = timezone.now().isoformat()
        return redirect(next_url or "dashesHome")

    error_message = None
    login_value = ""
    if request.method == "POST":
        login_value = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=login_value, password=password)

        if user is None or not user.is_active:
            error_message = "Usuário ou senha inválidos."
        elif not allowed_dashboards(user).exists():
            # Credencial válida, mas sem painel liberado: dizer isso é melhor do
            # que "usuário ou senha inválidos", que manda a pessoa tentar de novo.
            error_message = "Seu usuário não tem nenhum painel liberado. Fale com o administrador do sistema."
        else:
            login(request, user)
            request.session["dashes_authenticated"] = True
            request.session["dashes_authenticated_at"] = timezone.now().isoformat()
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect("dashesHome")

    return render(
        request,
        "tiqueue/dashes_login.html",
        {"error_message": error_message, "login_value": login_value, "next_url": next_url},
    )


@require_POST
def dashesLogoutPage(request):
    request.session.pop("dashes_authenticated", None)
    request.session.pop("dashes_authenticated_at", None)
    return redirect("dashesLoginPage")


@_dashes_access_required("ti-bi")
def dashesItBiPage(request):
    """Painel de BI do TI: indicadores do helpdesk (SM)."""
    period_key = (request.GET.get("periodo") or "12").strip()
    company_key = (request.GET.get("empresa") or "all").strip()
    attendant_key = (request.GET.get("atendente") or "all").strip()

    dashboard = None
    data_error = None
    try:
        dashboard = load_it_dashboard(period_key, company_key, attendant_key)
        attendant_key = dashboard["scope"]["attendant"]["key"]
    except Exception as exc:
        data_error = f"Não foi possível consultar o SM: {exc}"

    snapshot = _it_bi_snapshot(period_key, company_key, attendant_key)
    # A analise guardada pode ser de numeros antigos: comparar a impressao do
    # recorte avisa que ela envelheceu, em vez de exibir conclusao vencida.
    stale = bool(
        snapshot and dashboard and snapshot.source_fingerprint != it_dashboard_fingerprint(dashboard)
    )

    return render(
        request,
        "tiqueue/it_bi.html",
        {
            "dashboard": dashboard,
            "data_error": data_error,
            "insight_snapshot": snapshot,
            "insight_stale": stale,
            "dashes_mode": True,
            "active_dash": "ti-bi",
            "dashes_menu": allowed_dashboards(request.user),
            "it_bi_page_url": reverse("dashesItBiPage"),
            "it_bi_prepare_url": reverse("itBiPrepareInsights"),
            "it_bi_ai_url": reverse("itBiRequestAiInsights"),
            "it_bi_payload_url": reverse("itBiInsightPayloadApi"),
            "it_bi_pdf_url": reverse("itBiExportPdf"),
            "it_bi_scope_query": _it_bi_scope_query(period_key, company_key, attendant_key),
            "dna_ai_quota": _dashes_ai_quota(request.user),
            "dna_ai_limit_message": DASHES_AI_LIMIT_MESSAGE,
        },
    )


def _it_bi_scope_from_source(source):
    return (
        (source.get("periodo") or "12").strip(),
        (source.get("empresa") or "all").strip(),
        (source.get("atendente") or "all").strip(),
    )


def _it_bi_scope_query(period_key, company_key, attendant_key):
    return "?" + urllib_parse.urlencode(
        {"periodo": period_key, "empresa": company_key, "atendente": attendant_key}
    )


def _it_bi_snapshot(period_key, company_key, attendant_key):
    """Snapshot do recorte. O atendente entra na chave como período e empresa."""
    return ItBiInsightSnapshot.objects.filter(
        period_key=period_key, company_key=company_key, attendant_key=attendant_key
    ).first()


@login_required
@require_POST
def itBiPrepareInsights(request):
    """Calcula os indicadores do recorte e guarda o payload - nao chama a IA."""
    period_key, company_key, attendant_key = _it_bi_scope_from_source(request.POST)

    try:
        dashboard = load_it_dashboard(period_key, company_key, attendant_key)
        scope = dashboard["scope"]
        # Os cruzamentos pesados só rodam aqui, no pedido explícito: é isso que
        # separa o painel de abertura rápida das análises processadas.
        deep = compute_deep_analytics(
            scope["period"]["key"], scope["company"]["key"], scope["attendant"]["key"]
        )
    except Exception as exc:
        return JsonResponse(
            {"status": "error", "message": f"Nao foi possivel consultar o SM: {exc}"},
            status=503,
        )

    fingerprint = it_dashboard_fingerprint(dashboard)
    snapshot, created = ItBiInsightSnapshot.objects.update_or_create(
        period_key=scope["period"]["key"],
        company_key=scope["company"]["key"],
        attendant_key=scope["attendant"]["key"],
        defaults={
            "scope_label": (
                f"{scope['period']['label']} - {scope['company']['label']}"
                f" - {scope['attendant']['label']}"
            ),
            "source_fingerprint": fingerprint,
            "metrics": {
                "volume": dashboard["metrics"],
                "sla": dashboard["sla"],
                "ratings": {k: v for k, v in dashboard["ratings"].items() if k != "spread"},
                "logged": dashboard["logged"],
                "deep": deep,
            },
            "ai_payload": build_it_ai_payload(dashboard, fingerprint, deep=deep),
            "status": ItBiInsightSnapshot.STATUS_PREPARED,
            "ai_response": {},
            "ai_error": None,
            "created_by": request.user,
        },
    )
    return JsonResponse(
        {
            "status": "ok",
            "created": created,
            "snapshot_id": snapshot.id,
            "message": "Indicadores calculados e payload preparado.",
        }
    )


@login_required
@require_POST
def itBiRequestAiInsights(request):
    period_key, company_key, attendant_key = _it_bi_scope_from_source(request.POST)

    snapshot = _it_bi_snapshot(period_key, company_key, attendant_key)
    if snapshot is None or not snapshot.ai_payload:
        return JsonResponse(
            {"status": "error", "message": "Gere primeiro os indicadores deste recorte."},
            status=409,
        )

    # Mesma cota diaria do DNA: o teto e por usuario, nao por painel.
    quota = _dashes_ai_quota(request.user)
    if quota["blocked"]:
        return JsonResponse(
            {
                "status": "error",
                "code": "ai_daily_limit",
                "message": DASHES_AI_LIMIT_MESSAGE,
                "quota": {"limit": quota["limit"], "used": quota["used"]},
            },
            status=429,
        )

    runtime_config = get_openai_runtime_config()
    if not runtime_config["enabled"] or not runtime_config["api_key_configured"]:
        return JsonResponse(
            {"status": "error", "message": "Configure e ative a OpenAI em Sistema - Configuracoes."},
            status=409,
        )

    quota_record = _consume_dashes_ai_quota(request.user)

    snapshot.status = ItBiInsightSnapshot.STATUS_PROCESSING
    snapshot.ai_model = runtime_config["model"]
    snapshot.ai_requested_at = timezone.now()
    snapshot.save(update_fields=["status", "ai_model", "ai_requested_at", "updated_at"])

    try:
        ai_result = generate_customer_insights(
            snapshot.ai_payload,
            runtime_config=runtime_config,
            system_prompt=IT_BI_SYSTEM_PROMPT,
        )
    except Exception as exc:
        DashesAiUsage.objects.filter(pk=quota_record.pk, request_count__gt=0).update(
            request_count=models.F("request_count") - 1
        )
        if not isinstance(exc, OpenAIInsightError):
            exc = OpenAIInsightError(f"Falha inesperada ao processar os insights: {exc}")
        snapshot.status = ItBiInsightSnapshot.STATUS_ERROR
        snapshot.ai_error = str(exc)
        snapshot.ai_completed_at = timezone.now()
        snapshot.save(update_fields=["status", "ai_error", "ai_completed_at", "updated_at"])
        return JsonResponse({"status": "error", "message": str(exc)}, status=502)

    usage = ai_result["usage"]
    snapshot.status = ItBiInsightSnapshot.STATUS_COMPLETED
    snapshot.ai_response = ai_result["response"]
    snapshot.ai_response_id = ai_result["response_id"]
    snapshot.ai_model = ai_result["model"]
    snapshot.ai_input_tokens = usage["input_tokens"]
    snapshot.ai_output_tokens = usage["output_tokens"]
    snapshot.ai_total_tokens = usage["total_tokens"]
    snapshot.ai_error = None
    snapshot.ai_completed_at = timezone.now()
    snapshot.save()

    return JsonResponse(
        {
            "status": "ok",
            "snapshot_id": snapshot.id,
            "ai_status": snapshot.status,
            "quota": {"limit": quota["limit"], "used": quota_record.request_count},
        }
    )


@login_required
@require_GET
def itBiInsightPayloadApi(request):
    period_key, company_key, attendant_key = _it_bi_scope_from_source(request.GET)
    snapshot = _it_bi_snapshot(period_key, company_key, attendant_key)
    if snapshot is None:
        return JsonResponse({"status": "error", "message": "Nenhum payload preparado."}, status=404)
    return JsonResponse(
        {
            "snapshot_id": snapshot.id,
            "scope": snapshot.scope_label,
            "status": snapshot.status,
            "request": snapshot.ai_payload,
            "response": snapshot.ai_response,
            "metadata": {
                "model": snapshot.ai_model,
                "total_tokens": snapshot.ai_total_tokens,
                "requested_at": snapshot.ai_requested_at,
                "completed_at": snapshot.ai_completed_at,
                "error": snapshot.ai_error,
            },
        },
        json_dumps_params={"ensure_ascii": False, "indent": 2},
    )



def _customer_dna_scope_from_source(source):
    """Le view/unidade de um QueryDict (GET ou POST) e devolve o escopo do DNA."""
    view_mode = "group" if source.get("view") == "group" else "individual"
    raw_member_id = (source.get("unidade") or "").strip()
    try:
        member_customer_id = int(raw_member_id) if raw_member_id else None
    except (TypeError, ValueError):
        member_customer_id = None
    # Unidade so faz sentido dentro do grupo: no individual o escopo ja e o
    # proprio cliente, e deixar o parametro passar criaria dois snapshots
    # diferentes para o mesmo conjunto de dados.
    if view_mode != "group":
        member_customer_id = None
    return view_mode, member_customer_id


def _customer_dna_scope_from_request(request):
    return _customer_dna_scope_from_source(request.GET)


DASHES_AI_LIMIT_MESSAGE = (
    "Você atingiu o limite diário de análises de IA. "
    "Entre em contato com a administração ou com a TI para liberar novas consultas."
)


def _dashes_ai_quota(user):
    """Limite diário de IA e consumo de hoje. `limit=None` significa sem teto."""
    limit = getattr(user, "dashes_ai_daily_limit", None)
    today = timezone.localdate()
    used = (
        DashesAiUsage.objects.filter(user=user, usage_date=today)
        .values_list("request_count", flat=True)
        .first()
        or 0
    )
    return {
        "limit": limit,
        "used": used,
        "remaining": None if limit is None else max(limit - used, 0),
        "blocked": limit is not None and used >= limit,
        "date": today,
    }


def _consume_dashes_ai_quota(user):
    """Registra mais uma análise do dia.

    O incremento é feito no banco (`F`), não em Python: duas abas pedindo a
    análise ao mesmo tempo sobrescreveriam o contador uma da outra.
    """
    today = timezone.localdate()
    usage, _ = DashesAiUsage.objects.get_or_create(user=user, usage_date=today)
    DashesAiUsage.objects.filter(pk=usage.pk).update(
        request_count=models.F("request_count") + 1,
        last_request_at=timezone.now(),
    )
    usage.refresh_from_db()
    return usage


def _customer_dna_scope_query(view_mode, member_customer_id):
    """Querystring do escopo, para o JSON e o PDF abrirem no mesmo recorte da tela."""
    if view_mode != "group":
        return ""
    params = {"view": "group"}
    if member_customer_id:
        params["unidade"] = member_customer_id
    return "?" + urllib_parse.urlencode(params)


def _customer_dna_snapshot(customer_id, view_mode="individual", member_customer_id=None):
    """Snapshot daquele cliente **naquele escopo**.

    Individual, grupo inteiro e unidade do grupo geram numeros distintos e cada
    um guarda o seu snapshot; buscar so por customer_code devolvia o mais
    recente e misturava os escopos na tela, na IA, no JSON e no PDF.
    """
    return CustomerInsightSnapshot.objects.filter(
        customer_code=customer_id,
        view_mode=view_mode,
        member_customer_id=member_customer_id,
    ).first()


@_dashes_access_required("customer-dna")
def dashesCustomerDnaPage(request):
    raw_customer_id = (request.GET.get("cliente") or "10832").strip()
    try:
        customer_id = int(raw_customer_id)
    except (TypeError, ValueError):
        customer_id = 10832

    view_mode, member_customer_id = _customer_dna_scope_from_request(request)
    dashboard = None
    data_error = None
    try:
        dashboard = load_customer_dna(customer_id, view_mode, member_customer_id)
        if dashboard and not dashboard["group"]["is_group_view"]:
            view_mode, member_customer_id = "individual", None
        if dashboard is None:
            data_error = f"Nenhum faturamento elegível foi encontrado para o cliente {customer_id}."
    except Exception as exc:
        data_error = f"Não foi possível consultar o ERP Senior: {exc}"

    insight_snapshot = _customer_dna_snapshot(customer_id, view_mode, member_customer_id)
    return render(
        request,
        "tiqueue/customer_dna.html",
        {
            "dashboard": dashboard,
            "customer_id": customer_id,
            "dna_view_mode": view_mode,
            "dna_member_customer_id": member_customer_id,
            "data_error": data_error,
            "insight_snapshot": insight_snapshot,
            "dashes_mode": True,
            "active_dash": "customer-dna",
            "dashes_menu": allowed_dashboards(request.user),
            "dna_search_url": reverse("customerDnaSearchApi"),
            "dna_prepare_url": reverse("customerDnaPrepareInsights"),
            "dna_ai_url": reverse("customerDnaRequestAiInsights"),
            "dna_payload_url": reverse("customerDnaInsightPayloadApi", args=[customer_id]),
            "dna_pdf_url": reverse("customerDnaExportPdf", args=[customer_id]),
            "dna_page_url": reverse("dashesCustomerDnaPage"),
            "dna_scope_query": _customer_dna_scope_query(view_mode, member_customer_id),
            "dna_ai_quota": _dashes_ai_quota(request.user),
            "dna_ai_limit_message": DASHES_AI_LIMIT_MESSAGE,
        },
    )


@login_required
def customerDnaPage(request):
    raw_customer_id = (request.GET.get("cliente") or "10832").strip()
    try:
        customer_id = int(raw_customer_id)
    except (TypeError, ValueError):
        customer_id = 10832

    view_mode, member_customer_id = _customer_dna_scope_from_request(request)
    dashboard = None
    data_error = None
    try:
        dashboard = load_customer_dna(customer_id, view_mode, member_customer_id)
        if dashboard and not dashboard["group"]["is_group_view"]:
            view_mode, member_customer_id = "individual", None
        if dashboard is None:
            data_error = f"Nenhum faturamento elegível foi encontrado para o cliente {customer_id}."
    except Exception as exc:
        data_error = f"Não foi possível consultar o ERP Senior: {exc}"

    insight_snapshot = _customer_dna_snapshot(customer_id, view_mode, member_customer_id)

    return render(
        request,
        "tiqueue/customer_dna.html",
        {
            "dashboard": dashboard,
            "customer_id": customer_id,
            "dna_view_mode": view_mode,
            "dna_member_customer_id": member_customer_id,
            "data_error": data_error,
            "insight_snapshot": insight_snapshot,
            "dna_search_url": reverse("customerDnaSearchApi"),
            "dna_prepare_url": reverse("customerDnaPrepareInsights"),
            "dna_ai_url": reverse("customerDnaRequestAiInsights"),
            "dna_payload_url": reverse("customerDnaInsightPayloadApi", args=[customer_id]),
            "dna_pdf_url": reverse("customerDnaExportPdf", args=[customer_id]),
            "dna_page_url": reverse("customerDnaPage"),
            "dna_scope_query": _customer_dna_scope_query(view_mode, member_customer_id),
            "dna_ai_quota": _dashes_ai_quota(request.user),
            "dna_ai_limit_message": DASHES_AI_LIMIT_MESSAGE,
        },
    )


@login_required
@require_GET
def customerDnaSearchApi(request):
    term = (request.GET.get("q") or "").strip()
    try:
        items = search_customers(term)
    except Exception as exc:
        return JsonResponse(
            {"status": "error", "message": f"Falha ao consultar clientes no ERP Senior: {exc}"},
            status=503,
        )
    return JsonResponse({"status": "ok", "items": items})


@login_required
@require_POST
def customerDnaPrepareInsights(request):
    raw_customer_id = (request.POST.get("customer_id") or "").strip()
    try:
        customer_id = int(raw_customer_id)
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Cliente inválido."}, status=400)

    view_mode = "group" if request.POST.get("view") == "group" else "individual"
    raw_member_id = (request.POST.get("unidade") or "").strip()
    try:
        member_customer_id = int(raw_member_id) if raw_member_id else None
    except (TypeError, ValueError):
        member_customer_id = None

    try:
        prepared = prepare_customer_insights(customer_id, view_mode, member_customer_id)
    except Exception as exc:
        return JsonResponse(
            {"status": "error", "message": f"Não foi possível preparar os insights: {exc}"},
            status=503,
        )
    if prepared is None:
        return JsonResponse(
            {"status": "error", "message": "Nenhum dado elegível foi encontrado para este cliente."},
            status=404,
        )

    # O escopo pedido pode nao ser o escopo obtido (cliente sem grupo, unidade
    # invalida): grava o que o _resolve_customer_scope de fato resolveu, senao a
    # leitura procuraria por uma combinacao que nunca existiu.
    view_mode = prepared["view_mode"]
    member_customer_id = prepared["member_customer_id"]

    snapshot, created = CustomerInsightSnapshot.objects.update_or_create(
        customer_code=customer_id,
        source_fingerprint=prepared["source_fingerprint"],
        defaults={
            "view_mode": view_mode,
            "member_customer_id": member_customer_id,
            "customer_name": prepared["dashboard"]["customer"]["name"],
            "source_period_start": prepared["period_start"],
            "source_period_end": prepared["period_end"],
            "source_row_count": prepared["source_row_count"],
            "metrics": prepared["metrics"],
            "insight_cards": prepared["cards"],
            "ai_payload": prepared["ai_payload"],
            "created_by": request.user,
        },
    )
    return JsonResponse(
        {
            "status": "ok",
            "message": "Indicadores calculados e payload estrutural armazenado.",
            "created": created,
            "snapshot_id": snapshot.id,
            "redirect_url": f"{reverse('customerDnaPage')}?cliente={customer_id}",
        }
    )


@login_required
@require_POST
def customerDnaRequestAiInsights(request):
    raw_customer_id = (request.POST.get("customer_id") or "").strip()
    try:
        customer_id = int(raw_customer_id)
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Cliente inválido."}, status=400)

    view_mode, member_customer_id = _customer_dna_scope_from_source(request.POST)
    snapshot = _customer_dna_snapshot(customer_id, view_mode, member_customer_id)
    if snapshot is None or not snapshot.ai_payload:
        return JsonResponse(
            {"status": "error", "message": "Gere primeiro os indicadores e o payload deste cliente."},
            status=409,
        )

    # A cota e checada antes de qualquer coisa cara: a chamada a OpenAI custa
    # dinheiro e nao deve sair se o usuario ja estourou o teto do dia.
    quota = _dashes_ai_quota(request.user)
    if quota["blocked"]:
        return JsonResponse(
            {
                "status": "error",
                "code": "ai_daily_limit",
                "message": DASHES_AI_LIMIT_MESSAGE,
                "quota": {"limit": quota["limit"], "used": quota["used"]},
            },
            status=429,
        )

    runtime_config = get_openai_runtime_config()
    if not runtime_config["enabled"] or not runtime_config["api_key_configured"]:
        return JsonResponse(
            {"status": "error", "message": "Configure e ative a OpenAI em Sistema → Configurações."},
            status=409,
        )

    quota_record = _consume_dashes_ai_quota(request.user)

    snapshot.status = CustomerInsightSnapshot.STATUS_PROCESSING
    snapshot.ai_provider = "openai"
    snapshot.ai_model = runtime_config["model"]
    snapshot.ai_requested_at = timezone.now()
    snapshot.save(update_fields=["status", "ai_provider", "ai_model", "ai_requested_at", "updated_at"])

    try:
        ai_result = generate_customer_insights(snapshot.ai_payload, runtime_config=runtime_config)
    except Exception as exc:
        # Devolve a cota: o teto existe para controlar consumo de IA, e uma
        # chamada que falhou nao entregou analise nenhuma ao usuario.
        DashesAiUsage.objects.filter(pk=quota_record.pk, request_count__gt=0).update(
            request_count=models.F("request_count") - 1
        )
        if not isinstance(exc, OpenAIInsightError):
            exc = OpenAIInsightError(f"Falha inesperada ao processar os insights: {exc}")
        snapshot.status = CustomerInsightSnapshot.STATUS_ERROR
        snapshot.ai_error = str(exc)
        snapshot.ai_completed_at = timezone.now()
        snapshot.save(update_fields=["status", "ai_error", "ai_completed_at", "updated_at"])
        return JsonResponse(
            {
                "status": "error",
                "message": str(exc),
                "snapshot_id": snapshot.id,
                "redirect_url": f"{reverse('customerDnaPage')}?cliente={customer_id}",
            },
            status=502,
        )

    usage = ai_result["usage"]
    snapshot.status = CustomerInsightSnapshot.STATUS_COMPLETED
    snapshot.ai_response = ai_result["response"]
    snapshot.ai_response_id = ai_result["response_id"]
    snapshot.ai_model = ai_result["model"]
    snapshot.ai_input_tokens = usage["input_tokens"]
    snapshot.ai_output_tokens = usage["output_tokens"]
    snapshot.ai_total_tokens = usage["total_tokens"]
    snapshot.ai_error = None
    snapshot.ai_completed_at = timezone.now()
    snapshot.save(
        update_fields=[
            "status",
            "ai_response",
            "ai_response_id",
            "ai_model",
            "ai_input_tokens",
            "ai_output_tokens",
            "ai_total_tokens",
            "ai_error",
            "ai_completed_at",
            "updated_at",
        ]
    )
    return JsonResponse(
        {
            "status": "ok",
            "message": "Análise OpenAI gerada e armazenada com sucesso.",
            "snapshot_id": snapshot.id,
            "ai_status": snapshot.status,
            "quota": {"limit": quota["limit"], "used": quota_record.request_count},
            "redirect_url": f"{reverse('customerDnaPage')}?cliente={customer_id}",
        }
    )


@login_required
@require_GET
def customerDnaInsightPayloadApi(request, customer_id):
    view_mode, member_customer_id = _customer_dna_scope_from_request(request)
    snapshot = _customer_dna_snapshot(customer_id, view_mode, member_customer_id)
    if snapshot is None:
        return JsonResponse({"status": "error", "message": "Nenhum payload preparado."}, status=404)
    return JsonResponse(
        {
            "snapshot_id": snapshot.id,
            "status": snapshot.status,
            "request": snapshot.ai_payload,
            "response": snapshot.ai_response,
            "metadata": {
                "provider": snapshot.ai_provider,
                "model": snapshot.ai_model,
                "response_id": snapshot.ai_response_id,
                "input_tokens": snapshot.ai_input_tokens,
                "output_tokens": snapshot.ai_output_tokens,
                "total_tokens": snapshot.ai_total_tokens,
                "requested_at": snapshot.ai_requested_at,
                "completed_at": snapshot.ai_completed_at,
                "error": snapshot.ai_error,
            },
        },
        json_dumps_params={"ensure_ascii": False, "indent": 2},
    )


@login_required
@require_GET
def customerDnaExportPdf(request, customer_id):
    view_mode, member_customer_id = _customer_dna_scope_from_request(request)
    snapshot = _customer_dna_snapshot(customer_id, view_mode, member_customer_id)
    if snapshot is None or not snapshot.ai_payload or not snapshot.ai_response:
        return HttpResponse(
            "Gere os indicadores e conclua a análise de IA antes de exportar o PDF.",
            status=409,
            content_type="text/plain; charset=utf-8",
        )

    try:
        # Sem o escopo o PDF juntava indicadores do grupo com um dashboard
        # individual, e os numeros da capa nao batiam com os das paginas.
        dashboard = load_customer_dna(customer_id, snapshot.view_mode, snapshot.member_customer_id)
    except Exception as exc:
        return HttpResponse(f"Não foi possível consultar o ERP Senior: {exc}", status=503)
    if dashboard is None:
        return HttpResponse("Nenhum dado elegível foi encontrado para este cliente.", status=404)

    pdf_bytes = build_customer_dna_pdf(dashboard, snapshot)
    filename = f"connectmx-dna-cliente-{customer_id}-{slugify(snapshot.customer_name) or customer_id}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_GET
def itBiExportPdf(request):
    period_key, company_key, attendant_key = _it_bi_scope_from_source(request.GET)
    snapshot = _it_bi_snapshot(period_key, company_key, attendant_key)
    if snapshot is None or not snapshot.ai_payload:
        return HttpResponse(
            "Gere os indicadores deste recorte antes de exportar o PDF.",
            status=409,
            content_type="text/plain; charset=utf-8",
        )

    try:
        # Recarrega no mesmo recorte do snapshot: sem isso o PDF juntaria a
        # análise de um filtro com os números de outro.
        dashboard = load_it_dashboard(
            snapshot.period_key, snapshot.company_key, snapshot.attendant_key
        )
    except Exception as exc:
        return HttpResponse(f"Não foi possível consultar o SM: {exc}", status=503)

    pdf_bytes = build_it_bi_pdf(dashboard, snapshot)
    filename = f"connectmx-bi-ti-{slugify(snapshot.scope_label) or 'recorte'}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def projectCalendarPage(request):
    _sync_service_notifications()
    _sync_portal_critical_notifications()
    selected_user_id = request.GET.get("calendar_user")
    context = _build_global_project_calendar_context(
        calendar_month=_dashboard_month_start(request.GET.get("calendar_month"), timezone.localdate()),
        calendar_user_id=selected_user_id,
    )
    if request.GET.get("calendar_partial") == "1":
        return render(request, "partials/dashboard_delivery_calendar.html", context)

    context["active_notifications"] = SystemNotification.objects.filter(is_active=True)[:8]
    return render(request, "tiqueue/project_calendar.html", context)


def projectTimelinePage(request):
    _sync_service_notifications()
    _sync_portal_critical_notifications()
    selected_user_id = request.GET.get("calendar_user")
    context = _build_project_timeline_context(calendar_user_id=selected_user_id)
    if request.GET.get("calendar_partial") == "1":
        return render(request, "partials/project_timeline_view.html", context)

    context["active_notifications"] = SystemNotification.objects.filter(is_active=True)[:8]
    return render(request, "tiqueue/project_timeline.html", context)


def hubPage(request):
    _sync_service_notifications()
    _sync_portal_critical_notifications()
    return render(request, "tiqueue/hub.html", _build_hub_context(request.user))


def _portal_status_meta(status):
    mapping = {
        PortalDemand.STATUS_PENDING: {
            "label": "Pendente",
            "css": "is-pending",
            "color": "#c98b35",
        },
        PortalDemand.STATUS_ASSUMED: {
            "label": "Em atendimento",
            "css": "is-assumed",
            "color": "#4d77d9",
        },
        PortalDemand.STATUS_COMPLETED: {
            "label": "Concluída",
            "css": "is-completed",
            "color": "#2d9566",
        },
        PortalDemand.STATUS_CANCELLED: {
            "label": "Cancelada",
            "css": "is-cancelled",
            "color": "#9a5561",
        },
    }
    return mapping.get(
        status,
        {
            "label": status or "Sem status",
            "css": "is-unknown",
            "color": "#61688c",
        },
    )


def _portal_can_manage(user):
    return bool(getattr(user, "is_authenticated", False) and _is_system_admin(user))


def _portal_requester_account_record(user):
    if not getattr(user, "is_authenticated", False):
        return None
    cache_attr = "_portal_requester_account_cache"
    if hasattr(user, cache_attr):
        return getattr(user, cache_attr)
    account = (
        PortalRequesterAccount.objects.select_related("collaborator", "collaborator__sector", "user")
        .filter(user=user)
        .first()
    )
    setattr(user, cache_attr, account)
    return account


def _portal_requester_access_feature_enabled():
    return PortalRequesterAccount.objects.exists()


def _portal_can_open_new_demands(user):
    if _portal_can_manage(user):
        return True
    account = _portal_requester_account_record(user)
    if account:
        collaborator = account.collaborator
        sector = getattr(collaborator, "sector", None)
        return bool(
            getattr(user, "is_active", False)
            and account.is_active
            and getattr(collaborator, "is_active", False)
            and getattr(sector, "is_active", False)
        )
    return not _portal_requester_access_feature_enabled()


def _portal_pending_feedback_demands(user):
    """Concluded tickets of this requester that are still waiting for a rating.

    Business rule: a requester cannot open a new ticket while any of their own
    concluded tickets is still unrated. Portal managers are exempt, otherwise
    support staff would lock themselves out of the intake flow.
    """
    if not getattr(user, "is_authenticated", False):
        return []
    if _portal_can_manage(user):
        return []
    return list(
        PortalDemand.objects.filter(
            requester=user,
            status=PortalDemand.STATUS_COMPLETED,
            feedback_rating__isnull=True,
        ).order_by("completed_at", "id")
    )


def _portal_requester_access_denied_message():
    return "Seu usuário ainda não foi liberado para abrir demandas no portal de TI. Solicite o cadastro do setor, colaborador e acesso do portal."


def _sync_portal_requester_account_user(account, password=None, username=None):
    collaborator = account.collaborator
    user = account.user
    should_be_active = bool(
        account.is_active
        and getattr(collaborator, "is_active", False)
        and getattr(getattr(collaborator, "sector", None), "is_active", False)
    )
    if username:
        user.username = username
    user.userId = collaborator.registration_code
    user.nameUser = collaborator.full_name
    user.email = collaborator.email
    user.is_active = should_be_active
    if password:
        user.set_password(password)
    user.save()
    return user


def _sync_portal_requester_collaborator_accounts(collaborators):
    collaborator_ids = [row.id for row in collaborators if getattr(row, "id", None)]
    if not collaborator_ids:
        return
    accounts = (
        PortalRequesterAccount.objects.select_related("collaborator", "collaborator__sector", "user")
        .filter(collaborator_id__in=collaborator_ids)
    )
    for account in accounts:
        _sync_portal_requester_account_user(account)


def _portal_requester_collaborator_sync_errors(collaborator, linked_user=None):
    errors = {}
    compare_qs = User.objects.exclude(pk=getattr(linked_user, "pk", None)) if linked_user else User.objects.all()
    registration_code = (getattr(collaborator, "registration_code", "") or "").strip()
    email = (getattr(collaborator, "email", "") or "").strip().lower()
    if registration_code and compare_qs.filter(userId=registration_code).exists():
        errors["registration_code"] = "Já existe um usuário com esta matrícula."
    if email and compare_qs.filter(email=email).exists():
        errors["email"] = "Já existe um usuário com este e-mail."
    return errors


def _portal_scope_ids(task_group=None, task_type=None):
    group = task_group or getattr(task_type, "group", None)
    return getattr(group, "id", None), getattr(task_type, "id", None)


def _portal_format_minutes(total_minutes):
    total_minutes = int(total_minutes or 0)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes:02d}min"
    if hours:
        return f"{hours}h"
    return f"{minutes}min"


def _portal_relative_time_display(moment):
    if not moment:
        return "-"
    local_moment = timezone.localtime(moment)
    now = timezone.localtime(timezone.now())
    delta = now - local_moment
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    if total_minutes < 1:
        return "agora"
    if total_minutes < 60:
        return f"há {total_minutes} min"
    total_hours = total_minutes // 60
    if total_hours < 24:
        return f"há {total_hours}h"
    total_days = total_hours // 24
    if total_days < 7:
        return f"há {total_days} dia{'s' if total_days != 1 else ''}"
    return local_moment.strftime("%d/%m/%Y")


def _portal_similarity_tokens(*chunks):
    raw_text = " ".join(str(chunk or "") for chunk in chunks)
    normalized = unicodedata.normalize("NFKD", raw_text.lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = re.findall(r"[a-z0-9]{3,}", normalized)
    stop_words = {
        "para",
        "com",
        "sem",
        "uma",
        "que",
        "das",
        "dos",
        "por",
        "pra",
        "nao",
        "mais",
        "essa",
        "esse",
        "isso",
        "tipo",
        "grupo",
    }
    ordered = []
    for token in tokens:
        if token in stop_words or token in ordered:
            continue
        ordered.append(token)
    return ordered[:8]


def _portal_similarity_score(tokens, *chunks):
    haystack = " ".join(str(chunk or "") for chunk in chunks)
    normalized = unicodedata.normalize("NFKD", haystack.lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return sum(1 for token in tokens if token in normalized)


def _portal_trim_excerpt(value, limit=180):
    value = " ".join(str(value or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _portal_match_sla_policy(task_group=None, task_type=None, priority_level=None):
    group_id, type_id = _portal_scope_ids(task_group=task_group, task_type=task_type)
    policies = list(
        PortalDemandSlaPolicy.objects.filter(is_active=True)
        .select_related("task_group", "task_type", "default_attendant")
        .order_by("sort_order", "id")
    )
    candidates = []
    for policy in policies:
        if policy.task_type_id and policy.task_type_id != type_id:
            continue
        if policy.task_group_id and policy.task_group_id != group_id:
            continue
        if policy.priority_level and policy.priority_level != priority_level:
            continue
        specificity = (
            (4 if policy.task_type_id else 0)
            + (2 if policy.task_group_id else 0)
            + (1 if policy.priority_level else 0)
        )
        candidates.append((specificity, policy.sort_order, policy.id, policy))

    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
    return candidates[0][3]


def _portal_apply_sla_policy(demand, policy=None, save=False):
    matched_policy = policy or _portal_match_sla_policy(
        task_group=demand.task_group,
        task_type=demand.task_type,
        priority_level=demand.priority_level,
    )
    demand.sla_policy = matched_policy
    if matched_policy:
        opened_at = demand.created_at or timezone.now()
        demand.first_response_due_at = opened_at + timedelta(minutes=int(matched_policy.first_response_minutes or 0))
        demand.resolution_due_at = opened_at + timedelta(minutes=int(matched_policy.resolution_minutes or 0))
    else:
        demand.first_response_due_at = None
        demand.resolution_due_at = None
    if save and demand.pk:
        demand.save(update_fields=["sla_policy", "first_response_due_at", "resolution_due_at", "updated_at"])
    return matched_policy


def _portal_sla_metric(label, due_at, completed_at=None):
    local_due = timezone.localtime(due_at) if due_at else None
    local_completed = timezone.localtime(completed_at) if completed_at else None
    if not local_due:
        return {
            "label": label,
            "status": "Sem meta",
            "css": "is-neutral",
            "hint": "Nenhuma política de SLA aplicada.",
        }

    if local_completed:
        delta_minutes = int((local_completed - local_due).total_seconds() // 60)
        within = delta_minutes <= 0
        return {
            "label": label,
            "status": "Cumprido" if within else "Fora do prazo",
            "css": "is-success" if within else "is-danger",
            "hint": (
                f"Concluído em {local_completed.strftime('%d/%m/%Y %H:%M')}"
                if within
                else f"Concluído com atraso de {_portal_format_minutes(abs(delta_minutes))}"
            ),
        }

    now = timezone.localtime(timezone.now())
    delta_minutes = int((local_due - now).total_seconds() // 60)
    if delta_minutes >= 0:
        return {
            "label": label,
            "status": "No prazo",
            "css": "is-info",
            "hint": f"Restam {_portal_format_minutes(delta_minutes)} até {local_due.strftime('%d/%m/%Y %H:%M')}",
        }
    return {
        "label": label,
        "status": "Em atraso",
        "css": "is-danger",
        "hint": f"Atrasada há {_portal_format_minutes(abs(delta_minutes))}",
    }


def _portal_metric_chip_css(metric_css):
    mapping = {
        "is-success": "is-completed",
        "is-danger": "is-cancelled",
        "is-info": "is-assumed",
        "is-neutral": "is-soft-neutral",
    }
    return mapping.get(metric_css or "", "is-pending")


def _portal_due_display(due_at):
    if not due_at:
        return "-"
    return timezone.localtime(due_at).strftime("%d/%m/%Y %H:%M")


def _portal_triage_meta(demand):
    due_at = getattr(demand, "first_response_due_at", None) or getattr(demand, "resolution_due_at", None)
    priority_rank = {
        userQueue.PRIORITY_HIGH: 30,
        userQueue.PRIORITY_MEDIUM: 15,
        userQueue.PRIORITY_LOW: 0,
    }
    if not due_at:
        return {
            "label": "Normal",
            "css": "is-soft-neutral",
            "score": priority_rank.get(getattr(demand, "priority_level", ""), 0),
            "hint": "Demanda sem prazo definido por SLA.",
        }

    now = timezone.localtime(timezone.now())
    local_due = timezone.localtime(due_at)
    delta_minutes = int((local_due - now).total_seconds() // 60)
    base_score = priority_rank.get(getattr(demand, "priority_level", ""), 0)

    if delta_minutes < 0:
        return {
            "label": "Critica",
            "css": "is-cancelled",
            "score": 4000 + abs(delta_minutes) + base_score,
            "hint": f"SLA atrasado ha {_portal_format_minutes(abs(delta_minutes))}.",
        }
    if delta_minutes <= 60:
        return {
            "label": "Alta",
            "css": "is-pending",
            "score": 3000 - delta_minutes + base_score,
            "hint": f"Prazo vence em {_portal_format_minutes(delta_minutes)}.",
        }
    if delta_minutes <= 240:
        return {
            "label": "Media",
            "css": "is-assumed",
            "score": 2000 - delta_minutes + base_score,
            "hint": f"Prazo vence ainda hoje ou nas proximas horas.",
        }
    return {
        "label": "Normal",
        "css": "is-soft-neutral",
        "score": 1000 - min(delta_minutes, 999) + base_score,
        "hint": f"Prazo previsto para {_portal_due_display(local_due)}.",
    }


def _portal_build_sla_preview(policy):
    if not policy:
        return {
            "has_policy": False,
            "title": "Sem SLA configurado",
            "subtitle": "Nenhuma política ativa corresponde ao grupo, tipo e prioridade informados.",
        }

    return {
        "has_policy": True,
        "title": policy.name,
        "subtitle": policy.description or "Política aplicada automaticamente na abertura.",
        "first_response_display": _portal_format_minutes(policy.first_response_minutes),
        "resolution_display": _portal_format_minutes(policy.resolution_minutes),
        "default_attendant": _queue_collaborator_display_name(policy.default_attendant) if policy.default_attendant_id else "-",
        "auto_assign": bool(policy.auto_assign_on_create and policy.default_attendant_id),
    }


def _portal_pending_summary_data(pending_demands):
    now = timezone.localtime(timezone.now())
    today = now.date()
    critical = 0
    high_attention = 0
    due_today = 0
    auto_assignable = 0

    for demand in pending_demands:
        if getattr(demand, "triage_label", "") == "Critica":
            critical += 1
        if getattr(demand, "triage_label", "") in {"Critica", "Alta"}:
            high_attention += 1
        due_at = getattr(demand, "first_response_due_at", None)
        if due_at and timezone.localtime(due_at).date() == today:
            due_today += 1
        if getattr(getattr(demand, "sla_preview", {}), "get", None) and demand.sla_preview.get("auto_assign"):
            auto_assignable += 1

    return {
        "critical": critical,
        "high_attention": high_attention,
        "due_today": due_today,
        "auto_assignable": auto_assignable,
    }


def _portal_admin_operational_overview():
    open_demands = list(
        PortalDemand.objects.filter(status__in=[PortalDemand.STATUS_PENDING, PortalDemand.STATUS_ASSUMED])
        .select_related("requester", "assigned_to", "task_group", "task_type", "sla_policy", "sla_policy__default_attendant")
        .order_by("created_at", "id")
    )
    _decorate_portal_demands(open_demands)
    open_demands.sort(key=lambda demand: (-getattr(demand, "triage_score", 0), demand.created_at, demand.id))

    critical_demands = [demand for demand in open_demands if getattr(demand, "triage_label", "") == "Critica"]
    high_attention_demands = [
        demand for demand in open_demands if getattr(demand, "triage_label", "") in {"Critica", "Alta"}
    ]
    pending_demands = [demand for demand in open_demands if demand.status == PortalDemand.STATUS_PENDING]
    assumed_demands = [demand for demand in open_demands if demand.status == PortalDemand.STATUS_ASSUMED]

    spotlight_demands = critical_demands[:3] if critical_demands else high_attention_demands[:3]
    spotlight_title = "Demandas críticas agora" if critical_demands else "Demandas que exigem atenção"
    spotlight_empty = (
        "Nenhuma demanda crítica no momento. As próximas do SLA continuam monitoradas aqui."
        if not critical_demands
        else ""
    )

    return {
        "critical_count": len(critical_demands),
        "high_attention_count": len(high_attention_demands),
        "pending_count": len(pending_demands),
        "assumed_count": len(assumed_demands),
        "spotlight_title": spotlight_title,
        "spotlight_demands": spotlight_demands,
        "spotlight_empty": spotlight_empty,
        "critical_queue_url": f"{reverse('portalPendingDemandsPage')}?urgency=critica&sort=urgency",
        "attention_queue_url": f"{reverse('portalPendingDemandsPage')}?urgency=alta&sort=urgency",
    }


def _portal_ranked_canned_suggestions(demand, canned_responses, limit=5):
    demand_group_id = getattr(demand, "task_group_id", None)
    demand_type_id = getattr(demand, "task_type_id", None)

    def response_score(response):
        score = 0
        if getattr(response, "task_type_id", None):
            score += 5 if response.task_type_id == demand_type_id else -3
        if getattr(response, "task_group_id", None):
            score += 3 if response.task_group_id == demand_group_id else -2
        score -= int(getattr(response, "sort_order", 0) or 0) / 1000.0
        return score

    ranked = sorted(
        canned_responses,
        key=lambda response: (-response_score(response), getattr(response, "sort_order", 0), (response.title or "").lower(), response.id),
    )

    suggestions = []
    for response in ranked[:limit]:
        match_reasons = []
        if getattr(response, "task_type_id", None) and response.task_type_id == demand_type_id:
            match_reasons.append("mesmo tipo")
        if getattr(response, "task_group_id", None) and response.task_group_id == demand_group_id:
            match_reasons.append("mesmo grupo")
        response.match_reason = ", ".join(match_reasons) if match_reasons else "uso geral"
        suggestions.append(response)
    return suggestions


def _portal_knowledge_suggestions(title, description):
    tokens = _portal_similarity_tokens(title, description)
    if not tokens:
        return []

    lookup = Q()
    for token in tokens:
        lookup |= (
            Q(title__icontains=token)
            | Q(trigger__icontains=token)
            | Q(description__icontains=token)
            | Q(tags__icontains=token)
            | Q(resolution__icontains=token)
        )
    matches = (
        KnowledgeEntry.objects.select_related("category")
        .filter(lookup)
        .order_by("-updated_at", "-inserted_at", "-id")[:24]
    )
    ranked = []
    for entry in matches:
        score = _portal_similarity_score(
            tokens,
            entry.title,
            entry.trigger,
            entry.description,
            entry.tags,
            entry.resolution,
        )
        if score <= 0:
            continue
        ranked.append(
            {
                "id": entry.id,
                "title": entry.title,
                "category": entry.category.name,
                "score": score,
                "excerpt": _portal_trim_excerpt(entry.trigger or entry.description),
                "url": reverse("knowledgeEntryDetailPage", args=[entry.id]),
            }
        )
    ranked.sort(key=lambda row: (-row["score"], row["title"]))
    return ranked[:4]


def _portal_duplicate_suggestions(user, title, description):
    tokens = _portal_similarity_tokens(title, description)
    if not getattr(user, "is_authenticated", False) or not tokens:
        return []

    lookup = Q()
    for token in tokens:
        lookup |= Q(title__icontains=token) | Q(description__icontains=token)

    matches = (
        PortalDemand.objects.filter(requester=user)
        .exclude(status__in=[PortalDemand.STATUS_COMPLETED, PortalDemand.STATUS_CANCELLED])
        .filter(lookup)
        .select_related("task_group", "task_type")
        .order_by("-created_at", "-id")[:12]
    )
    ranked = []
    for demand in matches:
        score = _portal_similarity_score(tokens, demand.title, demand.description)
        if score <= 0:
            continue
        ranked.append(
            {
                "id": demand.id,
                "title": demand.title,
                "protocol": demand.protocol,
                "status": _portal_status_meta(demand.status)["label"],
                "created_at": timezone.localtime(demand.created_at).strftime("%d/%m/%Y %H:%M"),
                "url": demand.get_absolute_url(),
                "score": score,
            }
        )
    ranked.sort(key=lambda row: (-row["score"], row["protocol"]))
    return ranked[:4]


def _portal_can_access_demand(user, demand):
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(_portal_can_manage(user) or getattr(demand, "requester_id", None) == getattr(user, "id", None))


def _portal_can_reply_to_demand(user, demand):
    if not _portal_can_access_demand(user, demand):
        return False
    return demand.status not in {PortalDemand.STATUS_CANCELLED, PortalDemand.STATUS_COMPLETED}


def _portal_can_leave_feedback(user, demand):
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(
        getattr(demand, "requester_id", None) == getattr(user, "id", None)
        and demand.status == PortalDemand.STATUS_COMPLETED
        and not demand.has_feedback
    )


def _portal_actor_role(user, demand):
    if getattr(demand, "requester_id", None) == getattr(user, "id", None):
        return PortalDemandMessage.ROLE_REQUESTER
    return PortalDemandMessage.ROLE_ATTENDANT if _portal_can_manage(user) else PortalDemandMessage.ROLE_REQUESTER


def _portal_filter_private_activity_for_user(user, demand):
    if _portal_can_manage(user):
        return
    demand.thread_messages = [message for message in getattr(demand, "thread_messages", []) if not getattr(message, "is_internal", False)]
    demand.activity_logs = [
        entry
        for entry in getattr(demand, "activity_logs", [])
        if not getattr(getattr(entry, "related_message", None), "is_internal", False)
    ]


def _portal_redirect_target(request, fallback_name):
    fallback = reverse(fallback_name)
    candidate = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return fallback


def _portal_ai_routing_token():
    return (os.getenv("CONNECTMX_AI_ROUTING_TOKEN") or "").strip()


def _portal_ai_request_authorized(request):
    if _portal_can_manage(getattr(request, "user", None)):
        return True, "session"

    expected_token = _portal_ai_routing_token()
    if not expected_token:
        return False, "Token de integração não configurado no servidor."

    provided_token = (
        request.headers.get("X-ConnectMX-AI-Token")
        or request.headers.get("X-N8N-Token")
        or request.headers.get("Authorization")
        or ""
    ).strip()
    if provided_token.lower().startswith("bearer "):
        provided_token = provided_token[7:].strip()

    if not provided_token or not hmac.compare_digest(provided_token, expected_token):
        return False, "Token de integração inválido."

    return True, "token"


def _portal_priority_options_payload():
    options = []
    for value, label, color in userQueue.default_field_options(userQueue.FIELD_PRIORITY):
        options.append({"value": value, "label": label, "color": color})
    return options


def _portal_routing_policies_payload():
    policies = []
    for policy in _portal_sla_policies():
        policies.append(
            {
                "id": policy.id,
                "name": policy.name,
                "task_group_id": policy.task_group_id,
                "task_group_name": policy.task_group.name if policy.task_group_id else "",
                "task_type_id": policy.task_type_id,
                "task_type_name": policy.task_type.name if policy.task_type_id else "",
                "priority_level": policy.priority_level or "",
                "priority_label": policy.priority_display,
                "default_attendant_id": policy.default_attendant_id,
                "default_attendant_name": policy.default_attendant_display,
                "auto_assign_on_create": bool(policy.auto_assign_on_create),
                "first_response_minutes": int(policy.first_response_minutes or 0),
                "resolution_minutes": int(policy.resolution_minutes or 0),
            }
        )
    return policies


def _portal_custom_values_payload(demand):
    values = []
    for entry in PortalDemandCustomValue.objects.filter(demand=demand).select_related("field").order_by("field__sort_order", "field__id", "id"):
        field = getattr(entry, "field", None)
        values.append(
            {
                "field_id": field.id if field else None,
                "field_label": field.label if field else "",
                "field_key": field.field_key if field else "",
                "field_type": field.field_type if field else "",
                "value": entry.value or "",
            }
        )
    return values


def _portal_routing_context_payload(demand):
    task_groups = list(TaskGroup.objects.order_by("name"))
    task_types = list(TaskType.objects.select_related("group").order_by("group__name", "name"))
    return {
        "status": "ok",
        "demand": {
            "id": demand.id,
            "protocol": demand.protocol,
            "title": demand.title or "",
            "description": demand.description or "",
            "status": demand.status,
            "created_at": timezone.localtime(demand.created_at).isoformat() if demand.created_at else None,
            "requester": {
                "id": demand.requester_id,
                "user_code": getattr(demand.requester, "userId", "") or "",
                "name": _queue_collaborator_display_name(demand.requester),
            },
            "assigned_to_id": demand.assigned_to_id,
            "assigned_to_name": _queue_collaborator_display_name(demand.assigned_to) if demand.assigned_to_id else "",
            "task_group_id": demand.task_group_id,
            "task_type_id": demand.task_type_id,
            "priority_level": demand.priority_level or userQueue.PRIORITY_MEDIUM,
            "custom_values": _portal_custom_values_payload(demand),
        },
        "allowed_groups": [{"id": group.id, "name": group.name} for group in task_groups],
        "allowed_types": [
            {
                "id": task_type.id,
                "name": task_type.name,
                "group_id": task_type.group_id,
                "group_name": task_type.group.name if task_type.group_id else "",
                "color": task_type.color or "",
            }
            for task_type in task_types
        ],
        "priority_options": _portal_priority_options_payload(),
        "routing_policies": _portal_routing_policies_payload(),
        "apply_routing_url": reverse("portalDemandAiRoutingApplyApi", args=[demand.id]),
        "detail_url": demand.get_absolute_url(),
    }


def _portal_ai_webhook_url():
    return (os.getenv("CONNECTMX_AI_ROUTING_WEBHOOK_URL") or "").strip()


def _portal_ai_webhook_token():
    return (os.getenv("CONNECTMX_AI_ROUTING_WEBHOOK_TOKEN") or "").strip()


def _portal_ai_webhook_timeout():
    try:
        return max(3, int(os.getenv("CONNECTMX_AI_ROUTING_WEBHOOK_TIMEOUT") or 8))
    except (TypeError, ValueError):
        return 8


def _portal_connectmx_base_url(fallback=""):
    return (os.getenv("CONNECTMX_PUBLIC_BASE_URL") or fallback or "").strip().rstrip("/")


def _portal_absolute_connectmx_url(path, base_url=""):
    normalized_path = (path or "").strip()
    if not normalized_path:
        return ""
    if normalized_path.lower().startswith(("http://", "https://")):
        return normalized_path
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    resolved_base_url = _portal_connectmx_base_url(base_url)
    return f"{resolved_base_url}{normalized_path}" if resolved_base_url else normalized_path


def _portal_build_ai_routing_webhook_payload(demand, base_url=""):
    routing_context_url = _portal_absolute_connectmx_url(
        reverse("portalDemandAiRoutingContextApi", args=[demand.id]),
        base_url=base_url,
    )
    apply_routing_url = _portal_absolute_connectmx_url(
        reverse("portalDemandAiRoutingApplyApi", args=[demand.id]),
        base_url=base_url,
    )
    detail_url = _portal_absolute_connectmx_url(demand.get_absolute_url(), base_url=base_url)
    return {
        "event": "portal_demand_created",
        "triggered_at": timezone.localtime(timezone.now()).isoformat(),
        "connectmx_base_url": _portal_connectmx_base_url(base_url),
        "demand_id": demand.id,
        "protocol": demand.protocol,
        "title": demand.title or "",
        "description": demand.description or "",
        "status": demand.status,
        "priority_level": demand.priority_level or userQueue.PRIORITY_MEDIUM,
        "task_group_id": demand.task_group_id,
        "task_group_name": demand.task_group.name if demand.task_group_id else "",
        "task_type_id": demand.task_type_id,
        "task_type_name": demand.task_type.name if demand.task_type_id else "",
        "created_at": timezone.localtime(demand.created_at).isoformat() if demand.created_at else None,
        "requester": {
            "id": demand.requester_id,
            "user_code": getattr(demand.requester, "userId", "") or "",
            "name": _queue_collaborator_display_name(demand.requester),
        },
        "routing_context_url": routing_context_url,
        "apply_routing_url": apply_routing_url,
        "detail_url": detail_url,
    }


def _portal_send_ai_routing_webhook(demand_id, base_url=""):
    webhook_url = _portal_ai_webhook_url()
    if not webhook_url:
        return False

    demand = (
        PortalDemand.objects.select_related("requester", "task_group", "task_type")
        .filter(pk=demand_id)
        .first()
    )
    if not demand:
        return False

    source_key = f"portal-ai-routing-webhook-{demand.id}"
    payload = _portal_build_ai_routing_webhook_payload(demand, base_url=base_url)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    webhook_token = _portal_ai_webhook_token()
    if webhook_token:
        headers["Authorization"] = f"Bearer {webhook_token}"
        headers["X-ConnectMX-Webhook-Token"] = webhook_token

    request = urllib_request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=_portal_ai_webhook_timeout()) as response:
            status_code = getattr(response, "status", None) or response.getcode()
            if int(status_code or 0) >= 400:
                raise RuntimeError(f"Webhook retornou status {status_code}.")
    except Exception as exc:
        _upsert_system_notification(
            source_key=source_key,
            title="Falha no roteamento automático do portal",
            message=f"Não foi possível notificar o fluxo de IA para a demanda {demand.protocol}: {exc}",
            level=SystemNotification.LEVEL_WARNING,
        )
        return False

    notification = SystemNotification.objects.filter(source_key=source_key, is_active=True).first()
    if notification:
        notification.is_active = False
        notification.resolved_at = timezone.now()
        notification.save(update_fields=["is_active", "resolved_at", "updated_at"])
    return True


def _portal_schedule_ai_routing_webhook(demand_id, base_url=""):
    if not _portal_ai_webhook_url():
        return False
    worker = threading.Thread(
        target=_portal_send_ai_routing_webhook,
        args=(demand_id, base_url),
        daemon=True,
        name=f"cmx-ai-routing-{demand_id}",
    )
    worker.start()
    return True


def _create_portal_attachments(demand, files, uploaded_by, message=None):
    valid_files = [f for f in (files or []) if getattr(f, "name", "")]
    if not valid_files:
        return []
    uploaded_by_name = _queue_collaborator_display_name(uploaded_by) if uploaded_by else None
    created_rows = []
    for entry in valid_files:
        created_rows.append(
            PortalDemandAttachment.objects.create(
                demand=demand,
                message=message,
                uploaded_by=uploaded_by,
                uploaded_by_name=uploaded_by_name,
                original_name=os.path.basename(entry.name or "")[:255] or None,
                file=entry,
            )
        )
    return created_rows


def _format_portal_work_minutes(total_minutes):
    total_minutes = int(total_minutes or 0)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes:02d}min"
    if hours:
        return f"{hours}h"
    return f"{minutes}min"


def _portal_attendants_queryset():
    return User.objects.filter(is_active=True).filter(Q(is_system_admin=True) | Q(is_superuser=True)).order_by(
        "nameUser", "username", "id"
    )


def _portal_log_event(
    demand,
    event_type,
    actor=None,
    actor_name=None,
    summary="",
    details=None,
    from_attendant=None,
    to_attendant=None,
    related_message=None,
):
    return PortalDemandLog.objects.create(
        demand=demand,
        actor=actor,
        actor_name=(actor_name or (_queue_collaborator_display_name(actor) if actor else None)),
        event_type=event_type,
        summary=(summary or "").strip()[:255],
        details=(details or "").strip() or None,
        from_attendant=from_attendant,
        to_attendant=to_attendant,
        related_message=related_message,
    )


def _portal_next_queue_position(owner_user):
    aggregates = userQueue.objects.filter(user_code=str(getattr(owner_user, "userId", "") or "")).aggregate(
        max_position=models.Max("n_queue_position"),
        max_sort=models.Max("kanban_sort_order"),
    )
    return int(aggregates.get("max_position") or 0) + 1, int(aggregates.get("max_sort") or 0) + 1


def _move_portal_queue_item_to_attendant(queue_item, target_user):
    next_position, next_sort = _portal_next_queue_position(target_user)
    queue_item.user_code = target_user.userId
    queue_item.kanban_column = None
    queue_item.kanban_sort_order = next_sort
    queue_item.n_queue_position = next_position
    queue_item.is_current = False
    queue_item.save(update_fields=["user_code", "kanban_column", "kanban_sort_order", "n_queue_position", "is_current"])


def _decorate_portal_logs(demand):
    logs = list(getattr(demand, "activity_logs", []))
    for entry in logs:
        entry.actor_display = entry.display_actor_name
        entry.event_label = dict(PortalDemandLog.EVENT_CHOICES).get(entry.event_type, "Movimentação")
        entry.from_attendant_display = (
            _queue_collaborator_display_name(entry.from_attendant) if getattr(entry, "from_attendant_id", None) else "-"
        )
        entry.to_attendant_display = (
            _queue_collaborator_display_name(entry.to_attendant) if getattr(entry, "to_attendant_id", None) else "-"
        )
    demand.activity_logs = logs


def _decorate_portal_messages(demand):
    opening_attachments = list(getattr(demand, "opening_attachments", []))
    for attachment in opening_attachments:
        attachment.author_display = attachment.uploaded_by_name or (
            _queue_collaborator_display_name(attachment.uploaded_by) if attachment.uploaded_by_id else "Usuário"
        )

    message_rows = list(getattr(demand, "thread_messages", []))
    total_worked_minutes = 0
    for message in message_rows:
        message.author_display = message.display_author_name
        message.is_requester = message.author_role == PortalDemandMessage.ROLE_REQUESTER
        message.is_attendant = message.author_role == PortalDemandMessage.ROLE_ATTENDANT
        message.is_internal_note = bool(getattr(message, "is_internal", False))
        message.role_label = "Nota interna" if message.is_internal_note else dict(PortalDemandMessage.ROLE_CHOICES).get(
            message.author_role, "Mensagem"
        )
        message.canned_response_title = (
            getattr(message.canned_response, "title", "") if getattr(message, "canned_response_id", None) else ""
        )
        if message.has_worklog:
            total_worked_minutes += int(message.worked_minutes or 0)
            message.worked_period_display = (
                f"{message.work_started_at.strftime('%d/%m/%Y %H:%M')} até {message.work_ended_at.strftime('%d/%m/%Y %H:%M')}"
            )
        else:
            message.worked_period_display = ""
        for attachment in getattr(message, "prefetched_attachments", []):
            attachment.author_display = attachment.uploaded_by_name or (
                _queue_collaborator_display_name(attachment.uploaded_by) if attachment.uploaded_by_id else message.author_display
            )

    demand.opening_attachments = opening_attachments
    demand.thread_messages = message_rows
    demand.has_opening_block = bool((demand.description or "").strip() or opening_attachments)
    demand.reply_count = len(message_rows)
    demand.total_worked_minutes = total_worked_minutes
    demand.total_worked_display = _format_portal_work_minutes(total_worked_minutes) if total_worked_minutes else "-"


def _decorate_portal_demands(demands):
    priority_map = userQueue.default_field_option_map(userQueue.FIELD_PRIORITY)
    feedback_map = dict(PortalDemand.FEEDBACK_CHOICES)
    for demand in demands:
        demand.status_render = _portal_status_meta(demand.status)
        demand.priority_render = priority_map.get(
            demand.priority_level or "",
            {
                "label": demand.priority_level or "-",
                "color": "#61688c",
            },
        )
        demand.requester_display = _queue_collaborator_display_name(demand.requester)
        demand.assigned_display = _queue_collaborator_display_name(demand.assigned_to) if demand.assigned_to else "-"
        demand.feedback_rating_label = feedback_map.get(demand.feedback_rating or 0, "-")
        demand.detail_url = demand.get_absolute_url()
        demand.sla_preview = _portal_build_sla_preview(getattr(demand, "sla_policy", None))
        demand.sla_first_response = _portal_sla_metric("Primeira resposta", demand.first_response_due_at, demand.first_response_at)
        demand.sla_resolution = _portal_sla_metric("Resolução", demand.resolution_due_at, demand.completed_at)
        demand.sla_first_response_chip_css = _portal_metric_chip_css(demand.sla_first_response.get("css"))
        demand.sla_resolution_chip_css = _portal_metric_chip_css(demand.sla_resolution.get("css"))
        demand.first_response_due_display = _portal_due_display(demand.first_response_due_at)
        demand.resolution_due_display = _portal_due_display(demand.resolution_due_at)
        demand.triage_meta = _portal_triage_meta(demand)
        demand.triage_label = demand.triage_meta["label"]
        demand.triage_css = demand.triage_meta["css"]
        demand.triage_score = demand.triage_meta["score"]
        demand.triage_hint = demand.triage_meta["hint"]
        if demand.status == PortalDemand.STATUS_PENDING:
            demand.sla_next_label = "Primeira resposta"
            demand.sla_next_display = demand.first_response_due_display
            demand.sla_next_hint = demand.sla_first_response.get("hint")
        else:
            demand.sla_next_label = "Resolucao"
            demand.sla_next_display = demand.resolution_due_display
            demand.sla_next_hint = demand.sla_resolution.get("hint")
        demand.opened_since_display = _portal_relative_time_display(demand.created_at)
        demand.updated_since_display = _portal_relative_time_display(demand.updated_at)
        if demand.completed_at:
            demand.last_movement_label = "Concluída"
            demand.last_movement_display = demand.completed_at.strftime("%d/%m/%Y %H:%M")
        elif demand.assumed_at:
            demand.last_movement_label = "Assumida"
            demand.last_movement_display = demand.assumed_at.strftime("%d/%m/%Y %H:%M")
        else:
            demand.last_movement_label = "Abertura"
            demand.last_movement_display = demand.created_at.strftime("%d/%m/%Y %H:%M")

        if demand.status == PortalDemand.STATUS_PENDING:
            demand.next_action_label = "Aguardando atendente"
            demand.next_action_css = "is-pending"
            demand.next_action_hint = "A demanda ainda está na fila de triagem."
        elif demand.status == PortalDemand.STATUS_ASSUMED and not demand.first_response_at:
            demand.next_action_label = "Primeira resposta pendente"
            demand.next_action_css = "is-pending"
            demand.next_action_hint = "O atendimento foi assumido, mas ainda não houve resposta pública."
        elif demand.status == PortalDemand.STATUS_ASSUMED:
            demand.next_action_label = "Conversa em andamento"
            demand.next_action_css = "is-assumed"
            demand.next_action_hint = "A solicitação segue em atendimento."
        elif demand.status == PortalDemand.STATUS_COMPLETED and not demand.has_feedback:
            demand.next_action_label = "Aguardando feedback"
            demand.next_action_css = "is-pending"
            demand.next_action_hint = "O atendimento foi concluído e aguarda avaliação."
        elif demand.status == PortalDemand.STATUS_COMPLETED:
            demand.next_action_label = "Fechada com avaliação"
            demand.next_action_css = "is-completed"
            demand.next_action_hint = "A demanda foi concluída e já recebeu feedback."
        else:
            demand.next_action_label = "Encerrada"
            demand.next_action_css = "is-cancelled"
            demand.next_action_hint = "A demanda foi cancelada."
        if hasattr(demand, "prefetched_custom_values"):
            _decorate_portal_custom_values(demand)


def _portal_custom_field_queryset():
    return (
        PortalDemandCustomField.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "options",
                queryset=PortalDemandCustomFieldOption.objects.filter(is_active=True).order_by("sort_order", "id"),
            ),
            "task_groups",
            "task_types",
        )
        .order_by("sort_order", "id")
    )


def _portal_custom_fields():
    return list(_portal_custom_field_queryset())


def _portal_field_option_value(field, label):
    base_value = slugify(label or "") or "opcao"
    candidate = base_value[:40]
    suffix = 2
    while PortalDemandCustomFieldOption.objects.filter(field=field, value=candidate).exists():
        candidate = f"{base_value[:32]}-{suffix}"
        suffix += 1
    return candidate[:40]


def _portal_display_custom_value(field_type, raw_value):
    raw_value = "" if raw_value is None else str(raw_value).strip()
    if raw_value == "":
        return "-"
    if field_type == PortalDemandCustomField.FIELD_CHECKBOX:
        return "Sim" if raw_value == "1" else "Não"
    if field_type == PortalDemandCustomField.FIELD_DATE:
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return raw_value
    if field_type == PortalDemandCustomField.FIELD_NUMBER:
        try:
            numeric = Decimal(raw_value)
            return format(numeric, "f").rstrip("0").rstrip(".") or "0"
        except Exception:
            return raw_value
    return raw_value


def _decorate_portal_custom_values(demand):
    extra_details = []
    for custom_value in getattr(demand, "prefetched_custom_values", []):
        definition = getattr(custom_value, "field", None)
        if not definition:
            continue
        extra_details.append(
            {
                "label": definition.label,
                "value": _portal_display_custom_value(definition.field_type, custom_value.value),
                "field_type": definition.field_type,
            }
        )
    demand.extra_details = extra_details


def _portal_detail_queryset():
    return PortalDemand.objects.select_related(
        "requester", "assigned_to", "task_group", "task_type", "linked_queue_item", "sla_policy", "sla_policy__default_attendant"
    ).prefetch_related(
        Prefetch(
            "logs",
            queryset=PortalDemandLog.objects.select_related("actor", "from_attendant", "to_attendant", "related_message").order_by(
                "-created_at", "-id"
            ),
            to_attr="activity_logs",
        ),
        Prefetch(
            "attachments",
            queryset=PortalDemandAttachment.objects.filter(message__isnull=True)
            .select_related("uploaded_by")
            .order_by("created_at", "id"),
            to_attr="opening_attachments",
        ),
        Prefetch(
            "messages",
            queryset=PortalDemandMessage.objects.select_related("author", "canned_response")
            .prefetch_related(
                Prefetch(
                    "attachments",
                    queryset=PortalDemandAttachment.objects.select_related("uploaded_by").order_by("created_at", "id"),
                    to_attr="prefetched_attachments",
                )
            )
            .order_by("created_at", "id"),
            to_attr="thread_messages",
        ),
        Prefetch(
            "custom_values",
            queryset=PortalDemandCustomValue.objects.select_related("field").order_by("field__sort_order", "field__id", "id"),
            to_attr="prefetched_custom_values",
        ),
    )


def _portal_requester_queryset(user):
    return (
        PortalDemand.objects.filter(requester=user)
        .select_related("task_group", "task_type", "assigned_to", "linked_queue_item", "sla_policy", "sla_policy__default_attendant")
        .order_by("-created_at", "-id")
    )


def _portal_requester_demands(user):
    demand_rows = list(_portal_requester_queryset(user))
    _decorate_portal_demands(demand_rows)
    return demand_rows


def _portal_counts_from_demands(demands):
    pending = sum(1 for row in demands if row.status == PortalDemand.STATUS_PENDING)
    assumed = sum(1 for row in demands if row.status == PortalDemand.STATUS_ASSUMED)
    completed = sum(1 for row in demands if row.status == PortalDemand.STATUS_COMPLETED)
    awaiting_feedback = sum(1 for row in demands if row.status == PortalDemand.STATUS_COMPLETED and not row.has_feedback)
    feedback_values = [row.feedback_rating for row in demands if row.feedback_rating]
    feedback_avg = (sum(feedback_values) / len(feedback_values)) if feedback_values else None
    return {
        "total": len(demands),
        "pending": pending,
        "assumed": assumed,
        "completed": completed,
        "active": pending + assumed,
        "awaiting_feedback": awaiting_feedback,
        "feedback_avg": feedback_avg,
        "feedback_avg_display": f"{feedback_avg:.1f}/5" if feedback_avg else "-",
    }


def _portal_dashboard_context(user):
    base_queryset = PortalDemand.objects.filter(requester=user)
    aggregates = base_queryset.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=PortalDemand.STATUS_PENDING)),
        assumed=Count("id", filter=Q(status=PortalDemand.STATUS_ASSUMED)),
        completed=Count("id", filter=Q(status=PortalDemand.STATUS_COMPLETED)),
        awaiting_feedback=Count("id", filter=Q(status=PortalDemand.STATUS_COMPLETED, feedback_rating__isnull=True)),
        feedback_avg=Avg("feedback_rating"),
    )
    total = aggregates.get("total") or 0
    pending = aggregates.get("pending") or 0
    assumed = aggregates.get("assumed") or 0
    completed = aggregates.get("completed") or 0
    awaiting_feedback = aggregates.get("awaiting_feedback") or 0
    feedback_avg = aggregates.get("feedback_avg")
    feedback_avg_display = f"{feedback_avg:.1f}/5" if feedback_avg else "-"
    last_demand = base_queryset.order_by("-created_at", "-id").only("created_at").first()
    admin_pending = PortalDemand.objects.filter(status=PortalDemand.STATUS_PENDING).count() if _portal_can_manage(user) else None

    dashboard_cards = [
        {
            "label": "Total de demandas",
            "value": total,
            "hint": "Solicitações já registradas no portal",
            "tone": "primary",
        },
        {
            "label": "Em andamento",
            "value": pending + assumed,
            "hint": "Pendentes ou em atendimento",
            "tone": "info",
        },
        {
            "label": "Concluídas",
            "value": completed,
            "hint": "Demandas finalizadas",
            "tone": "success",
        },
        {
            "label": "Feedback médio",
            "value": feedback_avg_display,
            "hint": "Média das avaliações enviadas",
            "tone": "warning",
        },
    ]

    insight_cards = [
        {
            "label": "Aguardando feedback",
            "value": awaiting_feedback,
            "hint": "Demandas concluídas esperando retorno",
        },
        {
            "label": "Pendentes",
            "value": pending,
            "hint": "Aguardando atendente assumir",
        },
        {
            "label": "Última abertura",
            "value": timezone.localtime(last_demand.created_at).strftime("%d/%m/%Y %H:%M") if last_demand else "-",
            "hint": "Data mais recente registrada por você",
        },
    ]

    if admin_pending is not None:
        insight_cards.append(
            {
                "label": "Triagem do portal",
                "value": admin_pending,
                "hint": "Demandas globais pendentes de atendimento",
            }
        )

    return {
        "counts": {
            "total": total,
            "pending": pending,
            "assumed": assumed,
            "completed": completed,
            "active": pending + assumed,
            "awaiting_feedback": awaiting_feedback,
            "feedback_avg_display": feedback_avg_display,
        },
        "dashboard_cards": dashboard_cards,
        "insight_cards": insight_cards,
    }


def _build_portal_queue_note(demand):
    requester_name = _queue_collaborator_display_name(demand.requester)
    created_at = timezone.localtime(demand.created_at).strftime("%d/%m/%Y %H:%M")
    chunks = [
        f"Origem: Portal de chamados ({demand.protocol})",
        f"Solicitante: {requester_name} ({demand.requester.userId})",
        f"Abertura: {created_at}",
        f"Conversa: {demand.get_absolute_url()}",
    ]
    if demand.description:
        chunks.append("Detalhes completos disponíveis no campo de detalhamento da demanda.")
    custom_values = list(
        demand.custom_values.select_related("field").order_by("field__sort_order", "field__id", "id")
    )
    for entry in custom_values:
        if not entry.value:
            continue
        display_value = _portal_display_custom_value(entry.field.field_type, entry.value)
        chunks.append(f"{entry.field.label}: {display_value}")
    return "\n".join(chunks)


def _create_queue_item_from_portal_demand(demand, owner_user):
    task_group = demand.task_group or (demand.task_type.group if demand.task_type else None)
    next_position = (
        userQueue.objects.filter(user_code=owner_user.userId).aggregate(max_pos=models.Max("n_queue_position")).get("max_pos") or 0
    ) + 1
    default_columns = _ensure_user_queue_kanban_columns(owner_user)
    default_column = default_columns[0] if default_columns else None

    queue_item = userQueue.objects.create(
        user_code=owner_user.userId,
        a_ticket=demand.protocol,
        f_conclusion_rate=Decimal("0.00"),
        a_description=demand.title,
        a_demand_detail=demand.description or None,
        a_notes=_build_portal_queue_note(demand),
        priority_level=demand.priority_level or userQueue.PRIORITY_MEDIUM,
        estimated_effort_level=userQueue.ESTIMATE_MEDIUM,
        n_type_group=task_group.id if task_group else None,
        n_type_code=demand.task_type_id or None,
        task_group=task_group,
        task_type=demand.task_type,
        kanban_column=default_column,
        kanban_sort_order=next_position,
        n_queue_position=next_position,
        is_current=False,
    )
    return queue_item


def portalLoginPage(request):
    """Dedicated sign-in page for portal requesters.

    Separate from the internal ConnectMX login: same credentials, but its own
    entry point and always lands the person on the requester portal.
    """
    if request.user.is_authenticated:
        return redirect("portalDemandPage")

    error_message = None
    redirect_to = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME) or ""

    if request.method == "POST":
        identifier = (request.POST.get("login") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=identifier, password=password)
        if user:
            login(request, user)
            if redirect_to and url_has_allowed_host_and_scheme(
                url=redirect_to,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(redirect_to)
            return redirect("portalDemandPage")
        error_message = "Usuário ou senha inválidos."

    return render(
        request,
        "tiqueue/portal_login.html",
        {
            "error_message": error_message,
            "next_url": redirect_to,
            "session_expired": request.GET.get("expired") == "1",
        },
    )


@login_required
def portalDemandPage(request):
    access_denied_message = None
    if request.GET.get("denied") == "1":
        access_denied_message = "Você não possui acesso a esta demanda."
    can_request_portal = _portal_can_open_new_demands(request.user)
    if request.GET.get("requester_denied") == "1" or (not can_request_portal and _portal_requester_access_feature_enabled()):
        access_denied_message = _portal_requester_access_denied_message()

    dashboard_context = _portal_dashboard_context(request.user)
    pending_feedback = _portal_pending_feedback_demands(request.user)

    return render(
        request,
        "tiqueue/portal_demands.html",
        {
            "access_denied_message": access_denied_message,
            "pending_feedback": pending_feedback,
            "can_manage_portal": _portal_can_manage(request.user),
            "can_request_portal": can_request_portal,
            "portal_page_label": "Portal de Chamados",
            "portal_nav_active": "home",
            **dashboard_context,
        },
    )


_PORTAL_PRIORITY_HINTS = {
    userQueue.PRIORITY_LOW: "Consigo trabalhar normalmente. Pode ser tratado na ordem da fila.",
    userQueue.PRIORITY_MEDIUM: "Atrapalha meu trabalho, mas consigo contornar por enquanto.",
    userQueue.PRIORITY_HIGH: "Estou parado ou afeta várias pessoas. Preciso de ajuda imediata.",
}


def _portal_priority_options():
    return [
        {
            "value": value,
            "label": label,
            "color": color,
            "hint": _PORTAL_PRIORITY_HINTS.get(value, ""),
        }
        for value, label, color in userQueue.default_field_options(userQueue.FIELD_PRIORITY)
    ]


@login_required
def portalDemandCreatePage(request):
    if not _portal_can_open_new_demands(request.user):
        return redirect(f"{reverse('portalDemandPage')}?requester_denied=1")

    # Blocks both GET and POST, so the rule cannot be bypassed by posting directly.
    if _portal_pending_feedback_demands(request.user):
        return redirect(f"{reverse('portalMyDemandsPage')}?feedback_required=1")

    form = PortalDemandForm(request.POST or None, request.FILES or None)
    dashboard_context = _portal_dashboard_context(request.user)

    if request.method == "POST" and form.is_valid():
        request_base_url = request.build_absolute_uri("/").rstrip("/")
        with transaction.atomic():
            demand = form.save(commit=False)
            demand.requester = request.user
            demand.status = PortalDemand.STATUS_PENDING
            demand.priority_level = form.cleaned_data.get("priority_level") or userQueue.PRIORITY_MEDIUM
            demand.save()
            form.save_custom_values(demand)
            _create_portal_attachments(
                demand,
                request.FILES.getlist("attachments"),
                uploaded_by=request.user,
            )
            matched_policy = _portal_apply_sla_policy(demand, save=True)
            if matched_policy and matched_policy.auto_assign_on_create and matched_policy.default_attendant_id:
                _assume_portal_demand(demand, matched_policy.default_attendant)
            transaction.on_commit(
                lambda demand_id=demand.id, base_url=request_base_url: _portal_schedule_ai_routing_webhook(
                    demand_id,
                    base_url=base_url,
                )
            )
        return redirect(f"{reverse('portalMyDemandsPage')}?created=1")

    return render(
        request,
        "tiqueue/portal_demand_create.html",
        {
            "form": form,
            "dynamic_fields": form.get_dynamic_fields(),
            "priority_options": _portal_priority_options(),
            "task_groups": list(TaskGroup.objects.order_by("name")),
            "initial_sla_preview": _portal_build_sla_preview(None),
            "can_manage_portal": _portal_can_manage(request.user),
            "can_request_portal": True,
            "portal_page_label": "Nova Demanda",
            "portal_nav_active": "create",
            **dashboard_context,
        },
    )


@login_required
@require_GET
def portalDemandInsightsApi(request):
    title = (request.GET.get("title") or "").strip()
    description = (request.GET.get("description") or "").strip()
    priority_level = (request.GET.get("priority_level") or "").strip() or userQueue.PRIORITY_MEDIUM

    task_group = None
    task_type = None
    group_id = (request.GET.get("task_group") or "").strip()
    type_id = (request.GET.get("task_type") or "").strip()
    if group_id.isdigit():
        task_group = TaskGroup.objects.filter(pk=int(group_id)).first()
    if type_id.isdigit():
        task_type = TaskType.objects.select_related("group").filter(pk=int(type_id)).first()
        if task_type and not task_group:
            task_group = task_type.group

    matched_policy = _portal_match_sla_policy(task_group=task_group, task_type=task_type, priority_level=priority_level)
    payload = {
        "status": "ok",
        "knowledge": _portal_knowledge_suggestions(title, description),
        "duplicates": _portal_duplicate_suggestions(request.user, title, description),
        "sla": _portal_build_sla_preview(matched_policy),
    }
    return JsonResponse(payload)


@csrf_exempt
@require_GET
def portalDemandAiRoutingContextApi(request, demand_id):
    authorized, auth_mode = _portal_ai_request_authorized(request)
    if not authorized:
        return JsonResponse({"status": "error", "message": auth_mode}, status=403)

    demand = get_object_or_404(
        PortalDemand.objects.select_related("requester", "assigned_to", "task_group", "task_type", "sla_policy"),
        pk=demand_id,
    )
    payload = _portal_routing_context_payload(demand)
    payload["auth_mode"] = auth_mode
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def portalDemandAiRoutingApplyApi(request, demand_id):
    authorized, auth_mode = _portal_ai_request_authorized(request)
    if not authorized:
        return JsonResponse({"status": "error", "message": auth_mode}, status=403)

    try:
        if (request.content_type or "").lower().startswith("application/json"):
            payload = json.loads((request.body or b"{}").decode("utf-8") or "{}")
        else:
            payload = request.POST.dict()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"status": "error", "message": "Payload JSON inválido."}, status=400)

    def _clean_int(value):
        raw = str(value or "").strip()
        return int(raw) if raw.isdigit() else None

    def _clean_bool(value, default=True):
        if value is None:
            return default
        return str(value).strip().lower() not in {"0", "false", "no", "off", "nao", "não"}

    priority_options = {value for value, _label, _color in userQueue.default_field_options(userQueue.FIELD_PRIORITY)}
    task_group_id = _clean_int(payload.get("task_group_id"))
    task_type_id = _clean_int(payload.get("task_type_id"))
    priority_level = (str(payload.get("priority_level") or userQueue.PRIORITY_MEDIUM).strip().lower() or userQueue.PRIORITY_MEDIUM)
    confidence_raw = payload.get("confidence")
    reason = (str(payload.get("reason") or "").strip())[:600]
    auto_assign = _clean_bool(payload.get("auto_assign"), default=True)

    if priority_level not in priority_options:
        return JsonResponse({"status": "error", "message": "Prioridade inválida."}, status=400)

    confidence = None
    if confidence_raw not in {None, ""}:
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            return JsonResponse({"status": "error", "message": "Confidence inválido."}, status=400)
        if confidence < 0 or confidence > 1:
            return JsonResponse({"status": "error", "message": "Confidence deve estar entre 0 e 1."}, status=400)

    task_group = TaskGroup.objects.filter(pk=task_group_id).first() if task_group_id else None
    task_type = TaskType.objects.select_related("group").filter(pk=task_type_id).first() if task_type_id else None

    if task_type_id and not task_type:
        return JsonResponse({"status": "error", "message": "Tipo de tarefa inválido."}, status=400)
    if task_group_id and not task_group:
        return JsonResponse({"status": "error", "message": "Grupo de tarefa inválido."}, status=400)
    if task_type and task_group and task_type.group_id != task_group.id:
        return JsonResponse({"status": "error", "message": "O tipo informado não pertence ao grupo informado."}, status=400)
    if task_type and not task_group:
        task_group = task_type.group

    with transaction.atomic():
        demand = get_object_or_404(
            PortalDemand.objects.select_for_update().select_related("requester", "assigned_to", "task_group", "task_type", "sla_policy"),
            pk=demand_id,
        )

        if demand.status in {PortalDemand.STATUS_COMPLETED, PortalDemand.STATUS_CANCELLED}:
            return JsonResponse({"status": "error", "message": "A demanda já está encerrada."}, status=400)

        previous_snapshot = {
            "task_group_id": demand.task_group_id,
            "task_type_id": demand.task_type_id,
            "priority_level": demand.priority_level,
            "assigned_to_id": demand.assigned_to_id,
            "status": demand.status,
        }

        demand.task_group = task_group
        demand.task_type = task_type
        demand.priority_level = priority_level
        demand.save(update_fields=["task_group", "task_type", "priority_level", "updated_at"])

        matched_policy = _portal_apply_sla_policy(demand, save=True)
        auto_assigned = False
        if (
            auto_assign
            and demand.status == PortalDemand.STATUS_PENDING
            and matched_policy
            and matched_policy.auto_assign_on_create
            and matched_policy.default_attendant_id
        ):
            auto_assigned = bool(_assume_portal_demand(demand, matched_policy.default_attendant))
            demand.refresh_from_db()
        else:
            demand.refresh_from_db()

        applied_snapshot = {
            "task_group_id": demand.task_group_id,
            "task_type_id": demand.task_type_id,
            "priority_level": demand.priority_level,
            "assigned_to_id": demand.assigned_to_id,
            "status": demand.status,
            "sla_policy_id": demand.sla_policy_id,
        }
        _portal_log_event(
            demand,
            PortalDemandLog.EVENT_AI_ROUTED,
            actor_name="Agente IA (N8N)",
            summary="Roteamento automático aplicado",
            details=json.dumps(
                {
                    "confidence": confidence,
                    "reason": reason,
                    "auth_mode": auth_mode,
                    "auto_assign": auto_assign,
                    "previous": previous_snapshot,
                    "applied": applied_snapshot,
                },
                ensure_ascii=False,
            ),
            to_attendant=demand.assigned_to if demand.assigned_to_id else None,
        )

    _sync_portal_critical_notifications()
    response_payload = {
        "status": "ok",
        "message": "Roteamento aplicado com sucesso.",
        "demand_id": demand.id,
        "protocol": demand.protocol,
        "task_group_id": demand.task_group_id,
        "task_type_id": demand.task_type_id,
        "priority_level": demand.priority_level,
        "confidence": confidence,
        "reason": reason,
        "auto_assigned": auto_assigned,
        "assigned_to": {
            "id": demand.assigned_to_id,
            "name": _queue_collaborator_display_name(demand.assigned_to) if demand.assigned_to_id else "",
            "user_code": getattr(demand.assigned_to, "userId", "") if demand.assigned_to_id else "",
        },
        "sla": _portal_build_sla_preview(demand.sla_policy),
        "detail_url": demand.get_absolute_url(),
    }
    return JsonResponse(response_payload)


@login_required
def portalMyDemandsPage(request):
    success_message = None
    access_denied_message = None

    if request.GET.get("created") == "1":
        success_message = "Demanda enviada com sucesso. Ela já está na fila pendente para atendimento."
    elif request.GET.get("denied") == "1":
        access_denied_message = "Você não possui acesso a esta demanda."

    my_demands = _portal_requester_demands(request.user)
    counts = _portal_counts_from_demands(my_demands)
    pending_feedback = _portal_pending_feedback_demands(request.user)
    feedback_required = request.GET.get("feedback_required") == "1"

    return render(
        request,
        "tiqueue/portal_my_demands.html",
        {
            "my_demands": my_demands,
            "counts": counts,
            "pending_feedback": pending_feedback,
            "feedback_required": feedback_required,
            "success_message": success_message,
            "access_denied_message": access_denied_message,
            "can_manage_portal": _portal_can_manage(request.user),
            "can_request_portal": _portal_can_open_new_demands(request.user),
            "portal_page_label": "Minhas Demandas",
            "portal_nav_active": "my-demands",
        },
    )


@login_required
def portalDemandDetailPage(request, demand_id=None, demand_code=None):
    demand_filter = {"access_code": str(demand_code or "").strip().upper()} if demand_code else {"pk": demand_id}
    demand = get_object_or_404(_portal_detail_queryset(), **demand_filter)

    if not _portal_can_access_demand(request.user, demand):
        return redirect(f"{reverse('portalMyDemandsPage')}?denied=1")

    _portal_filter_private_activity_for_user(request.user, demand)

    reply_form = PortalDemandReplyForm(demand=demand, user=request.user)
    feedback_form = PortalDemandFeedbackForm(instance=demand)
    transfer_form = PortalDemandTransferForm(demand=demand)
    reply_success = request.GET.get("replied") == "1"
    feedback_success = request.GET.get("feedback") == "1"
    transfer_success = request.GET.get("transferred") == "1"
    workflow_state = (request.GET.get("workflow") or "").strip().lower()
    workflow_error_message = None

    if request.method == "POST":
        form_type = (request.POST.get("form_type") or "reply").strip().lower()
        if form_type == "feedback":
            can_submit_feedback = _portal_can_leave_feedback(request.user, demand)
            feedback_form = PortalDemandFeedbackForm(request.POST or None, instance=demand)
            if feedback_form.is_valid():
                if not can_submit_feedback:
                    feedback_form.add_error(None, "Somente o solicitante pode avaliar a demanda concluída.")
                else:
                    feedback = feedback_form.save(commit=False)
                    feedback.feedback_submitted_at = timezone.now()
                    feedback.save(update_fields=["feedback_rating", "feedback_comment", "feedback_submitted_at", "updated_at"])
                    return redirect(f"{demand.get_absolute_url()}?feedback=1#feedback")
        elif form_type == "transfer":
            transfer_form = PortalDemandTransferForm(request.POST or None, demand=demand)
            if not _portal_can_manage(request.user):
                transfer_form.add_error(None, "Somente atendentes administradores podem transferir demandas.")
            elif transfer_form.is_valid():
                with transaction.atomic():
                    locked_demand = get_object_or_404(
                        PortalDemand.objects.select_for_update().select_related("assigned_to", "linked_queue_item"),
                        pk=demand.pk,
                    )
                    transferred, transfer_error = _transfer_portal_demand(
                        locked_demand,
                        transfer_form.cleaned_data["target_attendant"],
                        request.user,
                    )
                if transferred:
                    return redirect(f"{locked_demand.get_absolute_url()}?transferred=1#management")
                transfer_form.add_error(None, transfer_error or "Nao foi possivel transferir a demanda.")
        elif form_type == "workflow":
            if not _portal_can_manage(request.user):
                workflow_error_message = "Somente atendentes administradores podem alterar o ciclo de vida da demanda."
            else:
                workflow_action = (request.POST.get("workflow_action") or "").strip().lower()
                with transaction.atomic():
                    locked_demand = get_object_or_404(
                        PortalDemand.objects.select_for_update().select_related("assigned_to", "linked_queue_item"),
                        pk=demand.pk,
                    )
                    if workflow_action == "complete":
                        changed, workflow_error_message = _complete_portal_demand(locked_demand, request.user)
                        if changed:
                            return redirect(f"{locked_demand.get_absolute_url()}?workflow=completed#conversation")
                    elif workflow_action == "cancel":
                        changed, workflow_error_message = _cancel_portal_demand(locked_demand, request.user)
                        if changed:
                            return redirect(f"{locked_demand.get_absolute_url()}?workflow=cancelled#conversation")
                    else:
                        workflow_error_message = "Acao de workflow invalida."
        else:
            reply_form = PortalDemandReplyForm(request.POST or None, request.FILES or None, demand=demand, user=request.user)
            if reply_form.is_valid():
                if not _portal_can_reply_to_demand(request.user, demand):
                    reply_form.add_error(None, "Esta demanda não aceita novas respostas no status atual.")
                else:
                    if (
                        (reply_form.cleaned_data.get("work_started_at") or reply_form.cleaned_data.get("work_ended_at"))
                        and not _portal_can_manage(request.user)
                    ):
                        reply_form.add_error(None, "Somente atendentes podem registrar apontamentos de tempo.")
                    else:
                        with transaction.atomic():
                            locked_demand = get_object_or_404(
                                PortalDemand.objects.select_for_update().select_related("assigned_to", "requester"),
                                pk=demand.pk,
                            )
                            message = PortalDemandMessage.objects.create(
                                demand=locked_demand,
                                author=request.user,
                                author_name=_queue_collaborator_display_name(request.user),
                                author_role=_portal_actor_role(request.user, locked_demand),
                                canned_response=reply_form.cleaned_data.get("canned_response"),
                                is_internal=bool(reply_form.cleaned_data.get("is_internal") and _portal_can_manage(request.user)),
                                message=reply_form.cleaned_data.get("message") or None,
                                work_started_at=reply_form.cleaned_data.get("work_started_at"),
                                work_ended_at=reply_form.cleaned_data.get("work_ended_at"),
                            )
                            if (
                                message.author_role == PortalDemandMessage.ROLE_ATTENDANT
                                and not message.is_internal
                                and not locked_demand.first_response_at
                            ):
                                locked_demand.first_response_at = timezone.now()
                                locked_demand.save(update_fields=["first_response_at", "updated_at"])
                            _create_portal_attachments(
                                locked_demand,
                                request.FILES.getlist("attachments"),
                                uploaded_by=request.user,
                                message=message,
                            )
                            if message.has_worklog:
                                _portal_log_event(
                                    locked_demand,
                                    PortalDemandLog.EVENT_WORKLOG,
                                    actor=request.user,
                                    summary=f"Tempo apontado: {message.worked_time_display}.",
                                    details=(
                                        f"Inicio: {timezone.localtime(message.work_started_at).strftime('%d/%m/%Y %H:%M')} | "
                                        f"Fim: {timezone.localtime(message.work_ended_at).strftime('%d/%m/%Y %H:%M')}"
                                    ),
                                    to_attendant=locked_demand.assigned_to if locked_demand.assigned_to_id else None,
                                    related_message=message,
                                )
                        return redirect(f"{demand.get_absolute_url()}?replied=1#conversation")

    _decorate_portal_demands([demand])
    _decorate_portal_messages(demand)
    _decorate_portal_logs(demand)
    if _portal_can_manage(request.user):
        _sync_portal_critical_notifications()
    reply_canned_responses = list(reply_form.fields["canned_response"].queryset) if "canned_response" in reply_form.fields else []
    quick_canned_responses = _portal_ranked_canned_suggestions(demand, reply_canned_responses, limit=5) if reply_canned_responses else []

    return render(
        request,
        "tiqueue/portal_demand_detail.html",
        {
            "demand": demand,
            "reply_form": reply_form,
            "feedback_form": feedback_form,
            "transfer_form": transfer_form,
            "reply_success": reply_success,
            "feedback_success": feedback_success,
            "transfer_success": transfer_success,
            "workflow_state": workflow_state,
            "workflow_error_message": workflow_error_message,
            "can_manage_portal": _portal_can_manage(request.user),
            "can_request_portal": _portal_can_open_new_demands(request.user),
            "can_reply": _portal_can_reply_to_demand(request.user, demand),
            "can_leave_feedback": _portal_can_leave_feedback(request.user, demand),
            "can_assume_here": _portal_can_manage(request.user) and demand.status == PortalDemand.STATUS_PENDING,
            "can_transfer_here": _portal_can_manage(request.user) and demand.status not in {PortalDemand.STATUS_COMPLETED, PortalDemand.STATUS_CANCELLED},
            "can_manage_workflow": _portal_can_manage(request.user) and demand.status in {PortalDemand.STATUS_PENDING, PortalDemand.STATUS_ASSUMED},
            "reply_canned_responses": reply_canned_responses,
            "quick_canned_responses": quick_canned_responses,
            "reply_canned_response_value": reply_form["canned_response"].value() if "canned_response" in reply_form.fields else "",
            "portal_page_label": "Conversa da Demanda",
            "portal_nav_active": "my-demands",
        },
    )


@login_required
def portalDemandCodeDetailPage(request, demand_code):
    return portalDemandDetailPage(request, demand_code=demand_code)


@login_required
def portalPendingDemandsPage(request):
    can_manage = _portal_can_manage(request.user)
    access_denied_message = None if can_manage else "Você não possui acesso a este módulo."
    field_created_flag = False
    option_created_flag = False
    sla_created_flag = False
    canned_created_flag = False

    pending_demands = []
    portal_custom_fields = []
    portal_custom_field_rows = []
    custom_field_form = PortalDemandCustomFieldCreateForm(prefix="portal_field")
    custom_option_forms = {}
    sla_form = PortalDemandSlaPolicyForm(prefix="portal_sla")
    canned_response_form = PortalCannedResponseForm(prefix="portal_canned")
    sla_policies = []
    canned_responses = []

    if request.method == "POST" and can_manage:
        form_type = (request.POST.get("form_type") or "").strip()
        if form_type == "create_custom_field":
            custom_field_form = PortalDemandCustomFieldCreateForm(request.POST, prefix="portal_field")
            if custom_field_form.is_valid():
                next_sort = (
                    PortalDemandCustomField.objects.aggregate(max_sort=models.Max("sort_order")).get("max_sort") or 0
                ) + 1
                definition = PortalDemandCustomField.objects.create(
                    label=custom_field_form.cleaned_data["label"],
                    field_type=custom_field_form.cleaned_data["field_type"],
                    placeholder=custom_field_form.cleaned_data["placeholder"] or None,
                    help_text=custom_field_form.cleaned_data["help_text"] or None,
                    is_required=custom_field_form.cleaned_data["is_required"],
                    sort_order=next_sort,
                    created_by=request.user,
                )
                definition.task_groups.set(custom_field_form.cleaned_data["task_groups"])
                definition.task_types.set(custom_field_form.cleaned_data["task_types"])
                if definition.field_type == PortalDemandCustomField.FIELD_SELECT:
                    option_label = custom_field_form.cleaned_data["initial_option_label"]
                    PortalDemandCustomFieldOption.objects.create(
                        field=definition,
                        value=_portal_field_option_value(definition, option_label),
                        label=option_label,
                        sort_order=1,
                    )
                return redirect(f"{reverse('portalPendingDemandsPage')}?field_created=1")
        elif form_type == "create_custom_option":
            field_id = (request.POST.get("field_id") or "").strip()
            target_field = get_object_or_404(
                PortalDemandCustomField.objects.filter(is_active=True),
                pk=field_id,
                field_type=PortalDemandCustomField.FIELD_SELECT,
            )
            option_form = PortalDemandCustomFieldOptionForm(request.POST, prefix=f"portal_option_{target_field.id}")
            custom_option_forms[target_field.id] = option_form
            if option_form.is_valid():
                next_sort = (
                    PortalDemandCustomFieldOption.objects.filter(field=target_field).aggregate(max_sort=models.Max("sort_order")).get("max_sort")
                    or 0
                ) + 1
                option_label = option_form.cleaned_data["label"]
                PortalDemandCustomFieldOption.objects.create(
                    field=target_field,
                    value=_portal_field_option_value(target_field, option_label),
                    label=option_label,
                    sort_order=next_sort,
                )
                return redirect(f"{reverse('portalPendingDemandsPage')}?option_created=1")
        elif form_type == "create_sla_policy":
            sla_form = PortalDemandSlaPolicyForm(request.POST, prefix="portal_sla")
            if sla_form.is_valid():
                next_sort = (PortalDemandSlaPolicy.objects.aggregate(max_sort=models.Max("sort_order")).get("max_sort") or 0) + 1
                policy = sla_form.save(commit=False)
                policy.sort_order = next_sort
                policy.save()
                return redirect(f"{reverse('portalPendingDemandsPage')}?sla_created=1")
        elif form_type == "create_canned_response":
            canned_response_form = PortalCannedResponseForm(request.POST, prefix="portal_canned")
            if canned_response_form.is_valid():
                next_sort = (PortalCannedResponse.objects.aggregate(max_sort=models.Max("sort_order")).get("max_sort") or 0) + 1
                canned = canned_response_form.save(commit=False)
                canned.sort_order = next_sort
                canned.created_by = request.user
                canned.save()
                return redirect(f"{reverse('portalPendingDemandsPage')}?canned_created=1")

    if can_manage:
        pending_demands = list(
            PortalDemand.objects.filter(status=PortalDemand.STATUS_PENDING)
            .select_related("requester", "task_group", "task_type", "sla_policy", "sla_policy__default_attendant")
            .prefetch_related(
                Prefetch(
                    "custom_values",
                    queryset=PortalDemandCustomValue.objects.select_related("field").order_by("field__sort_order", "field__id", "id"),
                    to_attr="prefetched_custom_values",
                )
            )
            .order_by("created_at", "id")
        )
        _decorate_portal_demands(pending_demands)
        portal_custom_fields = _portal_custom_fields()
        for definition in portal_custom_fields:
            if definition.id not in custom_option_forms:
                custom_option_forms[definition.id] = PortalDemandCustomFieldOptionForm(prefix=f"portal_option_{definition.id}")
            portal_custom_field_rows.append(
                {
                    "field": definition,
                    "option_form": custom_option_forms[definition.id],
                }
            )
        sla_policies = list(
            PortalDemandSlaPolicy.objects.select_related("task_group", "task_type", "default_attendant").order_by("sort_order", "id")
        )
        priority_map = userQueue.default_field_option_map(userQueue.FIELD_PRIORITY)
        for policy in sla_policies:
            policy.priority_display = (
                priority_map.get(policy.priority_level or "", {}).get("label")
                if policy.priority_level
                else "Todas as prioridades"
            )
            policy.default_attendant_display = (
                _queue_collaborator_display_name(policy.default_attendant) if policy.default_attendant_id else "-"
            )
        canned_responses = list(
            PortalCannedResponse.objects.select_related("task_group", "task_type", "created_by").order_by("sort_order", "title", "id")
        )
        field_created_flag = request.GET.get("field_created") == "1"
        option_created_flag = request.GET.get("option_created") == "1"
        sla_created_flag = request.GET.get("sla_created") == "1"
        canned_created_flag = request.GET.get("canned_created") == "1"

    summary = {
        "pending": PortalDemand.objects.filter(status=PortalDemand.STATUS_PENDING).count() if can_manage else 0,
        "custom_fields": len(portal_custom_fields),
        "sla_policies": len(sla_policies),
        "canned_responses": len(canned_responses),
    }

    return render(
        request,
        "tiqueue/portal_pending_demands.html",
        {
            "can_manage": can_manage,
            "access_denied_message": access_denied_message,
            "pending_demands": pending_demands,
            "summary": summary,
            "assumed_flag": request.GET.get("assumed") == "1",
            "bulk_assumed_flag": request.GET.get("bulk_assumed") == "1",
            "field_created_flag": field_created_flag,
            "option_created_flag": option_created_flag,
            "sla_created_flag": sla_created_flag,
            "canned_created_flag": canned_created_flag,
            "portal_custom_fields": portal_custom_fields,
            "portal_custom_field_rows": portal_custom_field_rows,
            "custom_field_form": custom_field_form,
            "custom_option_forms": custom_option_forms,
            "sla_form": sla_form,
            "canned_response_form": canned_response_form,
            "sla_policies": sla_policies,
            "canned_responses": canned_responses,
        },
    )


def _portal_admin_base_context(request, active_key, page_title):
    can_manage = _portal_can_manage(request.user)
    return {
        "can_manage": can_manage,
        "access_denied_message": None if can_manage else "Você não possui acesso a este módulo.",
        "summary": {
            "pending": PortalDemand.objects.filter(status=PortalDemand.STATUS_PENDING).count() if can_manage else 0,
            "custom_fields": PortalDemandCustomField.objects.filter(is_active=True).count() if can_manage else 0,
            "sla_policies": PortalDemandSlaPolicy.objects.filter(is_active=True).count() if can_manage else 0,
            "canned_responses": PortalCannedResponse.objects.filter(is_active=True).count() if can_manage else 0,
        },
        "portal_admin_overview": _portal_admin_operational_overview() if can_manage else {},
        "portal_admin_nav_active": active_key,
        "portal_admin_page_title": page_title,
    }


def _portal_requester_admin_summary():
    return {
        "sectors": PortalRequesterSector.objects.count(),
        "active_sectors": PortalRequesterSector.objects.filter(is_active=True).count(),
        "collaborators": PortalRequesterCollaborator.objects.count(),
        "active_collaborators": PortalRequesterCollaborator.objects.filter(is_active=True, sector__is_active=True).count(),
        "accounts": PortalRequesterAccount.objects.count(),
        "active_accounts": PortalRequesterAccount.objects.filter(
            is_active=True,
            collaborator__is_active=True,
            collaborator__sector__is_active=True,
        ).count(),
    }


@login_required
def portalRequesterAdminPage(request):
    context = _portal_admin_base_context(request, "requesters", "Setores, colaboradores e acessos do portal")
    summary = _portal_requester_admin_summary() if context["can_manage"] else {
        "sectors": 0,
        "active_sectors": 0,
        "collaborators": 0,
        "active_collaborators": 0,
        "accounts": 0,
        "active_accounts": 0,
    }

    editing_sector = None
    editing_collaborator = None
    editing_account = None

    edit_sector_id = (request.GET.get("edit_sector") or "").strip()
    edit_collaborator_id = (request.GET.get("edit_collaborator") or "").strip()
    edit_account_id = (request.GET.get("edit_account") or "").strip()

    if context["can_manage"] and edit_sector_id.isdigit():
        editing_sector = PortalRequesterSector.objects.filter(pk=int(edit_sector_id)).first()
    if context["can_manage"] and edit_collaborator_id.isdigit():
        editing_collaborator = PortalRequesterCollaborator.objects.select_related("sector").filter(pk=int(edit_collaborator_id)).first()
    if context["can_manage"] and edit_account_id.isdigit():
        editing_account = PortalRequesterAccount.objects.select_related("collaborator", "collaborator__sector", "user").filter(
            pk=int(edit_account_id)
        ).first()

    sector_form = PortalRequesterSectorForm(instance=editing_sector, prefix="portal_sector")
    collaborator_form = PortalRequesterCollaboratorForm(instance=editing_collaborator, prefix="portal_collaborator")
    account_form = PortalRequesterAccountForm(account=editing_account, prefix="portal_account")

    if request.method == "POST" and context["can_manage"]:
        form_type = (request.POST.get("form_type") or "").strip().lower()

        if form_type == "create_sector":
            sector_form = PortalRequesterSectorForm(request.POST, prefix="portal_sector")
            if sector_form.is_valid():
                sector_form.save()
                return redirect(f"{reverse('portalRequesterAdminPage')}?sector_created=1")

        elif form_type == "update_sector":
            sector_id = (request.POST.get("sector_id") or "").strip()
            editing_sector = PortalRequesterSector.objects.filter(pk=sector_id).first() if sector_id.isdigit() else None
            sector_form = PortalRequesterSectorForm(request.POST, instance=editing_sector, prefix="portal_sector")
            if editing_sector and sector_form.is_valid():
                sector = sector_form.save()
                _sync_portal_requester_collaborator_accounts(list(sector.collaborators.select_related("sector")))
                return redirect(f"{reverse('portalRequesterAdminPage')}?sector_updated=1")

        elif form_type == "toggle_sector":
            sector_id = (request.POST.get("sector_id") or "").strip()
            sector = PortalRequesterSector.objects.filter(pk=sector_id).first() if sector_id.isdigit() else None
            if sector:
                sector.is_active = not sector.is_active
                sector.save(update_fields=["is_active", "updated_at"])
                _sync_portal_requester_collaborator_accounts(list(sector.collaborators.select_related("sector")))
                return redirect(f"{reverse('portalRequesterAdminPage')}?sector_toggled=1")

        elif form_type == "create_collaborator":
            collaborator_form = PortalRequesterCollaboratorForm(request.POST, prefix="portal_collaborator")
            if collaborator_form.is_valid():
                collaborator_form.save()
                return redirect(f"{reverse('portalRequesterAdminPage')}?collaborator_created=1")

        elif form_type == "update_collaborator":
            collaborator_id = (request.POST.get("collaborator_id") or "").strip()
            editing_collaborator = (
                PortalRequesterCollaborator.objects.select_related("sector").filter(pk=collaborator_id).first()
                if collaborator_id.isdigit()
                else None
            )
            collaborator_form = PortalRequesterCollaboratorForm(
                request.POST,
                instance=editing_collaborator,
                prefix="portal_collaborator",
            )
            if editing_collaborator and collaborator_form.is_valid():
                collaborator = collaborator_form.save(commit=False)
                linked_account = PortalRequesterAccount.objects.select_related("user").filter(collaborator=editing_collaborator).first()
                sync_errors = _portal_requester_collaborator_sync_errors(
                    collaborator,
                    linked_user=linked_account.user if linked_account else None,
                )
                for field_name, message in sync_errors.items():
                    collaborator_form.add_error(field_name, message)
                if not collaborator_form.errors:
                    collaborator.save()
                    if linked_account:
                        linked_account.refresh_from_db()
                        _sync_portal_requester_account_user(linked_account)
                    return redirect(f"{reverse('portalRequesterAdminPage')}?collaborator_updated=1")

        elif form_type == "toggle_collaborator":
            collaborator_id = (request.POST.get("collaborator_id") or "").strip()
            collaborator = (
                PortalRequesterCollaborator.objects.select_related("sector").filter(pk=collaborator_id).first()
                if collaborator_id.isdigit()
                else None
            )
            if collaborator:
                collaborator.is_active = not collaborator.is_active
                collaborator.save(update_fields=["is_active", "updated_at"])
                _sync_portal_requester_collaborator_accounts([collaborator])
                return redirect(f"{reverse('portalRequesterAdminPage')}?collaborator_toggled=1")

        elif form_type == "create_account":
            account_form = PortalRequesterAccountForm(request.POST, prefix="portal_account")
            if account_form.is_valid():
                collaborator = account_form.cleaned_data["collaborator"]
                username = account_form.cleaned_data["username"]
                password = account_form.cleaned_data["password"]
                is_active = account_form.cleaned_data.get("is_active", False)
                sync_errors = _portal_requester_collaborator_sync_errors(collaborator)
                if User.objects.filter(username=username).exists():
                    account_form.add_error("username", "Já existe um usuário com este login.")
                for _field_name, message in sync_errors.items():
                    account_form.add_error("collaborator", message)
                if not account_form.errors:
                    user = User.objects.create_user(
                        userId=collaborator.registration_code,
                        username=username,
                        email=collaborator.email,
                        nameUser=collaborator.full_name,
                        password=password,
                        is_active=bool(is_active and collaborator.is_active and collaborator.sector.is_active),
                    )
                    account = PortalRequesterAccount.objects.create(
                        collaborator=collaborator,
                        user=user,
                        is_active=is_active,
                        created_by=request.user,
                    )
                    _sync_portal_requester_account_user(account, username=username)
                    return redirect(f"{reverse('portalRequesterAdminPage')}?account_created=1")

        elif form_type == "update_account":
            account_id = (request.POST.get("account_id") or "").strip()
            editing_account = (
                PortalRequesterAccount.objects.select_related("collaborator", "collaborator__sector", "user").filter(pk=account_id).first()
                if account_id.isdigit()
                else None
            )
            account_form = PortalRequesterAccountForm(request.POST, account=editing_account, prefix="portal_account")
            if editing_account and account_form.is_valid():
                collaborator = account_form.cleaned_data["collaborator"]
                username = account_form.cleaned_data["username"]
                password = account_form.cleaned_data["password"]
                is_active = account_form.cleaned_data.get("is_active", False)
                sync_errors = _portal_requester_collaborator_sync_errors(collaborator, linked_user=editing_account.user)
                if User.objects.exclude(pk=editing_account.user_id).filter(username=username).exists():
                    account_form.add_error("username", "Já existe um usuário com este login.")
                for _field_name, message in sync_errors.items():
                    account_form.add_error("collaborator", message)
                if not account_form.errors:
                    editing_account.collaborator = collaborator
                    editing_account.is_active = is_active
                    editing_account.save(update_fields=["collaborator", "is_active", "updated_at"])
                    _sync_portal_requester_account_user(editing_account, password=password, username=username)
                    return redirect(f"{reverse('portalRequesterAdminPage')}?account_updated=1")

        elif form_type == "toggle_account":
            account_id = (request.POST.get("account_id") or "").strip()
            account = (
                PortalRequesterAccount.objects.select_related("collaborator", "collaborator__sector", "user").filter(pk=account_id).first()
                if account_id.isdigit()
                else None
            )
            if account:
                account.is_active = not account.is_active
                account.save(update_fields=["is_active", "updated_at"])
                _sync_portal_requester_account_user(account)
                return redirect(f"{reverse('portalRequesterAdminPage')}?account_toggled=1")

    sectors = list(
        PortalRequesterSector.objects.annotate(
            collaborator_total=Count("collaborators", distinct=True),
            active_collaborator_total=Count("collaborators", filter=Q(collaborators__is_active=True), distinct=True),
        ).order_by("name", "id")
    ) if context["can_manage"] else []
    collaborators = list(
        PortalRequesterCollaborator.objects.select_related("sector").order_by("full_name", "id")
    ) if context["can_manage"] else []
    accounts = list(
        PortalRequesterAccount.objects.select_related("collaborator", "collaborator__sector", "user").order_by(
            "collaborator__full_name",
            "id",
        )
    ) if context["can_manage"] else []

    account_map = {account.collaborator_id: account for account in accounts}
    for collaborator in collaborators:
        collaborator.account = account_map.get(collaborator.id)
        collaborator.access_enabled = bool(
            collaborator.account
            and collaborator.account.is_active
            and collaborator.is_active
            and collaborator.sector.is_active
        )

    return render(
        request,
        "tiqueue/portal_requesters.html",
        {
            **context,
            "summary": summary,
            "sector_form": sector_form,
            "collaborator_form": collaborator_form,
            "account_form": account_form,
            "editing_sector": editing_sector,
            "editing_collaborator": editing_collaborator,
            "editing_account": editing_account,
            "sectors": sectors,
            "collaborators": collaborators,
            "accounts": accounts,
            "sector_created_flag": request.GET.get("sector_created") == "1",
            "sector_updated_flag": request.GET.get("sector_updated") == "1",
            "sector_toggled_flag": request.GET.get("sector_toggled") == "1",
            "collaborator_created_flag": request.GET.get("collaborator_created") == "1",
            "collaborator_updated_flag": request.GET.get("collaborator_updated") == "1",
            "collaborator_toggled_flag": request.GET.get("collaborator_toggled") == "1",
            "account_created_flag": request.GET.get("account_created") == "1",
            "account_updated_flag": request.GET.get("account_updated") == "1",
            "account_toggled_flag": request.GET.get("account_toggled") == "1",
        },
    )


def _portal_ticket_list_context(request):
    valid_views = {"abertos", "meus", "nao_atribuidos", "resolvidos", "todos"}
    view = (request.GET.get("view") or "abertos").strip()
    if view not in valid_views:
        view = "abertos"

    base_qs = PortalDemand.objects.select_related(
        "requester", "assigned_to", "task_group", "task_type", "sla_policy", "sla_policy__default_attendant"
    )

    if view == "meus":
        qs = base_qs.filter(assigned_to=request.user)
    elif view == "nao_atribuidos":
        qs = base_qs.filter(status=PortalDemand.STATUS_PENDING)
    elif view == "resolvidos":
        qs = base_qs.filter(status__in=[PortalDemand.STATUS_COMPLETED, PortalDemand.STATUS_CANCELLED])
    elif view == "todos":
        qs = base_qs.all()
    else:
        qs = base_qs.filter(status__in=[PortalDemand.STATUS_PENDING, PortalDemand.STATUS_ASSUMED])

    priority = (request.GET.get("priority") or "").strip()
    if priority:
        qs = qs.filter(priority_level=priority)

    group_id = (request.GET.get("group") or "").strip()
    if group_id.isdigit():
        qs = qs.filter(task_group_id=int(group_id))

    search = (request.GET.get("q") or "").strip()
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(access_code__icontains=search) | Q(requester__nameUser__icontains=search))

    sla_filter = (request.GET.get("sla") or "").strip()
    if sla_filter in {"vencido", "hoje"}:
        now = timezone.now()
        if sla_filter == "vencido":
            qs = qs.filter(
                Q(first_response_at__isnull=True, first_response_due_at__lt=now)
                | Q(completed_at__isnull=True, resolution_due_at__lt=now)
            )
        else:
            today = timezone.localdate()
            qs = qs.filter(
                Q(first_response_at__isnull=True, first_response_due_at__date=today)
                | Q(completed_at__isnull=True, resolution_due_at__date=today)
            )
    else:
        sla_filter = ""

    qs = qs.order_by("-created_at", "-id")

    paginator = Paginator(qs, 30)
    page_obj = paginator.get_page(request.GET.get("page") or "1")
    tickets = list(page_obj.object_list)
    _decorate_portal_demands(tickets)

    view_counts = {
        "abertos": base_qs.filter(status__in=[PortalDemand.STATUS_PENDING, PortalDemand.STATUS_ASSUMED]).count(),
        "meus": base_qs.filter(assigned_to=request.user).count(),
        "nao_atribuidos": base_qs.filter(status=PortalDemand.STATUS_PENDING).count(),
        "resolvidos": base_qs.filter(status__in=[PortalDemand.STATUS_COMPLETED, PortalDemand.STATUS_CANCELLED]).count(),
        "todos": base_qs.count(),
    }

    querystring_params = request.GET.copy()
    querystring_params.pop("page", None)
    base_querystring = querystring_params.urlencode()

    view_labels = {
        "abertos": "abertos",
        "meus": "atribuídos a você",
        "nao_atribuidos": "não atribuídos",
        "resolvidos": "resolvidos",
        "todos": "no total",
    }

    return {
        "tickets": tickets,
        "page_obj": page_obj,
        "current_view": view,
        "current_view_label": view_labels.get(view, ""),
        "view_counts": view_counts,
        "priority_filter": priority,
        "group_filter": group_id,
        "search_query": search,
        "sla_filter": sla_filter,
        "groups": list(TaskGroup.objects.order_by("name")),
        "base_querystring": base_querystring,
    }


def _portal_format_minutes(total_minutes):
    minutes = int(round(total_minutes or 0))
    if minutes < 60:
        return f"{minutes}min"
    hours = minutes // 60
    remaining = minutes % 60
    if hours < 24:
        return f"{hours}h {remaining:02d}min" if remaining else f"{hours}h"
    days = hours // 24
    leftover_hours = hours % 24
    return f"{days}d {leftover_hours}h" if leftover_hours else f"{days}d"


def _portal_ticket_header_stats():
    now = timezone.now()
    today = timezone.localdate()
    open_qs = PortalDemand.objects.filter(status__in=[PortalDemand.STATUS_PENDING, PortalDemand.STATUS_ASSUMED])

    breached = open_qs.filter(
        Q(first_response_at__isnull=True, first_response_due_at__lt=now)
        | Q(completed_at__isnull=True, resolution_due_at__lt=now)
    ).count()

    due_today = open_qs.filter(
        Q(first_response_at__isnull=True, first_response_due_at__date=today)
        | Q(completed_at__isnull=True, resolution_due_at__date=today)
    ).count()

    # Average first response over the last 30 days. Computed in Python over a
    # bounded slice so it stays portable across database backends (duration
    # aggregates behave inconsistently on SQLite).
    since = now - timedelta(days=30)
    response_pairs = list(
        PortalDemand.objects.filter(first_response_at__isnull=False, created_at__gte=since)
        .order_by("-first_response_at")
        .values_list("created_at", "first_response_at")[:500]
    )
    deltas = [
        (responded_at - created_at).total_seconds() / 60
        for created_at, responded_at in response_pairs
        if created_at and responded_at and responded_at >= created_at
    ]
    avg_first_response = _portal_format_minutes(sum(deltas) / len(deltas)) if deltas else "—"

    csat = PortalDemand.objects.filter(feedback_rating__isnull=False).aggregate(
        average=Avg("feedback_rating"), total=Count("id")
    )
    csat_total = csat.get("total") or 0
    csat_average = csat.get("average")

    return {
        "breached": breached,
        "due_today": due_today,
        "unassigned": open_qs.filter(status=PortalDemand.STATUS_PENDING).count(),
        "avg_first_response": avg_first_response,
        "csat_average": round(csat_average, 1) if csat_average else None,
        "csat_total": csat_total,
    }


def _portal_custom_field_rows(custom_option_forms=None):
    custom_option_forms = custom_option_forms or {}
    portal_custom_fields = _portal_custom_fields()
    portal_custom_field_rows = []
    for definition in portal_custom_fields:
        if definition.id not in custom_option_forms:
            custom_option_forms[definition.id] = PortalDemandCustomFieldOptionForm(prefix=f"portal_option_{definition.id}")
        portal_custom_field_rows.append(
            {
                "field": definition,
                "option_form": custom_option_forms[definition.id],
            }
        )
    return portal_custom_fields, portal_custom_field_rows, custom_option_forms


def _portal_sla_policies():
    sla_policies = list(
        PortalDemandSlaPolicy.objects.select_related("task_group", "task_type", "default_attendant").order_by("sort_order", "id")
    )
    priority_map = userQueue.default_field_option_map(userQueue.FIELD_PRIORITY)
    for policy in sla_policies:
        policy.priority_display = priority_map.get(policy.priority_level or "", {}).get("label") if policy.priority_level else "Todas as prioridades"
        policy.default_attendant_display = _queue_collaborator_display_name(policy.default_attendant) if policy.default_attendant_id else "-"
    return sla_policies


def _portal_canned_responses():
    return list(
        PortalCannedResponse.objects.select_related("task_group", "task_type", "created_by").order_by("sort_order", "title", "id")
    )


def _portal_refresh_open_demands_sla():
    open_demands = (
        PortalDemand.objects.filter(status__in=[PortalDemand.STATUS_PENDING, PortalDemand.STATUS_ASSUMED])
        .select_related("task_group", "task_type", "sla_policy")
        .order_by("id")
    )
    for demand in open_demands:
        _portal_apply_sla_policy(demand, save=True)


def _upsert_system_notification(source_key, title, message, level):
    notification = SystemNotification.objects.filter(source_key=source_key).first()
    if notification:
        notification.title = title
        notification.message = message
        notification.level = level
        notification.is_active = True
        notification.resolved_at = None
        notification.save(update_fields=["title", "message", "level", "is_active", "resolved_at", "updated_at"])
        return notification
    return SystemNotification.objects.create(
        source_key=source_key,
        title=title,
        message=message,
        level=level,
        is_active=True,
    )


def _resolve_system_notifications_by_prefix(prefix, active_keys=None):
    active_keys = set(active_keys or [])
    queryset = SystemNotification.objects.filter(source_key__startswith=prefix)
    if active_keys:
        queryset = queryset.exclude(source_key__in=active_keys)
    for notification in queryset.filter(is_active=True):
        notification.is_active = False
        notification.resolved_at = timezone.now()
        notification.save(update_fields=["is_active", "resolved_at", "updated_at"])


def _sync_portal_critical_notifications(open_demands=None):
    if open_demands is None:
        open_demands = list(
            PortalDemand.objects.filter(status__in=[PortalDemand.STATUS_PENDING, PortalDemand.STATUS_ASSUMED])
            .select_related("requester", "assigned_to", "task_group", "task_type", "sla_policy", "sla_policy__default_attendant")
            .order_by("created_at", "id")
        )
        _decorate_portal_demands(open_demands)

    active_keys = set()
    for demand in open_demands:
        if getattr(demand, "triage_label", "") != "Critica":
            continue
        source_key = f"portal-demand-critical-{demand.id}"
        title = f"Demanda critica no portal: {demand.protocol}"
        message_parts = [
            demand.title,
            f"Solicitante: {getattr(demand, 'requester_display', _queue_collaborator_display_name(demand.requester))}",
            getattr(demand, "triage_hint", ""),
        ]
        suggested_attendant = getattr(getattr(demand, "sla_preview", {}), "get", None)
        if suggested_attendant and demand.sla_preview.get("default_attendant") not in {"", "-"}:
            message_parts.append(f"Sugerido: {demand.sla_preview.get('default_attendant')}")
        if getattr(demand, "assigned_display", "-") not in {"", "-"} and demand.status == PortalDemand.STATUS_ASSUMED:
            message_parts.append(f"Responsavel atual: {demand.assigned_display}")
        message = " | ".join([part for part in message_parts if part])
        _upsert_system_notification(
            source_key=source_key,
            title=title,
            message=message[:1000],
            level=SystemNotification.LEVEL_ERROR,
        )
        active_keys.add(source_key)

    _resolve_system_notifications_by_prefix("portal-demand-critical-", active_keys)


@login_required
def portalPendingDemandsPage(request):
    context = _portal_admin_base_context(request, "pending", "Entrada de Chamados")
    if context["can_manage"]:
        _sync_portal_critical_notifications()
        context.update(_portal_ticket_list_context(request))
        context["header_stats"] = _portal_ticket_header_stats()
    else:
        context.update(
            {
                "tickets": [],
                "page_obj": None,
                "current_view": "abertos",
                "current_view_label": "",
                "view_counts": {"abertos": 0, "meus": 0, "nao_atribuidos": 0, "resolvidos": 0, "todos": 0},
                "priority_filter": "",
                "group_filter": "",
                "search_query": "",
                "sla_filter": "",
                "groups": [],
                "base_querystring": "",
                "header_stats": {},
            }
        )
    context["assumed_flag"] = request.GET.get("assumed") == "1"
    context["bulk_assumed_flag"] = request.GET.get("bulk_assumed") == "1"
    return render(request, "tiqueue/portal_pending_demands.html", context)


@login_required
def portalDemandFieldsConfigPage(request):
    context = _portal_admin_base_context(request, "fields", "Campos de Abertura do Portal")
    custom_field_form = PortalDemandCustomFieldCreateForm(prefix="portal_field")
    custom_option_forms = {}

    if request.method == "POST" and context["can_manage"]:
        form_type = (request.POST.get("form_type") or "").strip()
        if form_type == "create_custom_field":
            custom_field_form = PortalDemandCustomFieldCreateForm(request.POST, prefix="portal_field")
            if custom_field_form.is_valid():
                next_sort = (PortalDemandCustomField.objects.aggregate(max_sort=models.Max("sort_order")).get("max_sort") or 0) + 1
                definition = PortalDemandCustomField.objects.create(
                    label=custom_field_form.cleaned_data["label"],
                    field_type=custom_field_form.cleaned_data["field_type"],
                    placeholder=custom_field_form.cleaned_data["placeholder"] or None,
                    help_text=custom_field_form.cleaned_data["help_text"] or None,
                    is_required=custom_field_form.cleaned_data["is_required"],
                    sort_order=next_sort,
                    created_by=request.user,
                )
                definition.task_groups.set(custom_field_form.cleaned_data["task_groups"])
                definition.task_types.set(custom_field_form.cleaned_data["task_types"])
                if definition.field_type == PortalDemandCustomField.FIELD_SELECT:
                    option_label = custom_field_form.cleaned_data["initial_option_label"]
                    PortalDemandCustomFieldOption.objects.create(
                        field=definition,
                        value=_portal_field_option_value(definition, option_label),
                        label=option_label,
                        sort_order=1,
                    )
                return redirect(f"{reverse('portalDemandFieldsConfigPage')}?field_created=1")
        elif form_type == "create_custom_option":
            field_id = (request.POST.get("field_id") or "").strip()
            target_field = get_object_or_404(
                PortalDemandCustomField.objects.filter(is_active=True),
                pk=field_id,
                field_type=PortalDemandCustomField.FIELD_SELECT,
            )
            option_form = PortalDemandCustomFieldOptionForm(request.POST, prefix=f"portal_option_{target_field.id}")
            custom_option_forms[target_field.id] = option_form
            if option_form.is_valid():
                next_sort = (
                    PortalDemandCustomFieldOption.objects.filter(field=target_field).aggregate(max_sort=models.Max("sort_order")).get("max_sort")
                    or 0
                ) + 1
                option_label = option_form.cleaned_data["label"]
                PortalDemandCustomFieldOption.objects.create(
                    field=target_field,
                    value=_portal_field_option_value(target_field, option_label),
                    label=option_label,
                    sort_order=next_sort,
                )
                return redirect(f"{reverse('portalDemandFieldsConfigPage')}?option_created=1")

    if context["can_manage"]:
        portal_custom_fields, portal_custom_field_rows, custom_option_forms = _portal_custom_field_rows(custom_option_forms)
    else:
        portal_custom_fields, portal_custom_field_rows, custom_option_forms = [], [], {}
    context.update(
        {
            "custom_field_form": custom_field_form,
            "custom_option_forms": custom_option_forms,
            "portal_custom_fields": portal_custom_fields,
            "portal_custom_field_rows": portal_custom_field_rows,
            "field_created_flag": request.GET.get("field_created") == "1",
            "option_created_flag": request.GET.get("option_created") == "1",
        }
    )
    return render(request, "tiqueue/portal_pending_fields.html", context)


@login_required
def portalDemandSlaConfigPage(request):
    context = _portal_admin_base_context(request, "sla", "Políticas de SLA do Portal")
    sla_form = PortalDemandSlaPolicyForm(prefix="portal_sla")

    if request.method == "POST" and context["can_manage"]:
        sla_form = PortalDemandSlaPolicyForm(request.POST, prefix="portal_sla")
        if sla_form.is_valid():
            next_sort = (PortalDemandSlaPolicy.objects.aggregate(max_sort=models.Max("sort_order")).get("max_sort") or 0) + 1
            policy = sla_form.save(commit=False)
            policy.sort_order = next_sort
            policy.save()
            return redirect(f"{reverse('portalDemandSlaConfigPage')}?sla_created=1")

    context.update(
        {
            "sla_form": sla_form,
            "sla_policies": _portal_sla_policies() if context["can_manage"] else [],
            "sla_created_flag": request.GET.get("sla_created") == "1",
        }
    )
    return render(request, "tiqueue/portal_pending_sla.html", context)


@login_required
def portalDemandResponsesConfigPage(request):
    context = _portal_admin_base_context(request, "responses", "Respostas Prontas do Portal")
    canned_response_form = PortalCannedResponseForm(prefix="portal_canned")

    if request.method == "POST" and context["can_manage"]:
        canned_response_form = PortalCannedResponseForm(request.POST, prefix="portal_canned")
        if canned_response_form.is_valid():
            next_sort = (PortalCannedResponse.objects.aggregate(max_sort=models.Max("sort_order")).get("max_sort") or 0) + 1
            canned = canned_response_form.save(commit=False)
            canned.sort_order = next_sort
            canned.created_by = request.user
            canned.save()
            return redirect(f"{reverse('portalDemandResponsesConfigPage')}?canned_created=1")

    context.update(
        {
            "canned_response_form": canned_response_form,
            "canned_responses": _portal_canned_responses() if context["can_manage"] else [],
            "canned_created_flag": request.GET.get("canned_created") == "1",
        }
    )
    return render(request, "tiqueue/portal_pending_responses.html", context)


@login_required
def portalDemandSlaConfigPage(request):
    context = _portal_admin_base_context(request, "sla", "Politicas de SLA do Portal")
    editing_sla_policy = None
    edit_policy_id = (request.GET.get("edit") or "").strip()
    if context["can_manage"] and edit_policy_id.isdigit():
        editing_sla_policy = get_object_or_404(PortalDemandSlaPolicy, pk=int(edit_policy_id))
    sla_form = PortalDemandSlaPolicyForm(prefix="portal_sla", instance=editing_sla_policy)

    if request.method == "POST" and context["can_manage"]:
        form_type = (request.POST.get("form_type") or "create_sla").strip().lower()
        if form_type == "toggle_sla":
            policy_id = (request.POST.get("policy_id") or "").strip()
            if policy_id.isdigit():
                policy = get_object_or_404(PortalDemandSlaPolicy, pk=int(policy_id))
                policy.is_active = not policy.is_active
                policy.save(update_fields=["is_active", "updated_at"])
                _portal_refresh_open_demands_sla()
                _sync_portal_critical_notifications()
                return redirect(f"{reverse('portalDemandSlaConfigPage')}?sla_toggled=1")
        else:
            policy_id = (request.POST.get("policy_id") or "").strip()
            editing_sla_policy = (
                get_object_or_404(PortalDemandSlaPolicy, pk=int(policy_id))
                if form_type == "update_sla" and policy_id.isdigit()
                else None
            )
            sla_form = PortalDemandSlaPolicyForm(request.POST, prefix="portal_sla", instance=editing_sla_policy)
            if sla_form.is_valid():
                is_update = editing_sla_policy is not None
                next_sort = (PortalDemandSlaPolicy.objects.aggregate(max_sort=models.Max("sort_order")).get("max_sort") or 0) + 1
                policy = sla_form.save(commit=False)
                if not is_update:
                    policy.sort_order = next_sort
                policy.save()
                _portal_refresh_open_demands_sla()
                _sync_portal_critical_notifications()
                flag = "sla_updated" if is_update else "sla_created"
                return redirect(f"{reverse('portalDemandSlaConfigPage')}?{flag}=1")

    context.update(
        {
            "sla_form": sla_form,
            "sla_policies": _portal_sla_policies() if context["can_manage"] else [],
            "sla_created_flag": request.GET.get("sla_created") == "1",
            "sla_updated_flag": request.GET.get("sla_updated") == "1",
            "sla_toggled_flag": request.GET.get("sla_toggled") == "1",
            "editing_sla_policy": editing_sla_policy,
        }
    )
    return render(request, "tiqueue/portal_pending_sla.html", context)


@login_required
def portalDemandResponsesConfigPage(request):
    context = _portal_admin_base_context(request, "responses", "Respostas Prontas do Portal")
    editing_canned_response = None
    edit_canned_id = (request.GET.get("edit") or "").strip()
    if context["can_manage"] and edit_canned_id.isdigit():
        editing_canned_response = get_object_or_404(PortalCannedResponse, pk=int(edit_canned_id))
    canned_response_form = PortalCannedResponseForm(prefix="portal_canned", instance=editing_canned_response)

    if request.method == "POST" and context["can_manage"]:
        form_type = (request.POST.get("form_type") or "create_canned").strip().lower()
        if form_type == "toggle_canned":
            canned_id = (request.POST.get("canned_id") or "").strip()
            if canned_id.isdigit():
                canned = get_object_or_404(PortalCannedResponse, pk=int(canned_id))
                canned.is_active = not canned.is_active
                canned.save(update_fields=["is_active", "updated_at"])
                return redirect(f"{reverse('portalDemandResponsesConfigPage')}?canned_toggled=1")
        else:
            canned_id = (request.POST.get("canned_id") or "").strip()
            editing_canned_response = (
                get_object_or_404(PortalCannedResponse, pk=int(canned_id))
                if form_type == "update_canned" and canned_id.isdigit()
                else None
            )
            canned_response_form = PortalCannedResponseForm(request.POST, prefix="portal_canned", instance=editing_canned_response)
            if canned_response_form.is_valid():
                is_update = editing_canned_response is not None
                next_sort = (PortalCannedResponse.objects.aggregate(max_sort=models.Max("sort_order")).get("max_sort") or 0) + 1
                canned = canned_response_form.save(commit=False)
                if not is_update:
                    canned.sort_order = next_sort
                    canned.created_by = request.user
                canned.save()
                flag = "canned_updated" if is_update else "canned_created"
                return redirect(f"{reverse('portalDemandResponsesConfigPage')}?{flag}=1")

    context.update(
        {
            "canned_response_form": canned_response_form,
            "canned_responses": _portal_canned_responses() if context["can_manage"] else [],
            "canned_created_flag": request.GET.get("canned_created") == "1",
            "canned_updated_flag": request.GET.get("canned_updated") == "1",
            "canned_toggled_flag": request.GET.get("canned_toggled") == "1",
            "editing_canned_response": editing_canned_response,
        }
    )
    return render(request, "tiqueue/portal_pending_responses.html", context)


def _assume_portal_demand(demand, owner_user):
    if demand.status != PortalDemand.STATUS_PENDING or demand.linked_queue_item_id:
        return False

    queue_item = _create_queue_item_from_portal_demand(demand, owner_user)
    demand.status = PortalDemand.STATUS_ASSUMED
    demand.assigned_to = owner_user
    demand.linked_queue_item = queue_item
    demand.assumed_at = timezone.now()
    demand.save(
        update_fields=[
            "status",
            "assigned_to",
            "linked_queue_item",
            "assumed_at",
            "updated_at",
        ]
    )
    _portal_log_event(
        demand,
        PortalDemandLog.EVENT_ASSUMED,
        actor=owner_user,
        summary=f"Demanda assumida por {_queue_collaborator_display_name(owner_user)}.",
        to_attendant=owner_user,
    )
    _sync_portal_critical_notifications()
    return True


def _transfer_portal_demand(demand, target_user, actor_user):
    if demand.status in {PortalDemand.STATUS_COMPLETED, PortalDemand.STATUS_CANCELLED}:
        return False, "Somente demandas ativas podem ser transferidas."

    previous_attendant = demand.assigned_to
    if previous_attendant and previous_attendant.id == target_user.id:
        return False, "Selecione um atendente diferente do atual."

    queue_item = demand.linked_queue_item
    if queue_item is None:
        queue_item = _create_queue_item_from_portal_demand(demand, target_user)
        demand.linked_queue_item = queue_item
    else:
        _move_portal_queue_item_to_attendant(queue_item, target_user)

    demand.assigned_to = target_user
    demand.status = PortalDemand.STATUS_ASSUMED
    if not demand.assumed_at:
        demand.assumed_at = timezone.now()
    demand.save(update_fields=["assigned_to", "status", "linked_queue_item", "assumed_at", "updated_at"])

    if previous_attendant:
        summary = (
            f"Demanda transferida de {_queue_collaborator_display_name(previous_attendant)} para "
            f"{_queue_collaborator_display_name(target_user)}."
        )
        event_type = PortalDemandLog.EVENT_TRANSFERRED
    else:
        summary = f"Demanda atribuída diretamente para {_queue_collaborator_display_name(target_user)}."
        event_type = PortalDemandLog.EVENT_ASSUMED

    _portal_log_event(
        demand,
        event_type,
        actor=actor_user,
        summary=summary,
        from_attendant=previous_attendant,
        to_attendant=target_user,
    )
    _sync_portal_critical_notifications()
    return True, None


def _portal_archive_linked_queue_item(queue_item):
    if not queue_item:
        return None

    position = queue_item.n_queue_position
    source_fields = {field.name for field in queue_item._meta.fields}
    target_fields = {
        field.name
        for field in concludedTasks._meta.fields
        if field.name not in {"id", "n_register", "d_conclusion_date", "d_conclusion_time"}
    }
    allowed_fields = source_fields.intersection(target_fields)
    data = {field_name: getattr(queue_item, field_name) for field_name in allowed_fields}

    concluded = concludedTasks.objects.create(**data)
    concluded.extra_collaborators.set(queue_item.extra_collaborators.all())
    userQueue.objects.filter(user_code=queue_item.user_code, n_queue_position__gt=position).update(
        n_queue_position=models.F("n_queue_position") - 1
    )
    queue_item.delete()
    return concluded


def _portal_remove_linked_queue_item(queue_item):
    if not queue_item:
        return
    position = queue_item.n_queue_position
    userQueue.objects.filter(user_code=queue_item.user_code, n_queue_position__gt=position).update(
        n_queue_position=models.F("n_queue_position") - 1
    )
    queue_item.delete()


def _complete_portal_demand(demand, actor_user):
    if demand.status in {PortalDemand.STATUS_COMPLETED, PortalDemand.STATUS_CANCELLED}:
        return False, "Somente demandas ativas podem ser concluídas."

    queue_item = demand.linked_queue_item
    if queue_item is not None:
        _portal_archive_linked_queue_item(queue_item)

    demand.status = PortalDemand.STATUS_COMPLETED
    demand.completed_at = timezone.now()
    demand.linked_queue_item = None
    demand.save(update_fields=["status", "completed_at", "linked_queue_item", "updated_at"])
    _portal_log_event(
        demand,
        PortalDemandLog.EVENT_COMPLETED,
        actor=actor_user,
        summary=f"Demanda concluída por {_queue_collaborator_display_name(actor_user)}.",
        to_attendant=demand.assigned_to if demand.assigned_to_id else None,
    )
    _sync_portal_critical_notifications()
    return True, None


def _cancel_portal_demand(demand, actor_user):
    if demand.status in {PortalDemand.STATUS_COMPLETED, PortalDemand.STATUS_CANCELLED}:
        return False, "Somente demandas ativas podem ser canceladas."

    queue_item = demand.linked_queue_item
    if queue_item is not None:
        _portal_remove_linked_queue_item(queue_item)

    demand.status = PortalDemand.STATUS_CANCELLED
    demand.linked_queue_item = None
    demand.completed_at = None
    demand.save(update_fields=["status", "linked_queue_item", "completed_at", "updated_at"])
    _portal_log_event(
        demand,
        PortalDemandLog.EVENT_CANCELLED,
        actor=actor_user,
        summary=f"Demanda cancelada por {_queue_collaborator_display_name(actor_user)}.",
        to_attendant=demand.assigned_to if demand.assigned_to_id else None,
    )
    _sync_portal_critical_notifications()
    return True, None


@login_required
@require_POST
def portalDemandAssume(request, demand_id):
    if not _is_system_admin(request.user):
        return redirect(f"{reverse('portalPendingDemandsPage')}?denied=1")

    with transaction.atomic():
        demand = get_object_or_404(
            PortalDemand.objects.select_for_update().select_related("requester", "task_group", "task_type"),
            pk=demand_id,
        )

        if not _assume_portal_demand(demand, request.user):
            return redirect("portalPendingDemandsPage")

    redirect_target = _portal_redirect_target(request, "portalPendingDemandsPage")
    if redirect_target == reverse("portalPendingDemandsPage"):
        redirect_target = f"{redirect_target}?assumed=1"
    return redirect(redirect_target)


@login_required
@require_POST
def portalDemandBulkAssume(request):
    if not _is_system_admin(request.user):
        return redirect(f"{reverse('portalPendingDemandsPage')}?denied=1")

    raw_ids = []
    raw_ids.extend(request.POST.getlist("demand_ids"))
    csv_ids = (request.POST.get("selected_ids") or "").strip()
    if csv_ids:
        raw_ids.extend(csv_ids.split(","))

    demand_ids = []
    for value in raw_ids:
        value = str(value or "").strip()
        if value.isdigit():
            demand_ids.append(int(value))

    if not demand_ids:
        return redirect("portalPendingDemandsPage")

    unique_ids = list(dict.fromkeys(demand_ids))
    assumed_any = False
    with transaction.atomic():
        demands = list(
            PortalDemand.objects.select_for_update()
            .select_related("requester", "task_group", "task_type")
            .filter(id__in=unique_ids)
            .order_by("created_at", "id")
        )
        for demand in demands:
            if _assume_portal_demand(demand, request.user):
                assumed_any = True

    if assumed_any:
        return redirect(f"{reverse('portalPendingDemandsPage')}?bulk_assumed=1")
    return redirect("portalPendingDemandsPage")


def _serialize_tetris_score(entry, position, current_user_id=None):
    user = getattr(entry, "user", None)
    display_name = (getattr(user, "nameUser", "") or getattr(user, "username", "") or "Usuario").strip()
    return {
        "rank": position,
        "user_id": user.id if user else None,
        "user_name": display_name,
        "score": int(entry.best_score or 0),
        "lines": int(entry.best_lines or 0),
        "level": int(entry.best_level or 1),
        "is_current_user": bool(current_user_id and user and user.id == current_user_id),
    }


def _get_tetris_highscores(current_user=None, limit=10):
    current_user_id = getattr(current_user, "id", None)
    entries = (
        MaxiTetrisHighScore.objects.filter(best_score__gt=0)
        .select_related("user")
        .order_by("-best_score", "-best_lines", "-best_level", "id")[:limit]
    )
    return [
        _serialize_tetris_score(entry, position=index + 1, current_user_id=current_user_id)
        for index, entry in enumerate(entries)
    ]


def _get_tetris_personal_best(user):
    if not getattr(user, "is_authenticated", False):
        return None
    entry = MaxiTetrisHighScore.objects.filter(user=user, best_score__gt=0).first()
    if not entry:
        return None
    return {
        "score": int(entry.best_score or 0),
        "lines": int(entry.best_lines or 0),
        "level": int(entry.best_level or 1),
    }


def _sync_service_notifications():
    source_key = "service_agent_down"
    notif = SystemNotification.objects.filter(source_key=source_key).first()
    result = _service_agent_request("/services", method="GET")

    if not result.get("ok"):
        message = result.get("error") or "Falha ao consultar servicos no Service Agent."
        if notif:
            notif.title = "Falha na consulta de servicos"
            notif.message = message
            notif.level = SystemNotification.LEVEL_WARNING
            notif.is_active = True
            notif.resolved_at = None
            notif.save(update_fields=["title", "message", "level", "is_active", "resolved_at", "updated_at"])
        else:
            SystemNotification.objects.create(
                source_key=source_key,
                title="Falha na consulta de servicos",
                message=message,
                level=SystemNotification.LEVEL_WARNING,
                is_active=True,
            )
        return

    items = result.get("data", []) or []
    inactive = []
    for item in items:
        status_raw = str((item or {}).get("status") or "").strip().lower()
        is_running = status_raw == "4" or "running" in status_raw
        if not is_running:
            inactive.append(str((item or {}).get("display_name") or (item or {}).get("name") or "-"))

    if inactive:
        message = f"{len(inactive)} servico(s) inativo(s): " + ", ".join(inactive[:8])
        if len(inactive) > 8:
            message += ", ..."
        if notif:
            notif.title = "Servicos inativos detectados"
            notif.message = message
            notif.level = SystemNotification.LEVEL_ERROR
            notif.is_active = True
            notif.resolved_at = None
            notif.save(update_fields=["title", "message", "level", "is_active", "resolved_at", "updated_at"])
        else:
            SystemNotification.objects.create(
                source_key=source_key,
                title="Servicos inativos detectados",
                message=message,
                level=SystemNotification.LEVEL_ERROR,
                is_active=True,
            )
    elif notif and notif.is_active:
        notif.is_active = False
        notif.resolved_at = timezone.now()
        notif.save(update_fields=["is_active", "resolved_at", "updated_at"])


@login_required
@require_POST
def hubQuickAddItem(request):
    scope = (request.POST.get("scope") or "").strip().lower()
    name = (request.POST.get("name") or "").strip()
    link = (request.POST.get("link") or "").strip()
    image_url = (request.POST.get("image_url") or "").strip() or None
    category_name = (request.POST.get("category_name") or "").strip()

    if scope not in ("general", "my"):
        return JsonResponse({"status": "error", "message": "Escopo invalido."}, status=400)
    if not name or not link:
        return JsonResponse({"status": "error", "message": "Nome e link sao obrigatorios."}, status=400)

    try:
        if scope == "general":
            category_id = int(request.POST.get("category_id") or 0)
            category = HubToolCategory.objects.filter(pk=category_id, is_active=True).first()
            if not category:
                return JsonResponse({"status": "error", "message": "Categoria geral nao encontrada."}, status=404)
            HubTool.objects.create(
                category=category,
                name=name,
                link=link,
                image_url=image_url,
                is_active=True,
            )
        else:
            category_id_raw = request.POST.get("category_id")
            category = None
            if category_id_raw:
                try:
                    category = HubUserToolCategory.objects.filter(
                        pk=int(category_id_raw), user=request.user, is_active=True
                    ).first()
                except (TypeError, ValueError):
                    category = None
            if category is None and category_name:
                category = HubUserToolCategory.objects.filter(
                    user=request.user, name=category_name, is_active=True
                ).first()

            if category is None:
                return JsonResponse({"status": "error", "message": "Categoria do Meu HUB nao encontrada."}, status=404)

            HubUserTool.objects.create(
                user=request.user,
                category=category,
                category_name=category.name,
                name=name,
                link=link,
                image_url=image_url,
                is_active=True,
            )
    except IntegrityError:
        return JsonResponse({"status": "error", "message": "Ja existe item com esse nome nesta categoria."}, status=400)
    except Exception as exc:
        return JsonResponse({"status": "error", "message": f"Falha ao salvar item: {exc}"}, status=500)

    return JsonResponse({"status": "ok"})


@login_required
@require_GET
def maxiTetrisHighscores(request):
    return JsonResponse(
        {
            "status": "ok",
            "leaderboard": _get_tetris_highscores(request.user),
            "personal_best": _get_tetris_personal_best(request.user),
        }
    )


@login_required
@require_POST
def maxiTetrisSubmitScore(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    try:
        score = max(0, int(payload.get("score") or 0))
        lines = max(0, int(payload.get("lines") or 0))
        level = max(1, int(payload.get("level") or 1))
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Pontuacao invalida."}, status=400)

    leaderboard_entry, _ = MaxiTetrisHighScore.objects.get_or_create(user=request.user)
    is_better_score = (
        score > leaderboard_entry.best_score
        or (score == leaderboard_entry.best_score and lines > leaderboard_entry.best_lines)
        or (
            score == leaderboard_entry.best_score
            and lines == leaderboard_entry.best_lines
            and level > leaderboard_entry.best_level
        )
    )

    if is_better_score:
        leaderboard_entry.best_score = score
        leaderboard_entry.best_lines = lines
        leaderboard_entry.best_level = level
        leaderboard_entry.save(update_fields=["best_score", "best_lines", "best_level", "updated_at"])

    return JsonResponse(
        {
            "status": "ok",
            "saved": bool(is_better_score),
            "leaderboard": _get_tetris_highscores(request.user),
            "personal_best": _get_tetris_personal_best(request.user),
        }
    )


def _serialize_pomodoro_session(entry):
    return {
        "id": entry.id,
        "focus_minutes": entry.focus_minutes,
        "break_minutes": entry.break_minutes,
        "accomplishment": entry.accomplishment,
        "completed_at_label": timezone.localtime(entry.completed_at).strftime("%d/%m/%Y %H:%M"),
        "completed_at_iso": entry.completed_at.isoformat(),
    }


def _get_pomodoro_history(user, limit=40):
    entries = PomodoroSession.objects.filter(user=user)[:limit]
    return [_serialize_pomodoro_session(entry) for entry in entries]


def _get_pomodoro_stats(user):
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    sessions = PomodoroSession.objects.filter(user=user)

    today_agg = sessions.filter(completed_at__date=today).aggregate(count=Count("id"), minutes=Sum("focus_minutes"))
    week_agg = sessions.filter(completed_at__date__gte=week_start).aggregate(count=Count("id"), minutes=Sum("focus_minutes"))
    total_agg = sessions.aggregate(count=Count("id"), minutes=Sum("focus_minutes"))

    return {
        "today_sessions": today_agg["count"] or 0,
        "today_minutes": today_agg["minutes"] or 0,
        "week_sessions": week_agg["count"] or 0,
        "week_minutes": week_agg["minutes"] or 0,
        "total_sessions": total_agg["count"] or 0,
        "total_minutes": total_agg["minutes"] or 0,
    }


@login_required
def pomodoroPage(request):
    context = {
        "pomodoro_stats": _get_pomodoro_stats(request.user),
        "pomodoro_history": _get_pomodoro_history(request.user),
    }
    return render(request, "tiqueue/pomodoro.html", context)


@login_required
@require_POST
def pomodoroSaveSession(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    try:
        focus_minutes = max(1, min(180, int(payload.get("focus_minutes") or 25)))
        break_minutes = max(1, min(60, int(payload.get("break_minutes") or 5)))
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Duracao invalida."}, status=400)

    accomplishment = str(payload.get("accomplishment") or "").strip()[:240]

    PomodoroSession.objects.create(
        user=request.user,
        focus_minutes=focus_minutes,
        break_minutes=break_minutes,
        accomplishment=accomplishment,
    )

    return JsonResponse(
        {
            "status": "ok",
            "stats": _get_pomodoro_stats(request.user),
            "history": _get_pomodoro_history(request.user),
        }
    )


@login_required
@require_POST
def pomodoroDeleteSession(request, session_id):
    PomodoroSession.objects.filter(user=request.user, id=session_id).delete()
    return JsonResponse(
        {
            "status": "ok",
            "stats": _get_pomodoro_stats(request.user),
            "history": _get_pomodoro_history(request.user),
        }
    )


def _query_sm_tickets(attendant_id: str, closed: bool = False):
    """
    Returns SM tickets for the attendant in format:
    [{"codigo_helpdesk": "...", "descricao": "...", "detalhe_demanda": "..."}]
    """
    host = os.getenv("SM_DB_HOST", "192.168.0.209")
    port = int(os.getenv("SM_DB_PORT", "3306"))
    db_name = os.getenv("SM_DB_NAME", "sm")
    db_user = os.getenv("SM_DB_USER", "sm_viewer")
    db_pass = os.getenv("SM_DB_PASSWORD", "KcyVbd66h@UnvZ")

    status_filter = "in (5, 6, 13)" if closed else "not in (5, 6, 13)"
    query = f"""
        select
            A.subject AS descricao,
            A.id AS codigo_helpdesk,
            A.description_txt AS detalhe_demanda
        from
            helpdesk.helpdesk A
        where
            A.status_id {status_filter} AND
            A.user_id_attendent = %s
    """

    driver_errors = []

    # Try PyMySQL first.
    try:
        import pymysql  # type: ignore

        conn = pymysql.connect(
            host=host,
            user=db_user,
            password=db_pass,
            database=db_name,
            port=port,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(query, (attendant_id,))
                rows = cur.fetchall() or []
                return [
                    {
                        "codigo_helpdesk": str(r.get("codigo_helpdesk") or "").strip(),
                        "descricao": str(r.get("descricao") or "").strip(),
                        "detalhe_demanda": str(r.get("detalhe_demanda") or "").strip(),
                    }
                    for r in rows
                ]
        finally:
            conn.close()
    except Exception as exc:
        driver_errors.append(f"pymysql: {exc}")

    # Fallback to mysql-connector-python if available.
    try:
        import mysql.connector  # type: ignore

        conn = mysql.connector.connect(
            host=host,
            user=db_user,
            password=db_pass,
            database=db_name,
            port=port,
        )
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(query, (attendant_id,))
            rows = cur.fetchall() or []
            cur.close()
            return [
                {
                    "codigo_helpdesk": str(r.get("codigo_helpdesk") or "").strip(),
                    "descricao": str(r.get("descricao") or "").strip(),
                    "detalhe_demanda": str(r.get("detalhe_demanda") or "").strip(),
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as exc:
        driver_errors.append(f"mysql-connector: {exc}")

    # Fallback to mysqlclient (MySQLdb) if available.
    try:
        import MySQLdb  # type: ignore

        conn = MySQLdb.connect(
            host=host,
            user=db_user,
            passwd=db_pass,
            db=db_name,
            port=port,
            charset="utf8mb4",
        )
        try:
            cur = conn.cursor(MySQLdb.cursors.DictCursor)
            cur.execute(query, (attendant_id,))
            rows = cur.fetchall() or []
            cur.close()
            return [
                {
                    "codigo_helpdesk": str(r.get("codigo_helpdesk") or "").strip(),
                    "descricao": str(r.get("descricao") or "").strip(),
                    "detalhe_demanda": str(r.get("detalhe_demanda") or "").strip(),
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as exc:
        driver_errors.append(f"mysqlclient/MySQLdb: {exc}")

    raise RuntimeError(
        "Falha ao conectar no SM: nenhum driver MySQL disponivel. "
        "Instale um destes pacotes: `pip install pymysql` ou `pip install mysql-connector-python`. "
        f"Detalhes: {' | '.join(driver_errors)}"
    )


def _query_sm_open_tickets(attendant_id: str):
    return _query_sm_tickets(attendant_id, closed=False)


def _query_sm_closed_tickets(attendant_id: str):
    return _query_sm_tickets(attendant_id, closed=True)


@login_required
def contractsPage(request):
    if request.method == "POST":
        issue_date = request.POST.get("issue_date") or None
        due_date = request.POST.get("due_date") or None
        amount_raw = (request.POST.get("amount") or "").strip().replace(".", "").replace(",", ".")
        amount_value = None
        if amount_raw:
            try:
                amount_value = Decimal(amount_raw)
            except Exception:
                amount_value = None

        ContractRecord.objects.create(
            reference_month=(request.POST.get("reference_month") or "").strip() or None,
            company=(request.POST.get("company") or "").strip() or None,
            cnpj=(request.POST.get("cnpj") or "").strip() or None,
            supplier=(request.POST.get("supplier") or "").strip() or None,
            invoice_number=(request.POST.get("invoice_number") or "").strip() or None,
            issue_date=issue_date,
            due_date=due_date,
            amount=amount_value,
            item=(request.POST.get("item") or "").strip() or None,
            request_code=(request.POST.get("request_code") or "").strip() or None,
            contract_code=(request.POST.get("contract_code") or "").strip() or None,
            transaction_type=(request.POST.get("transaction_type") or "").strip() or None,
            cost_center=(request.POST.get("cost_center") or "").strip() or None,
            observation=(request.POST.get("observation") or "").strip() or None,
        )
        return redirect("contractsPage")

    contracts = ContractRecord.objects.all().order_by("-id")
    return render(request, "tiqueue/contracts.html", {"contracts": contracts})


KANBAN_DEFAULT_COLUMNS = [
    ("Backlog", "#343955", 1),
    ("Em andamento", "#3a3f61", 2),
    ("Bloqueado", "#5a3a3a", 3),
    ("Concluido", "#1f5a3a", 4),
]


def _normalize_text(value):
    base = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return base.strip().lower()


def _ensure_project_kanban_columns(project):
    if not project.kanban_columns.exists():
        for name, color, order in KANBAN_DEFAULT_COLUMNS:
            ProjectKanbanColumn.objects.create(project=project, name=name, color=color, sort_order=order)
    return list(ProjectKanbanColumn.objects.filter(project=project).order_by("sort_order", "id"))


def _roadmap_status_for_column(column):
    name = _normalize_text(column.name)
    if "conclu" in name or "done" in name:
        return "done"
    if "bloq" in name or "block" in name:
        return "blocked"
    if "andamento" in name or "doing" in name or "exec" in name:
        return "doing"
    if "backlog" in name or "planej" in name or "todo" in name or "to do" in name:
        return "planned"
    return "doing"


def _column_for_roadmap_status(project, status):
    columns = _ensure_project_kanban_columns(project)
    status = (status or "planned").strip().lower()

    preferred = []
    if status == "done":
        preferred = ["conclu", "done"]
    elif status == "blocked":
        preferred = ["bloq", "block"]
    elif status == "doing":
        preferred = ["andamento", "doing", "exec"]
    else:
        preferred = ["backlog", "planej", "todo", "to do"]

    for col in columns:
        name = _normalize_text(col.name)
        if any(token in name for token in preferred):
            return col

    # Fallback by sort order if column names were customized.
    if status == "planned":
        return columns[0]
    if status == "done":
        return columns[-1]
    if status == "blocked" and len(columns) >= 3:
        return columns[2]
    if status == "doing" and len(columns) >= 2:
        return columns[1]
    return columns[0]


def _sync_roadmap_item_to_kanban(item):
    target_column = _column_for_roadmap_status(item.project, item.status)
    linked = ProjectKanbanCard.objects.filter(roadmap_item=item).first()

    if linked:
        changed_fields = []
        if linked.project_id != item.project_id:
            linked.project = item.project
            changed_fields.append("project")
        if linked.column_id != target_column.id:
            linked.column = target_column
            changed_fields.append("column")
        if linked.title != item.title:
            linked.title = item.title
            changed_fields.append("title")
        desc = item.description or None
        if linked.description != desc:
            linked.description = desc
            changed_fields.append("description")
        if linked.due_date != item.end_date:
            linked.due_date = item.end_date
            changed_fields.append("due_date")

        if changed_fields:
            linked.save(update_fields=changed_fields + ["updated_at"])
        return linked

    max_sort = (
        ProjectKanbanCard.objects.filter(project=item.project, column=target_column)
        .aggregate(models.Max("sort_order"))
        .get("sort_order__max")
    )
    next_sort = int(max_sort or 0) + 1

    return ProjectKanbanCard.objects.create(
        project=item.project,
        column=target_column,
        roadmap_item=item,
        title=item.title,
        description=item.description or None,
        due_date=item.end_date,
        sort_order=next_sort,
    )


def _parse_iso_date(raw_value):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_user_id(raw_value):
    raw_value = str(raw_value or "").strip()
    return int(raw_value) if raw_value.isdigit() else None


def _normalize_project_milestone_key(raw_value):
    raw_value = (raw_value or "").strip()
    valid_keys = {choice[0] for choice in ProjectMilestone.MILESTONE_CHOICES}
    return raw_value if raw_value in valid_keys else ProjectMilestone.MILESTONE_ANALYSIS


def _project_milestone_templates_payload():
    payload = []
    for value, label in ProjectMilestone.MILESTONE_CHOICES:
        defaults = ProjectMilestone.template_defaults(value)
        payload.append(
            {
                "value": value,
                "label": label,
                "title": defaults["title"],
                "color": defaults["color"],
            }
        )
    return payload


def _project_milestone_next_sort(project, anchor_item=None):
    max_sort = (
        ProjectMilestone.objects.filter(project=project, anchor_item=anchor_item)
        .aggregate(models.Max("sort_order"))
        .get("sort_order__max")
    )
    return int(max_sort or 0) + 1


def _serialize_roadmap_subtask(subtask):
    return {
        "id": subtask.id,
        "description": subtask.description,
        "is_done": subtask.is_done,
    }


def _serialize_project_milestone(milestone, today=None):
    today = today or timezone.localdate()
    target_date = milestone.target_date
    is_overdue = bool(target_date and not milestone.is_done and target_date < today)
    overdue_days = (today - target_date).days if is_overdue else 0

    if milestone.is_done:
        status_label = "Concluido"
    elif is_overdue:
        status_label = "Atrasado"
    else:
        status_label = "Planejado"

    return {
        "id": milestone.id,
        "milestone_key": milestone.milestone_key,
        "template_label": dict(ProjectMilestone.MILESTONE_CHOICES).get(milestone.milestone_key, milestone.title),
        "anchor_item_id": milestone.anchor_item_id or "",
        "sort_order": milestone.sort_order,
        "title": milestone.title,
        "description": milestone.description or "",
        "target_date": (target_date.isoformat() if target_date else ""),
        "color": _normalize_hex_color(milestone.color, "#5CD6A3"),
        "is_done": bool(milestone.is_done),
        "completed_at": timezone.localtime(milestone.completed_at).isoformat() if milestone.completed_at else "",
        "status_label": status_label,
        "is_overdue": is_overdue,
        "overdue_days": overdue_days,
    }


def _roadmap_overdue_payload(item, today=None):
    today = today or timezone.localdate()
    if item.status == "done" or not item.end_date or item.end_date >= today:
        return {
            "is_overdue": False,
            "overdue_days": 0,
            "overdue_label": "",
        }

    overdue_days = (today - item.end_date).days
    return {
        "is_overdue": overdue_days > 0,
        "overdue_days": overdue_days,
        "overdue_label": f"{overdue_days} dia(s) em atraso" if overdue_days > 0 else "",
    }


def _serialize_roadmap_item(item):
    subtasks = list(getattr(item, "prefetched_subtasks", []) or item.subtasks.all().order_by("sort_order", "id"))
    subtask_total = len(subtasks)
    subtask_done = sum(1 for subtask in subtasks if subtask.is_done)
    responsible = getattr(item, "responsible", None)
    overdue_payload = _roadmap_overdue_payload(item)
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description or "",
        "status": item.status,
        "start": (item.start_date.isoformat() if item.start_date else ""),
        "end": (item.end_date.isoformat() if item.end_date else ""),
        "responsible_id": responsible.id if responsible else "",
        "responsible_name": (responsible.nameUser or responsible.username) if responsible else "",
        "subtasks": [_serialize_roadmap_subtask(subtask) for subtask in subtasks],
        "subtask_total": subtask_total,
        "subtask_done": subtask_done,
    } | overdue_payload


@login_required
def manageProjects(request):
    project_form = ProjectForm(prefix="project")
    roadmap_form = ProjectRoadmapItemForm(prefix="roadmap")
    column_form = ProjectKanbanColumnForm(prefix="col")
    card_form = ProjectKanbanCardForm(prefix="card")

    if request.method == "POST":
        form_id = request.POST.get("form_id")
        if form_id == "project":
            project_form = ProjectForm(request.POST, prefix="project")
            if project_form.is_valid():
                project_form.save()
        elif form_id == "edit_project":
            project_id = request.POST.get("project_id")
            if project_id:
                pf = ProjectForm(request.POST, prefix="edit")
                # We'll update manually to keep a compact inline form.
                name = (request.POST.get("name") or "").strip()
                description = (request.POST.get("description") or "").strip()
                developer_id = (request.POST.get("developer_id") or "").strip()
                participants_ids = request.POST.getlist("participants_ids")
                status = request.POST.get("status") or "active"
                color = (request.POST.get("color") or "").strip() or "#00bf63"
                start_date = request.POST.get("start_date") or None
                end_date = request.POST.get("end_date") or None
                if name:
                    project_obj = Project.objects.filter(pk=project_id).first()
                    if project_obj:
                        project_obj.name = name
                        project_obj.description = description or None
                        project_obj.developer_id = int(developer_id) if developer_id.isdigit() else None
                        project_obj.status = status
                        project_obj.color = color
                        project_obj.start_date = start_date or None
                        project_obj.end_date = end_date or None
                        project_obj.save()
                        valid_participants = [int(pid) for pid in participants_ids if str(pid).isdigit()]
                        project_obj.participants.set(valid_participants)
        elif form_id == "roadmap":
            roadmap_form = ProjectRoadmapItemForm(request.POST, prefix="roadmap")
            if roadmap_form.is_valid():
                item = roadmap_form.save()
                _sync_roadmap_item_to_kanban(item)
        elif form_id == "edit_roadmap":
            item_id = request.POST.get("item_id")
            if item_id:
                item = get_object_or_404(ProjectRoadmapItem, pk=item_id)
                title = (request.POST.get("title") or "").strip()
                description = (request.POST.get("description") or "").strip()
                status = request.POST.get("status") or "planned"
                start_date = request.POST.get("start_date") or None
                end_date = request.POST.get("end_date") or None
                sort_order = request.POST.get("sort_order")
                responsible = User.objects.filter(pk=_parse_user_id(request.POST.get("responsible"))).first()
                if title:
                    item.title = title
                    item.responsible = responsible
                    item.description = description or None
                    item.status = status
                    item.start_date = start_date or None
                    item.end_date = end_date or None
                    if sort_order not in (None, ""):
                        item.sort_order = int(sort_order)
                    item.save()
                    _sync_roadmap_item_to_kanban(item)
        elif form_id == "delete_roadmap":
            item_id = request.POST.get("item_id")
            if item_id:
                item = get_object_or_404(ProjectRoadmapItem, pk=item_id)
                linked = ProjectKanbanCard.objects.filter(roadmap_item=item).first()
                if linked:
                    linked.delete()
                item.delete()
        elif form_id == "kanban_col":
            column_form = ProjectKanbanColumnForm(request.POST, prefix="col")
            if column_form.is_valid():
                column_form.save()
        elif form_id == "kanban_card":
            card_form = ProjectKanbanCardForm(request.POST, prefix="card")
            if card_form.is_valid():
                card_form.save()

    projects = list(Project.objects.select_related("developer").prefetch_related("participants").all().order_by("name"))
    users = User.objects.order_by("nameUser", "username")
    roadmap_items = (
        ProjectRoadmapItem.objects.select_related("project", "responsible").order_by("project__name", "sort_order", "id")
    )
    columns = ProjectKanbanColumn.objects.select_related("project").order_by("project__name", "sort_order", "id")
    cards = ProjectKanbanCard.objects.select_related("project", "column").order_by(
        "project__name", "column__sort_order", "sort_order", "id"
    )

    project_stats = {
        "total": len(projects),
        "active": sum(1 for p in projects if p.status == "active"),
        "planned": sum(1 for p in projects if p.status == "planned"),
        "paused": sum(1 for p in projects if p.status == "paused"),
        "done": sum(1 for p in projects if p.status == "done"),
    }

    return render(
        request,
        "tiqueue/projects.html",
        {
            "project_form": project_form,
            "roadmap_form": roadmap_form,
            "column_form": column_form,
            "card_form": card_form,
            "projects": projects,
            "project_stats": project_stats,
            "users": users,
            "roadmap_items": roadmap_items,
            "columns": columns,
            "cards": cards,
        },
    )


def _project_catalog_open_statuses():
    return ["planned", "active", "paused"]


def _project_catalog_redirect_response(redirect_name, return_query=""):
    redirect_url = reverse(redirect_name)
    return_query = (return_query or "").strip().lstrip("?")
    if return_query:
        redirect_url = f"{redirect_url}?{return_query}"
    return redirect(redirect_url)


def _normalize_hex_color(raw_value, default="#343955"):
    raw_value = (raw_value or "").strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw_value or ""):
        return raw_value.lower()
    return default


def _project_catalog_user_options(statuses):
    return (
        User.objects.filter(is_active=True)
        .filter(Q(projects__status__in=statuses) | Q(project_participations__status__in=statuses))
        .distinct()
        .order_by("nameUser", "username")
    )


def _decorate_project_catalog_items(projects):
    for project in projects:
        total = int(getattr(project, "roadmap_total", 0) or 0)
        done = int(getattr(project, "roadmap_done", 0) or 0)
        project.roadmap_progress_pct = int(round((done / total) * 100)) if total > 0 else 0
        project.color = _normalize_hex_color(project.color, "#343955")
        project.card_style = f"--project-accent: {project.color};"


def _project_catalog_color_mix(hex_color, target_rgb=(1.0, 1.0, 1.0), ratio=0.35):
    from reportlab.lib import colors

    base = colors.HexColor(_normalize_hex_color(hex_color))
    ratio = max(0.0, min(1.0, float(ratio)))
    return colors.Color(
        (base.red * (1 - ratio)) + (target_rgb[0] * ratio),
        (base.green * (1 - ratio)) + (target_rgb[1] * ratio),
        (base.blue * (1 - ratio)) + (target_rgb[2] * ratio),
    )


def _project_catalog_pdf_bar(done_pct, overdue_pct, accent_color):
    from reportlab.graphics.shapes import Drawing, Rect
    from reportlab.lib import colors

    width = 160
    height = 10
    drawing = Drawing(width, height)
    drawing.add(Rect(0, 0, width, height, rx=height / 2, ry=height / 2, fillColor=colors.HexColor("#d7dfef"), strokeColor=None))

    done_width = max(0, min(width, width * (max(done_pct, 0) / 100.0)))
    overdue_width = max(0, min(width, width * (max(overdue_pct, 0) / 100.0)))

    if done_width > 0:
        drawing.add(Rect(0, 0, done_width, height, rx=height / 2, ry=height / 2, fillColor=accent_color, strokeColor=None))
    if overdue_width > 0:
        x = max(0, width - overdue_width)
        drawing.add(Rect(x, 0, overdue_width, height, rx=height / 2, ry=height / 2, fillColor=colors.HexColor("#ffb85d"), strokeColor=None))
    return drawing


def _build_project_catalog_pdf(project):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

    roadmap_items = list(
        ProjectRoadmapItem.objects.filter(project=project)
        .select_related("responsible")
        .prefetch_related(
            Prefetch(
                "subtasks",
                queryset=ProjectRoadmapSubtask.objects.order_by("sort_order", "id"),
                to_attr="prefetched_subtasks",
            )
        )
        .order_by("sort_order", "id")
    )
    participants = list(project.participants.all().order_by("nameUser", "username"))
    today = timezone.localdate()
    generated_at = timezone.localtime()

    total_steps = len(roadmap_items)
    done_steps = sum(1 for item in roadmap_items if item.status == "done")
    blocked_steps = sum(1 for item in roadmap_items if item.status == "blocked")
    doing_steps = sum(1 for item in roadmap_items if item.status == "doing")
    planned_steps = sum(1 for item in roadmap_items if item.status == "planned")
    overdue_steps = sum(1 for item in roadmap_items if _roadmap_overdue_payload(item, today)["is_overdue"])
    progress_pct = int(round((done_steps / total_steps) * 100)) if total_steps else 0
    overdue_pct = int(round((overdue_steps / total_steps) * 100)) if total_steps else 0

    accent = _project_catalog_color_mix(project.color, ratio=0.0)
    accent_soft = _project_catalog_color_mix(project.color, ratio=0.82)
    accent_mid = _project_catalog_color_mix(project.color, ratio=0.55)
    accent_dark = _project_catalog_color_mix(project.color, target_rgb=(0.08, 0.11, 0.20), ratio=0.22)

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ProjectPdfTitle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=21,
            leading=24,
            textColor=accent_dark,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ProjectPdfSubtitle",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#5d657f"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="ProjectPdfSection",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=accent_dark,
            spaceBefore=6,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ProjectPdfBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=12.5,
            textColor=colors.HexColor("#33405c"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="ProjectPdfMuted",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#67748f"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="ProjectPdfSmall",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.2,
            leading=10,
            textColor=accent_dark,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ProjectPdfStepBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.3,
            leading=10.2,
            textColor=colors.HexColor("#33405c"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="ProjectPdfStepMuted",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=7.6,
            leading=9.3,
            textColor=colors.HexColor("#67748f"),
        )
    )

    participants_text = ", ".join((user.nameUser or user.username) for user in participants) if participants else "-"
    responsible_name = (project.developer.nameUser or project.developer.username) if project.developer_id else "-"
    status_label = project.get_status_display()
    start_label = project.start_date.strftime("%d/%m/%Y") if project.start_date else "-"
    end_label = project.end_date.strftime("%d/%m/%Y") if project.end_date else "-"

    summary_table = Table(
        [
            [
                Paragraph("<b>Progresso</b><br/>%s%% concluido" % progress_pct, styles["ProjectPdfBody"]),
                Paragraph("<b>Etapas</b><br/>%s total / %s concluidas" % (total_steps, done_steps), styles["ProjectPdfBody"]),
                Paragraph("<b>Atrasadas</b><br/>%s etapa(s)" % overdue_steps, styles["ProjectPdfBody"]),
                Paragraph("<b>Kanban</b><br/>%s card(s)" % int(getattr(project, "kanban_cards_total", project.kanban_cards.count())), styles["ProjectPdfBody"]),
            ]
        ],
        colWidths=[44 * mm, 44 * mm, 44 * mm, 44 * mm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.8, accent_mid),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#d9e1f0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )

    info_table = Table(
        [
            [
                Paragraph("<b>Status</b><br/>%s" % xml_escape(status_label), styles["ProjectPdfBody"]),
                Paragraph("<b>Responsavel</b><br/>%s" % xml_escape(responsible_name), styles["ProjectPdfBody"]),
            ],
            [
                Paragraph("<b>Inicio</b><br/>%s" % start_label, styles["ProjectPdfBody"]),
                Paragraph("<b>Fim</b><br/>%s" % end_label, styles["ProjectPdfBody"]),
            ],
            [
                Paragraph("<b>Participantes</b><br/>%s" % xml_escape(participants_text), styles["ProjectPdfBody"]),
                Paragraph(
                    "<b>Roadmap</b><br/>Planejado: %s | Em execucao: %s | Bloqueado: %s"
                    % (planned_steps, doing_steps, blocked_steps),
                    styles["ProjectPdfBody"],
                ),
            ],
        ],
        colWidths=[88 * mm, 88 * mm],
    )
    info_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d9e1f0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#e3e8f2")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )

    pdf_title = f"ConnectMX - {project.name}"

    story = [
        Paragraph("Relatorio do Projeto", styles["ProjectPdfSubtitle"]),
        Paragraph(xml_escape(pdf_title), styles["ProjectPdfTitle"]),
        Paragraph(
            "Gerado em %s" % generated_at.strftime("%d/%m/%Y %H:%M"),
            styles["ProjectPdfMuted"],
        ),
        Spacer(1, 6),
        _project_catalog_pdf_bar(progress_pct, overdue_pct, accent),
        Spacer(1, 10),
        summary_table,
        Spacer(1, 10),
        Paragraph("Visao Geral", styles["ProjectPdfSection"]),
        Paragraph(xml_escape(project.description or "Sem descricao informada."), styles["ProjectPdfBody"]),
        Spacer(1, 8),
        info_table,
        Spacer(1, 10),
        Paragraph("Etapas do Roadmap", styles["ProjectPdfSection"]),
    ]

    if not roadmap_items:
        story.append(Paragraph("Nenhuma etapa cadastrada para este projeto.", styles["ProjectPdfBody"]))
    else:
        for index, item in enumerate(roadmap_items, start=1):
            overdue_payload = _roadmap_overdue_payload(item, today)
            subtasks = list(getattr(item, "prefetched_subtasks", []) or [])
            subtask_done = sum(1 for subtask in subtasks if subtask.is_done)
            responsible = (item.responsible.nameUser or item.responsible.username) if item.responsible_id else "-"
            period = " - "
            if item.start_date or item.end_date:
                start = item.start_date.strftime("%d/%m/%Y") if item.start_date else "-"
                end = item.end_date.strftime("%d/%m/%Y") if item.end_date else "-"
                period = f"{start} ate {end}"
            title_lines = [f"<b>{index}. {xml_escape(item.title)}</b>"]
            if item.description:
                title_lines.append(xml_escape(item.description))
            if subtasks:
                subtask_lines = []
                for subtask in subtasks:
                    mark = "OK" if subtask.is_done else "Pendente"
                    subtask_lines.append(f"{xml_escape(subtask.description)} ({mark})")
                checklist_text = "; ".join(subtask_lines)
            else:
                checklist_text = "Sem subtarefas"

            right_meta = (
                f"<b>Status:</b> {xml_escape(dict(ProjectRoadmapItem.STATUS_CHOICES).get(item.status, item.status))}<br/>"
                f"<b>Responsavel:</b> {xml_escape(responsible)}<br/>"
                f"<b>Periodo:</b> {xml_escape(period)}<br/>"
                f"<b>Checklist:</b> {subtask_done}/{len(subtasks)}<br/>"
                f"<b>Atraso:</b> {xml_escape(overdue_payload['overdue_label'] or 'No prazo')}"
            )

            stage_table = Table(
                [
                    [
                        Paragraph(
                            "<br/>".join(title_lines)
                            + "<br/><font color='#67748f'><b>Checklist:</b> "
                            + checklist_text
                            + "</font>",
                            styles["ProjectPdfStepBody"],
                        ),
                        Paragraph(
                            right_meta,
                            styles["ProjectPdfStepMuted"],
                        ),
                    ],
                ],
                colWidths=[114 * mm, 62 * mm],
            )
            stage_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, 0), accent_soft),
                        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#f5f7fc")),
                        ("BOX", (0, 0), (-1, -1), 0.8, accent_mid),
                        ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#e3e8f2")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            story.append(KeepTogether([stage_table, Spacer(1, 5)]))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=20 * mm,
        bottomMargin=14 * mm,
    )
    doc.title = pdf_title
    doc.author = "ConnectMX"

    def _on_page(canvas, document):
        canvas.saveState()
        page_width, page_height = A4
        canvas.setFillColor(accent)
        canvas.rect(0, page_height - 16 * mm, page_width, 16 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(document.leftMargin, page_height - 10.2 * mm, pdf_title[:72])
        canvas.setFont("Helvetica", 8.5)
        canvas.drawRightString(page_width - document.rightMargin, 9 * mm, f"Pagina {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buffer.getvalue()


@login_required
def projectCatalogPage(request):
    open_statuses = _project_catalog_open_statuses()
    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()
        return_query = request.POST.get("return_query") or ""
        project_id = (request.POST.get("project_id") or "").strip()

        if form_id in {"update_project_color", "edit_project_catalog", "conclude_project_catalog"} and project_id:
            project = get_object_or_404(Project, pk=project_id, status__in=open_statuses)

            if form_id == "update_project_color":
                project.color = _normalize_hex_color(request.POST.get("color"), project.color or "#343955")
                project.save(update_fields=["color"])
                return _project_catalog_redirect_response("projectCatalogPage", return_query)

            if form_id == "edit_project_catalog":
                valid_statuses = {choice[0] for choice in Project.STATUS_CHOICES}
                participant_ids = [int(pid) for pid in request.POST.getlist("participants_ids") if str(pid).isdigit()]
                status_value = (request.POST.get("status") or "").strip()

                project.name = (request.POST.get("name") or "").strip() or project.name
                project.description = (request.POST.get("description") or "").strip() or None
                project.developer_id = _parse_user_id(request.POST.get("developer_id"))
                project.status = status_value if status_value in valid_statuses else project.status
                project.color = _normalize_hex_color(request.POST.get("color"), project.color or "#343955")
                project.start_date = _parse_iso_date(request.POST.get("start_date"))
                project.end_date = _parse_iso_date(request.POST.get("end_date"))
                project.save()
                project.participants.set(participant_ids)
                return _project_catalog_redirect_response("projectCatalogPage", return_query)

            if form_id == "conclude_project_catalog":
                project.status = "done"
                project.save(update_fields=["status"])
                return _project_catalog_redirect_response("projectCatalogPage", return_query)

    name_q = (request.GET.get("q") or "").strip()
    responsible_id = (request.GET.get("responsible") or "").strip()
    participant_id = (request.GET.get("participant") or "").strip()
    date_from = _parse_iso_date(request.GET.get("date_from"))
    date_to = _parse_iso_date(request.GET.get("date_to"))

    projects_qs = (
        Project.objects.select_related("developer")
        .prefetch_related("participants")
        .filter(status__in=open_statuses)
    )

    if name_q:
        projects_qs = projects_qs.filter(Q(name__icontains=name_q) | Q(description__icontains=name_q))

    if responsible_id.isdigit():
        projects_qs = projects_qs.filter(developer_id=int(responsible_id))

    if participant_id.isdigit():
        projects_qs = projects_qs.filter(participants__id=int(participant_id))

    if date_from:
        projects_qs = projects_qs.filter(Q(end_date__isnull=True) | Q(end_date__gte=date_from))

    if date_to:
        projects_qs = projects_qs.filter(Q(start_date__isnull=True) | Q(start_date__lte=date_to))

    projects = list(
        projects_qs.distinct().annotate(
            roadmap_total=Count("roadmap_items", distinct=True),
            roadmap_done=Count("roadmap_items", filter=Q(roadmap_items__status="done"), distinct=True),
            kanban_cards_total=Count("kanban_cards", distinct=True),
        ).order_by("name")
    )
    _decorate_project_catalog_items(projects)
    filter_users = _project_catalog_user_options(open_statuses)
    project_edit_users = User.objects.filter(is_active=True).order_by("nameUser", "username")

    return render(
        request,
        "tiqueue/project_catalog.html",
        {
            "projects": projects,
            "page_title": "Projetos em aberto",
            "is_concluded_page": False,
            "filter_users": filter_users,
            "filters": {
                "q": name_q,
                "responsible": responsible_id,
                "participant": participant_id,
                "date_from": date_from.isoformat() if date_from else "",
                "date_to": date_to.isoformat() if date_to else "",
            },
            "project_edit_users": project_edit_users,
            "current_querystring": request.GET.urlencode(),
        },
    )


@login_required
def projectCatalogConcludedPage(request):
    projects = list(
        Project.objects.select_related("developer").prefetch_related("participants").filter(status="done").annotate(
            roadmap_total=Count("roadmap_items", distinct=True),
            roadmap_done=Count("roadmap_items", filter=Q(roadmap_items__status="done"), distinct=True),
            kanban_cards_total=Count("kanban_cards", distinct=True),
        ).order_by("name")
    )
    _decorate_project_catalog_items(projects)

    return render(
        request,
        "tiqueue/project_catalog.html",
        {
            "projects": projects,
            "page_title": "Projetos concluídos",
            "is_concluded_page": True,
            "filters": {
                "q": "",
                "responsible": "",
                "participant": "",
                "date_from": "",
                "date_to": "",
            },
            "current_querystring": "",
        },
    )


@login_required
@require_GET
def projectCatalogExportPdf(request, project_id):
    project = get_object_or_404(
        Project.objects.select_related("developer").prefetch_related("participants"),
        pk=project_id,
    )
    pdf_bytes = _build_project_catalog_pdf(project)
    filename = f"connectmx-projeto-{slugify(project.name) or project.id}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def manageHubTools(request):
    category_form = HubToolCategoryForm(prefix="cat")
    tool_form = HubToolForm(prefix="tool")

    if request.method == "POST":
        form_id = request.POST.get("form_id")

        if form_id == "category":
            category_form = HubToolCategoryForm(request.POST, prefix="cat")
            if category_form.is_valid():
                category_form.save()
            return redirect("manageHubTools")

        if form_id == "tool":
            tool_form = HubToolForm(request.POST, prefix="tool")
            if tool_form.is_valid():
                tool_form.save()
            return redirect("manageHubTools")

        if form_id == "edit_category":
            category_id = request.POST.get("category_id")
            name = (request.POST.get("name") or "").strip()
            sort_order = request.POST.get("sort_order")
            is_active = request.POST.get("is_active") == "on"
            if category_id and name:
                update_data = {"name": name, "is_active": is_active}
                if sort_order not in (None, ""):
                    update_data["sort_order"] = int(sort_order)
                HubToolCategory.objects.filter(pk=category_id).update(**update_data)
            return redirect("manageHubTools")

        if form_id == "edit_tool":
            tool_id = request.POST.get("tool_id")
            name = (request.POST.get("name") or "").strip()
            link = (request.POST.get("link") or "").strip()
            image_url = (request.POST.get("image_url") or "").strip()
            category_id = request.POST.get("category")
            sort_order = request.POST.get("sort_order")
            is_active = request.POST.get("is_active") == "on"
            if tool_id and name and link and category_id:
                update_data = {
                    "name": name,
                    "link": link,
                    "image_url": image_url or None,
                    "category_id": int(category_id),
                    "is_active": is_active,
                }
                if sort_order not in (None, ""):
                    update_data["sort_order"] = int(sort_order)
                HubTool.objects.filter(pk=tool_id).update(**update_data)
            return redirect("manageHubTools")

    categories = HubToolCategory.objects.all().order_by("sort_order", "name", "id")
    tools = HubTool.objects.select_related("category").order_by("category__sort_order", "sort_order", "name", "id")

    return render(
        request,
        "tiqueue/hub_tools_manage.html",
        {
            "category_form": category_form,
            "tool_form": tool_form,
            "categories": categories,
            "tools": tools,
        },
    )


@login_required
def manageMyHubTools(request):
    my_category_form = HubUserToolCategoryForm(prefix="mycat")
    my_tool_form = HubUserToolForm(prefix="mytool")
    my_tool_form.fields["category"].queryset = HubUserToolCategory.objects.filter(user=request.user).order_by(
        "sort_order", "name", "id"
    )

    if request.method == "POST":
        form_id = request.POST.get("form_id")

        if form_id == "my_category":
            my_category_form = HubUserToolCategoryForm(request.POST, prefix="mycat")
            if my_category_form.is_valid():
                cat = my_category_form.save(commit=False)
                cat.user = request.user
                cat.save()
            return redirect("manageMyHubTools")

        if form_id == "my_tool":
            my_tool_form = HubUserToolForm(request.POST, prefix="mytool")
            my_tool_form.fields["category"].queryset = HubUserToolCategory.objects.filter(user=request.user).order_by(
                "sort_order", "name", "id"
            )
            if my_tool_form.is_valid():
                my_tool = my_tool_form.save(commit=False)
                my_tool.user = request.user
                if my_tool.category_id:
                    my_tool.category_name = my_tool.category.name
                my_tool.save()
            return redirect("manageMyHubTools")

        if form_id == "edit_my_category":
            category_id = request.POST.get("category_id")
            name = (request.POST.get("name") or "").strip()
            sort_order = request.POST.get("sort_order")
            is_active = request.POST.get("is_active") == "on"
            if category_id and name:
                update_data = {"name": name, "is_active": is_active}
                if sort_order not in (None, ""):
                    update_data["sort_order"] = int(sort_order)
                HubUserToolCategory.objects.filter(pk=category_id, user=request.user).update(**update_data)
                HubUserTool.objects.filter(category_id=category_id, user=request.user).update(category_name=name)
            return redirect("manageMyHubTools")

        if form_id == "edit_my_tool":
            tool_id = request.POST.get("tool_id")
            name = (request.POST.get("name") or "").strip()
            link = (request.POST.get("link") or "").strip()
            image_url = (request.POST.get("image_url") or "").strip()
            category_id = request.POST.get("category")
            sort_order = request.POST.get("sort_order")
            is_active = request.POST.get("is_active") == "on"
            if tool_id and name and link and category_id:
                category_obj = HubUserToolCategory.objects.filter(pk=category_id, user=request.user).first()
                if category_obj:
                    update_data = {
                        "name": name,
                        "link": link,
                        "image_url": image_url or None,
                        "category_id": category_obj.id,
                        "category_name": category_obj.name,
                        "is_active": is_active,
                    }
                    if sort_order not in (None, ""):
                        update_data["sort_order"] = int(sort_order)
                    HubUserTool.objects.filter(pk=tool_id, user=request.user).update(**update_data)
            return redirect("manageMyHubTools")

    my_categories = HubUserToolCategory.objects.filter(user=request.user).order_by("sort_order", "name", "id")
    my_tools = HubUserTool.objects.filter(user=request.user).select_related("category").order_by(
        "category__sort_order", "category__name", "sort_order", "name", "id"
    )

    return render(
        request,
        "tiqueue/my_hub_tools_manage.html",
        {
            "my_category_form": my_category_form,
            "my_tool_form": my_tool_form,
            "my_categories": my_categories,
            "my_tools": my_tools,
        },
    )


@login_required
def knowledgeBasePage(request):
    return redirect("knowledgeEntriesPage")


@login_required
def knowledgeCategoriesPage(request):
    category_form = KnowledgeCategoryForm(prefix="kcat")

    if request.method == "POST":
        form_id = request.POST.get("form_id")

        if form_id == "category":
            category_form = KnowledgeCategoryForm(request.POST, prefix="kcat")
            if category_form.is_valid():
                category_form.save()
            return redirect("knowledgeCategoriesPage")

        if form_id == "edit_category":
            category_id = request.POST.get("category_id")
            name = (request.POST.get("name") or "").strip()
            description = (request.POST.get("description") or "").strip()
            sort_order = request.POST.get("sort_order")
            is_active = request.POST.get("is_active") == "on"

            if category_id and name:
                data = {
                    "name": name,
                    "description": description or None,
                    "is_active": is_active,
                }
                if sort_order not in (None, ""):
                    data["sort_order"] = int(sort_order)
                KnowledgeCategory.objects.filter(pk=category_id).update(**data)
            return redirect("knowledgeCategoriesPage")

    categories = KnowledgeCategory.objects.all().order_by("sort_order", "name", "id")

    return render(
        request,
        "tiqueue/knowledge_categories.html",
        {
            "category_form": category_form,
            "categories": categories,
        },
    )


@login_required
def knowledgeEntriesPage(request):
    entry_form = KnowledgeEntryForm(prefix="kentry")

    if request.method == "POST":
        form_id = request.POST.get("form_id")

        if form_id == "entry":
            entry_form = KnowledgeEntryForm(request.POST, prefix="kentry")
            if entry_form.is_valid():
                entry = entry_form.save(commit=False)
                entry.created_by = request.user
                entry.save()
                for f in request.FILES.getlist("attachments"):
                    KnowledgeEntryAttachment.objects.create(
                        entry=entry,
                        file=f,
                        original_name=getattr(f, "name", None) or None,
                    )
            return redirect("knowledgeEntriesPage")

        if form_id == "edit_entry":
            entry_id = request.POST.get("entry_id")
            if entry_id:
                entry = get_object_or_404(KnowledgeEntry, pk=entry_id)
                category_id = request.POST.get("category")
                title = (request.POST.get("title") or "").strip()
                trigger = (request.POST.get("trigger") or "").strip()
                description = (request.POST.get("description") or "").strip()

                if category_id and title and trigger and description:
                    entry.category_id = int(category_id)
                    entry.title = title
                    entry.trigger = trigger
                    entry.description = description
                    entry.impact = (request.POST.get("impact") or "").strip() or None
                    entry.workaround = (request.POST.get("workaround") or "").strip() or None
                    entry.root_cause = (request.POST.get("root_cause") or "").strip() or None
                    entry.resolution = (request.POST.get("resolution") or "").strip() or None
                    entry.tags = (request.POST.get("tags") or "").strip() or None
                    entry.is_resolved = request.POST.get("is_resolved") == "on"
                    entry.save()
            return redirect("knowledgeEntriesPage")

    categories = KnowledgeCategory.objects.filter(is_active=True).order_by("sort_order", "name", "id")

    return render(
        request,
        "tiqueue/knowledge_entries.html",
        {
            "entry_form": entry_form,
            "categories": categories,
        },
    )


@login_required
def knowledgeConsultPage(request):
    title_q = (request.GET.get("title") or "").strip()
    category_id = (request.GET.get("category") or "").strip()

    categories = KnowledgeCategory.objects.filter(is_active=True).order_by("sort_order", "name", "id")
    entries_qs = KnowledgeEntry.objects.select_related("category", "created_by").prefetch_related("attachments").all()

    if title_q:
        entries_qs = entries_qs.filter(title__icontains=title_q)
    if category_id:
        try:
            entries_qs = entries_qs.filter(category_id=int(category_id))
        except Exception:
            pass

    entries = entries_qs.order_by("-inserted_at", "-id")

    return render(
        request,
        "tiqueue/knowledge_consult.html",
        {
            "entries": entries,
            "categories": categories,
            "title_q": title_q,
            "selected_category": category_id,
        },
    )


@login_required
def knowledgeEntryDetailPage(request, entry_id):
    entry = get_object_or_404(
        KnowledgeEntry.objects.select_related("category", "created_by").prefetch_related("attachments"),
        pk=entry_id,
    )
    return render(request, "tiqueue/knowledge_entry_detail.html", {"entry": entry})


@login_required
def maxibotPage(request):
    return render(request, "tiqueue/maxibot.html")


def _maxibot_tokens(text):
    raw = (text or "").lower()
    words = re.findall(r"[a-z0-9à-úç]+", raw)
    stop = {
        "de", "da", "do", "das", "dos", "a", "o", "e", "em", "para", "por",
        "com", "sem", "na", "no", "nas", "nos", "um", "uma", "que", "como",
        "quando", "onde", "qual", "quais", "ser", "esta", "esse", "isso",
    }
    return {w for w in words if len(w) > 2 and w not in stop}


@login_required
@require_POST
def maxibotAsk(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "JSON invalido"}, status=400)

    question = (payload.get("question") or "").strip()
    if not question:
        return JsonResponse({"status": "error", "message": "Pergunta obrigatoria"}, status=400)

    q_tokens = _maxibot_tokens(question)
    if not q_tokens:
        return JsonResponse({"status": "ok", "answer": "Tente detalhar melhor a pergunta para eu localizar na base.", "sources": []})

    entries = list(
        KnowledgeEntry.objects.select_related("category")
        .filter(category__is_active=True)
        .order_by("-inserted_at")[:400]
    )

    ranked = []
    for entry in entries:
        title = entry.title or ""
        trigger = entry.trigger or ""
        description = entry.description or ""
        resolution = entry.resolution or ""
        workaround = entry.workaround or ""
        tags = entry.tags or ""
        category = entry.category.name if entry.category_id else ""

        field_weights = [
            (title, 4.0),
            (tags, 3.0),
            (category, 2.5),
            (trigger, 2.5),
            (resolution, 2.2),
            (workaround, 1.9),
            (description, 1.5),
        ]

        score = 0.0
        for text, weight in field_weights:
            tks = _maxibot_tokens(text)
            if not tks:
                continue
            common = len(q_tokens.intersection(tks))
            if common:
                score += common * weight

        if score > 0:
            ranked.append((score, entry))

    ranked.sort(key=lambda x: x[0], reverse=True)
    top = [e for _, e in ranked[:3]]

    if not top:
        return JsonResponse(
            {
                "status": "ok",
                "answer": "Nao encontrei resposta direta na Base de Conhecimento. Tente outra palavra-chave ou cadastre um novo registro.",
                "sources": [],
            }
        )

    bullets = []
    for e in top:
        base = e.resolution or e.workaround or e.description or e.trigger
        short = (base or "").strip()
        if len(short) > 220:
            short = short[:217] + "..."
        bullets.append(f"{e.title}: {short}")

    answer = "Possiveis respostas com base na Base de Conhecimento:\n- " + "\n- ".join(bullets)

    sources = [
        {
            "id": e.id,
            "category": e.category.name if e.category_id else "-",
            "title": e.title,
            "resolved": bool(e.is_resolved),
            "inserted_at": e.inserted_at.strftime("%d/%m/%Y %H:%M"),
            "url": reverse("knowledgeEntryDetailPage", args=[e.id]),
        }
        for e in top
    ]

    return JsonResponse({"status": "ok", "answer": answer, "sources": sources})


@login_required
def projectBoard(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    columns = _ensure_project_kanban_columns(project)

    # Keep Kanban synchronized with Roadmap items (one roadmap item -> one card).
    roadmap_items = ProjectRoadmapItem.objects.filter(project=project).order_by("sort_order", "id")
    for item in roadmap_items:
        _sync_roadmap_item_to_kanban(item)

    cards = list(ProjectKanbanCard.objects.filter(project=project).order_by("sort_order", "id"))
    cards_by_col = {c.id: [] for c in columns}
    for card in cards:
        cards_by_col.setdefault(card.column_id, []).append(card)

    columns_payload = []
    for col in columns:
        columns_payload.append({"col": col, "cards": cards_by_col.get(col.id, [])})

    return render(
        request,
        "tiqueue/project_board.html",
        {
            "project": project,
            "columns": columns_payload,
        },
    )


@login_required
@require_POST
def projectCardMove(request, card_id):
    card = get_object_or_404(ProjectKanbanCard, pk=card_id)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    column_id = payload.get("column_id")
    sort_order = payload.get("sort_order")
    if not column_id:
        return HttpResponseBadRequest("Missing column_id")

    column = get_object_or_404(ProjectKanbanColumn, pk=int(column_id), project=card.project)
    card.column = column
    if sort_order not in (None, ""):
        try:
            card.sort_order = int(sort_order)
        except Exception:
            pass
    card.save(update_fields=["column", "sort_order", "updated_at"])

    if card.roadmap_item_id:
        new_status = _roadmap_status_for_column(column)
        if card.roadmap_item.status != new_status:
            card.roadmap_item.status = new_status
            card.roadmap_item.save(update_fields=["status"])

    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def projectCardCreate(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    title = (payload.get("title") or "").strip()
    column_id = payload.get("column_id")
    if not title or not column_id:
        return JsonResponse({"status": "error", "message": "Titulo e coluna sao obrigatorios"}, status=400)

    column = get_object_or_404(ProjectKanbanColumn, pk=int(column_id), project=project)
    max_sort = (
        ProjectKanbanCard.objects.filter(project=project, column=column)
        .aggregate(models.Max("sort_order"))
        .get("sort_order__max")
    )
    next_sort = int(max_sort or 0) + 1

    card = ProjectKanbanCard.objects.create(project=project, column=column, title=title, sort_order=next_sort)
    return JsonResponse({"status": "ok", "id": card.id})


@login_required
@require_POST
def projectCardDelete(request, card_id):
    card = get_object_or_404(ProjectKanbanCard, pk=card_id)
    if card.roadmap_item_id:
        return JsonResponse(
            {"status": "error", "message": "Este card esta vinculado ao roadmap. Exclua a etapa no roadmap."},
            status=400,
        )
    card.delete()
    return JsonResponse({"status": "ok"})


@login_required
def projectRoadmapView(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    items = list(
        ProjectRoadmapItem.objects.filter(project=project)
        .select_related("responsible")
        .prefetch_related(Prefetch("subtasks", queryset=ProjectRoadmapSubtask.objects.order_by("sort_order", "id"), to_attr="prefetched_subtasks"))
        .order_by("sort_order", "id")
    )
    milestones = list(
        ProjectMilestone.objects.filter(project=project).select_related("anchor_item").order_by("sort_order", "target_date", "id")
    )

    # Build a timeline window
    starts = [i.start_date for i in items if i.start_date] + ([project.start_date] if project.start_date else [])
    ends = [i.end_date for i in items if i.end_date] + ([project.end_date] if project.end_date else [])
    milestone_dates = [milestone.target_date for milestone in milestones if milestone.target_date]
    starts += milestone_dates
    ends += milestone_dates
    today = timezone.localdate()
    if not starts:
        starts = [today]
    if not ends:
        ends = [today]
    window_start = min(starts)
    window_end = max(ends)
    if window_end < window_start:
        window_end = window_start
    total_days = max(1, (window_end - window_start).days + 1)

    def _pos(d):
        if not d:
            return 0
        return int(round(((d - window_start).days / total_days) * 100))

    def _span(a, b):
        if not a and not b:
            return (0, 2)
        s = a or b or window_start
        e = b or a or window_end
        if e < s:
            e = s
        left = _pos(s)
        right = _pos(e)
        width = max(2, right - left)
        return (left, width)

    visual_items = []
    for it in items:
        left, width = _span(it.start_date, it.end_date)
        visual_items.append(_serialize_roadmap_item(it) | {"left": left, "width": width})

    visual_milestones = []
    for milestone in milestones:
        left = _pos(milestone.target_date) if milestone.target_date else 0
        visual_milestones.append(_serialize_project_milestone(milestone, today=today) | {"left": left})

    done_count = sum(1 for i in items if i.status == "done")
    overdue_count = sum(1 for i in items if _roadmap_overdue_payload(i, today)["is_overdue"])
    total_count = len(items)
    progress_pct = int(round((done_count / total_count) * 100)) if total_count else 0
    overdue_pct = int(round((overdue_count / total_count) * 100)) if total_count else 0
    milestone_done_count = sum(1 for milestone in milestones if milestone.is_done)

    return render(
        request,
        "tiqueue/project_roadmap.html",
        {
        "project": project,
        "items": visual_items,
        "users": User.objects.order_by("nameUser", "username"),
        "window_start": window_start,
        "window_end": window_end,
        "done_count": done_count,
        "overdue_count": overdue_count,
            "milestones": visual_milestones,
            "milestone_done_count": milestone_done_count,
            "milestone_templates": _project_milestone_templates_payload(),
            "total_count": total_count,
            "progress_pct": progress_pct,
            "overdue_pct": overdue_pct,
        },
    )


@login_required
@require_POST
def projectMilestoneCreate(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    milestone_key = _normalize_project_milestone_key(payload.get("milestone_key"))
    template_defaults = ProjectMilestone.template_defaults(milestone_key)
    anchor_item_id = _parse_user_id(payload.get("anchor_item_id"))
    anchor_item = ProjectRoadmapItem.objects.filter(project=project, pk=anchor_item_id).first() if anchor_item_id else None
    milestone = ProjectMilestone.objects.create(
        project=project,
        anchor_item=anchor_item,
        milestone_key=milestone_key,
        title=template_defaults["title"],
        description=(payload.get("description") or "").strip() or None,
        target_date=_parse_iso_date(payload.get("target_date")),
        color=_normalize_hex_color(template_defaults["color"], "#5CD6A3"),
        sort_order=_project_milestone_next_sort(project, anchor_item=anchor_item),
    )
    return JsonResponse({"status": "ok", "milestone": _serialize_project_milestone(milestone)})


@login_required
@require_POST
def projectMilestoneUpdate(request, project_id, milestone_id):
    project = get_object_or_404(Project, pk=project_id)
    milestone = get_object_or_404(ProjectMilestone, pk=milestone_id, project=project)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    milestone_key = _normalize_project_milestone_key(payload.get("milestone_key"))
    template_defaults = ProjectMilestone.template_defaults(milestone_key)
    anchor_item_id = _parse_user_id(payload.get("anchor_item_id"))
    anchor_item = ProjectRoadmapItem.objects.filter(project=project, pk=anchor_item_id).first() if anchor_item_id else None
    anchor_changed = milestone.anchor_item_id != (anchor_item.id if anchor_item else None)

    milestone.anchor_item = anchor_item
    milestone.milestone_key = milestone_key
    milestone.title = template_defaults["title"]
    milestone.description = (payload.get("description") or "").strip() or None
    milestone.target_date = _parse_iso_date(payload.get("target_date"))
    milestone.color = _normalize_hex_color(template_defaults["color"], milestone.color or "#5CD6A3")
    if anchor_changed:
        milestone.sort_order = _project_milestone_next_sort(project, anchor_item=anchor_item)
    milestone.save(
        update_fields=["anchor_item", "milestone_key", "title", "description", "target_date", "color", "sort_order", "updated_at"]
    )
    return JsonResponse({"status": "ok", "milestone": _serialize_project_milestone(milestone)})


@login_required
@require_POST
def projectMilestoneToggle(request, project_id, milestone_id):
    project = get_object_or_404(Project, pk=project_id)
    milestone = get_object_or_404(ProjectMilestone, pk=milestone_id, project=project)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    requested_value = payload.get("is_done")
    if requested_value in (True, False):
        milestone.is_done = requested_value
    else:
        milestone.is_done = not milestone.is_done
    milestone.completed_at = timezone.now() if milestone.is_done else None
    milestone.save(update_fields=["is_done", "completed_at", "updated_at"])
    return JsonResponse({"status": "ok", "milestone": _serialize_project_milestone(milestone)})


@login_required
@require_POST
def projectMilestoneMove(request, project_id, milestone_id):
    project = get_object_or_404(Project, pk=project_id)
    milestone = get_object_or_404(ProjectMilestone, pk=milestone_id, project=project)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    direction = (payload.get("direction") or "").strip().lower()
    roadmap_item_ids = list(
        ProjectRoadmapItem.objects.filter(project=project).order_by("sort_order", "id").values_list("id", flat=True)
    )
    slots = [None] + roadmap_item_ids
    current_anchor = milestone.anchor_item_id if milestone.anchor_item_id in roadmap_item_ids else None
    current_index = slots.index(current_anchor)

    if direction == "left":
        target_index = max(0, current_index - 1)
    elif direction == "right":
        target_index = min(len(slots) - 1, current_index + 1)
    else:
        return JsonResponse({"status": "error", "message": "Direcao invalida"}, status=400)

    target_anchor_id = slots[target_index]
    target_anchor = ProjectRoadmapItem.objects.filter(project=project, pk=target_anchor_id).first() if target_anchor_id else None

    if milestone.anchor_item_id != (target_anchor.id if target_anchor else None):
        milestone.anchor_item = target_anchor
        milestone.sort_order = _project_milestone_next_sort(project, anchor_item=target_anchor)
        milestone.save(update_fields=["anchor_item", "sort_order", "updated_at"])

    return JsonResponse({"status": "ok", "milestone": _serialize_project_milestone(milestone)})


@login_required
@require_POST
def projectRoadmapItemCreate(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    title = (payload.get("title") or "").strip()
    if not title:
        return JsonResponse({"status": "error", "message": "Titulo obrigatorio"}, status=400)

    status = (payload.get("status") or "planned").strip() or "planned"
    start_date = _parse_iso_date(payload.get("start_date"))
    end_date = _parse_iso_date(payload.get("end_date"))
    description = (payload.get("description") or "").strip() or None
    responsible_id = _parse_user_id(payload.get("responsible_id"))
    responsible = User.objects.filter(pk=responsible_id).first() if responsible_id else None

    max_sort = (
        ProjectRoadmapItem.objects.filter(project=project)
        .aggregate(models.Max("sort_order"))
        .get("sort_order__max")
    )
    next_sort = int(max_sort or 0) + 1

    item = ProjectRoadmapItem.objects.create(
        project=project,
        responsible=responsible,
        title=title,
        description=description,
        status=status,
        start_date=start_date,
        end_date=end_date,
        sort_order=next_sort,
    )
    _sync_roadmap_item_to_kanban(item)
    return JsonResponse({"status": "ok", "id": item.id, "item": _serialize_roadmap_item(item)})


@login_required
@require_POST
def projectRoadmapItemUpdate(request, project_id, item_id):
    project = get_object_or_404(Project, pk=project_id)
    item = get_object_or_404(ProjectRoadmapItem, pk=item_id, project=project)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    title = (payload.get("title") or "").strip()
    if not title:
        return JsonResponse({"status": "error", "message": "Titulo obrigatorio"}, status=400)

    item.title = title
    item.responsible = User.objects.filter(pk=_parse_user_id(payload.get("responsible_id"))).first()
    item.description = (payload.get("description") or "").strip() or None
    item.status = (payload.get("status") or "planned").strip() or "planned"
    item.start_date = _parse_iso_date(payload.get("start_date"))
    item.end_date = _parse_iso_date(payload.get("end_date"))
    item.save(update_fields=["title", "responsible", "description", "status", "start_date", "end_date"])
    _sync_roadmap_item_to_kanban(item)
    return JsonResponse({"status": "ok", "item": _serialize_roadmap_item(item)})


@login_required
@require_POST
def projectRoadmapItemConclude(request, project_id, item_id):
    project = get_object_or_404(Project, pk=project_id)
    item = get_object_or_404(ProjectRoadmapItem, pk=item_id, project=project)
    if item.status != "done":
        item.status = "done"
        item.save(update_fields=["status"])
        _sync_roadmap_item_to_kanban(item)

    total = ProjectRoadmapItem.objects.filter(project=project).count()
    done = ProjectRoadmapItem.objects.filter(project=project, status="done").count()
    progress_pct = int(round((done / total) * 100)) if total else 0
    return JsonResponse(
        {
            "status": "ok",
            "done": done,
            "total": total,
            "progress_pct": progress_pct,
            "item": _serialize_roadmap_item(item),
        }
    )


@login_required
@require_POST
def projectRoadmapItemReopen(request, project_id, item_id):
    project = get_object_or_404(Project, pk=project_id)
    item = get_object_or_404(ProjectRoadmapItem, pk=item_id, project=project)
    if item.status == "done":
        item.status = "doing"
        item.save(update_fields=["status"])
        _sync_roadmap_item_to_kanban(item)

    total = ProjectRoadmapItem.objects.filter(project=project).count()
    done = ProjectRoadmapItem.objects.filter(project=project, status="done").count()
    progress_pct = int(round((done / total) * 100)) if total else 0
    return JsonResponse(
        {
            "status": "ok",
            "done": done,
            "total": total,
            "progress_pct": progress_pct,
            "item": _serialize_roadmap_item(item),
        }
    )


@login_required
@require_POST
def projectRoadmapSubtaskCreate(request, project_id, item_id):
    project = get_object_or_404(Project, pk=project_id)
    item = get_object_or_404(ProjectRoadmapItem, pk=item_id, project=project)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    description = (payload.get("description") or "").strip()
    if not description:
        return JsonResponse({"status": "error", "message": "Descricao obrigatoria"}, status=400)

    max_sort = item.subtasks.aggregate(models.Max("sort_order")).get("sort_order__max")
    subtask = ProjectRoadmapSubtask.objects.create(
        roadmap_item=item,
        description=description,
        sort_order=int(max_sort or 0) + 1,
    )
    return JsonResponse({"status": "ok", "subtask": _serialize_roadmap_subtask(subtask)})


@login_required
@require_POST
def projectRoadmapSubtaskUpdate(request, project_id, item_id, subtask_id):
    project = get_object_or_404(Project, pk=project_id)
    item = get_object_or_404(ProjectRoadmapItem, pk=item_id, project=project)
    subtask = get_object_or_404(ProjectRoadmapSubtask, pk=subtask_id, roadmap_item=item)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    description = (payload.get("description") or "").strip()
    if not description:
        return JsonResponse({"status": "error", "message": "Descricao obrigatoria"}, status=400)

    subtask.description = description
    subtask.save(update_fields=["description", "updated_at"])
    return JsonResponse({"status": "ok", "subtask": _serialize_roadmap_subtask(subtask)})


@login_required
@require_POST
def projectRoadmapSubtaskToggle(request, project_id, item_id, subtask_id):
    project = get_object_or_404(Project, pk=project_id)
    item = get_object_or_404(ProjectRoadmapItem, pk=item_id, project=project)
    subtask = get_object_or_404(ProjectRoadmapSubtask, pk=subtask_id, roadmap_item=item)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    requested_value = payload.get("is_done")
    if requested_value in (True, False):
        subtask.is_done = requested_value
    else:
        subtask.is_done = not subtask.is_done
    subtask.save(update_fields=["is_done", "updated_at"])
    return JsonResponse({"status": "ok", "subtask": _serialize_roadmap_subtask(subtask)})


@login_required
@require_POST
def projectRoadmapSubtaskDelete(request, project_id, item_id, subtask_id):
    project = get_object_or_404(Project, pk=project_id)
    item = get_object_or_404(ProjectRoadmapItem, pk=item_id, project=project)
    subtask = get_object_or_404(ProjectRoadmapSubtask, pk=subtask_id, roadmap_item=item)
    subtask.delete()
    return JsonResponse({"status": "ok", "subtask_id": subtask_id})

@login_required
def queueMainPage(request):
    pending_details_prefetch = Prefetch(
        "details",
        queryset=QueueTaskDetail.objects.filter(is_done=False).order_by("sort_order", "id"),
        to_attr="pending_details",
    )

    today = timezone.localdate()

    items = list(
        userQueue.objects.all()
        .select_related("task_type", "linked_project", "kanban_column")
        .annotate(
            detail_count=Count("details"),
            detail_done_count=Count("details", filter=Q(details__is_done=True)),
            detail_hours_total=Coalesce(
                Sum("details__duration_hours", filter=Q(details__is_done=True)),
                Value(0),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
            is_portal_origin=Exists(PortalDemand.objects.filter(linked_queue_item=OuterRef("pk"))),
        )
        .prefetch_related(pending_details_prefetch)
        .order_by("user_code", "-is_current", "n_queue_position", "n_register")
    )

    stats = {"attendant_count": 0, "total": len(items), "current": 0, "delayed": 0, "today": 0, "portal": 0}

    if not items:
        return render(request, "tiqueue/mainQueue.html", {"columns": [], "stats": stats})

    user_ids = sorted({i.user_code for i in items if i.user_code})
    users_by_id = {u.userId: u for u in User.objects.filter(userId__in=user_ids)}

    columns_map = {}
    user_order = []

    for item in items:
        key = item.user_code or "SEM_USUARIO"
        if key not in columns_map:
            user = users_by_id.get(key)
            columns_map[key] = {
                "user_code": key,
                "user_name": (user.nameUser if user and user.nameUser else (user.username if user else key)),
                "cards": [],
            }
            user_order.append(key)

        total = int(getattr(item, "detail_count", 0) or 0)
        done = int(getattr(item, "detail_done_count", 0) or 0)
        pct = int(round((done / total) * 100)) if total > 0 else 0
        pending = [d.description for d in (getattr(item, "pending_details", []) or []) if d.description]

        is_delayed = bool(item.d_predicted_date_end and item.d_predicted_date_end < today)
        is_due_today = bool(item.d_predicted_date_end and item.d_predicted_date_end == today)
        is_portal = bool(getattr(item, "is_portal_origin", False))

        if item.is_current:
            stats["current"] += 1
        if is_delayed:
            stats["delayed"] += 1
        if is_due_today:
            stats["today"] += 1
        if is_portal:
            stats["portal"] += 1

        columns_map[key]["cards"].append(
            {
                "id": item.n_register,
                "queue_position": item.n_queue_position or 0,
                "is_current": bool(item.is_current),
                "description": item.a_description or "-",
                "ticket": item.a_ticket or "",
                "project_name": (item.linked_project.name if item.linked_project else ""),
                "task_type_name": (item.task_type.name if item.task_type else ""),
                "kanban_column_name": (item.kanban_column.name if item.kanban_column else "Sem coluna"),
                "kanban_column_color": (item.kanban_column.color if item.kanban_column else "#61688c"),
                "pred_date_end": item.d_predicted_date_end,
                "pred_time_end": item.t_predicted_time_end,
                "pred_date_start": item.d_predicted_date_start,
                "pred_time_start": item.t_predicted_time_start,
                "real_date_end": item.d_real_date_end,
                "real_time_end": item.t_real_time_end,
                "real_date_start": item.d_real_date_start,
                "real_time_start": item.d_real_time_start,
                "progress_pct": pct,
                "details_total": total,
                "details_done": done,
                "pending_details": pending,
                "hours_total": float(getattr(item, "detail_hours_total", 0) or 0),
                "is_delayed": is_delayed,
                "is_due_today": is_due_today,
                "is_portal": is_portal,
            }
        )

    columns = [columns_map[k] for k in user_order]
    stats["attendant_count"] = len(columns)
    return render(request, "tiqueue/mainQueue.html", {"columns": columns, "stats": stats})


@login_required
def queueDemandDetailPage(request, item_id):
    item = get_object_or_404(
        userQueue.objects.select_related("task_group", "task_type", "linked_project", "linked_roadmap_item").prefetch_related(
            _queue_collaborators_prefetch()
        ),
        n_register=item_id,
    )
    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()

        if form_id == "notes":
            notes = (request.POST.get("a_notes") or "").strip()
            item.a_notes = notes or None
            item.save(update_fields=["a_notes"])
        elif form_id == "main":
            def _split_datetime_local(field_name):
                raw = (request.POST.get(field_name) or "").strip()
                if not raw:
                    return None, None
                parsed = None
                for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        parsed = datetime.strptime(raw, fmt)
                        break
                    except ValueError:
                        parsed = None
                if not parsed:
                    return None, None
                return parsed.date(), parsed.time().replace(second=0, microsecond=0)

            item.a_ticket = (request.POST.get("a_ticket") or "").strip() or None
            item.a_description = (request.POST.get("a_description") or "").strip() or None
            item.a_demand_detail = (request.POST.get("a_demand_detail") or "").strip() or None

            task_group_raw = (request.POST.get("task_group") or "").strip()
            task_type_raw = (request.POST.get("task_type") or "").strip()
            project_raw = (request.POST.get("linked_project") or "").strip()
            collaborator_ids = [
                int(value)
                for value in request.POST.getlist("extra_collaborators")
                if str(value).isdigit()
            ]

            item.task_group_id = int(task_group_raw) if task_group_raw.isdigit() else None
            item.task_type_id = int(task_type_raw) if task_type_raw.isdigit() else None
            item.n_type_group = item.task_group_id
            item.n_type_code = item.task_type_id
            item.linked_project_id = int(project_raw) if project_raw.isdigit() else None

            item.d_predicted_date_start, item.t_predicted_time_start = _split_datetime_local("predicted_start_dt")
            item.d_predicted_date_end, item.t_predicted_time_end = _split_datetime_local("predicted_end_dt")
            item.d_real_date_start, item.d_real_time_start = _split_datetime_local("real_start_dt")
            item.d_real_date_end, item.t_real_time_end = _split_datetime_local("real_end_dt")
            item.save()
            item.extra_collaborators.set(User.objects.filter(id__in=collaborator_ids))
        elif form_id == "subtask_add":
            description = (request.POST.get("subtask_description") or "").strip()
            if description:
                max_sort = (
                    QueueTaskDetail.objects.filter(queue_item=item)
                    .aggregate(v=models.Max("sort_order"))
                    .get("v")
                    or 0
                )
                QueueTaskDetail.objects.create(queue_item=item, description=description, sort_order=int(max_sort) + 1)
        elif form_id == "subtask_toggle":
            detail_id = request.POST.get("detail_id")
            detail = QueueTaskDetail.objects.filter(pk=detail_id, queue_item=item).first()
            if detail:
                detail.is_done = not detail.is_done
                if not detail.is_done:
                    detail.duration_hours = None
                detail.save(update_fields=["is_done", "duration_hours"])
        elif form_id == "subtask_delete":
            detail_id = request.POST.get("detail_id")
            QueueTaskDetail.objects.filter(pk=detail_id, queue_item=item).delete()

        return redirect("queueDemandDetailPage", item_id=item_id)

    details = list(QueueTaskDetail.objects.filter(queue_item=item).order_by("sort_order", "id"))
    done = sum(1 for d in details if d.is_done)
    total = len(details)
    progress_pct = int(round((done / total) * 100)) if total else 0
    task_groups = TaskGroup.objects.order_by("name")
    task_types = TaskType.objects.select_related("group").order_by("group__name", "name")
    projects = Project.objects.order_by("name")
    collaborator_users = User.objects.exclude(userId=item.user_code).order_by("nameUser", "username")

    def _datetime_local(date_value, time_value):
        if not date_value or not time_value:
            return ""
        return f"{date_value.strftime('%Y-%m-%d')}T{time_value.strftime('%H:%M')}"

    return render(
        request,
        "tiqueue/queue_demand_detail.html",
        {
            "item": item,
            "details": details,
            "detail_done": done,
            "detail_total": total,
            "progress_pct": progress_pct,
            "task_groups": task_groups,
            "task_types": task_types,
            "projects": projects,
            "collaborator_users": collaborator_users,
            "selected_collaborator_ids": [collaborator.id for collaborator in getattr(item, "extra_collaborators_prefetched", [])],
            "predicted_start_dt": _datetime_local(item.d_predicted_date_start, item.t_predicted_time_start),
            "predicted_end_dt": _datetime_local(item.d_predicted_date_end, item.t_predicted_time_end),
            "real_start_dt": _datetime_local(item.d_real_date_start, item.d_real_time_start),
            "real_end_dt": _datetime_local(item.d_real_date_end, item.t_real_time_end),
        },
    )


@login_required
def queueConcludedPage(request):
    completed = list(
        concludedTasks.objects.select_related("task_type", "task_group")
        .order_by("-d_conclusion_date", "-d_conclusion_time", "-n_register")
    )

    user_ids = [item.user_code for item in completed if item.user_code]
    users_by_id = {u.userId: u for u in User.objects.filter(userId__in=user_ids)}

    rows = []
    for item in completed:
        user = users_by_id.get(item.user_code)
        rows.append(
            {
                "id": item.n_register,
                "user_name": (user.nameUser if user and user.nameUser else (user.username if user else item.user_code)),
                "ticket": item.a_ticket or "-",
                "description": item.a_description or "-",
                "type_name": (item.task_type.name if item.task_type else "-"),
                "type_color": (item.task_type.color if item.task_type else "#61688c"),
                "rate": item.f_conclusion_rate,
                "pred_end_date": item.d_predicted_date_end,
                "pred_end_time": item.t_predicted_time_end,
                "real_end_date": item.d_real_date_end,
                "real_end_time": item.t_real_time_end,
                "done_date": item.d_conclusion_date,
                "done_time": item.d_conclusion_time,
            }
        )

    return render(request, "tiqueue/queueConcluded.html", {"rows": rows})


def _ensure_user_queue_kanban_columns(user):
    cols = list(
        UserQueueKanbanColumn.objects.filter(user=user, is_active=True).order_by("sort_order", "id")
    )
    if cols:
        return cols

    defaults = [
        ("Backlog", "#343955", 1),
        ("Em andamento", "#3a3f61", 2),
        ("Concluido", "#1f5a3a", 3),
    ]
    created = []
    for name, color, order in defaults:
        created.append(
            UserQueueKanbanColumn.objects.create(
                user=user,
                name=name,
                color=color,
                sort_order=order,
                is_active=True,
            )
        )
    return created


def _user_queue_custom_columns(user):
    return list(
        UserQueueCustomColumn.objects.filter(user=user)
        .prefetch_related(
            Prefetch(
                "options",
                queryset=UserQueueCustomColumnOption.objects.filter(is_active=True).order_by("sort_order", "id"),
            )
        )
        .order_by("sort_order", "id")
    )


def _build_queue_option_value(label, existing_values):
    base = slugify(unicodedata.normalize("NFKD", str(label or "")))[:32] or "opcao"
    candidate = base
    counter = 2
    while candidate in existing_values:
        suffix = f"-{counter}"
        candidate = f"{base[: max(1, 40 - len(suffix))]}{suffix}"
        counter += 1
    return candidate[:40]


def _serialize_queue_field_option(option, usage_count=0, affected_count=None, can_delete=True, remove_reason=""):
    return {
        "id": option.id,
        "value": option.value,
        "label": option.label,
        "color": option.color or "#61688c",
        "usage_count": int(usage_count or 0),
        "affected_count": int(usage_count if affected_count is None else affected_count),
        "can_delete": bool(can_delete),
        "remove_reason": remove_reason or "",
    }


def _serialize_queue_custom_column_option(option, usage_count=0, can_delete=True, remove_reason=""):
    return {
        "id": option.id,
        "value": option.value,
        "label": option.label,
        "color": option.color or "#61688c",
        "usage_count": int(usage_count or 0),
        "can_delete": bool(can_delete),
        "remove_reason": remove_reason or "",
    }


def _serialize_queue_custom_column(column, option_usage_map=None):
    option_usage_map = option_usage_map or {}
    options = list(getattr(column, "_prefetched_objects_cache", {}).get("options", []))
    return {
        "id": column.id,
        "name": column.name,
        "field_type": column.field_type,
        "color": column.color or "#61688c",
        "option_count": len(options),
        "options": [
            _serialize_queue_custom_column_option(
                opt,
                usage_count=option_usage_map.get((column.id, opt.value), 0),
            )
            for opt in options
        ],
    }


def _ensure_user_queue_field_options(user, field_key):
    options = list(
        UserQueueFieldOption.objects.filter(user=user, field_key=field_key, is_active=True).order_by("sort_order", "id")
    )
    if options:
        return options

    defaults = userQueue.default_field_options(field_key)
    created = []
    for index, (value, label, color) in enumerate(defaults, start=1):
        created.append(
            UserQueueFieldOption.objects.create(
                user=user,
                field_key=field_key,
                value=value,
                label=label,
                color=color,
                sort_order=index,
                is_active=True,
            )
        )
    return created


def _queue_field_option_payload(user):
    priority_options = _ensure_user_queue_field_options(user, userQueue.FIELD_PRIORITY)
    effort_options = _ensure_user_queue_field_options(user, userQueue.FIELD_EFFORT)

    def _usage_maps(field_key):
        current_rows = (
            userQueue.objects.filter(user_code=user.userId)
            .exclude(**{field_key: ""})
            .values(field_key)
            .annotate(total=Count("n_register"))
        )
        concluded_rows = (
            concludedTasks.objects.filter(user_code=user.userId)
            .exclude(**{field_key: ""})
            .values(field_key)
            .annotate(total=Count("n_register"))
        )
        current_map = {str(row[field_key] or ""): int(row["total"] or 0) for row in current_rows}
        concluded_map = {str(row[field_key] or ""): int(row["total"] or 0) for row in concluded_rows}
        return current_map, concluded_map

    priority_current_map, priority_concluded_map = _usage_maps(userQueue.FIELD_PRIORITY)
    effort_current_map, effort_concluded_map = _usage_maps(userQueue.FIELD_EFFORT)

    return {
        "priority_options": [
            _serialize_queue_field_option(
                opt,
                usage_count=priority_current_map.get(opt.value, 0),
                affected_count=priority_current_map.get(opt.value, 0) + priority_concluded_map.get(opt.value, 0),
                can_delete=len(priority_options) > 1,
                remove_reason="" if len(priority_options) > 1 else "Mantenha pelo menos uma opcao ativa.",
            )
            for opt in priority_options
        ],
        "effort_options": [
            _serialize_queue_field_option(
                opt,
                usage_count=effort_current_map.get(opt.value, 0),
                affected_count=effort_current_map.get(opt.value, 0) + effort_concluded_map.get(opt.value, 0),
                can_delete=len(effort_options) > 1,
                remove_reason="" if len(effort_options) > 1 else "Mantenha pelo menos uma opcao ativa.",
            )
            for opt in effort_options
        ],
    }


def _queue_property_payload(user, kanban_columns=None, custom_columns=None):
    kanban_columns = kanban_columns or _ensure_user_queue_kanban_columns(user)
    custom_columns = custom_columns or _user_queue_custom_columns(user)
    field_payload = _queue_field_option_payload(user)
    status_usage_rows = (
        userQueue.objects.filter(user_code=user.userId, kanban_column__isnull=False)
        .values("kanban_column_id")
        .annotate(total=Count("n_register"))
    )
    status_usage_map = {int(row["kanban_column_id"]): int(row["total"] or 0) for row in status_usage_rows}
    custom_option_usage_rows = (
        UserQueueCustomValue.objects.filter(column__user=user)
        .exclude(value__isnull=True)
        .exclude(value="")
        .values("column_id", "value")
        .annotate(total=Count("id"))
    )
    custom_option_usage_map = {
        (int(row["column_id"]), str(row["value"] or "")): int(row["total"] or 0)
        for row in custom_option_usage_rows
    }
    active_status_count = len(kanban_columns)
    return {
        **field_payload,
        "status_options": [
            {
                "id": col.id,
                "value": str(col.id),
                "name": col.name,
                "label": col.name,
                "color": col.color or "#61688c",
                "usage_count": status_usage_map.get(col.id, 0),
                "can_delete": active_status_count > 1 and status_usage_map.get(col.id, 0) == 0,
                "remove_reason": (
                    "Nao e possivel remover um status com tarefas vinculadas."
                    if status_usage_map.get(col.id, 0) > 0
                    else ("Mantenha pelo menos um status ativo." if active_status_count <= 1 else "")
                ),
            }
            for col in kanban_columns
        ],
        "custom_columns": [
            _serialize_queue_custom_column(col, option_usage_map=custom_option_usage_map)
            for col in custom_columns
        ],
    }


def _resolve_queue_field_render(option_map, fallback_map, value):
    chosen = option_map.get(value or "") or fallback_map.get(value or "")
    if chosen:
        return chosen
    normalized = str(value or "").strip()
    return {
        "value": normalized or "",
        "label": normalized or "-",
        "color": "#61688c",
    }


def _normalize_user_queue_field_value(user, field_key, candidate):
    allowed = [opt.value for opt in _ensure_user_queue_field_options(user, field_key)]
    normalized = str(candidate or "").strip()
    if normalized and normalized in allowed:
        return normalized
    if allowed:
        return allowed[0]
    defaults = userQueue.default_field_options(field_key)
    return defaults[0][0] if defaults else ""


def _format_custom_column_value(column, raw_value):
    value = str(raw_value or "").strip()
    field_type = column.field_type or UserQueueCustomColumn.FIELD_TEXT

    if field_type == UserQueueCustomColumn.FIELD_SELECT:
        option_map = {
            opt.value: opt for opt in list(getattr(column, "_prefetched_objects_cache", {}).get("options", []))
        }
        selected = option_map.get(value)
        return {
            "raw_value": value,
            "display": (selected.label if selected else value or "-"),
            "display_color": (selected.color if selected else column.color or "#61688c"),
            "is_checked": False,
        }

    if field_type == UserQueueCustomColumn.FIELD_CHECKBOX:
        is_checked = value.lower() in {"1", "true", "yes", "sim", "on"}
        return {
            "raw_value": "1" if is_checked else "",
            "display": "Concluido" if is_checked else "-",
            "display_color": column.color or "#00bf63",
            "is_checked": is_checked,
        }

    if field_type == UserQueueCustomColumn.FIELD_DATE:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date() if value else None
        except ValueError:
            parsed = None
        return {
            "raw_value": value,
            "display": parsed.strftime("%d/%m/%Y") if parsed else (value or "-"),
            "display_color": column.color or "#61688c",
            "is_checked": False,
        }

    return {
        "raw_value": value,
        "display": value or "-",
        "display_color": column.color or "#61688c",
        "is_checked": False,
    }


def _normalize_custom_column_value(column, raw_value):
    value = str(raw_value or "").strip()
    field_type = column.field_type or UserQueueCustomColumn.FIELD_TEXT

    if field_type == UserQueueCustomColumn.FIELD_SELECT:
        if not value:
            return ""
        allowed_values = {
            opt.value for opt in list(getattr(column, "_prefetched_objects_cache", {}).get("options", []))
        }
        if value not in allowed_values:
            raise ValueError("Opcao invalida para esta coluna.")
        return value[:40]

    if field_type == UserQueueCustomColumn.FIELD_NUMBER:
        if not value:
            return ""
        normalized = value.replace(",", ".")
        try:
            Decimal(normalized)
        except Exception as exc:
            raise ValueError("Informe um numero valido.") from exc
        return normalized[:250]

    if field_type == UserQueueCustomColumn.FIELD_DATE:
        if not value:
            return ""
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("Informe uma data valida.") from exc
        return parsed.isoformat()

    if field_type == UserQueueCustomColumn.FIELD_CHECKBOX:
        return "1" if value.lower() in {"1", "true", "yes", "sim", "on"} else ""

    return value[:250]


def _queue_collaborator_display_name(user_obj):
    if not user_obj:
        return "-"
    return (user_obj.nameUser or user_obj.username or user_obj.userId or "-").strip() or "-"


def _queue_collaborator_initials(user_obj):
    name = _queue_collaborator_display_name(user_obj)
    parts = [part for part in re.split(r"\s+", name) if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return f"{parts[0][:1]}{parts[-1][:1]}".upper()


def _serialize_queue_collaborator(user_obj):
    return {
        "id": user_obj.id,
        "name": _queue_collaborator_display_name(user_obj),
        "initials": _queue_collaborator_initials(user_obj),
        "user_id": user_obj.userId or "",
    }


def _decorate_queue_items(items, user, custom_columns):
    priority_options = _ensure_user_queue_field_options(user, userQueue.FIELD_PRIORITY)
    effort_options = _ensure_user_queue_field_options(user, userQueue.FIELD_EFFORT)
    priority_map = {opt.value: _serialize_queue_field_option(opt) for opt in priority_options}
    effort_map = {opt.value: _serialize_queue_field_option(opt) for opt in effort_options}
    default_priority_map = userQueue.default_field_option_map(userQueue.FIELD_PRIORITY)
    default_effort_map = userQueue.default_field_option_map(userQueue.FIELD_EFFORT)

    for item in items or []:
        item.priority_render = _resolve_queue_field_render(priority_map, default_priority_map, item.priority_level)
        item.effort_render = _resolve_queue_field_render(effort_map, default_effort_map, item.estimated_effort_level)
        collaborators = list(getattr(item, "extra_collaborators_prefetched", None) or item.extra_collaborators.all())
        item.extra_collaborators_render = [_serialize_queue_collaborator(collaborator) for collaborator in collaborators]
        item.extra_collaborators_display = ", ".join(
            collaborator["name"] for collaborator in item.extra_collaborators_render
        )


def _serialize_user_queue_saved_view(saved_view):
    return {
        "id": saved_view.id,
        "name": saved_view.name,
        "filters": saved_view.filters_json or {},
    }


def _queue_collaborators_prefetch():
    return Prefetch(
        "extra_collaborators",
        queryset=User.objects.only("id", "userId", "nameUser", "username").order_by("nameUser", "username"),
        to_attr="extra_collaborators_prefetched",
    )


def _normalize_user_queue_saved_view_filters(filters):
    if not isinstance(filters, dict):
        filters = {}

    allowed_quick_filters = {"", "today", "delayed", "no_deadline", "high_priority", "current"}

    def _string(name, max_len=120):
        return str(filters.get(name) or "").strip()[:max_len]

    def _bool(name):
        return bool(filters.get(name))

    def _date_string(name):
        value = _string(name, 10)
        if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            return value
        return ""

    type_value = _string("type", 20)
    if type_value and not type_value.isdigit():
        type_value = ""

    workspace_view = _string("workspace_view", 20).lower()
    if workspace_view not in {"demands", "kanban", "gantt"}:
        workspace_view = "demands"

    quick_filter = _string("quick_filter", 40).lower()
    if quick_filter not in allowed_quick_filters:
        quick_filter = ""

    return {
        "description": _string("description", 120),
        "ticket": _string("ticket", 80),
        "type": type_value,
        "date_from": _date_string("date_from"),
        "date_to": _date_string("date_to"),
        "today_only": _bool("today_only"),
        "operation_mode": _bool("operation_mode"),
        "quick_filter": quick_filter,
        "workspace_view": workspace_view,
        "layout_modern": _bool("layout_modern"),
        "focus_mode": _bool("focus_mode"),
    }


def _attach_queue_custom_values(items, custom_columns):
    if not items:
        return
    item_ids = [i.n_register for i in items]
    col_ids = [c.id for c in custom_columns]
    values_map = {
        (row["queue_item_id"], row["column_id"]): (row["value"] or "")
        for row in UserQueueCustomValue.objects.filter(queue_item_id__in=item_ids, column_id__in=col_ids)
        .values("queue_item_id", "column_id", "value")
    }
    for item in items:
        item.custom_values_render = [
            {
                "column_id": col.id,
                "column_name": col.name,
                "column_type": col.field_type,
                "column_color": col.color or "#61688c",
                **_format_custom_column_value(col, values_map.get((item.n_register, col.id), "")),
            }
            for col in custom_columns
        ]


def _build_queue_user_context(request):
    user = request.user.userId
    kanban_columns = _ensure_user_queue_kanban_columns(request.user)
    custom_columns = _user_queue_custom_columns(request.user)
    collaborator_users = list(
        User.objects.exclude(pk=request.user.pk).order_by("nameUser", "username")
    )
    queue_property_payload = _queue_property_payload(request.user, kanban_columns=kanban_columns, custom_columns=custom_columns)
    saved_views = list(UserQueueSavedView.objects.filter(user=request.user).order_by("name", "id"))
    default_col_id = kanban_columns[0].id if kanban_columns else None

    # Backfill defensivo para dados legados:
    # quando kanban_sort_order estiver vazio/0, usa a posicao da fila.
    with transaction.atomic():
        legacy_rows = userQueue.objects.filter(user_code=user).filter(
            Q(kanban_sort_order__isnull=True) | Q(kanban_sort_order=0)
        )
        for row in legacy_rows.only("n_register", "n_queue_position"):
            row.kanban_sort_order = int(row.n_queue_position or row.n_register or 0)
            row.save(update_fields=["kanban_sort_order"])

    try:
        queue_working = list(
            userQueue.objects.filter(is_current=True, user_code=user)
            .select_related("task_type", "linked_project", "kanban_column")
            .prefetch_related(_queue_collaborators_prefetch())
            .annotate(
                detail_count=Count("details"),
                detail_done_count=Count("details", filter=Q(details__is_done=True)),
                detail_hours_total=Coalesce(
                    Sum("details__duration_hours", filter=Q(details__is_done=True)),
                    Value(0),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                ),
            )
            .order_by('n_queue_position')
        )
        queue_data = list(
            userQueue.objects.filter(is_current=False, user_code=user)
            .select_related("task_type", "linked_project", "kanban_column")
            .prefetch_related(_queue_collaborators_prefetch())
            .annotate(
                detail_count=Count("details"),
                detail_done_count=Count("details", filter=Q(details__is_done=True)),
                detail_hours_total=Coalesce(
                    Sum("details__duration_hours", filter=Q(details__is_done=True)),
                    Value(0),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                ),
            )
            .order_by('n_queue_position')
        )
    except ObjectDoesNotExist:
        queue_data = 0
        queue_working = 0
    if queue_data and custom_columns:
        _attach_queue_custom_values(queue_data, custom_columns)
    if queue_working and custom_columns:
        _attach_queue_custom_values(queue_working, custom_columns)
    _decorate_queue_items(queue_data or [], request.user, custom_columns)
    _decorate_queue_items(queue_working or [], request.user, custom_columns)

    form = UserQueueCreateForm(request.POST or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        selected_template_id = (request.POST.get("demand_template_id") or "").strip()
        service.userQueueSaveItem(request, form.cleaned_data)

        created_item = (
            userQueue.objects.filter(user_code=request.user.userId)
            .order_by("-n_queue_position", "-n_register")
            .first()
        )
        if created_item and selected_template_id.isdigit():
            template = (
                DemandTemplate.objects.filter(pk=int(selected_template_id), is_active=True)
                .select_related("task_group", "task_type", "linked_project")
                .prefetch_related("details")
                .first()
            )
            if template:
                changed_fields = []
                if template.task_group_id:
                    created_item.task_group_id = template.task_group_id
                    created_item.n_type_group = template.task_group_id
                    changed_fields += ["task_group", "n_type_group"]
                if template.task_type_id:
                    created_item.task_type_id = template.task_type_id
                    created_item.n_type_code = template.task_type_id
                    changed_fields += ["task_type", "n_type_code"]
                if template.linked_project_id:
                    created_item.linked_project_id = template.linked_project_id
                    changed_fields.append("linked_project")

                now_dt = timezone.localtime()

                start_offset = float(template.predicted_start_offset_hours or 0)
                end_offset = float(template.predicted_end_offset_hours or 0)
                if start_offset:
                    start_dt = now_dt + timedelta(hours=start_offset)
                    created_item.d_predicted_date_start = start_dt.date()
                    created_item.t_predicted_time_start = start_dt.time().replace(second=0, microsecond=0)
                    changed_fields += ["d_predicted_date_start", "t_predicted_time_start"]
                if end_offset:
                    end_dt = now_dt + timedelta(hours=end_offset)
                    created_item.d_predicted_date_end = end_dt.date()
                    created_item.t_predicted_time_end = end_dt.time().replace(second=0, microsecond=0)
                    changed_fields += ["d_predicted_date_end", "t_predicted_time_end"]

                if changed_fields:
                    created_item.save(update_fields=list(dict.fromkeys(changed_fields)))

                max_sort = 0
                for detail in template.details.all():
                    max_sort += 1
                    QueueTaskDetail.objects.create(
                        queue_item=created_item,
                        description=detail.description,
                        sort_order=max_sort,
                    )
        return {"redirect": True}

    task_types = TaskType.objects.select_related("group").order_by("group__name", "name")
    task_groups = TaskGroup.objects.order_by("name")
    projects = Project.objects.order_by("name")
    demand_templates = (
        DemandTemplate.objects.filter(is_active=True)
        .select_related("task_group", "task_type", "linked_project")
        .prefetch_related("details")
        .order_by("name")
    )

    templates_payload = []
    for tpl in demand_templates:
        templates_payload.append(
            {
                "id": tpl.id,
                "name": tpl.name,
                "description": tpl.description or "",
                "task_group_id": tpl.task_group_id,
                "task_type_id": tpl.task_type_id,
                "linked_project_id": tpl.linked_project_id,
                "predicted_start_offset_hours": float(tpl.predicted_start_offset_hours or 0),
                "predicted_end_offset_hours": float(tpl.predicted_end_offset_hours or 0),
                "details": [
                    {"description": d.description, "sort_order": int(d.sort_order or 0)}
                    for d in tpl.details.all()
                ],
            }
        )

    return {
        'form': form,
        'queue_data': queue_data,
        'queue_working': queue_working,
        'task_types': task_types,
        'task_groups': task_groups,
        'projects': projects,
        'demand_templates': demand_templates,
        'demand_templates_payload': templates_payload,
        'my_kanban_columns': kanban_columns,
        'my_kanban_default_col_id': default_col_id,
        'queue_custom_columns': custom_columns,
        'queue_saved_views': saved_views,
        'queue_saved_views_payload': [_serialize_user_queue_saved_view(v) for v in saved_views],
        'queue_property_payload': queue_property_payload,
        'queue_collaborator_users': collaborator_users,
    }


@login_required
def queueUserPage(request):
    context = _build_queue_user_context(request)
    if context.get("redirect"):
        return redirect('queueUserPage')
    context["kanban_lab"] = True
    return render(request, 'tiqueue/userQueue.html', context)


@login_required
def queueUserPageKanbanLab(request):
    return redirect("queueUserPage")


@login_required
@require_POST
def queueUserPropertyPayloadData(request):
    kanban_columns = _ensure_user_queue_kanban_columns(request.user)
    custom_columns = _user_queue_custom_columns(request.user)
    return JsonResponse(_queue_property_payload(request.user, kanban_columns=kanban_columns, custom_columns=custom_columns))


@login_required
@require_POST
def upQueuePosition(request, id):
    service.serviceUserQueueUpItem(request, id)
    return JsonResponse({'status':'ok'})

@login_required
@require_POST
def dropQueuePosition(request, id):
    service.serviceUserQueueDropItem(request, id)
    return JsonResponse({'status':'ok'})

@login_required
@require_POST
def deleteQueueItem(request, id):
    service.serviceDeleteQueueItem(request, id)
    return JsonResponse({'status':'ok'})

@login_required
@require_POST
def duplicateQueueItem(request, id):
    original = get_object_or_404(
        userQueue.objects.prefetch_related(_queue_collaborators_prefetch()),
        n_register=id,
        user_code=request.user.userId,
    )

    user_code = request.user.userId
    next_position = (
        userQueue.objects.filter(user_code=user_code).aggregate(max_pos=models.Max("n_queue_position")).get("max_pos") or 0
    ) + 1

    with transaction.atomic():
        cloned = userQueue.objects.create(
            user_code=user_code,
            a_ticket=original.a_ticket,
            f_conclusion_rate=Decimal("0.00"),
            n_status_code=original.n_status_code,
            a_description=original.a_description,
            a_demand_detail=original.a_demand_detail,
            a_notes=original.a_notes,
            priority_level=original.priority_level,
            estimated_effort_level=original.estimated_effort_level,
            n_type_group=original.n_type_group,
            n_type_code=original.n_type_code,
            task_group=original.task_group,
            task_type=original.task_type,
            kanban_column=original.kanban_column,
            linked_project=original.linked_project,
            # New recurring task starts detached from old roadmap item.
            linked_roadmap_item=None,
            d_predicted_date_start=original.d_predicted_date_start,
            d_predicted_date_end=original.d_predicted_date_end,
            t_predicted_time_start=original.t_predicted_time_start,
            t_predicted_time_end=original.t_predicted_time_end,
            f_total_predicted_time=original.f_total_predicted_time,
            d_real_date_start=None,
            d_real_date_end=None,
            d_real_time_start=None,
            t_real_time_end=None,
            f_total_real_time=None,
            f_predicted_real_diference=None,
            n_queue_position=next_position,
            kanban_sort_order=next_position,
            is_current=False,
        )
        cloned.extra_collaborators.set(getattr(original, "extra_collaborators_prefetched", []))

    return JsonResponse({"status": "ok", "new_id": cloned.n_register})

@login_required
@require_POST
def endQueueItem(request, id):
    service.serviceEndQueueItem(request, id)
    return JsonResponse({'status':'ok'})

@login_required
def listQueueUpdate(request):
    user = request.user.userId
    custom_columns = _user_queue_custom_columns(request.user)

    try:
        queue_working = list(
            userQueue.objects.filter(is_current=True, user_code=user)
            .select_related("task_type", "linked_project", "kanban_column")
            .prefetch_related(_queue_collaborators_prefetch())
            .annotate(
                detail_count=Count("details"),
                detail_done_count=Count("details", filter=Q(details__is_done=True)),
                detail_hours_total=Coalesce(
                    Sum("details__duration_hours", filter=Q(details__is_done=True)),
                    Value(0),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                ),
            )
            .order_by('n_queue_position')
        )
        queue_data = list(
            userQueue.objects.filter(is_current=False, user_code=user)
            .select_related("task_type", "linked_project", "kanban_column")
            .prefetch_related(_queue_collaborators_prefetch())
            .annotate(
                detail_count=Count("details"),
                detail_done_count=Count("details", filter=Q(details__is_done=True)),
                detail_hours_total=Coalesce(
                    Sum("details__duration_hours", filter=Q(details__is_done=True)),
                    Value(0),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                ),
            )
            .order_by('n_queue_position')
        )
    except ObjectDoesNotExist:
        queue_data = 0
        queue_working = 0
    if queue_data and custom_columns:
        _attach_queue_custom_values(queue_data, custom_columns)
    if queue_working and custom_columns:
        _attach_queue_custom_values(queue_working, custom_columns)
    _decorate_queue_items(queue_data or [], request.user, custom_columns)
    _decorate_queue_items(queue_working or [], request.user, custom_columns)
    return render(
        request,
        'partials/queue.html',
        {'queue_data': queue_data, 'queue_working': queue_working, 'queue_custom_columns': custom_columns},
    )

def listCreateStatus(request):

    return render(request, 'tiqueue/statusCreate.html')

@login_required
def manageTaskTypes(request):
    group_form = TaskGroupForm(prefix="group")
    type_form = TaskTypeForm(prefix="type")

    if request.method == "POST":
        if request.POST.get("form_id") == "group":
            group_form = TaskGroupForm(request.POST, prefix="group")
            if group_form.is_valid():
                group_form.save()
        elif request.POST.get("form_id") == "type":
            type_form = TaskTypeForm(request.POST, prefix="type")
            if type_form.is_valid():
                type_form.save()
        elif request.POST.get("form_id") == "edit_group":
            group_id = request.POST.get("group_id")
            name = (request.POST.get("name") or "").strip()
            if group_id and name:
                TaskGroupForm._meta.model.objects.filter(pk=group_id).update(name=name)
        elif request.POST.get("form_id") == "edit_type":
            type_id = request.POST.get("type_id")
            name = (request.POST.get("name") or "").strip()
            group_id = request.POST.get("group")
            color = (request.POST.get("color") or "").strip()
            if type_id and name and group_id:
                update_data = {"name": name, "group_id": group_id}
                if color:
                    update_data["color"] = color
                TaskTypeForm._meta.model.objects.filter(pk=type_id).update(**update_data)

    groups_qs = TaskGroupForm._meta.model.objects.all().order_by("name")
    types_qs = TaskTypeForm._meta.model.objects.select_related("group").order_by("group__name", "name")

    selected_group_id = (request.GET.get("group") or "").strip()
    if selected_group_id:
        try:
            types_qs = types_qs.filter(group_id=int(selected_group_id))
        except Exception:
            selected_group_id = ""

    groups_paginator = Paginator(groups_qs, 8)
    types_paginator = Paginator(types_qs, 12)
    groups_page_obj = groups_paginator.get_page(request.GET.get("groups_page") or 1)
    types_page_obj = types_paginator.get_page(request.GET.get("types_page") or 1)

    return render(
        request,
        "tiqueue/task_types.html",
        {
            "group_form": group_form,
            "type_form": type_form,
            "groups": groups_page_obj.object_list,
            "types": types_page_obj.object_list,
            "groups_page_obj": groups_page_obj,
            "types_page_obj": types_page_obj,
            "all_groups": groups_qs,
            "selected_group_id": selected_group_id,
        },
    )


@login_required
def manageDemandTemplates(request):
    template_form = DemandTemplateForm(prefix="tpl")
    detail_form = DemandTemplateDetailForm(prefix="det")

    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()
        if form_id == "template":
            template_form = DemandTemplateForm(request.POST, prefix="tpl")
            if template_form.is_valid():
                template_form.save()
                return redirect("manageDemandTemplates")
        elif form_id == "detail":
            detail_form = DemandTemplateDetailForm(request.POST, prefix="det")
            if detail_form.is_valid():
                detail_form.save()
                return redirect("manageDemandTemplates")
        elif form_id == "template_toggle":
            template_id = request.POST.get("template_id")
            row = DemandTemplate.objects.filter(pk=template_id).first()
            if row:
                row.is_active = not row.is_active
                row.save(update_fields=["is_active"])
                return redirect("manageDemandTemplates")
        elif form_id == "template_delete":
            template_id = request.POST.get("template_id")
            DemandTemplate.objects.filter(pk=template_id).delete()
            return redirect("manageDemandTemplates")
        elif form_id == "detail_delete":
            detail_id = request.POST.get("detail_id")
            DemandTemplateDetail.objects.filter(pk=detail_id).delete()
            return redirect("manageDemandTemplates")

    templates = (
        DemandTemplate.objects.select_related("task_group", "task_type", "linked_project")
        .prefetch_related("details")
        .order_by("name")
    )

    return render(
        request,
        "tiqueue/demand_templates.html",
        {
            "template_form": template_form,
            "detail_form": detail_form,
            "templates": templates,
        },
    )

@login_required
@require_GET
def queueItemDetails(request, id):
    item = get_object_or_404(
        userQueue.objects.select_related("task_group", "task_type", "linked_project", "kanban_column").prefetch_related(
            _queue_collaborators_prefetch()
        ),
        n_register=id,
        user_code=request.user.userId,
    )

    def _date(d):
        return d.isoformat() if d else ""

    def _time(t):
        return t.strftime("%H:%M") if t else ""

    detail_counts = QueueTaskDetail.objects.filter(queue_item=item).aggregate(
        total=Count("id"),
        done=Count("id", filter=Q(is_done=True)),
    )
    detail_total = int(detail_counts.get("total") or 0)
    detail_done = int(detail_counts.get("done") or 0)
    detail_hours_total = _calc_task_total_hours(item)

    return JsonResponse(
        {
            "n_register": item.n_register,
            "a_ticket": item.a_ticket or "",
            "f_conclusion_rate": "" if item.f_conclusion_rate is None else str(item.f_conclusion_rate),
            "a_description": item.a_description or "",
            "a_demand_detail": item.a_demand_detail or "",
            "a_notes": item.a_notes or "",
            "task_group": item.task_group_id or "",
            "task_group_name": item.task_group.name if item.task_group else "",
            "task_type": item.task_type_id or "",
            "task_type_name": item.task_type.name if item.task_type else "",
            "priority_level": item.priority_level or userQueue.PRIORITY_MEDIUM,
            "estimated_effort_level": item.estimated_effort_level or userQueue.ESTIMATE_MEDIUM,
            "kanban_column": item.kanban_column_id or "",
            "kanban_column_name": item.kanban_column.name if item.kanban_column else "",
            "kanban_column_color": item.kanban_column.color if item.kanban_column else "#61688c",
            "linked_project_id": item.linked_project_id or "",
            "linked_project_name": item.linked_project.name if item.linked_project else "",
            "extra_collaborators_ids": [collaborator.id for collaborator in getattr(item, "extra_collaborators_prefetched", [])],
            "extra_collaborators": [
                _serialize_queue_collaborator(collaborator)
                for collaborator in getattr(item, "extra_collaborators_prefetched", [])
            ],
            "is_current": bool(item.is_current),
            "detail_total": detail_total,
            "detail_done": detail_done,
            "detail_hours_total": f"{detail_hours_total:.2f}",
            "d_predicted_date_start": _date(item.d_predicted_date_start),
            "t_predicted_time_start": _time(item.t_predicted_time_start),
            "d_predicted_date_end": _date(item.d_predicted_date_end),
            "t_predicted_time_end": _time(item.t_predicted_time_end),
            "d_real_date_start": _date(item.d_real_date_start),
            "d_real_time_start": _time(item.d_real_time_start),
            "d_real_date_end": _date(item.d_real_date_end),
            "t_real_time_end": _time(item.t_real_time_end),
        }
    )


@login_required
@require_POST
def createQueueItemInline(request):
    user_code = request.user.userId

    next_position = (
        userQueue.objects.filter(user_code=user_code).aggregate(max_pos=models.Max("n_queue_position")).get("max_pos") or 0
    ) + 1

    ticket = (request.POST.get("a_ticket") or "").strip() or None
    description = (request.POST.get("a_description") or "").strip() or None
    priority_level = _normalize_user_queue_field_value(
        request.user,
        userQueue.FIELD_PRIORITY,
        request.POST.get("priority_level") or userQueue.PRIORITY_MEDIUM,
    )
    estimated_effort_level = _normalize_user_queue_field_value(
        request.user,
        userQueue.FIELD_EFFORT,
        request.POST.get("estimated_effort_level") or userQueue.ESTIMATE_MEDIUM,
    )
    task_type_raw = (request.POST.get("task_type") or "").strip()
    kanban_column_raw = (request.POST.get("kanban_column") or "").strip()
    collaborator_ids = [
        int(value)
        for value in request.POST.getlist("extra_collaborators")
        if str(value).isdigit()
    ]

    def _parse_date(v):
        v = (v or "").strip()
        if not v:
            return None
        try:
            return datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _parse_time(v):
        v = (v or "").strip()
        if not v:
            return None
        try:
            return datetime.strptime(v, "%H:%M").time()
        except ValueError:
            return None

    d_real_date_start = _parse_date(request.POST.get("d_real_date_start"))
    d_real_time_start = _parse_time(request.POST.get("d_real_time_start"))
    d_real_date_end = _parse_date(request.POST.get("d_real_date_end"))
    t_real_time_end = _parse_time(request.POST.get("t_real_time_end"))

    item = userQueue.objects.create(
        user_code=user_code,
        a_ticket=ticket,
        a_description=description,
        n_queue_position=next_position,
        kanban_sort_order=next_position,
        priority_level=priority_level,
        estimated_effort_level=estimated_effort_level,
        d_real_date_start=d_real_date_start,
        d_real_time_start=d_real_time_start,
        d_real_date_end=d_real_date_end,
        t_real_time_end=t_real_time_end,
        is_current=False,
    )

    if task_type_raw.isdigit():
        item.task_type_id = int(task_type_raw)
        item.n_type_code = item.task_type_id
        item.task_group_id = item.task_type.group_id if item.task_type_id else None
        item.n_type_group = item.task_group_id
        item.save(update_fields=["task_type", "n_type_code", "task_group", "n_type_group"])

    if kanban_column_raw.isdigit():
        col = UserQueueKanbanColumn.objects.filter(id=int(kanban_column_raw), user=request.user, is_active=True).first()
        if col:
            item.kanban_column = col
            item.save(update_fields=["kanban_column"])

    if collaborator_ids:
        collaborators = User.objects.filter(id__in=collaborator_ids).exclude(pk=request.user.pk)
        item.extra_collaborators.set(collaborators)

    return JsonResponse({"status": "ok", "id": item.n_register})


@login_required
@require_POST
def updateQueueItem(request, id):
    item = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)
    post_data = request.POST.copy()

    def _as_form_value(field_name):
        try:
            model_field = item._meta.get_field(field_name)
        except Exception:
            model_field = None

        # For FK fields, ModelForm expects the related PK value.
        if getattr(model_field, "many_to_one", False):
            rel_id = getattr(item, f"{field_name}_id", None)
            return "" if rel_id is None else str(rel_id)
        if getattr(model_field, "many_to_many", False):
            return ""

        value = getattr(item, field_name, None)
        if value is None:
            return ""
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        if hasattr(value, "strftime") and not isinstance(value, str):
            try:
                return value.strftime("%H:%M")
            except Exception:
                pass
        return str(value)

    # Partial inline updates: keep all non-posted fields from current instance.
    if "priority_level" in post_data:
        post_data["priority_level"] = _normalize_user_queue_field_value(
            request.user, userQueue.FIELD_PRIORITY, post_data.get("priority_level")
        )
    if "estimated_effort_level" in post_data:
        post_data["estimated_effort_level"] = _normalize_user_queue_field_value(
            request.user, userQueue.FIELD_EFFORT, post_data.get("estimated_effort_level")
        )

    probe_form = UserQueueUpdateForm(instance=item, user=request.user)
    for field_name in probe_form.fields.keys():
        if field_name not in post_data:
            post_data[field_name] = _as_form_value(field_name)

    # Normaliza pares data/hora para evitar falhas de validação quando apenas a data é informada.
    for date_key, time_key in (
        ("d_predicted_date_start", "t_predicted_time_start"),
        ("d_predicted_date_end", "t_predicted_time_end"),
        ("d_real_date_start", "d_real_time_start"),
        ("d_real_date_end", "t_real_time_end"),
    ):
        d_val = (post_data.get(date_key) or "").strip()
        t_val = (post_data.get(time_key) or "").strip()
        if d_val and not t_val:
            post_data[time_key] = "00:00"
        if t_val and not d_val:
            post_data[date_key] = ""
            post_data[time_key] = ""

    form = UserQueueUpdateForm(post_data, instance=item, user=request.user)
    if not form.is_valid():
        return JsonResponse({"status": "error", "errors": form.errors}, status=400)

    item = form.save()

    update_type_fields = []

    if "task_group" in request.POST:
        task_group_raw = (request.POST.get("task_group") or "").strip()
        task_group_id = int(task_group_raw) if task_group_raw.isdigit() else None
        item.task_group_id = task_group_id
        item.n_type_group = task_group_id
        update_type_fields.extend(["task_group", "n_type_group"])

    if "task_type" in request.POST:
        task_type_raw = (request.POST.get("task_type") or "").strip()
        task_type_id = int(task_type_raw) if task_type_raw.isdigit() else None
        item.task_type_id = task_type_id
        item.n_type_code = task_type_id
        # Keep group coherent when type is explicitly changed.
        if task_type_id:
            tt = TaskType.objects.filter(id=task_type_id).only("group_id").first()
            if tt:
                item.task_group_id = tt.group_id
                item.n_type_group = tt.group_id
                if "task_group" not in update_type_fields:
                    update_type_fields.extend(["task_group", "n_type_group"])
        update_type_fields.extend(["task_type", "n_type_code"])

    if update_type_fields:
        item.save(update_fields=list(dict.fromkeys(update_type_fields)))

    if "extra_collaborators_present" in request.POST:
        collaborator_ids = [
            int(value)
            for value in request.POST.getlist("extra_collaborators")
            if str(value).isdigit()
        ]
        collaborators = User.objects.filter(id__in=collaborator_ids).exclude(pk=request.user.pk)
        item.extra_collaborators.set(collaborators)

    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def updateQueueGanttRange(request, id):
    item = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)

    def _parse_date(val):
        val = (val or "").strip()
        if not val:
            return None
        try:
            return date.fromisoformat(val)
        except ValueError:
            return None

    def _parse_time(val):
        val = (val or "").strip()
        if not val:
            return None
        try:
            return datetime.strptime(val, "%H:%M").time()
        except ValueError:
            return None

    item.d_real_date_start = _parse_date(request.POST.get("d_real_date_start"))
    item.d_real_date_end = _parse_date(request.POST.get("d_real_date_end"))
    update_fields = ["d_real_date_start", "d_real_date_end"]

    if "d_real_time_start" in request.POST:
        item.d_real_time_start = _parse_time(request.POST.get("d_real_time_start"))
        update_fields.append("d_real_time_start")
    if "t_real_time_end" in request.POST:
        item.t_real_time_end = _parse_time(request.POST.get("t_real_time_end"))
        update_fields.append("t_real_time_end")

    item.save(update_fields=update_fields)
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def toggleCurrentTask(request, id):
    item = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    is_current = bool(payload.get("is_current"))
    item.is_current = is_current
    item.save(update_fields=["is_current"])
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def createUserQueueCustomColumn(request):
    name = (request.POST.get("name") or "").strip()
    field_type = (request.POST.get("field_type") or UserQueueCustomColumn.FIELD_TEXT).strip().lower()
    color = (request.POST.get("color") or "#61688c").strip() or "#61688c"
    initial_option_label = (request.POST.get("initial_option_label") or "").strip()
    initial_option_color = (request.POST.get("initial_option_color") or color).strip() or color
    if not name:
        return JsonResponse({"status": "error", "message": "Nome obrigatorio."}, status=400)
    allowed_types = {choice[0] for choice in UserQueueCustomColumn.FIELD_TYPE_CHOICES}
    if field_type not in allowed_types:
        return JsonResponse({"status": "error", "message": "Tipo de coluna invalido."}, status=400)
    max_sort = (
        UserQueueCustomColumn.objects.filter(user=request.user).aggregate(max_sort=models.Max("sort_order")).get("max_sort")
        or 0
    )
    try:
        column = UserQueueCustomColumn.objects.create(
            user=request.user,
            name=name[:60],
            field_type=field_type,
            color=color[:7],
            sort_order=int(max_sort) + 1,
        )
    except IntegrityError:
        return JsonResponse({"status": "error", "message": "Ja existe uma coluna com este nome."}, status=400)
    if field_type == UserQueueCustomColumn.FIELD_SELECT and initial_option_label:
        UserQueueCustomColumnOption.objects.create(
            column=column,
            value=_build_queue_option_value(initial_option_label, set()),
            label=initial_option_label[:80],
            color=initial_option_color[:7],
            sort_order=1,
            is_active=True,
        )
    column = (
        UserQueueCustomColumn.objects.filter(pk=column.pk)
        .prefetch_related(
            Prefetch("options", queryset=UserQueueCustomColumnOption.objects.filter(is_active=True).order_by("sort_order", "id"))
        )
        .first()
    )
    return JsonResponse({"status": "ok", "column": _serialize_queue_custom_column(column)})


@login_required
@require_POST
def createTaskTypeQuick(request):
    group_raw = (request.POST.get("group_id") or "").strip()
    name = (request.POST.get("name") or "").strip()
    color = (request.POST.get("color") or "#5CD6A3").strip() or "#5CD6A3"
    if not group_raw.isdigit():
        return JsonResponse({"status": "error", "message": "Grupo invalido."}, status=400)
    if not name:
        return JsonResponse({"status": "error", "message": "Nome obrigatorio."}, status=400)
    group = TaskGroup.objects.filter(id=int(group_raw)).first()
    if not group:
        return JsonResponse({"status": "error", "message": "Grupo nao encontrado."}, status=400)
    try:
        row = TaskType.objects.create(group=group, name=name[:80], color=color[:7])
    except IntegrityError:
        return JsonResponse({"status": "error", "message": "Este tipo ja existe no grupo."}, status=400)
    return JsonResponse(
        {
            "status": "ok",
            "id": row.id,
            "name": row.name,
            "color": row.color,
            "group_id": row.group_id,
            "group_name": group.name,
        }
    )


@login_required
@require_POST
def createUserQueueFieldOption(request):
    field_key = (request.POST.get("field_key") or "").strip()
    label = (request.POST.get("label") or "").strip()
    color = (request.POST.get("color") or "#61688c").strip() or "#61688c"
    allowed_fields = {UserQueueFieldOption.FIELD_PRIORITY, UserQueueFieldOption.FIELD_EFFORT}
    if field_key not in allowed_fields:
        return JsonResponse({"status": "error", "message": "Campo invalido."}, status=400)
    if not label:
        return JsonResponse({"status": "error", "message": "Nome da opcao obrigatorio."}, status=400)

    existing = list(
        UserQueueFieldOption.objects.filter(user=request.user, field_key=field_key).values_list("value", flat=True)
    )
    value = _build_queue_option_value(label, set(existing))
    max_sort = (
        UserQueueFieldOption.objects.filter(user=request.user, field_key=field_key)
        .aggregate(models.Max("sort_order"))
        .get("sort_order__max")
        or 0
    )
    option = UserQueueFieldOption.objects.create(
        user=request.user,
        field_key=field_key,
        value=value,
        label=label[:60],
        color=color[:7],
        sort_order=int(max_sort) + 1,
        is_active=True,
    )
    return JsonResponse({"status": "ok", "option": _serialize_queue_field_option(option)})


@login_required
@require_POST
def deleteUserQueueFieldOption(request, option_id):
    option = get_object_or_404(UserQueueFieldOption, pk=option_id, user=request.user, is_active=True)
    remaining_options = list(
        UserQueueFieldOption.objects.filter(user=request.user, field_key=option.field_key, is_active=True)
        .exclude(pk=option.pk)
        .order_by("sort_order", "id")
    )
    if not remaining_options:
        return JsonResponse(
            {"status": "error", "message": "E necessario manter pelo menos uma opcao ativa."},
            status=400,
        )

    replacement = remaining_options[0]
    field_name = option.field_key
    with transaction.atomic():
        userQueue.objects.filter(user_code=request.user.userId, **{field_name: option.value}).update(
            **{field_name: replacement.value}
        )
        concludedTasks.objects.filter(user_code=request.user.userId, **{field_name: option.value}).update(
            **{field_name: replacement.value}
        )
        option.is_active = False
        option.save(update_fields=["is_active"])

    field_payload = _queue_field_option_payload(request.user)
    active_options = field_payload[
        "priority_options" if option.field_key == UserQueueFieldOption.FIELD_PRIORITY else "effort_options"
    ]
    return JsonResponse(
        {
            "status": "ok",
            "field_key": option.field_key,
            "replacement_value": replacement.value,
            "active_options": active_options,
        }
    )


@login_required
@require_POST
def createUserQueueCustomColumnOption(request, column_id):
    column = get_object_or_404(UserQueueCustomColumn, pk=column_id, user=request.user)
    if column.field_type != UserQueueCustomColumn.FIELD_SELECT:
        return JsonResponse({"status": "error", "message": "A coluna precisa ser do tipo lista."}, status=400)

    label = (request.POST.get("label") or "").strip()
    color = (request.POST.get("color") or column.color or "#61688c").strip() or "#61688c"
    if not label:
        return JsonResponse({"status": "error", "message": "Nome da opcao obrigatorio."}, status=400)

    existing = list(
        UserQueueCustomColumnOption.objects.filter(column=column).values_list("value", flat=True)
    )
    value = _build_queue_option_value(label, set(existing))
    max_sort = (
        UserQueueCustomColumnOption.objects.filter(column=column)
        .aggregate(models.Max("sort_order"))
        .get("sort_order__max")
        or 0
    )
    option = UserQueueCustomColumnOption.objects.create(
        column=column,
        value=value,
        label=label[:80],
        color=color[:7],
        sort_order=int(max_sort) + 1,
        is_active=True,
    )
    return JsonResponse({"status": "ok", "column_id": column.id, "option": _serialize_queue_custom_column_option(option)})


@login_required
@require_POST
def deleteUserQueueCustomColumnOption(request, option_id):
    option = get_object_or_404(
        UserQueueCustomColumnOption.objects.select_related("column"),
        pk=option_id,
        column__user=request.user,
        is_active=True,
    )

    with transaction.atomic():
        UserQueueCustomValue.objects.filter(column=option.column, value=option.value).update(
            value="",
            updated_at=timezone.now(),
        )
        option.is_active = False
        option.save(update_fields=["is_active"])

    column = (
        UserQueueCustomColumn.objects.filter(pk=option.column_id, user=request.user)
        .prefetch_related(
            Prefetch(
                "options",
                queryset=UserQueueCustomColumnOption.objects.filter(is_active=True).order_by("sort_order", "id"),
            )
        )
        .first()
    )
    usage_rows = (
        UserQueueCustomValue.objects.filter(column_id=option.column_id)
        .exclude(value__isnull=True)
        .exclude(value="")
        .values("column_id", "value")
        .annotate(total=Count("id"))
    )
    usage_map = {
        (int(row["column_id"]), str(row["value"] or "")): int(row["total"] or 0)
        for row in usage_rows
    }
    return JsonResponse(
        {
            "status": "ok",
            "column_id": option.column_id,
            "column": _serialize_queue_custom_column(column, option_usage_map=usage_map) if column else None,
        }
    )


@login_required
@require_POST
def setUserQueueCustomValue(request, id):
    item = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)
    column_id_raw = (request.POST.get("column_id") or "").strip()
    if not column_id_raw.isdigit():
        return JsonResponse({"status": "error", "message": "Coluna invalida."}, status=400)
    column = get_object_or_404(
        UserQueueCustomColumn.objects.prefetch_related(
            Prefetch("options", queryset=UserQueueCustomColumnOption.objects.filter(is_active=True).order_by("sort_order", "id"))
        ),
        id=int(column_id_raw),
        user=request.user,
    )
    value = (request.POST.get("value") or "")[:250]
    try:
        value = _normalize_custom_column_value(column, value)
    except ValueError as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=400)
    obj, _ = UserQueueCustomValue.objects.get_or_create(queue_item=item, column=column)
    obj.value = value
    obj.save(update_fields=["value", "updated_at"])
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def saveUserQueueView(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    name = str(payload.get("name") or "").strip()[:80]
    if not name:
        return JsonResponse({"status": "error", "message": "Nome obrigatorio."}, status=400)

    filters = _normalize_user_queue_saved_view_filters(payload.get("filters") or {})
    saved_view, _ = UserQueueSavedView.objects.update_or_create(
        user=request.user,
        name=name,
        defaults={"filters_json": filters},
    )
    return JsonResponse({"status": "ok", "view": _serialize_user_queue_saved_view(saved_view)})


@login_required
@require_POST
def deleteUserQueueView(request, view_id):
    saved_view = get_object_or_404(UserQueueSavedView, pk=view_id, user=request.user)
    saved_view.delete()
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def syncQueueWithSM(request):
    attendant_id = str(getattr(request.user, "id_sm", "") or "").strip()
    if not attendant_id:
        return JsonResponse(
            {"status": "error", "message": "Usuario sem ID SM cadastrado. Atualize o cadastro de usuario."},
            status=400,
        )

    try:
        sm_rows = _query_sm_open_tickets(attendant_id)
        sm_closed_rows = _query_sm_closed_tickets(attendant_id)
    except Exception as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)

    # Already registered in queue or concluded.
    existing_tickets = set(
        t.strip()
        for t in list(
            userQueue.objects.exclude(a_ticket__isnull=True).exclude(a_ticket__exact="").values_list("a_ticket", flat=True)
        )
        + list(
            concludedTasks.objects.exclude(a_ticket__isnull=True).exclude(a_ticket__exact="").values_list("a_ticket", flat=True)
        )
        if t and str(t).strip()
    )

    user_code = request.user.userId
    current_max_pos = (
        userQueue.objects.filter(user_code=user_code).aggregate(max_pos=models.Max("n_queue_position")).get("max_pos") or 0
    )

    created = 0
    skipped = 0
    auto_concluded = 0
    with transaction.atomic():
        # 1) Auto-conclude in ConnectMX what is already closed in SM.
        closed_tickets = {
            (row.get("codigo_helpdesk") or "").strip()
            for row in sm_closed_rows
            if (row.get("codigo_helpdesk") or "").strip()
        }
        if closed_tickets:
            queue_to_close = list(
                userQueue.objects.filter(user_code=user_code, a_ticket__in=closed_tickets)
                .order_by("n_queue_position", "n_register")
                .values_list("n_register", flat=True)
            )
            for reg_id in queue_to_close:
                service.serviceEndQueueItem(request, reg_id)
                auto_concluded += 1

        for row in sm_rows:
            ticket = (row.get("codigo_helpdesk") or "").strip()
            desc = (row.get("descricao") or "").strip()
            detail = (row.get("detalhe_demanda") or "").strip()
            if not ticket:
                skipped += 1
                continue
            if ticket in existing_tickets:
                skipped += 1
                continue

            current_max_pos += 1
            userQueue.objects.create(
                user_code=user_code,
                a_ticket=ticket,
                a_description=desc or f"Chamado {ticket}",
                a_demand_detail=detail or None,
                n_queue_position=current_max_pos,
                kanban_sort_order=current_max_pos,
                f_conclusion_rate=Decimal("0.00"),
                is_current=False,
            )
            existing_tickets.add(ticket)
            created += 1

    return JsonResponse(
        {
            "status": "ok",
            "created": created,
            "skipped": skipped,
            "auto_concluded": auto_concluded,
            "total_found": len(sm_rows),
            "total_closed_found": len(sm_closed_rows),
        }
    )


@login_required
@require_POST
def linkQueueItemToProject(request, id):
    item = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    project_id = payload.get("project_id")
    if not project_id:
        return JsonResponse({"status": "error", "message": "Projeto obrigatorio"}, status=400)

    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Projeto invalido"}, status=400)

    project = get_object_or_404(Project, pk=project_id)

    ticket = (item.a_ticket or "").strip()
    desc = (item.a_description or "").strip()
    title_base = f"{ticket} - {desc}" if ticket and desc else (desc or ticket or f"Demanda #{item.n_register}")
    title = title_base[:180]

    roadmap_description = desc or None
    if ticket:
        roadmap_description = f"Chamado: {ticket}\n{roadmap_description or ''}".strip()

    status = "doing" if item.is_current else "planned"

    with transaction.atomic():
        roadmap_item = None

        if item.linked_roadmap_item_id and item.linked_roadmap_item and item.linked_roadmap_item.project_id == project.id:
            roadmap_item = item.linked_roadmap_item
            roadmap_item.title = title
            roadmap_item.responsible = request.user
            roadmap_item.description = roadmap_description
            roadmap_item.status = status
            roadmap_item.start_date = item.d_predicted_date_start
            roadmap_item.end_date = item.d_predicted_date_end
            roadmap_item.save(
                update_fields=["title", "responsible", "description", "status", "start_date", "end_date"]
            )
        else:
            max_sort = (
                ProjectRoadmapItem.objects.filter(project=project).aggregate(models.Max("sort_order")).get("sort_order__max")
            )
            roadmap_item = ProjectRoadmapItem.objects.create(
                project=project,
                responsible=request.user,
                title=title,
                description=roadmap_description,
                status=status,
                start_date=item.d_predicted_date_start,
                end_date=item.d_predicted_date_end,
                sort_order=int(max_sort or 0) + 1,
            )

        _sync_roadmap_item_to_kanban(roadmap_item)

        item.linked_project = project
        item.linked_roadmap_item = roadmap_item
        item.save(update_fields=["linked_project", "linked_roadmap_item"])

    return JsonResponse(
        {
            "status": "ok",
            "project_name": project.name,
            "roadmap_item_id": roadmap_item.id,
        }
    )

@login_required
@require_POST
def reorderQueueItems(request):
    """
    Receives an ordered list of queue item ids (n_register) and persists positions
    for the authenticated user.
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    order = payload.get("order")
    if not isinstance(order, list) or not order:
        return HttpResponseBadRequest("Missing order")

    try:
        order_ids = [int(x) for x in order]
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Order must be a list of integers")

    if len(order_ids) != len(set(order_ids)):
        return HttpResponseBadRequest("Order contains duplicate ids")

    user_code = request.user.userId
    items = list(
        userQueue.objects.filter(user_code=user_code, n_register__in=order_ids).values("n_register", "n_queue_position")
    )

    if len(items) != len(set(order_ids)):
        return HttpResponseBadRequest("One or more items not found")

    # Persist exact visual order from top to bottom starting at 1.
    whens = []
    whens_sort = []
    for index, item_id in enumerate(order_ids):
        pos = index + 1
        whens.append(When(n_register=item_id, then=Value(pos)))
        whens_sort.append(When(n_register=item_id, then=Value(pos)))

    with transaction.atomic():
        userQueue.objects.filter(user_code=user_code, n_register__in=order_ids).update(
            n_queue_position=Case(*whens, output_field=IntegerField()),
            kanban_sort_order=Case(*whens_sort, output_field=IntegerField()),
        )

        return JsonResponse({"status": "ok"})


@login_required
@require_POST
def createMyQueueKanbanColumn(request):
    name = (request.POST.get("name") or "").strip()
    color = (request.POST.get("color") or "").strip() or "#343955"
    if not name:
        return JsonResponse({"status": "error", "message": "Nome obrigatorio"}, status=400)

    max_sort = (
        UserQueueKanbanColumn.objects.filter(user=request.user)
        .aggregate(models.Max("sort_order"))
        .get("sort_order__max")
        or 0
    )
    try:
        col = UserQueueKanbanColumn.objects.create(
            user=request.user,
            name=name,
            color=color,
            sort_order=int(max_sort) + 1,
            is_active=True,
        )
    except IntegrityError:
        return JsonResponse({"status": "error", "message": "Ja existe uma coluna com esse nome."}, status=400)

    return JsonResponse(
        {"status": "ok", "id": col.id, "value": str(col.id), "name": col.name, "label": col.name, "color": col.color}
    )


@login_required
@require_POST
def updateMyQueueKanbanColumn(request, column_id):
    col = get_object_or_404(UserQueueKanbanColumn, pk=column_id, user=request.user, is_active=True)
    name = (request.POST.get("name") or "").strip()
    color = (request.POST.get("color") or "").strip() or "#343955"
    if not name:
        return JsonResponse({"status": "error", "message": "Nome obrigatorio"}, status=400)

    if (
        UserQueueKanbanColumn.objects.filter(user=request.user, is_active=True, name=name)
        .exclude(pk=col.pk)
        .exists()
    ):
        return JsonResponse({"status": "error", "message": "Ja existe uma coluna com esse nome."}, status=400)

    col.name = name
    col.color = color
    col.save(update_fields=["name", "color"])
    return JsonResponse(
        {"status": "ok", "id": col.id, "value": str(col.id), "name": col.name, "label": col.name, "color": col.color}
    )


@login_required
@require_POST
def deleteMyQueueKanbanColumn(request, column_id):
    col = get_object_or_404(UserQueueKanbanColumn, pk=column_id, user=request.user, is_active=True)
    has_items = userQueue.objects.filter(user_code=request.user.userId, kanban_column=col).exists()
    if has_items:
        return JsonResponse(
            {"status": "error", "message": "Nao e possivel excluir: a coluna possui tarefas."},
            status=400,
        )

    active_count = UserQueueKanbanColumn.objects.filter(user=request.user, is_active=True).count()
    if active_count <= 1:
        return JsonResponse(
            {"status": "error", "message": "E necessario manter pelo menos uma coluna ativa."},
            status=400,
        )

    col.is_active = False
    col.save(update_fields=["is_active"])
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def reorderMyQueueKanbanColumn(request, column_id):
    direction = (request.POST.get("direction") or "").strip().lower()
    if direction not in ("left", "right"):
        return JsonResponse({"status": "error", "message": "Direcao invalida."}, status=400)

    columns = list(
        UserQueueKanbanColumn.objects.filter(user=request.user, is_active=True).order_by("sort_order", "id")
    )
    if len(columns) <= 1:
        return JsonResponse({"status": "ok"})

    idx = next((i for i, c in enumerate(columns) if c.id == column_id), -1)
    if idx < 0:
        return JsonResponse({"status": "error", "message": "Coluna nao encontrada."}, status=404)

    target_idx = idx - 1 if direction == "left" else idx + 1
    if target_idx < 0 or target_idx >= len(columns):
        return JsonResponse({"status": "ok"})

    col_a = columns[idx]
    col_b = columns[target_idx]
    col_a.sort_order, col_b.sort_order = col_b.sort_order, col_a.sort_order
    with transaction.atomic():
        col_a.save(update_fields=["sort_order"])
        col_b.save(update_fields=["sort_order"])

    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def moveMyQueueKanbanCard(request, item_id):
    item = get_object_or_404(userQueue, n_register=item_id, user_code=request.user.userId)
    column_id = request.POST.get("column_id")
    if not column_id:
        return JsonResponse({"status": "error", "message": "Coluna obrigatoria"}, status=400)

    col = get_object_or_404(UserQueueKanbanColumn, pk=int(column_id), user=request.user, is_active=True)
    item.kanban_column = col
    next_sort = (
        userQueue.objects.filter(user_code=request.user.userId, kanban_column=col)
        .aggregate(v=models.Max("kanban_sort_order"))
        .get("v")
        or 0
    )
    item.kanban_sort_order = int(next_sort) + 1
    item.save(update_fields=["kanban_column", "kanban_sort_order"])
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def reorderMyQueueKanbanCards(request, column_id):
    col = get_object_or_404(UserQueueKanbanColumn, pk=column_id, user=request.user, is_active=True)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    ordered_ids_raw = payload.get("ordered_ids") or []
    if not isinstance(ordered_ids_raw, list):
        return JsonResponse({"status": "error", "message": "ordered_ids invalido"}, status=400)

    db_ids = list(
        userQueue.objects.filter(user_code=request.user.userId, kanban_column=col).values_list("n_register", flat=True)
    )
    valid = set(int(i) for i in db_ids)
    ordered_ids = []
    for i in ordered_ids_raw:
        try:
            n = int(i)
        except Exception:
            continue
        if n in valid and n not in ordered_ids:
            ordered_ids.append(n)
    remainder = [i for i in db_ids if i not in ordered_ids]
    final_ids = ordered_ids + remainder

    with transaction.atomic():
        for idx, reg_id in enumerate(final_ids, start=1):
            userQueue.objects.filter(n_register=reg_id, user_code=request.user.userId).update(kanban_sort_order=idx)

    return JsonResponse({"status": "ok"})


def _calc_conclusion_rate(done: int, total: int) -> Decimal:
    if total <= 0:
        return Decimal("0.00")
    rate = (Decimal(done) / Decimal(total)) * Decimal("100")
    return rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _persist_queue_conclusion_rate(queue_item: userQueue) -> str:
    """
    Persist f_conclusion_rate based on subtask completion.
    Returns the rate as a string with 2 decimal places.
    """
    counts = QueueTaskDetail.objects.filter(queue_item=queue_item).aggregate(
        total=Count("id"),
        done=Count("id", filter=Q(is_done=True)),
    )
    total = int(counts.get("total") or 0)
    done = int(counts.get("done") or 0)
    rate = _calc_conclusion_rate(done, total)

    queue_item.f_conclusion_rate = rate
    queue_item.save(update_fields=["f_conclusion_rate"])
    return f"{rate:.2f}"


def _calc_task_total_hours(queue_item: userQueue) -> Decimal:
    total = (
        QueueTaskDetail.objects.filter(queue_item=queue_item, is_done=True)
        .aggregate(
            v=Coalesce(
                Sum("duration_hours"),
                Value(0),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )
        .get("v")
    )
    try:
        return Decimal(str(total or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


@login_required
@require_GET
def queueTaskDetailsList(request, id):
    # Details (subtasks) are private per owner of the queue item.
    item = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)
    details = list(
        QueueTaskDetail.objects.filter(queue_item=item)
        .order_by("sort_order", "id")
        .values("id", "description", "is_done", "duration_hours")
    )
    total = len(details)
    done = sum(1 for d in details if d.get("is_done"))
    rate = _calc_conclusion_rate(done, total)
    hours_total = _calc_task_total_hours(item)
    return JsonResponse(
        {
            "status": "ok",
            "details": details,
            "total": total,
            "done": done,
            "rate": f"{rate:.2f}",
            "hours_total": f"{hours_total:.2f}",
        }
    )


@login_required
@require_POST
def queueTaskDetailsAdd(request, id):
    # Adds a new subtask to a queue item (append at end).
    item = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    description = (payload.get("description") or "").strip()
    if not description:
        return JsonResponse({"status": "error", "message": "Descricao obrigatoria"}, status=400)

    max_sort = (
        QueueTaskDetail.objects.filter(queue_item=item)
        .aggregate(models.Max("sort_order"))
        .get("sort_order__max")
    )
    next_sort = int(max_sort or 0) + 1

    detail = QueueTaskDetail.objects.create(queue_item=item, description=description, sort_order=next_sort)
    rate = _persist_queue_conclusion_rate(item)
    hours_total = _calc_task_total_hours(item)
    return JsonResponse({"status": "ok", "id": detail.id, "rate": rate, "hours_total": f"{hours_total:.2f}"})


@login_required
@require_POST
def queueTaskDetailsToggle(request, detail_id):
    detail = get_object_or_404(QueueTaskDetail, pk=detail_id, queue_item__user_code=request.user.userId)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    detail.is_done = bool(payload.get("is_done"))
    duration = payload.get("duration_hours", None)
    if detail.is_done:
        if duration in (None, ""):
            # Keep existing duration if already set.
            pass
        else:
            try:
                d = Decimal(str(duration))
            except Exception:
                return JsonResponse({"status": "error", "message": "Duracao invalida"}, status=400)
            if d < 0:
                return JsonResponse({"status": "error", "message": "Duracao nao pode ser negativa"}, status=400)
            detail.duration_hours = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        detail.duration_hours = None
    detail.save(update_fields=["is_done", "duration_hours"])
    rate = _persist_queue_conclusion_rate(detail.queue_item)
    hours_total = _calc_task_total_hours(detail.queue_item)
    return JsonResponse(
        {
            "status": "ok",
            "rate": rate,
            "hours_total": f"{hours_total:.2f}",
            "duration_hours": (f"{detail.duration_hours:.2f}" if detail.duration_hours is not None else ""),
        }
    )


@login_required
@require_POST
def queueTaskDetailsDelete(request, detail_id):
    detail = get_object_or_404(QueueTaskDetail, pk=detail_id, queue_item__user_code=request.user.userId)
    queue_item = detail.queue_item
    detail.delete()
    rate = _persist_queue_conclusion_rate(queue_item)
    hours_total = _calc_task_total_hours(queue_item)
    return JsonResponse({"status": "ok", "rate": rate, "hours_total": f"{hours_total:.2f}"})


def _get_checklist_sections(template):
    return (
        ChecklistSection.objects.filter(template=template)
        .prefetch_related("fields", "fields__choice_group", "fields__choice_group__options")
        .order_by("sort_order", "id")
    )


def _build_checklist_form(template, entry=None, data=None):
    answers = {}
    if entry:
        for answer in ChecklistAnswer.objects.filter(entry=entry).prefetch_related("selected_options"):
            answers[answer.field_id] = answer

    fields = {}
    fields["entry_title"] = forms.CharField(
        required=False,
        label="Identificador",
        widget=forms.TextInput(attrs={"placeholder": "Opcional"}),
    )

    initial = {}
    if entry:
        initial["entry_title"] = entry.title or ""

    sections = []
    for section in _get_checklist_sections(template):
        section_fields = []
        for field in section.fields.all().order_by("sort_order", "id"):
            key = f"field_{field.id}"
            field_required = field.required
            help_text = field.help_text or ""

            if field.field_type == "text":
                fields[key] = forms.CharField(required=field_required, label=field.label, help_text=help_text)
                if field.id in answers:
                    initial[key] = answers[field.id].value_text or ""
            elif field.field_type == "textarea":
                fields[key] = forms.CharField(
                    required=field_required,
                    label=field.label,
                    help_text=help_text,
                    widget=forms.Textarea(attrs={"rows": 3}),
                )
                if field.id in answers:
                    initial[key] = answers[field.id].value_text or ""
            elif field.field_type == "number":
                fields[key] = forms.DecimalField(required=field_required, label=field.label, help_text=help_text)
                if field.id in answers:
                    initial[key] = answers[field.id].value_number
            elif field.field_type == "date":
                fields[key] = forms.DateField(
                    required=field_required,
                    label=field.label,
                    help_text=help_text,
                    widget=forms.DateInput(attrs={"type": "date"}),
                )
                if field.id in answers:
                    initial[key] = answers[field.id].value_date
            elif field.field_type == "time":
                fields[key] = forms.TimeField(
                    required=field_required,
                    label=field.label,
                    help_text=help_text,
                    widget=forms.TimeInput(attrs={"type": "time"}),
                )
                if field.id in answers:
                    initial[key] = answers[field.id].value_time
            elif field.field_type == "boolean":
                fields[key] = forms.BooleanField(
                    required=False, label=field.label, help_text=help_text, initial=False
                )
                if field.id in answers:
                    initial[key] = bool(answers[field.id].value_bool)
            elif field.field_type in ("single_choice", "multi_choice"):
                choices = []
                if field.choice_group:
                    choices = [(str(opt.id), opt.label) for opt in field.choice_group.options.all()]

                if field.field_type == "single_choice":
                    fields[key] = forms.ChoiceField(
                        required=field_required,
                        label=field.label,
                        help_text=help_text,
                        choices=[("", "Selecione")] + choices,
                    )
                    if field.id in answers:
                        selected = answers[field.id].selected_options.first()
                        if selected:
                            initial[key] = str(selected.id)
                else:
                    fields[key] = forms.MultipleChoiceField(
                        required=field_required,
                        label=field.label,
                        help_text=help_text,
                        choices=choices,
                        widget=forms.CheckboxSelectMultiple,
                    )
                    if field.id in answers:
                        initial[key] = [str(o.id) for o in answers[field.id].selected_options.all()]

            section_fields.append({"field": field, "key": key})

        sections.append({"section": section, "fields": section_fields})

    DynamicChecklistForm = type("DynamicChecklistForm", (forms.Form,), fields)
    form = DynamicChecklistForm(data=data, initial=initial)

    for section in sections:
        for item in section["fields"]:
            item["bound_field"] = form[item["key"]]

    return form, sections


def _save_checklist_answers(entry, template, cleaned_data):
    for section in _get_checklist_sections(template):
        for field in section.fields.all().order_by("sort_order", "id"):
            key = f"field_{field.id}"
            value = cleaned_data.get(key)
            answer, _ = ChecklistAnswer.objects.get_or_create(entry=entry, field=field)

            answer.value_text = None
            answer.value_number = None
            answer.value_date = None
            answer.value_time = None
            answer.value_bool = None

            if field.field_type in ("text", "textarea"):
                answer.value_text = (value or "").strip() if value is not None else ""
            elif field.field_type == "number":
                answer.value_number = value if value not in ("", None) else None
            elif field.field_type == "date":
                answer.value_date = value if value else None
            elif field.field_type == "time":
                answer.value_time = value if value else None
            elif field.field_type == "boolean":
                answer.value_bool = bool(value)

            answer.save()

            if field.field_type == "single_choice":
                option_ids = []
                if value:
                    option_ids = [int(value)]
                answer.selected_options.set(option_ids)
            elif field.field_type == "multi_choice":
                option_ids = [int(v) for v in (value or [])]
                answer.selected_options.set(option_ids)
            else:
                answer.selected_options.clear()


@login_required
def checklistList(request):
    entries = (
        ChecklistEntry.objects.select_related("template", "created_by")
        .order_by("-created_at")
    )
    templates = ChecklistTemplate.objects.filter(is_active=True).order_by("name")
    return render(
        request,
        "tiqueue/checklists.html",
        {"entries": entries, "templates": templates},
    )


@login_required
def checklistEntryCreate(request, template_id=None):
    template = None
    if template_id:
        template = get_object_or_404(ChecklistTemplate, pk=template_id)
    else:
        template = ChecklistTemplate.objects.filter(is_active=True).order_by("name").first()

    if not template:
        return redirect("checklistTemplates")

    if request.method == "POST":
        form, sections = _build_checklist_form(template, data=request.POST)
        if form.is_valid():
            entry = ChecklistEntry.objects.create(
                template=template,
                title=(form.cleaned_data.get("entry_title") or "").strip() or None,
                created_by=request.user,
            )
            _save_checklist_answers(entry, template, form.cleaned_data)
            return redirect("checklistList")
    else:
        form, sections = _build_checklist_form(template)

    return render(
        request,
        "tiqueue/checklist_form.html",
        {"form": form, "sections": sections, "template": template, "entry": None},
    )


@login_required
def checklistEntryEdit(request, entry_id):
    entry = get_object_or_404(ChecklistEntry, pk=entry_id)
    template = entry.template

    if request.method == "POST":
        form, sections = _build_checklist_form(template, entry=entry, data=request.POST)
        if form.is_valid():
            entry.title = (form.cleaned_data.get("entry_title") or "").strip() or None
            entry.save(update_fields=["title", "updated_at"])
            _save_checklist_answers(entry, template, form.cleaned_data)
            return redirect("checklistList")
    else:
        form, sections = _build_checklist_form(template, entry=entry)

    return render(
        request,
        "tiqueue/checklist_form.html",
        {"form": form, "sections": sections, "template": template, "entry": entry},
    )


@login_required
def checklistEntryPrint(request, entry_id):
    entry = get_object_or_404(ChecklistEntry, pk=entry_id)
    template = entry.template

    answers = {
        answer.field_id: answer
        for answer in ChecklistAnswer.objects.filter(entry=entry).prefetch_related("selected_options")
    }

    sections = []
    for section in _get_checklist_sections(template):
        fields = []
        for field in section.fields.all().order_by("sort_order", "id"):
            answer = answers.get(field.id)
            value = "-"
            if answer:
                if field.field_type in ("text", "textarea"):
                    value = answer.value_text or "-"
                elif field.field_type == "number":
                    value = answer.value_number if answer.value_number is not None else "-"
                elif field.field_type == "date":
                    value = answer.value_date.strftime("%d/%m/%Y") if answer.value_date else "-"
                elif field.field_type == "time":
                    value = answer.value_time.strftime("%H:%M") if answer.value_time else "-"
                elif field.field_type == "boolean":
                    if answer.value_bool is None:
                        value = "-"
                    else:
                        value = "Sim" if answer.value_bool else "Nao"
                elif field.field_type in ("single_choice", "multi_choice"):
                    selected = [opt.label for opt in answer.selected_options.all()]
                    value = ", ".join(selected) if selected else "-"

            fields.append({"label": field.label, "value": value, "help_text": field.help_text})

        sections.append({"title": section.title, "fields": fields})

    return render(
        request,
        "tiqueue/checklist_print.html",
        {"entry": entry, "template": template, "sections": sections},
    )


@login_required
def checklistTemplates(request):
    template_form = ChecklistTemplateForm(prefix="template")
    section_form = ChecklistSectionForm(prefix="section")
    field_form = ChecklistFieldForm(prefix="field")

    if request.method == "POST":
        form_id = request.POST.get("form_id")
        if form_id == "template":
            template_form = ChecklistTemplateForm(request.POST, prefix="template")
            if template_form.is_valid():
                template_form.save()
        elif form_id == "section":
            section_form = ChecklistSectionForm(request.POST, prefix="section")
            if section_form.is_valid():
                section_form.save()
        elif form_id == "field":
            field_form = ChecklistFieldForm(request.POST, prefix="field")
            if field_form.is_valid():
                field_form.save()
        elif form_id == "edit_template":
            template_id = request.POST.get("template_id")
            name = (request.POST.get("name") or "").strip()
            description = (request.POST.get("description") or "").strip()
            is_active = request.POST.get("is_active") == "on"
            if template_id and name:
                ChecklistTemplate.objects.filter(pk=template_id).update(
                    name=name, description=description or None, is_active=is_active
                )
        elif form_id == "edit_section":
            section_id = request.POST.get("section_id")
            title = (request.POST.get("title") or "").strip()
            sort_order = request.POST.get("sort_order")
            if section_id and title:
                update_data = {"title": title}
                if sort_order not in (None, ""):
                    update_data["sort_order"] = int(sort_order)
                ChecklistSection.objects.filter(pk=section_id).update(**update_data)
        elif form_id == "edit_field":
            field_id = request.POST.get("field_id")
            label = (request.POST.get("label") or "").strip()
            help_text = (request.POST.get("help_text") or "").strip()
            field_type = request.POST.get("field_type")
            required = request.POST.get("required") == "on"
            sort_order = request.POST.get("sort_order")
            choice_group = request.POST.get("choice_group") or None
            if field_id and label and field_type:
                update_data = {
                    "label": label,
                    "help_text": help_text or None,
                    "field_type": field_type,
                    "required": required,
                }
                if sort_order not in (None, ""):
                    update_data["sort_order"] = int(sort_order)
                update_data["choice_group_id"] = int(choice_group) if choice_group else None
                ChecklistField.objects.filter(pk=field_id).update(**update_data)

    templates = ChecklistTemplate.objects.all().order_by("name")
    sections = ChecklistSection.objects.select_related("template").order_by("template__name", "sort_order")
    fields = (
        ChecklistField.objects.select_related("section", "choice_group")
        .order_by("section__template__name", "section__sort_order", "sort_order")
    )
    choice_groups = ChecklistChoiceGroup.objects.order_by("name")

    return render(
        request,
        "tiqueue/checklist_templates.html",
        {
            "template_form": template_form,
            "section_form": section_form,
            "field_form": field_form,
            "templates": templates,
            "sections": sections,
            "fields": fields,
            "choice_groups": choice_groups,
        },
    )


@login_required
def checklistChoices(request):
    group_form = ChecklistChoiceGroupForm(prefix="group")
    option_form = ChecklistChoiceOptionForm(prefix="option")

    if request.method == "POST":
        form_id = request.POST.get("form_id")
        if form_id == "group":
            group_form = ChecklistChoiceGroupForm(request.POST, prefix="group")
            if group_form.is_valid():
                group_form.save()
        elif form_id == "option":
            option_form = ChecklistChoiceOptionForm(request.POST, prefix="option")
            if option_form.is_valid():
                option_form.save()
        elif form_id == "edit_group":
            group_id = request.POST.get("group_id")
            name = (request.POST.get("name") or "").strip()
            description = (request.POST.get("description") or "").strip()
            if group_id and name:
                ChecklistChoiceGroup.objects.filter(pk=group_id).update(
                    name=name, description=description or None
                )
        elif form_id == "edit_option":
            option_id = request.POST.get("option_id")
            label = (request.POST.get("label") or "").strip()
            group_id = request.POST.get("group")
            if option_id and label and group_id:
                ChecklistChoiceOption.objects.filter(pk=option_id).update(
                    label=label, group_id=int(group_id)
                )

    groups = ChecklistChoiceGroup.objects.all().order_by("name")
    options = ChecklistChoiceOption.objects.select_related("group").order_by("group__name", "label")

    return render(
        request,
        "tiqueue/checklist_choices.html",
        {
            "group_form": group_form,
            "option_form": option_form,
            "groups": groups,
            "options": options,
        },
    )


@login_required
def systemSettingsPage(request):
    can_manage = _is_system_admin(request.user)
    config = SystemConfig.objects.order_by("-updated_at", "-id").first()
    access_denied_message = None if can_manage else "Voce nao possui acesso a este modulo."

    if request.method == "POST" and can_manage:
        form_id = (request.POST.get("form_id") or "save_system_settings").strip()
        if form_id == "save_system_settings":
            version = (request.POST.get("system_version") or "").strip()
            service_agent_url = (request.POST.get("service_agent_url") or "").strip()
            service_agent_token = (request.POST.get("service_agent_token") or "").strip()
            timeout_raw = (request.POST.get("service_agent_timeout_sec") or "").strip()
            try:
                timeout = max(1, int(timeout_raw or "8"))
            except Exception:
                timeout = 8

            if config is None:
                config = SystemConfig.objects.create(
                    system_version=version or None,
                    service_agent_url=service_agent_url or None,
                    service_agent_token=service_agent_token or None,
                    service_agent_timeout_sec=timeout,
                )
            else:
                config.system_version = version or None
                config.service_agent_url = service_agent_url or None
                config.service_agent_token = service_agent_token or None
                config.service_agent_timeout_sec = timeout
                config.save(
                    update_fields=[
                        "system_version",
                        "service_agent_url",
                        "service_agent_token",
                        "service_agent_timeout_sec",
                        "updated_at",
                    ]
                )
            messages.success(request, "Configurações gerais salvas.")
        elif form_id == "save_openai_settings":
            if config is None:
                config = SystemConfig.objects.create()

            base_url = (request.POST.get("openai_base_url") or "https://api.openai.com/v1").strip().rstrip("/")
            model = (request.POST.get("openai_model") or "gpt-5.6-sol").strip()
            reasoning_effort = (request.POST.get("openai_reasoning_effort") or "medium").strip().lower()
            if reasoning_effort not in ALLOWED_REASONING_EFFORTS:
                reasoning_effort = "medium"
            try:
                timeout = min(max(int(request.POST.get("openai_timeout_sec") or 120), 10), 600)
            except (TypeError, ValueError):
                timeout = 120
            try:
                max_output_tokens = min(max(int(request.POST.get("openai_max_output_tokens") or 5000), 500), 50000)
            except (TypeError, ValueError):
                max_output_tokens = 5000

            api_key = (request.POST.get("openai_api_key") or "").strip()
            if request.POST.get("clear_openai_api_key") == "1":
                config.openai_api_key_encrypted = None
            elif api_key:
                config.openai_api_key_encrypted = encrypt_secret(api_key)

            config.openai_enabled = request.POST.get("openai_enabled") == "on"
            config.openai_base_url = base_url
            config.openai_model = model
            config.openai_reasoning_effort = reasoning_effort
            config.openai_timeout_sec = timeout
            config.openai_max_output_tokens = max_output_tokens
            config.save(
                update_fields=[
                    "openai_enabled",
                    "openai_api_key_encrypted",
                    "openai_base_url",
                    "openai_model",
                    "openai_reasoning_effort",
                    "openai_timeout_sec",
                    "openai_max_output_tokens",
                    "updated_at",
                ]
            )
            messages.success(request, "Configuração OpenAI salva com segurança.")
        elif form_id == "test_openai_connection":
            try:
                resolved_model = test_openai_connection()
                messages.success(request, f"Conexão com a OpenAI validada para o modelo {resolved_model}.")
            except OpenAIInsightError as exc:
                messages.error(request, str(exc))
        return redirect("systemSettingsPage")

    return render(
        request,
        "tiqueue/system_settings.html",
        {
            "config": config,
            "can_manage": can_manage,
            "access_denied_message": access_denied_message,
            "openai_runtime": public_openai_runtime_config(),
            "openai_reasoning_efforts": sorted(ALLOWED_REASONING_EFFORTS),
        },
    )


def _is_system_admin(user):
    return bool(getattr(user, "is_system_admin", False) or getattr(user, "is_superuser", False))


def _get_service_agent_config():
    config = SystemConfig.objects.order_by("-updated_at", "-id").first()
    if not config:
        return None, None, 8
    base_url = (config.service_agent_url or "").strip().rstrip("/")
    token = (config.service_agent_token or "").strip()
    timeout = int(config.service_agent_timeout_sec or 8)
    if timeout <= 0:
        timeout = 8
    return base_url, token, timeout


def _service_agent_request(path, method="GET", payload=None, query=None):
    base_url, token, timeout = _get_service_agent_config()
    if not base_url or not token:
        return {"ok": False, "error": "Configure URL e token do Service Agent nas configuracoes."}

    full_url = f"{base_url}{path}"
    if query:
        full_url = f"{full_url}?{urllib_parse.urlencode(query)}"

    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib_request.Request(full_url, data=body, method=method.upper(), headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            return {"ok": True, "status": resp.status, "data": data}
    except urllib_error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        detail = raw
        try:
            parsed = json.loads(raw)
            detail = parsed.get("detail") or parsed.get("message") or raw
        except Exception:
            pass
        return {"ok": False, "status": exc.code, "error": f"Service Agent retornou erro: {detail}"}
    except Exception as exc:
        return {"ok": False, "error": f"Falha ao conectar no Service Agent: {exc}"}


@login_required
def serviceAgentPage(request):
    can_manage = _is_system_admin(request.user)
    access_denied_message = None
    if not can_manage:
        access_denied_message = "Voce nao possui acesso a este modulo."

    return render(
        request,
        "tiqueue/service_agent.html",
        {
            "can_manage": can_manage,
            "access_denied_message": access_denied_message,
        },
    )


@login_required
@require_GET
def serviceAgentList(request):
    if not _is_system_admin(request.user):
        return JsonResponse({"status": "error", "message": "Sem permissao"}, status=403)

    q = (request.GET.get("q") or "").strip()
    result = _service_agent_request("/services", method="GET", query={"q": q} if q else None)
    if not result.get("ok"):
        return JsonResponse({"status": "error", "message": result.get("error")}, status=400)
    return JsonResponse({"status": "ok", "items": result.get("data", [])})


@login_required
@require_POST
def serviceAgentAction(request, service_name, action):
    if not _is_system_admin(request.user):
        return JsonResponse({"status": "error", "message": "Sem permissao"}, status=403)
    if action not in ("start", "stop", "restart"):
        return JsonResponse({"status": "error", "message": "Acao invalida"}, status=400)

    result = _service_agent_request(f"/services/{service_name}/{action}", method="POST", payload={})
    if not result.get("ok"):
        return JsonResponse({"status": "error", "message": result.get("error")}, status=400)
    return JsonResponse({"status": "ok", "data": result.get("data", {})})


def _cleanup_erp_users():
    host = os.getenv("ERP_DB_HOST", "192.168.30.2")
    port = int(os.getenv("ERP_DB_PORT", "1521"))
    db_name = os.getenv("ERP_DB_NAME", "dbprod")
    db_user = os.getenv("ERP_DB_USER", "sapiens")
    db_pass = os.getenv("ERP_DB_PASSWORD", "sapiens")

    last_error = None
    for driver_name in ("oracledb", "cx_Oracle"):
        try:
            if driver_name == "oracledb":
                import oracledb as oracle_driver  # type: ignore
            else:
                import cx_Oracle as oracle_driver  # type: ignore

            dsn = oracle_driver.makedsn(host, port, service_name=db_name)
            conn = oracle_driver.connect(user=db_user, password=db_pass, dsn=dsn)
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM R911MOD")
                cur.execute("DELETE FROM R911SEC")
                conn.commit()
                return None
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
                conn.close()
        except Exception as exc:
            last_error = f"{driver_name}: {exc}"
            continue

    return (
        "Falha ao executar limpeza no ERP. "
        "Instale um driver Oracle (`oracledb` ou `cx_Oracle`) e valide a conectividade. "
        f"Detalhe: {last_error or 'driver nao encontrado'}"
    )


@login_required
@require_POST
def serviceAgentErpCleanupUsers(request):
    if not _is_system_admin(request.user):
        return JsonResponse({"status": "error", "message": "Sem permissao"}, status=403)

    confirm_text = (request.POST.get("confirm_text") or "").strip()
    if confirm_text != "LIMPAR USUARIOS ERP":
        return JsonResponse(
            {
                "status": "error",
                "message": 'Confirmacao invalida. Digite exatamente: "LIMPAR USUARIOS ERP".',
            },
            status=400,
        )

    error = _cleanup_erp_users()
    if error:
        return JsonResponse({"status": "error", "message": error}, status=400)
    return JsonResponse({"status": "ok", "message": "Usuarios do ERP limpos com sucesso (R911MOD e R911SEC)."})


@login_required
def seniorUpdatesPage(request):
    if request.method == "POST":
        erp_version = (request.POST.get("erp_version") or "").strip()
        hcm_version = (request.POST.get("hcm_version") or "").strip()
        sde_version = (request.POST.get("sde_version") or "").strip()

        if erp_version and hcm_version and sde_version:
            SeniorSystemUpdate.objects.create(
                erp_version=erp_version,
                hcm_version=hcm_version,
                sde_version=sde_version,
                folder_name=(request.POST.get("folder_name") or "").strip() or None,
                release_date=(request.POST.get("release_date") or None),
                download_date=(request.POST.get("download_date") or None),
                planned_apply_date=(request.POST.get("planned_apply_date") or None),
                real_apply_date=(request.POST.get("real_apply_date") or None),
                in_production=(request.POST.get("in_production") == "on"),
                in_test_base=(request.POST.get("in_test_base") == "on"),
                in_simulation_base=(request.POST.get("in_simulation_base") == "on"),
                sent_to_drive=(request.POST.get("sent_to_drive") == "on"),
            )
        return redirect("seniorUpdatesPage")

    rows = SeniorSystemUpdate.objects.all()
    return render(
        request,
        "tiqueue/seniorUpdates.html",
        {
            "rows": rows,
            "erp_docs_url": "https://documentacao.senior.com.br/gestaoempresarialerp/notasdaversao/",
            "hcm_docs_url": "https://documentacao.senior.com.br/gestao-de-pessoas-hcm/notas-da-versao/",
            "sde_docs_url": "https://documentacao.senior.com.br/documentoseletronicos/notasdaversao/",
            "drive_folder_url": "https://drive.google.com/drive/folders/12tbd0xhKEjWf341IwMqPrdoKh5OZmyeT?usp=sharing",
        },
    )


@login_required
@require_POST
def seniorUpdatesInlineUpdate(request, update_id):
    row = get_object_or_404(SeniorSystemUpdate, pk=update_id)
    optional_text_fields = {"folder_name"}

    editable_fields = {
        "erp_version": "text",
        "hcm_version": "text",
        "sde_version": "text",
        "folder_name": "text",
        "release_date": "date",
        "download_date": "date",
        "planned_apply_date": "date",
        "real_apply_date": "date",
        "in_production": "bool",
        "in_test_base": "bool",
        "in_simulation_base": "bool",
        "sent_to_drive": "bool",
    }

    updated_fields = []
    for field, field_type in editable_fields.items():
        if field not in request.POST:
            continue

        raw = request.POST.get(field)
        if field_type == "bool":
            value = str(raw).lower() in ("1", "true", "on", "yes")
        elif field_type == "date":
            value = raw or None
        else:
            value = (raw or "").strip()
            if not value and field not in optional_text_fields:
                return JsonResponse({"status": "error", "message": f"{field} obrigatorio"}, status=400)
            if not value and field in optional_text_fields:
                value = None

        setattr(row, field, value)
        updated_fields.append(field)

    if not updated_fields:
        return JsonResponse({"status": "error", "message": "Nenhum campo informado"}, status=400)

    row.save(update_fields=updated_fields + ["updated_at"])
    return JsonResponse({"status": "ok"})


def _refresh_wifi_voucher_statuses():
    now = timezone.now()
    WifiVoucher.objects.filter(
        status=WifiVoucher.STATUS_PENDING,
        expires_at__isnull=False,
        expires_at__lte=now,
    ).update(status=WifiVoucher.STATUS_USED)


def _extract_voucher_codes_from_pdf(file_obj):
    raw = file_obj.read()
    text = ""

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        page_text = []
        for page in reader.pages:
            page_text.append(page.extract_text() or "")
        text = "\n".join(page_text)
    except Exception:
        text = ""

    # Fallback parser for PDFs that cannot be parsed by pypdf.
    if not text.strip():
        text = raw.decode("latin1", errors="ignore")

    # Vouchers are numeric ids (example: 991309). Keep 6+ digits to avoid date fragments.
    found = re.findall(r"\b\d{6,20}\b", text)
    codes = []
    seen = set()
    for code in found:
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


@login_required
def wifiVoucherPage(request):
    _refresh_wifi_voucher_statuses()
    fast_result = None

    if request.method == "POST":
        form_id = request.POST.get("form_id")

        if form_id == "create_group":
            duration_raw = request.POST.get("duration_hours")
            expected_raw = request.POST.get("expected_quantity")
            name = (request.POST.get("name") or "").strip()

            try:
                duration_hours = int(duration_raw or "0")
            except Exception:
                duration_hours = 0

            try:
                expected_quantity = int(expected_raw or "0")
            except Exception:
                expected_quantity = 0

            if duration_hours > 0:
                WifiVoucherGroup.objects.create(
                    name=name or None,
                    duration_hours=duration_hours,
                    expected_quantity=max(0, expected_quantity),
                )
            return redirect("wifiVoucherPage")

        if form_id == "add_voucher":
            group_id = request.POST.get("group_id")
            voucher_code = (request.POST.get("voucher_code") or "").strip()
            if group_id and voucher_code:
                group = get_object_or_404(WifiVoucherGroup, pk=group_id)
                WifiVoucher.objects.get_or_create(group=group, voucher_code=voucher_code)
            return redirect("wifiVoucherPage")

        if form_id == "import_pdf":
            group_id = request.POST.get("group_id")
            pdf_file = request.FILES.get("voucher_pdf")
            if group_id and pdf_file:
                group = get_object_or_404(WifiVoucherGroup, pk=group_id)
                codes = _extract_voucher_codes_from_pdf(pdf_file)
                for code in codes:
                    WifiVoucher.objects.get_or_create(group=group, voucher_code=code)
            return redirect("wifiVoucherPage")
        if form_id == "fast_deliver":
            group_id = request.POST.get("group_id")
            qty_raw = request.POST.get("quantity")
            delivered_codes = []
            requested_qty = 0
            group = None

            try:
                requested_qty = max(0, int(qty_raw or "0"))
            except Exception:
                requested_qty = 0

            if group_id and requested_qty > 0:
                group = get_object_or_404(WifiVoucherGroup, pk=group_id)
                now = timezone.now()
                expires_at = now + timedelta(hours=group.duration_hours)

                with transaction.atomic():
                    selected = list(
                        WifiVoucher.objects.select_for_update()
                        .filter(group=group, status=WifiVoucher.STATUS_AVAILABLE)
                        .order_by("voucher_code", "id")[:requested_qty]
                    )
                    selected_ids = [v.id for v in selected]
                    delivered_codes = [v.voucher_code for v in selected]

                    if selected_ids:
                        WifiVoucher.objects.filter(id__in=selected_ids).update(
                            status=WifiVoucher.STATUS_PENDING,
                            delivered_at=now,
                            expires_at=expires_at,
                        )

            if group:
                request.session["wifi_fast_result"] = {
                    "group_id": group.id,
                    "group_label": group.display_label,
                    "requested_qty": requested_qty,
                    "delivered_qty": len(delivered_codes),
                    "codes": delivered_codes,
                }
                return redirect(f"{reverse('wifiVoucherPage')}?open_group={group.id}")
            return redirect("wifiVoucherPage")

    if "wifi_fast_result" in request.session:
        fast_result = request.session.pop("wifi_fast_result", None)

    groups = list(
        WifiVoucherGroup.objects.annotate(
            total_vouchers=Count("vouchers"),
            available_count=Count("vouchers", filter=Q(vouchers__status=WifiVoucher.STATUS_AVAILABLE)),
            pending_count=Count("vouchers", filter=Q(vouchers__status=WifiVoucher.STATUS_PENDING)),
            used_count=Count("vouchers", filter=Q(vouchers__status=WifiVoucher.STATUS_USED)),
        ).order_by("duration_hours", "id")
    )

    group_ids = {g.id for g in groups}
    try:
        open_group = int(request.GET.get("open_group") or 0)
    except Exception:
        open_group = 0
    if open_group not in group_ids:
        open_group = groups[0].id if groups else 0

    group_cards = []
    for g in groups:
        page_key = f"page_{g.id}"
        page_number = request.GET.get(page_key) or 1
        vouchers_qs = WifiVoucher.objects.filter(group=g).order_by("status", "voucher_code", "id")
        paginator = Paginator(vouchers_qs, 12)
        page_obj = paginator.get_page(page_number)
        group_cards.append(
            {
                "group": g,
                "page_obj": page_obj,
                "page_key": page_key,
                "is_open": g.id == open_group,
            }
        )

    return render(
        request,
        "tiqueue/wifiVouchers.html",
        {
            "groups": groups,
            "group_cards": group_cards,
            "open_group": open_group,
            "fast_result": fast_result,
        },
    )


@login_required
@require_POST
def wifiVoucherDeliver(request, voucher_id):
    _refresh_wifi_voucher_statuses()
    voucher = get_object_or_404(WifiVoucher, pk=voucher_id)

    if voucher.status == WifiVoucher.STATUS_USED:
        return JsonResponse({"status": "error", "message": "Voucher ja utilizado"}, status=400)

    now = timezone.now()
    voucher.delivered_at = now
    voucher.expires_at = now + timedelta(hours=voucher.group.duration_hours)
    voucher.status = WifiVoucher.STATUS_PENDING
    voucher.save(update_fields=["delivered_at", "expires_at", "status"])

    return JsonResponse({"status": "ok"})


@login_required
def maintenancePage(request):
    return redirect("maintenanceSchedulePage")


MAINTENANCE_CATALOG_MODELS = {
    "toggle_type": MaintenanceType,
    "toggle_situation": MaintenanceSituation,
    "toggle_indicator": MaintenanceIndicator,
    "toggle_group": MaintenanceSystemGroup,
    "toggle_system": MaintenanceSystem,
}


def _maintenance_catalog_forms():
    return {
        "type_form": MaintenanceTypeForm(prefix="type"),
        "situation_form": MaintenanceSituationForm(prefix="sit"),
        "indicator_form": MaintenanceIndicatorForm(prefix="ind"),
        "group_form": MaintenanceSystemGroupForm(prefix="grp"),
        "system_form": MaintenanceSystemForm(prefix="sys"),
    }


@login_required
def maintenanceCatalogPage(request):
    forms_data = _maintenance_catalog_forms()
    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()
        if form_id == "create_type":
            forms_data["type_form"] = MaintenanceTypeForm(request.POST, prefix="type")
            if forms_data["type_form"].is_valid():
                forms_data["type_form"].save()
                return redirect("maintenanceCatalogPage")
        elif form_id == "create_situation":
            forms_data["situation_form"] = MaintenanceSituationForm(request.POST, prefix="sit")
            if forms_data["situation_form"].is_valid():
                forms_data["situation_form"].save()
                return redirect("maintenanceCatalogPage")
        elif form_id == "create_indicator":
            forms_data["indicator_form"] = MaintenanceIndicatorForm(request.POST, prefix="ind")
            if forms_data["indicator_form"].is_valid():
                forms_data["indicator_form"].save()
                return redirect("maintenanceCatalogPage")
        elif form_id == "create_group":
            forms_data["group_form"] = MaintenanceSystemGroupForm(request.POST, prefix="grp")
            if forms_data["group_form"].is_valid():
                forms_data["group_form"].save()
                return redirect("maintenanceCatalogPage")
        elif form_id == "create_system":
            forms_data["system_form"] = MaintenanceSystemForm(request.POST, prefix="sys")
            if forms_data["system_form"].is_valid():
                forms_data["system_form"].save()
                return redirect("maintenanceCatalogPage")
        elif form_id in MAINTENANCE_CATALOG_MODELS:
            model = MAINTENANCE_CATALOG_MODELS[form_id]
            item_id = (request.POST.get("item_id") or "").strip()
            item = model.objects.filter(pk=item_id).first() if item_id.isdigit() else None
            if item:
                item.is_active = not item.is_active
                item.save(update_fields=["is_active"])
            return redirect("maintenanceCatalogPage")

    return render(
        request,
        "tiqueue/maintenance_catalog.html",
        {
            **forms_data,
            "types": MaintenanceType.objects.all().order_by("name"),
            "situations": MaintenanceSituation.objects.all().order_by("name"),
            "indicators": MaintenanceIndicator.objects.all().order_by("name"),
            "system_groups": MaintenanceSystemGroup.objects.all().order_by("name"),
            "systems": MaintenanceSystem.objects.select_related("group").all().order_by("group__name", "name"),
        },
    )


MAINTENANCE_STATUS_TONES = {"done": "success", "upcoming": "info", "late": "danger", "running": "warning"}
MAINTENANCE_SCHEDULE_LABELS = {
    "done": "Concluído",
    "upcoming": "Agendado",
    "late": "Em atraso",
    "running": "Em andamento",
}
MAINTENANCE_OUTAGE_LABELS = {
    "done": "Normalizada",
    "upcoming": "Programada",
    "late": "Sem retorno",
    "running": "Em aberto",
}


def _format_maintenance_duration(delta):
    """Timedelta em texto curto ('2h 15min'). Devolve None quando nao ha o que medir."""
    if delta is None or delta.total_seconds() < 0:
        return None
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}min" if minutes else f"{hours}h"
    return f"{minutes}min"


def _decorate_maintenance_events(events, now, labels=MAINTENANCE_SCHEDULE_LABELS):
    """Marca cada evento com o estado da janela, usado pelos selos e filtros da tela."""
    for ev in events:
        if ev.real_return:
            ev.status_key = "done"
        elif ev.scheduled_start and ev.scheduled_start > now:
            ev.status_key = "upcoming"
        elif ev.expected_return and ev.expected_return < now:
            ev.status_key = "late"
        else:
            ev.status_key = "running"

        ev.status_label = labels[ev.status_key]
        ev.status_tone = MAINTENANCE_STATUS_TONES[ev.status_key]

        if ev.real_return and ev.scheduled_start:
            ev.duration_label = _format_maintenance_duration(ev.real_return - ev.scheduled_start)
        elif ev.scheduled_start and ev.status_key in ("running", "late"):
            ev.duration_label = _format_maintenance_duration(now - ev.scheduled_start)
        else:
            ev.duration_label = None
    return events


@login_required
def maintenanceOutagePage(request):
    outage_form = MaintenanceEventForm(prefix="out")
    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()
        if form_id == "create_outage":
            outage_form = MaintenanceEventForm(request.POST, prefix="out")
            if outage_form.is_valid():
                event = outage_form.save(commit=False)
                event.created_by = request.user
                event.is_outage = True
                event.save()
                outage_form.save_m2m()
                return redirect("maintenanceOutagePage")

    now = timezone.now()
    base_qs = MaintenanceEvent.objects.filter(is_outage=True)
    recent_outage = _decorate_maintenance_events(
        list(
            base_qs.select_related("maintenance_type", "situation", "indicator", "system_group")
            .prefetch_related("affected_systems")
            .order_by("-scheduled_start", "-id")[:50]
        ),
        now,
        MAINTENANCE_OUTAGE_LABELS,
    )

    closed = base_qs.filter(real_return__isnull=False).values_list("scheduled_start", "real_return")
    spans = [end - start for start, end in closed if start and end and end >= start]
    average_downtime = _format_maintenance_duration(sum(spans, timedelta()) / len(spans)) if spans else None

    return render(
        request,
        "tiqueue/maintenance_outages.html",
        {
            "outage_form": outage_form,
            "recent_outage": recent_outage,
            "has_systems": MaintenanceSystem.objects.filter(is_active=True).exists(),
            "stats": {
                "total": base_qs.count(),
                "open": base_qs.filter(real_return__isnull=True).count(),
                "last_30_days": base_qs.filter(scheduled_start__gte=now - timedelta(days=30)).count(),
                "incidents": base_qs.filter(indicator__is_incident=True).count(),
                "average_downtime": average_downtime,
            },
        },
    )


@login_required
def maintenanceSchedulePage(request):
    schedule_form = MaintenanceEventForm(prefix="sch")
    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()
        if form_id == "create_schedule":
            schedule_form = MaintenanceEventForm(request.POST, prefix="sch")
            if schedule_form.is_valid():
                event = schedule_form.save(commit=False)
                event.created_by = request.user
                event.is_outage = False
                event.save()
                schedule_form.save_m2m()
                return redirect("maintenanceSchedulePage")

    now = timezone.now()
    base_qs = MaintenanceEvent.objects.filter(is_outage=False)
    recent_schedule = _decorate_maintenance_events(
        list(
            base_qs.select_related("maintenance_type", "situation", "indicator", "system_group")
            .prefetch_related("affected_systems")
            .order_by("-scheduled_start", "-id")[:50]
        ),
        now,
    )

    return render(
        request,
        "tiqueue/maintenance_schedule.html",
        {
            "schedule_form": schedule_form,
            "recent_schedule": recent_schedule,
            "has_systems": MaintenanceSystem.objects.filter(is_active=True).exists(),
            "stats": {
                "total": base_qs.count(),
                "upcoming": base_qs.filter(scheduled_start__gt=now).count(),
                "next_7_days": base_qs.filter(
                    scheduled_start__gte=now, scheduled_start__lt=now + timedelta(days=7)
                ).count(),
                "running": base_qs.filter(scheduled_start__lte=now, real_return__isnull=True).count(),
                "done": base_qs.filter(real_return__isnull=False).count(),
            },
        },
    )


def _maintenance_calendar_payload(events, now):
    """
    Serializa os eventos para o grid do calendario.

    A data vai como texto local ja formatado ('2026-08-18'), nao como ISO em UTC:
    o `new Date(iso)` do navegador reinterpreta o fuso e jogava eventos da
    madrugada para o dia anterior, divergindo das tabelas renderizadas pelo Django.
    """
    payload = []
    for ev in _decorate_maintenance_events(list(events), now):
        start = timezone.localtime(ev.scheduled_start) if ev.scheduled_start else None
        expected = timezone.localtime(ev.expected_return) if ev.expected_return else None
        real = timezone.localtime(ev.real_return) if ev.real_return else None
        if start is None:
            continue
        payload.append(
            {
                "id": ev.id,
                "title": ev.title,
                "day": start.strftime("%Y-%m-%d"),
                "time": start.strftime("%H:%M"),
                "start_label": start.strftime("%d/%m/%Y %H:%M"),
                "expected_label": expected.strftime("%d/%m/%Y %H:%M") if expected else "",
                "real_label": real.strftime("%d/%m/%Y %H:%M") if real else "",
                "is_outage": ev.is_outage,
                "type_color": (ev.maintenance_type.color if ev.maintenance_type_id else "#343955"),
                "type_name": ev.maintenance_type.name if ev.maintenance_type_id else "",
                "situation": ev.situation.name if ev.situation_id else "",
                "indicator": ev.indicator.name if ev.indicator_id else "",
                "is_incident": bool(ev.indicator_id and ev.indicator.is_incident),
                "group": ev.system_group.name if ev.system_group_id else "",
                "systems": [s.name for s in ev.affected_systems.all()],
                "short_description": ev.short_description or "",
                "full_description": ev.full_description or "",
                "status_key": ev.status_key,
                "status_label": (
                    MAINTENANCE_OUTAGE_LABELS if ev.is_outage else MAINTENANCE_SCHEDULE_LABELS
                )[ev.status_key],
                "status_tone": ev.status_tone,
                "duration_label": ev.duration_label or "",
            }
        )
    return payload


@login_required
def maintenanceCalendarPage(request):
    event_form = MaintenanceEventForm(prefix="event")
    open_modal = False

    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()
        if form_id == "create_event":
            event_form = MaintenanceEventForm(request.POST, prefix="event")
            if event_form.is_valid():
                event = event_form.save(commit=False)
                event.created_by = request.user
                event.save()
                event_form.save_m2m()
                return redirect("maintenanceCalendarPage")
            open_modal = True

    now = timezone.now()
    events = (
        MaintenanceEvent.objects.select_related("maintenance_type", "situation", "indicator", "system_group")
        .prefetch_related("affected_systems")
        .order_by("scheduled_start", "id")
    )
    upcoming = _decorate_maintenance_events(
        list(events.filter(scheduled_start__gte=now)[:8]),
        now,
    )

    return render(
        request,
        "tiqueue/maintenance_calendar.html",
        {
            "event_form": event_form,
            "open_modal": open_modal,
            "upcoming": upcoming,
            "has_systems": MaintenanceSystem.objects.filter(is_active=True).exists(),
            # Vai como json_script no template: os titulos sao texto do usuario e
            # um "</script>" dentro de um json.dumps|safe quebraria a pagina.
            "calendar_items": _maintenance_calendar_payload(events, now),
        },
    )


@login_required
def myAgendaPage(request):
    reminder_form = MyAgendaReminderForm(prefix="rem")
    today = timezone.localdate()

    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()
        if form_id == "create_reminder":
            reminder_form = MyAgendaReminderForm(request.POST, prefix="rem")
            if reminder_form.is_valid():
                reminder = reminder_form.save(commit=False)
                if reminder.reminder_date < today:
                    reminder_form.add_error("reminder_date", "Não é permitido agendar em datas passadas.")
                else:
                    reminder.user = request.user
                    reminder.save()
                    return redirect("myAgendaPage")
        elif form_id == "toggle_done":
            reminder_id = request.POST.get("reminder_id")
            reminder = MyAgendaReminder.objects.filter(pk=reminder_id, user=request.user).first()
            if reminder:
                reminder.is_done = not reminder.is_done
                reminder.save(update_fields=["is_done", "updated_at"])
            return redirect("myAgendaPage")

    reminders = (
        MyAgendaReminder.objects.filter(user=request.user)
        .order_by("reminder_date", "reminder_time", "id")
    )
    calendar_items = []
    for r in reminders:
        start_iso = r.reminder_date.isoformat()
        if r.reminder_time:
            dt = datetime.combine(r.reminder_date, r.reminder_time)
            start_iso = dt.isoformat()
        color = "#4a87ff"
        if r.priority == MyAgendaReminder.PRIORITY_HIGH:
            color = "#d63f56"
        elif r.priority == MyAgendaReminder.PRIORITY_LOW:
            color = "#35b37e"
        if r.color:
            color = r.color
        if r.is_done:
            color = "#8b94aa"
        calendar_items.append(
            {
                "id": r.id,
                "title": r.title,
                "start": start_iso,
                "priority": r.priority,
                "is_done": r.is_done,
                "color": color,
            }
        )

    upcoming = reminders.filter(reminder_date__gte=today)[:30]
    return render(
        request,
        "tiqueue/my_agenda.html",
        {
            "reminder_form": reminder_form,
            "calendar_items_json": json.dumps(calendar_items),
            "upcoming": upcoming,
        },
    )


@login_required
def dataModelerPage(request):
    launches = DataModelLaunch.objects.all().order_by("-created_at", "-id")
    selected_id = request.GET.get("launch_id")
    selected_launch = None
    if selected_id and str(selected_id).isdigit():
        selected_launch = launches.filter(pk=int(selected_id)).first()
    if selected_launch is None:
        selected_launch = launches.first()
    return render(
        request,
        "tiqueue/data_modeler.html",
        {
            "launches": launches,
            "selected_launch": selected_launch,
        },
    )


@login_required
@require_GET
def dataModelerState(request):
    launch_id = request.GET.get("launch_id")
    if not launch_id or not str(launch_id).isdigit():
        return JsonResponse({"status": "error", "message": "launch_id inválido"}, status=400)

    launch = DataModelLaunch.objects.filter(pk=int(launch_id)).first()
    if not launch:
        return JsonResponse({"status": "error", "message": "Lançamento não encontrado"}, status=404)

    tables = list(
        DataModelTable.objects.filter(launch=launch).prefetch_related("fields").order_by("id")
    )
    fields_map = {}
    for table in tables:
        fields_map[table.id] = [
            {
                "id": field.id,
                "name": field.name,
                "data_type": field.data_type,
                "size": field.size or "",
                "is_primary": field.is_primary,
                "is_nullable": field.is_nullable,
                "sort_order": field.sort_order,
            }
            for field in table.fields.all()
        ]

    relations = list(
        DataModelRelation.objects.filter(launch=launch)
        .select_related("source_field__table", "target_field__table")
        .order_by("id")
    )

    payload = {
        "status": "ok",
        "launch": {
            "id": launch.id,
            "name": launch.name,
            "description": launch.description or "",
        },
        "tables": [
            {
                "id": t.id,
                "name": t.name,
                "x": t.x,
                "y": t.y,
                "color": t.color,
                "fields": fields_map.get(t.id, []),
            }
            for t in tables
        ],
        "relations": [
            {
                "id": rel.id,
                "source_field_id": rel.source_field_id,
                "target_field_id": rel.target_field_id,
                "relation_type": rel.relation_type,
            }
            for rel in relations
        ],
    }
    return JsonResponse(payload)


@login_required
@require_POST
def dataModelerLaunchCreate(request):
    name = (request.POST.get("name") or "").strip()
    description = (request.POST.get("description") or "").strip()
    if not name:
        return JsonResponse({"status": "error", "message": "Nome do lançamento é obrigatório."}, status=400)
    launch, created = DataModelLaunch.objects.get_or_create(
        name=name, defaults={"description": description or None, "created_by": request.user}
    )
    if not created:
        launch.description = description or launch.description
        launch.save(update_fields=["description", "updated_at"])
    return JsonResponse({"status": "ok", "launch_id": launch.id})


@login_required
@require_POST
def dataModelerTableCreate(request):
    launch_id = request.POST.get("launch_id")
    name = (request.POST.get("name") or "").strip()
    color = (request.POST.get("color") or "").strip() or "#343955"
    if not launch_id or not str(launch_id).isdigit() or not name:
        return JsonResponse({"status": "error", "message": "Dados inválidos para criar tabela."}, status=400)
    launch = DataModelLaunch.objects.filter(pk=int(launch_id)).first()
    if not launch:
        return JsonResponse({"status": "error", "message": "Lançamento não encontrado."}, status=404)
    table = DataModelTable.objects.create(launch=launch, name=name, color=color)
    return JsonResponse({"status": "ok", "table_id": table.id})


@login_required
@require_POST
def dataModelerTableUpdate(request, table_id):
    table = DataModelTable.objects.filter(pk=table_id).first()
    if not table:
        return JsonResponse({"status": "error", "message": "Tabela não encontrada."}, status=404)
    if "name" in request.POST:
        table.name = (request.POST.get("name") or table.name).strip() or table.name
    if "color" in request.POST:
        table.color = (request.POST.get("color") or "").strip() or table.color
    if "x" in request.POST and "y" in request.POST:
        try:
            table.x = int(float(request.POST.get("x")))
            table.y = int(float(request.POST.get("y")))
        except Exception:
            pass
    table.save()
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def dataModelerFieldCreate(request, table_id):
    table = DataModelTable.objects.filter(pk=table_id).first()
    if not table:
        return JsonResponse({"status": "error", "message": "Tabela não encontrada."}, status=404)
    name = (request.POST.get("name") or "").strip()
    data_type = (request.POST.get("data_type") or "varchar").strip()
    size = (request.POST.get("size") or "").strip() or None
    is_primary = str(request.POST.get("is_primary", "")).lower() in ("1", "true", "on", "yes")
    is_nullable = str(request.POST.get("is_nullable", "true")).lower() in ("1", "true", "on", "yes")
    if not name:
        return JsonResponse({"status": "error", "message": "Nome do campo é obrigatório."}, status=400)
    sort_order = (table.fields.aggregate(mx=Coalesce(models.Max("sort_order"), 0)).get("mx") or 0) + 1
    field = DataModelField.objects.create(
        table=table,
        name=name,
        data_type=data_type,
        size=size,
        is_primary=is_primary,
        is_nullable=is_nullable,
        sort_order=sort_order,
    )
    return JsonResponse({"status": "ok", "field_id": field.id})


@login_required
@require_POST
def dataModelerRelationCreate(request):
    launch_id = request.POST.get("launch_id")
    source_field_id = request.POST.get("source_field_id")
    target_field_id = request.POST.get("target_field_id")
    relation_type = (request.POST.get("relation_type") or "1:N").strip()
    if not all([launch_id, source_field_id, target_field_id]):
        return JsonResponse({"status": "error", "message": "Dados incompletos para relação."}, status=400)
    if source_field_id == target_field_id:
        return JsonResponse({"status": "error", "message": "Campos de origem e destino devem ser diferentes."}, status=400)
    launch = DataModelLaunch.objects.filter(pk=launch_id).first()
    source_field = DataModelField.objects.filter(pk=source_field_id).select_related("table").first()
    target_field = DataModelField.objects.filter(pk=target_field_id).select_related("table").first()
    if not launch or not source_field or not target_field:
        return JsonResponse({"status": "error", "message": "Entidades não encontradas."}, status=404)
    if source_field.table.launch_id != launch.id or target_field.table.launch_id != launch.id:
        return JsonResponse({"status": "error", "message": "Campos fora do lançamento selecionado."}, status=400)
    exists = DataModelRelation.objects.filter(
        launch=launch, source_field=source_field, target_field=target_field
    ).exists()
    if exists:
        return JsonResponse({"status": "ok"})
    DataModelRelation.objects.create(
        launch=launch,
        source_field=source_field,
        target_field=target_field,
        relation_type=relation_type if relation_type in ("1:1", "1:N", "N:1", "N:N") else "1:N",
    )
    return JsonResponse({"status": "ok"})


def _oracle_type_from_field(field):
    data_type = (field.data_type or "varchar").lower()
    size = (field.size or "").strip()
    if data_type in ("varchar",):
        return f"VARCHAR2({size or '255'})"
    if data_type in ("text",):
        return "CLOB"
    if data_type in ("int", "bigint"):
        return f"NUMBER({size})" if size else "NUMBER"
    if data_type in ("decimal", "float"):
        return f"NUMBER({size})" if size else "NUMBER"
    if data_type == "bool":
        return "NUMBER(1)"
    if data_type == "date":
        return "DATE"
    if data_type == "datetime":
        return "TIMESTAMP"
    if data_type == "json":
        return "CLOB"
    return f"VARCHAR2({size or '255'})"


@login_required
@require_GET
def dataModelerGenerateOracleSql(request):
    launch_id = request.GET.get("launch_id")
    if not launch_id or not str(launch_id).isdigit():
        return JsonResponse({"status": "error", "message": "launch_id invalido"}, status=400)

    launch = DataModelLaunch.objects.filter(pk=int(launch_id)).first()
    if not launch:
        return JsonResponse({"status": "error", "message": "Lancamento nao encontrado"}, status=404)

    tables = list(DataModelTable.objects.filter(launch=launch).prefetch_related("fields").order_by("id"))
    relations = list(
        DataModelRelation.objects.filter(launch=launch)
        .select_related("source_field__table", "target_field__table")
        .order_by("id")
    )

    lines = []
    lines.append(f"-- SQL Oracle gerado automaticamente para o lancamento: {launch.name}")
    lines.append("")

    for table in tables:
        table_name = table.name.strip().upper()
        fields = list(table.fields.all())
        if not fields:
            continue
        col_lines = []
        pk_fields = []
        for f in fields:
            col_name = f.name.strip().upper()
            col_type = _oracle_type_from_field(f)
            nullable = "" if f.is_nullable or f.is_primary else " NOT NULL"
            col_lines.append(f"    {col_name} {col_type}{nullable}")
            if f.is_primary:
                pk_fields.append(col_name)
        if pk_fields:
            col_lines.append(f"    CONSTRAINT PK_{table_name} PRIMARY KEY ({', '.join(pk_fields)})")
        lines.append(f"CREATE TABLE {table_name} (")
        lines.append(",\n".join(col_lines))
        lines.append(");")
        lines.append("")

    for rel in relations:
        src_table = rel.source_field.table.name.strip().upper()
        src_col = rel.source_field.name.strip().upper()
        tgt_table = rel.target_field.table.name.strip().upper()
        tgt_col = rel.target_field.name.strip().upper()
        constraint_name = f"FK_{src_table}_{src_col}_{tgt_table}_{tgt_col}"[:120]
        lines.append(
            f"ALTER TABLE {src_table} ADD CONSTRAINT {constraint_name} FOREIGN KEY ({src_col}) REFERENCES {tgt_table} ({tgt_col});"
        )

    if len(lines) <= 2:
        lines.append("-- Nenhuma tabela/campo encontrado para gerar SQL.")

    return JsonResponse({"status": "ok", "sql": "\n".join(lines)})
