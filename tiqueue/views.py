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
    SystemConfig,
    DemandTemplate,
    DemandTemplateDetail,
)
from accounts.models import User
from .models import TaskType, TaskGroup
from . import services as service
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseBadRequest
import json
import unicodedata
from django.db import transaction, models, IntegrityError
from django.db.models import Case, When, IntegerField, Value, Count, Q, Prefetch, Sum, DecimalField
from django.db.models.functions import Coalesce
from django.views.decorators.http import require_GET
from django import forms
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta, datetime
from django.utils import timezone
import io
import re
from django.core.paginator import Paginator
from django.urls import reverse
import os

def index(request):
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
    if request.user.is_authenticated:
        my_categories = list(
            HubUserToolCategory.objects.filter(user=request.user, is_active=True).order_by("sort_order", "name", "id")
        )
        my_tools = (
            HubUserTool.objects.filter(user=request.user, is_active=True)
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

    return render(
        request,
        "tiqueue/index.html",
        {
            "categories": categories,
            "my_tools_grouped": my_tools_grouped,
        },
    )


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
                status = request.POST.get("status") or "active"
                color = (request.POST.get("color") or "").strip() or "#00bf63"
                start_date = request.POST.get("start_date") or None
                end_date = request.POST.get("end_date") or None
                if name:
                    Project.objects.filter(pk=project_id).update(
                        name=name,
                        description=description or None,
                        developer_id=int(developer_id) if developer_id.isdigit() else None,
                        status=status,
                        color=color,
                        start_date=start_date or None,
                        end_date=end_date or None,
                    )
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
                if title:
                    item.title = title
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

    projects = Project.objects.select_related("developer").all().order_by("name")
    users = User.objects.order_by("nameUser", "username")
    roadmap_items = (
        ProjectRoadmapItem.objects.select_related("project").order_by("project__name", "sort_order", "id")
    )
    columns = ProjectKanbanColumn.objects.select_related("project").order_by("project__name", "sort_order", "id")
    cards = ProjectKanbanCard.objects.select_related("project", "column").order_by(
        "project__name", "column__sort_order", "sort_order", "id"
    )

    return render(
        request,
        "tiqueue/projects.html",
        {
            "project_form": project_form,
            "roadmap_form": roadmap_form,
            "column_form": column_form,
            "card_form": card_form,
            "projects": projects,
            "users": users,
            "roadmap_items": roadmap_items,
            "columns": columns,
            "cards": cards,
        },
    )


@login_required
def projectCatalogPage(request):
    projects = list(
        Project.objects.select_related("developer").annotate(
            roadmap_total=Count("roadmap_items"),
            roadmap_done=Count("roadmap_items", filter=Q(roadmap_items__status="done")),
            kanban_cards_total=Count("kanban_cards"),
        ).order_by("name")
    )

    for p in projects:
        total = int(getattr(p, "roadmap_total", 0) or 0)
        done = int(getattr(p, "roadmap_done", 0) or 0)
        p.roadmap_progress_pct = int(round((done / total) * 100)) if total > 0 else 0

    return render(
        request,
        "tiqueue/project_catalog.html",
        {
            "projects": projects,
        },
    )


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
    items = list(ProjectRoadmapItem.objects.filter(project=project).order_by("sort_order", "id"))

    # Build a timeline window
    starts = [i.start_date for i in items if i.start_date] + ([project.start_date] if project.start_date else [])
    ends = [i.end_date for i in items if i.end_date] + ([project.end_date] if project.end_date else [])
    today = date.today()
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
        visual_items.append(
            {
                "id": it.id,
                "title": it.title,
                "description": it.description or "",
                "status": it.status,
                "start": (it.start_date.isoformat() if it.start_date else ""),
                "end": (it.end_date.isoformat() if it.end_date else ""),
                "left": left,
                "width": width,
            }
        )

    done_count = sum(1 for i in items if i.status == "done")
    total_count = len(items)
    progress_pct = int(round((done_count / total_count) * 100)) if total_count else 0

    return render(
        request,
        "tiqueue/project_roadmap.html",
        {
            "project": project,
            "items": visual_items,
            "window_start": window_start,
            "window_end": window_end,
            "done_count": done_count,
            "total_count": total_count,
            "progress_pct": progress_pct,
        },
    )


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
    start_date = payload.get("start_date") or None
    end_date = payload.get("end_date") or None
    description = (payload.get("description") or "").strip() or None

    max_sort = (
        ProjectRoadmapItem.objects.filter(project=project)
        .aggregate(models.Max("sort_order"))
        .get("sort_order__max")
    )
    next_sort = int(max_sort or 0) + 1

    item = ProjectRoadmapItem.objects.create(
        project=project,
        title=title,
        description=description,
        status=status,
        start_date=start_date or None,
        end_date=end_date or None,
        sort_order=next_sort,
    )
    _sync_roadmap_item_to_kanban(item)
    return JsonResponse({"status": "ok", "id": item.id})


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
    return JsonResponse({"status": "ok", "done": done, "total": total, "progress_pct": progress_pct})

def queueMainPage(request):
    pending_details_prefetch = Prefetch(
        "details",
        queryset=QueueTaskDetail.objects.filter(is_done=False).order_by("sort_order", "id"),
        to_attr="pending_details",
    )

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
        )
        .prefetch_related(pending_details_prefetch)
        .order_by("user_code", "-is_current", "n_queue_position", "n_register")
    )

    if not items:
        return render(request, "tiqueue/mainQueue.html", {"columns": []})

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
            }
        )

    columns = [columns_map[k] for k in user_order]
    return render(request, "tiqueue/mainQueue.html", {"columns": columns})


@login_required
def queueDemandDetailPage(request, item_id):
    item = get_object_or_404(
        userQueue.objects.select_related("task_group", "task_type", "linked_project", "linked_roadmap_item"),
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
                try:
                    parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
                    return parsed.date(), parsed.time().replace(second=0, microsecond=0)
                except ValueError:
                    return None, None

            item.a_ticket = (request.POST.get("a_ticket") or "").strip() or None
            item.a_description = (request.POST.get("a_description") or "").strip() or None
            item.a_demand_detail = (request.POST.get("a_demand_detail") or "").strip() or None

            task_group_raw = (request.POST.get("task_group") or "").strip()
            task_type_raw = (request.POST.get("task_type") or "").strip()
            project_raw = (request.POST.get("linked_project") or "").strip()

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


def _build_queue_user_context(request):
    user = request.user.userId
    kanban_columns = _ensure_user_queue_kanban_columns(request.user)
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

    form = UserQueueCreateForm(request.POST or None)
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
    original = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)

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

    return JsonResponse({"status": "ok", "new_id": cloned.n_register})

@login_required
@require_POST
def endQueueItem(request, id):
    service.serviceEndQueueItem(request, id)
    return JsonResponse({'status':'ok'})

@login_required
def listQueueUpdate(request):
    user = request.user.userId

    try:
        queue_working = list(
            userQueue.objects.filter(is_current=True, user_code=user)
            .select_related("task_type", "linked_project", "kanban_column")
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
    return render(request, 'partials/queue.html', {'queue_data': queue_data, 'queue_working':queue_working})

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
    item = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)

    def _date(d):
        return d.isoformat() if d else ""

    def _time(t):
        return t.strftime("%H:%M") if t else ""

    return JsonResponse(
        {
            "n_register": item.n_register,
            "a_ticket": item.a_ticket or "",
            "f_conclusion_rate": "" if item.f_conclusion_rate is None else str(item.f_conclusion_rate),
            "a_description": item.a_description or "",
            "task_group": item.task_group_id or "",
            "task_type": item.task_type_id or "",
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
def updateQueueItem(request, id):
    item = get_object_or_404(userQueue, n_register=id, user_code=request.user.userId)
    form = UserQueueUpdateForm(request.POST, instance=item)
    if not form.is_valid():
        return JsonResponse({"status": "error", "errors": form.errors}, status=400)

    item = form.save()

    task_group_raw = (request.POST.get("task_group") or "").strip()
    task_type_raw = (request.POST.get("task_type") or "").strip()

    task_group_id = int(task_group_raw) if task_group_raw.isdigit() else None
    task_type_id = int(task_type_raw) if task_type_raw.isdigit() else None

    item.task_group_id = task_group_id
    item.task_type_id = task_type_id
    item.n_type_group = task_group_id
    item.n_type_code = task_type_id
    item.save(update_fields=["task_group", "task_type", "n_type_group", "n_type_code"])
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
            roadmap_item.description = roadmap_description
            roadmap_item.status = status
            roadmap_item.start_date = item.d_predicted_date_start
            roadmap_item.end_date = item.d_predicted_date_end
            roadmap_item.save(
                update_fields=["title", "description", "status", "start_date", "end_date"]
            )
        else:
            max_sort = (
                ProjectRoadmapItem.objects.filter(project=project).aggregate(models.Max("sort_order")).get("sort_order__max")
            )
            roadmap_item = ProjectRoadmapItem.objects.create(
                project=project,
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
    for the authenticated user. Only reorders items with n_queue_position >= 2.
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

    if any(i["n_queue_position"] == 1 for i in items):
        return HttpResponseBadRequest("Cannot reorder current working item")

    # Persist sequential positions starting at 2 (position 1 is reserved for current item).
    whens = []
    for index, item_id in enumerate(order_ids):
        whens.append(When(n_register=item_id, then=Value(index + 2)))

    with transaction.atomic():
        userQueue.objects.filter(user_code=user_code, n_register__in=order_ids).update(
            n_queue_position=Case(*whens, output_field=IntegerField())
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

    return JsonResponse({"status": "ok", "id": col.id, "name": col.name, "color": col.color})


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
    return JsonResponse({"status": "ok", "id": col.id, "name": col.name, "color": col.color})


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
    config = SystemConfig.objects.order_by("-updated_at", "-id").first()
    if request.method == "POST":
        version = (request.POST.get("system_version") or "").strip()
        if config is None:
            config = SystemConfig.objects.create(system_version=version or None)
        else:
            config.system_version = version or None
            config.save(update_fields=["system_version", "updated_at"])
        return redirect("systemSettingsPage")

    return render(
        request,
        "tiqueue/system_settings.html",
        {"config": config},
    )


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
