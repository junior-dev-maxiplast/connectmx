"""
Integração com a API v4 da Econodata, desenhada para gastar o mínimo de tokens.

Documentação: https://econodata-tecnologia.github.io/api-v4-docs/
Registro da integração: REGISTRO_ECONODATA.md (raiz do projeto Django).

Regras de economia (valem para todo uso deste módulo):
1. Só consulta por CNPJ, em lote de até 100 (`POST /companies`).
2. Pede só os campos do perfil (`campos`, dot-path), nunca blocos inteiros: a
   API cobra por campo entregue, e o perfil "básico" custa 20 tokens por empresa.
3. Antes de cada chamada paga, uma estimativa grátis (`X-Estimate-Only: true`)
   confere o custo contra o teto mensal (`ECONODATA_MONTHLY_TOKENS`).
4. O que já foi comprado fica em `EconodataEmpresa` e não é consultado de novo
   enquanto não vencer (`ECONODATA_CACHE_DAYS`) — inclusive o "não encontrado".
   Quando falta só um campo, pede-se só ele.
5. Toda chamada (estimativa ou paga) fica em `EconodataConsulta`, com o custo do
   header `X-Tokens-Charged`, para conferir com o extrato da Econodata.
6. Nenhuma tela consulta a API ao abrir: só por ação explícita.

Custos medidos em 06/11/2026 com estimativas grátis (lote de 10 empresas):
base 10 por empresa · cadastro.* 5 por campo · cadastro.nivelAtividade 0 ·
perfilNegocio.* 50 por campo (dívidas ~15) · estrategico.* 300 por campo ·
calc_list (contagem) 100 por chamada.
"""

import hashlib
import json
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.db.models import Sum
from django.utils import timezone

from .models import EconodataConsulta, EconodataContagem, EconodataEmpresa, EconodataProspect


DEFAULT_BASE_URL = "https://api.econodata.com.br/v4"
DEFAULT_MONTHLY_TOKENS = 10000
DEFAULT_CACHE_DAYS = 180
BATCH_SIZE = 100
TIMEOUT_SECONDS = 30
INACTIVE_SITUATIONS = {"BAIXADA", "INAPTA", "SUSPENSA", "NULA"}

PROFILES = {
    "basico": {
        "label": "Básico",
        "description": "CNAE principal, situação na Receita e nível de atividade (~20 tokens por empresa).",
        "campos": ["cadastro.cnaePrimario", "cadastro.situacao", "cadastro.nivelAtividade"],
    },
    "cadastral": {
        "label": "Cadastral",
        "description": (
            "Básico + tipo de unidade, abertura, enquadramento, capital social, situação cadastral "
            "detalhada e CNAEs secundários (~50 tokens por empresa)."
        ),
        "campos": [
            "cadastro.cnaePrimario", "cadastro.situacao", "cadastro.nivelAtividade",
            "cadastro.tipoUnidade", "cadastro.dataAbertura", "cadastro.enquadramentoPorte",
            "cadastro.capitalSocial", "cadastro.situacaoCadastral", "cadastro.cnaesSecundarios",
        ],
    },
    "estrategico": {
        "label": "Estratégico (contas-chave)",
        "description": (
            "Faturamento e funcionários estimados do grupo, porte e quantidade de filiais "
            "(~710 tokens por empresa: usar só em contas-chave)."
        ),
        "campos": [
            "estrategico.faturamentoMatrizFiliais", "estrategico.headcountMatrizFiliais",
            "perfilNegocio.porte", "perfilNegocio.qtdFiliais",
        ],
    },
}

# Custo aproximado por campo, em tokens por empresa (medido; a Econodata ainda não
# publica a tabela). Serve para mostrar "≈ N tokens" sem chamar a API.
_FIELD_COST = {"cadastro": 5, "perfilNegocio": 50, "estrategico": 300, "contatosBasicos": 20, "contatosAvancados": 50}
_FREE_FIELDS = {"cadastro.nivelAtividade"}
_BASE_COST = 10


class EconodataError(RuntimeError):
    def __init__(self, message, status=None, tokens_charged=0):
        super().__init__(message)
        self.status = status
        self.tokens_charged = tokens_charged


class EconodataNotConfigured(EconodataError):
    pass


class EconodataBudgetExceeded(EconodataError):
    pass


# ---------------------------------------------------------------------------
# Configuração (lida a cada uso: o .env é carregado pelo settings.py)
# ---------------------------------------------------------------------------

def _setting(name, default=""):
    return (os.environ.get(name) or "").strip() or default


def api_key():
    return _setting("ECONODATA_API_KEY")


def base_url():
    return _setting("ECONODATA_API_URL", DEFAULT_BASE_URL).rstrip("/")


def _int_setting(name, default):
    try:
        return max(int(_setting(name, str(default))), 0)
    except ValueError:
        return default


def monthly_token_limit():
    return _int_setting("ECONODATA_MONTHLY_TOKENS", DEFAULT_MONTHLY_TOKENS)


def cache_days():
    return _int_setting("ECONODATA_CACHE_DAYS", DEFAULT_CACHE_DAYS)


def is_configured():
    return bool(api_key())


# ---------------------------------------------------------------------------
# CNPJ
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")


def normalize_cnpj(value):
    """CNPJ em 14 posições, sem máscara.

    Aceita o CNPJ alfanumérico (a Receita emite desde julho de 2026: letras nas 12
    primeiras posições). O CNPJ numérico que o ERP guarda como número e perde o
    zero à esquerda (13 dígitos) volta a ter 14. CPF e lixo devolvem "".
    """
    raw = _NON_ALNUM.sub("", str(value or "")).upper()
    if raw.isdigit() and 13 <= len(raw) <= 14:
        return raw.zfill(14)
    if len(raw) == 14 and raw[-2:].isdigit():
        return raw
    return ""


_DV_WEIGHTS = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def is_valid_cnpj(value):
    """Confere os dígitos verificadores (módulo 11), inclusive no CNPJ alfanumérico.

    No alfanumérico cada caractere vale o código ASCII − 48 (A = 17, B = 18...),
    como define a Receita. Um CNPJ com dígito errado digitado no ERP não vai para
    a Econodata: poderia fazer a API recusar o lote inteiro.
    """
    cnpj = normalize_cnpj(value)
    if len(cnpj) != 14 or len(set(cnpj)) == 1:
        return False
    values = [ord(character) - 48 for character in cnpj]
    for size in (12, 13):
        total = sum(number * weight for number, weight in zip(values[:size], _DV_WEIGHTS[13 - size:]))
        remainder = total % 11
        if values[size] != (0 if remainder < 2 else 11 - remainder):
            return False
    return True


def cnpj_root(cnpj):
    return normalize_cnpj(cnpj)[:8]


def format_cnpj(cnpj):
    value = normalize_cnpj(cnpj)
    if len(value) != 14:
        return value
    return f"{value[:2]}.{value[2:5]}.{value[5:8]}/{value[8:12]}-{value[12:]}"


# ---------------------------------------------------------------------------
# Custos
# ---------------------------------------------------------------------------

def approx_tokens(campos, companies=1):
    """Custo aproximado (tokens) de consultar `campos` para `companies` empresas."""
    per_company = _BASE_COST
    for field in campos:
        if field in _FREE_FIELDS:
            continue
        per_company += _FIELD_COST.get(field.split(".", 1)[0], 50)
    return per_company * companies


def tokens_used_this_month(now=None):
    now = now or timezone.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = (
        EconodataConsulta.objects.filter(criado_em__gte=start, estimativa=False)
        .aggregate(total=Sum("tokens_cobrados"))
        .get("total")
    )
    return int(used or 0)


def tokens_left_this_month(now=None):
    return max(monthly_token_limit() - tokens_used_this_month(now), 0)


def _check_budget(estimated):
    left = tokens_left_this_month()
    if estimated > left:
        raise EconodataBudgetExceeded(
            f"A consulta custaria ~{estimated} tokens e restam {left} no teto mensal "
            f"(ECONODATA_MONTHLY_TOKENS={monthly_token_limit()}). Nada foi consultado."
        )


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_STATUS_MESSAGES = {
    400: "Requisição malformada.",
    401: "Chave da API ausente, inválida ou revogada (ECONODATA_API_KEY).",
    402: "Saldo de tokens insuficiente na Econodata. Nada foi entregue nem debitado.",
    403: "A chave não tem permissão para este recurso (ex.: chave trial).",
    404: "Empresa ou recurso não encontrado na Econodata.",
    422: "A Econodata recusou os parâmetros da consulta.",
    429: "Limite de requisições da Econodata excedido. Tente de novo em instantes.",
    503: "Econodata indisponível no momento. Tente de novo em instantes.",
}


def _send(method, url, headers, payload):
    """Única saída de rede do módulo (os testes substituem esta função)."""
    return requests.request(method, url, headers=headers, json=payload, timeout=TIMEOUT_SECONDS)


def _request(method, path, payload=None, estimate=False, retries=1):
    if path != "/health" and not is_configured():
        raise EconodataNotConfigured("Chave ECONODATA_API_KEY não configurada no arquivo connect/.env.")
    headers = {"Accept": "application/json"}
    if path != "/health":
        headers["Authorization"] = f"Bearer {api_key()}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if estimate:
        headers["X-Estimate-Only"] = "true"
    try:
        response = _send(method, f"{base_url()}{path}", headers, payload)
    except requests.RequestException as exc:
        # Sem resposta não dá para saber se uma consulta paga foi cobrada: o
        # saldo (grátis, `econodata_status`) confirma. Repetir em 24 h não cobra de novo.
        raise EconodataError(f"Sem resposta da Econodata ({exc.__class__.__name__}). Confira o saldo antes de repetir.")
    status = response.status_code
    if status in (429, 503) and retries > 0:
        try:
            wait = int(response.headers.get("Retry-After") or 2)
        except ValueError:
            wait = 2
        time.sleep(min(max(wait, 1), 30))
        return _request(method, path, payload, estimate, retries - 1)
    try:
        tokens = int(response.headers.get("X-Tokens-Charged") or 0)
    except ValueError:
        tokens = 0
    try:
        body = response.json()
    except ValueError:
        body = {}
    if status >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        message = _STATUS_MESSAGES.get(status, f"Erro {status} na Econodata.")
        if detail:
            message = f"{message} Detalhe: {detail}"
        raise EconodataError(message, status=status, tokens_charged=tokens)
    return body, tokens, status


def _log(endpoint, *, estimate, quantidade, encontrados=0, tokens_estimados=None, tokens_cobrados=0,
         status=None, sucesso=True, erro="", user=None, origem=""):
    return EconodataConsulta.objects.create(
        usuario=user if getattr(user, "is_authenticated", False) else None,
        origem=origem[:60],
        endpoint=endpoint,
        estimativa=estimate,
        quantidade=quantidade,
        encontrados=encontrados,
        tokens_estimados=tokens_estimados,
        tokens_cobrados=tokens_cobrados,
        status_code=status,
        sucesso=sucesso,
        erro=erro[:2000],
    )


def _call(endpoint, method, path, payload, *, estimate, quantidade, user, origem):
    """Chamada com auditoria. Devolve (corpo, tokens_cobrados)."""
    try:
        body, tokens, status = _request(method, path, payload, estimate=estimate)
    except EconodataError as exc:
        _log(endpoint, estimate=estimate, quantidade=quantidade, tokens_cobrados=exc.tokens_charged,
             status=exc.status, sucesso=False, erro=str(exc), user=user, origem=origem)
        raise
    estimated = None
    if estimate and isinstance(body, dict):
        try:
            estimated = int(body.get("tokensEstimados") or 0)
        except (TypeError, ValueError):
            estimated = None
    found = 0
    if isinstance(body, dict):
        found = len(body.get("empresas") or body.get("resultados") or [])
    _log(endpoint, estimate=estimate, quantidade=quantidade, encontrados=found, tokens_estimados=estimated,
         tokens_cobrados=tokens, status=status, user=user, origem=origem)
    return body, tokens


# ---------------------------------------------------------------------------
# Grátis: saúde e saldo
# ---------------------------------------------------------------------------

def health():
    body, _tokens, _status = _request("GET", "/health")
    return body.get("status") == "ok"


def balance(user=None, origem="saldo"):
    body, _tokens = _call("account/balance", "GET", "/account/balance", None, estimate=False,
                          quantidade=0, user=user, origem=origem)
    return int(body.get("saldoTokens") or 0)


def account_status(user=None, origem="status"):
    """Situação da integração, sem gastar tokens."""
    status = {
        "configured": is_configured(),
        "base_url": base_url(),
        "monthly_limit": monthly_token_limit(),
        "used_this_month": tokens_used_this_month(),
        "left_this_month": tokens_left_this_month(),
        "cache_days": cache_days(),
        "cached_companies": EconodataEmpresa.objects.filter(encontrado=True).count(),
        "health": None,
        "balance": None,
        "error": "",
    }
    if not status["configured"]:
        status["error"] = "Chave ECONODATA_API_KEY não configurada."
        return status
    try:
        status["health"] = health()
        status["balance"] = balance(user=user, origem=origem)
    except (EconodataError, requests.RequestException) as exc:
        status["error"] = str(exc)
    return status


# ---------------------------------------------------------------------------
# Enriquecimento por CNPJ (lote) com cache
# ---------------------------------------------------------------------------

def _chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _is_expired(record, now):
    return record.consultado_em < now - timedelta(days=cache_days())


def plan_enrichment(cnpjs, profile="basico", force=False, now=None):
    """O que falta consultar: [(campos, [cnpjs])], sem chamar a API."""
    now = now or timezone.now()
    campos = PROFILES[profile]["campos"]
    normalized = list(dict.fromkeys(filter(None, (normalize_cnpj(value) for value in cnpjs))))
    invalid = [cnpj for cnpj in normalized if not is_valid_cnpj(cnpj)]
    normalized = [cnpj for cnpj in normalized if cnpj not in invalid]
    existing = {record.cnpj: record for record in EconodataEmpresa.objects.filter(cnpj__in=normalized)}
    pending = defaultdict(list)
    cached = 0
    for cnpj in normalized:
        record = existing.get(cnpj)
        if record is None or force or _is_expired(record, now):
            missing = list(campos)
        elif not record.encontrado:
            cached += 1  # "não encontrado" dentro da validade: não paga de novo
            continue
        else:
            missing = [field for field in campos if field not in (record.campos or [])]
        if missing:
            pending[tuple(missing)].append(cnpj)
        else:
            cached += 1
    calls = [(list(fields), chunk) for fields, group in pending.items() for chunk in _chunks(group, BATCH_SIZE)]
    return {
        "profile": profile,
        "total": len(normalized),
        "invalid": invalid,
        "cached": cached,
        "to_fetch": sum(len(chunk) for _fields, chunk in calls),
        "calls": calls,
        "approx_tokens": sum(approx_tokens(fields, len(chunk)) for fields, chunk in calls),
    }


def enrich(cnpjs, profile="basico", *, confirm=False, force=False, user=None, origem="enriquecimento"):
    """Consulta (em lote) os CNPJs que ainda não estão no cache.

    Sem `confirm`, só estima o custo (grátis) e devolve o plano. Com `confirm`,
    confere o teto mensal e faz as chamadas pagas.
    """
    plan = plan_enrichment(cnpjs, profile, force=force)
    result = {**{key: plan[key] for key in ("profile", "total", "cached", "to_fetch", "approx_tokens")},
              "invalid": len(plan["invalid"]),
              "calls": len(plan["calls"]), "estimated_tokens": 0, "charged_tokens": 0,
              "found": 0, "not_found": 0, "confirmed": confirm}
    if not plan["calls"]:
        return result

    estimates = []
    for fields, chunk in plan["calls"]:
        body, _tokens = _call("companies", "POST", "/companies", {"cnpjs": chunk, "campos": fields},
                              estimate=True, quantidade=len(chunk), user=user, origem=origem)
        estimates.append(int(body.get("tokensEstimados") or 0))
    result["estimated_tokens"] = sum(estimates)
    if not confirm:
        return result

    _check_budget(result["estimated_tokens"])
    now = timezone.now()
    for fields, chunk in plan["calls"]:
        try:
            body, tokens = _call("companies", "POST", "/companies", {"cnpjs": chunk, "campos": fields},
                                 estimate=False, quantidade=len(chunk), user=user, origem=origem)
        except EconodataError as exc:
            if exc.status != 404:  # 404: nenhum CNPJ do lote foi encontrado
                raise
            body, tokens = {"empresas": [], "erros": [{"cnpj": cnpj, "motivo": "not_found"} for cnpj in chunk]}, exc.tokens_charged
        result["charged_tokens"] += tokens
        delivered = set()
        for company in body.get("empresas") or []:
            cnpj = normalize_cnpj(company.get("cnpj"))
            if not cnpj:
                continue
            delivered.add(cnpj)
            _store_company(cnpj, company, fields, now)
            result["found"] += 1
        for cnpj in chunk:
            if cnpj not in delivered:
                _store_not_found(cnpj, now)
                result["not_found"] += 1
    return result


def _store_company(cnpj, company, fields, now):
    record = EconodataEmpresa.objects.filter(cnpj=cnpj).first()
    refresh = record is None or not record.encontrado or _is_expired(record, now)
    data = {} if refresh or record is None else dict(record.dados or {})
    for bucket, values in company.items():
        if bucket in ("cnpj", "empresaDesbloqueada"):
            continue
        if isinstance(values, dict):
            data.setdefault(bucket, {}).update(values)
        else:
            data[bucket] = values
    known = set() if refresh or record is None else set(record.campos or [])
    record = record or EconodataEmpresa(cnpj=cnpj)
    record.raiz = cnpj[:8]
    record.encontrado = True
    record.dados = data
    record.campos = sorted(known | set(fields))
    if refresh:
        record.consultado_em = now
    _apply_columns(record)
    record.save()
    return record


def _store_not_found(cnpj, now):
    EconodataEmpresa.objects.update_or_create(
        cnpj=cnpj,
        defaults={"raiz": cnpj[:8], "encontrado": False, "dados": {}, "campos": [], "consultado_em": now},
    )


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _range_value(faixa, total_key="valor"):
    """Valor de uma faixa da Econodata: o pontual, senão o meio da faixa."""
    if not isinstance(faixa, dict):
        return None
    value = _decimal(faixa.get(total_key))
    if value is not None:
        return value
    low, high = _decimal(faixa.get("de")), _decimal(faixa.get("ate"))
    if low is not None and high is not None and high < Decimal("999999999"):
        return (low + high) / 2
    return low


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _apply_columns(record):
    data = record.dados or {}
    cadastro = data.get("cadastro") or {}
    perfil = data.get("perfilNegocio") or {}
    estrategico = data.get("estrategico") or {}
    cnae = cadastro.get("cnaePrimario") or {}
    record.cnae_codigo = (cnae.get("codigo") or "")[:20]
    record.cnae_descricao = (cnae.get("descricao") or "")[:255]
    record.situacao = (cadastro.get("situacao") or "")[:20]
    record.nivel_atividade = (cadastro.get("nivelAtividade") or "")[:20]
    record.tipo_unidade = (cadastro.get("tipoUnidade") or "")[:10]
    record.enquadramento_porte = (cadastro.get("enquadramentoPorte") or "")[:40]
    record.data_abertura = _parse_date(cadastro.get("dataAbertura"))
    record.capital_social = _decimal(cadastro.get("capitalSocial"))
    record.porte = (perfil.get("porte") or "")[:20]
    quantity = perfil.get("qtdFiliais")
    record.qtd_filiais = int(quantity) if isinstance(quantity, (int, float)) and quantity >= 0 else None
    record.faturamento_grupo = _range_value(estrategico.get("faturamentoMatrizFiliais"))
    headcount = _range_value(estrategico.get("headcountMatrizFiliais"), total_key="total")
    record.funcionarios_grupo = int(headcount) if headcount is not None else None


# ---------------------------------------------------------------------------
# Contagem de mercado (calc_list) com cache
# ---------------------------------------------------------------------------

def _filters_key(filtros):
    encoded = json.dumps(filtros, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def count_companies(filtros, *, confirm=False, user=None, origem="contagem"):
    """Quantas empresas casam com os filtros (100 tokens por contagem nova; cache em seguida)."""
    key = _filters_key(filtros)
    now = timezone.now()
    cached = EconodataContagem.objects.filter(chave=key).first()
    if cached and cached.consultado_em >= now - timedelta(days=cache_days()):
        return {"total": cached.total, "cached": True, "estimated_tokens": 0, "charged_tokens": 0}
    body, _tokens = _call("companies/calc_list", "POST", "/companies/calc_list", {"filtros": filtros},
                          estimate=True, quantidade=1, user=user, origem=origem)
    estimated = int(body.get("tokensEstimados") or 0)
    if not confirm:
        return {"total": None, "cached": False, "estimated_tokens": estimated, "charged_tokens": 0}
    _check_budget(estimated)
    body, tokens = _call("companies/calc_list", "POST", "/companies/calc_list", {"filtros": filtros},
                         estimate=False, quantidade=1, user=user, origem=origem)
    total = int(body.get("totalRegistros") or 0)
    EconodataContagem.objects.update_or_create(chave=key, defaults={"filtros": filtros, "total": total, "consultado_em": now})
    return {"total": total, "cached": False, "estimated_tokens": estimated, "charged_tokens": tokens}


def cached_count(filtros):
    """Contagem já paga para estes filtros (dentro da validade), sem chamar a API."""
    record = EconodataContagem.objects.filter(chave=_filters_key(filtros)).first()
    if record and record.consultado_em >= timezone.now() - timedelta(days=cache_days()):
        return record
    return None


# ---------------------------------------------------------------------------
# Prospecção: empresas parecidas com os melhores clientes (search_list)
# ---------------------------------------------------------------------------

PROSPECT_FIELDS = [
    "cadastro.razaoSocial", "cadastro.nomeFantasia", "cadastro.cidade", "cadastro.uf",
    "cadastro.cnaePrimario", "cadastro.enquadramentoPorte", "cadastro.nivelAtividade",
]
# Medido com estimativa grátis em 06/11/2026: a página de busca tem base de ~1.000
# tokens e ~27 por empresa (10 empresas = 1.270; 20 = 1.535). Página de 20 rende
# mais por token. A contagem (calc_list) custa 100.
PROSPECT_PAGE_SIZE = 20
PROSPECT_APPROX_TOKENS = 1535
COUNT_APPROX_TOKENS = 100


def search_companies(filtros, *, page_size=PROSPECT_PAGE_SIZE, campos=None, confirm=False, user=None, origem="prospeccao"):
    """Uma página de empresas que casam com os filtros. Sem `confirm`, só estima (grátis).

    Toda página é cobrada (base + campos entregues por empresa), por isso a
    página é pequena e só leva os campos que a tela mostra.
    """
    payload = {"filtros": filtros, "pagina": {"tamanho": int(page_size)}, "campos": campos or PROSPECT_FIELDS}
    body, _tokens = _call("companies/search_list", "POST", "/companies/search_list", payload,
                          estimate=True, quantidade=int(page_size), user=user, origem=origem)
    estimated = int(body.get("tokensEstimados") or 0)
    if not confirm:
        return {"estimated_tokens": estimated, "charged_tokens": 0, "total": None, "companies": []}
    _check_budget(estimated)
    body, tokens = _call("companies/search_list", "POST", "/companies/search_list", payload,
                         estimate=False, quantidade=int(page_size), user=user, origem=origem)
    return {
        "estimated_tokens": estimated,
        "charged_tokens": tokens,
        "total": body.get("totalRegistros"),
        "companies": body.get("resultados") or [],
    }


def store_prospects(companies, *, sector_code="", sector_name="", filtros=None, known_roots=()):
    """Guarda as empresas achadas; quem já é cliente (mesma raiz de CNPJ) fica marcado."""
    known_roots = set(known_roots)
    saved = []
    for company in companies:
        cnpj = normalize_cnpj(company.get("cnpj"))
        if not cnpj:
            continue
        cadastro = company.get("cadastro") or {}
        cnae = cadastro.get("cnaePrimario") or {}
        record, _created = EconodataProspect.objects.update_or_create(
            cnpj=cnpj,
            defaults={
                "raiz": cnpj[:8],
                "razao_social": (cadastro.get("razaoSocial") or format_cnpj(cnpj))[:255],
                "nome_fantasia": (cadastro.get("nomeFantasia") or "")[:255],
                "cidade": (cadastro.get("cidade") or "")[:80],
                "uf": (cadastro.get("uf") or "")[:2],
                "cnae_codigo": (cnae.get("codigo") or "")[:20] if isinstance(cnae, dict) else "",
                "cnae_descricao": (cnae.get("descricao") or "")[:255] if isinstance(cnae, dict) else "",
                "enquadramento_porte": (cadastro.get("enquadramentoPorte") or "")[:40],
                "nivel_atividade": (cadastro.get("nivelAtividade") or "")[:20],
                "segmento_codigo": sector_code or "",
                "segmento_nome": (sector_name or "")[:80],
                "filtros": filtros or {},
                "ja_cliente": cnpj[:8] in known_roots,
                "dados": company,
            },
        )
        saved.append(record)
    return saved


# ---------------------------------------------------------------------------
# Leitura para as telas (só cache: nunca chama a API)
# ---------------------------------------------------------------------------

def cached_companies(cnpjs):
    normalized = [cnpj for cnpj in (normalize_cnpj(value) for value in cnpjs) if cnpj]
    return {record.cnpj: record for record in EconodataEmpresa.objects.filter(cnpj__in=normalized)}


def company_card(record, today=None):
    """Dados prontos para exibir de uma empresa em cache."""
    if record is None:
        return None
    today = today or date.today()
    years = None
    if record.data_abertura:
        years = round((today - record.data_abertura).days / 365.25, 1)
    estrategico = (record.dados or {}).get("estrategico") or {}

    def source(key):
        value = estrategico.get(key)
        return (value.get("origem") or "") if isinstance(value, dict) else ""

    return {
        "cnpj": format_cnpj(record.cnpj),
        "found": record.encontrado,
        "situacao": record.situacao,
        "situacao_alerta": record.situacao in INACTIVE_SITUATIONS,
        "nivel_atividade": record.nivel_atividade,
        "cnae_codigo": record.cnae_codigo,
        "cnae_descricao": record.cnae_descricao,
        "tipo_unidade": record.tipo_unidade,
        "enquadramento_porte": record.enquadramento_porte,
        "porte": record.porte,
        "capital_social": record.capital_social,
        "data_abertura": record.data_abertura,
        "anos_abertura": years,
        "faturamento_grupo": record.faturamento_grupo,
        "faturamento_fonte": source("faturamentoMatrizFiliais"),
        "funcionarios_grupo": record.funcionarios_grupo,
        "funcionarios_fonte": source("headcountMatrizFiliais"),
        "qtd_filiais": record.qtd_filiais,
        "consultado_em": record.consultado_em,
        "campos": record.campos or [],
    }


def cnae_division(code):
    """Divisão do CNAE (2 dígitos) a partir do código da Econodata ("C-1012-1/01" -> "10")."""
    match = re.search(r"(\d{2})\d{2}", str(code or ""))
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Seleção dos clientes no ERP (Oracle) para a carga em lote
# ---------------------------------------------------------------------------

ACTIVE_CUSTOMERS_SQL = """
SELECT C.CODCLI AS CLIENTE, C.CGCCPF AS CNPJ, TRIM(C.CODRAM) AS RAMO, SUM(I.VLRBRU) AS FATURAMENTO
FROM E140NFV N
JOIN E140IPV I
  ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
JOIN E085CLI C ON C.CODCLI = N.CODCLI
WHERE N.SITNFV = '2'
  AND N.CODCLI NOT IN (1001, 1002, 1004)
  AND N.DATEMI >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), :offset)
GROUP BY C.CODCLI, C.CGCCPF, TRIM(C.CODRAM)
ORDER BY FATURAMENTO DESC
"""


def active_customers(months=12, without_sector=False):
    """Clientes com venda nos últimos `months` meses, do maior faturamento para o menor.

    Um CNPJ só aparece uma vez (o ERP pode ter mais de um código de cliente para
    o mesmo CNPJ). CPF fica de fora.
    """
    from . import customer_dna as legacy

    rows = legacy._query(ACTIVE_CUSTOMERS_SQL, {"offset": -(max(int(months), 1) - 1)})
    seen = set()
    customers = []
    for row in rows:
        cnpj = normalize_cnpj(row.get("cnpj"))
        if not cnpj or cnpj in seen:
            continue
        if without_sector and (row.get("ramo") or "").strip():
            continue
        seen.add(cnpj)
        customers.append({
            "code": row.get("cliente"),
            "cnpj": cnpj,
            "sector": (row.get("ramo") or "").strip(),
            "revenue": _decimal(row.get("faturamento")) or Decimal("0"),
        })
    return customers


# ---------------------------------------------------------------------------
# Visão de mercado de um cliente (ou grupo) para o DNA: só lê o cache
# ---------------------------------------------------------------------------

def market_view(cnpjs, our_revenue=None, today=None):
    """Junta o que já está em cache para os CNPJs do cliente (o primeiro é o principal).

    `our_revenue` é o nosso faturamento anual com o cliente, comparado com o
    faturamento estimado do grupo (matriz + filiais) quando ele já foi comprado.
    """
    normalized = list(dict.fromkeys(filter(None, (normalize_cnpj(value) for value in cnpjs))))
    records = cached_companies(normalized)
    cards = [company_card(records[cnpj], today) for cnpj in normalized if cnpj in records]
    main = company_card(records.get(normalized[0]), today) if normalized else None
    found = [card for card in cards if card["found"]]
    revenue_card = next((card for card in found if card["faturamento_grupo"]), None)
    group_revenue = revenue_card["faturamento_grupo"] if revenue_card else None
    share = None
    if group_revenue and our_revenue:
        share = (Decimal(str(our_revenue)) / group_revenue) * 100
    invalid = [cnpj for cnpj in normalized if cnpj not in records and not is_valid_cnpj(cnpj)]
    missing = [cnpj for cnpj in normalized if cnpj not in records and cnpj not in invalid]
    profiles = []
    for name, profile in PROFILES.items():
        # O estratégico é do grupo (matriz + filiais): basta o CNPJ principal.
        plan = plan_enrichment(normalized[:1] if name == "estrategico" else normalized, name)
        profiles.append({
            "key": name,
            "label": profile["label"],
            "description": profile["description"],
            "to_fetch": plan["to_fetch"],
            "approx_tokens": plan["approx_tokens"],
        })
    return {
        "configured": is_configured(),
        "main": main,
        "cards": cards,
        "total": len(normalized),
        "cached": len(cards),
        "missing": len(missing),
        "invalid": [format_cnpj(cnpj) for cnpj in invalid],
        "valid": len(normalized) - len(invalid),
        "irregular": [card for card in found if card["situacao_alerta"]],
        "group_revenue": group_revenue,
        "group_revenue_source": revenue_card["faturamento_fonte"] if revenue_card else "",
        "share_pct": share,
        "has_strategic": any("estrategico.faturamentoMatrizFiliais" in card["campos"] for card in found),
        "profiles": profiles,
        "cache_days": cache_days(),
    }
