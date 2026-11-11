"""IA (OpenAI) da inteligência de clientes.

Duas tarefas, no mesmo motor do DNA (`openai_insights.generate_customer_insights`,
Responses API com saída estruturada):

* classificar o ramo de atividade de quem está sem ramo no ERP — o modelo só pode
  escolher um dos ramos do próprio ERP (E026RAM) ou "OUTROS";
* escrever a leitura do mapa, da carteira e de um cliente, só com os números
  enviados (resumo, destaques, alertas e ações).
"""

from . import ai_additional
from .ai_config import get_openai_runtime_config
from .openai_insights import OpenAIInsightError, generate_customer_insights

OTHER_SECTOR = "OUTROS"
CLASSIFY_BATCH = 40
LIST_LIMIT = 5

CLASSIFY_PROMPT = (
    "Você classifica clientes B2B de uma indústria de embalagens plásticas flexíveis (bobinas, sacos, "
    "ráfia e big bags) pelo ramo de atividade do cliente. Use o nome, o nome fantasia, a cidade, o que "
    "ele compra e o CNAE quando houver. Escolha só entre os ramos da lista; se nenhum servir, responda "
    "OUTROS. Confiança alta só quando o nome ou o CNAE deixam o ramo claro. Motivo em até 12 palavras, "
    "em português do Brasil. Responda um item para cada cliente enviado, com o mesmo codigo_cliente."
)

_COMMON_RULES = (
    "Escreva em português do Brasil, frases curtas e diretas. Use só os números e nomes do JSON "
    "enviado; não invente dados, não estime o que não foi enviado e não repita a pergunta. Lucro "
    "bruto = faturamento sem impostos − CPV (custo do produto vendido). Margem = lucro bruto ÷ "
    "faturamento sem impostos. Crescimento compara com o mesmo período do ano anterior. Valores em "
    "reais (R$), pesos em kg. No máximo 5 itens por lista."
)

PROMPTS = {
    "map": (
        "Você é analista comercial de uma indústria de embalagens plásticas flexíveis e lê o mapa de "
        "clientes por região, UF e cidade. Aponte onde se vende e se lucra mais, onde a margem é fraca, "
        "onde cresce ou cai e que ações o comercial deve tomar em cada território. " + _COMMON_RULES
    ),
    "portfolio": (
        "Você é analista comercial de uma indústria de embalagens plásticas flexíveis e lê a carteira de "
        "clientes: concentração do lucro, clientes de margem negativa ou baixa, segmentos RFM e clientes "
        "com risco de parar de comprar. Diga o que proteger, o que corrigir e onde crescer. " + _COMMON_RULES
    ),
    "customer": (
        "Você é analista comercial de uma indústria de embalagens plásticas flexíveis e prepara o "
        "representante para falar com um cliente. Resuma a relação, aponte pontos fortes e riscos, "
        "proponha próximos passos e escreva uma abordagem de 2 a 4 frases para o representante usar. "
        + _COMMON_RULES
    ),
    "management": (
        "Você é o controller de uma indústria de embalagens plásticas flexíveis e escreve para a "
        "diretoria. Leia as pontes de faturamento e de lucro bruto (efeitos de clientes perdidos e "
        "novos, produtos, volume, mix, preço e custo), a retenção de receita (NRR e GRR), a matriz de "
        "rentabilidade dos clientes, os grupos de produto, os representantes, a concentração e a "
        "projeção com a carteira a faturar. Escreva um resumo executivo de 2 a 3 frases, o que explica "
        "o resultado (os efeitos que mais pesaram, com valores), os riscos e as decisões que a "
        "diretoria deveria tomar. Os meses são fechados; a projeção é estatística simples, não meta. "
        + _COMMON_RULES
    ),
}

_TEXT_LIST = {"type": "array", "items": {"type": "string"}}

SCHEMAS = {
    "map": {
        "name": "leitura_mapa_clientes",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["resumo", "destaques", "alertas", "acoes"],
            "properties": {"resumo": {"type": "string"}, "destaques": _TEXT_LIST, "alertas": _TEXT_LIST, "acoes": _TEXT_LIST},
        },
    },
    "portfolio": {
        "name": "leitura_carteira_clientes",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["resumo", "destaques", "alertas", "acoes"],
            "properties": {"resumo": {"type": "string"}, "destaques": _TEXT_LIST, "alertas": _TEXT_LIST, "acoes": _TEXT_LIST},
        },
    },
    "management": {
        "name": "leitura_gerencial",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["resumo", "o_que_explica", "riscos", "decisoes"],
            "properties": {"resumo": {"type": "string"}, "o_que_explica": _TEXT_LIST, "riscos": _TEXT_LIST, "decisoes": _TEXT_LIST},
        },
    },
    "customer": {
        "name": "leitura_cliente",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["resumo", "pontos_fortes", "riscos", "proximos_passos", "abordagem"],
            "properties": {
                "resumo": {"type": "string"},
                "pontos_fortes": _TEXT_LIST,
                "riscos": _TEXT_LIST,
                "proximos_passos": _TEXT_LIST,
                "abordagem": {"type": "string"},
            },
        },
    },
}


def _require_config(runtime_config):
    config = dict(runtime_config or get_openai_runtime_config())
    if not config.get("enabled"):
        raise OpenAIInsightError("A integração OpenAI está desativada nas configurações.")
    if not config.get("api_key"):
        raise OpenAIInsightError("A chave da OpenAI não foi configurada.")
    return config


def _add_usage(total, usage):
    for key in total:
        total[key] += int((usage or {}).get(key) or 0)


def classify_schema(sector_names):
    return {
        "name": "classificacao_ramos",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["classificacoes"],
            "properties": {
                "classificacoes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["codigo_cliente", "ramo", "confianca", "motivo"],
                        "properties": {
                            "codigo_cliente": {"type": "integer"},
                            "ramo": {"type": "string", "enum": list(sector_names) + [OTHER_SECTOR]},
                            "confianca": {"type": "string", "enum": ["alta", "media", "baixa"]},
                            "motivo": {"type": "string"},
                        },
                    },
                },
            },
        },
    }


def classify_sectors(customers, sectors, runtime_config=None):
    """Ramo sugerido para cada cliente, em lotes de 40 (raciocínio "low": é uma classificação).

    `customers`: [{"code", "name", "trade_name", "city", "state", "groups", "cnae"}]
    `sectors`: ramos do ERP, [{"code", "name"}].
    """
    config = _require_config(runtime_config)
    config["reasoning_effort"] = "low"
    names = [sector["name"] for sector in sectors]
    code_by_name = {sector["name"]: sector["code"] for sector in sectors}
    results = []
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    model = config.get("model")
    for start in range(0, len(customers), CLASSIFY_BATCH):
        batch = customers[start:start + CLASSIFY_BATCH]
        payload = {
            "ramos": names,
            "clientes": [
                {
                    "codigo_cliente": customer["code"],
                    "nome": customer["name"],
                    "fantasia": customer.get("trade_name") or "",
                    "cidade": f"{customer.get('city') or ''}/{customer.get('state') or ''}",
                    "compra": customer.get("groups") or [],
                    "cnae": customer.get("cnae") or "",
                }
                for customer in batch
            ],
            "response_format": {"json_schema": classify_schema(names)},
        }
        answer = generate_customer_insights(payload, runtime_config=config, system_prompt=CLASSIFY_PROMPT)
        _add_usage(usage, answer.get("usage"))
        model = answer.get("model") or model
        expected = {customer["code"] for customer in batch}
        for item in answer["response"].get("classificacoes") or []:
            code = item.get("codigo_cliente")
            sector_name = item.get("ramo")
            if code not in expected or (sector_name not in code_by_name and sector_name != OTHER_SECTOR):
                continue
            expected.discard(code)
            results.append({
                "code": code,
                "sector_code": code_by_name.get(sector_name, ""),
                "sector_name": sector_name,
                "confidence": item.get("confianca") if item.get("confianca") in ("alta", "media", "baixa") else "baixa",
                "reason": (item.get("motivo") or "").strip()[:240],
            })
    return {"results": results, "usage": usage, "model": model}


def generate_insight(scope, data, runtime_config=None, extra=None):
    """Leitura da IA para `scope` ("map", "portfolio", "customer" ou "management") a partir de `data`.

    `extra` é o campo "Adicional" da tela: uma pergunta a mais, respondida só com os mesmos dados.
    """
    config = _require_config(runtime_config)
    payload = ai_additional.extend({"dados": data, "response_format": {"json_schema": SCHEMAS[scope]}}, extra)
    answer = generate_customer_insights(payload, runtime_config=config, system_prompt=ai_additional.prompt(PROMPTS[scope], extra))
    response = answer["response"]
    for key, value in list(response.items()):
        if isinstance(value, list):
            response[key] = [str(text).strip() for text in value if str(text).strip()][:LIST_LIMIT]
        elif isinstance(value, str):
            response[key] = value.strip()
    return {"response": ai_additional.attach(response, extra), "model": answer.get("model"), "usage": answer.get("usage") or {}}
