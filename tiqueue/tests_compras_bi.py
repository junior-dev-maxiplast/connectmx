"""BI de Compras: montagem do painel e ciclo de indicadores.

O ERP não entra nestes testes — a conexão Oracle (`_query_many`) e os
catálogos de filtro são substituídos por dados controlados. O que se fixa
aqui é a leitura que o painel faz do recorte: as três abas (pagamento,
entrega, preço) e o ciclo de indicadores/IA/PDF, igual ao BI de Viagens.
"""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from . import compras_bi
from .models import ComprasBiInsightSnapshot, Dashboard, DashboardAccess


User = get_user_model()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        password="secret123",
        userId=extra.pop("userId", username[:18]),
        email=extra.pop("email", f"{username}@example.com"),
        **extra,
    )


FORNECEDORES = [{"key": "10", "code": 10, "name": "Fornecedor Teste", "label": "10 - Fornecedor Teste", "total": 4}]
BRANCHES = [{"key": "1-1", "empresa": 1, "filial": 1, "label": "Filial 1", "total": 4}]
FAMILIAS = [{"key": "BOB", "code": "BOB", "name": "BOBINA", "label": "BOB - BOBINA", "total": 4}]

FAKE_PRAZO_DATA = {
    "totals": [{
        "fornecedores": 2, "notas": 3, "ordens": 4, "produtos": 5, "linhas": 6,
        "prazo_medio": 32.5, "prazo_meta": 28, "linhas_com_prazo": 5,
        "inconsistencias": 1, "pagos": 4,
    }],
    "monthly": [
        {"competencia": "2026-07", "linhas": 3, "prazo_medio": 30, "prazo_meta": 28},
        {"competencia": "2026-08", "linhas": 3, "prazo_medio": 35, "prazo_meta": 28},
    ],
    "familias": [{"rotulo": "BOBINA", "linhas": 4, "prazo_medio": 30}],
    "origens": [{"rotulo": "100 - Nacional", "linhas": 4, "prazo_medio": 30}],
    "fornecedores": [{"codigo": 10, "nome": "Fornecedor Teste", "linhas": 4, "ordens": 2, "prazo_medio": 30}],
    "detalhe": [{
        "ordem": 100, "nota": "555", "ds_fornecedor": "Fornecedor Teste", "ds_filial": "Matriz",
        "ds_produto": "Produto X", "data_geracao": date(2026, 7, 1), "dt_entrega": date(2026, 7, 10),
        "dt_emissao_nota": date(2026, 7, 10), "dt_pagamento": date(2026, 8, 2),
        "dias_entrega": 9, "dias_pagamento_real": 32, "situacao_pagamento": "Pago",
    }],
}

FAKE_PRECO_DATA = {
    "totals": [{
        "fornecedores": 2, "produtos": 3, "ordens": 4, "sequencias": 5,
        "qtd_cotada": 1200, "total_cotado": 36000, "min_unit": 25, "media_unit": 30, "max_unit": 35,
    }],
    "monthly": [
        {"competencia": "2026-07", "preco_medio": 28, "total": 14000, "quantidade": 500},
        {"competencia": "2026-08", "preco_medio": 32, "total": 22000, "quantidade": 700},
    ],
    "fornecedores": [{"codigo": 10, "nome": "Fornecedor Teste", "quantidade": 1200, "total": 36000, "preco_medio": 30}],
    "produtos": [{"codigo": 500, "nome": "Produto X", "quantidade": 1200, "total": 36000, "preco_medio": 30,
                  "min_unit": 25, "max_unit": 35}],
    "detalhe": [{
        "ordem": 100, "seq_ordem": 1, "ds_fornecedor": "Fornecedor Teste", "ds_produto": "Produto X",
        "quantidade": 300, "valor_unitario": 30, "valor_total": 9000, "data_geracao": date(2026, 7, 1),
    }],
}


def fake_query_many_prazo(statements, params):
    return {name: FAKE_PRAZO_DATA.get(name, []) for name in statements}


def fake_query_many_preco(statements, params):
    return {name: FAKE_PRECO_DATA.get(name, []) for name in statements}


class ComprasDashboardBuildTests(TestCase):
    """Monta o painel das três abas a partir de linhas controladas."""

    def _load(self, view_key, fake):
        with patch.object(compras_bi, "_query_many", side_effect=fake):
            return compras_bi.load_compras_dashboard(
                period_key="12", view_key=view_key,
                fornecedores_available=FORNECEDORES, branches_available=BRANCHES, familias_available=FAMILIAS,
            )

    def test_pagamento_tab_compares_real_against_contracted_term(self):
        dashboard = self._load("pagamento", fake_query_many_prazo)

        self.assertEqual(dashboard["scope"]["view"]["key"], "pagamento")
        self.assertEqual(dashboard["metrics"]["prazo_medio"], 32.5)
        self.assertEqual(dashboard["metrics"]["prazo_meta"], 28)
        self.assertEqual(dashboard["metrics"]["variacao_dias"], 4.5)
        self.assertIsNotNone(dashboard["metrics"]["pagos_pct"])

    def test_entrega_tab_has_no_contracted_meta(self):
        dashboard = self._load("entrega", fake_query_many_prazo)

        self.assertEqual(dashboard["scope"]["view"]["key"], "entrega")
        self.assertIsNone(dashboard["metrics"]["prazo_meta_display"])
        self.assertIsNone(dashboard["metrics"]["variacao_dias"])
        self.assertIsNone(dashboard["metrics"]["pagos_pct"])

    def test_preco_tab_reports_min_average_and_max_unit_price(self):
        dashboard = self._load("preco", fake_query_many_preco)

        self.assertEqual(dashboard["scope"]["view"]["key"], "preco")
        self.assertEqual(dashboard["metrics"]["preco_min"], 25)
        self.assertEqual(dashboard["metrics"]["preco_medio"], 30)
        self.assertEqual(dashboard["metrics"]["preco_max"], 35)
        self.assertEqual(len(dashboard["produtos"]), 1)

    def test_monthly_series_gets_axis_ticks_and_bar_heights(self):
        dashboard = self._load("pagamento", fake_query_many_prazo)

        self.assertEqual(len(dashboard["monthly"]), 2)
        self.assertTrue(all("height_pct" in item for item in dashboard["monthly"]))
        self.assertTrue(any(item["tick"] for item in dashboard["monthly"]))

    def test_unknown_filter_values_fall_back_instead_of_raising(self):
        dashboard = self._load("rota-invalida", fake_query_many_prazo)
        # Aba desconhecida cai para "pagamento", o default do resolver.
        self.assertEqual(dashboard["scope"]["view"]["key"], "pagamento")

    def test_fingerprint_changes_with_the_numbers(self):
        dashboard = self._load("pagamento", fake_query_many_prazo)
        first = compras_bi.compras_dashboard_fingerprint(dashboard)
        dashboard["metrics"]["prazo_medio"] = 99
        second = compras_bi.compras_dashboard_fingerprint(dashboard)
        self.assertNotEqual(first, second)

    def test_ai_payload_declares_the_prazo_data_quality_notes(self):
        dashboard = self._load("pagamento", fake_query_many_prazo)
        fingerprint = compras_bi.compras_dashboard_fingerprint(dashboard)
        payload = compras_bi.build_compras_ai_payload(dashboard, fingerprint)

        self.assertIn("by_familia", payload["deterministic_metrics"])
        self.assertTrue(any("nota de entrada" in note for note in payload["data_quality_notes"]))

    def test_ai_payload_for_preco_carries_product_ranking_instead(self):
        dashboard = self._load("preco", fake_query_many_preco)
        fingerprint = compras_bi.compras_dashboard_fingerprint(dashboard)
        payload = compras_bi.build_compras_ai_payload(dashboard, fingerprint)

        self.assertIn("top_produtos", payload["deterministic_metrics"])
        self.assertNotIn("by_familia", payload["deterministic_metrics"])


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class ComprasBiPageTests(TestCase):
    def setUp(self):
        self.dashboard = Dashboard.objects.get(slug="compras")
        self.url = reverse("dashesComprasBiPage")
        self.user = make_user("compradora")
        DashboardAccess.objects.create(user=self.user, dashboard=self.dashboard)

    def sign_in(self, user=None):
        self.client.force_login(user or self.user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def stubbed_erp(self, fake=fake_query_many_prazo):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.object(compras_bi, "_query_many", side_effect=fake))
        stack.enter_context(patch.object(compras_bi, "list_fornecedores", return_value=FORNECEDORES))
        stack.enter_context(patch.object(compras_bi, "list_branches", return_value=BRANCHES))
        stack.enter_context(patch.object(compras_bi, "list_familias", return_value=FAMILIAS))
        return stack

    def test_migration_registers_the_panel_in_the_catalog(self):
        self.assertEqual(self.dashboard.url_name, "dashesComprasBiPage")
        self.assertTrue(self.dashboard.is_active)

    def test_panel_requires_explicit_permission(self):
        outsider = make_user("semacesso")
        other = Dashboard.objects.get(slug="viagens")
        DashboardAccess.objects.create(user=outsider, dashboard=other)
        self.sign_in(outsider)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "tiqueue/dashes_denied.html")

    def test_page_renders_the_payment_tab_by_default(self):
        self.sign_in()
        with self.stubbed_erp():
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tiqueue/compras_bi.html")
        self.assertIsNone(response.context["data_error"])
        self.assertContains(response, "Prazo de pagamento")

    def test_price_tab_renders_through_the_aba_filter(self):
        self.sign_in()
        with self.stubbed_erp(fake_query_many_preco):
            response = self.client.get(self.url + "?aba=preco")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["dashboard"]["scope"]["view"]["key"], "preco")

    def test_erp_failure_shows_a_message_instead_of_a_stack_trace(self):
        self.sign_in()
        with patch.object(compras_bi, "list_fornecedores", side_effect=RuntimeError("ORA-12541")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("ORA-12541", response.context["data_error"])
        self.assertIsNone(response.context["dashboard"])

    def test_prepare_stores_one_snapshot_per_scope(self):
        self.sign_in()
        with self.stubbed_erp():
            first = self.client.post(reverse("comprasBiPrepareInsights"),
                                      {"periodo": "12", "aba": "pagamento", "fornecedor": "all",
                                       "filial": "all", "familia": "all"})
            second = self.client.post(reverse("comprasBiPrepareInsights"),
                                       {"periodo": "6", "aba": "pagamento", "fornecedor": "all",
                                        "filial": "all", "familia": "all"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        # Recortes diferentes não se sobrescrevem: cada um guarda o seu.
        self.assertEqual(ComprasBiInsightSnapshot.objects.count(), 2)
        snapshot = ComprasBiInsightSnapshot.objects.get(period_key="12")
        self.assertEqual(snapshot.status, ComprasBiInsightSnapshot.STATUS_PREPARED)
        self.assertTrue(snapshot.ai_payload)
        self.assertEqual(snapshot.created_by, self.user)

    def test_prepare_reports_an_erp_failure_as_503(self):
        self.sign_in()
        with patch("tiqueue.views.list_compras_fornecedores", side_effect=RuntimeError("ORA-12541")):
            response = self.client.post(reverse("comprasBiPrepareInsights"),
                                        {"periodo": "12", "aba": "pagamento", "fornecedor": "all",
                                         "filial": "all", "familia": "all"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")

    def test_ai_is_refused_before_the_indicators_exist(self):
        self.sign_in()

        response = self.client.post(reverse("comprasBiRequestAiInsights"),
                                    {"periodo": "12", "aba": "pagamento", "fornecedor": "all",
                                     "filial": "all", "familia": "all"})

        self.assertEqual(response.status_code, 409)

    def test_ai_respects_the_daily_quota(self):
        # Cota estourada nem chega a chamar a OpenAI — o teto é por usuário e
        # vale para todos os painéis do Dashes.
        self.user.dashes_ai_daily_limit = 0
        self.user.save(update_fields=["dashes_ai_daily_limit"])
        self.sign_in()
        ComprasBiInsightSnapshot.objects.create(
            view_key="pagamento", period_key="12", fornecedor_key="all",
            filial_key="all", familia_key="all",
            source_fingerprint="fp", ai_payload={"x": 1},
        )

        with patch("tiqueue.views.generate_customer_insights") as ai_call:
            response = self.client.post(reverse("comprasBiRequestAiInsights"),
                                        {"periodo": "12", "aba": "pagamento", "fornecedor": "all",
                                         "filial": "all", "familia": "all"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "ai_daily_limit")
        ai_call.assert_not_called()

    def test_pdf_is_refused_before_the_indicators_exist(self):
        self.sign_in()

        response = self.client.get(
            reverse("comprasBiExportPdf")
            + "?periodo=12&aba=pagamento&fornecedor=all&filial=all&familia=all"
        )

        self.assertEqual(response.status_code, 409)
