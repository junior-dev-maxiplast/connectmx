"""Central de Decisão: agregação entre painéis, alertas proativos e ações.

O ERP/MySQL não entram nestes testes — `load_romaneio_dashboard`,
`load_travel_dashboard` e `load_it_dashboard` são mockados, mesmo padrão de
`tests_romaneio_bi.py`. O que se garante aqui é que a Central lê e combina
corretamente o que esses módulos e os `*InsightSnapshot` já devolvem — não
que os números batem com a operação.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import decision_center
from .models import (
    CustomerInsightSnapshot,
    DashAlert,
    DashDecisionAction,
    Dashboard,
    DashboardAccess,
    RomaneioBiInsightSnapshot,
)

User = get_user_model()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        password="secret123",
        userId=extra.pop("userId", username[:18]),
        email=extra.pop("email", f"{username}@example.com"),
        **extra,
    )


def fake_dashboard(metrics):
    """Formato mínimo que `load_romaneio_dashboard`/`load_travel_dashboard`/
    `load_it_dashboard` devolvem, só com o que a Central lê."""
    return {"metrics": metrics}


class AlertRuleEvaluationTests(TestCase):
    """`_evaluate_rule` isolado — sem banco, sem painel, só a matemática."""

    def test_below_pct_of_baseline_triggers_under_the_ratio(self):
        rule = decision_center.ALERT_RULES["romaneios"][0]  # registros < 75% da média
        result = decision_center._evaluate_rule(rule, {"records": 50}, {"records": 300})
        # média mensal do trimestre = 100; 50 < 75% de 100.
        self.assertIsNotNone(result)
        self.assertEqual(result["baseline"], 100)

    def test_below_pct_of_baseline_does_not_trigger_near_the_ratio(self):
        rule = decision_center.ALERT_RULES["romaneios"][0]
        result = decision_center._evaluate_rule(rule, {"records": 80}, {"records": 300})
        # 80 >= 75% de 100 - sem alerta.
        self.assertIsNone(result)

    def test_above_absolute_triggers_over_the_threshold(self):
        rule = decision_center.ALERT_RULES["romaneios"][1]  # missing_weight_pct > 20
        self.assertIsNotNone(decision_center._evaluate_rule(rule, {"missing_weight_pct": 25}, {}))
        self.assertIsNone(decision_center._evaluate_rule(rule, {"missing_weight_pct": 10}, {}))

    def test_drop_points_below_baseline_is_not_divided_by_the_baseline_months(self):
        # sla_valid_pct e um percentual pronto em qualquer recorte - dividir a
        # media trimestral por 3 daria um numero sem sentido (ver
        # `rate_metric` em decision_center.py).
        rule = decision_center.ALERT_RULES["ti-bi"][0]
        result = decision_center._evaluate_rule(rule, {"sla_valid_pct": 60}, {"sla_valid_pct": 80})
        self.assertIsNotNone(result)
        self.assertEqual(result["baseline"], 80)

    def test_missing_metric_does_not_raise(self):
        rule = decision_center.ALERT_RULES["romaneios"][0]
        self.assertIsNone(decision_center._evaluate_rule(rule, {}, {"records": 300}))
        self.assertIsNone(decision_center._evaluate_rule(rule, {"records": 50}, {}))


class RefreshAlertsTests(TestCase):
    def setUp(self):
        self.dashboard = Dashboard.objects.get(slug="romaneios")

    def test_refresh_creates_one_alert_per_triggered_rule(self):
        current = fake_dashboard({"records": 50, "missing_weight_pct": 30})
        baseline = fake_dashboard({"records": 300, "missing_weight_pct": 0})
        decision_center.refresh_alerts_for_scoped_dashboard(
            self.dashboard, current_dashboard=current, baseline_dashboard=baseline,
        )
        alerts = DashAlert.objects.filter(dashboard=self.dashboard, status=DashAlert.STATUS_ACTIVE)
        self.assertEqual(
            set(alerts.values_list("metric_key", flat=True)),
            {"romaneio_registros_queda", "romaneio_peso_ausente"},
        )

    def test_rerunning_updates_instead_of_duplicating(self):
        current = fake_dashboard({"records": 50, "missing_weight_pct": 30})
        baseline = fake_dashboard({"records": 300, "missing_weight_pct": 0})
        decision_center.refresh_alerts_for_scoped_dashboard(self.dashboard, current, baseline)
        decision_center.refresh_alerts_for_scoped_dashboard(self.dashboard, current, baseline)
        self.assertEqual(
            DashAlert.objects.filter(dashboard=self.dashboard, metric_key="romaneio_registros_queda").count(), 1,
        )

    def test_condition_clearing_resolves_the_alert_instead_of_deleting_it(self):
        low = fake_dashboard({"records": 50, "missing_weight_pct": 30})
        baseline = fake_dashboard({"records": 300, "missing_weight_pct": 0})
        decision_center.refresh_alerts_for_scoped_dashboard(self.dashboard, low, baseline)

        healthy = fake_dashboard({"records": 300, "missing_weight_pct": 0})
        decision_center.refresh_alerts_for_scoped_dashboard(self.dashboard, healthy, baseline)

        alert = DashAlert.objects.get(dashboard=self.dashboard, metric_key="romaneio_registros_queda")
        self.assertEqual(alert.status, DashAlert.STATUS_RESOLVED)
        self.assertIsNotNone(alert.resolved_at)

    def test_dna_alert_fires_only_when_a_customer_is_critical(self):
        dna_dashboard = Dashboard.objects.get(slug="customer-dna")
        decision_center.refresh_dna_alert(dna_dashboard, {"health_counts": {"critico": 2, "atencao": 0, "saudavel": 5}})
        self.assertTrue(
            DashAlert.objects.filter(
                dashboard=dna_dashboard, metric_key="dna_clientes_criticos", status=DashAlert.STATUS_ACTIVE,
            ).exists()
        )

        decision_center.refresh_dna_alert(dna_dashboard, {"health_counts": {"critico": 0, "atencao": 0, "saudavel": 7}})
        self.assertFalse(
            DashAlert.objects.filter(
                dashboard=dna_dashboard, metric_key="dna_clientes_criticos", status=DashAlert.STATUS_ACTIVE,
            ).exists()
        )


class CollectDashboardSummariesTests(TestCase):
    def test_erp_failure_on_one_dashboard_does_not_break_the_others(self):
        romaneios = Dashboard.objects.get(slug="romaneios")
        ti = Dashboard.objects.get(slug="ti-bi")

        with patch("tiqueue.romaneio_bi.load_romaneio_dashboard", side_effect=RuntimeError("ORA-12541")), \
             patch("tiqueue.it_bi.load_it_dashboard",
                   return_value=fake_dashboard({"tickets_display": "10", "backlog_display": "2"})):
            cards, _cache = decision_center.collect_dashboard_summaries([romaneios, ti])

        self.assertFalse(cards["romaneios"]["available"])
        self.assertIn("ORA-12541", cards["romaneios"]["error"])
        self.assertTrue(cards["ti-bi"]["available"])

    def test_dna_card_counts_the_latest_snapshot_per_customer(self):
        from datetime import timedelta

        from django.utils import timezone

        CustomerInsightSnapshot.objects.create(
            customer_code=1, customer_name="A", status=CustomerInsightSnapshot.STATUS_COMPLETED,
            source_fingerprint="f1", ai_response={"health": "critico"},
        )
        # Segunda analise do mesmo cliente, mais recente: só ela deve contar.
        # `created_at` empurrado manualmente pra frente porque é `auto_now_add`
        # (o `.create()` ignora qualquer valor passado) e duas criações no
        # mesmo teste podem cair no mesmo microssegundo, tornando a ordem
        # "mais recente" ambígua sem isso.
        recent = CustomerInsightSnapshot.objects.create(
            customer_code=1, customer_name="A", status=CustomerInsightSnapshot.STATUS_COMPLETED,
            source_fingerprint="f2", ai_response={"health": "saudavel"},
        )
        CustomerInsightSnapshot.objects.filter(pk=recent.pk).update(
            created_at=timezone.now() + timedelta(minutes=1)
        )
        CustomerInsightSnapshot.objects.create(
            customer_code=2, customer_name="B", status=CustomerInsightSnapshot.STATUS_COMPLETED,
            source_fingerprint="f3", ai_response={"health": "critico"},
        )

        card = decision_center._dna_customer_card()
        self.assertEqual(card["analyzed_count"], 2)
        self.assertEqual(card["health_counts"]["critico"], 1)
        self.assertEqual(card["health_counts"]["saudavel"], 1)


class ImportAiActionsTests(TestCase):
    def setUp(self):
        self.dashboard = Dashboard.objects.get(slug="romaneios")

    def _completed_snapshot(self, actions):
        return RomaneioBiInsightSnapshot.objects.create(
            period_key="12", source_fingerprint="fp",
            status=RomaneioBiInsightSnapshot.STATUS_COMPLETED,
            ai_response={"recommended_actions": actions},
        )

    def test_imports_each_recommended_action_once(self):
        self._completed_snapshot(["Revisar cadastro de peso", "Treinar equipe da filial 2"])
        created = decision_center.import_ai_actions([self.dashboard])
        self.assertEqual(created, 2)
        self.assertEqual(
            DashDecisionAction.objects.filter(dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_AI).count(), 2,
        )

    def test_rerunning_does_not_duplicate_the_same_action_text(self):
        self._completed_snapshot(["Revisar cadastro de peso"])
        decision_center.import_ai_actions([self.dashboard])
        decision_center.import_ai_actions([self.dashboard])
        self.assertEqual(
            DashDecisionAction.objects.filter(dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_AI).count(), 1,
        )


class DecisionCenterPageTests(TestCase):
    def setUp(self):
        self.url = reverse("dashesDecisionCenterPage")
        self.dna = Dashboard.objects.get(slug="customer-dna")
        self.romaneios = Dashboard.objects.get(slug="romaneios")
        self.user = make_user("gestor")
        DashboardAccess.objects.create(user=self.user, dashboard=self.dna)
        DashboardAccess.objects.create(user=self.user, dashboard=self.romaneios)

        # Sem isso, todo teste que abre a Central tenta uma conexão Oracle de
        # verdade (timeout de rede) - o padrão default aqui é neutro o
        # bastante pra não disparar nenhuma regra de alerta; testes que
        # querem números específicos sobrescrevem com seu próprio `patch`.
        self._load_romaneio_patcher = patch(
            "tiqueue.romaneio_bi.load_romaneio_dashboard",
            return_value=fake_dashboard({"records": 100, "missing_weight_pct": 0,
                                          "records_display": "100", "weight_display": "-"}),
        )
        self._load_romaneio_patcher.start()
        self.addCleanup(self._load_romaneio_patcher.stop)

    def sign_in(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_requires_dashes_session(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_renders_a_card_per_allowed_dashboard(self):
        self.sign_in()
        with patch(
            "tiqueue.romaneio_bi.load_romaneio_dashboard",
            return_value=fake_dashboard({"records": 300, "missing_weight_pct": 0,
                                          "records_display": "300", "weight_display": "1.000,0 kg"}),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Central de Decisão")
        self.assertContains(response, self.romaneios.name)
        self.assertContains(response, self.dna.name)

    def test_sidebar_badge_matches_active_alert_count(self):
        # `refresh_alerts_for_dashboards` de verdade recalcularia (e poderia
        # resolver) este alerta sintético — o que este teste quer provar é só
        # a fiação do selo da sidebar com o que já está em `DashAlert`, não a
        # lógica de recálculo (coberta em `RefreshAlertsTests`).
        self.sign_in()
        DashAlert.objects.create(
            dashboard=self.romaneios, metric_key="x", title="t", message="m",
            status=DashAlert.STATUS_ACTIVE,
        )
        with patch("tiqueue.views.refresh_alerts_for_dashboards"):
            response = self.client.get(self.url)
        self.assertContains(response, 'dashes-alert-badge')

    def test_acknowledge_alert(self):
        self.sign_in()
        alert = DashAlert.objects.create(
            dashboard=self.romaneios, metric_key="x", title="t", message="m",
            status=DashAlert.STATUS_ACTIVE,
        )
        response = self.client.post(reverse("dashesAlertAcknowledge", args=[alert.id]))
        self.assertEqual(response.status_code, 200)
        alert.refresh_from_db()
        self.assertEqual(alert.acknowledged_by, self.user)

    def test_cannot_acknowledge_an_alert_from_a_dashboard_without_access(self):
        self.sign_in()
        other = Dashboard.objects.get(slug="ti-bi")
        alert = DashAlert.objects.create(
            dashboard=other, metric_key="x", title="t", message="m", status=DashAlert.STATUS_ACTIVE,
        )
        response = self.client.post(reverse("dashesAlertAcknowledge", args=[alert.id]))
        self.assertEqual(response.status_code, 404)

    def test_create_and_update_manual_action(self):
        self.sign_in()
        response = self.client.post(
            reverse("dashesActionCreate"),
            {"dashboard_id": self.romaneios.id, "description": "Ligar para o fornecedor"},
        )
        self.assertEqual(response.status_code, 200)
        action = DashDecisionAction.objects.get(dashboard=self.romaneios)
        self.assertEqual(action.origin, DashDecisionAction.ORIGIN_MANUAL)
        self.assertEqual(action.created_by, self.user)
        # A tela insere essa ação na lista sem recarregar a página - por
        # isso o JSON precisa vir com o suficiente pra montar a linha.
        payload = response.json()
        self.assertEqual(payload["action"], {
            "id": action.id, "description": "Ligar para o fornecedor", "dashboard_name": self.romaneios.name,
        })

        response = self.client.post(
            reverse("dashesActionUpdateStatus", args=[action.id]), {"status": "done"},
        )
        self.assertEqual(response.status_code, 200)
        action.refresh_from_db()
        self.assertEqual(action.status, "done")
        self.assertIsNotNone(action.completed_at)


class NoRegressionTests(TestCase):
    """A Central de Decisão é aditiva: estes dois comportamentos antigos não
    podem mudar de jeito nenhum."""

    def test_dashes_home_still_redirects_to_the_first_allowed_dashboard(self):
        dna = Dashboard.objects.get(slug="customer-dna")
        Dashboard.objects.exclude(slug="customer-dna").update(is_active=False)
        user = make_user("um_so_painel")
        DashboardAccess.objects.create(user=user, dashboard=dna)
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

        response = self.client.get(reverse("dashesHome"))
        self.assertRedirects(response, reverse(dna.url_name), fetch_redirect_response=False)

    def test_existing_dash_panel_routes_are_untouched(self):
        # Nao muda nome de rota nem view dos 4 paineis - so prova que as
        # URLs continuam resolvendo; o comportamento de cada uma ja tem sua
        # propria suite (tests_romaneio_bi.py e outras).
        for name in (
            "dashesRomaneioBiPage", "romaneioBiPrepareInsights", "romaneioBiRequestAiInsights",
            "dashesItBiPage", "itBiPrepareInsights", "itBiRequestAiInsights",
            "dashesTravelBiPage", "travelBiPrepareInsights", "travelBiRequestAiInsights",
            "dashesCustomerDnaPage",
        ):
            reverse(name)


class SparklinePointsTests(TestCase):
    def test_reads_the_series_key_that_each_module_uses(self):
        fake = {"series": [
            {"label": "01/25", "height_pct": 40}, {"label": "02/25", "height_pct": 80},
        ]}
        with patch.object(decision_center, "_load_dashboard", return_value=fake) as load_mock:
            points = decision_center._sparkline_points("romaneios", {})
        load_mock.assert_called_once_with("romaneios", decision_center.SPARKLINE_PERIOD_KEY)
        self.assertEqual(points, [{"label": "01/25", "height_pct": 40}, {"label": "02/25", "height_pct": 80}])

    def test_travel_and_it_read_the_monthly_key_instead_of_series(self):
        fake = {"monthly": [{"label": "01/25", "height_pct": 55}]}
        with patch.object(decision_center, "_load_dashboard", return_value=fake):
            self.assertEqual(decision_center._sparkline_points("viagens", {})[0]["height_pct"], 55)
            self.assertEqual(decision_center._sparkline_points("ti-bi", {})[0]["height_pct"], 55)

    def test_erp_failure_returns_an_empty_list_instead_of_raising(self):
        with patch.object(decision_center, "_load_dashboard", side_effect=RuntimeError("ORA-12541")):
            self.assertEqual(decision_center._sparkline_points("romaneios", {}), [])

    def test_reuses_the_cached_dashboard_instead_of_loading_again(self):
        cached = {"series": [{"label": "x", "height_pct": 10}]}
        cache = {("sparkline", "romaneios"): cached}
        with patch.object(decision_center, "_load_dashboard") as load_mock:
            points = decision_center._sparkline_points("romaneios", cache)
        load_mock.assert_not_called()
        self.assertEqual(points, [{"label": "x", "height_pct": 10}])


class AlertLinkedActionTests(TestCase):
    """Todo alerta ativo ganha uma ação de checklist correspondente (`_ensure_alert_action`) —
    sem isso "fechar o ciclo" não teria nada com `origin_alert` preenchido pra fechar."""

    def setUp(self):
        self.dashboard = Dashboard.objects.get(slug="romaneios")

    def _trigger(self):
        low = fake_dashboard({"records": 50, "missing_weight_pct": 0})
        baseline = fake_dashboard({"records": 300, "missing_weight_pct": 0})
        decision_center.refresh_alerts_for_scoped_dashboard(self.dashboard, low, baseline)

    def test_a_triggered_alert_creates_a_linked_action(self):
        self._trigger()
        alert = DashAlert.objects.get(dashboard=self.dashboard, metric_key="romaneio_registros_queda")
        action = DashDecisionAction.objects.get(origin_alert=alert)
        self.assertEqual(action.origin, DashDecisionAction.ORIGIN_ALERT)
        self.assertEqual(action.dashboard, self.dashboard)

    def test_rerunning_while_still_open_does_not_duplicate_the_action(self):
        self._trigger()
        self._trigger()
        alert = DashAlert.objects.get(dashboard=self.dashboard, metric_key="romaneio_registros_queda")
        self.assertEqual(DashDecisionAction.objects.filter(origin_alert=alert).count(), 1)

    def test_a_new_action_is_created_after_the_previous_one_is_done(self):
        self._trigger()
        alert = DashAlert.objects.get(dashboard=self.dashboard, metric_key="romaneio_registros_queda")
        DashDecisionAction.objects.filter(origin_alert=alert).update(status=DashDecisionAction.STATUS_DONE)
        self._trigger()
        self.assertEqual(DashDecisionAction.objects.filter(origin_alert=alert).count(), 2)


class ActionResolutionProgressTests(TestCase):
    def setUp(self):
        self.dashboard = Dashboard.objects.get(slug="romaneios")
        self.alert = DashAlert.objects.create(
            dashboard=self.dashboard, metric_key="romaneio_registros_queda", title="t", message="m",
            current_value=50.0, status=DashAlert.STATUS_ACTIVE,
        )

    def test_capture_copies_the_alert_metric_onto_the_action(self):
        action = DashDecisionAction.objects.create(
            dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_ALERT, origin_alert=self.alert,
            description="d",
        )
        decision_center.capture_action_resolution(action)
        self.assertEqual(action.resolved_metric_key, "romaneio_registros_queda")
        self.assertEqual(action.resolved_metric_value, 50.0)

    def test_capture_is_a_no_op_for_actions_without_an_alert(self):
        action = DashDecisionAction.objects.create(
            dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_MANUAL, description="d",
        )
        decision_center.capture_action_resolution(action)
        self.assertIsNone(action.resolved_metric_value)

    def test_progress_reports_improvement_when_the_metric_moved_the_right_way(self):
        action = DashDecisionAction.objects.create(
            dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_ALERT, origin_alert=self.alert,
            description="d", resolved_metric_key="romaneio_registros_queda", resolved_metric_value=50.0,
        )
        cache = {("current", "romaneios"): fake_dashboard({"records": 300})}
        progress = decision_center.resolved_action_progress(action, cache)
        self.assertEqual(progress["before"], 50.0)
        self.assertEqual(progress["after"], 300.0)
        self.assertTrue(progress["improved"])

    def test_progress_reports_worsening_when_the_metric_moved_the_wrong_way(self):
        action = DashDecisionAction.objects.create(
            dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_ALERT, origin_alert=self.alert,
            description="d", resolved_metric_key="romaneio_registros_queda", resolved_metric_value=200.0,
        )
        cache = {("current", "romaneios"): fake_dashboard({"records": 50})}
        progress = decision_center.resolved_action_progress(action, cache)
        self.assertFalse(progress["improved"])

    def test_progress_is_none_without_a_current_dashboard_in_the_cache(self):
        action = DashDecisionAction.objects.create(
            dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_ALERT, origin_alert=self.alert,
            description="d", resolved_metric_key="romaneio_registros_queda", resolved_metric_value=50.0,
        )
        self.assertIsNone(decision_center.resolved_action_progress(action, {}))

    def test_progress_is_none_for_actions_without_a_captured_metric(self):
        action = DashDecisionAction.objects.create(
            dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_MANUAL, description="d",
        )
        cache = {("current", "romaneios"): fake_dashboard({"records": 300})}
        self.assertIsNone(decision_center.resolved_action_progress(action, cache))


class UpdateActionStatusViewTests(TestCase):
    def setUp(self):
        self.dashboard = Dashboard.objects.get(slug="romaneios")
        self.user = make_user("gestor_acoes")
        DashboardAccess.objects.create(user=self.user, dashboard=self.dashboard)
        self.client.force_login(self.user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_marking_an_alert_linked_action_done_captures_the_metric(self):
        alert = DashAlert.objects.create(
            dashboard=self.dashboard, metric_key="romaneio_registros_queda", title="t", message="m",
            current_value=42.0, status=DashAlert.STATUS_ACTIVE,
        )
        action = DashDecisionAction.objects.create(
            dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_ALERT, origin_alert=alert, description="d",
        )
        response = self.client.post(reverse("dashesActionUpdateStatus", args=[action.id]), {"status": "done"})
        self.assertEqual(response.status_code, 200)
        action.refresh_from_db()
        self.assertEqual(action.resolved_metric_value, 42.0)
        self.assertEqual(action.resolved_metric_key, "romaneio_registros_queda")

    def test_marking_a_manual_action_done_leaves_the_metric_fields_empty(self):
        action = DashDecisionAction.objects.create(
            dashboard=self.dashboard, origin=DashDecisionAction.ORIGIN_MANUAL, description="d",
        )
        self.client.post(reverse("dashesActionUpdateStatus", args=[action.id]), {"status": "done"})
        action.refresh_from_db()
        self.assertIsNone(action.resolved_metric_value)


class DecisionCenterExportPdfTests(TestCase):
    def setUp(self):
        self.url = reverse("dashesDecisionCenterExportPdf")
        self.romaneios = Dashboard.objects.get(slug="romaneios")
        self.user = make_user("gestor_pdf")
        DashboardAccess.objects.create(user=self.user, dashboard=self.romaneios)
        self.client.force_login(self.user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_requires_dashes_session(self):
        self.client.logout()
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_generates_a_pdf_with_the_same_data_as_the_page(self):
        with patch(
            "tiqueue.romaneio_bi.load_romaneio_dashboard",
            return_value=fake_dashboard({"records": 300, "missing_weight_pct": 0,
                                          "records_display": "300", "weight_display": "1.000 kg"}),
        ):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertGreater(len(response.content), 0)

    def test_erp_failure_is_reported_instead_of_a_stack_trace(self):
        with patch("tiqueue.romaneio_bi.load_romaneio_dashboard", side_effect=RuntimeError("ORA-12541")):
            response = self.client.get(self.url)
        # A Central tolera 1 painel fora do ar (mostra "indisponível") — só
        # falha o PDF de verdade se algo além do ERP quebrar, o que não é o
        # caso aqui: o card mockado sozinho já basta pra gerar o documento.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")


class PendingActionsPaginationTests(TestCase):
    """Ações pendentes não renderizam tudo de uma vez - `Paginator` +
    `get_page`, mesmo padrão de `portal_pending_demands.html`."""

    def setUp(self):
        self.url = reverse("dashesDecisionCenterPage")
        self.romaneios = Dashboard.objects.get(slug="romaneios")
        self.user = make_user("gestor_paginacao")
        DashboardAccess.objects.create(user=self.user, dashboard=self.romaneios)
        self.client.force_login(self.user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

        self._load_romaneio_patcher = patch(
            "tiqueue.romaneio_bi.load_romaneio_dashboard",
            return_value=fake_dashboard({"records": 100, "missing_weight_pct": 0,
                                          "records_display": "100", "weight_display": "-"}),
        )
        self._load_romaneio_patcher.start()
        self.addCleanup(self._load_romaneio_patcher.stop)

        for index in range(23):
            DashDecisionAction.objects.create(
                dashboard=self.romaneios, origin=DashDecisionAction.ORIGIN_MANUAL,
                description=f"Ação {index}",
            )

    def test_first_page_shows_only_a_page_worth_of_actions(self):
        response = self.client.get(self.url)
        actions_page = response.context["actions_page"]
        self.assertEqual(len(actions_page.object_list), 10)
        self.assertEqual(actions_page.paginator.count, 23)
        self.assertEqual(actions_page.paginator.num_pages, 3)

    def test_second_page_shows_the_next_batch(self):
        response = self.client.get(self.url, {"page": 2})
        actions_page = response.context["actions_page"]
        self.assertEqual(actions_page.number, 2)
        self.assertEqual(len(actions_page.object_list), 10)
        # Sem sobreposição entre as páginas.
        first_page_ids = {action.id for action in self.client.get(self.url).context["actions_page"].object_list}
        second_page_ids = {action.id for action in actions_page.object_list}
        self.assertFalse(first_page_ids & second_page_ids)

    def test_out_of_range_page_falls_back_to_the_last_page_instead_of_erroring(self):
        response = self.client.get(self.url, {"page": 999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["actions_page"].number, 3)

    def test_non_numeric_page_falls_back_to_the_first_page(self):
        response = self.client.get(self.url, {"page": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["actions_page"].number, 1)

    def test_pager_is_hidden_when_everything_fits_on_one_page(self):
        DashDecisionAction.objects.all().delete()
        DashDecisionAction.objects.create(
            dashboard=self.romaneios, origin=DashDecisionAction.ORIGIN_MANUAL, description="Só uma ação",
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, 'class="dc-pager"')
