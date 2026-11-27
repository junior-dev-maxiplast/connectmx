""""Expandir dados" dos cards de indicador e dos cards que ainda não abriam registros: Painel
Gerencial, Contas a Receber, Carteira, Mapa e Agenda (intel_drill), DNA de Representantes, DNA do
Cliente e Ficha do Cliente. Cada detalhe precisa fechar com o número do card."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import chart_drill, intel_drill, management_drill, panel_drill
from . import commercial_dna as dna
from . import customer_intel as intel
from . import management_intel as mgmt
from .models import Dashboard, DashboardAccess, SalesAgendaContact
from .tests_chart_drill import fake_run, order_row
from .tests_commercial_dna import billing_row
from .tests_drill_panels import paid_row, synthetic_base

D = Decimal
User = get_user_model()
TODAY = date(2026, 11, 18)


# ---------------------------------------------------------------------------
# Painel Gerencial: fila "Indicadores"
# ---------------------------------------------------------------------------

class ManagementKpiTests(SimpleTestCase):
    def setUp(self):
        self.base, self.window = synthetic_base()

    def detail(self, point):
        return management_drill.management_detail(self.base, self.base, self.window, "indicador", point, today=TODAY)

    def test_value_cards_add_up_to_the_card(self):
        analysis = mgmt.analyze(self.base, self.window)
        cards = {card["key"]: card for card in mgmt.kpis(analysis)}
        self.assertEqual(set(cards), set(management_drill.KPI_POINTS))
        for point in ("faturamento", "lucro", "margem", "volume", "preco", "custo", "lucro-kg"):
            _label, field, _sort = management_drill.KPI_POINTS[point]
            result = self.detail(point)
            # O primeiro número do detalhe é o do card (por extenso), a partir dos mesmos totais.
            self.assertEqual(result["summary"][0]["value"], management_drill._kpi_value(field, analysis["now"]), point)
            self.assertEqual(result["title"], cards[point]["label"], point)
        self.assertEqual(self.detail("margem")["summary"][0]["value"], cards["margem"]["value"])
        rows = self.detail("faturamento")["sections"][0]["rows"]
        self.assertEqual(sum(row["net"] for row in rows), float(analysis["now"]["net"]))
        self.assertEqual(sorted(row["customer"] for row in rows), ["CLIENTE 1", "CLIENTE 2", "CLIENTE 3", "CLIENTE 4"])  # o perdido entra pelo "antes"

    def test_retention_active_customers_and_dependency(self):
        analysis = mgmt.analyze(self.base, self.window)
        nrr = self.detail("nrr")
        self.assertEqual(nrr["summary"][0]["value"], mgmt.pct(analysis["retention"]["nrr"]))
        base_rows = nrr["sections"][0]["rows"]
        self.assertEqual(sum(row["before"] for row in base_rows), 1900.0)
        self.assertEqual(sum(row["after"] for row in base_rows), 1300.0)  # 1300 / 1900 = NRR
        self.assertEqual([row["customer"] for row in nrr["sections"][1]["rows"]], ["CLIENTE 3"])
        active = self.detail("clientes")
        self.assertEqual(active["summary"][0]["value"], str(analysis["customers_now"]))
        self.assertEqual([row["customer"] for row in active["sections"][1]["rows"]], ["CLIENTE 2"])
        dependency = self.detail("dependencia")
        self.assertEqual(dependency["summary"][0]["value"], mgmt.pct(analysis["concentration"]["top10"]))
        self.assertAlmostEqual(sum(row["share"] for row in dependency["sections"][0]["rows"]), 1.0, places=3)
        with self.assertRaises(management_drill.UnknownPoint):
            self.detail("nada")


# ---------------------------------------------------------------------------
# Contas a Receber: prazo, pontualidade e créditos
# ---------------------------------------------------------------------------

class ReceivablesKpiTests(SimpleTestCase):
    def test_paid_range_weights_term_and_delay_by_value(self):
        rows = {"paid": [paid_row(), paid_row(titulo="124", emissao=date(2026, 9, 21), pagamento=date(2026, 10, 21), valor=D("3000"))]}
        run = fake_run(rows)
        with mock.patch.object(dna, "_run", run):
            result = panel_drill.paid_range_detail(date(2026, 9, 1), date(2026, 11, 1), today=TODAY)
        facts = {fact["label"]: fact for fact in result["summary"]}
        self.assertEqual(facts["Prazo médio"]["value"], "30,0 dia(s)")  # (30 × 1000 + 30 × 3000) ÷ 4000
        self.assertEqual(facts["Em dia"]["value"], dna._money(D("1000"), compact=False))
        params = run.calls[0]["paid"][1]
        self.assertEqual((params["start_date"], params["end_date"]), (date(2026, 9, 1), TODAY + timedelta(days=1)))
        self.assertEqual(result["sections"][0]["groupBy"][0], "paid_month")

    def test_credit_titles(self):
        rows = {"credits": [
            {"empresa": 1, "filial": 1, "titulo": "9", "tipo": "DEV", "descricao": "DEVOLUCAO", "cliente": 500, "representante": 10004,
             "emissao": date(2026, 11, 1), "vencimento": None, "aberto": D("250"), "original": D("300"), "nome": "CLIENTE", "cidade": "CASCAVEL", "uf": "PR"},
            {"empresa": 1, "filial": 1, "titulo": "10", "tipo": "ADT", "descricao": "ADIANTAMENTO", "cliente": 501, "representante": None,
             "emissao": date(2026, 10, 1), "vencimento": None, "aberto": D("750"), "original": D("750"), "nome": "OUTRO", "cidade": "", "uf": "SC"},
        ]}
        with mock.patch.object(dna, "_run", fake_run(rows)):
            result = panel_drill.credit_titles_detail(today=TODAY)
        self.assertEqual(result["summary"][0]["value"], dna._money(D("1000"), compact=False))
        self.assertEqual(result["summary"][2]["label"], "Adiantamento")
        self.assertEqual(sum(row["open"] for row in result["sections"][0]["rows"]), 1000.0)


# ---------------------------------------------------------------------------
# Carteira, Mapa e Agenda (base do customer_intel em memória)
# ---------------------------------------------------------------------------

def metric(code, net, state="PR", city="CASCAVEL", ibge=4104808, margin="0.2", previous=None, rfm="champions", rhythm="on_track", segment="10"):
    net = D(str(net))
    tsi = net * D("0.8")
    known = margin is not None
    return {
        "code": code, "name": f"CLIENTE {code}", "trade_name": "", "city": city, "state": state, "ibge": ibge, "cnpj": "",
        "net": net, "tsi": tsi, "kg": net / 10, "notes": 1, "active_months": 1,
        "margin": D(margin) if known else None, "profit": tsi * D(margin) if known else None,
        "previous_net": D(str(previous)) if previous is not None else None, "growth": None,
        "history_net": net, "history_profit": None, "typical_value": net / 2, "purchase_months": 2,
        "first_purchase": TODAY, "last_purchase": TODAY - timedelta(days=30), "days_since": 30, "is_new": False,
        "rhythm": {"status": rhythm, "label": rhythm, "tone": "", "days_since": 30, "typical_days": 30, "next_purchase": TODAY, "overdue_days": 5},
        "momentum": {}, "rep_code": 10004, "rep_name": "LJM",
        "segment": {"code": segment, "name": "ALIMENTOS", "source": "erp"} if segment else None,
        "rfm": {"key": rfm, "label": intel.RFM_SEGMENTS[rfm][0]} if rfm else None,
    }


class PortfolioAndMapDetailTests(SimpleTestCase):
    def setUp(self):
        self.window = intel.resolve_window("12", today=TODAY)
        self.rows = [
            metric(1, 1000, previous=800), metric(2, 500, margin="-0.1", previous=600, rfm="loyal", rhythm="late"),
            metric(3, 300, state="SC", city="JOINVILLE", ibge=4209102, margin=None, rhythm="at_risk"),
            metric(4, 0, city="TOLEDO", ibge=4127700, previous=900, rfm=None),  # parou de comprar (cidade perdida)
        ]

    def test_portfolio_cards_follow_the_summary(self):
        summary = intel.portfolio_summary(self.rows)
        profit = intel_drill.portfolio_detail(self.rows, self.rows, self.window, "lucro")
        self.assertEqual(profit["summary"][0]["value"], dna._money(summary["profit"], compact=False))
        self.assertEqual(sum(row["profit"] or 0 for row in profit["sections"][0]["rows"]), float(summary["profit"]))
        negative = intel_drill.portfolio_detail(self.rows, self.rows, self.window, "margem-negativa")
        self.assertEqual([row["customer"] for row in negative["sections"][0]["rows"]], ["CLIENTE 2"])
        active = intel_drill.portfolio_detail(self.rows, self.rows, self.window, "clientes")
        self.assertEqual(active["summary"][0]["value"], str(summary["customers"]))
        self.assertEqual([row["customer"] for row in active["sections"][1]["rows"]], ["CLIENTE 4"])
        rhythm = intel_drill.portfolio_detail(self.rows, self.rows, self.window, "ritmo")
        self.assertEqual(rhythm["summary"][0]["value"], str(summary["at_risk_count"]))
        self.assertEqual(rhythm["summary"][1]["value"], str(summary["late_count"]))
        top = intel_drill.portfolio_detail(self.rows, self.rows, self.window, "concentracao")
        self.assertEqual(len(top["sections"][0]["rows"]), intel.pareto_stats([item["profit"] for item in self.rows if item["net"] > 0 and item["margin"] is not None])["top20_count"])
        rfm = intel_drill.portfolio_detail(self.rows, self.rows, self.window, "rfm:champions")
        self.assertEqual(sorted(row["customer"] for row in rfm["sections"][0]["rows"]), ["CLIENTE 1", "CLIENTE 3"])
        for point in ("nada", "rfm:nada"):
            with self.assertRaises(intel_drill.UnknownPoint):
                intel_drill.portfolio_detail(self.rows, self.rows, self.window, point)

    def test_map_areas_match_the_geography(self):
        geo = intel.geography(self.rows)
        state = intel_drill.map_detail(self.rows, self.window, "uf:PR")
        self.assertEqual(state["summary"][0]["value"], dna._money(geo["states"]["PR"]["net"], compact=False))
        self.assertEqual(state["summary"][3]["value"], str(geo["states"]["PR"]["customers"]))
        self.assertEqual([row["customer"] for row in state["sections"][2]["rows"]], ["CLIENTE 4"])  # parou de comprar
        self.assertEqual(sum(row["customers"] for row in state["sections"][0]["rows"]), 2)  # por município
        city = intel_drill.map_detail(self.rows, self.window, "cidade:4209102")
        self.assertEqual([row["customer"] for row in city["sections"][0]["rows"]], ["CLIENTE 3"])
        region = intel_drill.map_detail(self.rows, self.window, "regiao:S")
        self.assertEqual(region["summary"][3]["value"], "3")
        summary = intel_drill.map_detail(self.rows, self.window, "resumo:lucro")
        self.assertEqual(summary["sections"][1]["sort"]["key"], "profit")
        lost = intel_drill.map_detail(self.rows, self.window, "perdidas")
        self.assertEqual(lost["summary"][0]["value"], "1")
        self.assertEqual(intel_drill.map_detail(self.rows, self.window, "perdida:4127700")["summary"][0]["value"], dna._money(D("900"), compact=False))
        segment = intel_drill.map_detail(self.rows, self.window, "segmento:PR|10")
        self.assertEqual(len(segment["sections"][0]["rows"]), 2)
        for point in ("uf:XX", "cidade:0", "regiao:X", "perdida:4104808", "nada"):
            with self.assertRaises(intel_drill.UnknownPoint, msg=point):
                intel_drill.map_detail(self.rows, self.window, point)

    def test_agenda_card_counts_the_main_reason(self):
        items = [
            {"item": self.rows[0], "reasons": [{"key": "due", "text": "compra prevista"}], "last_contact": None, "snoozed": False},
            {"item": self.rows[1], "reasons": [{"key": "late", "text": "atrasado"}, {"key": "cross", "text": "x"}], "last_contact": None, "snoozed": False},
            {"item": self.rows[2], "reasons": [{"key": "due", "text": "compra prevista"}], "snoozed": True,
             "last_contact": {"date": TODAY, "outcome_label": "Pedido fechado"}},
        ]
        agenda = {"items": items}
        result = intel_drill.agenda_detail(agenda, "previstas", {"due": ("Compra prevista", "is-info"), "late": ("Atrasado", "is-warn"), "cross": ("Venda cruzada", "is-info")})
        self.assertEqual(result["summary"][0]["value"], "2")
        self.assertEqual(result["summary"][1]["value"], dna._money(D("500"), compact=False))  # só quem está na fila
        self.assertEqual([row["position"] for row in result["sections"][0]["rows"]], [1, 3])
        with self.assertRaises(intel_drill.UnknownPoint):
            intel_drill.agenda_detail(agenda, "nada", {})


# ---------------------------------------------------------------------------
# DNA de Representantes e DNA do Cliente
# ---------------------------------------------------------------------------

class PeriodCardTests(SimpleTestCase):
    def test_period_summary_is_the_dna_summary(self):
        rows = {
            "billing": [billing_row(nota=1, data=date(2026, 3, 10)), billing_row(nota=2, data=date(2026, 4, 10), valor=D("1000"), qtd_dev=D("50"), pedido=10)],
            "previous": [billing_row(nota=9, data=date(2025, 3, 5), valor=D("1000"))],
        }
        run = fake_run(rows)
        with mock.patch.object(dna, "_run", run):
            result = chart_drill.representative_period_detail(10004, date(2026, 1, 1), date(2026, 12, 1), focus="pedidos")
            returns = chart_drill.representative_period_detail(10004, date(2026, 1, 1), date(2026, 12, 1), focus="devolucoes")
            term = chart_drill.representative_period_detail(10004, date(2026, 1, 1), date(2026, 12, 1), focus="prazo")
        totals = dna.summarize([dna.billing_line(row) for row in rows["billing"]])
        self.assertEqual(result["summary"][0]["value"], str(totals["orders"]))
        self.assertEqual(result["title"], "Pedidos faturados")
        self.assertEqual(returns["summary"][0]["value"], dna._money(totals["returned_value"], compact=False))
        self.assertEqual(returns["sections"][0]["key"], "returns")
        self.assertEqual(len(returns["sections"][0]["rows"]), 1)
        self.assertEqual(term["sections"][0]["rows"][0]["days"], 45.0)
        params = run.calls[0]["previous"][1]
        self.assertEqual((params["start_date"], params["end_date"]), (date(2025, 1, 1), date(2025, 12, 1)))

    def test_goal_range_and_family(self):
        rows = {"billing": [billing_row(data=date(2026, 3, 10)), billing_row(nota=2, data=date(2026, 5, 10), familia=" SACO MONO LISO")],
                "orders": [order_row(entrega=date(2026, 12, 10))], "production": []}
        goals = [{"year": 2026, "month": 3, "family": "BOBINA MONO IMPRESSO", "unit_code": 1, "weight_kg": 500},
                 {"year": 2026, "month": 12, "family": "SACO MONO LISO", "unit_code": 1, "weight_kg": 300}]
        with mock.patch.object(dna, "_run", fake_run(rows)):
            year = chart_drill.representative_goal_detail(10004, date(2026, 1, 1), date(2027, 1, 1), "Meta de 2026", goals=goals, today=TODAY)
            family = chart_drill.representative_goal_detail(10004, date(2026, 1, 1), date(2027, 1, 1), "Saco", goals=goals, today=TODAY, family="SACO MONO LISO")
        self.assertEqual(year["summary"][0]["label"], "Meta do período")
        self.assertEqual(year["summary"][0]["value"], dna._kg(D("800")))
        self.assertEqual(len(year["sections"][0]["rows"]), 2)
        self.assertEqual(len(year["sections"][1]["rows"]), 1)  # carteira com entrega em dezembro
        self.assertEqual(family["summary"][0]["value"], dna._kg(D("300")))
        self.assertEqual([row["group"] for row in family["sections"][0]["rows"]], ["Saco Mono Liso"])
        # Sem família escolhida, o subtítulo não cita grupo (os laços da meta reusam o nome da variável).
        self.assertNotIn(" · grupo", year["subtitle"])
        self.assertIn(" · grupo Saco Mono Liso", family["subtitle"])

    def test_intake_and_customer_cards(self):
        rows = {"orders": [order_row(emissao=date(2026, 3, 1)), order_row(sequencia=2, emissao=date(2025, 1, 1))], "production": [],
                "billing": [billing_row(data=date(2026, 3, 10)), billing_row(nota=2, data=date(2026, 3, 11), produto=77)], "previous": []}
        with mock.patch.object(dna, "_run", fake_run(rows)):
            intake = chart_drill.representative_intake_detail(10004, date(2026, 1, 1), date(2026, 12, 1), today=TODAY)
            product = chart_drill.customer_product_detail([500], date(2026, 1, 1), date(2026, 12, 1), 77)
            orders = chart_drill.customer_orders_detail([500], date(2026, 1, 1), date(2026, 12, 1), today=TODAY)
        self.assertEqual(len(intake["sections"][0]["rows"]), 1)  # só o emitido no período
        self.assertEqual(intake["summary"][0]["value"], dna._kg(D("200")))
        self.assertEqual([row["invoice"] for row in product["sections"][0]["rows"]], ["2"])
        self.assertEqual(len(orders["sections"][0]["rows"]), 2)  # os dois itens estão em aberto
        self.assertEqual(len(orders["sections"][1]["rows"]), 1)

    def test_customer_share_and_classification_use_the_page_window(self):
        rows = {"billing": [billing_row(data=date(2026, 3, 10))], "previous": []}
        ranking = {"customers_count": 10, "customer_abc": {500: {"class": "A", "position": 2, "cumulative": D("0.3"), "share": D("0.05"), "amount": D("1")}}}
        with mock.patch.object(dna, "_run", fake_run(rows)), mock.patch.object(dna, "company_ranking", return_value=ranking) as company:
            share = chart_drill.customer_period_detail([500], date(2025, 12, 1), date(2026, 11, 19), focus="participacao")
            classification = chart_drill.customer_period_detail([500], date(2025, 12, 1), date(2026, 11, 19), focus="classificacao")
        self.assertEqual(share["summary"][0]["value"], dna._pct(D("0.05"), decimals=2))
        self.assertEqual(company.call_args.args[0]["months"], 12)
        self.assertEqual(classification["title"], "Classificação Geral")
        self.assertEqual(classification["sections"][0]["key"], "months")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class KpiEndpointTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="drill-kpis", password="x", userId="drill-kpis", email="drill-kpis@example.com", is_system_admin=True,
        )
        self.login(self.admin)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_receivables_range_and_credits(self):
        today = date.today()
        month = today.strftime("%Y-%m")
        with mock.patch.object(dna, "_run", fake_run({"paid": [paid_row(pagamento=today, vencimento=today)], "credits": []})):
            paid = self.client.get(reverse("dashesReceivablesPanelDetail"), {"de": month, "ate": month})
            credits = self.client.get(reverse("dashesReceivablesPanelDetail"), {"creditos": "1"})
        self.assertEqual(paid.status_code, 200)
        self.assertEqual(credits.status_code, 200)
        self.assertEqual(self.client.get(reverse("dashesReceivablesPanelDetail"), {"de": month, "ate": "1999-01"}).status_code, 400)

    def test_portfolio_map_and_agenda(self):
        window = intel.resolve_window("12", today=TODAY)
        metrics = {1: metric(1, 1000, previous=800), 2: metric(2, 0, previous=500, city="TOLEDO", ibge=4127700)}
        prepared = ({"today": TODAY, "customers": {}, "mix": {}}, window, metrics)
        with mock.patch("tiqueue.intel_views._prepare", return_value=prepared):
            card = self.client.get(reverse("dashesCustomerPortfolioDetail"), {"indicador": "clientes", "periodo": "12"})
            bad = self.client.get(reverse("dashesCustomerPortfolioDetail"), {"indicador": "nada"})
            area = self.client.get(reverse("dashesCustomerMapDetail"), {"ponto": "uf:PR", "periodo": "12"})
            lost = self.client.get(reverse("dashesCustomerMapDetail"), {"ponto": "perdidas"})
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.json()["summary"][0]["value"], "1")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(area.status_code, 200)
        self.assertEqual(lost.json()["summary"][0]["value"], "1")
        agenda = {"items": [{"item": metrics[1], "reasons": [{"key": "due", "text": "x"}], "last_contact": None, "snoozed": False}]}
        SalesAgendaContact.objects.create(customer_code=1, customer_name="CLIENTE 1", outcome="pedido", user=self.admin)
        with mock.patch("tiqueue.management_views._agenda", return_value=({"customers": {}}, None, 7, agenda, [])):
            due = self.client.get(reverse("dashesSalesAgendaDetail"), {"indicador": "previstas"})
            contacts = self.client.get(reverse("dashesSalesAgendaDetail"), {"indicador": "contatos"})
        self.assertEqual(due.json()["summary"][0]["value"], "1")
        self.assertEqual(contacts.json()["summary"][1]["value"], "1")

    def test_representative_and_customer_cards(self):
        rows = {"billing": [billing_row(data=date(2026, 3, 10))], "previous": [], "orders": [order_row()], "production": []}
        url = reverse("dashesRepresentativeDnaDetail")
        with mock.patch.object(dna, "_run", fake_run(rows)):
            for params in ({"visual": "periodo", "foco": "volume", "inicio": "2025-12-01", "fim": "2026-11-18"},
                           {"visual": "mix-grupo", "familia": "BOBINA MONO IMPRESSO", "inicio": "2025-12-01", "fim": "2026-11-18"},
                           {"visual": "entrada", "inicio": "2025-12-01", "fim": "2026-11-18"},
                           {"visual": "meta-acumulado"}, {"visual": "meta-ano"}, {"visual": "meta-familia", "familia": "BOBINA MONO IMPRESSO"}):
                response = self.client.get(url, {"representante": "10004", **params})
                self.assertEqual(response.status_code, 200, params)
            self.assertEqual(self.client.get(url, {"representante": "10004", "visual": "meta-familia"}).status_code, 400)
            self.assertEqual(self.client.get(url, {"representante": "10004", "visual": "periodo"}).status_code, 400)
        scope = {"customer_codes": [500], "mode": "individual", "members": [{"code": 500, "name": "CLIENTE TESTE"}]}
        with mock.patch.object(dna, "_run", fake_run(rows)), mock.patch.object(dna.legacy, "_resolve_customer_scope", return_value=scope):
            for params in ({"visual": "periodo", "foco": "margem"}, {"visual": "mix-grupo", "familia": "BOBINA MONO IMPRESSO"},
                           {"visual": "produto", "produto": "80000500482"}, {"visual": "carteira"}):
                response = self.client.get(reverse("dashesCustomerDnaDetail"), {"cliente": "500", "inicio": "2025-12-01", "fim": "2026-11-18", **params})
                self.assertEqual(response.status_code, 200, params)
            self.assertEqual(self.client.get(reverse("dashesCustomerDnaDetail"), {"cliente": "500", "visual": "carteira"}).status_code, 400)
            group = self.client.get(reverse("dashesCustomerProfileDetail", args=[500]), {"grupo": "Bobina Mono Impresso"})
        self.assertEqual(group.status_code, 200)
        self.assertEqual(len(group.json()["sections"][0]["rows"]), 1)

    def test_new_details_need_the_permission_of_their_panel(self):
        user = User.objects.create_user(username="so-vendas-kpi", password="x", userId="so-vendas-kpi", email="vk@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="sales-panel"))
        self.login(user)
        for name, params in (("dashesCustomerMapDetail", {"ponto": "uf:PR"}), ("dashesSalesAgendaDetail", {"indicador": "previstas"}),
                             ("dashesCustomerPortfolioDetail", {"indicador": "lucro"})):
            self.assertEqual(self.client.get(reverse(name), params).status_code, 403, name)
