"""Inteligência de clientes: cálculos (sem Oracle) e as telas de mapa, carteira e ficha."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import commercial_dna as dna
from . import customer_intel as intel
from . import intel_ai
from .models import CustomerIntelInsight, CustomerSegmentGuess, Dashboard, DashboardAccess

TODAY = date(2026, 11, 9)
User = get_user_model()


def month_row(customer, month, net, rep=10004, kg=100, tsi=None, cpv=None, notes=1, last_day=5, net_to_day=None):
    tsi = Decimal(str(net)) * Decimal("0.8") if tsi is None else Decimal(str(tsi))
    return {
        "cliente": customer, "representante": rep, "mes": month, "nome_representante": "LJM REPRESENTACOES",
        "liquido": Decimal(str(net)), "liquido_ate_dia": Decimal(str(net if net_to_day is None else net_to_day)),
        "sem_impostos": tsi, "peso": Decimal(str(kg)),
        "cpv": None if cpv is None else Decimal(str(cpv)), "base_cpv": tsi if cpv is not None else None,
        "notas": notes, "primeira": month.replace(day=2), "ultima": month.replace(day=last_day),
    }


def info_row(customer, name, state, city, sector=None, cnpj="92791243000294", ibge=4106902):
    return {"cliente": customer, "nome": name, "fantasia": "", "cnpj": cnpj, "tipo": "J", "cidade": city, "uf": state,
            "ramo": sector, "ramo_nome": "FRIGORIFICOS" if sector == "011" else None, "ibge": ibge}


def synthetic_base():
    """Três clientes: um mensal no PR, um que parou em SC e um novo no PR."""
    months = [dna._add_months(date(2026, 11, 1), -offset) for offset in range(0, 24)]
    monthly = []
    for month in months:
        monthly.append(month_row(1, month, 1000, cpv=600, net_to_day=300))  # compra todo mês
    for month in months[13:]:
        monthly.append(month_row(2, month, 500, cpv=300))  # parou há mais de um ano
    monthly.append(month_row(3, date(2026, 10, 1), 2000, cpv=1000))  # novo
    info = [
        info_row(1, "FRIGORIFICO UM", "PR", "Curitiba", sector="011"),
        info_row(2, "CLIENTE DOIS", "SC", "Chapecó", cnpj="11111111000191", ibge=4204202),
        info_row(3, "CLIENTE TRES", "PR", "Cascavel", cnpj="", ibge=4104808),
    ]
    mix = [
        {"cliente": 1, "familia": "FILME MONO IMPRESSO", "complemento": "", "liquido": Decimal("12000"), "peso": Decimal("1200")},
        {"cliente": 3, "familia": "SACO IMPRESSO", "complemento": "", "liquido": Decimal("2000"), "peso": Decimal("100")},
    ]
    sectors = [{"codigo": "011", "nome": "FRIGORIFICOS"}, {"codigo": "002", "nome": "CEREAIS"}]
    return intel.build_base(monthly, info, mix, sectors, TODAY)


class RhythmAndRfmTests(SimpleTestCase):
    def test_purchase_rhythm_states(self):
        monthly = list(range(100, 112))
        on_track = intel.purchase_rhythm(monthly, date(2026, 10, 25), TODAY)
        late = intel.purchase_rhythm(monthly, date(2026, 9, 25), TODAY)
        at_risk = intel.purchase_rhythm(monthly, date(2026, 8, 1), TODAY)
        inactive = intel.purchase_rhythm(monthly, date(2026, 3, 1), TODAY)
        single = intel.purchase_rhythm([110], date(2026, 10, 1), TODAY)
        self.assertEqual([on_track["status"], late["status"], at_risk["status"], inactive["status"], single["status"]],
                         ["on_track", "late", "at_risk", "inactive", "single"])
        self.assertEqual(on_track["typical_days"], 30)
        self.assertEqual(on_track["next_purchase"], date(2026, 11, 24))
        self.assertGreater(late["overdue_days"], 0)

    def test_percentile_scores_and_segments(self):
        scores = intel.percentile_scores({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5})
        self.assertEqual(scores, {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5})
        self.assertEqual(intel.percentile_scores({"a": 7, "b": 7})["a"], intel.percentile_scores({"a": 7, "b": 7})["b"])
        self.assertEqual(intel.rfm_segment(5, 5, 5), "champions")
        self.assertEqual(intel.rfm_segment(1, 5, 5), "cant_lose")
        self.assertEqual(intel.rfm_segment(1, 1, 1), "lost")
        self.assertEqual(intel.rfm_segment(5, 1, 1, is_new=True), "new")

    def test_pareto(self):
        stats = intel.pareto_stats([Decimal(80), Decimal(10), Decimal(5), Decimal(3), Decimal(2)])
        self.assertEqual(stats["top20_count"], 1)
        self.assertEqual(stats["top20_share"], Decimal("0.8"))
        self.assertEqual(stats["count_for_80"], 1)
        self.assertEqual(intel.pareto_stats([])["count"], 0)


class MetricsTests(SimpleTestCase):
    def setUp(self):
        self.base = synthetic_base()
        self.window = intel.resolve_window("12", today=TODAY)
        self.metrics = intel.customer_metrics(self.base, self.window, TODAY)
        intel.apply_segments(self.metrics, self.base, {3: {"code": "002", "name": "CEREAIS", "confidence": "alta"}})
        intel.apply_rfm(self.metrics)

    def test_profit_margin_and_growth_with_day_cut(self):
        first = self.metrics[1]
        self.assertEqual(first["net"], Decimal("12000"))
        self.assertEqual(first["margin"], Decimal("0.25"))  # 1 − 600 ÷ 800
        self.assertEqual(first["profit"], Decimal("2400"))
        # Ano anterior: 11 meses cheios + o mês final cortado no mesmo dia (300).
        self.assertEqual(first["previous_net"], Decimal("11300"))
        self.assertEqual(first["rhythm"]["status"], "on_track")
        self.assertEqual(first["segment"]["source"], "erp")
        self.assertEqual(self.metrics[3]["segment"]["source"], "ia")

    def test_geography_counts_lost_customers_in_growth(self):
        geo = intel.geography(list(self.metrics.values()))
        self.assertEqual(geo["states"]["PR"]["customers"], 2)
        sc = geo["states"]["SC"]
        self.assertEqual(sc["customers"], 0)  # perdemos o cliente de SC
        self.assertEqual(sc["growth"], Decimal("-1"))
        summary = intel.portfolio_summary(list(self.metrics.values()))
        total_previous = self.metrics[1]["previous_net"] + self.metrics[2]["previous_net"]
        self.assertEqual(summary["growth"], dna._change(Decimal("14000"), total_previous))

    def test_reactivation_and_cross_sell(self):
        self.assertEqual([item["code"] for item in intel.reactivation_rows(self.metrics)], [2])
        model = intel.cross_sell_model(self.base, self.metrics, min_peers=1)
        self.assertIn("011", model)
        self.assertEqual(intel.cross_sell_for(self.metrics[1], self.base, model)["items"], [])

    def test_sector_cnaes_prefers_enriched_customers(self):
        record = mock.Mock(cnae_codigo="C-1012-1/01")
        self.assertEqual(intel.sector_cnaes("011", [record]), ["C-1012"])
        self.assertEqual(intel.sector_cnaes("011", []), intel.SECTOR_CNAE_PREFIXES["011"])


@override_settings(ALLOWED_HOSTS=["testserver"])
class IntelPagesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="adm", password="x", userId="adm", email="adm@example.com", is_system_admin=True)
        self.login(self.admin)
        self.base = synthetic_base()
        patcher = mock.patch.object(intel, "load_base", return_value=self.base)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_pages_render(self):
        for url in (reverse("dashesCustomerMapPage"), reverse("dashesCustomerPortfolioPage"),
                    reverse("dashesCustomerPortfolioPage") + "?visao=reativacao", reverse("dashesCustomerProfilePage", args=[1])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
        self.assertContains(self.client.get(reverse("dashesCustomerProfilePage", args=[1])), "FRIGORIFICO UM")
        self.assertEqual(self.client.get(reverse("dashesCustomerProfilePage", args=[999])).status_code, 404)

    def test_rfm_filter_on_the_portfolio(self):
        rows = [{"rfm": {"key": "champions"}}, {"rfm": {"key": "hibernating"}}, {"rfm": None}]
        self.assertEqual(intel.filter_rfm(rows, []), rows)
        self.assertEqual(len(intel.filter_rfm(rows, ["champions"])), 1)
        self.assertEqual(len(intel.filter_rfm(rows, ["champions", "hibernating"])), 2)
        page = self.client.get(reverse("dashesCustomerPortfolioPage"), {"rfm": "champions,xyz"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["filters"]["rfm"], ["champions"])
        self.assertContains(page, 'data-mx-filter="rfm"')
        self.assertEqual(page.context["insight_fields"]["rfm"], "champions")
        # A distribuição dos segmentos continua mostrando a carteira toda.
        self.assertEqual(page.context["summary"]["rfm_customers"], self.client.get(reverse("dashesCustomerPortfolioPage")).context["summary"]["customers"])

    def test_user_without_permission_is_blocked(self):
        user = User.objects.create_user(username="vendas", password="x", userId="vendas", email="v@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="customer-dna"))
        self.login(user)
        self.assertEqual(self.client.get(reverse("dashesCustomerMapPage")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashesCustomerPortfolioPage")).status_code, 403)

    def test_geo_endpoint_uses_the_cached_mesh(self):
        with mock.patch("tiqueue.ibge.mesh", return_value={"type": "FeatureCollection", "features": []}) as mesh:
            response = self.client.get(reverse("dashesCustomerMapGeo", args=["41"]))
            invalid = self.client.get(reverse("dashesCustomerMapGeo", args=["99"]))
        self.assertEqual(response.status_code, 200)
        mesh.assert_called_once_with("41")
        self.assertEqual(invalid.status_code, 404)

    def test_ai_insight_is_saved_and_shown(self):
        answer = {"response": {"resumo": "Sul concentra o lucro.", "destaques": ["PR cresce"], "alertas": [], "acoes": ["Visitar SC"]},
                  "model": "gpt-teste", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
        config = {"enabled": True, "api_key_configured": True}
        with mock.patch("tiqueue.intel_views.get_openai_runtime_config", return_value=config), \
                mock.patch.object(intel_ai, "generate_insight", return_value=answer) as generate:
            response = self.client.post(reverse("dashesCustomerMapInsight"), {"periodo": "12", "next": reverse("dashesCustomerMapPage")})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(generate.call_args.args[0], "map")
        insight = CustomerIntelInsight.objects.get(scope="map")
        self.assertEqual(insight.total_tokens, 15)
        with mock.patch("tiqueue.intel_views.get_openai_runtime_config", return_value=config):
            self.assertContains(self.client.get(reverse("dashesCustomerMapPage")), "Sul concentra o lucro.")

    def test_ai_additional_question_is_sent_saved_and_shown(self):
        answer = {"response": {"resumo": "Sul concentra o lucro.", "destaques": [], "alertas": [], "acoes": [],
                               "pergunta_adicional": "Qual UF mais caiu?", "resposta_adicional": "SC caiu 12%."},
                  "model": "gpt-teste", "usage": {"total_tokens": 15}}
        config = {"enabled": True, "api_key_configured": True}
        with mock.patch("tiqueue.intel_views.get_openai_runtime_config", return_value=config), \
                mock.patch.object(intel_ai, "generate_insight", return_value=answer) as generate:
            self.client.post(reverse("dashesCustomerMapInsight"),
                             {"periodo": "12", "adicional": "  Qual UF\n mais caiu?  ", "next": reverse("dashesCustomerMapPage")})
        self.assertEqual(generate.call_args.kwargs["extra"], "Qual UF mais caiu?")
        insight = CustomerIntelInsight.objects.get(scope="map")
        self.assertEqual(insight.payload["instrucao_adicional"], "Qual UF mais caiu?")
        with mock.patch("tiqueue.intel_views.get_openai_runtime_config", return_value=config):
            page = self.client.get(reverse("dashesCustomerMapPage"))
        self.assertContains(page, 'name="adicional"')
        self.assertContains(page, "Qual UF mais caiu?")
        self.assertContains(page, "SC caiu 12%.")

    def test_ai_classifies_only_customers_without_sector(self):
        config = {"enabled": True, "api_key_configured": True}
        result = {"results": [{"code": 2, "sector_code": "002", "sector_name": "CEREAIS", "confidence": "alta", "reason": "nome"}],
                  "usage": {"total_tokens": 99}, "model": "gpt-teste"}
        with mock.patch("tiqueue.intel_views.get_openai_runtime_config", return_value=config), \
                mock.patch.object(intel_ai, "classify_sectors", return_value=result) as classify:
            self.client.post(reverse("dashesCustomerSegmentsClassify"), {"next": reverse("dashesCustomerPortfolioPage")})
        sent = [customer["code"] for customer in classify.call_args.args[0]]
        self.assertNotIn(1, sent)  # cliente 1 já tem ramo no ERP
        self.assertEqual(CustomerSegmentGuess.objects.get(customer_code=2).sector_name, "CEREAIS")

    def test_econodata_actions_need_an_admin(self):
        user = User.objects.create_user(username="mapa", password="x", userId="mapa", email="m@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="customer-map"))
        self.login(user)
        with mock.patch("tiqueue.econodata.count_companies") as count:
            response = self.client.post(reverse("dashesCustomerMapEconodata"), {"acao": "contar", "uf": "PR", "segmento": "011"})
        self.assertEqual(response.status_code, 302)
        count.assert_not_called()
