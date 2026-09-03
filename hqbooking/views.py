import calendar
import json
import os
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (
    HeadquartersDateBlock,
    HeadquartersEnvironment,
    HeadquartersReservation,
    LunchReservation,
    SimulationRomaneioEntry,
)

SESSION_KEY_EMPLOYEE_ID = "hqbooking_employee_id"
SESSION_KEY_ADMIN_ID = "hqbooking_admin_id"
SESSION_KEY_IS_ADMIN = "hqbooking_is_admin"

# Mock admin secret. Keep simple by design for this independent module.
ADMIN_ACCESS_KEY = "admin123"


def _resolve_lunch_target_date(today, friday_choice=None):
    weekday = today.weekday()  # Monday=0 ... Sunday=6

    # Friday: user can choose Saturday or Monday.
    if weekday == 4:
        if friday_choice == "monday":
            return today + timedelta(days=3)
        return today + timedelta(days=1)

    target = today + timedelta(days=1)
    # No lunch reservation on Sunday.
    if target.weekday() == 6:
        target = target + timedelta(days=1)
    return target


def _employee_from_session(request):
    return (request.session.get(SESSION_KEY_EMPLOYEE_ID) or "").strip()


def _admin_from_session(request):
    return (request.session.get(SESSION_KEY_ADMIN_ID) or "").strip()


def _is_admin(request):
    return bool(request.session.get(SESSION_KEY_IS_ADMIN))


def _require_employee(request):
    employee_id = _employee_from_session(request)
    if not employee_id:
        return None
    return employee_id


def _require_admin(request):
    if not _is_admin(request):
        return None
    return _admin_from_session(request)


def _status_set_active():
    return [HeadquartersReservation.STATUS_PENDING, HeadquartersReservation.STATUS_APPROVED]


def login_page(request):
    if _employee_from_session(request):
        return redirect("hqbooking_calendar")
    return render(request, "hqbooking/login.html")


@require_POST
def login_submit(request):
    employee_id = (request.POST.get("employee_id") or "").strip()
    if not employee_id:
        return render(
            request,
            "hqbooking/login.html",
            {"error_message": "Informe sua matricula para entrar."},
        )

    request.session[SESSION_KEY_EMPLOYEE_ID] = employee_id
    return redirect("hqbooking_calendar")


@require_POST
def logout_submit(request):
    request.session.pop(SESSION_KEY_EMPLOYEE_ID, None)
    return redirect("hqbooking_login")


def calendar_page(request):
    employee_id = _require_employee(request)
    if not employee_id:
        return redirect("hqbooking_login")

    today = date.today()
    return render(
        request,
        "hqbooking/calendar.html",
        {"employee_id": employee_id, "today_year": today.year, "today_month": today.month},
    )


@require_GET
def calendar_data(request):
    employee_id = _require_employee(request)
    if not employee_id:
        return JsonResponse({"status": "error", "message": "Não autenticado"}, status=401)

    try:
        year = int(request.GET.get("year") or 0)
        month = int(request.GET.get("month") or 0)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid year/month")

    if year < 1900 or year > 3000 or month < 1 or month > 12:
        return HttpResponseBadRequest("Invalid year/month")

    first_day = date(year, month, 1)
    _, num_days = calendar.monthrange(year, month)
    month_dates = [date(year, month, d) for d in range(1, num_days + 1)]

    approved_qs = (
        HeadquartersReservation.objects.filter(
            reserved_date__in=month_dates,
            status=HeadquartersReservation.STATUS_APPROVED,
        )
        .prefetch_related("environments")
    )
    approved_map = {
        r.reserved_date: {
            "employee_id": r.employee_id,
            "start_time": (r.start_time.strftime("%H:%M") if r.start_time else ""),
            "end_time": (r.end_time.strftime("%H:%M") if r.end_time else ""),
            "reason": (r.reason or ""),
            "status": r.status,
            "environments": [e.name for e in r.environments.all()],
        }
        for r in approved_qs
    }

    my_qs = (
        HeadquartersReservation.objects.filter(
            employee_id=employee_id,
            reserved_date__in=month_dates,
        )
        .prefetch_related("environments")
        .order_by("-created_at")
    )
    mine_map = {}
    for r in my_qs:
        if r.reserved_date not in mine_map:
            mine_map[r.reserved_date] = r

    blocks_map = {
        b.blocked_date: b
        for b in HeadquartersDateBlock.objects.filter(blocked_date__in=month_dates)
    }

    active_reservation_count = HeadquartersReservation.objects.filter(
        employee_id=employee_id,
        reserved_date__gte=date.today(),
        status__in=_status_set_active(),
    ).count()

    days_payload = []
    today = date.today()
    for d in month_dates:
        approved = approved_map.get(d)
        mine = mine_map.get(d)
        block = blocks_map.get(d)

        is_past = d < today
        is_blocked = block is not None
        is_reserved = approved is not None
        is_mine = bool(mine is not None)

        reserved_by = approved["employee_id"] if approved else ""
        start_time_val = approved["start_time"] if approved else (mine.start_time.strftime("%H:%M") if mine and mine.start_time else "")
        end_time_val = approved["end_time"] if approved else (mine.end_time.strftime("%H:%M") if mine and mine.end_time else "")
        reason_val = approved["reason"] if approved else ((mine.reason or "") if mine else "")
        env_names = approved["environments"] if approved else ([e.name for e in mine.environments.all()] if mine else [])
        my_status = mine.status if mine else ""

        days_payload.append(
            {
                "date": d.isoformat(),
                "day": d.day,
                "weekday": d.weekday(),
                "is_past": is_past,
                "is_blocked": is_blocked,
                "block_reason": (block.reason or "") if block else "",
                "is_reserved": is_reserved,
                "reserved_by": reserved_by,
                "start_time": start_time_val,
                "end_time": end_time_val,
                "reason": reason_val,
                "environments": env_names,
                "is_mine": is_mine,
                "my_status": my_status,
            }
        )

    all_environments = list(HeadquartersEnvironment.objects.all().order_by("name", "id"))
    env_payload = [{"id": e.id, "name": e.name, "description": e.description or ""} for e in all_environments]

    return JsonResponse(
        {
            "status": "ok",
            "year": year,
            "month": month,
            "month_label": first_day.strftime("%B %Y"),
            "days": days_payload,
            "current_user": employee_id,
            "active_reservation_count": active_reservation_count,
            "max_active_reservations": 3,
            "environments": env_payload,
        }
    )


@require_POST
def reserve_date(request):
    employee_id = _require_employee(request)
    if not employee_id:
        return JsonResponse({"status": "error", "message": "Não autenticado"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    target_iso = (payload.get("date") or "").strip()
    try:
        target_date = date.fromisoformat(target_iso)
    except ValueError:
        return JsonResponse({"status": "error", "message": "Data inválida"}, status=400)

    start_time_raw = (payload.get("start_time") or "").strip()
    end_time_raw = (payload.get("end_time") or "").strip()
    reason = (payload.get("reason") or "").strip()
    selected_envs = payload.get("environments") or []

    if not start_time_raw or not end_time_raw:
        return JsonResponse({"status": "error", "message": "Informe horario de inicio e termino."}, status=400)
    if not reason:
        return JsonResponse({"status": "error", "message": "Informe o motivo da reserva."}, status=400)
    if len(reason) > 180:
        return JsonResponse({"status": "error", "message": "Motivo deve ter no maximo 180 caracteres."}, status=400)
    if not isinstance(selected_envs, list) or not selected_envs:
        return JsonResponse({"status": "error", "message": "Selecione ao menos um ambiente."}, status=400)

    env_ids = []
    try:
        env_ids = sorted(set(int(v) for v in selected_envs))
    except Exception:
        return JsonResponse({"status": "error", "message": "Lista de ambientes inválida."}, status=400)

    valid_envs = list(HeadquartersEnvironment.objects.filter(id__in=env_ids))
    if len(valid_envs) != len(env_ids):
        return JsonResponse({"status": "error", "message": "Um ou mais ambientes não existem."}, status=400)

    try:
        start_h, start_m = start_time_raw.split(":")
        end_h, end_m = end_time_raw.split(":")
        start_time_obj = time(hour=int(start_h), minute=int(start_m))
        end_time_obj = time(hour=int(end_h), minute=int(end_m))
    except Exception:
        return JsonResponse({"status": "error", "message": "Horário inválido."}, status=400)

    if start_time_obj >= end_time_obj:
        return JsonResponse({"status": "error", "message": "Horario final deve ser maior que o inicial."}, status=400)
    if target_date < date.today():
        return JsonResponse({"status": "error", "message": "Não é permitido reservar datas passadas."}, status=400)

    if HeadquartersDateBlock.objects.filter(blocked_date=target_date).exists():
        return JsonResponse({"status": "error", "message": "Esta data esta bloqueada pelo administrador."}, status=400)

    with transaction.atomic():
        active_for_user = HeadquartersReservation.objects.filter(
            employee_id=employee_id,
            reserved_date__gte=date.today(),
            status__in=_status_set_active(),
        ).count()
        if active_for_user >= 3:
            return JsonResponse(
                {"status": "error", "message": "Voce ja atingiu o limite de 3 reservas ativas."},
                status=400,
            )

        already_requested = HeadquartersReservation.objects.filter(
            employee_id=employee_id,
            reserved_date=target_date,
            status__in=_status_set_active(),
        ).exists()
        if already_requested:
            return JsonResponse(
                {"status": "error", "message": "Voce ja possui solicitacao ativa para esta data."},
                status=400,
            )

        date_is_blocked_by_approved = HeadquartersReservation.objects.filter(
            reserved_date=target_date,
            status=HeadquartersReservation.STATUS_APPROVED,
        ).exists()
        if date_is_blocked_by_approved:
            return JsonResponse({"status": "error", "message": "Este dia ja esta reservado (aprovado)."}, status=400)

        booking = HeadquartersReservation.objects.create(
            reserved_date=target_date,
            employee_id=employee_id,
            start_time=start_time_obj,
            end_time=end_time_obj,
            reason=reason,
            status=HeadquartersReservation.STATUS_PENDING,
        )
        booking.environments.set(valid_envs)

    return JsonResponse({"status": "ok", "message": "Solicitacao enviada para aprovacao."})


@require_POST
def cancel_reservation(request):
    employee_id = _require_employee(request)
    if not employee_id:
        return JsonResponse({"status": "error", "message": "Não autenticado"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    target_iso = (payload.get("date") or "").strip()
    try:
        target_date = date.fromisoformat(target_iso)
    except ValueError:
        return JsonResponse({"status": "error", "message": "Data inválida"}, status=400)

    deleted, _ = HeadquartersReservation.objects.filter(
        reserved_date=target_date,
        employee_id=employee_id,
        status__in=_status_set_active(),
    ).delete()

    if not deleted:
        return JsonResponse(
            {"status": "error", "message": "Voce so pode cancelar a propria solicitacao/reserva."},
            status=400,
        )

    return JsonResponse({"status": "ok", "message": "Solicitacao/reserva cancelada com sucesso."})


def admin_login_page(request):
    if _is_admin(request):
        return redirect("hqbooking_admin_panel")
    return render(request, "hqbooking/admin_login.html")


@require_POST
def admin_login_submit(request):
    admin_id = (request.POST.get("admin_id") or "").strip()
    access_key = (request.POST.get("access_key") or "").strip()

    if not admin_id or not access_key:
        return render(
            request,
            "hqbooking/admin_login.html",
            {"error_message": "Informe matricula e chave de acesso."},
        )

    if access_key != ADMIN_ACCESS_KEY:
        return render(
            request,
            "hqbooking/admin_login.html",
            {"error_message": "Chave de acesso inválida."},
        )

    request.session[SESSION_KEY_IS_ADMIN] = True
    request.session[SESSION_KEY_ADMIN_ID] = admin_id
    return redirect("hqbooking_admin_panel")


@require_POST
def admin_logout_submit(request):
    request.session.pop(SESSION_KEY_IS_ADMIN, None)
    request.session.pop(SESSION_KEY_ADMIN_ID, None)
    return redirect("hqbooking_admin_login")


def admin_panel_page(request):
    admin_id = _require_admin(request)
    if not admin_id:
        return redirect("hqbooking_admin_login")
    return render(request, "hqbooking/admin_panel.html", {"admin_id": admin_id})


@require_GET
def admin_requests_data(request):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    status_filter = (request.GET.get("status") or "").strip().upper()
    order = (request.GET.get("order") or "asc").strip().lower()
    environment_id = (request.GET.get("environment_id") or "").strip()

    qs = HeadquartersReservation.objects.all().prefetch_related("environments")
    if status_filter in {HeadquartersReservation.STATUS_PENDING, HeadquartersReservation.STATUS_APPROVED, HeadquartersReservation.STATUS_REJECTED}:
        qs = qs.filter(status=status_filter)

    if environment_id:
        try:
            qs = qs.filter(environments__id=int(environment_id))
        except Exception:
            return JsonResponse({"status": "error", "message": "Filtro de ambiente inválido"}, status=400)

    if order == "desc":
        qs = qs.order_by("-reserved_date", "-created_at", "-id")
    else:
        qs = qs.order_by("reserved_date", "-created_at", "-id")

    payload = []
    for r in qs:
        payload.append(
            {
                "id": r.id,
                "employee_id": r.employee_id,
                "date": r.reserved_date.isoformat(),
                "start_time": r.start_time.strftime("%H:%M") if r.start_time else "",
                "end_time": r.end_time.strftime("%H:%M") if r.end_time else "",
                "reason": r.reason or "",
                "status": r.status,
                "environments": [{"id": e.id, "name": e.name} for e in r.environments.all()],
            }
        )

    return JsonResponse({"status": "ok", "requests": payload})


@require_POST
def admin_approve_request(request, reservation_id):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    with transaction.atomic():
        target = HeadquartersReservation.objects.select_for_update().filter(pk=reservation_id).first()
        if not target:
            return JsonResponse({"status": "error", "message": "Solicitação não encontrada"}, status=404)
        if target.status != HeadquartersReservation.STATUS_PENDING:
            return JsonResponse({"status": "error", "message": "Somente pendentes podem ser aprovadas"}, status=400)

        if HeadquartersDateBlock.objects.select_for_update().filter(blocked_date=target.reserved_date).exists():
            return JsonResponse({"status": "error", "message": "Data bloqueada. Remova o bloqueio antes de aprovar."}, status=400)

        already_approved = HeadquartersReservation.objects.select_for_update().filter(
            reserved_date=target.reserved_date,
            status=HeadquartersReservation.STATUS_APPROVED,
        ).exclude(pk=target.pk).exists()
        if already_approved:
            return JsonResponse({"status": "error", "message": "Ja existe reserva aprovada para esta data"}, status=400)

        target.status = HeadquartersReservation.STATUS_APPROVED
        target.reviewed_at = timezone.now()
        target.save(update_fields=["status", "reviewed_at", "updated_at"])

        HeadquartersReservation.objects.filter(
            reserved_date=target.reserved_date,
            status=HeadquartersReservation.STATUS_PENDING,
        ).exclude(pk=target.pk).update(
            status=HeadquartersReservation.STATUS_REJECTED,
            reviewed_at=timezone.now(),
        )

    return JsonResponse({"status": "ok", "message": "Solicitacao aprovada com sucesso"})


@require_POST
def admin_reject_request(request, reservation_id):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    with transaction.atomic():
        target = HeadquartersReservation.objects.select_for_update().filter(pk=reservation_id).first()
        if not target:
            return JsonResponse({"status": "error", "message": "Solicitação não encontrada"}, status=404)
        if target.status == HeadquartersReservation.STATUS_REJECTED:
            return JsonResponse({"status": "ok", "message": "Solicitacao ja estava recusada"})

        target.status = HeadquartersReservation.STATUS_REJECTED
        target.reviewed_at = timezone.now()
        target.save(update_fields=["status", "reviewed_at", "updated_at"])

    return JsonResponse({"status": "ok", "message": "Solicitacao recusada com sucesso"})


@require_GET
def admin_environments_data(request):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    envs = HeadquartersEnvironment.objects.all().order_by("name", "id")
    payload = [{"id": e.id, "name": e.name, "description": e.description or ""} for e in envs]
    return JsonResponse({"status": "ok", "environments": payload})


@require_POST
def admin_environment_create(request):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip()
    if not name:
        return JsonResponse({"status": "error", "message": "Nome do ambiente obrigatorio"}, status=400)

    exists = HeadquartersEnvironment.objects.filter(name__iexact=name).exists()
    if exists:
        return JsonResponse({"status": "error", "message": "Ja existe ambiente com este nome"}, status=400)

    env = HeadquartersEnvironment.objects.create(name=name, description=description or None)
    return JsonResponse({"status": "ok", "id": env.id})


@require_POST
def admin_environment_update(request, environment_id):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    env = HeadquartersEnvironment.objects.filter(pk=environment_id).first()
    if not env:
        return JsonResponse({"status": "error", "message": "Ambiente não encontrado"}, status=404)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip()
    if not name:
        return JsonResponse({"status": "error", "message": "Nome do ambiente obrigatorio"}, status=400)

    exists = HeadquartersEnvironment.objects.exclude(pk=env.pk).filter(name__iexact=name).exists()
    if exists:
        return JsonResponse({"status": "error", "message": "Ja existe ambiente com este nome"}, status=400)

    env.name = name
    env.description = description or None
    env.save(update_fields=["name", "description", "updated_at"])
    return JsonResponse({"status": "ok"})


@require_POST
def admin_environment_delete(request, environment_id):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    env = HeadquartersEnvironment.objects.filter(pk=environment_id).first()
    if not env:
        return JsonResponse({"status": "error", "message": "Ambiente não encontrado"}, status=404)

    env.delete()
    return JsonResponse({"status": "ok"})


@require_GET
def admin_blocks_data(request):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    blocks = HeadquartersDateBlock.objects.all().order_by("blocked_date", "id")
    payload = [
        {
            "id": b.id,
            "blocked_date": b.blocked_date.isoformat(),
            "reason": b.reason or "",
            "blocked_by": b.blocked_by or "",
        }
        for b in blocks
    ]
    return JsonResponse({"status": "ok", "blocks": payload})


@require_POST
def admin_block_create(request):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    raw_date = (payload.get("blocked_date") or "").strip()
    reason = (payload.get("reason") or "").strip()
    try:
        block_date = date.fromisoformat(raw_date)
    except ValueError:
        return JsonResponse({"status": "error", "message": "Data inválida"}, status=400)

    if block_date < date.today():
        return JsonResponse({"status": "error", "message": "Não é permitido bloquear data passada"}, status=400)

    if HeadquartersDateBlock.objects.filter(blocked_date=block_date).exists():
        return JsonResponse({"status": "error", "message": "Data ja esta bloqueada"}, status=400)

    if HeadquartersReservation.objects.filter(
        reserved_date=block_date,
        status=HeadquartersReservation.STATUS_APPROVED,
    ).exists():
        return JsonResponse({"status": "error", "message": "Ja existe reserva aprovada para esta data"}, status=400)

    HeadquartersDateBlock.objects.create(
        blocked_date=block_date,
        reason=reason or None,
        blocked_by=admin_id,
    )
    return JsonResponse({"status": "ok"})


@require_POST
def admin_block_delete(request, block_id):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Não autorizado"}, status=401)

    block = HeadquartersDateBlock.objects.filter(pk=block_id).first()
    if not block:
        return JsonResponse({"status": "error", "message": "Bloqueio não encontrado"}, status=404)

    block.delete()
    return JsonResponse({"status": "ok"})


def lunch_booking_page(request):
    today = timezone.localdate()
    weekday = today.weekday()
    is_friday = weekday == 4
    default_target = _resolve_lunch_target_date(today, None)

    context = {
        "today_iso": today.isoformat(),
        "is_friday": is_friday,
        "default_target_iso": default_target.isoformat(),
    }

    if request.method == "POST":
        employee_id = (request.POST.get("employee_id") or "").strip()
        friday_choice = (request.POST.get("friday_choice") or "").strip().lower()

        if not employee_id:
            context["error_message"] = "Informe a matricula."
            return render(request, "hqbooking/lunch_booking.html", context)

        target_date = _resolve_lunch_target_date(today, friday_choice if is_friday else None)
        try:
            LunchReservation.objects.create(employee_id=employee_id, reserved_date=target_date)
            context["success_date_label"] = target_date.strftime("%d/%m/%Y")
        except IntegrityError:
            context["error_message"] = (
                f"Ja existe reserva dessa matricula para {target_date.strftime('%d/%m/%Y')}."
            )

        context["default_target_iso"] = target_date.isoformat()

    return render(request, "hqbooking/lunch_booking.html", context)


def lunch_booking_admin_page(request):
    rows = LunchReservation.objects.all().order_by("reserved_date", "employee_id", "id")

    grouped = []
    selected_date = (request.GET.get("date") or "").strip()
    current_date = None
    bucket = None

    for r in rows:
        iso = r.reserved_date.isoformat()
        if iso != current_date:
            bucket = {
                "date": r.reserved_date,
                "date_iso": iso,
                "count": 0,
                "employees": [],
            }
            grouped.append(bucket)
            current_date = iso

        bucket["count"] += 1
        bucket["employees"].append(r.employee_id)

    if not selected_date and grouped:
        selected_date = grouped[0]["date_iso"]

    return render(
        request,
        "hqbooking/lunch_admin.html",
        {
            "grouped": grouped,
            "selected_date": selected_date,
        },
    )


def _simulation_oracle_config():
    service_name = (os.getenv("ERP_SIM_DB_NAME") or "dbprod").strip()
    return {
        "host": os.getenv("ERP_SIM_DB_HOST", "192.168.30.2"),
        "port": int(os.getenv("ERP_SIM_DB_PORT", "1521")),
        "service_name": service_name,
        # As colunas novas da USU_TCONROM já existem na base de produção — não
        # há mais um usuário próprio de simulação. O login é o mesmo usado nos
        # BI's (`tiqueue/customer_dna.py`, `tiqueue/views.py`): reaproveita
        # ERP_DB_USER/ERP_DB_PASSWORD de propósito, para que uma troca de
        # senha em produção não fique esquecida aqui.
        "user": os.getenv("ERP_DB_USER", "sapiens"),
        "password": os.getenv("ERP_DB_PASSWORD", "sapiens"),
        "is_ready": bool(service_name),
    }


def _parse_romaneio_date(raw_value):
    raw_value = str(raw_value or "").strip()
    if not raw_value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%d%m%Y"):
        try:
            return datetime.strptime(raw_value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_romaneio_time(raw_value):
    raw_value = str(raw_value or "").strip()
    if not raw_value:
        return None
    compact = raw_value.replace(":", "")
    for fmt, candidate in (
        ("%H:%M:%S", raw_value),
        ("%H:%M", raw_value),
        ("%H%M%S", compact),
        ("%H%M", compact),
    ):
        try:
            return datetime.strptime(candidate, fmt).time()
        except ValueError:
            continue
    return None


def _parse_romaneio_int(raw_value):
    raw_value = str(raw_value or "").strip()
    if not raw_value:
        return None
    return int(raw_value) if raw_value.isdigit() else None


# USU_NUMEMB e USU_CODEND são NUMBER na USU_TCONROM (confirmado direto no
# dicionário de dados do Oracle: NUMBER(9) e NUMBER(6)) — apesar do nome
# "código", pallet e endereçamento são puramente numéricos, sem letra nem
# hífen. A normalização também remove zeros à esquerda, porque é assim que o
# Oracle vai guardar o valor: sem ela, "004521" e "4521" pareceriam pallets
# diferentes na checagem de duplicidade quando na verdade são a mesma linha.
def _parse_romaneio_numeric_code(raw_value, max_digits):
    raw_text = str(raw_value or "").strip()
    if not raw_text or not raw_text.isdigit():
        return None
    normalized = str(int(raw_text))
    if len(normalized) > max_digits:
        return None
    return normalized


ROMANEIO_PACKAGE_CODE_MAX_DIGITS = 9  # USU_NUMEMB NUMBER(9)
ROMANEIO_ADDRESS_CODE_MAX_DIGITS = 6  # USU_CODEND NUMBER(6)


def _parse_romaneio_decimal(raw_value):
    normalized = str(raw_value or "").strip().replace("R$", "").replace(" ", "")
    if not normalized:
        return None
    normalized = normalized.replace(".", "").replace(",", ".") if normalized.count(",") == 1 else normalized.replace(",", ".")
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None


# Formato atual da etiqueta: Empresa/Filial/Quantidade de volumes/Peso/
# Código do pallet/Endereçamento — 6 campos, nesta ordem, separados por quebra
# de linha, tab, `/`, `|` ou `;`. Matrícula e sequência não vêm no código: a
# primeira é digitada no aparelho e a segunda é calculada no Oracle a partir
# de USU_SEQCON no momento do envio (`_next_simulation_romaneio_sequence`).
ROMANEIO_PAYLOAD_FIELD_COUNT = 6


def _split_romaneio_payload(raw_payload):
    source = str(raw_payload or "").strip()
    if not source:
        return []

    splitters = [r"\r?\n", r"\t", r"/", r"\|", r";"]
    for splitter in splitters:
        parts = [item.strip() for item in re.split(splitter, source) if item.strip()]
        if len(parts) == ROMANEIO_PAYLOAD_FIELD_COUNT:
            return parts
    return []


def _map_romaneio_payload(parts):
    if not isinstance(parts, list) or len(parts) != ROMANEIO_PAYLOAD_FIELD_COUNT:
        return None
    return {
        "company_code": parts[0],
        "branch_code": parts[1],
        "volume_quantity": parts[2],
        "romaneio_weight": parts[3],
        "package_code": parts[4],
        "address_code": parts[5],
    }


def _extract_romaneio_payload(raw_payload):
    payload_parts = _split_romaneio_payload(raw_payload)
    mapped = _map_romaneio_payload(payload_parts)
    if not mapped:
        return None

    return {
        "company_code": mapped["company_code"],
        "branch_code": mapped["branch_code"],
        "volume_quantity": _parse_romaneio_int(mapped["volume_quantity"]),
        "romaneio_weight": _parse_romaneio_decimal(mapped["romaneio_weight"]),
        "package_code": _parse_romaneio_numeric_code(mapped["package_code"], ROMANEIO_PACKAGE_CODE_MAX_DIGITS),
        "address_code": _parse_romaneio_numeric_code(mapped["address_code"], ROMANEIO_ADDRESS_CODE_MAX_DIGITS),
    }


def _normalize_romaneio_sequence_value(raw_value):
    if raw_value in (None, ""):
        return 0
    if isinstance(raw_value, Decimal):
        return int(raw_value)
    if isinstance(raw_value, (int, float)):
        return int(raw_value)

    raw_text = str(raw_value).strip()
    if not raw_text:
        return 0
    if raw_text.isdigit():
        return int(raw_text)

    try:
        return int(Decimal(raw_text.replace(",", ".")))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Sequência Oracle retornou um valor inválido.")


def _next_simulation_romaneio_sequence(cursor, company_code, branch_code):
    cursor.execute(
        """
        SELECT NVL(MAX(USU_SEQCON), 0)
          FROM USU_TCONROM
         WHERE USU_CODEMP = :empresa
           AND USU_CODFIL = :filial
        """,
        {
            "empresa": int(company_code) if str(company_code).isdigit() else company_code,
            "filial": int(branch_code) if str(branch_code).isdigit() else branch_code,
        },
    )
    row = cursor.fetchone() or [0]
    current_sequence = _normalize_romaneio_sequence_value(row[0] if isinstance(row, (list, tuple)) else row)
    return current_sequence + 1


def _connect_simulation_oracle():
    config = _simulation_oracle_config()
    if not config["is_ready"]:
        raise RuntimeError(
            "A configuração padrão da base de simulações não está disponível. "
            "Verifique as variáveis ERP_SIM_DB_HOST, ERP_SIM_DB_PORT, ERP_SIM_DB_NAME, "
            "ERP_DB_USER e ERP_DB_PASSWORD."
        )

    last_error = None
    for driver_name in ("oracledb", "cx_Oracle"):
        try:
            if driver_name == "oracledb":
                import oracledb as oracle_driver  # type: ignore
            else:
                import cx_Oracle as oracle_driver  # type: ignore

            dsn = oracle_driver.makedsn(
                config["host"],
                int(config["port"]),
                service_name=config["service_name"],
            )
            conn = oracle_driver.connect(
                user=config["user"],
                password=config["password"],
                dsn=dsn,
            )
            return conn, driver_name
        except Exception as exc:
            last_error = f"{driver_name}: {exc}"

    raise RuntimeError(
        "Falha ao conectar na base Oracle de simulações. "
        f"Detalhe: {last_error or 'driver Oracle não encontrado'}"
    )


def _insert_simulation_romaneio_oracle(entry):
    payload = {
        "empresa": int(entry.company_code) if str(entry.company_code).isdigit() else entry.company_code,
        "filial": int(entry.branch_code) if str(entry.branch_code).isdigit() else entry.branch_code,
        "sequencia_registro": None,
        # USU_CODMAT: matrícula digitada no aparelho, não o login de sistema —
        # o nome da coluna no ERP é o do funcionário, não o do usuário.
        "matricula": int(entry.user_code) if str(entry.user_code).isdigit() else entry.user_code,
        "data_geracao": entry.generated_date,
        "hora_geracao": int(entry.generated_time.strftime("%H%M")),
        "quantidade_volumes": int(entry.volume_quantity),
        "peso_romaneio": Decimal(entry.romaneio_weight),
        # USU_NUMEMB e USU_CODEND são NUMBER no Oracle (NUMBER(9) e NUMBER(6)) —
        # confirmado no dicionário de dados. `package_code`/`address_code`
        # chegam aqui já normalizados (só dígitos, sem zero à esquerda) por
        # `_parse_romaneio_numeric_code`, então o cast é seguro.
        "codigo_embalagem": int(entry.package_code) if str(entry.package_code).isdigit() else entry.package_code,
        "endereco": int(entry.address_code) if str(entry.address_code).isdigit() else entry.address_code,
    }

    last_error = None
    conn = None
    cur = None
    try:
        conn, driver_name = _connect_simulation_oracle()
        cur = conn.cursor()
        next_sequence = _next_simulation_romaneio_sequence(
            cur,
            entry.company_code,
            entry.branch_code,
        )
        entry.sequence_record = str(next_sequence)
        payload["sequencia_registro"] = next_sequence
        cur.execute(
            """
            INSERT INTO USU_TCONROM (
                USU_CODEMP,
                USU_CODFIL,
                USU_SEQCON,
                USU_CODMAT,
                USU_DATGER,
                USU_HORGER,
                USU_QTDVOL,
                USU_PESROM,
                USU_NUMEMB,
                USU_CODEND
            ) VALUES (
                :empresa,
                :filial,
                :sequencia_registro,
                :matricula,
                :data_geracao,
                :hora_geracao,
                :quantidade_volumes,
                :peso_romaneio,
                :codigo_embalagem,
                :endereco
            )
            """,
            payload,
        )
        conn.commit()
        return None
    except Exception as exc:
        last_error = str(exc)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return (
        "Falha ao inserir romaneio na base Oracle de simulações. "
        f"Detalhe: {last_error or 'driver Oracle não encontrado'}"
    )


def _find_duplicate_romaneio_entry(package_code):
    """Devolve a leitura já contabilizada desta embalagem, se existir.

    Só um `sync_status` de sucesso conta como "já contabilizado": uma
    tentativa anterior que ficou com erro no Oracle, ou que ela mesma foi
    recusada por duplicidade, não pode travar uma nova tentativa para o mesmo
    pallet. Embalagem vazia nunca é duplicada — nem toda leitura antiga trazia
    o código do pallet.
    """
    package_code = str(package_code or "").strip()
    if not package_code:
        return None
    return (
        SimulationRomaneioEntry.objects.filter(
            package_code=package_code,
            sync_status=SimulationRomaneioEntry.SYNC_SUCCESS,
        )
        .order_by("-id")
        .first()
    )


def _duplicate_romaneio_message(package_code, duplicate):
    return (
        f"O pallet {package_code} já foi contabilizado antes — matrícula {duplicate.user_code}, "
        f"sequência {duplicate.sequence_record}, em "
        f"{duplicate.generated_date.strftime('%d/%m/%Y')} {duplicate.generated_time.strftime('%H:%M')}."
    )


def _submit_romaneio_entry(
    *,
    company_code,
    branch_code,
    user_code,
    generated_date,
    generated_time,
    volume_quantity,
    romaneio_weight,
    package_code="",
    address_code="",
    barcode_payload=None,
    client_reference="",
):
    package_code = str(package_code or "").strip()
    address_code = str(address_code or "").strip()

    # Regra de negócio: cada embalagem entra uma única vez. Duas matrículas
    # diferentes lendo o mesmo pallet, ou a mesma matrícula relendo por
    # engano, caem aqui do mesmo jeito — o que importa é o `package_code`,
    # não quem leu.
    duplicate = _find_duplicate_romaneio_entry(package_code)
    if duplicate:
        message = _duplicate_romaneio_message(package_code, duplicate)
        entry = SimulationRomaneioEntry.objects.create(
            company_code=company_code,
            branch_code=branch_code,
            sequence_record="",
            user_code=user_code,
            generated_date=generated_date,
            generated_time=generated_time,
            volume_quantity=volume_quantity,
            romaneio_weight=romaneio_weight,
            package_code=package_code,
            address_code=address_code,
            barcode_payload=barcode_payload,
            client_reference=client_reference,
            sync_status=SimulationRomaneioEntry.SYNC_DUPLICATE,
            sync_message=message,
        )
        return entry, message

    entry = SimulationRomaneioEntry.objects.create(
        company_code=company_code,
        branch_code=branch_code,
        sequence_record="",
        user_code=user_code,
        generated_date=generated_date,
        generated_time=generated_time,
        volume_quantity=volume_quantity,
        romaneio_weight=romaneio_weight,
        package_code=package_code,
        address_code=address_code,
        barcode_payload=barcode_payload,
        client_reference=client_reference,
        sync_status=SimulationRomaneioEntry.SYNC_PENDING,
    )

    error = _insert_simulation_romaneio_oracle(entry)
    if error:
        entry.sync_status = SimulationRomaneioEntry.SYNC_ERROR
        entry.sync_message = error[:255]
        entry.save(update_fields=["sequence_record", "sync_status", "sync_message"])
        return entry, error

    # A constraint `uq_romaneio_package_success` do modelo é só um backstop de
    # integridade local para a corrida entre duas leituras da mesma embalagem
    # passando pela checagem acima ao mesmo tempo — não tem como recuperar
    # dela aqui sem arriscar rotular como "duplicado" um envio que este mesmo
    # request acabou de gravar de verdade no Oracle. Uma trava correta contra
    # essa corrida depende de constraint na própria USU_TCONROM.
    entry.sync_status = SimulationRomaneioEntry.SYNC_SUCCESS
    entry.sync_message = (
        f"Registro enviado com sucesso para a base de simulações. Sequência gerada: {entry.sequence_record}."
    )
    entry.synced_at = timezone.now()
    entry.save(update_fields=["sequence_record", "sync_status", "sync_message", "synced_at"])
    return entry, None


def _fetch_simulation_romaneio_ranking_data(days=5):
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=max(days - 1, 0))
    conn = None
    cur = None
    try:
        conn, _driver_name = _connect_simulation_oracle()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                USU_CODMAT,
                COUNT(*) AS total_romaneios,
                NVL(SUM(USU_QTDVOL), 0) AS total_volumes,
                NVL(SUM(USU_PESROM), 0) AS total_peso
            FROM USU_TCONROM
            WHERE TRUNC(USU_DATGER) BETWEEN :data_inicial AND :data_final
            GROUP BY USU_CODMAT
            ORDER BY total_romaneios DESC, total_peso DESC, total_volumes DESC, USU_CODMAT
            """,
            {"data_inicial": start_date, "data_final": end_date},
        )
        ranking_rows = []
        for position, row in enumerate(cur.fetchall(), start=1):
            ranking_rows.append(
                {
                    "rank": position,
                    "user_code": str(row[0]),
                    "total_romaneios": int(row[1] or 0),
                    "total_volumes": int(row[2] or 0),
                    "total_peso": Decimal(str(row[3] or 0)),
                }
            )

        cur.execute(
            """
            SELECT
                TRUNC(USU_DATGER) AS dia,
                COUNT(*) AS total_romaneios,
                NVL(SUM(USU_QTDVOL), 0) AS total_volumes,
                NVL(SUM(USU_PESROM), 0) AS total_peso
            FROM USU_TCONROM
            WHERE TRUNC(USU_DATGER) BETWEEN :data_inicial AND :data_final
            GROUP BY TRUNC(USU_DATGER)
            ORDER BY dia
            """,
            {"data_inicial": start_date, "data_final": end_date},
        )
        timeline_map = {
            row[0]: {
                "date": row[0],
                "total_romaneios": int(row[1] or 0),
                "total_volumes": int(row[2] or 0),
                "total_peso": Decimal(str(row[3] or 0)),
            }
            for row in cur.fetchall()
        }

        timeline = []
        for offset in range(days):
            current_day = start_date + timedelta(days=offset)
            timeline.append(
                timeline_map.get(
                    current_day,
                    {
                        "date": current_day,
                        "total_romaneios": 0,
                        "total_volumes": 0,
                        "total_peso": Decimal("0"),
                    },
                )
            )

        summary = {
            "total_romaneios": sum(item["total_romaneios"] for item in ranking_rows),
            "total_volumes": sum(item["total_volumes"] for item in ranking_rows),
            "total_peso": sum((item["total_peso"] for item in ranking_rows), Decimal("0")),
            "total_users": len(ranking_rows),
            "start_date": start_date,
            "end_date": end_date,
        }

        return {
            "ranking_rows": ranking_rows,
            "timeline": timeline,
            "summary": summary,
            "top_by_count": max(ranking_rows, key=lambda item: (item["total_romaneios"], item["total_peso"], item["total_volumes"]), default=None),
            "top_by_weight": max(ranking_rows, key=lambda item: (item["total_peso"], item["total_romaneios"], item["total_volumes"]), default=None),
            "top_by_volume": max(ranking_rows, key=lambda item: (item["total_volumes"], item["total_romaneios"], item["total_peso"]), default=None),
        }
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def logistics_romaneio_page(request):
    oracle_config = _simulation_oracle_config()
    read_now = timezone.localtime()
    form_values = {
        "barcode_payload": "",
        "company_code": "",
        "branch_code": "",
        "sequence_record": "",
        "user_code": "",
        "generated_date": read_now.strftime("%Y-%m-%d"),
        "generated_time": read_now.strftime("%H:%M:%S"),
        "volume_quantity": "",
        "romaneio_weight": "",
        "package_code": "",
        "address_code": "",
    }
    open_launch_modal = False

    if request.method == "POST":
        form_values = {
            "barcode_payload": (request.POST.get("barcode_payload") or "").strip(),
            "company_code": (request.POST.get("company_code") or "").strip(),
            "branch_code": (request.POST.get("branch_code") or "").strip(),
            "sequence_record": (request.POST.get("sequence_record") or "").strip(),
            "user_code": (request.POST.get("user_code") or "").strip(),
            "generated_date": (request.POST.get("generated_date") or "").strip(),
            "generated_time": (request.POST.get("generated_time") or "").strip(),
            "volume_quantity": (request.POST.get("volume_quantity") or "").strip(),
            "romaneio_weight": (request.POST.get("romaneio_weight") or "").strip(),
            "package_code": (request.POST.get("package_code") or "").strip(),
            "address_code": (request.POST.get("address_code") or "").strip(),
        }

        barcode_payload = form_values["barcode_payload"] or None
        company_code = form_values["company_code"]
        branch_code = form_values["branch_code"]
        user_code = form_values["user_code"]
        package_code = _parse_romaneio_numeric_code(form_values["package_code"], ROMANEIO_PACKAGE_CODE_MAX_DIGITS)
        address_code = _parse_romaneio_numeric_code(form_values["address_code"], ROMANEIO_ADDRESS_CODE_MAX_DIGITS)
        read_now = timezone.localtime()
        generated_date = _parse_romaneio_date(form_values["generated_date"]) or read_now.date()
        generated_time = _parse_romaneio_time(form_values["generated_time"]) or read_now.time().replace(microsecond=0)
        volume_quantity = _parse_romaneio_int(form_values["volume_quantity"])
        romaneio_weight = _parse_romaneio_decimal(form_values["romaneio_weight"])

        if not all([company_code, branch_code, user_code, generated_date, generated_time]):
            open_launch_modal = True
            messages.error(request, "Preencha empresa, filial, usuário, data e hora da geração.")
        elif volume_quantity is None:
            open_launch_modal = True
            messages.error(request, "Informe uma quantidade de volumes válida.")
        elif romaneio_weight is None:
            open_launch_modal = True
            messages.error(request, "Informe um peso de romaneio válido.")
        elif not package_code:
            open_launch_modal = True
            messages.error(request, "Informe um código do pallet numérico (até 9 dígitos).")
        elif not address_code:
            open_launch_modal = True
            messages.error(request, "Informe um endereçamento numérico (até 6 dígitos).")
        else:
            entry, error = _submit_romaneio_entry(
                company_code=company_code,
                branch_code=branch_code,
                user_code=user_code,
                generated_date=generated_date,
                generated_time=generated_time,
                volume_quantity=volume_quantity,
                romaneio_weight=romaneio_weight,
                package_code=package_code,
                address_code=address_code,
                barcode_payload=barcode_payload,
            )
            if error:
                messages.error(request, error)
            else:
                messages.success(
                    request,
                    f"Romaneio enviado com sucesso para a base de simulações. Sequência gerada: {entry.sequence_record}.",
                )

            return redirect("logistics_romaneio")

    recent_entries = SimulationRomaneioEntry.objects.all()[:30]
    latest_entry = SimulationRomaneioEntry.objects.first()
    total_entries = SimulationRomaneioEntry.objects.count()
    success_entries = SimulationRomaneioEntry.objects.filter(
        sync_status=SimulationRomaneioEntry.SYNC_SUCCESS
    ).count()
    error_entries = SimulationRomaneioEntry.objects.filter(
        sync_status=SimulationRomaneioEntry.SYNC_ERROR
    ).count()
    return render(
        request,
        "hqbooking/logistics_romaneio.html",
        {
            "oracle_ready": oracle_config["is_ready"],
            "oracle_service_name": oracle_config["service_name"],
            "form_values": form_values,
            "open_launch_modal": open_launch_modal,
            "recent_entries": recent_entries,
            "latest_entry": latest_entry,
            "total_entries": total_entries,
            "success_entries": success_entries,
            "error_entries": error_entries,
        },
    )


def logistics_romaneio_mobile_page(request):
    """Contagem de pallets no celular: matrícula, leitura pela câmera e gravação.

    Tela separada da versão de mesa porque o fluxo é outro: lá o leitor HID
    dispara teclas na página inteira e a pessoa confere um formulário completo;
    aqui a leitura vem só da câmera, um passo por vez, e a conferência é de
    exibição com dois botões. A gravação reaproveita
    `logistics_romaneio_quick_submit`, então a página não precisa de contexto:
    matrícula e leitura vivem no aparelho até o momento do envio.
    """
    return render(request, "hqbooking/logistics_romaneio_mobile.html")


@require_POST
def logistics_romaneio_quick_submit(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "JSON inválido."}, status=400)

    barcode_payload = (payload.get("barcode_payload") or "").strip()
    user_code = (payload.get("user_code") or "").strip()

    if not barcode_payload:
        return JsonResponse({"status": "error", "message": "Informe a leitura do código de barras."}, status=400)
    if not user_code:
        return JsonResponse({"status": "error", "message": "Informe o código do usuário padrão antes de iniciar a leitura contínua."}, status=400)

    mapped_payload = _extract_romaneio_payload(barcode_payload)
    if not mapped_payload:
        return JsonResponse(
            {
                "status": "error",
                "message": "Não foi possível interpretar a leitura automaticamente. Verifique se o código enviou os 6 campos do romaneio.",
            },
            status=400,
        )

    company_code = mapped_payload["company_code"]
    branch_code = mapped_payload["branch_code"]
    volume_quantity = mapped_payload["volume_quantity"]
    romaneio_weight = mapped_payload["romaneio_weight"]
    package_code = mapped_payload["package_code"]
    address_code = mapped_payload["address_code"]
    generated_now = timezone.localtime()
    generated_date = generated_now.date()
    generated_time = generated_now.time().replace(microsecond=0)

    if not company_code or not branch_code:
        return JsonResponse({"status": "error", "message": "A leitura não trouxe empresa e filial válidas."}, status=400)
    if volume_quantity is None:
        return JsonResponse({"status": "error", "message": "A leitura não trouxe uma quantidade de volumes válida."}, status=400)
    if romaneio_weight is None:
        return JsonResponse({"status": "error", "message": "A leitura não trouxe um peso de romaneio válido."}, status=400)
    if not package_code:
        return JsonResponse(
            {"status": "error", "message": "A leitura não trouxe um código do pallet numérico válido."},
            status=400,
        )
    if not address_code:
        return JsonResponse(
            {"status": "error", "message": "A leitura não trouxe um endereçamento numérico válido."},
            status=400,
        )

    entry, error = _submit_romaneio_entry(
        company_code=company_code,
        branch_code=branch_code,
        user_code=user_code,
        generated_date=generated_date,
        generated_time=generated_time,
        volume_quantity=volume_quantity,
        romaneio_weight=romaneio_weight,
        package_code=package_code,
        address_code=address_code,
        barcode_payload=barcode_payload,
    )
    if error:
        is_duplicate = entry.sync_status == SimulationRomaneioEntry.SYNC_DUPLICATE
        return JsonResponse(
            {
                "status": "duplicate" if is_duplicate else "error",
                "message": error,
                "entry": {
                    "created_at": timezone.localtime(entry.created_at).strftime("%d/%m/%Y %H:%M"),
                    "company_code": entry.company_code,
                    "branch_code": entry.branch_code,
                    "sequence_record": entry.sequence_record,
                    "user_code": entry.user_code,
                    "generated_date": entry.generated_date.strftime("%d/%m/%Y"),
                    "generated_time": entry.generated_time.strftime("%H:%M"),
                    "volume_quantity": entry.volume_quantity,
                    "romaneio_weight": str(entry.romaneio_weight),
                    "package_code": entry.package_code,
                    "address_code": entry.address_code,
                    "sync_status": entry.get_sync_status_display(),
                    "sync_message": entry.sync_message,
                },
            },
            status=409 if is_duplicate else 500,
        )

    return JsonResponse(
        {
            "status": "ok",
            "message": f"Romaneio salvo com sucesso. Sequência gerada: {entry.sequence_record}.",
            "entry": {
                "created_at": timezone.localtime(entry.created_at).strftime("%d/%m/%Y %H:%M"),
                "company_code": entry.company_code,
                "branch_code": entry.branch_code,
                "sequence_record": entry.sequence_record,
                "user_code": entry.user_code,
                "generated_date": entry.generated_date.strftime("%d/%m/%Y"),
                "generated_time": entry.generated_time.strftime("%H:%M"),
                "volume_quantity": entry.volume_quantity,
                "romaneio_weight": str(entry.romaneio_weight),
                "package_code": entry.package_code,
                "address_code": entry.address_code,
                "sync_status": entry.get_sync_status_display(),
                "sync_message": entry.sync_message,
            },
        }
    )


def logistics_romaneio_ranking_page(request):
    oracle_config = _simulation_oracle_config()
    ranking_data = {
        "ranking_rows": [],
        "timeline": [],
        "summary": {
            "total_romaneios": 0,
            "total_volumes": 0,
            "total_peso": Decimal("0"),
            "total_users": 0,
            "start_date": timezone.localdate() - timedelta(days=4),
            "end_date": timezone.localdate(),
        },
        "top_by_count": None,
        "top_by_weight": None,
        "top_by_volume": None,
    }
    ranking_error = None

    if oracle_config["is_ready"]:
        try:
            ranking_data = _fetch_simulation_romaneio_ranking_data(days=5)
        except Exception as exc:
            ranking_error = str(exc)
    else:
        ranking_error = (
            "A configuração Oracle não está pronta para montar o ranking. "
            "Verifique as variáveis da base de simulações."
        )

    return render(
        request,
        "hqbooking/logistics_romaneio_ranking.html",
        {
            "oracle_ready": oracle_config["is_ready"],
            "oracle_service_name": oracle_config["service_name"],
            "ranking_error": ranking_error,
            **ranking_data,
        },
    )

