import calendar
import json
from datetime import date, time, timedelta

from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (
    HeadquartersDateBlock,
    HeadquartersEnvironment,
    HeadquartersReservation,
    LunchReservation,
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
        return JsonResponse({"status": "error", "message": "Nao autenticado"}, status=401)

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
        return JsonResponse({"status": "error", "message": "Nao autenticado"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    target_iso = (payload.get("date") or "").strip()
    try:
        target_date = date.fromisoformat(target_iso)
    except ValueError:
        return JsonResponse({"status": "error", "message": "Data invalida"}, status=400)

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
        return JsonResponse({"status": "error", "message": "Lista de ambientes invalida."}, status=400)

    valid_envs = list(HeadquartersEnvironment.objects.filter(id__in=env_ids))
    if len(valid_envs) != len(env_ids):
        return JsonResponse({"status": "error", "message": "Um ou mais ambientes nao existem."}, status=400)

    try:
        start_h, start_m = start_time_raw.split(":")
        end_h, end_m = end_time_raw.split(":")
        start_time_obj = time(hour=int(start_h), minute=int(start_m))
        end_time_obj = time(hour=int(end_h), minute=int(end_m))
    except Exception:
        return JsonResponse({"status": "error", "message": "Horario invalido."}, status=400)

    if start_time_obj >= end_time_obj:
        return JsonResponse({"status": "error", "message": "Horario final deve ser maior que o inicial."}, status=400)
    if target_date < date.today():
        return JsonResponse({"status": "error", "message": "Nao e permitido reservar datas passadas."}, status=400)

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
        return JsonResponse({"status": "error", "message": "Nao autenticado"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    target_iso = (payload.get("date") or "").strip()
    try:
        target_date = date.fromisoformat(target_iso)
    except ValueError:
        return JsonResponse({"status": "error", "message": "Data invalida"}, status=400)

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
            {"error_message": "Chave de acesso invalida."},
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
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

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
            return JsonResponse({"status": "error", "message": "Filtro de ambiente invalido"}, status=400)

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
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

    with transaction.atomic():
        target = HeadquartersReservation.objects.select_for_update().filter(pk=reservation_id).first()
        if not target:
            return JsonResponse({"status": "error", "message": "Solicitacao nao encontrada"}, status=404)
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
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

    with transaction.atomic():
        target = HeadquartersReservation.objects.select_for_update().filter(pk=reservation_id).first()
        if not target:
            return JsonResponse({"status": "error", "message": "Solicitacao nao encontrada"}, status=404)
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
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

    envs = HeadquartersEnvironment.objects.all().order_by("name", "id")
    payload = [{"id": e.id, "name": e.name, "description": e.description or ""} for e in envs]
    return JsonResponse({"status": "ok", "environments": payload})


@require_POST
def admin_environment_create(request):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

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
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

    env = HeadquartersEnvironment.objects.filter(pk=environment_id).first()
    if not env:
        return JsonResponse({"status": "error", "message": "Ambiente nao encontrado"}, status=404)

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
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

    env = HeadquartersEnvironment.objects.filter(pk=environment_id).first()
    if not env:
        return JsonResponse({"status": "error", "message": "Ambiente nao encontrado"}, status=404)

    env.delete()
    return JsonResponse({"status": "ok"})


@require_GET
def admin_blocks_data(request):
    admin_id = _require_admin(request)
    if not admin_id:
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

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
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    raw_date = (payload.get("blocked_date") or "").strip()
    reason = (payload.get("reason") or "").strip()
    try:
        block_date = date.fromisoformat(raw_date)
    except ValueError:
        return JsonResponse({"status": "error", "message": "Data invalida"}, status=400)

    if block_date < date.today():
        return JsonResponse({"status": "error", "message": "Nao e permitido bloquear data passada"}, status=400)

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
        return JsonResponse({"status": "error", "message": "Nao autorizado"}, status=401)

    block = HeadquartersDateBlock.objects.filter(pk=block_id).first()
    if not block:
        return JsonResponse({"status": "error", "message": "Bloqueio nao encontrado"}, status=404)

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
    def _parse_structure(raw):
        try:
            parsed = json.loads(raw or "[]")
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return []

    def _rows_from_structure(structure):
        rows = []
        tire_no = 1
        for axle_idx, axle in enumerate(structure, start=1):
            left = axle.get("left") or []
            right = axle.get("right") or []
            left_slots = []
            right_slots = []
            for wheel in left:
                left_slots.append({"tire_number": tire_no, "tire_code": (wheel.get("name") or f"L{tire_no}")})
                tire_no += 1
            for wheel in right:
                right_slots.append({"tire_number": tire_no, "tire_code": (wheel.get("name") or f"R{tire_no}")})
                tire_no += 1
            rows.append({"axle_index": axle_idx, "left_slots": left_slots, "right_slots": right_slots})
        return rows, tire_no - 1

    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "").strip()
        if form_id == "save_model":
            model_id_raw = (request.POST.get("model_id") or "").strip()
            name = (request.POST.get("name") or "").strip()
            structure_raw = (request.POST.get("structure_json") or "[]").strip()
            if name:
                try:
                    structure = json.loads(structure_raw)
                    if not isinstance(structure, list):
                        structure = []
                except Exception:
                    structure = []

                axle_count = len(structure)
                wheel_count = 0
                for axle in structure:
                    wheel_count += len(axle.get("left", [])) + len(axle.get("right", []))
                    if axle.get("spare"):
                        wheel_count += 1

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

        elif form_id == "tire_update":
            truck_id = (request.POST.get("truck_id") or "").strip()
            tire_number_raw = (request.POST.get("tire_number") or "").strip()
            tire_code = (request.POST.get("tire_code") or "").strip() or None
            changed_on_raw = (request.POST.get("changed_on") or "").strip()
            odometer_raw = (request.POST.get("odometer_km") or "").strip()
            note = (request.POST.get("note") or "").strip() or None

            if truck_id.isdigit() and tire_number_raw.isdigit():
                truck = Truck.objects.filter(pk=int(truck_id)).first()
                tire_number = int(tire_number_raw)
                if truck and tire_number >= 1:
                    changed_on = None
                    if changed_on_raw:
                        try:
                            changed_on = date.fromisoformat(changed_on_raw)
                        except ValueError:
                            changed_on = None
                    odometer_km = int(odometer_raw) if odometer_raw.isdigit() else None

                    row, _ = TruckTireChange.objects.get_or_create(truck=truck, tire_number=tire_number)
                    row.tire_code = tire_code
                    row.changed_on = changed_on
                    row.odometer_km = odometer_km
                    row.note = note
                    row.save(update_fields=["tire_code", "changed_on", "odometer_km", "note", "updated_at"])

                    TruckTireChangeHistory.objects.create(
                        truck=truck,
                        tire_number=tire_number,
                        tire_code=tire_code,
                        changed_on=changed_on,
                        odometer_km=odometer_km,
                        note=note,
                    )
                    return redirect(f"{request.path}?tab=trucks&truck={truck.id}")

    models_qs = TruckModelTemplate.objects.all().order_by("name")
    trucks_qs = Truck.objects.select_related("model_template").all().order_by("identifier")
    active_tab = (request.GET.get("tab") or "models").strip().lower()
    if active_tab not in {"models", "trucks"}:
        active_tab = "models"

    selected_model = None
    model_id = (request.GET.get("model") or "").strip()
    if model_id.isdigit():
        selected_model = TruckModelTemplate.objects.filter(pk=int(model_id)).first()
    if not selected_model:
        selected_model = models_qs.first()

    selected_truck = None
    truck_id = (request.GET.get("truck") or "").strip()
    if truck_id.isdigit():
        selected_truck = trucks_qs.filter(pk=int(truck_id)).first()
    if not selected_truck:
        selected_truck = trucks_qs.first()

    structure = []
    if selected_model and selected_model.structure_json:
        structure = _parse_structure(selected_model.structure_json)

    if not structure:
        structure = [{"left": [{"name": "DE"}], "right": [{"name": "DD"}], "spare": None}]

    truck_structure = []
    truck_rows = []
    truck_history_rows = []
    if selected_truck and selected_truck.model_template:
        truck_structure = _parse_structure(selected_truck.model_template.structure_json)
        if not truck_structure:
            truck_structure = structure
        truck_rows, _ = _rows_from_structure(truck_structure)
        latest = {
            r.tire_number: r
            for r in TruckTireChange.objects.filter(truck=selected_truck).order_by("tire_number")
        }
        for row in truck_rows:
            for slot in row["left_slots"]:
                slot["change"] = latest.get(slot["tire_number"])
            for slot in row["right_slots"]:
                slot["change"] = latest.get(slot["tire_number"])

        truck_history_rows = list(
            TruckTireChangeHistory.objects.filter(truck=selected_truck).order_by("-created_at", "-id")[:250]
        )

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

    return render(
        request,
        "hqbooking/truck_tires.html",
        {
            "active_tab": active_tab,
            "models": models_qs,
            "selected_model": selected_model,
            "selected_structure_json": json.dumps(structure, ensure_ascii=False),
            "models_payload": json.dumps(models_payload, ensure_ascii=False),
            "trucks": trucks_qs,
            "selected_truck": selected_truck,
            "truck_rows": truck_rows,
            "truck_history_rows": truck_history_rows,
        },
    )
