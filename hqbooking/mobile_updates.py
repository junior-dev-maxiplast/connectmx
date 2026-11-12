"""Atualização remota do app ConnectMX Mobile, servida pelo próprio ConnectMX.

O app (expo-updates) pergunta ao servidor, ao abrir e de tempos em tempos, se há
JavaScript novo para a versão do APK instalado (a "runtime version"). O ConnectMX
responde com o manifesto da última atualização publicada com
`manage.py publicar_app_mobile` e serve os arquivos dela. Assim, mudança de tela
ou de regra (como a leitura do QR Code) chega aos celulares sem reinstalar.
Mudança nativa (permissão, módulo novo, versão do Expo) ainda pede APK novo, com
outra versão: o app só recebe atualizações da própria versão.

Formato, conferido no cliente Android do expo-updates 57 (protocolo 1):
- resposta `multipart/mixed` com as partes `manifest` e `extensions`;
- `204` sem corpo quando não há nada novo;
- `hash` de cada arquivo = SHA-256 em base64url sem `=`; `key` = MD5 em hex.

Os arquivos ficam em `MOBILE_APP_UPDATES_DIR/<versão>/<data da publicação>/`,
fora do git. O link dos arquivos usa `CONNECTMX_PUBLIC_URL` quando existe: atrás
do nginx o Django pode não enxergar a porta (8888) do endereço público.
"""

import base64
import hashlib
import json
import mimetypes
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

PLATFORM = "android"
JAVASCRIPT_SUFFIXES = {".hbc", ".js", ".bundle"}


def updates_root():
    configured = getattr(settings, "MOBILE_APP_UPDATES_DIR", None)
    return Path(configured) if configured else Path(settings.BASE_DIR) / "app_updates"


def safe_name(value):
    """Nome de pasta aceito na URL (versão ou data): só letras, números, ponto, hífen e sublinhado."""
    return bool(value) and len(value) <= 64 and all(char.isalnum() or char in "._-" for char in value)


def read_json(path):
    """Lê JSON em UTF-8 ou UTF-16 (o `>` do PowerShell 5.1 grava UTF-16)."""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"{Path(path).name} não é um JSON válido.")


def published_updates(runtime_version):
    """Atualizações publicadas para a versão, da mais antiga para a mais nova (o nome é a data)."""
    if not safe_name(runtime_version):
        return []
    folder = updates_root() / runtime_version
    if not folder.is_dir():
        return []
    return sorted(item for item in folder.iterdir() if item.is_dir() and (item / "metadata.json").is_file())


def latest_update_dir(runtime_version):
    updates = published_updates(runtime_version)
    return updates[-1] if updates else None


def update_id(update_dir):
    """Identificador estável da atualização: o mesmo pacote publicado de novo não vira "novo"."""
    digest = hashlib.sha256((Path(update_dir) / "metadata.json").read_bytes()).hexdigest()
    return str(uuid.UUID(digest[:32]))


def created_at(update_dir):
    update_dir = Path(update_dir)
    try:
        moment = datetime.strptime(update_dir.name[:14], "%Y%m%d%H%M%S").replace(tzinfo=dt_timezone.utc)
    except ValueError:
        moment = datetime.fromtimestamp((update_dir / "metadata.json").stat().st_mtime, tz=dt_timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def platform_files(update_dir):
    """O bundle e as imagens do Android segundo o `metadata.json` do `expo export`."""
    metadata = read_json(Path(update_dir) / "metadata.json")
    files = (metadata.get("fileMetadata") or {}).get(PLATFORM) or {}
    bundle = files.get("bundle")
    if not bundle:
        raise ValueError("metadata.json sem o bundle do Android: gere o pacote com --platform android.")
    if not (Path(update_dir) / bundle).is_file():
        raise ValueError(f"Arquivo do bundle não encontrado: {bundle}")
    assets = []
    for asset in files.get("assets") or []:
        path = asset.get("path")
        if not path or not (Path(update_dir) / path).is_file():
            raise ValueError(f"Arquivo do pacote não encontrado: {path}")
        assets.append({"path": path, "ext": (asset.get("ext") or "").lstrip(".")})
    return bundle, assets


def _sha256_base64url(data):
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")


def _asset_entry(base_url, runtime_version, update_dir, relative_path, ext, is_launch_asset):
    data = (Path(update_dir) / relative_path).read_bytes()
    if is_launch_asset:
        extension, content_type = ".bundle", "application/javascript"
    else:
        extension = f".{ext}" if ext else ""
        content_type = mimetypes.guess_type(f"arquivo{extension}")[0] or "application/octet-stream"
    url_path = reverse(
        "mobile_update_asset",
        args=[runtime_version, Path(update_dir).name, str(relative_path).replace("\\", "/")],
    )
    return {
        "hash": _sha256_base64url(data),
        "key": hashlib.md5(data).hexdigest(),
        "fileExtension": extension,
        "contentType": content_type,
        "url": f"{base_url}{url_path}",
    }


def build_manifest(base_url, runtime_version, update_dir):
    bundle, assets = platform_files(update_dir)
    config_path = Path(update_dir) / "expoConfig.json"
    expo_config = read_json(config_path) if config_path.is_file() else {}
    return {
        "id": update_id(update_dir),
        "createdAt": created_at(update_dir),
        "runtimeVersion": runtime_version,
        "launchAsset": _asset_entry(base_url, runtime_version, update_dir, bundle, None, True),
        "assets": [_asset_entry(base_url, runtime_version, update_dir, asset["path"], asset["ext"], False) for asset in assets],
        "metadata": {},
        "extra": {"expoClient": expo_config} if expo_config else {},
    }


def _base_url(request):
    public = (getattr(settings, "CONNECTMX_PUBLIC_URL", "") or "").rstrip("/")
    return public or request.build_absolute_uri("/").rstrip("/")


def _protocol_headers(response):
    response["expo-protocol-version"] = "1"
    response["expo-sfv-version"] = "0"
    response["Cache-Control"] = "private, max-age=0"
    return response


def _multipart(parts):
    boundary = f"connectmx-{uuid.uuid4().hex}"
    chunks = []
    for name, body in parts:
        chunks.append(
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{body}\r\n"
        )
    chunks.append(f"--{boundary}--\r\n")
    return "".join(chunks).encode("utf-8"), boundary


@require_GET
def update_manifest(request):
    """O que o app consulta: o manifesto da última atualização da versão dele, ou 204 se não há nada novo."""
    platform = request.headers.get("expo-platform") or request.GET.get("platform") or ""
    runtime_version = request.headers.get("expo-runtime-version") or request.GET.get("runtime-version") or ""
    if platform != PLATFORM:
        return JsonResponse({"error": "Só há atualizações para Android."}, status=400)
    if not safe_name(runtime_version):
        return JsonResponse({"error": "Falta a versão do app (expo-runtime-version)."}, status=400)

    update_dir = latest_update_dir(runtime_version)
    if update_dir is None:
        return _protocol_headers(HttpResponse(status=204))
    current = (request.headers.get("expo-current-update-id") or "").strip().lower()
    if current and current == update_id(update_dir):
        return _protocol_headers(HttpResponse(status=204))

    try:
        manifest = build_manifest(_base_url(request), runtime_version, update_dir)
    except (OSError, ValueError) as exc:
        return JsonResponse({"error": f"A atualização publicada está incompleta: {exc}"}, status=500)
    body, boundary = _multipart([
        ("manifest", json.dumps(manifest, ensure_ascii=False)),
        ("extensions", json.dumps({"assetRequestHeaders": {}})),
    ])
    return _protocol_headers(HttpResponse(body, content_type=f"multipart/mixed; boundary={boundary}"))


@require_GET
def update_asset(request, runtime_version, update_name, asset_path):
    """Arquivo de uma atualização publicada (bundle ou imagem). Não sai da pasta da atualização."""
    if not (safe_name(runtime_version) and safe_name(update_name)):
        raise Http404
    base = (updates_root() / runtime_version / update_name).resolve()
    target = (base / asset_path).resolve()
    if base not in target.parents or not target.is_file():
        raise Http404
    if target.suffix in JAVASCRIPT_SUFFIXES:
        content_type = "application/javascript"
    else:
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    response = FileResponse(open(target, "rb"), content_type=content_type)
    # O nome da pasta muda a cada publicação: o arquivo nunca muda no mesmo endereço.
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
