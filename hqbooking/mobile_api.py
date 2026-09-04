"""API JSON consumida pelo ConnectMX Mobile (app Expo em `connectmx-mobile/`).

Existe separada de `views.py` porque o cliente aqui não é um navegador: não há
sessão, cookie nem CSRF para carregar. A autenticação é uma chave estática no
cabeçalho, conferida em `_require_app_key`, e todo o resto reaproveita as mesmas
funções da tela web (`_extract_romaneio_payload`, `_submit_romaneio_entry`), para
que app e web gravem exatamente o mesmo registro na USU_TCONROM.
"""

import json
import os

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import SimulationRomaneioEntry
from .views import (
    ROMANEIO_ADDRESS_CODE_MAX_DIGITS,
    ROMANEIO_PACKAGE_CODE_MAX_DIGITS,
    _extract_romaneio_payload,
    _parse_romaneio_date,
    _parse_romaneio_decimal,
    _parse_romaneio_int,
    _parse_romaneio_numeric_code,
    _parse_romaneio_record_type,
    _parse_romaneio_time,
    _romaneio_record_type_label,
    _simulation_oracle_config,
    _submit_romaneio_entry,
)


API_KEY_ENV = "CONNECTMX_MOBILE_API_KEY"
API_KEY_HEADER = "HTTP_X_CONNECTMX_KEY"


def _configured_api_key():
    return (os.getenv(API_KEY_ENV) or "").strip()


def _require_app_key(request):
    """Devolve uma resposta de erro quando a chave do app não confere.

    Sem `CONNECTMX_MOBILE_API_KEY` no ambiente a rota fica aberta: é o modo de
    desenvolvimento, em que o celular aponta para o runserver da máquina. Em
    produção basta definir a variável para exigir a chave.
    """
    expected = _configured_api_key()
    if not expected:
        return None

    received = (request.META.get(API_KEY_HEADER) or "").strip()
    if received != expected:
        return JsonResponse(
            {"status": "error", "message": "Chave do aplicativo inválida."},
            status=401,
        )
    return None


def _serialize_entry(entry):
    return {
        "id": entry.id,
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
        "record_type": entry.record_type,
        "record_type_label": _romaneio_record_type_label(entry.record_type),
        "sync_status": entry.sync_status,
        "sync_status_label": entry.get_sync_status_display(),
        "sync_message": entry.sync_message or "",
        "source": "leitura" if entry.barcode_payload else "manual",
        "client_reference": entry.client_reference,
    }


@require_GET
@csrf_exempt
def mobile_ping(request):
    """Usada pela tela de Ajustes para validar o endereço do servidor."""
    key_error = _require_app_key(request)
    if key_error:
        return key_error

    oracle_config = _simulation_oracle_config()
    return JsonResponse(
        {
            "status": "ok",
            "app": "connectmx-mobile",
            "api_version": 1,
            "requires_key": bool(_configured_api_key()),
            "oracle_ready": oracle_config["is_ready"],
            "oracle_service_name": oracle_config["service_name"],
            "server_time": timezone.localtime().strftime("%d/%m/%Y %H:%M:%S"),
        }
    )


@csrf_exempt
@require_POST
def mobile_romaneio_create(request):
    """Grava uma leitura vinda do app.

    Aceita os dois caminhos da tela web em um único corpo: se vier
    `barcode_payload`, os campos saem da leitura; senão, empresa, filial,
    volumes, peso, código do pallet e endereçamento vêm preenchidos à mão.
    Data e hora são opcionais e caem no horário do servidor, como na leitura
    contínua da web. Em qualquer um dos dois caminhos, `_submit_romaneio_entry`
    recusa a gravação se o mesmo `package_code` já tiver uma leitura de sucesso
    na mesma etapa — cada embalagem entra uma vez por estágio da contagem.
    """
    key_error = _require_app_key(request)
    if key_error:
        return key_error

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"status": "error", "message": "JSON inválido."}, status=400)

    user_code = str(payload.get("user_code") or "").strip()
    if not user_code:
        return JsonResponse(
            {"status": "error", "message": "Informe a matrícula antes de enviar."},
            status=400,
        )

    # O app manda o id da leitura na fila local. Ele é a defesa contra a
    # duplicidade do reenvio automático: quando o INSERT deu certo mas a resposta
    # não chegou ao celular, o retry cai aqui e devolvemos o mesmo registro em vez
    # de gravar um segundo romaneio.
    client_reference = str(payload.get("client_reference") or "")[:64].strip()
    if client_reference:
        existing = (
            SimulationRomaneioEntry.objects.filter(
                client_reference=client_reference,
                sync_status=SimulationRomaneioEntry.SYNC_SUCCESS,
            )
            .order_by("-id")
            .first()
        )
        if existing:
            return JsonResponse(
                {
                    "status": "ok",
                    "duplicate": True,
                    "message": (
                        "Esta leitura já havia sido gravada. "
                        f"Sequência: {existing.sequence_record}."
                    ),
                    "entry": _serialize_entry(existing),
                }
            )

    # A etapa da contagem não vem no código de barras: é o botão que a pessoa
    # tocou na tela inicial. Vale igual para leitura e digitação, porque é ela
    # que decide se este pallet já foi contado *nesta* etapa.
    record_type = _parse_romaneio_record_type(payload.get("record_type"))
    if record_type is None:
        return JsonResponse(
            {
                "status": "error",
                "message": (
                    "Informe a etapa da contagem: 1 separar, 2 guardar, 3 paletizar ou 4 carregar."
                ),
            },
            status=400,
        )

    barcode_payload = str(payload.get("barcode_payload") or "").strip()

    if barcode_payload:
        mapped = _extract_romaneio_payload(barcode_payload)
        if not mapped:
            return JsonResponse(
                {
                    "status": "error",
                    "message": (
                        "Não foi possível interpretar a leitura. "
                        "Verifique se o código traz os 6 campos do romaneio."
                    ),
                },
                status=400,
            )
        company_code = mapped["company_code"]
        branch_code = mapped["branch_code"]
        volume_quantity = mapped["volume_quantity"]
        romaneio_weight = mapped["romaneio_weight"]
        package_code = mapped["package_code"]
        address_code = mapped["address_code"]
    else:
        company_code = str(payload.get("company_code") or "").strip()
        branch_code = str(payload.get("branch_code") or "").strip()
        volume_quantity = _parse_romaneio_int(payload.get("volume_quantity"))
        romaneio_weight = _parse_romaneio_decimal(payload.get("romaneio_weight"))
        package_code = _parse_romaneio_numeric_code(payload.get("package_code"), ROMANEIO_PACKAGE_CODE_MAX_DIGITS)
        address_code = _parse_romaneio_numeric_code(payload.get("address_code"), ROMANEIO_ADDRESS_CODE_MAX_DIGITS)

    read_now = timezone.localtime()
    generated_date = _parse_romaneio_date(payload.get("generated_date")) or read_now.date()
    generated_time = (
        _parse_romaneio_time(payload.get("generated_time"))
        or read_now.time().replace(microsecond=0)
    )

    if not company_code or not branch_code:
        return JsonResponse(
            {"status": "error", "message": "Empresa e filial são obrigatórias."},
            status=400,
        )
    if volume_quantity is None:
        return JsonResponse(
            {"status": "error", "message": "Informe uma quantidade de volumes válida."},
            status=400,
        )
    if romaneio_weight is None:
        return JsonResponse(
            {"status": "error", "message": "Informe um peso de romaneio válido."},
            status=400,
        )
    if not package_code:
        return JsonResponse(
            {"status": "error", "message": "Informe um código do pallet numérico (até 9 dígitos)."},
            status=400,
        )
    if not address_code:
        return JsonResponse(
            {"status": "error", "message": "Informe um endereçamento numérico (até 6 dígitos)."},
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
        record_type=record_type,
        barcode_payload=barcode_payload or None,
        client_reference=client_reference,
    )

    if error:
        if entry.sync_status == SimulationRomaneioEntry.SYNC_DUPLICATE:
            # Recusa definitiva de negócio, não falha de infraestrutura: o app
            # trata como "blocked" (não retenta sozinho) e mostra o motivo.
            return JsonResponse(
                {"status": "duplicate_package", "message": error, "entry": _serialize_entry(entry)},
                status=409,
            )
        # O registro ficou no banco local com sync_status=erro. Devolvemos 502
        # para o app tratar como "não gravou no ERP", mas com a entrada junto
        # para a pessoa ver o que foi tentado.
        return JsonResponse(
            {"status": "sync_error", "message": error, "entry": _serialize_entry(entry)},
            status=502,
        )

    return JsonResponse(
        {
            "status": "ok",
            "message": f"Romaneio gravado. Sequência gerada: {entry.sequence_record}.",
            "entry": _serialize_entry(entry),
        }
    )


@require_GET
@csrf_exempt
def mobile_romaneio_list(request):
    """Últimos envios, para o app mostrar o histórico do aparelho."""
    key_error = _require_app_key(request)
    if key_error:
        return key_error

    user_code = str(request.GET.get("user_code") or "").strip()
    try:
        limit = int(request.GET.get("limit") or 30)
    except ValueError:
        limit = 30
    limit = max(1, min(limit, 100))

    queryset = SimulationRomaneioEntry.objects.all()
    if user_code:
        queryset = queryset.filter(user_code=user_code)

    return JsonResponse(
        {
            "status": "ok",
            "entries": [_serialize_entry(entry) for entry in queryset[:limit]],
        }
    )
