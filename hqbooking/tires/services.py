"""Regras de domínio da logística de pneus.

As views desta pasta cuidam apenas de requisição/resposta; toda a manipulação de
estado dos pneus (instalar, remover, recapar, descartar, reposicionar) vive aqui
para poder ser testada e reutilizada entre as páginas do módulo.
"""

import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db.models import Count
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

# O mapa do caminhao colore a posicao pela vida ja gasta do pneu: novo, e
# depois um degrau a cada recape, ate o limite.
RECAP_HEAT_LABELS = {
    0: "Novo",
    1: "1o recape",
    2: "2o recape",
    3: "3o recape",
}


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


def clean_text(raw_value, max_length=None):
    """Colapsa espacos e corta no tamanho do campo."""
    text = " ".join(str(raw_value or "").split())
    if max_length:
        text = text[:max_length]
    return text


def parse_groove_depth(raw_value):
    """Sulco em milimetros. Aceita '12', '12,5' e '12.5 mm'."""
    text = str(raw_value or "").lower().replace("mm", "").strip()
    value = parse_decimal(text)
    if value is None:
        return None
    if value < 0 or value > Decimal("99.9"):
        return None
    return value.quantize(Decimal("0.1"))


MAX_PHOTO_BYTES = 8 * 1024 * 1024
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")


def validate_movement_photo(uploaded):
    """Confere tipo e tamanho da foto anexada. Devolve erro ou None."""
    if not uploaded:
        return None
    name = str(getattr(uploaded, "name", "") or "").lower()
    if not name.endswith(PHOTO_EXTENSIONS):
        return "Anexe uma imagem (JPG, PNG, WEBP ou HEIC)."
    if getattr(uploaded, "size", 0) > MAX_PHOTO_BYTES:
        return "A foto precisa ter no maximo 8 MB."
    return None


def read_tire_specs(post):
    """Le DOT, medida, modelo e sulco de um POST e valida os obrigatorios.

    Devolve (specs, erro). DOT e sulco sao exigidos porque sao eles que
    identificam o lote de fabricacao e dizem quando o pneu precisa sair de
    operacao — sem os dois o cadastro nao serve para acompanhar desgaste.
    """
    dot_code = clean_text(post.get("dot_code"), 20)
    if not dot_code:
        return None, "Informe o DOT do pneu."

    groove_raw = (post.get("groove_depth_mm") or "").strip()
    if not groove_raw:
        return None, "Informe a medida do sulco do pneu."
    groove_depth_mm = parse_groove_depth(groove_raw)
    if groove_depth_mm is None:
        return None, "Informe uma medida de sulco valida, em milimetros."

    return (
        {
            "dot_code": dot_code,
            "size_spec": clean_text(post.get("size_spec"), 40) or None,
            "tire_model": clean_text(post.get("tire_model"), 80) or None,
            "groove_depth_mm": groove_depth_mm,
        },
        None,
    )


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
    photo=None,
):
    return TireMovement.objects.create(
        tire=tire,
        movement_type=movement_type,
        truck=truck,
        tire_number=tire_number,
        position_label=position_label or None,
        movement_date=movement_date or timezone.localdate(),
        odometer_km=odometer_km,
        movement_cost=movement_cost,
        note=note or None,
        photo=photo or None,
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


def _known_values(field):
    """Valores ja usados num campo livre, uma entrada por grafia canonica."""
    seen = {}
    for value in Tire.objects.values_list(field, flat=True):
        cleaned = " ".join(str(value or "").split())
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen[key] = cleaned
    return sorted(seen.values(), key=str.lower)


def known_tire_models():
    return _known_values("tire_model")


def known_sizes():
    return _known_values("size_spec")


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


def update_tire_identity(
    tire,
    serial_number,
    brand,
    purchase_value=None,
    registered_on=None,
    note=None,
    specs=None,
):
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

    fields = ["serial_number", "brand", "purchase_value", "registered_on", "notes", "updated_at"]
    if specs:
        for key, value in specs.items():
            setattr(tire, key, value)
            fields.append(key)
    tire.save(update_fields=fields)

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
    specs=None,
):
    """Cadastra um pneu no estoque.

    `recap_count` e `retread_total` existem para pneus que já vinham rodando
    antes do sistema: um pneu usado não começa a vida com zero recapes.
    `specs` traz DOT, medida, modelo e sulco, validados por `read_tire_specs`.
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
        **(specs or {}),
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
    specs=None,
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
        specs=specs,
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


def discard_tire(
    tire,
    movement_date=None,
    odometer_km=None,
    note=None,
    truck=None,
    tire_number=None,
    position_label=None,
    photo=None,
):
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
        photo=photo,
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
    specs=None,
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
            specs=specs,
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


def recap_filter_options():
    """Faixas do filtro de recapes: uma por degrau, do novo ao limite."""
    return [
        {"key": str(level), "level": level, "label": RECAP_HEAT_LABELS[level]}
        for level in sorted(RECAP_HEAT_LABELS)
    ]


def parse_recap_level(raw_value):
    """Nível de recape vindo da URL, ou None quando o filtro está desligado."""
    raw_value = str(raw_value or "").strip()
    if not raw_value.isdigit():
        return None
    level = int(raw_value)
    return level if 0 <= level <= MAX_RETREADS else None


def filter_by_recap_level(queryset, level):
    """Aplica o filtro de recapes. O topo é `>=` para não perder dado antigo
    que porventura tenha passado do limite."""
    if level is None:
        return queryset
    if level >= MAX_RETREADS:
        return queryset.filter(recap_count__gte=MAX_RETREADS)
    return queryset.filter(recap_count=level)


def _grouped_totals(queryset, field):
    """`{valor: total}` agrupando por um campo.

    O `order_by()` vazio é obrigatório: a ordenação padrão do modelo entraria
    no GROUP BY e devolveria uma linha por pneu em vez de uma por grupo.
    """
    rows = queryset.order_by().values(field).annotate(total=Count("id"))
    return {row[field]: row["total"] for row in rows}


def count_by_status(queryset):
    """Quantos pneus por status, para os contadores dos filtros."""
    return _grouped_totals(queryset, "status")


def count_by_recap_level(queryset):
    """Quantos pneus por nível de recape, com o topo agrupado no limite."""
    counts = {level: 0 for level in RECAP_HEAT_LABELS}
    for value, total in _grouped_totals(queryset, "recap_count").items():
        level = min(int(value or 0), MAX_RETREADS)
        counts[level] = counts.get(level, 0) + total
    return counts


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
        tire = change.tire if change and change.tire_id else None
        slot["change"] = change
        slot["last_metrics"] = last_metrics.get(slot["tire_number"])
        slot["tire_id"] = change.tire_id if change and change.tire_id else ""
        slot["display_code"] = change.tire_code if change and change.tire_code else slot["tire_code"]
        slot["display_brand"] = change.tire_brand if change and change.tire_brand else "—"
        slot["display_status"] = tire.get_status_display() if tire else "Vazia"
        slot["display_recap_count"] = tire.recap_count if tire else 0
        slot["display_dot"] = (tire.dot_code if tire else None) or "—"
        slot["display_size"] = (tire.size_spec if tire else None) or "—"
        slot["display_model"] = (tire.tire_model if tire else None) or "—"
        slot["display_groove"] = tire.groove_depth_mm if tire else None
        slot["is_filled"] = bool(tire)

        age_days = None
        if change and change.changed_on:
            age_days = max((today - change.changed_on).days, 0)
        slot["tire_age_days"] = age_days

        # O mapa de calor conta recapes, nao rodagem: e o numero de recapes que
        # diz quanta vida o pneu ainda tem antes do descarte.
        level = min(int(tire.recap_count or 0), MAX_RETREADS) if tire else None
        slot["tire_heat_level"] = level
        slot["tire_heat_label"] = RECAP_HEAT_LABELS.get(level) if level is not None else None
        return slot

    for row in rows:
        row["left_slots"] = [enrich(slot) for slot in row["left_slots"]]
        row["right_slots"] = [enrich(slot) for slot in row["right_slots"]]
    return rows, [enrich(slot) for slot in spare_slots]


def heat_legend():
    """Legenda do mapa de calor: um degrau por recape."""
    return [
        {"level": level, "label": RECAP_HEAT_LABELS[level]}
        for level in sorted(RECAP_HEAT_LABELS)
    ]


def tire_tracks_for_truck(truck, tones=None, limit_per_tire=40):
    """Movimentações agrupadas por pneu, para os filtros da tela do caminhão.

    Entram todos os pneus que já passaram por este caminhão, e de cada um a
    vida inteira — inclusive o que aconteceu fora do veículo (recapagem,
    estoque, descarte) — porque é isso que explica o estado atual da posição.
    """
    tones = tones or {}

    assignments = {
        row.tire_id: row
        for row in TruckTireChange.objects.filter(truck=truck, tire__isnull=False)
    }
    tire_ids = set(assignments)
    tire_ids.update(
        TireMovement.objects.filter(truck=truck)
        .exclude(tire__isnull=True)
        .values_list("tire_id", flat=True)
        .distinct()
    )
    if not tire_ids:
        return []

    grouped = {}
    for movement in (
        TireMovement.objects.select_related("tire", "truck")
        .filter(tire_id__in=tire_ids)
        .order_by("-created_at", "-id")
    ):
        rows = grouped.setdefault(movement.tire_id, [])
        if len(rows) < limit_per_tire:
            rows.append(
                {
                    "movement": movement,
                    "tone": tones.get(movement.movement_type, "is-neutral"),
                    "is_here": movement.truck_id == truck.id,
                }
            )

    tracks = []
    for tire in Tire.objects.filter(pk__in=tire_ids).order_by("serial_number", "id"):
        row = assignments.get(tire.id)
        level = min(int(tire.recap_count or 0), MAX_RETREADS)
        tracks.append(
            {
                "tire": tire,
                "is_here": bool(row),
                "tire_number": row.tire_number if row else None,
                "position_label": tire.current_slot_label if row else None,
                "heat_level": level,
                "heat_label": RECAP_HEAT_LABELS[level],
                "movements": grouped.get(tire.id, []),
            }
        )

    # Quem está no caminhão agora vem primeiro, na ordem das posições.
    tracks.sort(key=lambda item: (not item["is_here"], item["tire_number"] or 0, item["tire"].serial_number))
    return tracks


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


# --------------------------------------------------------------------------- #
# Exclusão definitiva
# --------------------------------------------------------------------------- #
#
# Excluir apaga a linha do banco, diferente de descartar (que preserva o pneu
# como histórico). Só é liberado para um cadastro que nunca foi usado: qualquer
# vínculo vira um "impedimento" mostrado ao usuário, em vez de um erro seco.


def _plural(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


def tire_delete_blockers(tire, counters=None):
    """Motivos que impedem excluir um pneu do cadastro. Vazio = pode excluir.

    `counters` permite reaproveitar contagens já feitas em lote pela listagem
    do estoque, evitando uma consulta por linha da tabela.
    """
    if counters is None:
        counters = _delete_counters_for([tire]).get(tire.id, {})

    blockers = []
    if tire.status == Tire.STATUS_INSTALLED or tire.current_truck_id:
        where = tire.current_truck.identifier if tire.current_truck_id else "um caminhão"
        blockers.append(f"está instalado em {where}")
    elif counters.get("slots"):
        blockers.append("ainda ocupa uma posição de caminhão")

    if tire.status == Tire.STATUS_RETREADING:
        blockers.append("está em recapagem")
    if tire.status == Tire.STATUS_DISCARDED:
        blockers.append("já foi descartado e o registro da baixa se perderia")

    history = counters.get("history") or 0
    if history:
        blockers.append(_plural(history, "ciclo no histórico de trocas", "ciclos no histórico de trocas"))

    movements = counters.get("movements") or 0
    if movements:
        blockers.append(_plural(movements, "movimentação registrada", "movimentações registradas"))

    return blockers


def _delete_counters_for(tires):
    """Contagens de vínculo de vários pneus de uma vez.

    O cadastro em si (`TYPE_REGISTER`) não conta: ele nasce junto com o pneu e
    some junto com ele.
    """
    tires = list(tires)
    ids = [tire.id for tire in tires]
    serials = {tire.id: (tire.serial_number or "").strip().lower() for tire in tires}
    counters = {tire_id: {"slots": 0, "history": 0, "movements": 0} for tire_id in ids}
    if not ids:
        return counters

    for tire_id in TruckTireChange.objects.filter(tire_id__in=ids).values_list("tire_id", flat=True):
        counters[tire_id]["slots"] += 1

    for tire_id in (
        TireMovement.objects.filter(tire_id__in=ids)
        .exclude(movement_type=TireMovement.TYPE_REGISTER)
        .values_list("tire_id", flat=True)
    ):
        counters[tire_id]["movements"] += 1

    for tire_id in (
        TruckTireChangeHistory.objects.filter(tire_id__in=ids)
        .exclude(tire_id__isnull=True)
        .values_list("tire_id", flat=True)
    ):
        counters[tire_id]["history"] += 1

    # O histórico também aponta para o pneu que saiu apenas pelo número, sem
    # chave estrangeira — esse vínculo conta do mesmo jeito.
    by_serial = {serial: tire_id for tire_id, serial in serials.items() if serial}
    if by_serial:
        for code in (
            TruckTireChangeHistory.objects.filter(previous_tire_code__isnull=False)
            .values_list("previous_tire_code", flat=True)
            .iterator(chunk_size=1000)
        ):
            tire_id = by_serial.get(str(code or "").strip().lower())
            if tire_id:
                counters[tire_id]["history"] += 1

    return counters


def tire_delete_map(tires):
    """`{id do pneu: lista de impedimentos}` para uma listagem inteira."""
    tires = list(tires)
    counters = _delete_counters_for(tires)
    return {tire.id: tire_delete_blockers(tire, counters.get(tire.id, {})) for tire in tires}


def model_delete_blockers(template, truck_count=None):
    """Motivos que impedem excluir um modelo de caminhão."""
    if truck_count is None:
        truck_count = Truck.objects.filter(model_template=template).count()
    if not truck_count:
        return []
    return [_plural(truck_count, "caminhão usa este modelo", "caminhões usam este modelo")]
