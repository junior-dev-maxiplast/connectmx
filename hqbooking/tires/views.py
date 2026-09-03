"""Páginas da logística de pneus.

Cada tela é uma rota própria e recebe apenas o contexto que precisa. As ações de
escrita são endpoints POST dedicados que redirecionam de volta para a página de
origem com uma mensagem.
"""

import json
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from ..models import (
    Tire,
    TireMovement,
    Truck,
    TruckModelTemplate,
    TruckTireChange,
    TruckTireChangeHistory,
)
from . import services


MOVEMENT_TONES = {
    TireMovement.TYPE_REGISTER: "is-neutral",
    TireMovement.TYPE_INSTALL: "is-success",
    TireMovement.TYPE_REPOSITION: "is-info",
    TireMovement.TYPE_TO_STOCK: "is-info",
    TireMovement.TYPE_TO_RETREAD: "is-warning",
    TireMovement.TYPE_FROM_RETREAD: "is-success",
    TireMovement.TYPE_RETREAD: "is-warning",
    TireMovement.TYPE_DISCARD: "is-danger",
}

STATUS_TONES = {
    Tire.STATUS_STOCK: "is-info",
    Tire.STATUS_INSTALLED: "is-success",
    Tire.STATUS_RETREADING: "is-warning",
    Tire.STATUS_DISCARDED: "is-danger",
}

# Teto da checagem de duplicidade em tempo real, alinhado ao limite do lote.
MAX_SERIAL_CHECK = services.MAX_BATCH_SIZE


def _shell(active, **context):
    """Contexto comum a todas as telas do módulo."""
    base = {
        "active_page": active,
        "today": timezone.localdate(),
        "today_iso": timezone.localdate().isoformat(),
    }
    base.update(context)
    return base


def _movement_day(movement):
    if movement.movement_date:
        return movement.movement_date
    created = movement.created_at
    return timezone.localtime(created).date() if timezone.is_aware(created) else created.date()


# --------------------------------------------------------------------------- #
# Painel
# --------------------------------------------------------------------------- #


def dashboard(request):
    today = timezone.localdate()
    summary = services.inventory_summary()
    fleet = services.fleet_overview()

    window_days = 14
    window_start = today - timedelta(days=window_days - 1)
    day_counts = {window_start + timedelta(days=offset): 0 for offset in range(window_days)}

    month_start = today - timedelta(days=29)
    recent_movements = list(
        TireMovement.objects.filter(
            Q(movement_date__gte=month_start)
            | Q(movement_date__isnull=True, created_at__date__gte=month_start)
        ).only("movement_type", "movement_date", "created_at")
    )
    type_counts = {value: 0 for value, _label in TireMovement.TYPE_CHOICES}
    for movement in recent_movements:
        day = _movement_day(movement)
        if day in day_counts:
            day_counts[day] += 1
        type_counts[movement.movement_type] = type_counts.get(movement.movement_type, 0) + 1

    max_day = max(day_counts.values()) if day_counts else 0
    activity_days = [
        {
            "date_label": day.strftime("%d/%m"),
            "count": count,
            "height_pct": int(round((count / max_day) * 100)) if max_day else 0,
            "is_today": day == today,
        }
        for day, count in day_counts.items()
    ]

    status_blocks = []
    for key, label in (
        ("stock", "Estoque"),
        ("installed", "Instalados"),
        ("retreading", "Recapagem"),
        ("discarded", "Descartados"),
    ):
        count = int(summary.get(key, 0) or 0)
        status_blocks.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "percentage": int(round((count / summary["total"]) * 100)) if summary["total"] else 0,
            }
        )

    recap_buckets = {"Sem recape": 0, "1 recape": 0, "2 recapes": 0, "No limite": 0}
    brand_counts = {}
    for tire in Tire.objects.only("brand", "recap_count"):
        brand_counts[(tire.brand or "Sem marca").strip()] = brand_counts.get((tire.brand or "Sem marca").strip(), 0) + 1
        recap_count = int(tire.recap_count or 0)
        if recap_count <= 0:
            recap_buckets["Sem recape"] += 1
        elif recap_count == 1:
            recap_buckets["1 recape"] += 1
        elif recap_count == 2:
            recap_buckets["2 recapes"] += 1
        else:
            recap_buckets["No limite"] += 1

    max_bucket = max(recap_buckets.values()) if recap_buckets else 0
    recap_rows = [
        {
            "label": label,
            "count": count,
            "bar_pct": int(round((count / max_bucket) * 100)) if max_bucket else 0,
            "tone": tone,
        }
        for (label, count), tone in zip(
            recap_buckets.items(), ["is-neutral", "is-success", "is-warning", "is-danger"]
        )
    ]

    max_brand = max(brand_counts.values()) if brand_counts else 0
    brand_rows = [
        {
            "label": label,
            "count": count,
            "bar_pct": int(round((count / max_brand) * 100)) if max_brand else 0,
        }
        for label, count in sorted(brand_counts.items(), key=lambda item: (-item[1], item[0].lower()))[:5]
    ]

    history_rows = list(
        TruckTireChangeHistory.objects.only("run_km", "run_days").filter(
            Q(run_km__isnull=False) | Q(run_days__isnull=False)
        )
    )
    run_km_values = [int(row.run_km) for row in history_rows if row.run_km is not None]
    run_day_values = [int(row.run_days) for row in history_rows if row.run_days is not None]

    alerts = []
    if summary["at_limit"]:
        alerts.append(
            {
                "tone": "is-warning",
                "title": f"{summary['at_limit']} pneu(s) no limite de recapagem",
                "detail": "Chegaram ao teto de 3 ciclos e precisam de substituição ou descarte controlado.",
                "href": f"{reverse('tires_inventory')}?status=stock",
            }
        )
    if summary["retreading"]:
        alerts.append(
            {
                "tone": "is-info",
                "title": f"{summary['retreading']} pneu(s) em recapagem",
                "detail": "Acompanhe o retorno para não faltar item disponível na frota.",
                "href": f"{reverse('tires_inventory')}?status=retreading",
            }
        )
    for change in (
        TruckTireChange.objects.select_related("truck", "tire")
        .filter(tire__isnull=False, changed_on__isnull=False)
        .order_by("changed_on")[:40]
    ):
        age_days = max((today - change.changed_on).days, 0)
        if age_days < 45:
            continue
        alerts.append(
            {
                "tone": "is-danger" if age_days >= 90 else "is-warning",
                "title": f"{change.tire.serial_number} · {age_days} dias na mesma posição",
                "detail": f"{change.truck.identifier} · posição {change.tire_code or change.tire_number}",
                "href": reverse("tires_truck", args=[change.truck_id]),
            }
        )
        if len(alerts) >= 5:
            break

    feed = []
    for movement in TireMovement.objects.select_related("tire", "truck").order_by("-created_at", "-id")[:8]:
        feed.append(
            {
                "tone": MOVEMENT_TONES.get(movement.movement_type, "is-neutral"),
                "title": movement.get_movement_type_display(),
                "subject": movement.tire.serial_number if movement.tire_id else "Pneu removido",
                "context": (
                    f"{movement.truck.identifier} · {movement.position_label or '—'}"
                    if movement.truck_id
                    else (movement.position_label or "Estoque")
                ),
                "date_label": _movement_day(movement).strftime("%d/%m/%Y"),
            }
        )

    capacity_total = sum(item["capacity"] for item in fleet)
    metrics = [
        {
            "label": "Pneus monitorados",
            "value": summary["total"],
            "hint": f"{summary['stock']} em estoque · {summary['installed']} rodando",
        },
        {
            "label": "Ocupação da frota",
            "value": f"{int(round((summary['installed'] / capacity_total) * 100)) if capacity_total else 0}%",
            "hint": f"{summary['installed']} de {capacity_total} posições",
        },
        {
            "label": "Movimentações (30d)",
            "value": len(recent_movements),
            "hint": f"{type_counts.get(TireMovement.TYPE_INSTALL, 0)} instalações · {type_counts.get(TireMovement.TYPE_FROM_RETREAD, 0)} retornos",
        },
        {
            "label": "Valor rastreado",
            "value": f"R$ {summary['tracked_total']:,.2f}".replace(",", "·").replace(".", ",").replace("·", "."),
            "hint": f"R$ {summary['retread_total']:,.2f} em recapes".replace(",", "·").replace(".", ",").replace("·", "."),
        },
    ]

    return render(
        request,
        "hqbooking/tires/dashboard.html",
        _shell(
            "dashboard",
            summary=summary,
            metrics=metrics,
            status_blocks=status_blocks,
            activity_days=activity_days,
            recap_rows=recap_rows,
            brand_rows=brand_rows,
            fleet=sorted(fleet, key=lambda item: (-item["occupancy_pct"], item["truck"].identifier))[:6],
            alerts=alerts,
            feed=feed,
            avg_run_km=int(round(sum(run_km_values) / len(run_km_values))) if run_km_values else 0,
            avg_run_days=int(round(sum(run_day_values) / len(run_day_values))) if run_day_values else 0,
        ),
    )


# --------------------------------------------------------------------------- #
# Frota
# --------------------------------------------------------------------------- #


def fleet(request):
    search = (request.GET.get("q") or "").strip()
    model_id = services.parse_positive_int(request.GET.get("model"))

    models = services.model_options()
    counts = services.trucks_per_model()
    if model_id and not any(model.id == model_id for model in models):
        model_id = None

    model_filters = [{"key": "", "label": "Todos os modelos", "count": sum(counts.values())}]
    model_filters += [
        {"key": model.id, "label": model.name, "count": counts.get(model.id, 0)} for model in models
    ]

    return render(
        request,
        "hqbooking/tires/fleet.html",
        _shell(
            "fleet",
            fleet=services.fleet_overview(search=search, model_id=model_id),
            models=models,
            model_filters=model_filters,
            active_model=model_id,
            search=search,
            has_filters=bool(search or model_id),
            has_trucks=Truck.objects.exists(),
        ),
    )


def truck_detail(request, truck_id):
    truck = get_object_or_404(Truck.objects.select_related("model_template"), pk=truck_id)
    rows, spare_slots, positions = services.truck_layout(truck)
    rows, spare_slots = services.enrich_slots(truck, rows, spare_slots)

    capacity = len(positions)
    filled = sum(
        1
        for slot in [s for row in rows for s in row["left_slots"] + row["right_slots"]] + spare_slots
        if slot["is_filled"]
    )

    transfer_targets = [
        {"tire_number": slot["tire_number"], "position_label": slot["position_label"]}
        for row in rows
        for slot in row["left_slots"] + row["right_slots"]
    ]

    movements = [
        {
            "movement": movement,
            "tone": MOVEMENT_TONES.get(movement.movement_type, "is-neutral"),
        }
        for movement in TireMovement.objects.select_related("tire").filter(truck=truck).order_by("-created_at", "-id")[:12]
    ]

    tire_tracks = services.tire_tracks_for_truck(truck, MOVEMENT_TONES)

    return render(
        request,
        "hqbooking/tires/truck_detail.html",
        _shell(
            "fleet",
            truck=truck,
            models=services.model_options(),
            rows=rows,
            spare_slots=spare_slots,
            capacity=capacity,
            filled=filled,
            open_slots=max(capacity - filled, 0),
            occupancy_pct=int(round((filled / capacity) * 100)) if capacity else 0,
            stock_tires=list(Tire.objects.filter(status=Tire.STATUS_STOCK).order_by("brand", "serial_number")),
            movements=movements,
            tire_tracks=tire_tracks,
            transfer_targets=transfer_targets,
            brands=services.known_brands(),
            tire_models=services.known_tire_models(),
            tire_sizes=services.known_sizes(),
            heat_levels=services.heat_legend(),
        ),
    )


@require_POST
def truck_save(request):
    truck_id = services.parse_positive_int(request.POST.get("truck_id"))
    identifier = (request.POST.get("identifier") or "").strip()
    model_id = services.parse_positive_int(request.POST.get("model_id"))
    template = TruckModelTemplate.objects.filter(pk=model_id).first() if model_id else None

    if not identifier:
        messages.error(request, "Informe a identificação do caminhão.")
        return redirect("tires_fleet")
    if not template:
        messages.error(request, "Selecione um modelo base para o caminhão.")
        return redirect("tires_fleet")

    truck = Truck.objects.filter(pk=truck_id).first() if truck_id else None
    if truck:
        truck.identifier = identifier
        truck.model_template = template
        truck.tire_count = int(template.wheel_count or 0)
        truck.layout_model = "TEMPLATE"
        truck.save(update_fields=["identifier", "model_template", "tire_count", "layout_model", "updated_at"])
        messages.success(request, "Caminhão atualizado.")
    else:
        truck = Truck.objects.create(
            identifier=identifier,
            model_template=template,
            tire_count=int(template.wheel_count or 0),
            layout_model="TEMPLATE",
        )
        messages.success(request, "Caminhão cadastrado.")

    return redirect("tires_truck", truck_id=truck.id)


@require_POST
def slot_action(request, truck_id):
    truck = get_object_or_404(Truck.objects.select_related("model_template"), pk=truck_id)
    detail_url = reverse("tires_truck", args=[truck.id])

    tire_number = services.parse_positive_int(request.POST.get("tire_number"))
    action_mode = (request.POST.get("action_mode") or "create_and_install").strip()
    changed_on = services.parse_date(request.POST.get("changed_on"))
    odometer_km = services.parse_positive_int(request.POST.get("odometer_km"))
    note = (request.POST.get("note") or "").strip() or None

    rows, spare_slots, positions = services.truck_layout(truck)
    if not tire_number or tire_number not in positions:
        messages.error(request, "Posição inválida para este caminhão.")
        return redirect(detail_url)

    position_label = positions[tire_number]
    current_row = (
        TruckTireChange.objects.select_related("tire").filter(truck=truck, tire_number=tire_number).first()
    )
    current_tire = current_row.tire if current_row else None

    with transaction.atomic():
        if action_mode in {"move_to_stock", "send_current_to_retread", "discard_current"}:
            if not current_row or not current_tire:
                messages.error(request, "Não existe pneu instalado nesta posição.")
                return redirect(detail_url)

            if action_mode == "move_to_stock":
                services.move_to_stock(
                    current_tire,
                    movement_date=changed_on,
                    odometer_km=odometer_km,
                    note=note or "Removido do caminhão para o estoque.",
                    truck=truck,
                    tire_number=tire_number,
                    position_label=position_label,
                )
                current_row.delete()
                messages.success(request, "Pneu enviado para o estoque.")
                return redirect(detail_url)

            if action_mode == "send_current_to_retread":
                if current_tire.recap_count >= services.MAX_RETREADS:
                    messages.error(request, "Este pneu já atingiu o limite de 3 recapes.")
                    return redirect(detail_url)
                services.send_to_retread(
                    current_tire,
                    movement_date=changed_on,
                    odometer_km=odometer_km,
                    note=note or "Removido do caminhão e enviado para recapagem.",
                    truck=truck,
                    tire_number=tire_number,
                    position_label=position_label,
                )
                current_row.delete()
                messages.success(request, "Pneu enviado para recapagem.")
                return redirect(detail_url)

            photo = request.FILES.get("photo")
            photo_error = services.validate_movement_photo(photo)
            if photo_error:
                messages.error(request, photo_error)
                return redirect(detail_url)
            services.discard_tire(
                current_tire,
                movement_date=changed_on,
                odometer_km=odometer_km,
                note=note or "Pneu descartado a partir do caminhão.",
                truck=truck,
                tire_number=tire_number,
                position_label=position_label,
                photo=photo,
            )
            current_row.delete()
            messages.success(request, "Pneu descartado.")
            return redirect(detail_url)

        if action_mode == "install_spare_to_position":
            target_number = services.parse_positive_int(request.POST.get("target_tire_number"))
            spare_numbers = {slot["tire_number"] for slot in spare_slots}
            regular_positions = {
                slot["tire_number"]: slot["position_label"]
                for row in rows
                for slot in row["left_slots"] + row["right_slots"]
            }

            if tire_number not in spare_numbers:
                messages.error(request, "A instalação direta só pode partir de um estepe.")
                return redirect(detail_url)
            if not current_row or not current_tire:
                messages.error(request, "Não existe pneu instalado neste estepe.")
                return redirect(detail_url)
            if not target_number or target_number not in regular_positions:
                messages.error(request, "Selecione uma posição válida para instalar o estepe.")
                return redirect(detail_url)

            spare_note = note or f"Instalado a partir do estepe {position_label}."
            current_row.delete()
            services.install_on_slot(
                truck,
                tire_number=target_number,
                position_label=regular_positions[target_number],
                tire=current_tire,
                changed_on=changed_on,
                odometer_km=odometer_km,
                note=spare_note,
                action_type="install_from_spare",
            )
            messages.success(request, "Estepe instalado na posição selecionada.")
            return redirect(detail_url)

        purchase_value_raw = (request.POST.get("new_tire_purchase_value") or "").strip()
        purchase_value = services.parse_decimal(purchase_value_raw)
        if purchase_value_raw and purchase_value is None:
            messages.error(request, "Informe um valor válido para o novo pneu.")
            return redirect(detail_url)

        # Só os modos que criam um pneu exigem a ficha física; instalar do
        # estoque reaproveita o cadastro que já passou por essa validação.
        specs = None
        if action_mode in {"create_and_install", "initial_load"}:
            specs, specs_error = services.read_tire_specs(request.POST)
            if specs_error:
                messages.error(request, specs_error)
                return redirect(detail_url)

        if action_mode == "initial_load":
            if current_row:
                messages.error(request, "Esta posição já tem um pneu. A carga inicial só vale para posições vazias.")
                return redirect(detail_url)

            retread_total_raw = (request.POST.get("initial_retread_total") or "").strip()
            retread_total = services.parse_decimal(retread_total_raw)
            if retread_total_raw and retread_total is None:
                messages.error(request, "Informe um valor válido para o gasto com recapes.")
                return redirect(detail_url)

            _tire, error = services.initial_load_on_slot(
                truck,
                tire_number=tire_number,
                position_label=position_label,
                serial_number=request.POST.get("new_tire_serial"),
                brand=request.POST.get("new_tire_brand"),
                recap_count=services.parse_positive_int(request.POST.get("initial_recap_count")) or 0,
                purchase_value=purchase_value,
                retread_total=retread_total,
                installed_on=changed_on,
                odometer_km=odometer_km,
                note=note,
                specs=specs,
            )
            if error:
                messages.error(request, error)
                return redirect(detail_url)

            messages.success(request, "Carga inicial registrada. A rodagem passa a contar a partir da data informada.")
            return redirect(detail_url)

        tire, error = services.resolve_tire_for_install(
            action_mode=action_mode,
            stock_tire_id=services.parse_positive_int(request.POST.get("stock_tire_id")),
            new_tire_brand=request.POST.get("new_tire_brand"),
            new_tire_serial=request.POST.get("new_tire_serial"),
            new_tire_purchase_value=purchase_value,
            registered_on=changed_on,
            note=note,
            specs=specs,
        )
        if error:
            messages.error(request, error)
            return redirect(detail_url)

        if tire.status == Tire.STATUS_INSTALLED and (
            tire.current_truck_id != truck.id or tire.current_tire_number != tire_number
        ):
            messages.error(request, "Este pneu já está instalado em outro caminhão.")
            return redirect(detail_url)

        services.install_on_slot(
            truck,
            tire_number=tire_number,
            position_label=position_label,
            tire=tire,
            changed_on=changed_on,
            odometer_km=odometer_km,
            note=note,
        )

    messages.success(request, "Movimentação registrada.")
    return redirect(detail_url)


@require_POST
def slot_swap(request, truck_id):
    truck = get_object_or_404(Truck.objects.select_related("model_template"), pk=truck_id)
    detail_url = reverse("tires_truck", args=[truck.id])

    source = services.parse_positive_int(request.POST.get("source_tire_number"))
    target = services.parse_positive_int(request.POST.get("target_tire_number"))
    changed_on = services.parse_date(request.POST.get("changed_on"))
    odometer_km = services.parse_positive_int(request.POST.get("odometer_km"))
    note = (request.POST.get("note") or "").strip()

    if not source or not target:
        messages.error(request, "Selecione a posição de origem e de destino.")
        return redirect(detail_url)
    if not changed_on:
        messages.error(request, "Informe a data da troca.")
        return redirect(detail_url)
    if odometer_km is None:
        messages.error(request, "Informe a quilometragem da troca.")
        return redirect(detail_url)
    if not note:
        messages.error(request, "Informe o motivo da troca de posição.")
        return redirect(detail_url)

    _rows, _spares, positions = services.truck_layout(truck)
    if source not in positions or target not in positions:
        messages.error(request, "Uma das posições selecionadas não existe neste caminhão.")
        return redirect(detail_url)

    with transaction.atomic():
        error = services.reposition_tire(
            truck,
            source_tire_number=source,
            target_tire_number=target,
            positions=positions,
            changed_on=changed_on,
            odometer_km=odometer_km,
            note=note,
        )
    if error:
        messages.error(request, error)
        return redirect(detail_url)

    messages.success(request, "Pneu reposicionado.")
    return redirect(detail_url)


# --------------------------------------------------------------------------- #
# Estoque
# --------------------------------------------------------------------------- #


def _inventory_url(status=None, recaps=None, search=None):
    """Link de um filtro do estoque preservando os outros já aplicados."""
    params = {}
    if status:
        params["status"] = status
    if recaps is not None:
        params["recapes"] = str(recaps)
    if search:
        params["q"] = search
    base = reverse("tires_inventory")
    return f"{base}?{urlencode(params)}" if params else base


def inventory(request):
    status = (request.GET.get("status") or "").strip().lower()
    if status not in dict(Tire.STATUS_CHOICES):
        status = ""
    search = (request.GET.get("q") or "").strip()
    recap_level = services.parse_recap_level(request.GET.get("recapes"))

    base = Tire.objects.select_related("current_truck").order_by("serial_number", "id")
    if search:
        base = base.filter(Q(serial_number__icontains=search) | Q(brand__icontains=search))

    by_status = base.filter(status=status) if status else base
    by_recaps = services.filter_by_recap_level(base, recap_level)
    queryset = services.filter_by_recap_level(by_status, recap_level)

    tires = list(queryset[:400])
    blockers = services.tire_delete_map(tires)
    for tire in tires:
        tire.delete_blockers = blockers.get(tire.id, [])

    # Cada linha de filtro conta sobre o resultado dos OUTROS filtros, então o
    # número do botão é sempre quantas linhas ele traz se for clicado.
    status_counts = services.count_by_status(by_recaps)
    recap_counts = services.count_by_recap_level(by_status)

    filters = [
        {
            "key": "",
            "label": "Todos",
            "count": sum(status_counts.values()),
            "url": _inventory_url(status=None, recaps=recap_level, search=search),
        }
    ]
    for key, label in [
        (Tire.STATUS_STOCK, "Estoque"),
        (Tire.STATUS_INSTALLED, "Instalados"),
        (Tire.STATUS_RETREADING, "Recapagem"),
        (Tire.STATUS_DISCARDED, "Descartados"),
    ]:
        filters.append(
            {
                "key": key,
                "label": label,
                "count": status_counts.get(key, 0),
                "url": _inventory_url(status=key, recaps=recap_level, search=search),
            }
        )

    recap_filters = [
        {
            "key": "",
            "level": None,
            "label": "Qualquer recape",
            "count": sum(recap_counts.values()),
            "url": _inventory_url(status=status, recaps=None, search=search),
        }
    ]
    for option in services.recap_filter_options():
        recap_filters.append(
            {
                "key": option["key"],
                "level": option["level"],
                "label": option["label"],
                "count": recap_counts.get(option["level"], 0),
                "url": _inventory_url(status=status, recaps=option["level"], search=search),
            }
        )

    return render(
        request,
        "hqbooking/tires/inventory.html",
        _shell(
            "inventory",
            summary=services.inventory_summary(),
            filters=filters,
            recap_filters=recap_filters,
            active_status=status,
            active_recaps="" if recap_level is None else str(recap_level),
            search=search,
            tires=tires,
            status_tones=STATUS_TONES,
            brands=services.known_brands(),
            tire_models=services.known_tire_models(),
            tire_sizes=services.known_sizes(),
        ),
    )


@require_POST
def tire_check_serials(request):
    """Diz quais números do lote já existem, para a prévia avisar antes do envio."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"status": "error", "message": "Payload inválido."}, status=400)

    serials = payload.get("serials")
    if not isinstance(serials, list):
        return JsonResponse({"status": "error", "message": "Envie a lista de números."}, status=400)

    return JsonResponse({"status": "ok", "taken": services.existing_serials(serials[:MAX_SERIAL_CHECK])})


def tire_detail(request, tire_id):
    tire = get_object_or_404(Tire.objects.select_related("current_truck"), pk=tire_id)
    movements = list(
        TireMovement.objects.select_related("truck").filter(tire=tire).order_by("-created_at", "-id")[:60]
    )
    # Cada linha do histórico é gravada quando um pneu ENTRA numa posição, e a
    # rodagem que ela carrega é a de quem SAIU. Então os ciclos deste pneu são
    # as linhas em que ele aparece como anterior, não as em que ele é o `tire`.
    history = list(
        TruckTireChangeHistory.objects.select_related("truck")
        .filter(previous_tire_code__iexact=tire.serial_number)
        .exclude(run_km__isnull=True, run_days__isnull=True)
        .order_by("-created_at", "-id")[:30]
    )
    total_run_km = sum(int(row.run_km) for row in history if row.run_km is not None)
    total_run_days = sum(int(row.run_days) for row in history if row.run_days is not None)
    total_cost = (tire.purchase_value or Decimal("0")) + (tire.total_retread_cost or Decimal("0"))

    return render(
        request,
        "hqbooking/tires/tire_detail.html",
        _shell(
            "inventory",
            tire=tire,
            movements=movements,
            history=history,
            movement_tones=MOVEMENT_TONES,
            status_tones=STATUS_TONES,
            total_run_km=total_run_km,
            total_run_days=total_run_days,
            total_cost=total_cost,
            cost_per_km=(total_cost / total_run_km) if total_run_km else None,
            retread_slots=range(services.MAX_RETREADS),
            delete_blockers=services.tire_delete_blockers(tire),
            brands=services.known_brands(),
            tire_models=services.known_tire_models(),
            tire_sizes=services.known_sizes(),
        ),
    )


@require_POST
def tire_edit(request, tire_id):
    """Corrige os dados cadastrais de um pneu, preservando o histórico."""
    tire = get_object_or_404(Tire, pk=tire_id)
    detail_url = reverse("tires_tire", args=[tire.id])

    purchase_value_raw = (request.POST.get("purchase_value") or "").strip()
    purchase_value = services.parse_decimal(purchase_value_raw)
    if purchase_value_raw and purchase_value is None:
        messages.error(request, "Informe um valor válido para o pneu.")
        return redirect(detail_url)

    specs, specs_error = services.read_tire_specs(request.POST)
    if specs_error:
        messages.error(request, specs_error)
        return redirect(detail_url)

    error = services.update_tire_identity(
        tire,
        serial_number=request.POST.get("serial_number"),
        brand=request.POST.get("brand"),
        purchase_value=purchase_value,
        registered_on=services.parse_date(request.POST.get("registered_on")),
        note=(request.POST.get("note") or "").strip() or None,
        specs=specs,
    )
    if error:
        messages.error(request, error)
        return redirect(detail_url)

    messages.success(request, "Dados do pneu atualizados.")
    return redirect(detail_url)


@require_POST
def tire_create(request):
    redirect_url = reverse("tires_inventory")

    # Resolvida uma vez só: o lote inteiro compartilha a mesma grafia de marca.
    brand = services.normalize_brand(request.POST.get("brand"))
    if not brand:
        messages.error(request, "Informe a marca para cadastrar o pneu ou o lote.")
        return redirect(redirect_url)

    purchase_value_raw = (request.POST.get("purchase_value") or "").strip()
    purchase_value = services.parse_decimal(purchase_value_raw)
    if purchase_value_raw and purchase_value is None:
        messages.error(request, "Informe um valor válido para o pneu.")
        return redirect(redirect_url)

    retread_total_raw = (request.POST.get("retread_total") or "").strip()
    retread_total = services.parse_decimal(retread_total_raw)
    if retread_total_raw and retread_total is None:
        messages.error(request, "Informe um valor válido para o gasto com recapes.")
        return redirect(redirect_url)

    specs, specs_error = services.read_tire_specs(request.POST)
    if specs_error:
        messages.error(request, specs_error)
        return redirect(redirect_url)

    # Pneus usados entram com o histórico que já têm, não zerados.
    recap_count = services.parse_positive_int(request.POST.get("recap_count")) or 0
    registered_on = services.parse_date(request.POST.get("registered_on"))
    note = (request.POST.get("note") or "").strip() or None
    serial_number = (request.POST.get("serial_number") or "").strip()
    serial_batch = (request.POST.get("serial_batch") or "").strip()
    batch_mode = (request.POST.get("batch_mode") or "").strip().lower()

    if batch_mode not in {"single", "paste", "generate"}:
        if serial_batch:
            batch_mode = "paste"
        elif request.POST.get("batch_prefix"):
            batch_mode = "generate"
        else:
            batch_mode = "single"

    serials = []
    repeated = []
    if batch_mode == "generate":
        serials, error = services.build_generated_serials(
            request.POST.get("batch_prefix"),
            services.parse_positive_int(request.POST.get("batch_start_number")),
            services.parse_positive_int(request.POST.get("batch_quantity")),
            pad_length=services.parse_positive_int(request.POST.get("batch_pad_length")) or 0,
        )
        if error:
            messages.error(request, error)
            return redirect(redirect_url)
    elif batch_mode == "paste":
        serials, repeated = services.parse_batch_serials(serial_batch)
    elif serial_number:
        serials = [serial_number]

    if not serials:
        messages.error(request, "Informe ao menos um número de pneu.")
        return redirect(redirect_url)

    created = []
    skipped = list(repeated)
    for serial in serials:
        if Tire.objects.filter(serial_number__iexact=serial).exists():
            skipped.append(serial)
            continue
        services.register_tire(
            serial_number=serial,
            brand=brand,
            registered_on=registered_on,
            purchase_value=purchase_value,
            note=note,
            recap_count=recap_count,
            retread_total=retread_total,
            specs=specs,
        )
        created.append(serial)

    if created:
        messages.success(
            request,
            f"{len(created)} pneu(s) cadastrado(s) e enviado(s) ao estoque."
            if len(created) > 1
            else f"Pneu {created[0]} cadastrado e enviado ao estoque.",
        )
    if skipped:
        preview = ", ".join(skipped[:8]) + ("..." if len(skipped) > 8 else "")
        messages.warning(request, f"Ignorados por já existirem ou estarem repetidos: {preview}")
    if not created and not skipped:
        messages.error(request, "Nenhum pneu foi cadastrado.")

    return redirect(redirect_url)


@require_POST
def tire_action(request):
    redirect_url = request.POST.get("next") or reverse("tires_inventory")

    tire_id = services.parse_positive_int(request.POST.get("tire_id"))
    tire = Tire.objects.select_related("current_truck").filter(pk=tire_id).first() if tire_id else None
    if not tire:
        messages.error(request, "Pneu não encontrado.")
        return redirect(reverse("tires_inventory"))

    action = (request.POST.get("action") or "").strip()
    note = (request.POST.get("note") or "").strip() or None
    movement_date = services.parse_date(request.POST.get("movement_date"))
    cost_raw = (request.POST.get("cost") or "").strip()
    cost = services.parse_decimal(cost_raw)
    if cost_raw and cost is None:
        messages.error(request, "Informe um valor válido para o recape.")
        return redirect(redirect_url)

    if action == "send_to_retread":
        if tire.status != Tire.STATUS_STOCK:
            messages.error(request, "Somente pneus em estoque podem ser enviados para recapagem.")
        elif tire.recap_count >= services.MAX_RETREADS:
            messages.error(request, "Este pneu já atingiu o limite de 3 recapes.")
        else:
            services.send_to_retread(tire, movement_date=movement_date, note=note)
            messages.success(request, "Pneu enviado para recapagem.")
        return redirect(redirect_url)

    if action == "return_from_retread":
        if tire.status != Tire.STATUS_RETREADING:
            messages.error(request, "Apenas pneus em recapagem podem retornar ao estoque.")
        elif tire.recap_count >= services.MAX_RETREADS:
            messages.error(request, "Este pneu já atingiu o limite de 3 recapes.")
        else:
            services.return_from_retread(tire, movement_date=movement_date, movement_cost=cost, note=note)
            messages.success(request, "Pneu retornou da recapagem para o estoque.")
        return redirect(redirect_url)

    if action == "retread":
        if tire.status != Tire.STATUS_STOCK:
            messages.error(request, "Somente pneus em estoque podem receber recape.")
        elif tire.recap_count >= services.MAX_RETREADS:
            messages.error(request, "Este pneu já atingiu o limite de 3 recapes.")
        else:
            tire.recap_count += 1
            tire.save(update_fields=["recap_count", "updated_at"])
            services.log_movement(
                tire,
                TireMovement.TYPE_RETREAD,
                movement_date=movement_date,
                note=note or f"Recape {tire.recap_count}/{services.MAX_RETREADS}",
            )
            messages.success(request, "Recape registrado.")
        return redirect(redirect_url)

    if action == "discard":
        if tire.status == Tire.STATUS_INSTALLED:
            messages.error(request, "Remova o pneu do caminhão antes de descartar.")
        elif tire.status == Tire.STATUS_DISCARDED:
            messages.info(request, "Este pneu já está descartado.")
        else:
            # A foto e a observação ficam no próprio movimento de descarte: é
            # ali que se comprova depois o motivo da baixa.
            photo = request.FILES.get("photo")
            photo_error = services.validate_movement_photo(photo)
            if photo_error:
                messages.error(request, photo_error)
                return redirect(redirect_url)
            services.discard_tire(tire, movement_date=movement_date, note=note, photo=photo)
            messages.success(
                request,
                "Pneu descartado com foto anexada." if photo else "Pneu descartado.",
            )
        return redirect(redirect_url)

    if action == "delete_permanently":
        # Exclusão só vale para cadastro sem vínculo: se o pneu já rodou, o
        # caminho é descartar, que preserva a história dele.
        blockers = services.tire_delete_blockers(tire)
        if blockers:
            messages.error(
                request,
                f"Não é possível excluir o pneu {tire.serial_number}: {'; '.join(blockers)}.",
            )
            return redirect(redirect_url)

        label = tire.serial_number
        tire.delete()
        messages.success(request, f"Pneu {label} excluído do cadastro.")
        return redirect(reverse("tires_inventory"))

    messages.error(request, "Ação de estoque inválida.")
    return redirect(redirect_url)


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #


def model_list(request):
    truck_counts = services.trucks_per_model()

    cards = []
    structures = {}
    for template in services.model_options():
        structure = services.load_structure(template.structure_json)
        structures[str(template.id)] = {"name": template.name, "structure": structure}
        cards.append(
            {
                "model": template,
                "summary": services.summarize_structure(structure),
                "truck_count": truck_counts.get(template.id, 0),
                "delete_blockers": services.model_delete_blockers(
                    template, truck_counts.get(template.id, 0)
                ),
            }
        )

    # Abre o editor já carregado quando a URL aponta para um modelo específico.
    open_model = services.parse_positive_int(request.GET.get("model"))
    if open_model and str(open_model) not in structures:
        open_model = None
    open_new = (request.GET.get("novo") or "").strip() in {"1", "true", "on"}

    return render(
        request,
        "hqbooking/tires/models.html",
        _shell(
            "models",
            cards=cards,
            structures=structures,
            default_structure=services.normalize_structure([]),
            open_model=open_model,
            open_new=open_new,
        ),
    )


@require_POST
def model_save(request):
    model_id = services.parse_positive_int(request.POST.get("model_id"))
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Informe um nome para o modelo.")
        return redirect("tires_models")

    structure = services.load_structure(request.POST.get("structure_json"))
    summary = services.summarize_structure(structure)
    payload = json.dumps(structure, ensure_ascii=False)

    template = TruckModelTemplate.objects.filter(pk=model_id).first() if model_id else None
    if template:
        template.name = name
        template.axle_count = summary["axle_count"]
        template.wheel_count = summary["wheel_count"]
        template.structure_json = payload
        template.save(update_fields=["name", "axle_count", "wheel_count", "structure_json", "updated_at"])
        messages.success(request, "Modelo atualizado.")
    else:
        template = TruckModelTemplate.objects.create(
            name=name,
            axle_count=summary["axle_count"],
            wheel_count=summary["wheel_count"],
            structure_json=payload,
        )
        messages.success(request, "Modelo criado.")

    return redirect("tires_models")


@require_POST
def model_delete(request):
    model_id = services.parse_positive_int(request.POST.get("model_id"))
    template = TruckModelTemplate.objects.filter(pk=model_id).first() if model_id else None
    if not template:
        messages.error(request, "Modelo não encontrado.")
        return redirect("tires_models")

    blockers = services.model_delete_blockers(template)
    if blockers:
        messages.error(
            request,
            f"Não é possível excluir o modelo “{template.name}”: {'; '.join(blockers)}. "
            "Troque o modelo desses caminhões antes de excluir.",
        )
        return redirect("tires_models")

    name = template.name
    template.delete()
    messages.success(request, f"Modelo “{name}” excluído.")
    return redirect("tires_models")


# --------------------------------------------------------------------------- #
# Movimentações
# --------------------------------------------------------------------------- #


def movements(request):
    truck_id = services.parse_positive_int(request.GET.get("truck"))
    movement_type = (request.GET.get("type") or "").strip()
    search = (request.GET.get("q") or "").strip()
    date_from = services.parse_date(request.GET.get("de"))
    date_to = services.parse_date(request.GET.get("ate"))

    queryset = TireMovement.objects.select_related("tire", "truck").order_by("-created_at", "-id")
    selected_truck = Truck.objects.filter(pk=truck_id).first() if truck_id else None
    if selected_truck:
        queryset = queryset.filter(truck=selected_truck)
    if movement_type in dict(TireMovement.TYPE_CHOICES):
        queryset = queryset.filter(movement_type=movement_type)
    if search:
        queryset = queryset.filter(
            Q(tire__serial_number__icontains=search)
            | Q(tire__brand__icontains=search)
            | Q(position_label__icontains=search)
            | Q(note__icontains=search)
            | Q(truck__identifier__icontains=search)
        )
    if date_from:
        queryset = queryset.filter(
            Q(movement_date__gte=date_from) | Q(movement_date__isnull=True, created_at__date__gte=date_from)
        )
    if date_to:
        queryset = queryset.filter(
            Q(movement_date__lte=date_to) | Q(movement_date__isnull=True, created_at__date__lte=date_to)
        )

    rows = list(queryset[:600])
    grouped = []
    for row in rows:
        day = _movement_day(row)
        if not grouped or grouped[-1]["day"] != day:
            grouped.append({"day": day, "items": []})
        grouped[-1]["items"].append(row)

    return render(
        request,
        "hqbooking/tires/movements.html",
        _shell(
            "movements",
            grouped=grouped,
            total=len(rows),
            trucks=list(Truck.objects.order_by("identifier")),
            selected_truck=selected_truck,
            movement_type=movement_type,
            movement_type_choices=TireMovement.TYPE_CHOICES,
            movement_tones=MOVEMENT_TONES,
            search=search,
            date_from=date_from.isoformat() if date_from else "",
            date_to=date_to.isoformat() if date_to else "",
        ),
    )
