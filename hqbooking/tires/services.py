"""Regras de domínio da logística de pneus.

As views desta pasta cuidam apenas de requisição/resposta; toda a manipulação de
estado dos pneus (instalar, remover, recapar, descartar, reposicionar) vive aqui
para poder ser testada e reutilizada entre as páginas do módulo.
"""

import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from ..models import (
    Tire,
    TireMovement,
    Truck,
    TruckModelTemplate,
    TruckTireChange,
    TruckTireChangeHistory,
)


MAX_RETREADS = 3
MAX_BATCH_SIZE = 500
HEAT_MAX_DAYS = 180


# --------------------------------------------------------------------------- #
# Parsing de entrada
# --------------------------------------------------------------------------- #


def parse_date(raw_value):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        return None


def parse_positive_int(raw_value):
    raw_value = str(raw_value or "").strip()
    if not raw_value.isdigit():
        return None
    return int(raw_value)


def parse_decimal(raw_value):
    raw_value = str(raw_value or "").strip().replace("R$", "").replace(" ", "")
    if not raw_value:
        return None
    if raw_value.count(",") == 1 and raw_value.count(".") >= 1:
        raw_value = raw_value.replace(".", "").replace(",", ".")
    else:
        raw_value = raw_value.replace(",", ".")
    try:
        return Decimal(raw_value)
    except (InvalidOperation, ValueError):
        return None


def parse_batch_serials(raw_value):
    """Divide uma lista colada em números únicos, devolvendo também os repetidos."""
    unique = []
    repeated = []
    seen = set()
    raw_text = str(raw_value or "").replace("\r", "\n")
    for chunk in raw_text.replace(";", "\n").replace(",", "\n").split("\n"):
        serial = chunk.strip()
        if not serial:
            continue
        key = serial.lower()
        if key in seen:
            repeated.append(serial)
            continue
        seen.add(key)
        unique.append(serial)
    return unique, repeated


def build_generated_serials(prefix, start_number, quantity, pad_length=0):
    normalized_prefix = str(prefix or "").strip()
    if not normalized_prefix:
        return [], "Informe um prefixo para gerar os números do lote."
    if start_number is None:
        return [], "Informe o número inicial da sequência."
    if quantity is None or quantity <= 0:
        return [], "Informe uma quantidade válida para gerar o lote."
    if quantity > MAX_BATCH_SIZE:
        return [], f"O cadastro sequencial aceita no máximo {MAX_BATCH_SIZE} pneus por vez."

    pad = max(0, min(int(pad_length or 0), 8))
    serials = []
    for offset in range(quantity):
        value = str(start_number + offset)
        serials.append(f"{normalized_prefix}{value.zfill(pad) if pad else value}")
    return serials, None


# --------------------------------------------------------------------------- #
# Layout do caminhão
# --------------------------------------------------------------------------- #


def _normalize_wheels(items, default_prefix):
    normalized = []
    for index, item in enumerate(items or [], start=1):
        raw_name = item.get("name") if isinstance(item, dict) else item
        name = str(raw_name or "").strip() or f"{default_prefix} {index}"
        normalized.append({"name": name})
    return normalized


def normalize_structure(raw_structure):
    """Garante a forma canônica: lista de eixos com left/right e estepes no 1º eixo."""
    structure = []
    collected_spares = []

    for axle in raw_structure or []:
        if not isinstance(axle, dict):
            continue

        spares = _normalize_wheels(axle.get("spares"), "Estepe")
        legacy_spare = axle.get("spare")
        if legacy_spare:
            spares.extend(_normalize_wheels([legacy_spare], "Estepe"))
        collected_spares.extend(spares)

        structure.append(
            {
                "left": _normalize_wheels(axle.get("left"), "Esquerda"),
                "right": _normalize_wheels(axle.get("right"), "Direita"),
                "spares": [],
            }
        )

    if not structure:
        structure = [{"left": [{"name": "DE"}], "right": [{"name": "DD"}], "spares": []}]

    structure[0]["spares"] = collected_spares
    return structure


def load_structure(raw_json):
    try:
        parsed = json.loads(raw_json or "[]")
    except (TypeError, ValueError):
        parsed = []
    return normalize_structure(parsed)


def structure_to_rows(structure):
    """Converte a estrutura em linhas de eixo numeradas + lista de estepes."""
    rows = []
    spare_slots = []
    tire_no = 1

    normalized = normalize_structure(structure)
    for axle_index, axle in enumerate(normalized, start=1):
        left_slots = []
        right_slots = []

        for wheel in axle.get("left", []):
            label = (wheel.get("name") or f"E{tire_no}").strip()
            left_slots.append(
                {"tire_number": tire_no, "position_label": label, "tire_code": label, "is_spare": False}
            )
            tire_no += 1

        for wheel in axle.get("right", []):
            label = (wheel.get("name") or f"D{tire_no}").strip()
            right_slots.append(
                {"tire_number": tire_no, "position_label": label, "tire_code": label, "is_spare": False}
            )
            tire_no += 1

        rows.append({"axle_index": axle_index, "left_slots": left_slots, "right_slots": right_slots})

    for spare_index, spare in enumerate(normalized[0].get("spares", []), start=1):
        label = (spare.get("name") or f"Estepe {spare_index}").strip()
        spare_slots.append(
            {"tire_number": tire_no, "position_label": label, "tire_code": label, "is_spare": True}
        )
        tire_no += 1

    return rows, spare_slots, tire_no - 1


def position_lookup(rows, spare_slots):
    lookup = {}
    for row in rows:
        for slot in row.get("left_slots", []) + row.get("right_slots", []):
            lookup[slot["tire_number"]] = slot["position_label"]
    for slot in spare_slots or []:
        lookup[slot["tire_number"]] = slot["position_label"]
    return lookup


def truck_layout(truck):
    """Linhas, estepes e mapa de posições de um caminhão a partir do seu modelo."""
    structure = load_structure(truck.model_template.structure_json if truck.model_template else "[]")
    rows, spare_slots, _total = structure_to_rows(structure)
    return rows, spare_slots, position_lookup(rows, spare_slots)


def summarize_structure(structure):
    normalized = normalize_structure(structure)
    wheel_count = sum(len(axle.get("left", [])) + len(axle.get("right", [])) for axle in normalized)
    spare_count = len(normalized[0].get("spares", []))
    return {
        "axle_count": len(normalized),
        "wheel_count": wheel_count + spare_count,
        "spare_count": spare_count,
    }


# --------------------------------------------------------------------------- #
# Registro de movimentos
# --------------------------------------------------------------------------- #


def log_movement(
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


def run_metrics(previous_changed_on, changed_on, previous_odometer_km, odometer_km):
    run_days = None
    run_km = None
    if previous_changed_on and changed_on and changed_on >= previous_changed_on:
        run_days = (changed_on - previous_changed_on).days
    if previous_odometer_km is not None and odometer_km is not None and odometer_km >= previous_odometer_km:
        run_km = odometer_km - previous_odometer_km
    return run_days, run_km


def record_history(
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


def _write_assignment(row, tire, changed_on=None, odometer_km=None, note=None):
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


def _detach_tire(tire, status):
    tire.status = status
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


# --------------------------------------------------------------------------- #
# Operações sobre pneus
# --------------------------------------------------------------------------- #


def known_brands():
    """Marcas já cadastradas, uma entrada por grafia canônica."""
    seen = {}
    for brand in Tire.objects.values_list("brand", flat=True):
        cleaned = " ".join(str(brand or "").split())
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen[key] = cleaned
    return sorted(seen.values(), key=str.lower)


def normalize_brand(raw_value):
    """Reaproveita a grafia já cadastrada quando a marca só difere no caixa.

    Evita que "Goodyear", "goodyear" e "GOODYEAR" virem três marcas distintas
    nos gráficos e na busca.
    """
    brand = " ".join(str(raw_value or "").split())
    if not brand:
        return brand
    existing = Tire.objects.filter(brand__iexact=brand).values_list("brand", flat=True).first()
    return existing or brand


def existing_serials(serials):
    """Quais dos números informados já existem no cadastro (ignorando caixa)."""
    wanted = {}
    for serial in serials or []:
        cleaned = str(serial or "").strip()
        if cleaned:
            wanted.setdefault(cleaned.lower(), cleaned)
    if not wanted:
        return []

    taken = set()
    for stored in Tire.objects.values_list("serial_number", flat=True).iterator(chunk_size=1000):
        key = str(stored or "").strip().lower()
        if key in wanted:
            taken.add(wanted[key])
    return sorted(taken)


def update_tire_identity(tire, serial_number, brand, purchase_value=None, registered_on=None, note=None):
    """Corrige os dados cadastrais de um pneu sem tocar no histórico.

    Devolve uma mensagem de erro ou None. A posição ativa guarda uma cópia do
    número e da marca para o mapa do caminhão, então ela é sincronizada aqui.
    """
    serial = str(serial_number or "").strip()
    if not serial:
        return "Informe o número do pneu."

    brand = normalize_brand(brand)
    if not brand:
        return "Informe a marca do pneu."

    clash = Tire.objects.filter(serial_number__iexact=serial).exclude(pk=tire.pk).exists()
    if clash:
        return "Já existe outro pneu cadastrado com este número."

    previous_serial = tire.serial_number
    tire.serial_number = serial
    tire.brand = brand
    tire.purchase_value = purchase_value
    tire.registered_on = registered_on or tire.registered_on
    tire.notes = note
    tire.save(
        update_fields=["serial_number", "brand", "purchase_value", "registered_on", "notes", "updated_at"]
    )

    TruckTireChange.objects.filter(tire=tire).update(tire_code=serial, tire_brand=brand)

    if previous_serial and previous_serial != serial:
        # `previous_tire_code` identifica o pneu (é por ele que a ficha acha os
        # ciclos dele), então acompanha a correção. Os demais campos do
        # histórico continuam sendo a foto da época.
        TruckTireChangeHistory.objects.filter(previous_tire_code__iexact=previous_serial).update(
            previous_tire_code=serial
        )
    return None


def register_tire(
    serial_number,
    brand,
    registered_on=None,
    purchase_value=None,
    note=None,
    recap_count=0,
    retread_total=None,
):
    """Cadastra um pneu no estoque.

    `recap_count` e `retread_total` existem para pneus que já vinham rodando
    antes do sistema: um pneu usado não começa a vida com zero recapes.
    """
    recap_count = max(0, min(int(recap_count or 0), MAX_RETREADS))
    tire = Tire.objects.create(
        serial_number=serial_number,
        brand=normalize_brand(brand),
        status=Tire.STATUS_STOCK,
        purchase_value=purchase_value,
        recap_count=recap_count,
        total_retread_cost=retread_total or Decimal("0"),
        registered_on=registered_on or timezone.localdate(),
        notes=note,
    )
    log_movement(tire, TireMovement.TYPE_REGISTER, movement_date=registered_on, note=note)
    return tire


def initial_load_on_slot(
    truck,
    tire_number,
    position_label,
    serial_number,
    brand,
    recap_count=0,
    purchase_value=None,
    retread_total=None,
    installed_on=None,
    odometer_km=None,
    note=None,
):
    """Carga inicial: pneu que já estava rodando no veículo antes do sistema.

    Diferente de "cadastrar e instalar", aqui nada é inventado — a data e o
    hodômetro informados são os de quando o pneu realmente foi para a posição,
    e os recapes já realizados entram no contador. É isso que faz a primeira
    troca calcular a rodagem certa em vez de contar a partir de hoje.

    Devolve (pneu, erro).
    """
    serial = str(serial_number or "").strip()
    brand = normalize_brand(brand)
    if not serial:
        return None, "Informe o número do pneu."
    if not brand:
        return None, "Informe a marca do pneu."
    if not installed_on:
        return None, "Informe desde quando o pneu está nesta posição."

    if Tire.objects.filter(serial_number__iexact=serial).exists():
        return None, "Este número já está cadastrado. Use 'Instalar pneu do estoque'."

    try:
        recap_count = max(0, min(int(recap_count or 0), MAX_RETREADS))
    except (TypeError, ValueError):
        recap_count = 0

    baseline = note or "Carga inicial: pneu já estava no veículo."
    tire = register_tire(
        serial_number=serial,
        brand=brand,
        registered_on=installed_on,
        purchase_value=purchase_value,
        note=baseline,
        recap_count=recap_count,
        retread_total=retread_total,
    )
    install_on_slot(
        truck,
        tire_number=tire_number,
        position_label=position_label,
        tire=tire,
        changed_on=installed_on,
        odometer_km=odometer_km,
        note=baseline,
        action_type="initial_load",
    )
    return tire, None


def move_to_stock(tire, movement_date=None, odometer_km=None, note=None, truck=None, tire_number=None, position_label=None):
    _detach_tire(tire, Tire.STATUS_STOCK)
    log_movement(
        tire,
        TireMovement.TYPE_TO_STOCK,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        movement_date=movement_date,
        odometer_km=odometer_km,
        note=note,
    )


def send_to_retread(tire, movement_date=None, odometer_km=None, note=None, truck=None, tire_number=None, position_label=None):
    _detach_tire(tire, Tire.STATUS_RETREADING)
    log_movement(
        tire,
        TireMovement.TYPE_TO_RETREAD,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        movement_date=movement_date,
        odometer_km=odometer_km,
        note=note or "Enviado para recapagem.",
    )


def return_from_retread(tire, movement_date=None, odometer_km=None, movement_cost=None, note=None):
    tire.status = Tire.STATUS_STOCK
    tire.current_truck = None
    tire.current_tire_number = None
    tire.current_slot_label = None
    tire.discarded_on = None
    tire.recap_count = min(int(tire.recap_count or 0) + 1, MAX_RETREADS)
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
    log_movement(
        tire,
        TireMovement.TYPE_FROM_RETREAD,
        movement_date=movement_date,
        odometer_km=odometer_km,
        movement_cost=movement_cost,
        note=note or f"Retorno da recapagem {tire.recap_count}/{MAX_RETREADS}.",
    )


def discard_tire(tire, movement_date=None, odometer_km=None, note=None, truck=None, tire_number=None, position_label=None):
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
    log_movement(
        tire,
        TireMovement.TYPE_DISCARD,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        movement_date=movement_date,
        odometer_km=odometer_km,
        note=note,
    )


def assign_to_slot(
    tire,
    truck,
    tire_number,
    position_label,
    movement_type=TireMovement.TYPE_INSTALL,
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
    log_movement(
        tire,
        movement_type or TireMovement.TYPE_INSTALL,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        movement_date=changed_on,
        odometer_km=odometer_km,
        note=note,
    )


def resolve_tire_for_install(
    action_mode,
    stock_tire_id,
    new_tire_brand,
    new_tire_serial,
    new_tire_purchase_value=None,
    registered_on=None,
    note=None,
):
    """Devolve (pneu, erro) para instalação vinda do estoque ou de cadastro novo."""
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
        return None, "Informe a marca e o número do pneu."

    tire = Tire.objects.filter(serial_number__iexact=serial).first()
    if tire is None:
        return register_tire(
            serial_number=serial,
            brand=brand,
            registered_on=registered_on,
            purchase_value=new_tire_purchase_value,
            note=note,
        ), None

    if tire.status == Tire.STATUS_DISCARDED:
        return None, "Este pneu está descartado e não pode ser reutilizado."
    if tire.status == Tire.STATUS_RETREADING:
        return None, "Este pneu está em recapagem e ainda não retornou ao estoque."
    if tire.status == Tire.STATUS_INSTALLED:
        return None, "Este pneu já está instalado em outro caminhão."
    return tire, None


def _reposition_note(base_note, from_position, to_position):
    route = f"{from_position} -> {to_position}"
    note = (base_note or "").strip()
    return f"{note} ({route})" if note else f"Reposicionado manualmente ({route})."


def reposition_tire(truck, source_tire_number, target_tire_number, positions, changed_on=None, odometer_km=None, note=None):
    """Troca dois pneus de posição no mesmo caminhão. Devolve mensagem de erro ou None."""
    if source_tire_number == target_tire_number:
        return "Selecione uma posição diferente para reposicionar o pneu."

    source_position = positions.get(source_tire_number, f"Posição {source_tire_number}")
    target_position = positions.get(target_tire_number, f"Posição {target_tire_number}")

    source_row = (
        TruckTireChange.objects.select_related("tire").filter(truck=truck, tire_number=source_tire_number).first()
    )
    if not source_row or not source_row.tire:
        return "Não existe pneu instalado na posição de origem."

    target_row = (
        TruckTireChange.objects.select_related("tire").filter(truck=truck, tire_number=target_tire_number).first()
    )
    source_tire = source_row.tire
    target_tire = target_row.tire if target_row else None

    source_note = _reposition_note(note, source_position, target_position)
    target_note = _reposition_note(note, target_position, source_position) if target_tire else None

    source_previous = (
        source_row.tire_code,
        source_row.tire_brand,
        source_row.changed_on,
        source_row.odometer_km,
    )
    target_previous = (
        target_row.tire_code if target_row else None,
        target_row.tire_brand if target_row else None,
        target_row.changed_on if target_row else None,
        target_row.odometer_km if target_row else None,
    )
    source_run_days, source_run_km = run_metrics(source_previous[2], changed_on, source_previous[3], odometer_km)
    target_run_days, target_run_km = run_metrics(target_previous[2], changed_on, target_previous[3], odometer_km)

    if target_tire:
        _write_assignment(source_row, target_tire, changed_on=changed_on, odometer_km=odometer_km, note=target_note)
    else:
        source_row.delete()

    target_row, _created = TruckTireChange.objects.get_or_create(truck=truck, tire_number=target_tire_number)
    _write_assignment(target_row, source_tire, changed_on=changed_on, odometer_km=odometer_km, note=source_note)

    assign_to_slot(
        source_tire,
        truck=truck,
        tire_number=target_tire_number,
        position_label=target_position,
        movement_type=TireMovement.TYPE_REPOSITION,
        changed_on=changed_on,
        odometer_km=odometer_km,
        note=source_note,
    )
    record_history(
        truck=truck,
        tire_number=target_tire_number,
        tire=source_tire,
        changed_on=changed_on,
        odometer_km=odometer_km,
        previous_tire_code=target_previous[0],
        previous_tire_brand=target_previous[1],
        previous_changed_on=target_previous[2],
        previous_odometer_km=target_previous[3],
        run_days=target_run_days,
        run_km=target_run_km,
        action_type="swap",
        note=source_note,
    )

    if target_tire:
        assign_to_slot(
            target_tire,
            truck=truck,
            tire_number=source_tire_number,
            position_label=source_position,
            movement_type=TireMovement.TYPE_REPOSITION,
            changed_on=changed_on,
            odometer_km=odometer_km,
            note=target_note,
        )
        record_history(
            truck=truck,
            tire_number=source_tire_number,
            tire=target_tire,
            changed_on=changed_on,
            odometer_km=odometer_km,
            previous_tire_code=source_previous[0],
            previous_tire_brand=source_previous[1],
            previous_changed_on=source_previous[2],
            previous_odometer_km=source_previous[3],
            run_days=source_run_days,
            run_km=source_run_km,
            action_type="swap",
            note=target_note,
        )

    return None


def install_on_slot(truck, tire_number, position_label, tire, changed_on=None, odometer_km=None, note=None, action_type="install"):
    """Instala um pneu numa posição, devolvendo ao estoque quem estava lá."""
    current_row = (
        TruckTireChange.objects.select_related("tire").filter(truck=truck, tire_number=tire_number).first()
    )
    current_tire = current_row.tire if current_row else None
    previous_code = current_row.tire_code if current_row else None
    previous_brand = current_row.tire_brand if current_row else None
    previous_changed_on = current_row.changed_on if current_row else None
    previous_odometer_km = current_row.odometer_km if current_row else None
    run_days, run_km = run_metrics(previous_changed_on, changed_on, previous_odometer_km, odometer_km)

    if current_tire and current_tire.id != tire.id:
        move_to_stock(
            current_tire,
            movement_date=changed_on,
            odometer_km=odometer_km,
            note=note or "Movido automaticamente para estoque durante a troca.",
            truck=truck,
            tire_number=tire_number,
            position_label=position_label,
        )

    row, _created = TruckTireChange.objects.get_or_create(truck=truck, tire_number=tire_number)
    _write_assignment(row, tire, changed_on=changed_on, odometer_km=odometer_km, note=note)

    assign_to_slot(
        tire,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label,
        changed_on=changed_on,
        odometer_km=odometer_km,
        note=note,
    )
    record_history(
        truck=truck,
        tire_number=tire_number,
        tire=tire,
        changed_on=changed_on,
        odometer_km=odometer_km,
        previous_tire_code=previous_code,
        previous_tire_brand=previous_brand,
        previous_changed_on=previous_changed_on,
        previous_odometer_km=previous_odometer_km,
        run_days=run_days,
        run_km=run_km,
        action_type=action_type,
        note=note,
    )


# --------------------------------------------------------------------------- #
# Leitura para as telas
# --------------------------------------------------------------------------- #


def inventory_summary():
    rows = list(Tire.objects.only("status", "purchase_value", "total_retread_cost", "recap_count"))
    summary = {
        "total": len(rows),
        "stock": 0,
        "installed": 0,
        "retreading": 0,
        "discarded": 0,
        "recappable": 0,
        "at_limit": 0,
        "retreaded": 0,
        "purchase_total": Decimal("0"),
        "retread_total": Decimal("0"),
    }
    for row in rows:
        summary[row.status] = summary.get(row.status, 0) + 1
        summary["purchase_total"] += row.purchase_value or Decimal("0")
        summary["retread_total"] += row.total_retread_cost or Decimal("0")
        recap_count = int(row.recap_count or 0)
        if recap_count:
            summary["retreaded"] += 1
        if row.status != Tire.STATUS_DISCARDED:
            if recap_count < MAX_RETREADS:
                summary["recappable"] += 1
            else:
                summary["at_limit"] += 1
    summary["tracked_total"] = summary["purchase_total"] + summary["retread_total"]
    summary["operational"] = summary["stock"] + summary["installed"] + summary["retreading"]
    return summary


def enrich_slots(truck, rows, spare_slots, today=None):
    """Anexa a cada posição o pneu instalado, idade e última rodagem registrada."""
    today = today or timezone.localdate()
    assignments = {
        row.tire_number: row
        for row in TruckTireChange.objects.select_related("tire").filter(truck=truck)
    }

    last_metrics = {}
    history_qs = TruckTireChangeHistory.objects.filter(truck=truck).order_by("-created_at", "-id")
    for item in history_qs.iterator(chunk_size=500):
        if item.tire_number in last_metrics:
            continue
        if item.run_km is None and item.run_days is None:
            continue
        last_metrics[item.tire_number] = item

    def enrich(slot):
        change = assignments.get(slot["tire_number"])
        slot["change"] = change
        slot["last_metrics"] = last_metrics.get(slot["tire_number"])
        slot["tire_id"] = change.tire_id if change and change.tire_id else ""
        slot["display_code"] = change.tire_code if change and change.tire_code else slot["tire_code"]
        slot["display_brand"] = change.tire_brand if change and change.tire_brand else "—"
        slot["display_status"] = change.tire.get_status_display() if change and change.tire_id else "Vazia"
        slot["display_recap_count"] = change.tire.recap_count if change and change.tire_id else 0
        slot["is_filled"] = bool(change and change.tire_id)

        age_days = None
        heat = None
        if change and change.changed_on:
            age_days = max((today - change.changed_on).days, 0)
            heat = min(age_days / HEAT_MAX_DAYS, 1.0)
        slot["tire_age_days"] = age_days
        slot["tire_heat_css"] = f"{heat:.4f}" if heat is not None else None
        return slot

    for row in rows:
        row["left_slots"] = [enrich(slot) for slot in row["left_slots"]]
        row["right_slots"] = [enrich(slot) for slot in row["right_slots"]]
    return rows, [enrich(slot) for slot in spare_slots]


def fleet_overview(search=None, model_id=None):
    """Uma linha por caminhão com ocupação e última movimentação.

    `search` filtra pela placa/identificação e `model_id` pelo modelo base.
    """
    queryset = Truck.objects.select_related("model_template").order_by("identifier")
    if search:
        queryset = queryset.filter(identifier__icontains=search)
    if model_id:
        queryset = queryset.filter(model_template_id=model_id)
    trucks = list(queryset)
    installed_counts = {}
    for tire in Tire.objects.filter(status=Tire.STATUS_INSTALLED).only("current_truck_id"):
        if tire.current_truck_id:
            installed_counts[tire.current_truck_id] = installed_counts.get(tire.current_truck_id, 0) + 1

    last_movements = {}
    for movement in TireMovement.objects.filter(truck__isnull=False).order_by("-created_at", "-id").only(
        "truck_id", "movement_type", "movement_date", "created_at"
    )[:400]:
        last_movements.setdefault(movement.truck_id, movement)

    overview = []
    for truck in trucks:
        capacity = int(truck.tire_count or 0)
        installed = int(installed_counts.get(truck.id, 0) or 0)
        overview.append(
            {
                "truck": truck,
                "model_name": truck.model_template.name if truck.model_template else "Sem modelo",
                "capacity": capacity,
                "installed": installed,
                "open_slots": max(capacity - installed, 0),
                "occupancy_pct": int(round((installed / capacity) * 100)) if capacity else 0,
                "last_movement": last_movements.get(truck.id),
            }
        )
    return overview


def model_options():
    return list(TruckModelTemplate.objects.order_by("name"))


def trucks_per_model():
    """Quantidade de caminhões por modelo, para os contadores dos filtros."""
    counts = {}
    for truck in Truck.objects.only("model_template_id"):
        if truck.model_template_id:
            counts[truck.model_template_id] = counts.get(truck.model_template_id, 0) + 1
    return counts
