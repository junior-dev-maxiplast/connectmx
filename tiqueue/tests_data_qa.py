"""Pergunte aos dados (item 4 da Central de Decisão): montagem do payload da
pergunta e o ciclo completo da view.

A OpenAI está ativa neste ambiente de dev (ver memória do projeto,
`openai-ativa-em-dev`) — `answer_question`/`generate_customer_insights` são
sempre mockados aqui, nunca chamados de verdade, mesmo padrão de
`tests_romaneio_bi.py`.
"""

from unittest.mock import patch

from accounts.models import DashesAiUsage
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import data_qa
from .models import Dashboard, DashboardAccess, DashQuestion

User = get_user_model()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        password="secret123",
        userId=extra.pop("userId", username[:18]),
        email=extra.pop("email", f"{username}@example.com"),
        **extra,
    )


FAKE_INSIGHTS_PAYLOAD = {
    "schema_version": "1.0",
    "request_type": "pallet_counting_productivity",
    "scope": {"period": "Últimos 3 meses"},
    "deterministic_metrics": {"volume": {"records": 300}},
    "data_quality_notes": ["nota de qualidade"],
    "analysis_instructions": ["instrução original de insights, não de pergunta"],
    "response_format": {"type": "json_schema", "json_schema": {"name": "pallet_counting_productivity_insights"}},
}


class BuildQuestionPayloadTests(TestCase):
    def test_overrides_instructions_and_schema_but_keeps_the_data(self):
        with patch.object(data_qa, "_load_and_payload", return_value=dict(FAKE_INSIGHTS_PAYLOAD)):
            payload = data_qa.build_question_payload("romaneios", "quantos registros no total?")

        self.assertEqual(payload["question"], "quantos registros no total?")
        self.assertEqual(payload["request_type"], "dashes_data_question")
        self.assertEqual(payload["response_format"]["json_schema"]["name"], "dashes_data_question_answer")
        self.assertNotEqual(payload["analysis_instructions"], FAKE_INSIGHTS_PAYLOAD["analysis_instructions"])
        # Os números continuam lá - só as instruções e o schema mudam.
        self.assertEqual(payload["deterministic_metrics"], FAKE_INSIGHTS_PAYLOAD["deterministic_metrics"])
        self.assertEqual(payload["data_quality_notes"], FAKE_INSIGHTS_PAYLOAD["data_quality_notes"])

    def test_unknown_source_raises(self):
        with self.assertRaises(KeyError):
            data_qa.build_question_payload("nao-existe", "pergunta")


class AskDataQuestionViewTests(TestCase):
    def setUp(self):
        self.url = reverse("dashesAskDataQuestion")
        self.romaneios = Dashboard.objects.get(slug="romaneios")
        self.dna = Dashboard.objects.get(slug="customer-dna")
        self.user = make_user("gestor_pergunta")
        DashboardAccess.objects.create(user=self.user, dashboard=self.romaneios)
        DashboardAccess.objects.create(user=self.user, dashboard=self.dna)
        self.client.force_login(self.user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def _post(self, source="romaneios", question="quantos registros no total?"):
        return self.client.post(self.url, {"source": source, "question": question})

    def test_rejects_a_source_without_access(self):
        response = self._post(source="ti-bi")
        self.assertEqual(response.status_code, 400)

    def test_rejects_a_source_without_qa_support(self):
        # customer-dna está liberado pro usuário, mas não entra em BI_SOURCES
        # (a análise ali é por cliente, não por painel inteiro).
        response = self._post(source="customer-dna")
        self.assertEqual(response.status_code, 400)

    def test_rejects_an_empty_question(self):
        response = self._post(question="   ")
        self.assertEqual(response.status_code, 400)

    def test_successful_answer_is_persisted_and_consumes_quota(self):
        fake_result = {
            "response": {"answer": "300 registros no período.", "confidence": "high", "data_available": True},
            "response_id": "resp_1", "model": "gpt-x",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
        with patch("tiqueue.views.get_openai_runtime_config",
                   return_value={"enabled": True, "api_key_configured": True}), \
             patch("tiqueue.views.answer_question", return_value=fake_result) as answer_mock:
            response = self._post()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["answer"], "300 registros no período.")
        self.assertEqual(payload["confidence"], "high")
        answer_mock.assert_called_once_with("romaneios", "quantos registros no total?", runtime_config={
            "enabled": True, "api_key_configured": True,
        })

        question = DashQuestion.objects.get()
        self.assertEqual(question.status, DashQuestion.STATUS_COMPLETED)
        self.assertEqual(question.created_by, self.user)
        self.assertEqual(question.answer, "300 registros no período.")
        self.assertEqual(DashesAiUsage.objects.get(user=self.user).request_count, 1)

    def test_ai_failure_refunds_the_quota_and_records_the_error(self):
        with patch("tiqueue.views.get_openai_runtime_config",
                   return_value={"enabled": True, "api_key_configured": True}), \
             patch("tiqueue.views.answer_question", side_effect=RuntimeError("timeout")):
            response = self._post()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(DashesAiUsage.objects.get(user=self.user).request_count, 0)
        question = DashQuestion.objects.get()
        self.assertEqual(question.status, DashQuestion.STATUS_ERROR)
        self.assertIn("timeout", question.ai_error)

    def test_respects_the_daily_quota(self):
        self.user.dashes_ai_daily_limit = 0
        self.user.save(update_fields=["dashes_ai_daily_limit"])
        with patch("tiqueue.views.answer_question") as answer_mock:
            response = self._post()
        self.assertEqual(response.status_code, 429)
        answer_mock.assert_not_called()

    def test_openai_disabled_is_reported_before_calling_the_ai(self):
        with patch("tiqueue.views.get_openai_runtime_config",
                   return_value={"enabled": False, "api_key_configured": False}), \
             patch("tiqueue.views.answer_question") as answer_mock:
            response = self._post()
        self.assertEqual(response.status_code, 409)
        answer_mock.assert_not_called()
        self.assertFalse(DashQuestion.objects.exists())
