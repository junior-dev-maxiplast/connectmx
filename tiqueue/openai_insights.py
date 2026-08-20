import json
from urllib import error as urllib_error, request as urllib_request

from .ai_config import get_openai_runtime_config


class OpenAIInsightError(Exception):
    pass


def _request_json(url, api_key, timeout, method="GET", payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib_request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib_request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib_error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("error", {}).get("message") or raw
        except json.JSONDecodeError:
            detail = raw
        raise OpenAIInsightError(f"OpenAI retornou HTTP {exc.code}: {detail[:600]}") from exc
    except urllib_error.URLError as exc:
        raise OpenAIInsightError(f"Não foi possível conectar à OpenAI: {exc.reason}") from exc
    except TimeoutError as exc:
        raise OpenAIInsightError("A solicitação à OpenAI excedeu o tempo limite configurado.") from exc


def _extract_output_text(response):
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    refusals = []
    texts = []
    for output_item in response.get("output") or []:
        if output_item.get("type") != "message":
            continue
        for content_item in output_item.get("content") or []:
            if content_item.get("type") == "output_text" and content_item.get("text"):
                texts.append(content_item["text"])
            elif content_item.get("type") == "refusal":
                refusals.append(content_item.get("refusal") or "Solicitação recusada pelo modelo.")
    if refusals:
        raise OpenAIInsightError(" ".join(refusals))
    if not texts:
        raise OpenAIInsightError("A OpenAI não retornou conteúdo textual estruturado.")
    return "\n".join(texts)


# Formato do DNA do Cliente, usado quando a chamada não informa um schema.
LEGACY_REQUIRED_FIELDS = {
    "executive_summary": str,
    "classification": str,
    "principal_opportunity": str,
    "principal_attention": str,
    "insights": list,
    "recommended_actions": list,
}

JSON_TYPE_MAP = {
    "string": str,
    "array": list,
    "object": dict,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


def _required_fields_from_schema(response_schema):
    """Campos obrigatórios lidos do próprio schema enviado à IA.

    A validação era fixa no formato do DNA do Cliente, então qualquer painel com
    schema próprio (o BI do TI devolve `health` e `principal_risk`, não
    `classification`) era recusado mesmo com a resposta correta.
    """
    schema = (response_schema or {}).get("schema") or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or list(properties.keys())
    fields = {}
    for name in required:
        json_type = (properties.get(name) or {}).get("type")
        expected = JSON_TYPE_MAP.get(json_type)
        if expected is not None:
            fields[name] = expected
    return fields or LEGACY_REQUIRED_FIELDS


def _validate_insight_response(data, response_schema=None):
    required = _required_fields_from_schema(response_schema) if response_schema else LEGACY_REQUIRED_FIELDS
    if not isinstance(data, dict):
        raise OpenAIInsightError("A resposta estruturada da IA não é um objeto JSON.")
    for field, expected_type in required.items():
        if not isinstance(data.get(field), expected_type):
            raise OpenAIInsightError(f"A resposta da IA não contém o campo válido '{field}'.")
    return data


DEFAULT_SYSTEM_PROMPT = (
    "Você é um analista comercial B2B. Produza somente análises sustentadas pelos "
    "dados enviados e respeite rigorosamente o formato JSON solicitado."
)


def generate_customer_insights(ai_payload, runtime_config=None, system_prompt=None):
    """Envia o payload à OpenAI e devolve a análise.

    `system_prompt` permite que outros painéis usem o mesmo motor com o papel
    adequado — o BI do TI não é análise comercial.
    """
    config = runtime_config or get_openai_runtime_config()
    if not config["enabled"]:
        raise OpenAIInsightError("A integração OpenAI está desativada nas configurações.")
    if not config["api_key"]:
        raise OpenAIInsightError("A variável OPENAI_API_KEY ou uma chave protegida não foi configurada.")

    response_schema = ai_payload["response_format"]["json_schema"]
    input_payload = {key: value for key, value in ai_payload.items() if key != "response_format"}
    request_payload = {
        "model": config["model"],
        "store": False,
        "input": [
            {
                "role": "developer",
                "content": [{
                    "type": "input_text",
                    "text": system_prompt or DEFAULT_SYSTEM_PROMPT,
                }],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": json.dumps(input_payload, ensure_ascii=False)}],
            },
        ],
        "reasoning": {"effort": config["reasoning_effort"]},
        "text": {
            "verbosity": "medium",
            "format": {
                "type": "json_schema",
                "name": response_schema["name"],
                "strict": response_schema.get("strict", True),
                "schema": response_schema["schema"],
            },
        },
        "max_output_tokens": config["max_output_tokens"],
    }
    response = _request_json(
        f"{config['base_url']}/responses",
        config["api_key"],
        config["timeout"],
        method="POST",
        payload=request_payload,
    )
    if response.get("status") == "incomplete":
        reason = (response.get("incomplete_details") or {}).get("reason") or "motivo não informado"
        raise OpenAIInsightError(f"A resposta da OpenAI ficou incompleta: {reason}.")

    try:
        structured_response = json.loads(_extract_output_text(response))
    except json.JSONDecodeError as exc:
        raise OpenAIInsightError("A OpenAI retornou um JSON inválido.") from exc
    usage = response.get("usage") or {}
    return {
        "response": _validate_insight_response(structured_response, response_schema),
        "response_id": response.get("id"),
        "model": response.get("model") or config["model"],
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
    }


def test_openai_connection(runtime_config=None):
    config = runtime_config or get_openai_runtime_config()
    if not config["api_key"]:
        raise OpenAIInsightError("Nenhuma chave OpenAI foi configurada.")
    response = _request_json(
        f"{config['base_url']}/models/{config['model']}",
        config["api_key"],
        min(config["timeout"], 30),
    )
    return response.get("id") or config["model"]
