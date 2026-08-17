import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.6-sol"
ALLOWED_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _fernet():
    digest = hashlib.sha256(f"connectmx-openai:{settings.SECRET_KEY}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value):
    if not value:
        return None
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"fernet:v1:{token}"


def decrypt_secret(value):
    if not value:
        return ""
    if not value.startswith("fernet:v1:"):
        return ""
    try:
        return _fernet().decrypt(value.split(":", 2)[2].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def get_openai_runtime_config():
    from .models import SystemConfig

    database_config = SystemConfig.objects.order_by("-updated_at", "-id").first()
    database_key = decrypt_secret(getattr(database_config, "openai_api_key_encrypted", None))
    environment_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    api_key = environment_key or database_key

    database_enabled = bool(getattr(database_config, "openai_enabled", False))
    enabled = _env_bool("CONNECTMX_OPENAI_ENABLED", database_enabled)
    base_url = (os.environ.get("OPENAI_BASE_URL") or getattr(database_config, "openai_base_url", None) or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")
    model = (os.environ.get("OPENAI_MODEL") or getattr(database_config, "openai_model", None) or DEFAULT_OPENAI_MODEL).strip()
    reasoning_effort = (os.environ.get("OPENAI_REASONING_EFFORT") or getattr(database_config, "openai_reasoning_effort", None) or "medium").strip().lower()
    if reasoning_effort not in ALLOWED_REASONING_EFFORTS:
        reasoning_effort = "medium"

    database_timeout = int(getattr(database_config, "openai_timeout_sec", 120) or 120)
    database_max_tokens = int(getattr(database_config, "openai_max_output_tokens", 5000) or 5000)
    return {
        "enabled": enabled,
        "api_key": api_key,
        "api_key_configured": bool(api_key),
        "api_key_source": "environment" if environment_key else ("encrypted_database" if database_key else "missing"),
        "base_url": base_url,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "timeout": _env_int("OPENAI_TIMEOUT_SECONDS", database_timeout, 10, 600),
        "max_output_tokens": _env_int("OPENAI_MAX_OUTPUT_TOKENS", database_max_tokens, 500, 50000),
    }


def public_openai_runtime_config():
    config = get_openai_runtime_config()
    return {key: value for key, value in config.items() if key != "api_key"}
