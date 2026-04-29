import calendar
import json
from datetime import date, time, timedelta

from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import HeadquartersDateBlock, HeadquartersEnvironment, HeadquartersReservation, LunchReservation

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
