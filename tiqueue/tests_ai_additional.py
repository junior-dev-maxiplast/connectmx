"""Campo "Adicional" das análises de IA: limpeza do texto, schema estrito e o caminho da pergunta."""

from unittest import mock

from django.test import SimpleTestCase

from . import ai_additional, intel_ai

SCHEMA = {
    "name": "leitura",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["resumo"],
        "properties": {"resumo": {"type": "string"}},
    },
}


class AdditionalHelperTests(SimpleTestCase):
    def test_clean_keeps_one_line_and_500_characters(self):
        self.assertEqual(ai_additional.clean("  Qual  produto\n gerou\tperdas?  "), "Qual produto gerou perdas?")
        self.assertEqual(len(ai_additional.clean("a" * 900)), 500)
        self.assertEqual(ai_additional.clean(None), "")
        self.assertEqual(ai_additional.clean("ok\x00\x07"), "ok")

    def test_extend_adds_the_question_and_a_required_answer_field(self):
        payload = {"dados": {"x": 1}, "response_format": {"json_schema": SCHEMA}}
        extended = ai_additional.extend(payload, "Qual cliente caiu mais?")
        schema = extended["response_format"]["json_schema"]["schema"]
        self.assertEqual(extended["instrucao_adicional"], "Qual cliente caiu mais?")
        self.assertIn("resposta_adicional", schema["required"])
        self.assertEqual(schema["properties"]["resposta_adicional"], {"type": "string"})
        self.assertFalse(schema["additionalProperties"])
        # O payload guardado no recorte não muda.
        self.assertNotIn("resposta_adicional", SCHEMA["schema"]["properties"])
        self.assertNotIn("instrucao_adicional", payload)

    def test_without_question_nothing_changes(self):
        payload = {"dados": {}, "response_format": {"json_schema": SCHEMA}}
        self.assertIs(ai_additional.extend(payload, "   "), payload)
        self.assertEqual(ai_additional.prompt("Papel.", ""), "Papel.")
        self.assertIsNone(ai_additional.prompt(None, ""))
        self.assertEqual(ai_additional.attach({"resumo": "x", "resposta_adicional": ""}, ""), {"resumo": "x"})

    def test_prompt_and_attach_with_question(self):
        self.assertTrue(ai_additional.prompt("Papel.", "Pergunta").startswith("Papel. Instrução adicional"))
        self.assertTrue(ai_additional.prompt(None, "Pergunta").startswith(ai_additional.DEFAULT_SYSTEM_PROMPT))
        answer = ai_additional.attach({"resumo": "x", "resposta_adicional": " O grupo A. "}, "Qual grupo?")
        self.assertEqual(answer["pergunta_adicional"], "Qual grupo?")
        self.assertEqual(answer["resposta_adicional"], "O grupo A.")
        empty = ai_additional.attach({"resumo": "x"}, "Qual grupo?")
        self.assertEqual(empty["resposta_adicional"], "A IA não respondeu à instrução adicional.")

    def test_generate_insight_sends_the_question_with_the_screen_data(self):
        answer = {"response": {"resumo": "Resumo.", "destaques": [], "alertas": [], "acoes": [], "resposta_adicional": "O PR."},
                  "model": "gpt-teste", "usage": {"total_tokens": 9}}
        config = {"enabled": True, "api_key": "chave-de-teste"}
        with mock.patch.object(intel_ai, "generate_customer_insights", return_value=answer) as call:
            result = intel_ai.generate_insight("map", {"ufs": []}, runtime_config=config, extra="Qual UF mais caiu?")
        payload = call.call_args.args[0]
        self.assertEqual(payload["instrucao_adicional"], "Qual UF mais caiu?")
        self.assertEqual(payload["dados"], {"ufs": []})
        self.assertIn("resposta_adicional", payload["response_format"]["json_schema"]["schema"]["required"])
        self.assertIn("Instrução adicional", call.call_args.kwargs["system_prompt"])
        self.assertEqual(result["response"]["pergunta_adicional"], "Qual UF mais caiu?")
        self.assertEqual(result["response"]["resposta_adicional"], "O PR.")
        # O schema do escopo continua sem o campo para as próximas chamadas.
        self.assertNotIn("resposta_adicional", intel_ai.SCHEMAS["map"]["schema"]["properties"])
