""""Expandir dados" dos BIs antigos, dos painéis animados e do Painel Gerencial
(bi_drill, panel_drill, management_drill e os endpoints de drill_views)."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import bi_drill, chart_drill, it_bi, management_drill, panel_drill
from . import commercial_dna as dna
from . import customer_intel as intel
from . import management_intel as mgmt
from .drill_views import _reference_date
from .models import Dashboard, DashboardAccess
from .tests_chart_drill import fake_run, order_row
from .tests_commercial_dna import billing_row

D = Decimal
User = get_user_model()
TODAY = date(2026, 11, 18)


# ---------------------------------------------------------------------------
# Janela do snapshot (quadros de "Análise processada" dos BIs)
# ---------------------------------------------------------------------------

class PinnedWindowTests(SimpleTestCase):
    def test_months_back_follows_mysql_and_oracle(self):
        self.assertEqual(bi_drill._months_back(date(2026, 3, 31), 1, oracle=False), date(2026, 2, 28))
        self.assertEqual(bi_drill._months_back(date(2026, 2, 28), 1, oracle=True), date(2026, 1, 31))
        self.assertEqual(bi_drill._months_back(date(2026, 8, 21), 12, oracle=True), date(2025, 8, 21))

    def test_rolling_period_becomes_a_closed_range_only_with_a_reference_day(self):
        period = bi_drill.resolve_period("12")
        self.assertIs(bi_drill._pinned(period, None), period)
        pinned = bi_drill._pinned(period, date(2026, 8, 21))
        self.assertEqual((pinned["start"], pinned["end"]), (date(2025, 8, 21), date(2026, 8, 22)))
        closed = bi_drill.resolve_period("mes-anterior", today=date(2026, 8, 21))
        self.assertIs(bi_drill._pinned(closed, date(2026, 8, 21)), closed)  # mês fechado já tem início e fim

    def test_reference_date_parameter(self):
        self.assertEqual(_reference_date("2026-08-21"), date(2026, 8, 21))
        for raw in (None, "", "21/08/2026", "2999-01-01", "1999-12-31"):
            self.assertIsNone(_reference_date(raw), raw)

    def test_it_detail_counts_the_window_from_the_snapshot_day(self):
        captured = {}

        def query(statements, params):
            captured.update(statements=statements, params=params)
            return {"rows": [], "count": [{"total": 0, "fechados": 0}]}

        with mock.patch.object(it_bi, "_query_many", side_effect=query), mock.patch.object(it_bi, "list_attendants", return_value=[]):
            result = bi_drill.it_detail("12", "all", "all", "dia_semana", "1", "Seg", today=date(2026, 8, 20))
            self.assertIn("A.date_open >= %s AND A.date_open < %s", captured["statements"]["rows"])
            self.assertEqual(captured["params"]["rows"][:2], (date(2025, 8, 20), date(2026, 8, 21)))
            self.assertIn("janela da análise de 20/08/2026", result["subtitle"])
            bi_drill.it_detail("12", "all", "all", "dia_semana", "1", "Seg")
            self.assertIn("DATE_SUB(CURDATE(), INTERVAL %s MONTH)", captured["statements"]["rows"])

    def test_unknown_dimension_and_value(self):
        with self.assertRaises(bi_drill.UnknownDimension):
            bi_drill.it_detail("12", "all", "all", "nao-existe", "1")
        with self.assertRaises(bi_drill.UnknownDimension):
            bi_drill.it_detail("12", "all", "all", "dia_semana", "9")


# ---------------------------------------------------------------------------
# Painel Gerencial
# ---------------------------------------------------------------------------

def cell(net, kg, tsi=None, cost=None):
    tsi = net * D("0.8") if tsi is None else tsi
    cost = tsi * D("0.6") if cost is None else cost
    return {"net": net, "tsi": tsi, "kg": kg, "cost": cost, "profit": tsi - cost}


class BridgeEffectsTests(SimpleTestCase):
    """As células somadas por etapa fecham as pontes do painel (mesma divisão e fórmulas)."""

    def setUp(self):
        self.previous = {
            (1, "A"): cell(D("1000"), D("100")),
            (1, "B"): cell(D("500"), D("40")),     # o cliente 1 deixa de comprar B
            (2, "A"): cell(D("800"), D("90")),     # cliente 2 perdido
            (3, "A"): cell(D("300"), D("0")),      # sem kg num dos anos: "outros"
            (5, "A"): cell(D("400"), D("50"), cost=D("100")),
        }
        self.current = {
            (1, "A"): cell(D("1300"), D("110")),
            (1, "C"): cell(D("200"), D("20")),     # produto novo num cliente atual
            (3, "A"): cell(D("350"), D("10")),
            (4, "B"): cell(D("600"), D("50")),     # cliente novo
            (5, "A"): cell(D("380"), D("40"), cost=D("150")),
        }

    def test_cells_add_up_to_each_step(self):
        for field, builder in (("net", mgmt.revenue_bridge), ("profit", mgmt.profit_bridge)):
            bridge = builder(self.previous, self.current)
            effects, _average = management_drill.bridge_effects(self.previous, self.current, field)
            self.assertEqual(set(effects) - {step["key"] for step in bridge["steps"]}, set())
            for step in bridge["steps"]:
                total = sum((value for *_cells, value in effects.get(step["key"], [])), D(0))
                self.assertAlmostEqual(float(total), float(step["value"]), places=6, msg=(field, step["key"]))
            self.assertAlmostEqual(float(bridge["residual"]), 0, places=6)


def synthetic_base():
    """Base do Gerencial em memória: células (cliente, representante, grupo, mês)."""
    window = mgmt.closed_window("12", TODAY)
    now, before = window["last_key"], window["last_key"] - 12
    raw = {
        (1, 10, "BOBINA", now): (D("1000"), D("100")), (1, 10, "BOBINA", before): (D("800"), D("90")),
        (2, 10, "SACO", before): (D("500"), D("60")),  # perdido
        (3, 20, "SACO", now): (D("700"), D("70")),     # novo
        (4, 20, "BOBINA", now): (D("300"), D("30")), (4, 20, "BOBINA", before): (D("600"), D("50")),  # comprou menos
    }
    cells = {
        key: {"net": net, "tsi": net * D("0.8"), "kg": kg, "cpv": net * D("0.5"), "cpv_base": net * D("0.8"), "cpv_known": True}
        for key, (net, kg) in raw.items()
    }
    base = {
        "cells": cells, "cost_ratio": D("0.6"), "backlog": {}, "today": TODAY, "loaded_at": None,
        "customers": {code: {"name": f"CLIENTE {code}", "place": "Cascavel/PR"} for code in (1, 2, 3, 4)},
        "rep_names": {10: "Rep Dez", 20: "Rep Vinte"},
    }
    return base, window


class ManagementDetailTests(SimpleTestCase):
    def setUp(self):
        self.base, self.window = synthetic_base()

    def detail(self, visual, point):
        return management_drill.management_detail(self.base, self.base, self.window, visual, point, today=TODAY)

    def test_retention_steps_list_the_customers_of_each_flow(self):
        expected = {"lost": (["CLIENTE 2"], -500), "new": (["CLIENTE 3"], 700), "expansion": (["CLIENTE 1"], 200), "contraction": (["CLIENTE 4"], -300)}
        for point, (names, total) in expected.items():
            rows = self.detail("retencao", point)["sections"][0]["rows"]
            self.assertEqual([row["customer"] for row in rows], names, point)
            self.assertEqual(sum(row["change"] for row in rows), total, point)
        self.assertEqual(sum(row["after"] or 0 for row in self.detail("retencao", "fim")["sections"][0]["rows"]), 2000)

    def test_bridge_quadrant_group_and_rep(self):
        rows = self.detail("ponte-receita", "lost_customers")["sections"][0]["rows"]
        self.assertEqual([(row["customer"], row["effect"]) for row in rows], [("CLIENTE 2", -500.0)])
        quadrants = sum(len(self.detail("quadrante", key)["sections"][0]["rows"]) for key in mgmt.QUADRANT_ORDER)
        self.assertEqual(quadrants, 3)  # os três clientes que compraram no período
        group = self.detail("grupo", "BOBINA")["sections"][0]["rows"]
        self.assertEqual(sorted((row["customer"], row["net"], row["net_before"]) for row in group),
                         [("CLIENTE 1", 1000.0, 800.0), ("CLIENTE 4", 300.0, 600.0)])
        rep = self.detail("rep", "20")["sections"][0]["rows"]
        self.assertEqual(sorted(row["customer"] for row in rep), ["CLIENTE 3", "CLIENTE 4"])
        cell_rows = self.detail("calor-margem", f"SACO|{self.window['last_key']}")["sections"][0]["rows"]
        self.assertEqual([row["customer"] for row in cell_rows], ["CLIENTE 3"])

    def test_unknown_visual_and_point(self):
        for visual, point in (("nada", "x"), ("ponte-receita", "x"), ("retencao", "x"), ("matriz", "abc"), ("quadrante", "x"), ("calor-margem", "sem-mes")):
            with self.assertRaises(management_drill.UnknownPoint, msg=(visual, point)):
                self.detail(visual, point)


# ---------------------------------------------------------------------------
# Painéis animados: calendário de faturamento e recebido por mês
# ---------------------------------------------------------------------------

def paid_row(**overrides):
    row = {
        "empresa": 1, "filial": 1, "titulo": "123", "tipo": "DP", "cliente": 500, "representante": 10004,
        "emissao": date(2026, 9, 1), "vencimento": date(2026, 10, 1), "pagamento": date(2026, 10, 1), "valor": D("1000"),
        "portador": "001", "nome_portador": "BANCO TESTE", "nome": "CLIENTE TESTE", "cidade": "CASCAVEL", "uf": "PR",
        "nome_representante": "LJM",
    }
    row.update(overrides)
    return row


class PanelDetailTests(SimpleTestCase):
    def test_sales_day_lists_the_invoices_of_the_day(self):
        rows = {"billing": [billing_row(data=date(2026, 11, 17), valor=D("2000")), billing_row(nota=2, data=date(2026, 11, 17), valor=D("500"), qtd_dev=D("100"))]}
        run = fake_run(rows)
        with mock.patch.object(dna, "_run", run):
            result = panel_drill.sales_day_detail(date(2026, 11, 17))
        self.assertEqual(result["title"], "Terça, 17/11/2026")
        self.assertEqual(len(result["sections"][0]["rows"]), 1)  # a nota devolvida por inteiro fica fora, como no calendário
        self.assertEqual(result["summary"][0]["value"], dna._money(D("2000"), compact=False))
        params = run.calls[0]["billing"][1]
        self.assertEqual((params["start_date"], params["end_date"]), (date(2026, 11, 17), date(2026, 11, 18)))

    def test_paid_month_splits_on_time_and_late(self):
        rows = {"paid": [paid_row(), paid_row(titulo="124", pagamento=date(2026, 10, 20), valor=D("500"))]}
        run = fake_run(rows)
        with mock.patch.object(dna, "_run", run):
            result = panel_drill.paid_month_detail(date(2026, 10, 1), today=TODAY)
        all_rows, late_rows = (section["rows"] for section in result["sections"])
        self.assertEqual(len(all_rows), 2)
        self.assertEqual([row["late"] for row in late_rows], [19])
        self.assertEqual(result["summary"][1]["value"], dna._money(D("1000"), compact=False))  # em dia
        params = run.calls[0]["paid"][1]
        self.assertEqual((params["start_date"], params["end_date"]), (date(2026, 10, 1), date(2026, 11, 1)))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class PanelDrillEndpointTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="drill-panels", password="x", userId="drill-panels", email="drill-panels@example.com", is_system_admin=True,
        )
        self.login(self.admin)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_sales_and_receivables_details(self):
        today = date.today()  # a view valida o dia contra a data real
        rows = {"billing": [billing_row(data=today)], "paid": [paid_row(pagamento=today, vencimento=today)]}
        with mock.patch.object(dna, "_run", fake_run(rows)):
            sales = self.client.get(reverse("dashesSalesPanelDetail"), {"dia": today.isoformat()})
            paid = self.client.get(reverse("dashesReceivablesPanelDetail"), {"mes": today.strftime("%Y-%m")})
        self.assertEqual(sales.status_code, 200)
        self.assertEqual(len(sales.json()["sections"][0]["rows"]), 1)
        self.assertEqual(paid.status_code, 200)
        self.assertEqual(len(paid.json()["sections"][0]["rows"]), 1)
        for url, params in ((reverse("dashesSalesPanelDetail"), {"dia": "17/11/2026"}), (reverse("dashesSalesPanelDetail"), {"dia": "2099-01-01"}),
                            (reverse("dashesReceivablesPanelDetail"), {"mes": "2099-01"}), (reverse("dashesReceivablesPanelDetail"), {})):
            self.assertEqual(self.client.get(url, params).status_code, 400, params)
        with mock.patch.object(dna, "_run", side_effect=RuntimeError("sem rede")):
            self.assertEqual(self.client.get(reverse("dashesSalesPanelDetail"), {"dia": today.isoformat()}).status_code, 502)

    def test_management_detail(self):
        base, _window = synthetic_base()
        with mock.patch.object(mgmt, "load_base", return_value=base):
            ok = self.client.get(reverse("dashesManagementDetail"), {"visual": "retencao", "ponto": "lost", "periodo": "12"})
            bad = self.client.get(reverse("dashesManagementDetail"), {"visual": "retencao", "ponto": "x"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual([row["customer"] for row in ok.json()["sections"][0]["rows"]], ["CLIENTE 2"])
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get(reverse("dashesManagementDetail")).status_code, 400)

    def test_bi_detail_passes_the_reference_day(self):
        with mock.patch.object(bi_drill, "it_detail", return_value={"title": "ok", "sections": []}) as detail:
            response = self.client.get(reverse("dashesItBiDetail"), {"periodo": "12", "dim": "dia_semana", "valor": "1", "ref": "2026-08-20"})
            self.client.get(reverse("dashesItBiDetail"), {"periodo": "12", "dim": "dia_semana", "valor": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(detail.call_args_list[0].args[-1], date(2026, 8, 20))
        self.assertIsNone(detail.call_args_list[1].args[-1])

    def test_each_detail_needs_the_permission_of_its_panel(self):
        user = User.objects.create_user(username="so-vendas", password="x", userId="so-vendas", email="v@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="sales-panel"))
        self.login(user)
        self.assertEqual(self.client.get(reverse("dashesReceivablesPanelDetail"), {"mes": "2026-10"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashesManagementDetail"), {"visual": "retencao", "ponto": "lost"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashesItBiDetail"), {"dim": "mes", "valor": "2026-10"}).status_code, 403)
        with mock.patch.object(dna, "_run", fake_run({"billing": []})):
            self.assertEqual(self.client.get(reverse("dashesSalesPanelDetail"), {"dia": date.today().isoformat()}).status_code, 200)


# ---------------------------------------------------------------------------
# Tabelas de registros: DNA de Representantes e Carteira de Clientes
# ---------------------------------------------------------------------------

class RecordTableDetailTests(SimpleTestCase):
    def test_customer_of_the_representative_in_the_period(self):
        rows = {
            "billing": [
                billing_row(nota=1, data=date(2026, 3, 10), valor=D("2000")),
                billing_row(nota=2, data=date(2026, 3, 12), valor=D("500"), familia=" SACO MONO LISO"),
                billing_row(nota=3, data=date(2025, 12, 31), valor=D("900")),  # fora do período
            ],
            "previous": [billing_row(nota=9, data=date(2025, 3, 5), valor=D("1000"))],
        }
        run = fake_run(rows)
        with mock.patch.object(dna, "_run", run):
            result = chart_drill.representative_customer_detail(10004, 500, date(2026, 1, 1), date(2026, 12, 1), groups=["BOBINA MONO IMPRESSO"])
        billed, previous = (section["rows"] for section in result["sections"])
        self.assertEqual([row["invoice"] for row in billed], ["1"])  # grupo da tela e período
        self.assertEqual(len(previous), 1)
        self.assertEqual(result["title"], "CLIENTE TESTE (500)")
        params = run.calls[0]["previous"][1]
        self.assertEqual((params["start_date"], params["end_date"], params["cliente"]), (date(2025, 1, 1), date(2025, 12, 1), 500))
        self.assertIn("N.CODCLI = :cliente", run.calls[0]["billing"][0])

    def test_open_items_of_the_customer(self):
        rows = {"orders": [order_row(), order_row(sequencia=2, qtd_aberta=D("0"), qtd_faturada=D("100"), situacao_item=4)], "production": []}
        run = fake_run(rows)
        with mock.patch.object(dna, "_run", run):
            result = chart_drill.representative_customer_orders(10004, 500, today=date(2026, 11, 18))
        self.assertEqual(len(result["sections"][0]["rows"]), 1)  # o item já faturado fica fora
        self.assertEqual(result["summary"][0]["value"], dna._kg(D("200")))
        self.assertIn("PED.CODCLI = :cliente", run.calls[0]["orders"][0])

    def test_portfolio_customer_by_month_and_invoices(self):
        today = date(2026, 11, 18)
        window = intel.resolve_window("12", today=today)
        month = window["last_key"]

        def month_row(key, net):
            return {"month": key, "rep": 10004, "net": net, "net_to_day": net, "tsi": net * D("0.8"), "cpv": net * D("0.4"),
                    "cpv_base": net * D("0.8"), "kg": D("10"), "notes": 1, "first": today, "last": today}

        base = {"today": today, "customers": {500: {"name": "CLIENTE TESTE"}},
                "months": {500: [month_row(month, D("1000")), month_row(month - 1, D("500")), month_row(month - 30, D("900"))]}}
        with mock.patch.object(dna, "_run", fake_run({"billing": [billing_row(data=today)]})):
            result = chart_drill.portfolio_customer_detail(base, window, 500)
            history = chart_drill.portfolio_customer_detail(base, window, 500, history=True)
        self.assertEqual([row["net"] for row in result["sections"][0]["rows"]], [1000.0, 500.0])  # o mês fora do período fica de fora
        self.assertEqual(result["summary"][0]["value"], dna._money(D("1500"), compact=False))
        self.assertEqual(result["summary"][2]["value"], dna._pct(D("0.5")))
        self.assertEqual(len(result["sections"][1]["rows"]), 1)
        self.assertEqual(history["summary"][0]["value"], dna._money(D("1500"), compact=False))  # 24 meses: a linha de 30 meses atrás não entra


class RecordTableEndpointTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="drill-records", password="x", userId="drill-records", email="drill-records@example.com", is_system_admin=True,
        )
        self.client.force_login(self.admin)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_representative_record_visuals(self):
        url = reverse("dashesRepresentativeDnaDetail")
        rows = {"billing": [billing_row(data=date(2026, 3, 10))], "previous": [], "orders": [order_row()], "production": []}
        with mock.patch.object(dna, "_run", fake_run(rows)):
            customer = self.client.get(url, {"representante": "10004", "visual": "cliente-periodo", "cliente": "500", "inicio": "2025-12-01", "fim": "2026-11-18"})
            orders = self.client.get(url, {"representante": "10004", "visual": "carteira-cliente", "cliente": "500"})
        self.assertEqual(customer.status_code, 200)
        self.assertEqual(len(customer.json()["sections"][0]["rows"]), 1)
        self.assertEqual(orders.status_code, 200)
        self.assertEqual(len(orders.json()["sections"][0]["rows"]), 1)
        for params in ({"representante": "10004", "visual": "cliente-periodo", "cliente": "500"},
                       {"representante": "10004", "visual": "cliente-periodo", "cliente": "500", "inicio": "2026-11-18", "fim": "2026-01-01"},
                       {"representante": "10004", "visual": "carteira-cliente"},
                       {"representante": "10004", "visual": "ultima-compra", "mes": "2026-11"}):
            self.assertEqual(self.client.get(url, params).status_code, 400, params)

    def test_portfolio_detail(self):
        base = {"today": date.today(), "customers": {}, "months": {}}
        with mock.patch.object(intel, "load_base", return_value=base), mock.patch.object(dna, "_run", fake_run({"billing": []})):
            ok = self.client.get(reverse("dashesCustomerPortfolioDetail"), {"cliente": "500", "periodo": "12"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["title"], "Cliente 500 (500)")
        self.assertEqual(self.client.get(reverse("dashesCustomerPortfolioDetail"), {"cliente": "x"}).status_code, 400)
        user = User.objects.create_user(username="so-dna", password="x", userId="so-dna", email="d@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="representative-dna"))
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()
        self.assertEqual(self.client.get(reverse("dashesCustomerPortfolioDetail"), {"cliente": "500"}).status_code, 403)
