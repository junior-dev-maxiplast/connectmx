"""
Pergunte aos dados — item 4 da Central de Decisão: pergunta em linguagem
natural sobre um painel do Dashes, respondida pela mesma engine de IA que já
gera os insights de cada painel.

A fonte é escolhida por quem pergunta (um seletor na tela), não adivinhada
por classificação de texto: só há 3 opções, errar a fonte custaria uma
resposta errada, e adivinhar bem exigiria uma chamada de IA extra — mais uma
unidade de cota gasta por pergunta, sem necessidade.

A IA nunca gera SQL nem toca o ERP/MySQL diretamente: recebe o mesmo payload
determinístico que a análise de insights daquele painel já monta
(`build_romaneio_ai_payload`, `build_travel_ai_payload`, `build_it_ai_payload`
— o mesmo JSON com `deterministic_metrics` e `data_quality_notes`), só que com
a pergunta no lugar das instruções de "escreva um resumo executivo" e um
schema de resposta próprio. Isso é o que garante que uma pergunta maliciosa
("ignore os dados e...") não tem como fazer a IA executar nada — ela só tem
números para ler.

Recorte fixo em `period_key="3"` (3 meses): largo o bastante pra cobrir a
maioria das perguntas sem herdar o custo de "tudo" nem a estreiteza de
"1 mês". Sem seletor de período nesta v1 — quem perguntar por um recorte que
o payload não cobre recebe isso de volta na resposta (a instrução da IA pede
isso explicitamente), não uma estimativa inventada.
"""

from .openai_insights import generate_customer_insights

QA_PERIOD_KEY = "3"

QA_SYSTEM_PROMPT = (
    "Você é um analista de dados que responde perguntas objetivas em português "
    "do Brasil, usando só os números do payload enviado. Nunca invente dado que "
    "não esteja explícito ali. Se o payload não tiver o necessário para "
    "responder (outro recorte, outro painel, outra métrica), diga isso "
    "claramente em vez de estimar ou supor."
)

QA_RESPONSE_SCHEMA = {
    "name": "dashes_data_question_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "data_available": {
                "type": "boolean",
                "description": "false quando o payload não tinha o necessário para responder de verdade.",
            },
        },
        "required": ["answer", "confidence", "data_available"],
    },
}


def _load_and_payload(slug):
    """Carrega o painel no recorte da pergunta e monta o mesmo payload
    determinístico que a análise de insights daquele painel usa.

    Import tardio de propósito, igual a `decision_center._load_dashboard`:
    resolver a função no topo do módulo travaria a referência antes de
    qualquer patch de teste.
    """
    if slug == "romaneios":
        from .romaneio_bi import build_romaneio_ai_payload, load_romaneio_dashboard, romaneio_dashboard_fingerprint
        dashboard = load_romaneio_dashboard(period_key=QA_PERIOD_KEY)
        return build_romaneio_ai_payload(dashboard, romaneio_dashboard_fingerprint(dashboard))
    if slug == "viagens":
        from .travel_bi import build_travel_ai_payload, load_travel_dashboard, travel_dashboard_fingerprint
        dashboard = load_travel_dashboard(period_key=QA_PERIOD_KEY)
        return build_travel_ai_payload(dashboard, travel_dashboard_fingerprint(dashboard))
    if slug == "ti-bi":
        from .it_bi import build_it_ai_payload, load_it_dashboard, it_dashboard_fingerprint
        dashboard = load_it_dashboard(period_key=QA_PERIOD_KEY)
        return build_it_ai_payload(dashboard, it_dashboard_fingerprint(dashboard))
    raise KeyError(f"Fonte sem payload de pergunta: {slug}")


def build_question_payload(slug, question):
    """Payload de insights do painel, com a pergunta no lugar das instruções
    de análise e o schema de resposta trocado pelo de pergunta e resposta."""
    payload = dict(_load_and_payload(slug))
    payload["request_type"] = "dashes_data_question"
    payload["question"] = question
    payload["analysis_instructions"] = [
        "Responda somente à pergunta em `question`, usando os números em "
        "`deterministic_metrics` (e `data_quality_notes`, se relevante).",
        "Cite números na resposta sempre que possível.",
        "Se a pergunta pedir um recorte (filial, período, colaborador etc.) "
        "que não está disponível neste payload, diga isso em vez de estimar.",
        "Escreva em português do Brasil, de forma direta — uma resposta "
        "curta, não um relatório.",
    ]
    payload["response_format"] = {"type": "json_schema", "json_schema": QA_RESPONSE_SCHEMA}
    return payload


def answer_question(slug, question, runtime_config=None):
    """Monta o payload da pergunta e chama a mesma engine de IA dos insights.

    Devolve o mesmo formato de `generate_customer_insights`:
    `{"response": {...}, "response_id", "model", "usage"}`.
    """
    payload = build_question_payload(slug, question)
    return generate_customer_insights(payload, runtime_config=runtime_config, system_prompt=QA_SYSTEM_PROMPT)
