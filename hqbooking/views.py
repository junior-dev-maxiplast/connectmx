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
    Tire,
    TireMovement,
    Truck,
    TruckTireChange,
    TruckTireChangeHistory,
    TruckModelTemplate,
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
        "user": os.getenv("ERP_SIM_DB_USER", "seniorerpsimulacoes"),
        "password": os.getenv("ERP_SIM_DB_PASSWORD", "seniorerpsimulacoes"),
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


def _parse_romaneio_decimal(raw_value):
    normalized = str(raw_value or "").strip().replace("R$", "").replace(" ", "")
    if not normalized:
        return None
    normalized = normalized.replace(".", "").replace(",", ".") if normalized.count(",") == 1 else normalized.replace(",", ".")
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None


def _split_romaneio_payload(raw_payload):
    source = str(raw_payload or "").strip()
    if not source:
        return []

    splitters = [r"\r?\n", r"\t", r"/", r"\|", r";"]
    for splitter in splitters:
        parts = [item.strip() for item in re.split(splitter, source) if item.strip()]
        if len(parts) in (5, 8):
            return parts
    return []


def _map_romaneio_payload(parts):
    if not isinstance(parts, list):
        return None
    if len(parts) == 5:
        return {
            "company_code": parts[0],
            "branch_code": parts[1],
            "source_sequence": parts[2],
            "volume_quantity": parts[3],
            "romaneio_weight": parts[4],
        }
    if len(parts) == 8:
        return {
            "company_code": parts[0],
            "branch_code": parts[1],
            "source_sequence": parts[2],
            "volume_quantity": parts[6],
            "romaneio_weight": parts[7],
        }
    return None


def _extract_romaneio_payload(raw_payload):
    payload_parts = _split_romaneio_payload(raw_payload)
    mapped = _map_romaneio_payload(payload_parts)
    if not mapped:
        return None

    return {
        "company_code": mapped["company_code"],
        "branch_code": mapped["branch_code"],
        "source_sequence": mapped["source_sequence"],
        "volume_quantity": _parse_romaneio_int(mapped["volume_quantity"]),
        "romaneio_weight": _parse_romaneio_decimal(mapped["romaneio_weight"]),
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
            "ERP_SIM_DB_USER e ERP_SIM_DB_PASSWORD."
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
        "cod_usuario": int(entry.user_code) if str(entry.user_code).isdigit() else entry.user_code,
        "data_geracao": entry.generated_date,
        "hora_geracao": int(entry.generated_time.strftime("%H%M")),
        "quantidade_volumes": int(entry.volume_quantity),
        "peso_romaneio": Decimal(entry.romaneio_weight),
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
                USU_CODUSU,
                USU_DATGER,
                USU_HORGER,
                USU_QTDVOL,
                USU_PESROM
            ) VALUES (
                :empresa,
                :filial,
                :sequencia_registro,
                :cod_usuario,
                :data_geracao,
                :hora_geracao,
                :quantidade_volumes,
                :peso_romaneio
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


def _submit_romaneio_entry(
    *,
    company_code,
    branch_code,
    user_code,
    generated_date,
    generated_time,
    volume_quantity,
    romaneio_weight,
    barcode_payload=None,
):
    entry = SimulationRomaneioEntry.objects.create(
        company_code=company_code,
        branch_code=branch_code,
        sequence_record="",
        user_code=user_code,
        generated_date=generated_date,
        generated_time=generated_time,
        volume_quantity=volume_quantity,
        romaneio_weight=romaneio_weight,
        barcode_payload=barcode_payload,
        sync_status=SimulationRomaneioEntry.SYNC_PENDING,
    )

    error = _insert_simulation_romaneio_oracle(entry)
    if error:
        entry.sync_status = SimulationRomaneioEntry.SYNC_ERROR
        entry.sync_message = error[:255]
        entry.save(update_fields=["sequence_record", "sync_status", "sync_message"])
        return entry, error

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
                USU_CODUSU,
                COUNT(*) AS total_romaneios,
                NVL(SUM(USU_QTDVOL), 0) AS total_volumes,
                NVL(SUM(USU_PESROM), 0) AS total_peso
            FROM USU_TCONROM
            WHERE TRUNC(USU_DATGER) BETWEEN :data_inicial AND :data_final
            GROUP BY USU_CODUSU
            ORDER BY total_romaneios DESC, total_peso DESC, total_volumes DESC, USU_CODUSU
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
        }

        barcode_payload = form_values["barcode_payload"] or None
        company_code = form_values["company_code"]
        branch_code = form_values["branch_code"]
        user_code = form_values["user_code"]
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
        else:
            entry, error = _submit_romaneio_entry(
                company_code=company_code,
                branch_code=branch_code,
                user_code=user_code,
                generated_date=generated_date,
                generated_time=generated_time,
                volume_quantity=volume_quantity,
                romaneio_weight=romaneio_weight,
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
                "message": "Não foi possível interpretar a leitura automaticamente. Verifique se o código enviou 5 campos do romaneio.",
            },
            status=400,
        )

    company_code = mapped_payload["company_code"]
    branch_code = mapped_payload["branch_code"]
    volume_quantity = mapped_payload["volume_quantity"]
    romaneio_weight = mapped_payload["romaneio_weight"]
    generated_now = timezone.localtime()
    generated_date = generated_now.date()
    generated_time = generated_now.time().replace(microsecond=0)

    if not company_code or not branch_code:
        return JsonResponse({"status": "error", "message": "A leitura não trouxe empresa e filial válidas."}, status=400)
    if volume_quantity is None:
        return JsonResponse({"status": "error", "message": "A leitura não trouxe uma quantidade de volumes válida."}, status=400)
    if romaneio_weight is None:
        return JsonResponse({"status": "error", "message": "A leitura não trouxe um peso de romaneio válido."}, status=400)

    entry, error = _submit_romaneio_entry(
        company_code=company_code,
        branch_code=branch_code,
        user_code=user_code,
        generated_date=generated_date,
        generated_time=generated_time,
        volume_quantity=volume_quantity,
        romaneio_weight=romaneio_weight,
        barcode_payload=barcode_payload,
    )
    if error:
        return JsonResponse(
            {
                "status": "error",
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
                    "sync_status": entry.get_sync_status_display(),
                    "sync_message": entry.sync_message,
                },
            },
            status=500,
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


def _normalize_wheel_list(items, default_prefix):
    normalized = []
    for index, item in enumerate(items or [], start=1):
        if isinstance(item, dict):
            raw_name = item.get("name")
        else:
            raw_name = item
        name = str(raw_name or "").strip() or f"{default_prefix} {index}"
        normalized.append({"name": name})
    return normalized


def _normalize_truck_structure(raw_structure):
    structure = []
    collected_spares = []

    for axle in raw_structure or []:
        if not isinstance(axle, dict):
            continue

        left = _normalize_wheel_list(axle.get("left"), "Esquerda")
        right = _normalize_wheel_list(axle.get("right"), "Direita")
        spares = _normalize_wheel_list(axle.get("spares"), "Estepe")

        legacy_spare = axle.get("spare")
        if legacy_spare:
            spares.extend(_normalize_wheel_list([legacy_spare], "Estepe"))

        collected_spares.extend(spares)
        structure.append(
            {
                "left": left,
                "right": right,
                "spares": [],
            }
        )

    if not structure:
        structure = [{"left": [{"name": "DE"}], "right": [{"name": "DD"}], "spares": []}]

    structure[0]["spares"] = collected_spares
    return structure


def _structure_to_rows(structure):
    rows = []
    spare_slots = []
    tire_no = 1

    normalized = _normalize_truck_structure(structure)
    for axle_idx, axle in enumerate(normalized, start=1):
        left_slots = []
        right_slots = []

        for wheel in axle.get("left", []):
            label = (wheel.get("name") or f"E{tire_no}").strip()
            left_slots.append(
                {
                    "tire_number": tire_no,
                    "position_label": label,
                    "tire_code": label,
                    "is_spare": False,
                }
            )
            tire_no += 1

        for wheel in axle.get("right", []):
            label = (wheel.get("name") or f"D{tire_no}").strip()
            right_slots.append(
                {
                    "tire_number": tire_no,
                    "position_label": label,
                    "tire_code": label,
                    "is_spare": False,
                }
            )
            tire_no += 1

        rows.append({"axle_index": axle_idx, "left_slots": left_slots, "right_slots": right_slots})

    for spare_index, spare in enumerate(normalized[0].get("spares", []), start=1):
        label = (spare.get("name") or f"Estepe {spare_index}").strip()
        spare_slots.append(
            {
                "tire_number": tire_no,
                "position_label": label,
                "tire_code": label,
                "is_spare": True,
            }
        )
        tire_no += 1

    return rows, spare_slots, tire_no - 1


def _position_lookup(rows, spare_slots):
    lookup = {}
    for row in rows:
        for slot in row.get("left_slots", []):
            lookup[slot["tire_number"]] = slot["position_label"]
        for slot in row.get("right_slots", []):
            lookup[slot["tire_number"]] = slot["position_label"]
    for slot in spare_slots or []:
        lookup[slot["tire_number"]] = slot["position_label"]
    return lookup


def _parse_optional_date(raw_value):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        return None


def _parse_optional_positive_int(raw_value):
    raw_value = str(raw_value or "").strip()
    if not raw_value.isdigit():
        return None
    return int(raw_value)


def _parse_optional_decimal(raw_value):
    raw_value = str(raw_value or "").strip().replace("R$", "").replace(" ", "")
    if not raw_value:
        return None
    raw_value = raw_value.replace(".", "").replace(",", ".") if raw_value.count(",") == 1 and raw_value.count(".") >= 1 else raw_value.replace(",", ".")
    try:
        return Decimal(raw_value)
    except (InvalidOperation, ValueError):
        return None


def _parse_tire_batch_serials(raw_value):
    normalized_tokens = []
    repeated_tokens = []
    seen = set()
    raw_text = str(raw_value or "").replace("\r", "\n")
    for chunk in raw_text.replace(";", "\n").replace(",", "\n").split("\n"):
        serial = str(chunk or "").strip()
        if not serial:
            continue
        key = serial.lower()
        if key in seen:
            repeated_tokens.append(serial)
            continue
        seen.add(key)
        normalized_tokens.append(serial)
    return normalized_tokens, repeated_tokens


def _build_generated_tire_serials(prefix, start_number, quantity, pad_length=0):
    normalized_prefix = str(prefix or "").strip()
    if not normalized_prefix:
        return [], "Informe um prefixo para gerar os números do lote."
    if start_number is None:
        return [], "Informe o número inicial da sequência."
    if quantity is None or quantity <= 0:
        return [], "Informe uma quantidade válida para gerar o lote."
    if quantity > 500:
        return [], "O cadastro em lote sequencial aceita no máximo 500 pneus por vez."

    normalized_pad = max(0, min(int(pad_length or 0), 8))
    generated_serials = []
    for offset in range(quantity):
        current_number = start_number + offset
        serial_suffix = str(current_number).zfill(normalized_pad) if normalized_pad else str(current_number)
        generated_serials.append(f"{normalized_prefix}{serial_suffix}")
    return generated_serials, None


def _log_tire_movement(
    tire,
    movement_type,
    truck=None,
    tire_number=None,
    position_label=None,
    movement_date=None,
    odometer_km=None,
    movement_cost=None,
    note=None,
):
    TireMovement.objects.create(
        tire=tire,
        movement_type=movement_type,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label or None,
        movement_date=movement_date or timezone.localdate(),
        odometer_km=odometer_km,
        movement_cost=movement_cost,
        note=note or None,
    )


def _calculate_slot_run_metrics(previous_changed_on, changed_on, previous_odometer_km, odometer_km):
    run_days = None
    run_km = None
    if previous_changed_on and changed_on and changed_on >= previous_changed_on:
        run_days = (changed_on - previous_changed_on).days
    if previous_odometer_km is not None and odometer_km is not None and odometer_km >= previous_odometer_km:
        run_km = odometer_km - previous_odometer_km
    return run_days, run_km


def _build_reposition_note(base_note, from_position, to_position):
    route = f"{from_position} -> {to_position}"
    note = (base_note or "").strip()
    if note:
        return f"{note} ({route})"
    return f"Reposicionado manualmente ({route})."


def _upsert_truck_tire_change_row(row, tire, changed_on=None, odometer_km=None, note=None):
    row.tire = tire
    row.tire_code = tire.serial_number if tire else None
    row.tire_brand = tire.brand if tire else None
    row.changed_on = changed_on
    row.odometer_km = odometer_km
    row.note = note or None
    row.save(
        update_fields=[
            "tire",
            "tire_code",
            "tire_brand",
            "changed_on",
            "odometer_km",
            "note",
            "updated_at",
        ]
    )


def _record_truck_tire_history(
    truck,
    tire_number,
    tire,
    changed_on=None,
    odometer_km=None,
    previous_tire_code=None,
    previous_tire_brand=None,
    previous_changed_on=None,
    previous_odometer_km=None,
    run_days=None,
    run_km=None,
    action_type=None,
    note=None,
):
    TruckTireChangeHistory.objects.create(
        truck=truck,
        tire_number=tire_number,
        tire=tire,
        tire_code=tire.serial_number if tire else None,
        tire_brand=tire.brand if tire else None,
        changed_on=changed_on,
        odometer_km=odometer_km,
        previous_tire_code=previous_tire_code,
        previous_tire_brand=previous_tire_brand,
        previous_changed_on=previous_changed_on,
        previous_odometer_km=previous_odometer_km,
        run_days=run_days,
        run_km=run_km,
        action_type=action_type,
        note=note,
    )


def _move_tire_to_stock(tire, movement_date=None, odometer_km=None, note=None, truck=None, tire_number=None, position_label=None):
    tire.status = Tire.STATUS_STOCK
    tire.current_truck = None
    tire.current_tire_number = None
    tire.current_slot_label = None
    tire.discarded_on = None
    tire.save(
        update_fields=[
            "status",
            "current_truck",
            "current_tire_number",
            "current_slot_label",
            "discarded_on",
            "updated_at",
        ]
    )
    _log_tire_movement(
        tire=tire,
        movement_type=TireMovement.TYPE_TO_STOCK,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        movement_date=movement_date,
        odometer_km=odometer_km,
        note=note,
    )


def _send_tire_to_retread(
    tire,
    movement_date=None,
    odometer_km=None,
    note=None,
    truck=None,
    tire_number=None,
    position_label=None,
):
    tire.status = Tire.STATUS_RETREADING
    tire.current_truck = None
    tire.current_tire_number = None
    tire.current_slot_label = None
    tire.discarded_on = None
    tire.save(
        update_fields=[
            "status",
            "current_truck",
            "current_tire_number",
            "current_slot_label",
            "discarded_on",
            "updated_at",
        ]
    )
    _log_tire_movement(
        tire=tire,
        movement_type=TireMovement.TYPE_TO_RETREAD,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        movement_date=movement_date,
        odometer_km=odometer_km,
        note=note or "Enviado para recapagem.",
    )


def _return_tire_from_retread(tire, movement_date=None, odometer_km=None, movement_cost=None, note=None):
    tire.status = Tire.STATUS_STOCK
    tire.current_truck = None
    tire.current_tire_number = None
    tire.current_slot_label = None
    tire.discarded_on = None
    tire.recap_count = min(int(tire.recap_count or 0) + 1, 3)
    if movement_cost is not None:
        tire.last_retread_cost = movement_cost
        tire.total_retread_cost = (tire.total_retread_cost or Decimal("0")) + movement_cost
    tire.save(
        update_fields=[
            "status",
            "current_truck",
            "current_tire_number",
            "current_slot_label",
            "discarded_on",
            "recap_count",
            "last_retread_cost",
            "total_retread_cost",
            "updated_at",
        ]
    )
    _log_tire_movement(
        tire=tire,
        movement_type=TireMovement.TYPE_FROM_RETREAD,
        movement_date=movement_date,
        odometer_km=odometer_km,
        movement_cost=movement_cost,
        note=note or f"Retorno da recapagem {tire.recap_count}/3.",
    )


def _discard_tire(tire, movement_date=None, odometer_km=None, note=None, truck=None, tire_number=None, position_label=None):
    tire.status = Tire.STATUS_DISCARDED
    tire.current_truck = None
    tire.current_tire_number = None
    tire.current_slot_label = None
    tire.discarded_on = movement_date or timezone.localdate()
    tire.save(
        update_fields=[
            "status",
            "current_truck",
            "current_tire_number",
            "current_slot_label",
            "discarded_on",
            "updated_at",
        ]
    )
    _log_tire_movement(
        tire=tire,
        movement_type=TireMovement.TYPE_DISCARD,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        movement_date=movement_date,
        odometer_km=odometer_km,
        note=note,
    )


def _assign_tire_to_slot(tire, truck, tire_number, position_label, changed_on=None, odometer_km=None, note=None):
    tire.status = Tire.STATUS_INSTALLED
    tire.current_truck = truck
    tire.current_tire_number = tire_number
    tire.current_slot_label = position_label
    if not tire.registered_on:
        tire.registered_on = changed_on or timezone.localdate()
    tire.save(
        update_fields=[
            "status",
            "current_truck",
            "current_tire_number",
            "current_slot_label",
            "registered_on",
            "updated_at",
        ]
    )
    _log_tire_movement(
        tire=tire,
        movement_type=TireMovement.TYPE_INSTALL,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        movement_date=changed_on,
        odometer_km=odometer_km,
        note=note,
    )


def _assign_tire_to_slot_with_type(
    tire,
    truck,
    tire_number,
    position_label,
    movement_type,
    changed_on=None,
    odometer_km=None,
    note=None,
):
    tire.status = Tire.STATUS_INSTALLED
    tire.current_truck = truck
    tire.current_tire_number = tire_number
    tire.current_slot_label = position_label
    if not tire.registered_on:
        tire.registered_on = changed_on or timezone.localdate()
    tire.save(
        update_fields=[
            "status",
            "current_truck",
            "current_tire_number",
            "current_slot_label",
            "registered_on",
            "updated_at",
        ]
    )
    _log_tire_movement(
        tire=tire,
        movement_type=movement_type or TireMovement.TYPE_INSTALL,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        movement_date=changed_on,
        odometer_km=odometer_km,
        note=note,
    )


def _reposition_truck_tire(
    truck,
    source_tire_number,
    target_tire_number,
    position_lookup,
    changed_on=None,
    odometer_km=None,
    note=None,
):
    if source_tire_number == target_tire_number:
        return "Selecione uma posição diferente para reposicionar o pneu."

    source_position = position_lookup.get(source_tire_number, f"Posição {source_tire_number}")
    target_position = position_lookup.get(target_tire_number, f"Posição {target_tire_number}")

    source_row = (
        TruckTireChange.objects.select_related("tire")
        .filter(truck=truck, tire_number=source_tire_number)
        .first()
    )
    if not source_row or not source_row.tire:
        return "Não existe pneu instalado na posição de origem."

    target_row = (
        TruckTireChange.objects.select_related("tire")
        .filter(truck=truck, tire_number=target_tire_number)
        .first()
    )
    source_tire = source_row.tire
    target_tire = target_row.tire if target_row else None

    source_note = _build_reposition_note(note, source_position, target_position)
    target_note = _build_reposition_note(note, target_position, source_position) if target_tire else None

    source_previous_code = source_row.tire_code
    source_previous_brand = source_row.tire_brand
    source_previous_changed_on = source_row.changed_on
    source_previous_odometer_km = source_row.odometer_km
    source_run_days, source_run_km = _calculate_slot_run_metrics(
        source_previous_changed_on,
        changed_on,
        source_previous_odometer_km,
        odometer_km,
    )

    target_previous_code = target_row.tire_code if target_row else None
    target_previous_brand = target_row.tire_brand if target_row else None
    target_previous_changed_on = target_row.changed_on if target_row else None
    target_previous_odometer_km = target_row.odometer_km if target_row else None
    target_run_days, target_run_km = _calculate_slot_run_metrics(
        target_previous_changed_on,
        changed_on,
        target_previous_odometer_km,
        odometer_km,
    )

    if target_tire:
        _upsert_truck_tire_change_row(
            source_row,
            target_tire,
            changed_on=changed_on,
            odometer_km=odometer_km,
            note=target_note,
        )
    else:
        source_row.delete()

    target_row, _ = TruckTireChange.objects.get_or_create(truck=truck, tire_number=target_tire_number)
    _upsert_truck_tire_change_row(
        target_row,
        source_tire,
        changed_on=changed_on,
        odometer_km=odometer_km,
        note=source_note,
    )

    _assign_tire_to_slot_with_type(
        source_tire,
        truck=truck,
        tire_number=target_tire_number,
        position_label=target_position,
        movement_type=TireMovement.TYPE_REPOSITION,
        changed_on=changed_on,
        odometer_km=odometer_km,
        note=source_note,
    )
    _record_truck_tire_history(
        truck=truck,
        tire_number=target_tire_number,
        tire=source_tire,
        changed_on=changed_on,
        odometer_km=odometer_km,
        previous_tire_code=target_previous_code,
        previous_tire_brand=target_previous_brand,
        previous_changed_on=target_previous_changed_on,
        previous_odometer_km=target_previous_odometer_km,
        run_days=target_run_days,
        run_km=target_run_km,
        action_type="swap",
        note=source_note,
    )

    if target_tire:
        _assign_tire_to_slot_with_type(
            target_tire,
            truck=truck,
            tire_number=source_tire_number,
            position_label=source_position,
            movement_type=TireMovement.TYPE_REPOSITION,
            changed_on=changed_on,
            odometer_km=odometer_km,
            note=target_note,
        )
        _record_truck_tire_history(
            truck=truck,
            tire_number=source_tire_number,
            tire=target_tire,
            changed_on=changed_on,
            odometer_km=odometer_km,
            previous_tire_code=source_previous_code,
            previous_tire_brand=source_previous_brand,
            previous_changed_on=source_previous_changed_on,
            previous_odometer_km=source_previous_odometer_km,
            run_days=source_run_days,
            run_km=source_run_km,
            action_type="swap",
            note=target_note,
        )

    return None


def _resolve_tire_for_install(
    action_mode,
    stock_tire_id,
    new_tire_brand,
    new_tire_serial,
    new_tire_purchase_value=None,
    registered_on=None,
    note=None,
):
    if action_mode == "install_stock":
        if not stock_tire_id:
            return None, "Selecione um pneu do estoque para instalar."
        tire = Tire.objects.filter(pk=stock_tire_id, status=Tire.STATUS_STOCK).first()
        if not tire:
            return None, "O pneu selecionado não está disponível no estoque."
        return tire, None

    serial = (new_tire_serial or "").strip()
    brand = (new_tire_brand or "").strip()
    if not serial or not brand:
        return None, "Informe a marca e o numero do pneu."

    tire = Tire.objects.filter(serial_number__iexact=serial).first()
    if tire is None:
        tire = Tire.objects.create(
            brand=brand,
            serial_number=serial,
            status=Tire.STATUS_STOCK,
            purchase_value=new_tire_purchase_value,
            registered_on=registered_on or timezone.localdate(),
            notes=note or None,
        )
        _log_tire_movement(
            tire=tire,
            movement_type=TireMovement.TYPE_REGISTER,
            movement_date=registered_on,
            note=note,
        )
        return tire, None

    if tire.status == Tire.STATUS_DISCARDED:
        return None, "Este pneu está descartado e não pode ser reutilizado."
    if tire.status == Tire.STATUS_RETREADING:
        return None, "Este pneu está em recapagem e ainda não retornou ao estoque."
    if tire.status == Tire.STATUS_INSTALLED:
        return None, "Este pneu já está instalado em outro caminhão."
    return tire, None


TRUCK_LAYOUT_PRESETS = {
    "BASCULANTE_4": [
        {"left": ["DE"], "right": ["DD"]},
        {"left": ["TE"], "right": ["TD"]},
    ],
    "TRUCK_6": [
        {"left": ["DE"], "right": ["DD"]},
        {"left": ["1EE", "1EI"], "right": ["1DI", "1DE"]},
    ],
    "BASCULANTE_8": [
        {"left": ["DE"], "right": ["DD"]},
        {"left": ["1EE", "1EI"], "right": ["1DI", "1DE"]},
        {"left": ["2EE"], "right": ["2DE"]},
    ],
    "BASCULANTE_10": [
        {"left": ["DE"], "right": ["DD"]},
        {"left": ["1EE", "1EI"], "right": ["1DI", "1DE"]},
        {"left": ["2EE", "2EI"], "right": ["2DI", "2DE"]},
    ],
    "BASCULANTE_12": [
        {"left": ["DE"], "right": ["DD"]},
        {"left": ["1EE", "1EI"], "right": ["1DI", "1DE"]},
        {"left": ["2EE", "2EI"], "right": ["2DI", "2DE"]},
        {"left": ["3EE"], "right": ["3DE"]},
    ],
    "CARRETA_14": [
        {"left": ["DE"], "right": ["DD"]},
        {"left": ["1EE", "1EI"], "right": ["1DI", "1DE"]},
        {"left": ["2EE", "2EI"], "right": ["2DI", "2DE"]},
        {"left": ["3EE", "3EI"], "right": ["3DI", "3DE"]},
    ],
}


def _build_auto_axle_layout(axle_count):
    axle_count = max(2, int(axle_count or 2))
    rows = [{"left": ["DE"], "right": ["DD"]}]
    for i in range(1, axle_count):
        rows.append(
            {
                "left": [f"{i}EE", f"{i}EI"],
                "right": [f"{i}DI", f"{i}DE"],
            }
        )
    return rows


def _flatten_layout_rows(base_rows, tire_count):
    tire_count = max(2, int(tire_count or 2))
    current = []
    for row in base_rows:
        for code in row.get("left", []):
            current.append(("left", code))
        for code in row.get("right", []):
            current.append(("right", code))

    if len(current) > tire_count:
        current = current[:tire_count]
    elif len(current) < tire_count:
        idx = len(current) + 1
        while len(current) < tire_count:
            current.append(("left" if len(current) % 2 == 0 else "right", f"COD {idx}"))
            idx += 1

    numbered = []
    for idx, (side, code) in enumerate(current, start=1):
        numbered.append({"tire_number": idx, "tire_code": code, "side": side})

    # Reagrupa em linhas visuais (left / right)
    rows = []
    li = [x for x in numbered if x["side"] == "left"]
    ri = [x for x in numbered if x["side"] == "right"]
    max_len = max(len(li), len(ri))
    for i in range(max_len):
        rows.append(
            {
                "left_slots": li[i : i + 1],
                "right_slots": ri[i : i + 1],
            }
        )
    return rows


def truck_tire_control_page(request):
    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()
        if form_id == "save_model":
            model_id_raw = (request.POST.get("model_id") or "").strip()
            name = (request.POST.get("name") or "").strip()
            structure_raw = (request.POST.get("structure_json") or "[]").strip()
            if name:
                try:
                    structure = json.loads(structure_raw)
                except Exception:
                    structure = []
                structure = _normalize_truck_structure(structure)

                axle_count = len(structure)
                wheel_count = 0
                for axle in structure:
                    wheel_count += len(axle.get("left", [])) + len(axle.get("right", []))
                wheel_count += len(structure[0].get("spares", [])) if structure else 0

                if model_id_raw.isdigit():
                    row = TruckModelTemplate.objects.filter(pk=int(model_id_raw)).first()
                    if row:
                        row.name = name
                        row.axle_count = axle_count
                        row.wheel_count = wheel_count
                        row.structure_json = json.dumps(structure, ensure_ascii=False)
                        row.save(update_fields=["name", "axle_count", "wheel_count", "structure_json", "updated_at"])
                        return redirect(f"{request.path}?model={row.id}")

                row = TruckModelTemplate.objects.create(
                    name=name,
                    axle_count=axle_count,
                    wheel_count=wheel_count,
                    structure_json=json.dumps(structure, ensure_ascii=False),
                )
                return redirect(f"{request.path}?model={row.id}")

        elif form_id == "delete_model":
            model_id_raw = (request.POST.get("model_id") or "").strip()
            if model_id_raw.isdigit():
                TruckModelTemplate.objects.filter(pk=int(model_id_raw)).delete()
            return redirect(request.path)

        elif form_id == "save_truck":
            truck_id_raw = (request.POST.get("truck_id") or "").strip()
            identifier = (request.POST.get("truck_identifier") or "").strip()
            model_id_raw = (request.POST.get("truck_model_id") or "").strip()
            template = TruckModelTemplate.objects.filter(pk=int(model_id_raw)).first() if model_id_raw.isdigit() else None
            if identifier and template:
                if truck_id_raw.isdigit():
                    truck = Truck.objects.filter(pk=int(truck_id_raw)).first()
                else:
                    truck = None

                if truck:
                    truck.identifier = identifier
                    truck.model_template = template
                    truck.tire_count = int(template.wheel_count or 0)
                    truck.layout_model = "TEMPLATE"
                    truck.save(update_fields=["identifier", "model_template", "tire_count", "layout_model", "updated_at"])
                else:
                    truck = Truck.objects.create(
                        identifier=identifier,
                        model_template=template,
                        tire_count=int(template.wheel_count or 0),
                        layout_model="TEMPLATE",
                    )
                return redirect(f"{request.path}?tab=trucks&truck={truck.id}&model={template.id}")

        elif form_id == "create_tire":
            serial_number = (request.POST.get("serial_number") or "").strip()
            serial_batch = (request.POST.get("serial_batch") or "").strip()
            batch_mode = (request.POST.get("batch_mode") or "").strip().lower()
            batch_prefix = (request.POST.get("batch_prefix") or "").strip()
            batch_start_number = _parse_optional_positive_int(request.POST.get("batch_start_number"))
            batch_quantity = _parse_optional_positive_int(request.POST.get("batch_quantity"))
            batch_pad_length = _parse_optional_positive_int(request.POST.get("batch_pad_length"))
            brand = (request.POST.get("brand") or "").strip()
            registered_on = _parse_optional_date(request.POST.get("registered_on"))
            purchase_value_raw = (request.POST.get("purchase_value") or "").strip()
            purchase_value = _parse_optional_decimal(purchase_value_raw)
            note = (request.POST.get("note") or "").strip() or None

            if purchase_value_raw and purchase_value is None:
                messages.error(request, "Informe um valor valido para o pneu.")
                return redirect(f"{request.path}?tab=inventory")
            if not brand:
                messages.error(request, "Informe a marca para cadastrar o pneu ou o lote.")
                return redirect(f"{request.path}?tab=inventory")

            if batch_mode not in {"single", "paste", "generate"}:
                if serial_batch:
                    batch_mode = "paste"
                elif batch_prefix or batch_start_number is not None or batch_quantity is not None:
                    batch_mode = "generate"
                else:
                    batch_mode = "single"

            repeated_in_batch = []
            batch_serials = []
            if batch_mode == "generate":
                batch_serials, generation_error = _build_generated_tire_serials(
                    batch_prefix,
                    batch_start_number,
                    batch_quantity,
                    pad_length=batch_pad_length or 0,
                )
                if generation_error:
                    messages.error(request, generation_error)
                    return redirect(f"{request.path}?tab=inventory")
            elif batch_mode == "paste":
                batch_serials, repeated_in_batch = _parse_tire_batch_serials(serial_batch)

            if batch_serials:
                created_serials = []
                existing_serials = []

                for batch_serial in batch_serials:
                    if Tire.objects.filter(serial_number__iexact=batch_serial).exists():
                        existing_serials.append(batch_serial)
                        continue

                    tire = Tire.objects.create(
                        serial_number=batch_serial,
                        brand=brand,
                        status=Tire.STATUS_STOCK,
                        purchase_value=purchase_value,
                        registered_on=registered_on or timezone.localdate(),
                        notes=note,
                    )
                    _log_tire_movement(
                        tire=tire,
                        movement_type=TireMovement.TYPE_REGISTER,
                        movement_date=registered_on,
                        note=note,
                    )
                    created_serials.append(batch_serial)

                if created_serials:
                    messages.success(
                        request,
                        f"Lote cadastrado com sucesso: {len(created_serials)} pneu(s) enviado(s) ao estoque.",
                    )
                if existing_serials or repeated_in_batch:
                    skipped_serials = existing_serials + repeated_in_batch
                    messages.warning(
                        request,
                        "Alguns numeros foram ignorados por ja existirem ou estarem repetidos no lote: "
                        + ", ".join(skipped_serials[:8])
                        + ("..." if len(skipped_serials) > 8 else ""),
                    )
                if not created_serials and not existing_serials and not repeated_in_batch:
                    messages.error(request, "Informe ao menos um numero de pneu no lote.")
                return redirect(f"{request.path}?tab=inventory")

            if not serial_number:
                messages.error(request, "Informe um numero de pneu ou preencha o cadastro em lote.")
                return redirect(f"{request.path}?tab=inventory")

            existing = Tire.objects.filter(serial_number__iexact=serial_number).first()
            if existing:
                messages.error(request, "Ja existe um pneu cadastrado com este numero.")
                return redirect(f"{request.path}?tab=inventory")

            tire = Tire.objects.create(
                serial_number=serial_number,
                brand=brand,
                status=Tire.STATUS_STOCK,
                purchase_value=purchase_value,
                registered_on=registered_on or timezone.localdate(),
                notes=note,
            )
            _log_tire_movement(
                tire=tire,
                movement_type=TireMovement.TYPE_REGISTER,
                movement_date=registered_on,
                note=note,
            )
            messages.success(request, "Pneu cadastrado e enviado ao estoque.")
            return redirect(f"{request.path}?tab=inventory")

        elif form_id == "inventory_action":
            tire_id = _parse_optional_positive_int(request.POST.get("tire_id"))
            action = (request.POST.get("inventory_action") or "").strip()
            note = (request.POST.get("inventory_note") or "").strip() or None
            movement_date = _parse_optional_date(request.POST.get("inventory_date"))
            movement_cost_raw = (request.POST.get("inventory_cost") or "").strip()
            movement_cost = _parse_optional_decimal(movement_cost_raw)
            tire = Tire.objects.select_related("current_truck").filter(pk=tire_id).first() if tire_id else None

            if not tire:
                messages.error(request, "Pneu não encontrado.")
                return redirect(f"{request.path}?tab=inventory")
            if movement_cost_raw and movement_cost is None:
                messages.error(request, "Informe um valor válido para o recape.")
                return redirect(f"{request.path}?tab=inventory")

            if action == "send_to_retread":
                if tire.status != Tire.STATUS_STOCK:
                    messages.error(request, "Somente pneus em estoque podem ser enviados para recapagem.")
                    return redirect(f"{request.path}?tab=inventory")
                if tire.recap_count >= 3:
                    messages.error(request, "Este pneu ja atingiu o limite de 3 recapes.")
                    return redirect(f"{request.path}?tab=inventory")

                _send_tire_to_retread(tire, movement_date=movement_date, note=note)
                messages.success(request, "Pneu enviado para recapagem.")
                return redirect(f"{request.path}?tab=inventory")

            if action == "return_from_retread":
                if tire.status != Tire.STATUS_RETREADING:
                    messages.error(request, "Apenas pneus em recapagem podem retornar ao estoque.")
                    return redirect(f"{request.path}?tab=inventory")
                if tire.recap_count >= 3:
                    messages.error(request, "Este pneu ja atingiu o limite de 3 recapes.")
                    return redirect(f"{request.path}?tab=inventory")

                _return_tire_from_retread(
                    tire,
                    movement_date=movement_date,
                    movement_cost=movement_cost,
                    note=note,
                )
                messages.success(request, "Pneu retornou da recapagem para o estoque.")
                return redirect(f"{request.path}?tab=inventory")

            if action == "retread":
                if tire.status != Tire.STATUS_STOCK:
                    messages.error(request, "Somente pneus em estoque podem receber recape.")
                    return redirect(f"{request.path}?tab=inventory")
                if tire.recap_count >= 3:
                    messages.error(request, "Este pneu ja atingiu o limite de 3 recapes.")
                    return redirect(f"{request.path}?tab=inventory")

                tire.recap_count += 1
                tire.save(update_fields=["recap_count", "updated_at"])
                _log_tire_movement(
                    tire=tire,
                    movement_type=TireMovement.TYPE_RETREAD,
                    movement_date=movement_date,
                    note=note or f"Recape {tire.recap_count}/3",
                )
                messages.success(request, "Recape registrado com sucesso.")
                return redirect(f"{request.path}?tab=inventory")

            if action == "discard":
                if tire.status == Tire.STATUS_INSTALLED:
                    messages.error(request, "Remova o pneu do caminhao antes de descartar.")
                    return redirect(f"{request.path}?tab=inventory")
                if tire.status == Tire.STATUS_DISCARDED:
                    messages.info(request, "Este pneu ja esta descartado.")
                    return redirect(f"{request.path}?tab=inventory")

                _discard_tire(tire, movement_date=movement_date, note=note)
                messages.success(request, "Pneu descartado com sucesso.")
                return redirect(f"{request.path}?tab=inventory")

            if action == "delete_permanently":
                if tire.status == Tire.STATUS_INSTALLED or tire.current_truck_id:
                    messages.error(request, "Remova o pneu do caminhao antes de excluir.")
                    return redirect(f"{request.path}?tab=inventory")
                if TruckTireChange.objects.filter(tire=tire).exists():
                    messages.error(request, "Este pneu ainda esta vinculado a uma posicao ativa.")
                    return redirect(f"{request.path}?tab=inventory")

                tire_label = tire.serial_number
                tire.delete()
                messages.success(request, f"Pneu {tire_label} excluido do cadastro com sucesso.")
                return redirect(f"{request.path}?tab=inventory")

            messages.error(request, "Acao de estoque invalida.")
            return redirect(f"{request.path}?tab=inventory")

        elif form_id == "tire_update":
            truck_id = _parse_optional_positive_int(request.POST.get("truck_id"))
            tire_number = _parse_optional_positive_int(request.POST.get("tire_number"))
            action_mode = (request.POST.get("action_mode") or "create_and_install").strip()
            stock_tire_id = _parse_optional_positive_int(request.POST.get("stock_tire_id"))
            new_tire_brand = (request.POST.get("new_tire_brand") or "").strip()
            new_tire_serial = (request.POST.get("new_tire_serial") or "").strip()
            new_tire_purchase_value_raw = (request.POST.get("new_tire_purchase_value") or "").strip()
            new_tire_purchase_value = _parse_optional_decimal(new_tire_purchase_value_raw)
            changed_on = _parse_optional_date(request.POST.get("changed_on"))
            odometer_km = _parse_optional_positive_int(request.POST.get("odometer_km"))
            note = (request.POST.get("note") or "").strip() or None

            truck = Truck.objects.select_related("model_template").filter(pk=truck_id).first() if truck_id else None
            if truck and tire_number:
                if new_tire_purchase_value_raw and new_tire_purchase_value is None:
                    messages.error(request, "Informe um valor válido para o novo pneu.")
                    return redirect(f"{request.path}?tab=trucks&truck={truck.id}")
                truck_structure = _normalize_truck_structure(
                    json.loads(truck.model_template.structure_json or "[]") if truck.model_template else []
                )
                truck_rows, truck_spare_slots, _ = _structure_to_rows(truck_structure)
                position_label = _position_lookup(truck_rows, truck_spare_slots).get(tire_number, f"Posição {tire_number}")

                with transaction.atomic():
                    current_row = (
                        TruckTireChange.objects.select_related("tire")
                        .filter(truck=truck, tire_number=tire_number)
                        .first()
                    )
                    current_tire = current_row.tire if current_row else None
                    previous_tire_code = current_row.tire_code if current_row else None
                    previous_tire_brand = current_row.tire_brand if current_row else None
                    previous_changed_on = current_row.changed_on if current_row else None
                    previous_odometer_km = current_row.odometer_km if current_row else None

                    run_days = None
                    run_km = None
                    if previous_changed_on and changed_on and changed_on >= previous_changed_on:
                        run_days = (changed_on - previous_changed_on).days
                    if previous_odometer_km is not None and odometer_km is not None and odometer_km >= previous_odometer_km:
                        run_km = odometer_km - previous_odometer_km

                    if action_mode == "move_to_stock":
                        if not current_row:
                            messages.error(request, "Não existe pneu instalado nesta posição.")
                            return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                        if current_tire:
                            _move_tire_to_stock(
                                current_tire,
                                movement_date=changed_on,
                                odometer_km=odometer_km,
                                note=note or "Removido do caminhão para estoque.",
                                truck=truck,
                                tire_number=tire_number,
                                position_label=position_label,
                            )

                        current_row.delete()
                        messages.success(request, "Pneu enviado para o estoque.")
                        return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                    if action_mode == "send_current_to_retread":
                        if not current_row:
                            messages.error(request, "Não existe pneu instalado nesta posição.")
                            return redirect(f"{request.path}?tab=trucks&truck={truck.id}")
                        if not current_tire:
                            messages.error(request, "Não foi possível localizar o pneu atual desta posição.")
                            return redirect(f"{request.path}?tab=trucks&truck={truck.id}")
                        if current_tire.recap_count >= 3:
                            messages.error(request, "Este pneu ja atingiu o limite de 3 recapes.")
                            return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                        _send_tire_to_retread(
                            current_tire,
                            movement_date=changed_on,
                            odometer_km=odometer_km,
                            note=note or "Removido do caminhão e enviado para recapagem.",
                            truck=truck,
                            tire_number=tire_number,
                            position_label=position_label,
                        )
                        current_row.delete()
                        messages.success(request, "Pneu enviado diretamente para recapagem.")
                        return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                    if action_mode == "discard_current":
                        if not current_row:
                            messages.error(request, "Não existe pneu instalado nesta posição.")
                            return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                        if current_tire:
                            _discard_tire(
                                current_tire,
                                movement_date=changed_on,
                                odometer_km=odometer_km,
                                note=note or "Pneu descartado a partir do caminhão.",
                                truck=truck,
                                tire_number=tire_number,
                                position_label=position_label,
                            )

                        current_row.delete()
                        messages.success(request, "Pneu descartado com sucesso.")
                        return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                    if action_mode == "install_spare_to_position":
                        target_tire_number = _parse_optional_positive_int(request.POST.get("target_tire_number"))
                        spare_slot_numbers = {slot["tire_number"] for slot in truck_spare_slots}
                        regular_position_lookup = {
                            slot["tire_number"]: slot["position_label"]
                            for row in truck_rows
                            for slot in row.get("left_slots", []) + row.get("right_slots", [])
                        }

                        if tire_number not in spare_slot_numbers:
                            messages.error(request, "A instalacao direta so pode ser feita a partir de um estepe.")
                            return redirect(f"{request.path}?tab=trucks&truck={truck.id}")
                        if not current_row or not current_tire:
                            messages.error(request, "Não existe pneu instalado neste estepe.")
                            return redirect(f"{request.path}?tab=trucks&truck={truck.id}")
                        if not target_tire_number or target_tire_number not in regular_position_lookup:
                            messages.error(request, "Selecione uma posição válida para instalar o estepe.")
                            return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                        target_position_label = regular_position_lookup[target_tire_number]
                        target_row = (
                            TruckTireChange.objects.select_related("tire")
                            .filter(truck=truck, tire_number=target_tire_number)
                            .first()
                        )
                        target_previous_tire = target_row.tire if target_row else None
                        target_previous_code = target_row.tire_code if target_row else None
                        target_previous_brand = target_row.tire_brand if target_row else None
                        target_previous_changed_on = target_row.changed_on if target_row else None
                        target_previous_odometer_km = target_row.odometer_km if target_row else None

                        target_run_days = None
                        target_run_km = None
                        if (
                            target_previous_changed_on
                            and changed_on
                            and changed_on >= target_previous_changed_on
                        ):
                            target_run_days = (changed_on - target_previous_changed_on).days
                        if (
                            target_previous_odometer_km is not None
                            and odometer_km is not None
                            and odometer_km >= target_previous_odometer_km
                        ):
                            target_run_km = odometer_km - target_previous_odometer_km

                        if target_previous_tire:
                            _move_tire_to_stock(
                                target_previous_tire,
                                movement_date=changed_on,
                                odometer_km=odometer_km,
                                note=note or f"Movido para estoque para receber o estepe {position_label}.",
                                truck=truck,
                                tire_number=target_tire_number,
                                position_label=target_position_label,
                            )

                        current_row.delete()
                        target_row, _ = TruckTireChange.objects.get_or_create(truck=truck, tire_number=target_tire_number)
                        target_row.tire = current_tire
                        target_row.tire_code = current_tire.serial_number
                        target_row.tire_brand = current_tire.brand
                        target_row.changed_on = changed_on
                        target_row.odometer_km = odometer_km
                        target_row.note = note or f"Instalado a partir do estepe {position_label}."
                        target_row.save(
                            update_fields=[
                                "tire",
                                "tire_code",
                                "tire_brand",
                                "changed_on",
                                "odometer_km",
                                "note",
                                "updated_at",
                            ]
                        )

                        _assign_tire_to_slot(
                            current_tire,
                            truck=truck,
                            tire_number=target_tire_number,
                            position_label=target_position_label,
                            changed_on=changed_on,
                            odometer_km=odometer_km,
                            note=note or f"Instalado a partir do estepe {position_label}.",
                        )

                        TruckTireChangeHistory.objects.create(
                            truck=truck,
                            tire_number=target_tire_number,
                            tire=current_tire,
                            tire_code=current_tire.serial_number,
                            tire_brand=current_tire.brand,
                            changed_on=changed_on,
                            odometer_km=odometer_km,
                            previous_tire_code=target_previous_code,
                            previous_tire_brand=target_previous_brand,
                            previous_changed_on=target_previous_changed_on,
                            previous_odometer_km=target_previous_odometer_km,
                            run_days=target_run_days,
                            run_km=target_run_km,
                            action_type="install_from_spare",
                            note=note or f"Instalado a partir do estepe {position_label}.",
                        )
                        messages.success(request, "Estepe instalado diretamente na posição selecionada.")
                        return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                    target_tire, error_message = _resolve_tire_for_install(
                        action_mode=action_mode,
                        stock_tire_id=stock_tire_id,
                        new_tire_brand=new_tire_brand,
                        new_tire_serial=new_tire_serial,
                        new_tire_purchase_value=new_tire_purchase_value,
                        registered_on=changed_on,
                        note=note,
                    )
                    if error_message:
                        messages.error(request, error_message)
                        return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                    if (
                        target_tire.status == Tire.STATUS_INSTALLED
                        and (target_tire.current_truck_id != truck.id or target_tire.current_tire_number != tire_number)
                    ):
                        messages.error(request, "Este pneu já está instalado em outro caminhão.")
                        return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

                    if current_tire and current_tire.id != target_tire.id:
                        _move_tire_to_stock(
                            current_tire,
                            movement_date=changed_on,
                            odometer_km=odometer_km,
                            note=note or "Movido automaticamente para estoque durante a troca.",
                            truck=truck,
                            tire_number=tire_number,
                            position_label=position_label,
                        )

                    row, _ = TruckTireChange.objects.get_or_create(truck=truck, tire_number=tire_number)
                    row.tire = target_tire
                    row.tire_code = target_tire.serial_number
                    row.tire_brand = target_tire.brand
                    row.changed_on = changed_on
                    row.odometer_km = odometer_km
                    row.note = note
                    row.save(
                        update_fields=[
                            "tire",
                            "tire_code",
                            "tire_brand",
                            "changed_on",
                            "odometer_km",
                            "note",
                            "updated_at",
                        ]
                    )

                    _assign_tire_to_slot(
                        target_tire,
                        truck=truck,
                        tire_number=tire_number,
                        position_label=position_label,
                        changed_on=changed_on,
                        odometer_km=odometer_km,
                        note=note,
                    )

                    TruckTireChangeHistory.objects.create(
                        truck=truck,
                        tire_number=tire_number,
                        tire=target_tire,
                        tire_code=target_tire.serial_number,
                        tire_brand=target_tire.brand,
                        changed_on=changed_on,
                        odometer_km=odometer_km,
                        previous_tire_code=previous_tire_code,
                        previous_tire_brand=previous_tire_brand,
                        previous_changed_on=previous_changed_on,
                        previous_odometer_km=previous_odometer_km,
                        run_days=run_days,
                        run_km=run_km,
                        action_type="install",
                        note=note,
                    )
                    messages.success(request, "Troca de pneu registrada com sucesso.")
                    return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

        elif form_id == "swap_tires":
            truck_id = _parse_optional_positive_int(request.POST.get("truck_id"))
            source_tire_number = _parse_optional_positive_int(request.POST.get("source_tire_number"))
            target_tire_number = _parse_optional_positive_int(request.POST.get("target_tire_number"))
            changed_on = _parse_optional_date(request.POST.get("changed_on"))
            odometer_km = _parse_optional_positive_int(request.POST.get("odometer_km"))
            note = (request.POST.get("note") or "").strip()

            truck = Truck.objects.select_related("model_template").filter(pk=truck_id).first() if truck_id else None
            if not truck or not truck.model_template:
                messages.error(request, "Caminhão não encontrado para a troca de posição.")
                return redirect(f"{request.path}?tab=trucks")

            if not source_tire_number or not target_tire_number:
                messages.error(request, "Selecione a posição de origem e de destino.")
                return redirect(f"{request.path}?tab=trucks&truck={truck.id}")
            if source_tire_number == target_tire_number:
                messages.error(request, "Selecione uma posição de destino diferente da origem.")
                return redirect(f"{request.path}?tab=trucks&truck={truck.id}")
            if not changed_on:
                messages.error(request, "Informe a data da troca.")
                return redirect(f"{request.path}?tab=trucks&truck={truck.id}")
            if odometer_km is None:
                messages.error(request, "Informe a quilometragem da troca.")
                return redirect(f"{request.path}?tab=trucks&truck={truck.id}")
            if not note:
                messages.error(request, "Informe o motivo da troca de posição.")
                return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

            truck_structure = _normalize_truck_structure(
                json.loads(truck.model_template.structure_json or "[]") if truck.model_template else []
            )
            truck_rows, truck_spare_slots, _ = _structure_to_rows(truck_structure)
            position_lookup = _position_lookup(truck_rows, truck_spare_slots)
            if source_tire_number not in position_lookup or target_tire_number not in position_lookup:
                messages.error(request, "Uma das posições selecionadas não existe neste caminhão.")
                return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

            with transaction.atomic():
                error_message = _reposition_truck_tire(
                    truck=truck,
                    source_tire_number=source_tire_number,
                    target_tire_number=target_tire_number,
                    position_lookup=position_lookup,
                    changed_on=changed_on,
                    odometer_km=odometer_km,
                    note=note,
                )
                if error_message:
                    messages.error(request, error_message)
                    return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

            messages.success(request, "Pneu reposicionado com sucesso.")
            return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

    models_qs = TruckModelTemplate.objects.all().order_by("name")
    trucks_qs = Truck.objects.select_related("model_template").all().order_by("identifier")
    active_tab = (request.GET.get("tab") or "guide").strip().lower()
    if active_tab not in {"dashboard", "guide", "models", "trucks", "movements", "history", "inventory"}:
        active_tab = "guide"
    today = timezone.localdate()

    selected_model = None
    new_model_mode = (request.GET.get("new_model") or "").strip() in {"1", "true", "yes", "on"}
    model_id = (request.GET.get("model") or "").strip()
    if model_id.isdigit():
        selected_model = TruckModelTemplate.objects.filter(pk=int(model_id)).first()
    if not selected_model and not (active_tab == "models" and new_model_mode):
        selected_model = models_qs.first()

    selected_truck = None
    new_truck_mode = (request.GET.get("new") or "").strip() in {"1", "true", "yes", "on"}
    truck_id = (request.GET.get("truck") or "").strip()
    if truck_id.isdigit():
        selected_truck = trucks_qs.filter(pk=int(truck_id)).first()
    if not selected_truck and not (active_tab == "trucks" and new_truck_mode):
        selected_truck = trucks_qs.first()

    structure = []
    if selected_model and selected_model.structure_json:
        try:
            structure = json.loads(selected_model.structure_json or "[]")
        except Exception:
            structure = []
        structure = _normalize_truck_structure(structure)

    if not structure:
        structure = _normalize_truck_structure([])

    truck_structure = []
    truck_rows = []
    truck_spare_slots = []
    truck_history_rows = []
    truck_movement_rows = []
    truck_transfer_targets = []
    if selected_truck and selected_truck.model_template:
        tire_heat_max_days = 180

        try:
            truck_structure = json.loads(selected_truck.model_template.structure_json or "[]")
        except Exception:
            truck_structure = []
        truck_structure = _normalize_truck_structure(truck_structure)
        if not truck_structure:
            truck_structure = structure
        truck_rows, truck_spare_slots, _ = _structure_to_rows(truck_structure)
        latest = {
            r.tire_number: r
            for r in TruckTireChange.objects.select_related("tire").filter(truck=selected_truck).order_by("tire_number")
        }
        history_qs = TruckTireChangeHistory.objects.filter(truck=selected_truck).order_by("-created_at", "-id")
        truck_history_rows = list(history_qs[:250])
        truck_movement_rows = list(
            TireMovement.objects.select_related("tire")
            .filter(truck=selected_truck)
            .order_by("-created_at", "-id")[:250]
        )

        last_run_metrics_by_tire = {}
        for item in history_qs.iterator(chunk_size=500):
            if item.tire_number in last_run_metrics_by_tire:
                continue
            if item.run_km is None and item.run_days is None:
                continue
            last_run_metrics_by_tire[item.tire_number] = item

        def _enrich_tire_slot(slot):
            change = latest.get(slot["tire_number"])
            slot["change"] = change
            slot["last_metrics"] = last_run_metrics_by_tire.get(slot["tire_number"])
            slot["tire_id"] = change.tire_id if change and change.tire_id else ""
            slot["display_code"] = change.tire_code if change and change.tire_code else slot["tire_code"]
            slot["display_brand"] = change.tire_brand if change and change.tire_brand else "-"
            slot["display_status"] = change.tire.get_status_display() if change and change.tire_id else "Vazio"
            slot["display_recap_count"] = change.tire.recap_count if change and change.tire_id else 0

            tire_age_days = None
            tire_heat = None
            tire_heat_css = None
            if change and change.changed_on:
                tire_age_days = max((today - change.changed_on).days, 0)
                tire_heat = min(float(tire_age_days) / float(tire_heat_max_days), 1.0)
                tire_heat_css = f"{tire_heat:.4f}"

            slot["tire_age_days"] = tire_age_days
            slot["tire_heat"] = tire_heat
            slot["tire_heat_css"] = tire_heat_css

        for row in truck_rows:
            for slot in row["left_slots"]:
                _enrich_tire_slot(slot)
                truck_transfer_targets.append(
                    {"tire_number": slot["tire_number"], "position_label": slot["position_label"]}
                )
            for slot in row["right_slots"]:
                _enrich_tire_slot(slot)
                truck_transfer_targets.append(
                    {"tire_number": slot["tire_number"], "position_label": slot["position_label"]}
                )
        for slot in truck_spare_slots:
            _enrich_tire_slot(slot)

    models_payload = [
        {
            "id": m.id,
            "name": m.name,
            "axle_count": int(m.axle_count or 0),
            "wheel_count": int(m.wheel_count or 0),
            "structure_json": m.structure_json or "[]",
        }
        for m in models_qs
    ]

    inventory_status = (request.GET.get("inventory_status") or "").strip().lower()
    inventory_search = (request.GET.get("inventory_search") or "").strip()
    inventory_all_qs = Tire.objects.select_related("current_truck").all()
    inventory_stats_rows = list(
        Tire.objects.only("status", "purchase_value", "total_retread_cost", "recap_count")
    )
    inventory_summary = {
        "total": len(inventory_stats_rows),
        "stock": 0,
        "installed": 0,
        "retreading": 0,
        "discarded": 0,
        "recappable": 0,
        "purchase_total": Decimal("0"),
        "retread_total": Decimal("0"),
    }
    for tire_row in inventory_stats_rows:
        inventory_summary[tire_row.status] = inventory_summary.get(tire_row.status, 0) + 1
        inventory_summary["purchase_total"] += tire_row.purchase_value or Decimal("0")
        inventory_summary["retread_total"] += tire_row.total_retread_cost or Decimal("0")
        if tire_row.status != Tire.STATUS_DISCARDED and int(tire_row.recap_count or 0) < 3:
            inventory_summary["recappable"] += 1
    inventory_summary["tracked_total"] = inventory_summary["purchase_total"] + inventory_summary["retread_total"]
    inventory_summary["operational"] = (
        inventory_summary["stock"] + inventory_summary["installed"] + inventory_summary["retreading"]
    )
    inventory_status_counts = {
        "all": inventory_summary["total"],
        "stock": inventory_summary["stock"],
        "installed": inventory_summary["installed"],
        "retreading": inventory_summary["retreading"],
        "discarded": inventory_summary["discarded"],
    }
    inventory_recent_tires = list(inventory_all_qs.order_by("-created_at", "-id")[:4])
    inventory_qs = inventory_all_qs.order_by("serial_number", "id")
    if inventory_status in {
        Tire.STATUS_STOCK,
        Tire.STATUS_INSTALLED,
        Tire.STATUS_RETREADING,
        Tire.STATUS_DISCARDED,
    }:
        inventory_qs = inventory_qs.filter(status=inventory_status)
    if inventory_search:
        inventory_qs = inventory_qs.filter(
            Q(serial_number__icontains=inventory_search) | Q(brand__icontains=inventory_search)
        )
    inventory_tires = list(inventory_qs[:400])
    stock_tires = list(
        Tire.objects.filter(status=Tire.STATUS_STOCK)
        .order_by("brand", "serial_number", "id")
    )
    dashboard_tire_rows = list(
        Tire.objects.select_related("current_truck")
        .only(
            "id",
            "brand",
            "serial_number",
            "status",
            "recap_count",
            "purchase_value",
            "total_retread_cost",
            "current_truck_id",
            "registered_on",
        )
        .all()
    )
    dashboard_recent_days_window = 7
    dashboard_recent_start = today - timedelta(days=dashboard_recent_days_window - 1)
    dashboard_movement_days_map = {
        dashboard_recent_start + timedelta(days=offset): 0
        for offset in range(dashboard_recent_days_window)
    }
    dashboard_recent_feed_rows = list(
        TireMovement.objects.select_related("tire", "truck")
        .order_by("-created_at", "-id")[:6]
    )
    dashboard_movement_30_start = today - timedelta(days=29)
    dashboard_movement_rows_30 = list(
        TireMovement.objects.filter(
            Q(movement_date__gte=dashboard_movement_30_start)
            | Q(movement_date__isnull=True, created_at__date__gte=dashboard_movement_30_start)
        )
        .only("movement_type", "movement_date", "created_at")
        .order_by("-created_at", "-id")
    )
    dashboard_type_counts = {value: 0 for value, _label in TireMovement.TYPE_CHOICES}

    def _resolve_dashboard_event_day(movement_row):
        if movement_row.movement_date:
            return movement_row.movement_date
        if timezone.is_aware(movement_row.created_at):
            return timezone.localtime(movement_row.created_at).date()
        return movement_row.created_at.date()

    for movement_row in dashboard_movement_rows_30:
        event_day = _resolve_dashboard_event_day(movement_row)
        if event_day in dashboard_movement_days_map:
            dashboard_movement_days_map[event_day] += 1
        dashboard_type_counts[movement_row.movement_type] = dashboard_type_counts.get(movement_row.movement_type, 0) + 1

    dashboard_total_capacity = sum(int(truck.tire_count or 0) for truck in trucks_qs)
    dashboard_installed_by_truck = {}
    dashboard_brand_counts = {}
    dashboard_retreaded_count = 0
    dashboard_recap_limit_count = 0
    dashboard_recap_bucket_counts = {
        "Sem recape": 0,
        "1 recape": 0,
        "2 recapes": 0,
        "No limite": 0,
    }
    for tire_row in dashboard_tire_rows:
        brand_label = (tire_row.brand or "Sem marca").strip()
        dashboard_brand_counts[brand_label] = dashboard_brand_counts.get(brand_label, 0) + 1

        recap_count = int(tire_row.recap_count or 0)
        if recap_count <= 0:
            dashboard_recap_bucket_counts["Sem recape"] += 1
        elif recap_count == 1:
            dashboard_recap_bucket_counts["1 recape"] += 1
        elif recap_count == 2:
            dashboard_recap_bucket_counts["2 recapes"] += 1
        else:
            dashboard_recap_bucket_counts["No limite"] += 1

        if recap_count > 0:
            dashboard_retreaded_count += 1
        if tire_row.status != Tire.STATUS_DISCARDED and recap_count >= 3:
            dashboard_recap_limit_count += 1

        if tire_row.status == Tire.STATUS_INSTALLED and tire_row.current_truck_id:
            dashboard_installed_by_truck[tire_row.current_truck_id] = (
                dashboard_installed_by_truck.get(tire_row.current_truck_id, 0) + 1
            )

    dashboard_status_definitions = [
        ("stock", "Estoque", "is-stock"),
        ("installed", "Instalados", "is-installed"),
        ("retreading", "Recapagem", "is-retreading"),
        ("discarded", "Descartados", "is-discarded"),
    ]
    dashboard_status_segments = []
    for key, label, tone_class in dashboard_status_definitions:
        count = int(inventory_summary.get(key, 0) or 0)
        percentage = int(round((count / inventory_summary["total"]) * 100)) if inventory_summary["total"] else 0
        dashboard_status_segments.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "percentage": percentage,
                "tone_class": tone_class,
            }
        )

    dashboard_ring_metrics = [
        {
            "label": "Cobertura operacional",
            "percentage": int(
                round((inventory_summary["operational"] / inventory_summary["total"]) * 100)
            )
            if inventory_summary["total"]
            else 0,
            "value_label": f"{inventory_summary['operational']} ativos",
            "helper": "Estoque, instalados e em recapagem sob controle.",
            "tone_class": "is-primary",
        },
        {
            "label": "OcupaÃ§Ã£o dos caminhÃµes",
            "percentage": int(
                round((inventory_summary["installed"] / dashboard_total_capacity) * 100)
            )
            if dashboard_total_capacity
            else 0,
            "value_label": f"{inventory_summary['installed']}/{dashboard_total_capacity or 0}",
            "helper": "PosiÃ§Ãµes preenchidas na frota cadastrada.",
            "tone_class": "is-success",
        },
        {
            "label": "Pneus recapados",
            "percentage": int(
                round((dashboard_retreaded_count / inventory_summary["total"]) * 100)
            )
            if inventory_summary["total"]
            else 0,
            "value_label": f"{dashboard_retreaded_count} com recape",
            "helper": "Itens que jÃ¡ passaram por pelo menos um novo ciclo.",
            "tone_class": "is-warning",
        },
    ]

    dashboard_metric_cards = [
        {
            "label": "Pneus monitorados",
            "value": inventory_summary["total"],
            "detail": f"{inventory_summary['stock']} em estoque e {inventory_summary['installed']} rodando",
            "tone_class": "is-primary",
        },
        {
            "label": "Valor rastreado",
            "value": f"R$ {inventory_summary['tracked_total']}",
            "detail": f"R$ {inventory_summary['purchase_total']} em pneus e R$ {inventory_summary['retread_total']} em recapes",
            "tone_class": "is-soft",
        },
        {
            "label": "MovimentaÃ§Ãµes em 30 dias",
            "value": len(dashboard_movement_rows_30),
            "detail": f"{dashboard_type_counts.get(TireMovement.TYPE_INSTALL, 0)} instalaÃ§Ãµes e {dashboard_type_counts.get(TireMovement.TYPE_RETREAD, 0)} recapes",
            "tone_class": "is-success",
        },
        {
            "label": "Limite de recape",
            "value": dashboard_recap_limit_count,
            "detail": "Pneus que jÃ¡ chegaram ao teto operacional de recapagens",
            "tone_class": "is-warning",
        },
    ]

    dashboard_weekday_labels = ["Seg", "Ter", "Qua", "Qui", "Sex", "SÃ¡b", "Dom"]
    dashboard_max_day_count = max(dashboard_movement_days_map.values()) if dashboard_movement_days_map else 0
    dashboard_movement_days = []
    for chart_day, count in dashboard_movement_days_map.items():
        dashboard_movement_days.append(
            {
                "label": dashboard_weekday_labels[chart_day.weekday()],
                "date_label": chart_day.strftime("%d/%m"),
                "count": count,
                "height_pct": int(round((count / dashboard_max_day_count) * 100)) if dashboard_max_day_count else 0,
                "is_today": chart_day == today,
            }
        )

    dashboard_type_labels = dict(TireMovement.TYPE_CHOICES)
    dashboard_max_type_count = max(dashboard_type_counts.values()) if dashboard_type_counts else 0
    dashboard_movement_type_rows = []
    for movement_type, count in sorted(
        dashboard_type_counts.items(),
        key=lambda item: (-item[1], dashboard_type_labels.get(item[0], item[0])),
    ):
        if count <= 0:
            continue
        dashboard_movement_type_rows.append(
            {
                "label": dashboard_type_labels.get(movement_type, movement_type),
                "count": count,
                "bar_pct": int(round((count / dashboard_max_type_count) * 100)) if dashboard_max_type_count else 0,
            }
        )

    dashboard_truck_rows = []
    for truck in trucks_qs:
        capacity = int(truck.tire_count or 0)
        installed_count = int(dashboard_installed_by_truck.get(truck.id, 0) or 0)
        dashboard_truck_rows.append(
            {
                "identifier": truck.identifier,
                "model_name": truck.model_template.name if truck.model_template else "Sem modelo",
                "installed_count": installed_count,
                "capacity": capacity,
                "occupancy_pct": int(round((installed_count / capacity) * 100)) if capacity else 0,
                "slots_open": max(capacity - installed_count, 0),
            }
        )
    dashboard_truck_rows.sort(key=lambda item: (-item["occupancy_pct"], item["identifier"]))
    dashboard_truck_rows = dashboard_truck_rows[:6]

    dashboard_brand_rows = []
    dashboard_max_brand_count = max(dashboard_brand_counts.values()) if dashboard_brand_counts else 0
    for brand_label, count in sorted(
        dashboard_brand_counts.items(),
        key=lambda item: (-item[1], item[0].lower()),
    )[:6]:
        dashboard_brand_rows.append(
            {
                "label": brand_label,
                "count": count,
                "bar_pct": int(round((count / dashboard_max_brand_count) * 100)) if dashboard_max_brand_count else 0,
            }
        )

    dashboard_recap_bucket_rows = []
    dashboard_max_recap_bucket = max(dashboard_recap_bucket_counts.values()) if dashboard_recap_bucket_counts else 0
    for label, tone_class in [
        ("Sem recape", "is-primary"),
        ("1 recape", "is-success"),
        ("2 recapes", "is-warning"),
        ("No limite", "is-danger"),
    ]:
        count = dashboard_recap_bucket_counts.get(label, 0)
        dashboard_recap_bucket_rows.append(
            {
                "label": label,
                "count": count,
                "fill_pct": int(round((count / dashboard_max_recap_bucket) * 100)) if dashboard_max_recap_bucket else 0,
                "tone_class": tone_class,
            }
        )

    dashboard_history_rows = list(
        TruckTireChangeHistory.objects.only("run_km", "run_days")
        .filter(Q(run_km__isnull=False) | Q(run_days__isnull=False))
    )
    dashboard_run_km_values = [
        int(history_row.run_km)
        for history_row in dashboard_history_rows
        if history_row.run_km is not None
    ]
    dashboard_run_day_values = [
        int(history_row.run_days)
        for history_row in dashboard_history_rows
        if history_row.run_days is not None
    ]
    dashboard_avg_run_km = int(round(sum(dashboard_run_km_values) / len(dashboard_run_km_values))) if dashboard_run_km_values else 0
    dashboard_avg_run_days = int(round(sum(dashboard_run_day_values) / len(dashboard_run_day_values))) if dashboard_run_day_values else 0

    dashboard_installed_changes = list(
        TruckTireChange.objects.select_related("truck", "tire")
        .filter(tire__isnull=False, changed_on__isnull=False)
        .order_by("changed_on", "truck__identifier", "tire_number")
    )
    dashboard_alert_rows = []
    if dashboard_recap_limit_count:
        dashboard_alert_rows.append(
            {
                "tone_class": "is-warning",
                "title": f"{dashboard_recap_limit_count} pneu(s) no limite de recapagem",
                "detail": "Esses itens jÃ¡ chegaram ao teto operacional e merecem atenÃ§Ã£o no prÃ³ximo ciclo.",
                "meta": "Revisar substituiÃ§Ã£o ou descarte controlado.",
            }
        )
    if inventory_summary["retreading"]:
        dashboard_alert_rows.append(
            {
                "tone_class": "is-primary",
                "title": f"{inventory_summary['retreading']} pneu(s) em recapagem",
                "detail": "Acompanhe o retorno para nÃ£o faltar item disponÃ­vel na frota.",
                "meta": "Monitoramento de prazo e custo em andamento.",
            }
        )

    for change_row in sorted(
        dashboard_installed_changes,
        key=lambda item: (today - item.changed_on).days if item.changed_on else 0,
        reverse=True,
    ):
        age_days = max((today - change_row.changed_on).days, 0)
        if age_days < 45:
            continue
        dashboard_alert_rows.append(
            {
                "tone_class": "is-danger" if age_days >= 90 else "is-warning",
                "title": f"{change_row.tire.serial_number} em {change_row.truck.identifier}",
                "detail": f"{age_days} dia(s) na posiÃ§Ã£o {change_row.tire_number} ({change_row.current_slot_label if hasattr(change_row, 'current_slot_label') else change_row.tire_number}).",
                "meta": f"Ãšltima troca em {change_row.changed_on.strftime('%d/%m/%Y')}.",
            }
        )
        if len(dashboard_alert_rows) >= 5:
            break

    dashboard_recent_movement_feed = []
    dashboard_movement_tone_map = {
        TireMovement.TYPE_REGISTER: "is-primary",
        TireMovement.TYPE_INSTALL: "is-success",
        TireMovement.TYPE_REPOSITION: "is-primary",
        TireMovement.TYPE_TO_STOCK: "is-primary",
        TireMovement.TYPE_TO_RETREAD: "is-warning",
        TireMovement.TYPE_FROM_RETREAD: "is-success",
        TireMovement.TYPE_RETREAD: "is-warning",
        TireMovement.TYPE_DISCARD: "is-danger",
    }
    for movement_row in dashboard_recent_feed_rows:
        event_day = _resolve_dashboard_event_day(movement_row)
        dashboard_recent_movement_feed.append(
            {
                "tone_class": dashboard_movement_tone_map.get(movement_row.movement_type, "is-primary"),
                "title": dashboard_type_labels.get(movement_row.movement_type, movement_row.movement_type),
                "subtitle": (
                    f"{movement_row.tire.serial_number} • {movement_row.tire.brand}"
                    if movement_row.tire_id
                    else "Pneu removido do cadastro"
                ),
                "meta": (
                    f"{movement_row.truck.identifier} • {movement_row.position_label}"
                    if movement_row.truck_id
                    else (movement_row.position_label or "Estoque")
                ),
                "date_label": event_day.strftime("%d/%m/%Y"),
            }
        )
    models_count = models_qs.count()
    trucks_count = trucks_qs.count()
    movement_total = TireMovement.objects.count()
    default_model = selected_model or models_qs.first()
    default_truck = selected_truck or trucks_qs.first()
    guide_steps = [
        {
            "number": 1,
            "title": "Criar modelo base",
            "description": "Desenhe os eixos, defina as rodas por lado e salve a estrutura que servira de base para os caminhões.",
            "status_label": f"{models_count} modelo(s) cadastrado(s)" if models_count else "Nenhum modelo cadastrado ainda",
            "is_complete": models_count > 0,
            "is_locked": False,
            "action_label": "Criar modelo",
            "action_href": f"{request.path}?tab=models&new_model=1",
            "secondary_label": "Ver modelos",
            "secondary_href": f"{request.path}?tab=models{f'&model={default_model.id}' if default_model else ''}",
            "helper_text": "Comece sempre pelo desenho do modelo para reaproveitar a estrutura.",
        },
        {
            "number": 2,
            "title": "Cadastrar caminhão",
            "description": "Crie o caminhão com base no modelo salvo para herdar automaticamente a quantidade de posições e estepes.",
            "status_label": f"{trucks_count} caminhão(ões) cadastrado(s)" if trucks_count else "Nenhum caminhão cadastrado ainda",
            "is_complete": trucks_count > 0,
            "is_locked": models_count == 0,
            "action_label": "Novo caminhão" if models_count > 0 else "Criar primeiro modelo",
            "action_href": f"{request.path}?tab=trucks&new=1" if models_count > 0 else f"{request.path}?tab=models&new_model=1",
            "secondary_label": "Abrir caminhões",
            "secondary_href": f"{request.path}?tab=trucks{f'&truck={default_truck.id}' if default_truck else ''}",
            "helper_text": "O caminhão será o local onde as trocas e reposições vão acontecer.",
        },
        {
            "number": 3,
            "title": "Cadastrar pneus no estoque",
            "description": "Registre número, marca, data e valor para deixar o estoque pronto para instalação e rastreabilidade financeira.",
            "status_label": f"{inventory_summary['total']} pneu(s) monitorado(s)" if inventory_summary["total"] else "Estoque ainda vazio",
            "is_complete": inventory_summary["total"] > 0,
            "is_locked": False,
            "action_label": "Cadastrar pneu",
            "action_href": f"{request.path}?tab=inventory#truckInventoryCreateForm",
            "secondary_label": "Abrir estoque",
            "secondary_href": f"{request.path}?tab=inventory",
            "helper_text": "Use o número real do pneu para facilitar trocas, recapes e consultas futuras.",
        },
        {
            "number": 4,
            "title": "Instalar e movimentar",
            "description": "Abra o caminhão, clique nas posições e instale pneus do estoque, mova estepes ou reposicione itens entre os eixos.",
            "status_label": f"{inventory_summary['installed']} pneu(s) em operação" if inventory_summary["installed"] else "Nenhum pneu instalado ainda",
            "is_complete": inventory_summary["installed"] > 0,
            "is_locked": trucks_count == 0 or inventory_summary["total"] == 0,
            "action_label": "Abrir caminhão" if trucks_count > 0 else "Cadastrar caminhão",
            "action_href": (
                f"{request.path}?tab=trucks&truck={default_truck.id}"
                if default_truck
                else (f"{request.path}?tab=trucks&new=1" if models_count > 0 else f"{request.path}?tab=models&new_model=1")
            ),
            "secondary_label": "Ver movimentações",
            "secondary_href": f"{request.path}?tab=movements{f'&truck={default_truck.id}' if default_truck else ''}",
            "helper_text": "Este passo liga o estoque ao mapa visual do caminhão.",
        },
        {
            "number": 5,
            "title": "Acompanhar histórico e ciclo",
            "description": "Consulte recapagens, descarte, transferências e todo o histórico operacional para manter rastreabilidade completa.",
            "status_label": f"{movement_total} movimentação(ões) registrada(s)" if movement_total else "Histórico ainda sem movimentações",
            "is_complete": movement_total > 0,
            "is_locked": False,
            "action_label": "Abrir histórico",
            "action_href": f"{request.path}?tab=history{f'&truck={default_truck.id}' if default_truck else ''}",
            "secondary_label": "Abrir movimentações",
            "secondary_href": f"{request.path}?tab=movements{f'&truck={default_truck.id}' if default_truck else ''}",
            "helper_text": "O histórico ajuda a identificar desgaste, recapagens e descartes ao longo do tempo.",
        },
    ]
    guide_total_steps = len(guide_steps)
    guide_completed_steps = sum(1 for step in guide_steps if step["is_complete"])
    guide_progress_percent = int(round((guide_completed_steps / guide_total_steps) * 100)) if guide_total_steps else 0
    guide_next_step = next((step for step in guide_steps if not step["is_complete"]), guide_steps[-1] if guide_steps else None)

    return render(
        request,
        "hqbooking/truck_tires.html",
        {
            "active_tab": active_tab,
            "models": models_qs,
            "selected_model": selected_model,
            "new_model_mode": bool(new_model_mode and active_tab == "models"),
            "selected_structure_json": json.dumps(structure, ensure_ascii=False),
            "models_payload": json.dumps(models_payload, ensure_ascii=False),
            "trucks": trucks_qs,
            "selected_truck": selected_truck,
            "new_truck_mode": bool(new_truck_mode and active_tab == "trucks"),
            "truck_rows": truck_rows,
            "truck_spare_slots": truck_spare_slots,
            "truck_history_rows": truck_history_rows,
            "truck_movement_rows": truck_movement_rows,
            "stock_tires": stock_tires,
            "inventory_tires": inventory_tires,
            "inventory_status": inventory_status,
            "inventory_search": inventory_search,
            "inventory_summary": inventory_summary,
            "inventory_status_counts": inventory_status_counts,
            "inventory_recent_tires": inventory_recent_tires,
            "dashboard_metric_cards": dashboard_metric_cards,
            "dashboard_ring_metrics": dashboard_ring_metrics,
            "dashboard_status_segments": dashboard_status_segments,
            "dashboard_movement_days": dashboard_movement_days,
            "dashboard_movement_type_rows": dashboard_movement_type_rows,
            "dashboard_truck_rows": dashboard_truck_rows,
            "dashboard_brand_rows": dashboard_brand_rows,
            "dashboard_recap_bucket_rows": dashboard_recap_bucket_rows,
            "dashboard_alert_rows": dashboard_alert_rows,
            "dashboard_recent_movement_feed": dashboard_recent_movement_feed,
            "dashboard_avg_run_km": dashboard_avg_run_km,
            "dashboard_avg_run_days": dashboard_avg_run_days,
            "models_count": models_count,
            "trucks_count": trucks_count,
            "movement_total": movement_total,
            "guide_steps": guide_steps,
            "guide_total_steps": guide_total_steps,
            "guide_completed_steps": guide_completed_steps,
            "guide_progress_percent": guide_progress_percent,
            "guide_next_step": guide_next_step,
            "today_iso": today.isoformat(),
            "today_display": today.strftime("%d/%m/%Y"),
            "tire_status_choices": Tire.STATUS_CHOICES,
            "truck_transfer_targets_json": json.dumps(truck_transfer_targets, ensure_ascii=False),
        },
    )


def truck_tire_history_page(request):
    trucks_qs = Truck.objects.select_related("model_template").all().order_by("identifier")

    selected_truck = None
    truck_id = (request.GET.get("truck") or "").strip()
    if truck_id.isdigit():
        selected_truck = trucks_qs.filter(pk=int(truck_id)).first()
    movement_type = (request.GET.get("movement_type") or "").strip()
    history_search = (request.GET.get("search") or "").strip()
    date_from = _parse_optional_date(request.GET.get("date_from"))
    date_to = _parse_optional_date(request.GET.get("date_to"))

    history_qs = TireMovement.objects.select_related("tire", "truck").all().order_by("-created_at", "-id")
    if selected_truck:
        history_qs = history_qs.filter(truck=selected_truck)
    if movement_type in {value for value, _label in TireMovement.TYPE_CHOICES}:
        history_qs = history_qs.filter(movement_type=movement_type)
    if history_search:
        history_qs = history_qs.filter(
            Q(tire__serial_number__icontains=history_search)
            | Q(tire__brand__icontains=history_search)
            | Q(position_label__icontains=history_search)
            | Q(note__icontains=history_search)
            | Q(truck__identifier__icontains=history_search)
        )
    if date_from:
        history_qs = history_qs.filter(
            Q(movement_date__gte=date_from) | Q(movement_date__isnull=True, created_at__date__gte=date_from)
        )
    if date_to:
        history_qs = history_qs.filter(
            Q(movement_date__lte=date_to) | Q(movement_date__isnull=True, created_at__date__lte=date_to)
        )

    history_rows = list(history_qs[:1000])

    return render(
        request,
        "hqbooking/truck_tire_history.html",
        {
            "trucks": trucks_qs,
            "selected_truck": selected_truck,
            "history_rows": history_rows,
            "movement_type": movement_type,
            "history_search": history_search,
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
            "movement_type_choices": TireMovement.TYPE_CHOICES,
        },
    )
