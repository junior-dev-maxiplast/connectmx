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


PORTAL_CHAT_ACTIONS = ("ask", "solution", "resolved", "summary")
PORTAL_CHAT_CAPABILITIES = ("yes", "no", "unknown", "not_applicable")

PORTAL_CHAT_SYSTEM_PROMPT = (
    "Você é o assistente de abertura de chamados do suporte de TI da Maxiplast, "
    "conversando com um colaborador que não é da área técnica. Seus objetivos, nessa "
    "ordem: primeiro, ajudar a pessoa a resolver o problema agora, com uma solução que "
    "já está registrada na base; se ela não conseguir, abrir um chamado bem descrito.\n\n"
    "Base consultada:\n"
    "- Em cada turno, o sistema envia a seção 'Base consultada' com registros reais: "
    "soluções da base de conhecimento (ref KB-n) e chamados já concluídos (ref CH-n).\n"
    "- Só proponha solução que esteja nesses registros e que descreva o mesmo problema. "
    "Nunca invente passos, comandos, caminhos de menu ou causas.\n"
    "- Use no máximo um registro por resposta, o mais parecido. Em source_id, copie o "
    "ref exato (ex.: KB-3). Use null se não usou nenhum.\n"
    "- Se nenhum registro descrever o problema, não sugira solução: faça a pergunta "
    "necessária ou feche o resumo.\n\n"
    "Quando for propor passos, avalie em capability se a pessoa consegue executá-los sozinha:\n"
    "- 'yes': todos os passos são ações de usuário comum, como reiniciar o computador "
    "ou um programa, conferir cabo ou Wi-Fi, fechar e abrir de novo, sair e entrar na conta.\n"
    "- 'no': algum passo exige acesso de administrador ou de TI, como servidor, instalar "
    "ou reinstalar driver ou programa, mudar permissão, cadastro, rede ou configuração de "
    "servidor, mexer em equipamento de infraestrutura. Também 'no' quando a pessoa disser "
    "que não consegue, não tem permissão ou não se sente segura para tentar.\n"
    "- 'unknown': depende de algo que você não sabe, por exemplo se a pessoa é "
    "administradora do próprio computador. Use action 'ask' e pergunte isso de forma "
    "objetiva, com quick_replies.\n"
    "- 'not_applicable': quando não há passos em jogo (perguntas e resumo sem solução).\n\n"
    "Ações:\n"
    "- 'ask': faça uma pergunta curta por vez, só se for essencial (no máximo duas perguntas "
    "na conversa). Nunca pergunte sobre categoria, grupo, tipo ou área responsável. Em "
    "quick_replies, até três respostas curtas que a pessoa pode tocar, ou lista vazia.\n"
    "- 'solution': capability 'yes' e um registro que resolve o caso. Em steps, de 1 a 5 "
    "passos curtos, uma ação por passo, em linguagem simples, sem repetir passos que a "
    "pessoa já disse ter feito. Em question, pergunte se conseguiu fazer os passos e como "
    "ficou. Em quick_replies, use ['Resolveu', 'Não resolveu', 'Não consegui fazer algum passo'].\n"
    "- 'resolved': a pessoa confirmou que o problema está resolvido. Em question, uma "
    "confirmação curta. Não abra chamado nesse caso. Se ela só disse que funcionou agora "
    "sem confirmar o fim do problema, use 'ask' e pergunte se considera resolvido, com "
    "quick_replies ['Sim, resolvido', 'Ainda não'].\n"
    "- 'summary': monta o resumo do chamado. Se capability for 'no', não dê os passos à "
    "pessoa: registre na descrição o problema e que a base já tem uma solução documentada "
    "(cite o título) que exige atendimento técnico, e avise em question que o chamado vai "
    "para a equipe de TI.\n\n"
    "Acompanhamento:\n"
    "- Se a pessoa já tentou os passos e não resolveu, ou não conseguiu fazer algum deles, "
    "não repita os mesmos passos. Proponha outro registro ou outro passo, se houver; senão, "
    "feche com 'summary' e descreva na descrição o que foi tentado e em que passo travou.\n"
    "- Depois de duas rodadas de solução sem sucesso, feche com 'summary'.\n\n"
    "Resumo do chamado (campos de 'summary'):\n"
    "- title tem até 120 caracteres e resume o problema em uma frase.\n"
    "- description reúne o que foi dito: o que aconteceu, desde quando, o impacto e o que "
    "já foi tentado. Texto corrido, não em tópicos.\n"
    "- priority_level: 'high' só quando a pessoa não consegue trabalhar ou afeta várias "
    "pessoas; 'low' quando dá para contornar; 'medium' nos demais casos.\n"
    "- keywords: de 3 a 8 termos curtos e específicos (sistema, equipamento, erro, local). "
    "Nas demais ações, lista vazia.\n"
    "- request_attachment: true só quando um print, uma mensagem de erro ou um log ajudaria "
    "o diagnóstico, e só uma vez por conversa. Nas ações 'solution', 'resolved' e 'summary', "
    "use false.\n"
    "- Se houver imagens, PDFs ou textos anexados, use o conteúdo (por exemplo, a mensagem "
    "de erro visível) e mencione isso na descrição.\n\n"
    "Escreva sempre em português do Brasil, em tom direto e cordial."
)

PORTAL_CHAT_RESPONSE_SCHEMA = {
    "name": "portal_chat_turn",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(PORTAL_CHAT_ACTIONS)},
            "question": {"type": ["string", "null"]},
            "title": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]},
            "priority_level": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
            "request_attachment": {"type": "boolean"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "source_id": {"type": ["string", "null"]},
            "capability": {"type": "string", "enum": list(PORTAL_CHAT_CAPABILITIES)},
            "steps": {"type": "array", "items": {"type": "string"}},
            "quick_replies": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "action",
            "question",
            "title",
            "description",
            "priority_level",
            "request_attachment",
            "keywords",
            "source_id",
            "capability",
            "steps",
            "quick_replies",
        ],
        "additionalProperties": False,
    },
}


def _require_openai(config):
    if not config["enabled"]:
        raise OpenAIInsightError("A integração OpenAI está desativada nas configurações.")
    if not config["api_key"]:
        raise OpenAIInsightError("A variável OPENAI_API_KEY ou uma chave protegida não foi configurada.")


def _structured_response(config, input_messages, schema_definition, max_output_tokens, verbosity="low"):
    request_payload = {
        "model": config["model"],
        "store": False,
        "input": input_messages,
        "reasoning": {"effort": config["reasoning_effort"]},
        "text": {
            "verbosity": verbosity,
            "format": {
                "type": "json_schema",
                "name": schema_definition["name"],
                "strict": schema_definition["strict"],
                "schema": schema_definition["schema"],
            },
        },
        "max_output_tokens": max_output_tokens,
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
        return json.loads(_extract_output_text(response))
    except json.JSONDecodeError as exc:
        raise OpenAIInsightError("A OpenAI retornou um JSON inválido.") from exc


def _clean_chat_list(value, limit):
    return [str(item).strip() for item in (value or []) if str(item).strip()][:limit]


def generate_portal_chat_reply(messages, extra_parts=None, runtime_config=None, knowledge_context=None):
    """Um turno do assistente de abertura de chamado.

    `messages`: lista de {"role": "user"|"assistant", "content": str}, da mais
    antiga para a mais nova. `extra_parts` são itens de conteúdo (imagens, PDF,
    texto de arquivos) anexados ao último turno do usuário. `knowledge_context`
    é o bloco 'Base consultada' deste turno: os únicos registros que a IA pode
    usar como solução.
    """
    config = runtime_config or get_openai_runtime_config()
    _require_openai(config)

    input_messages = [
        {
            "role": "developer",
            "content": [{"type": "input_text", "text": PORTAL_CHAT_SYSTEM_PROMPT}],
        },
    ]
    if knowledge_context:
        input_messages.append(
            {
                "role": "developer",
                "content": [{"type": "input_text", "text": knowledge_context}],
            }
        )
    for message in messages:
        role = "user" if message.get("role") == "user" else "assistant"
        content_type = "input_text" if role == "user" else "output_text"
        text = str(message.get("content") or "")
        if not text:
            continue
        input_messages.append({"role": role, "content": [{"type": content_type, "text": text}]})

    if extra_parts:
        for item in reversed(input_messages):
            if item["role"] == "user":
                item["content"].extend(extra_parts)
                break

    data = _structured_response(
        config,
        input_messages,
        PORTAL_CHAT_RESPONSE_SCHEMA,
        max_output_tokens=min(config["max_output_tokens"], 1000),
    )
    if not isinstance(data, dict) or data.get("action") not in PORTAL_CHAT_ACTIONS:
        raise OpenAIInsightError("A resposta da IA veio fora do formato esperado.")
    action = data["action"]
    if action in {"ask", "solution", "resolved"} and not (data.get("question") or "").strip():
        raise OpenAIInsightError("A IA respondeu sem a mensagem para a pessoa.")
    if action == "summary" and not (data.get("title") or "").strip():
        raise OpenAIInsightError("A IA tentou fechar o resumo sem título.")

    steps = _clean_chat_list(data.get("steps"), limit=5) if action == "solution" else []
    if action == "solution" and (not steps or data.get("capability") != "yes"):
        raise OpenAIInsightError("A IA sugeriu passos sem confirmar que a pessoa consegue executá-los.")
    data["steps"] = steps
    data["quick_replies"] = (
        _clean_chat_list(data.get("quick_replies"), limit=3) if action in {"ask", "solution", "resolved"} else []
    )
    return data


DOCUMENTATION_SYSTEM_PROMPT = (
    "Você documenta soluções de chamados de TI para a base de conhecimento interna "
    "da Maxiplast. Receberá o registro completo de um chamado já resolvido "
    "(descrição, conversa, notas internas, turnos apontados, movimentações, "
    "anexos e avaliação) e a descrição de como o atendente resolveu.\n\n"
    "Regras:\n"
    "- Baseie-se somente no que está no registro e na descrição do atendente. "
    "Não invente passos, comandos ou causas que não aparecem ali.\n"
    "- Se algo não estiver no registro, escreva 'Não informado' no campo.\n"
    "- resolution deve ser um passo a passo numerado e objetivo de como resolver "
    "o problema da próxima vez.\n"
    "- root_cause é a causa provável, citando o que sustenta essa conclusão.\n"
    "- workaround é o contorno temporário usado durante o atendimento, se houver; "
    "caso contrário, 'Não há contorno registrado'.\n"
    "- title tem até 120 caracteres e descreve o problema, não a solução.\n"
    "- tags são palavras-chave separadas por vírgula, para facilitar a busca."
)

DOCUMENTATION_RESPONSE_SCHEMA = {
    "name": "demand_documentation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "trigger": {"type": "string"},
            "impact": {"type": "string"},
            "root_cause": {"type": "string"},
            "resolution": {"type": "string"},
            "workaround": {"type": "string"},
            "tags": {"type": "string"},
        },
        "required": ["title", "trigger", "impact", "root_cause", "resolution", "workaround", "tags"],
        "additionalProperties": False,
    },
}


def generate_demand_documentation(record_text, how_it_was_solved, runtime_config=None):
    """Transforma o registro de um chamado resolvido em documentação de solução."""
    config = runtime_config or get_openai_runtime_config()
    _require_openai(config)

    user_text = (
        "REGISTRO DO CHAMADO\n"
        f"{record_text}\n\n"
        "COMO O ATENDENTE RESOLVEU (descrição dele)\n"
        f"{how_it_was_solved}"
    )
    input_messages = [
        {"role": "developer", "content": [{"type": "input_text", "text": DOCUMENTATION_SYSTEM_PROMPT}]},
        {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
    ]
    data = _structured_response(
        config,
        input_messages,
        DOCUMENTATION_RESPONSE_SCHEMA,
        max_output_tokens=min(config["max_output_tokens"], 2500),
        verbosity="medium",
    )
    if not isinstance(data, dict) or not (data.get("title") or "").strip():
        raise OpenAIInsightError("A IA não conseguiu montar o título da documentação.")
    return {key: str(data.get(key) or "").strip() for key in DOCUMENTATION_RESPONSE_SCHEMA["schema"]["required"]}


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
