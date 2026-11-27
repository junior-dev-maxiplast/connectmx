""""Expandir dados" dos gráficos do Dashes (chart_drill + drill_views)."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import chart_drill
from . import commercial_dna as dna
from .models import Dashboard, DashboardAccess
from .tests_commercial_dna import billing_row

D = Decimal
User = get_user_model()


def order_row(**overrides):
    row = {
        "empresa": 1, "filial": 1, "pedido": 70, "sequencia": 1, "emissao": date(2026, 10, 20),
        "entrega": date(2026, 11, 25), "situacao_item": 1, "cliente": 500, "nome_cliente": "CLIENTE TESTE",
        "representante": 10004, "produto": 1, "descricao_produto": "SACO TESTE", "familia": " SACO MONO LISO",
        "qtd_pedida": D("100"), "qtd_faturada": D("0"), "qtd_cancelada": D("0"), "qtd_aberta": D("100"),
        "preco_unitario": D("20"), "peso_unitario": D("2"),
    }
    row.update(overrides)
    return row


def fake_run(rows):
    """Troca dna._run: devolve as linhas por nome de consulta e guarda o SQL pedido."""
    calls = []

    def run(statements):
        calls.append(statements)
        return {name: list(rows.get(name, [])) for name in statements}

    run.calls = calls
    return run


class ParseMonthTests(SimpleTestCase):
    def test_valid_and_invalid_months(self):
        self.assertEqual(chart_drill.parse_month("2026-11"), date(2026, 11, 1))
        self.assertEqual(chart_drill.parse_month(" 2026-1 "), date(2026, 1, 1))
        for raw in ("", None, "xx", "2026-13", "2026", "1999-01", "2026-11-01"):
            self.assertIsNone(chart_drill.parse_month(raw), raw)


class RepresentativeGoalMonthTests(SimpleTestCase):
    def setUp(self):
        self.rows = {
            "billing": [
                billing_row(nota=1, data=date(2026, 11, 3), peso=D("100"), valor=D("2000")),
                billing_row(nota=2, data=date(2026, 11, 5), peso=D("50"), valor=D("900"), familia=" SACO MONO LISO"),
                # Devolvida por inteiro: não entra na barra, nem na tabela.
                billing_row(nota=3, data=date(2026, 11, 6), peso=D("80"), valor=D("1000"), qtd_dev=D("100")),
            ],
            "orders": [
                order_row(),
                order_row(pedido=71, entrega=date(2026, 11, 2)),  # atrasado em 18/11
                order_row(pedido=72, situacao_item=4),           # fechado: fora da carteira
            ],
            "production": [{"empresa": 1, "filial": 1, "pedido": 70, "sequencia": 1, "qtd_produzida": D("50")}],
        }
        self.goals = [
            {"year": 2026, "month": 11, "unit_code": 1, "family": "BOBINA MONO IMPRESSO", "weight_kg": D("300")},
            {"year": 2026, "month": 11, "unit_code": 4, "family": "BOBINA MONO IMPRESSO", "weight_kg": D("100")},
            {"year": 2026, "month": 10, "unit_code": 1, "family": "BOBINA MONO IMPRESSO", "weight_kg": D("999")},
        ]

    def detail(self, groups=None):
        run = fake_run(self.rows)
        with mock.patch.object(dna, "_run", run):
            result = chart_drill.representative_detail(
                10004, "meta", date(2026, 11, 1), goals=self.goals, groups=groups,
                today=date(2026, 11, 18), customer_url=lambda code: f"/dna/?cliente={code}",
            )
        return result, run.calls

    def test_sections_add_up_to_the_bar(self):
        result, calls = self.detail()
        billed, orders, goal = result["sections"]
        self.assertEqual([row["invoice"] for row in billed["rows"]], ["2", "1"])  # mais recente primeiro
        self.assertEqual(sum(row["kg"] for row in billed["rows"]), 150)
        self.assertEqual(billed["rows"][1]["customer_url"], "/dna/?cliente=500")
        self.assertEqual(len(orders["rows"]), 2)
        self.assertEqual(sum(row["kg"] for row in orders["rows"]), 400)  # 2 itens x 100 x 2 kg
        late = next(row for row in orders["rows"] if row["order"] == "71")
        self.assertEqual((late["status"], late["__tone"]), ("atrasado", "bad"))
        ready = next(row for row in orders["rows"] if row["order"] == "70")
        self.assertAlmostEqual(ready["ready"], 0.5)  # 100 kg produzidos de 200 a faturar
        by_family = {row["family"]: row for row in goal["rows"]}
        self.assertEqual(by_family["Bobina Mono Impresso"]["goal"], 400)  # só a meta de novembro
        self.assertEqual(by_family["Bobina Mono Impresso"]["billed"], 100)
        self.assertIn("Matriz", by_family["Bobina Mono Impresso"]["units"])
        self.assertEqual(by_family["Saco Mono Liso"]["to_bill"], 400)
        self.assertEqual(by_family["Saco Mono Liso"]["units"], "sem meta")
        facts = {fact["label"]: fact for fact in result["summary"]}
        self.assertEqual(facts["Realizado"]["note"], "138% da meta")  # (150 + 400) / 400
        self.assertEqual(facts["Falta"]["value"], "meta batida")
        self.assertIn("LJM", result["subtitle"])
        self.assertEqual(result["title"], "nov/2026")
        # Carteira: só itens com entrega no mês, sem a janela de emissão (início = fim).
        statements = calls[0]
        self.assertIn("ITEM.DATENT >= :delivery_start", statements["orders"][0])
        self.assertEqual(statements["orders"][1]["start_date"], statements["orders"][1]["end_date"])
        self.assertEqual(statements["billing"][1]["start_date"], date(2026, 11, 1))
        self.assertEqual(statements["billing"][1]["end_date"], date(2026, 12, 1))

    def test_group_filter_applies_to_billing_like_the_chart(self):
        result, _calls = self.detail(groups=["SACO MONO LISO"])
        billed, orders, _goal = result["sections"]
        self.assertEqual([row["invoice"] for row in billed["rows"]], ["2"])
        self.assertEqual(len(orders["rows"]), 2)  # o gráfico não filtra a carteira por grupo
        self.assertIn("Saco Mono Liso", result["subtitle"])


class RepresentativeRevenueMonthTests(SimpleTestCase):
    def test_month_and_same_month_last_year(self):
        rows = {
            "billing": [billing_row(data=date(2026, 10, 10), valor=D("3000"))],
            "previous": [billing_row(data=date(2025, 10, 10), valor=D("2000"))],
        }
        run = fake_run(rows)
        with mock.patch.object(dna, "_run", run):
            result = chart_drill.representative_detail(10004, "faturamento", date(2026, 10, 1))
        self.assertEqual([section["label"] for section in result["sections"]], ["Notas do mês", "Mesmo mês de 2025"])
        self.assertEqual(result["summary"][0]["note"], "+50,0% contra out/2025")
        self.assertEqual(run.calls[0]["previous"][1]["start_date"], date(2025, 10, 1))


class CustomerMonthTests(SimpleTestCase):
    def run_detail(self, codes):
        rows = {"billing": [billing_row(nota=100 + index, cliente=code, data=date(2026, 10, 10 + index)) for index, code in enumerate(codes)]}
        with mock.patch.object(dna, "_run", fake_run(rows)):
            return chart_drill.customer_detail(codes, date(2026, 10, 1), customer_url=lambda code: f"/dna/?cliente={code}")

    def test_single_customer_hides_the_customer_column(self):
        result = self.run_detail([500])
        keys = [column["key"] for column in result["sections"][0]["columns"]]
        self.assertNotIn("customer", keys)
        self.assertIn("profit", keys)
        self.assertNotIn("Clientes", [fact["label"] for fact in result["summary"]])
        self.assertIn("CLIENTE TESTE (500)", result["subtitle"])
        row = result["sections"][0]["rows"][0]
        # Lucro bruto = sem impostos (2000 - 425) - CPV 1200.
        self.assertAlmostEqual(row["profit"], 375.0)
        self.assertAlmostEqual(row["margin"], 375 / 1575, places=4)

    def test_group_of_customers_keeps_the_customer_column(self):
        result = self.run_detail([500, 501])
        keys = [column["key"] for column in result["sections"][0]["columns"]]
        self.assertIn("customer", keys)
        self.assertIn("Clientes", [fact["label"] for fact in result["summary"]])


class SeriesMonthKeyTests(SimpleTestCase):
    def test_monthly_series_and_goal_months_carry_the_month_key(self):
        lines = [dna.billing_line(billing_row(data=date(2026, 10, 10)))]
        series = dna._monthly_series(lines, date(2026, 11, 18))
        self.assertEqual(series[-1]["month"], "2026-11")
        self.assertEqual(series[-2]["month"], "2026-10")
        months = dna._goal_tracking([], lines, [], date(2026, 11, 18))["months"]
        self.assertEqual(months[0]["month"], "2026-01")


class DrillEndpointsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="drill", password="x", userId="drill", email="drill@example.com", is_system_admin=True,
        )
        self.login(self.admin)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_representative_detail_json(self):
        rows = {"billing": [billing_row(data=date(2026, 11, 3))], "orders": [order_row()], "production": []}
        with mock.patch.object(dna, "_run", fake_run(rows)):
            response = self.client.get(reverse("dashesRepresentativeDnaDetail"), {"representante": "10004", "visual": "meta", "mes": "2026-11"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([section["key"] for section in data["sections"]], ["billed", "orders", "goal"])
        self.assertTrue(data["sections"][0]["rows"][0]["customer_url"].startswith(reverse("dashesCustomerDnaV2Page")))

    def test_bad_parameters_and_erp_failure(self):
        url = reverse("dashesRepresentativeDnaDetail")
        for params in ({}, {"representante": "x", "mes": "2026-11"}, {"representante": "10004", "mes": "13/2026"},
                       {"representante": "10004", "mes": "2026-11", "visual": "outro"}):
            response = self.client.get(url, params)
            self.assertEqual(response.status_code, 400, params)
            self.assertIn("error", response.json())
        with mock.patch.object(dna, "_run", side_effect=RuntimeError("sem rede")):
            response = self.client.get(url, {"representante": "10004", "mes": "2026-11"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("ERP Senior", response.json()["error"])

    def test_customer_details(self):
        scope = {"mode": "individual", "members": [], "customer_codes": [500], "selected_member_id": 500}
        rows = {"billing": [billing_row(data=date(2026, 10, 10))]}
        with mock.patch.object(dna, "_run", fake_run(rows)), mock.patch.object(dna.legacy, "_resolve_customer_scope", return_value=scope):
            dna_response = self.client.get(reverse("dashesCustomerDnaDetail"), {"cliente": "500", "mes": "2026-10"})
            profile_response = self.client.get(reverse("dashesCustomerProfileDetail", args=[500]), {"mes": "2026-10"})
        self.assertEqual(dna_response.status_code, 200)
        self.assertEqual(len(dna_response.json()["sections"][0]["rows"]), 1)
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(self.client.get(reverse("dashesCustomerProfileDetail", args=[500])).status_code, 400)

    def test_each_detail_needs_the_permission_of_its_panel(self):
        user = User.objects.create_user(username="cliente", password="x", userId="cliente", email="c@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="customer-dna"))
        self.login(user)
        self.assertEqual(self.client.get(reverse("dashesRepresentativeDnaDetail"), {"representante": "10004", "mes": "2026-11"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashesCustomerProfileDetail", args=[500]), {"mes": "2026-10"}).status_code, 403)
        self.client.logout()
        response = self.client.get(reverse("dashesCustomerDnaDetail"), {"cliente": "500", "mes": "2026-10"})
        self.assertEqual(response.status_code, 302)  # sem sessão do Dashes: vai para o login
