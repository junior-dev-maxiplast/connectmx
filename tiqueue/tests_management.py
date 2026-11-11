"""Painel Gerencial (pontes, retenção, projeção, metas) e Agenda Comercial."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import customer_intel as intel
from . import intel_ai
from . import management_intel as mgmt
from . import sales_agenda
from .models import CustomerIntelInsight, Dashboard, DashboardAccess, SalesAgendaContact
from .tests_customer_intel import TODAY, synthetic_base

D = Decimal
User = get_user_model()


def cell(net, kg, cost=None, tsi=None):
    tsi = D(net) if tsi is None else D(tsi)
    cost = D(cost) if cost is not None else D(0)
    return {"net": D(net), "tsi": tsi, "kg": D(kg), "cost": cost, "profit": tsi - cost}


class BridgeTests(SimpleTestCase):
    def test_price_and_volume_of_one_product(self):
        previous = {(1, "BOBINA"): cell(1000, 100)}
        current = {(1, "BOBINA"): cell(1320, 120)}
        bridge = mgmt.revenue_bridge(previous, current)
        effects = {step["key"]: step["value"] for step in bridge["steps"]}
        self.assertEqual(effects["volume"], D(200))  # 20 kg × R$ 10
        self.assertEqual(effects["price"], D(120))  # 120 kg × R$ 1
        self.assertEqual(effects["mix"], D(0))
        self.assertEqual(bridge["residual"], D(0))

    def test_effects_always_close_the_difference(self):
        previous = {
            (1, "BOBINA"): cell(1000, 100, cost=700), (1, "SACO"): cell(300, 10, cost=200),
            (2, "BOBINA"): cell(500, 50, cost=400),  # cliente perdido
            (3, "SACO"): cell(200, 0, cost=100),  # sem kg
        }
        current = {
            (1, "BOBINA"): cell(1500, 120, cost=900), (1, "FOLHA"): cell(250, 20, cost=150),  # produto novo
            (3, "SACO"): cell(260, 0, cost=120),
            (4, "SACO"): cell(800, 40, cost=500),  # cliente novo
        }
        revenue = mgmt.revenue_bridge(previous, current)
        profit = mgmt.profit_bridge(previous, current)
        self.assertEqual(revenue["residual"], D(0))
        self.assertEqual(profit["residual"], D(0))
        revenue_effects = {step["key"]: step["value"] for step in revenue["steps"]}
        self.assertEqual(revenue_effects["lost_customers"], D(-500))
        self.assertEqual(revenue_effects["lost_products"], D(-300))
        self.assertEqual(revenue_effects["new_products"], D(250))
        self.assertEqual(revenue_effects["new_customers"], D(800))
        self.assertEqual(revenue_effects["other"], D(60))
        self.assertEqual(revenue["counts"], {"lost_customers": 1, "new_customers": 1})

    def test_higher_cost_per_kg_reduces_profit(self):
        previous = {(1, "BOBINA"): cell(1000, 100, cost=600)}
        current = {(1, "BOBINA"): cell(1000, 100, cost=700)}
        effects = {step["key"]: step["value"] for step in mgmt.profit_bridge(previous, current)["steps"]}
        self.assertEqual(effects["cost"], D(-100))
        self.assertEqual(effects["price"], D(0))


class RetentionWindowForecastTests(SimpleTestCase):
    def test_retention(self):
        data = mgmt.retention({1: D(100), 2: D(100), 3: D(50)}, {1: D(150), 2: D(60), 4: D(40)})
        self.assertEqual(data["nrr"], D("0.84"))  # (150 + 60) ÷ 250
        self.assertEqual(data["grr"], D("0.64"))  # (100 + 60) ÷ 250
        self.assertEqual((data["retained"], data["lost"], data["new"]), (2, 1, 1))
        self.assertEqual(data["end"], D(250))  # 150 + 60 + 40

    def test_closed_windows(self):
        window = mgmt.closed_window("12", date(2026, 11, 9))
        self.assertEqual(window["range_label"], "nov/25 a out/26")
        self.assertEqual(window["previous_range_label"], "nov/24 a out/25")
        january = mgmt.closed_window("ytd", date(2027, 1, 15))
        self.assertEqual(january["range_label"], "jan/26 a dez/26")

    def test_forecast_and_backlog(self):
        cells, backlog = {}, {}
        for key in range(dna_key(2024, 11), dna_key(2026, 11)):
            value = D(100) if key <= dna_key(2025, 11) else D(150)
            cells[(1, 10004, "BOBINA", key)] = {"net": value, "tsi": value, "kg": D(10), "cpv": D(50), "cpv_base": value, "cpv_known": True}
        backlog[dna_key(2026, 10)] = {"value": D(30), "kg": D(1), "items": 1, "customers": 1}  # atrasada
        backlog[dna_key(2026, 11)] = {"value": D(70), "kg": D(2), "items": 2, "customers": 1}
        base = {"cells": cells, "backlog": backlog, "cost_ratio": D("0.5"), "today": date(2026, 11, 9)}
        data = mgmt.forecast(base, date(2026, 11, 9))
        self.assertEqual(data["growth"], D("0.5"))
        first = data["rows"][0]
        self.assertEqual(first["projected"], D(150))  # nov/25 (100) × 1,5
        self.assertEqual(first["backlog"], D(100))  # 70 + 30 atrasado
        self.assertEqual(data["overdue_backlog"], D(30))

    def test_rep_goal_attainment_and_cost_estimate(self):
        base = mgmt.build_base([
            {"cliente": 1, "representante": 10004, "mes": date(2026, 1, 1), "familia": "FILME LAMINADO IMPRESSO", "bi_laminado": 1,
             "liquido": D(1000), "sem_impostos": D(800), "peso": D(400), "cpv": None, "base_cpv": None},
            {"cliente": 2, "representante": 10004, "mes": date(2026, 2, 1), "familia": "SACO IMPRESSO", "bi_laminado": 0,
             "liquido": D(500), "sem_impostos": D(400), "peso": D(100), "cpv": D(200), "base_cpv": D(400)},
        ], [], date(2026, 3, 10))
        self.assertIn((1, 10004, "BOBINA BI LAMINADO IMPRESSO", dna_key(2026, 1)), base["cells"])
        self.assertEqual(base["cost_ratio"], D("0.5"))
        window = mgmt.closed_window("ytd", date(2026, 3, 10))
        goals = [{"year": 2026, "month": 1, "rep_code": 10004, "weight_kg": D(500)}, {"year": 2026, "month": 2, "rep_code": 10004, "weight_kg": D(500)},
                 {"year": 2026, "month": 3, "rep_code": 10004, "weight_kg": D(9999)}]
        rows = mgmt.rep_performance(base, window, goals, {10004: "LJM"})["rows"]
        self.assertEqual(rows[0]["goal_kg"], D(1000))  # março ainda não fechou
        self.assertEqual(rows[0]["attainment"], D("0.5"))  # 500 kg de 1.000
        customers = mgmt.slice_cells(base, window["first_key"], window["last_key"], by=("customer",))
        self.assertEqual(customers[1]["cost"], D(400))  # 800 sem CPV × 0,5 estimado


def dna_key(year, month):
    return year * 12 + month - 1


class AgendaTests(SimpleTestCase):
    def setUp(self):
        self.base = synthetic_base()
        self.window = intel.resolve_window("12", today=TODAY)
        self.metrics = intel.customer_metrics(self.base, self.window, TODAY)
        intel.apply_segments(self.metrics, self.base, {})
        intel.apply_rfm(self.metrics)
        self.model = intel.cross_sell_model(self.base, self.metrics)

    def test_reasons_and_snooze(self):
        agenda = sales_agenda.build_agenda(self.metrics, self.base, self.model, horizon=30, today=TODAY)
        keys = {entry["item"]["code"]: entry["reasons"][0]["key"] for entry in agenda["items"]}
        self.assertEqual(keys[1], "due")  # compra todo mês: próxima dentro de 30 dias
        self.assertEqual(keys[2], "reactivate")
        contacts = {1: {"date": TODAY - timedelta(days=2), "outcome": "retornar", "outcome_label": "Retornar depois", "note": "", "by": ""}}
        snoozed = sales_agenda.build_agenda(self.metrics, self.base, self.model, horizon=30, today=TODAY, contacts=contacts)
        self.assertTrue(next(entry for entry in snoozed["items"] if entry["item"]["code"] == 1)["snoozed"])
        self.assertEqual(snoozed["items"][-1]["item"]["code"], 1)  # contatado vai para o fim

    def test_irregular_cnpj_leaves_the_agenda(self):
        receita = {"11111111000191": {"situacao": "BAIXADA", "alerta": True, "found": True}}
        agenda = sales_agenda.build_agenda(self.metrics, self.base, self.model, horizon=30, today=TODAY, receita=receita)
        self.assertNotIn(2, [entry["item"]["code"] for entry in agenda["items"]])


@override_settings(ALLOWED_HOSTS=["testserver"])
class ManagementPagesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="dir", password="x", userId="dir", email="dir@example.com", is_system_admin=True)
        self.login(self.admin)
        base = synthetic_management_base()
        patchers = [mock.patch.object(mgmt, "load_base", return_value=base), mock.patch.object(intel, "load_base", return_value=synthetic_base())]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_management_page_renders(self):
        for period in ("12", "6", "3", "ytd"):
            response = self.client.get(f"{reverse('dashesManagementPage')}?periodo={period}")
            self.assertEqual(response.status_code, 200, period)
        self.assertContains(response, "Ponte do lucro bruto")
        self.assertContains(response, "Simulador de cenários")

    def test_management_ai_summary(self):
        answer = {"response": {"resumo": "Preço explicou o lucro.", "o_que_explica": ["Preço +R$ 1"], "riscos": [], "decisoes": ["Rever custo"]},
                  "model": "gpt-teste", "usage": {"total_tokens": 20}}
        config = {"enabled": True, "api_key_configured": True}
        with mock.patch("tiqueue.intel_views.get_openai_runtime_config", return_value=config), \
                mock.patch.object(intel_ai, "generate_insight", return_value=answer) as generate:
            self.client.post(reverse("dashesManagementInsight"), {"periodo": "12", "next": reverse("dashesManagementPage")})
        self.assertEqual(generate.call_args.args[0], "management")
        self.assertIn("ponte_lucro_bruto", generate.call_args.args[1])
        self.assertTrue(CustomerIntelInsight.objects.filter(scope="management").exists())
        with mock.patch("tiqueue.intel_views.get_openai_runtime_config", return_value=config):
            page = self.client.get(reverse("dashesManagementPage"))
        self.assertContains(page, "Preço explicou o lucro.")
        self.assertContains(page, "Decisões sugeridas")

    def test_click_filters_and_chosen_months(self):
        last = mgmt.dna._month_key(TODAY) - 1
        months = f"{(last - 2) // 12}-{(last - 2) % 12 + 1:02d},{last // 12}-{last % 12 + 1:02d}"
        group = sorted({key[2] for key in synthetic_management_base()["cells"]})[0]
        page = self.client.get(reverse("dashesManagementPage"), {"rep": "10004", "grupo": group, "meses": months})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-mx-filter="grupo"')
        self.assertContains(page, "Representante: </span>")
        self.assertContains(page, "2 meses escolhidos")
        # A leitura da IA usa o mesmo recorte (o formulário leva os filtros).
        self.assertEqual(page.context["insight_fields"]["meses"], months)
        self.assertEqual(page.context["insight_fields"]["rep"], "10004")
        self.assertEqual(page.context["window"]["month_keys"], [last - 2, last])

    def test_agenda_page_csv_and_contact(self):
        page = self.client.get(f"{reverse('dashesSalesAgendaPage')}?dias=30")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "FRIGORIFICO UM")
        csv_response = self.client.get(f"{reverse('dashesSalesAgendaPage')}?dias=30&formato=csv")
        self.assertEqual(csv_response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("FRIGORIFICO UM", csv_response.content.decode("utf-8-sig"))
        response = self.client.post(reverse("dashesSalesAgendaContact"), {
            "cliente": "1", "nome": "FRIGORIFICO UM", "rep": "10004", "motivo": "due", "resultado": "pedido", "nota": "fechou 2 t",
            "next": f"{reverse('dashesSalesAgendaPage')}?dias=30",
        })
        self.assertEqual(response.status_code, 302)
        contact = SalesAgendaContact.objects.get()
        self.assertEqual((contact.outcome, contact.user, contact.rep_code), ("pedido", self.admin, 10004))
        page = self.client.get(f"{reverse('dashesSalesAgendaPage')}?dias=30")
        self.assertContains(page, "Já contatados")
        self.assertContains(page, "Pedido fechado")

    def test_invalid_contact_and_permissions(self):
        response = self.client.post(reverse("dashesSalesAgendaContact"), {"cliente": "x", "resultado": "pedido"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SalesAgendaContact.objects.exists())
        user = User.objects.create_user(username="rep", password="x", userId="rep", email="r@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="sales-agenda"))
        self.login(user)
        self.assertEqual(self.client.get(reverse("dashesSalesAgendaPage")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashesManagementPage")).status_code, 403)


def synthetic_management_base():
    rows = []
    for offset in range(1, 25):
        month = date(2026, 11, 1)
        month = date(month.year + (month.month - 1 - offset) // 12, (month.month - 1 - offset) % 12 + 1, 1)
        growth = D("1.2") if offset <= 12 else D(1)
        rows.append({"cliente": 1, "representante": 10004, "mes": month, "familia": "FILME MONO IMPRESSO", "bi_laminado": 0,
                     "liquido": D(1000) * growth, "sem_impostos": D(800) * growth, "peso": D(100), "cpv": D(500), "base_cpv": D(800) * growth})
        if offset > 12:
            rows.append({"cliente": 2, "representante": 10004, "mes": month, "familia": "SACO IMPRESSO", "bi_laminado": 0,
                         "liquido": D(300), "sem_impostos": D(240), "peso": D(20), "cpv": D(150), "base_cpv": D(240)})
    backlog = [{"mes": date(2026, 11, 1), "valor": D(900), "peso": D(80), "itens": 3, "clientes": 1}]
    base = mgmt.build_base(rows, backlog, TODAY)
    base["customers"] = {1: {"name": "FRIGORIFICO UM", "place": "Curitiba/PR"}, 2: {"name": "CLIENTE DOIS", "place": "Chapecó/SC"}}
    base["rep_names"] = {10004: "LJM"}
    base["loaded_at"] = timezone.now()
    return base


class ManagementFilterTests(SimpleTestCase):
    def test_filter_base_keeps_only_the_chosen_cells(self):
        base = synthetic_management_base()
        self.assertIs(mgmt.filter_base(base), base)
        group = sorted({key[2] for key in base["cells"]})[0]
        films = mgmt.filter_base(base, groups=[group])
        self.assertTrue(films["filtered"])
        self.assertTrue(films["cells"] and all(key[2] == group for key in films["cells"]))
        self.assertLess(len(films["cells"]), len(base["cells"]))
        self.assertFalse(mgmt.filter_base(base, reps=[99999])["cells"])

    def test_chosen_months_compare_with_the_same_months_a_year_before(self):
        base = synthetic_management_base()
        last = mgmt.dna._month_key(TODAY) - 1
        window = mgmt.month_window([last, last - 2], TODAY)
        self.assertEqual(window["month_keys"], [last - 2, last])
        analysis = mgmt.analyze(base, window, (), base["rep_names"], base["customers"], full_base=base)
        # Cliente 1: 1.200 por mês no último ano e 1.000 no anterior; o cliente 2 (300/mês) só no anterior.
        self.assertEqual(analysis["now"]["net"], D(2400))
        self.assertEqual(analysis["before"]["net"], D(2600))
        self.assertEqual(len(analysis["heatmap"]["months"]), 2)
        # Mês fora dos 12 últimos fechados não entra; sem meses válidos, volta à janela padrão.
        self.assertEqual(mgmt.month_window([last - 20], TODAY)["key"], mgmt.DEFAULT_PERIOD)

