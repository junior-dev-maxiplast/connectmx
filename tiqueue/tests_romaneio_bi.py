"""BI de Romaneios: leitura de indicadores e ciclo de análise de IA.

O ERP não entra nestes testes — a conexão Oracle é substituída por linhas
controladas. O ambiente de desenvolvimento não teve acesso de rede ao Oracle
de produção (ver docstring de `romaneio_bi.py`), então as linhas fixadas aqui
são plausíveis, não uma calibração contra a base real: o que se garante é que
o painel lê corretamente o formato que a consulta devolve, não que os números
batem com a operação.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import romaneio_bi
from .models import Dashboard, DashboardAccess, RomaneioBiInsightSnapshot


User = get_user_model()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        password="secret123",
        userId=extra.pop("userId", username[:18]),
        email=extra.pop("email", f"{username}@example.com"),
        **extra,
    )


BRANCHES = [{"key": "1-1", "empresa": 1, "filial": 1, "label": "Filial 1", "total": 10}]
MATRICULAS = [
    {"key": "501", "label": "Matrícula 501", "nome": "", "total": 6},
    {"key": "502", "label": "Matrícula 502", "nome": "", "total": 4},
]
# Só a 501 tem nome no cadastro (R034FUN) — simula um colaborador desligado
# ou uma matrícula digitada errado, que continua aparecendo sem nome.
EMPLOYEE_NAMES = {501: "Fulana de Tal"}

# Recorte mínimo que exercita cada agregação sem precisar do banco.
FAKE_DATA = {
    "totals": [{
        "registros": 10, "volumes": 40, "peso": 4000,
        "colaboradores": 2, "pallets": 5, "sem_peso": 1, "sem_volume": 0,
    }],
    "series": [
        {"competencia": "2026-08", "tipreg": 1, "total": 3},
        {"competencia": "2026-08", "tipreg": 4, "total": 2},
        {"competencia": "2026-09", "tipreg": 1, "total": 5},
    ],
    "by_stage": [
        {"tipreg": 1, "estagio": "Separar", "total": 8, "volumes": 32, "peso": 3200},
        {"tipreg": 4, "estagio": "Carregar", "total": 2, "volumes": 8, "peso": 800},
    ],
    "by_branch": [{"empresa": 1, "filial": 1, "total": 10}],
    "ranking": [
        {"matricula": 501, "registros": 6, "volumes": 24, "peso": 2400, "dias_trabalhados": 3, "pallets": 3},
        {"matricula": 502, "registros": 4, "volumes": 16, "peso": 1600, "dias_trabalhados": 2, "pallets": 2},
    ],
    "funnel": [{
        "total_pallets": 5, "completos": 2,
        "etapa_1": 1, "etapa_2": 1, "etapa_3": 1, "etapa_4": 2,
        "lead_time_medio": 1.5, "lead_time_base": 2,
    }],
    "top_workdays": [
        {"matricula": 501, "dias": 3, "registros": 6},
        {"matricula": 502, "dias": 2, "registros": 4},
    ],
}


def fake_query_many(statements, params):
    return {name: FAKE_DATA.get(name, []) for name in statements}


class RomaneioStageVocabularyTests(TestCase):
    """O CASE de rótulos da consulta original vive em Python — o teste garante
    que os quatro estágios continuam batendo com o app mobile e o servidor de
    gravação (hqbooking/views.py, ROMANEIO_RECORD_TYPE_LABELS)."""

    def test_stage_labels_match_the_mobile_app_and_the_writer(self):
        self.assertEqual(
            romaneio_bi.STAGE_LABELS,
            {1: "Separar", 2: "Guardar", 3: "Paletizar", 4: "Carregar"},
        )

    def test_unmapped_stage_code_falls_back_to_outro(self):
        self.assertIn("ELSE 'Outro'", romaneio_bi._BASE_SQL)
        self.assertEqual(romaneio_bi.STAGE_DEFAULT_LABEL, "Outro")


class RomaneioDashboardTests(TestCase):
    def load(self, period="12", branch="all", stage="all", matricula="all"):
        with patch.object(romaneio_bi, "_query_many", side_effect=fake_query_many), \
             patch.object(romaneio_bi, "_employee_names", return_value=EMPLOYEE_NAMES):
            return romaneio_bi.load_romaneio_dashboard(
                period, branch, stage, matricula,
                branches_available=BRANCHES, matriculas_available=MATRICULAS,
            )

    def test_totals_read_straight_from_the_aggregate_row(self):
        dashboard = self.load()
        metrics = dashboard["metrics"]
        self.assertEqual(metrics["records"], 10)
        self.assertEqual(metrics["volumes"], 40)
        self.assertEqual(metrics["weight"], 4000.0)
        self.assertEqual(metrics["employees"], 2)
        self.assertEqual(metrics["pallets"], 5)
        # 10 registros / 2 colaboradores = 5.0, formatado com vírgula decimal.
        self.assertEqual(metrics["records_per_employee_display"], "5,0")
        self.assertEqual(metrics["missing_weight_pct"], 10.0)

    def test_stage_breakdown_is_ordered_by_the_pallet_flow(self):
        dashboard = self.load()
        labels = [item["label"] for item in dashboard["by_stage"]]
        # A consulta devolveu Separar depois de Carregar; o painel reordena
        # para a ordem do fluxo (1 a 4), não a ordem que o banco escolheu.
        self.assertEqual(labels, ["Separar", "Carregar"])

    def test_series_is_grouped_by_competencia_and_stacked_by_stage(self):
        dashboard = self.load()
        series = {item["competencia"]: item for item in dashboard["series"]}
        self.assertEqual(set(series), {"2026-08", "2026-09"})
        august = series["2026-08"]
        self.assertEqual(august["total"], 5)
        self.assertEqual(august["stages"]["1"], 3)
        self.assertEqual(august["stages"]["4"], 2)
        # As fatias empilhadas somam 100% da barra.
        self.assertAlmostEqual(
            august["stage_shares"]["1"] + august["stage_shares"]["4"], 100.0, places=1
        )

    def test_ranking_keeps_the_database_order(self):
        dashboard = self.load()
        self.assertEqual([item["matricula"] for item in dashboard["ranking"]], [501, 502])
        self.assertEqual(dashboard["ranking_total_employees"], 2)
        # 6 de 10 registros do recorte.
        self.assertEqual(dashboard["ranking"][0]["share_pct"], 60.0)

    def test_ranking_carries_the_employee_name_from_the_erp_registry(self):
        # 501 está no cadastro (R034FUN); 502 não — matrícula desligada ou
        # digitada errado no aparelho, então aparece só pelo número mesmo.
        dashboard = self.load()
        ranking = {item["matricula"]: item for item in dashboard["ranking"]}
        self.assertEqual(ranking[501]["nome"], "Fulana de Tal")
        self.assertEqual(ranking[502]["nome"], "")

    def test_unknown_filter_values_fall_back_to_all(self):
        dashboard = self.load(branch="9-9", stage="9", matricula="999999")
        scope = dashboard["scope"]
        self.assertEqual(scope["branch"]["key"], "all")
        self.assertEqual(scope["stage"]["key"], "all")
        # Matrícula fora do catálogo dos "regulares" ainda é aceita se for um
        # código válido digitado direto na URL.
        self.assertEqual(scope["matricula"]["key"], "999999")
        self.assertEqual(scope["matricula"]["code"], 999999)


class RomaneioDeepAnalyticsTests(TestCase):
    def test_funnel_and_top_workdays_are_computed_from_the_whole_pallet_history(self):
        with patch.object(romaneio_bi, "_query_many", side_effect=fake_query_many), \
             patch.object(romaneio_bi, "list_branches", return_value=BRANCHES), \
             patch.object(romaneio_bi, "list_matriculas", return_value=MATRICULAS), \
             patch.object(romaneio_bi, "_employee_names", return_value=EMPLOYEE_NAMES):
            deep = romaneio_bi.compute_deep_analytics("12", "all", "all", "all")

        funnel = deep["funnel"]
        self.assertEqual(funnel["total"], 5)
        self.assertEqual(funnel["completed"], 2)
        self.assertEqual(funnel["completed_pct"], 40.0)
        self.assertEqual(funnel["lead_time_base"], 2)

        top = deep["top_workdays"]
        self.assertEqual(top[0]["matricula"], 501)
        self.assertEqual(top[0]["nome"], "Fulana de Tal")
        self.assertEqual(top[1]["nome"], "")
        self.assertEqual(top[0]["share_pct"], 100.0)
        self.assertEqual(top[1]["share_pct"], round(2 / 3 * 100, 1))

    def test_stage_filter_is_ignored_in_the_funnel(self):
        # Filtrar por etapa quebraria a contagem de etapas distintas por
        # pallet: o funil sempre olha o ciclo inteiro, independente da tela.
        with patch.object(romaneio_bi, "_query_many", side_effect=fake_query_many) as query_many, \
             patch.object(romaneio_bi, "list_branches", return_value=BRANCHES), \
             patch.object(romaneio_bi, "list_matriculas", return_value=MATRICULAS), \
             patch.object(romaneio_bi, "_employee_names", return_value=EMPLOYEE_NAMES):
            romaneio_bi.compute_deep_analytics("12", "all", "1", "all")

        _statements, params = query_many.call_args[0]
        self.assertNotIn("stage", params["funnel"])


class RomaneioBiPageTests(TestCase):
    def setUp(self):
        self.dashboard = Dashboard.objects.get(slug="romaneios")
        self.url = reverse("dashesRomaneioBiPage")
        self.user = make_user("logistica_pallets")
        DashboardAccess.objects.create(user=self.user, dashboard=self.dashboard)

    def sign_in(self, user=None):
        self.client.force_login(user or self.user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def stubbed_erp(self):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.object(romaneio_bi, "_query_many", side_effect=fake_query_many))
        stack.enter_context(patch.object(romaneio_bi, "list_branches", return_value=BRANCHES))
        stack.enter_context(patch.object(romaneio_bi, "list_matriculas", return_value=MATRICULAS))
        stack.enter_context(patch.object(romaneio_bi, "_employee_names", return_value=EMPLOYEE_NAMES))
        return stack

    def test_migration_registers_the_panel_in_the_catalog(self):
        self.assertEqual(self.dashboard.url_name, "dashesRomaneioBiPage")
        self.assertTrue(self.dashboard.is_active)

    def test_panel_requires_explicit_permission(self):
        outsider = make_user("semacesso_pallets")
        other = Dashboard.objects.get(slug="ti-bi")
        DashboardAccess.objects.create(user=outsider, dashboard=other)
        self.sign_in(outsider)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "tiqueue/dashes_denied.html")

    def test_page_renders_the_dashboard(self):
        self.sign_in()
        with self.stubbed_erp():
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tiqueue/romaneio_bi.html")
        self.assertIsNone(response.context["data_error"])
        self.assertContains(response, "Ranking de colaboradores")

    def test_erp_failure_shows_a_message_instead_of_a_stack_trace(self):
        self.sign_in()
        with patch.object(romaneio_bi, "list_branches", side_effect=RuntimeError("ORA-12541")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("ORA-12541", response.context["data_error"])
        self.assertIsNone(response.context["dashboard"])

    def test_prepare_stores_one_snapshot_per_scope(self):
        self.sign_in()
        with self.stubbed_erp(), \
             patch("tiqueue.views.compute_romaneio_deep_analytics", return_value={"funnel": {}, "top_workdays": []}):
            first = self.client.post(
                reverse("romaneioBiPrepareInsights"),
                {"periodo": "12", "filial": "all", "estagio": "all", "matricula": "all"},
            )
            second = self.client.post(
                reverse("romaneioBiPrepareInsights"),
                {"periodo": "6", "filial": "all", "estagio": "all", "matricula": "all"},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        # Recortes diferentes não se sobrescrevem: cada um guarda o seu.
        self.assertEqual(RomaneioBiInsightSnapshot.objects.count(), 2)
        snapshot = RomaneioBiInsightSnapshot.objects.get(period_key="12")
        self.assertEqual(snapshot.status, RomaneioBiInsightSnapshot.STATUS_PREPARED)
        self.assertTrue(snapshot.ai_payload)
        self.assertEqual(snapshot.created_by, self.user)

    def test_prepare_reports_an_erp_failure_as_503(self):
        self.sign_in()
        with patch.object(romaneio_bi, "list_branches", side_effect=RuntimeError("ORA-12541")):
            response = self.client.post(
                reverse("romaneioBiPrepareInsights"),
                {"periodo": "12", "filial": "all", "estagio": "all", "matricula": "all"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")

    def test_ai_is_refused_before_the_indicators_exist(self):
        self.sign_in()

        response = self.client.post(
            reverse("romaneioBiRequestAiInsights"),
            {"periodo": "12", "filial": "all", "estagio": "all", "matricula": "all"},
        )

        self.assertEqual(response.status_code, 409)

    def test_ai_respects_the_daily_quota(self):
        # Cota estourada nem chega a chamar a OpenAI — o teto é por usuário e
        # vale para todos os painéis do Dashes.
        self.user.dashes_ai_daily_limit = 0
        self.user.save(update_fields=["dashes_ai_daily_limit"])
        self.sign_in()
        RomaneioBiInsightSnapshot.objects.create(
            period_key="12", branch_key="all", stage_key="all", matricula_key="all",
            source_fingerprint="fp", ai_payload={"x": 1},
        )

        with patch("tiqueue.views.generate_customer_insights") as ai_call:
            response = self.client.post(
                reverse("romaneioBiRequestAiInsights"),
                {"periodo": "12", "filial": "all", "estagio": "all", "matricula": "all"},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "ai_daily_limit")
        ai_call.assert_not_called()

    def test_pdf_is_refused_before_the_indicators_exist(self):
        self.sign_in()

        response = self.client.get(
            reverse("romaneioBiExportPdf") + "?periodo=12&filial=all&estagio=all&matricula=all"
        )

        self.assertEqual(response.status_code, 409)
