"""Painéis-padrão animados: cálculos (sem Oracle) e as telas de Vendas, Contas a Receber, Entregas e Qualidade."""

from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import panels
from .models import Dashboard, DashboardAccess

D = Decimal
TODAY = date(2026, 11, 9)
User = get_user_model()


def fake_rows(statements):
    """Responde às consultas dos painéis pelo nome, como o `commercial_dna._run`."""
    rows = {}
    for name in statements:
        if name == "daily":
            rows[name] = [
                {"dia": datetime(2026, 11, 3), "cliente": 1, "representante": 10004, "empresa": 1, "filial": 1, "familia": "FILME MONO IMPRESSO", "bi_laminado": 0, "uf": "PR", "liquido": D(1000), "peso": D(40)},
                {"dia": datetime(2026, 11, 4), "cliente": 2, "representante": 10001, "empresa": 1, "filial": 2, "familia": "SACO IMPRESSO", "bi_laminado": 0, "uf": "SC", "liquido": D(500), "peso": D(20)},
                {"dia": datetime(2025, 11, 4), "cliente": 1, "representante": 10004, "empresa": 1, "filial": 1, "familia": "FILME MONO IMPRESSO", "bi_laminado": 0, "uf": "PR", "liquido": D(800), "peso": D(40)},
            ]
        elif name == "intake":
            rows[name] = [{"dia": datetime(2026, 11, 2), "cliente": 1, "representante": 10004, "empresa": 1, "filial": 1, "uf": "PR", "valor": D(2000), "peso": D(80), "pedidos": 2}]
        elif name == "monthly":
            rows[name] = [
                {"mes": datetime(2026, 10, 1), "representante": 10004, "empresa": 1, "filial": 1, "familia": "FILME MONO IMPRESSO", "bi_laminado": 0, "liquido": D(100), "peso": D(5)},
                {"mes": datetime(2026, 10, 1), "representante": 10004, "empresa": 1, "filial": 1, "familia": "FILME MONO IMPRESSO FINO", "bi_laminado": 0, "liquido": D(50), "peso": D(2)},
            ]
        elif name == "calendar":
            rows[name] = [{"dia": datetime(2026, 11, 3), "liquido": D(1000)}]
        elif name == "reps":
            rows[name] = [{"codigo": 10004, "nome": "LJM REPRESENTACOES"}, {"codigo": 10001, "nome": "VS&JE"}]
        elif name == "branches":
            rows[name] = [{"empresa": 1, "filial": 1, "sigla": "MATRIZ"}, {"empresa": 1, "filial": 2, "sigla": "RAFIA (F02)"}]
        elif name == "customers":
            rows[name] = [{"cliente": 1, "nome": "FRIGORIFICO UM", "cidade": "CURITIBA", "uf": "PR"}, {"cliente": 2, "nome": "CLIENTE DOIS", "cidade": "CHAPECO", "uf": "SC"}]
        elif name == "titles":
            rows[name] = [
                {"empresa": 1, "filial": 1, "titulo": "123/1", "tipo": "NFV", "cliente": 1, "representante": 10004, "emissao": datetime(2026, 9, 1),
                 "vencimento": datetime(2026, 10, 1), "vencimento_original": datetime(2026, 9, 25), "aberto": D(1000), "original": D(1000),
                 "portador": "341", "nome_portador": "BANCO ITAU", "nome": "FRIGORIFICO UM", "cidade": "CURITIBA", "uf": "PR"},
                {"empresa": 1, "filial": 1, "titulo": "124/1", "tipo": "NFV", "cliente": 2, "representante": 10001, "emissao": datetime(2026, 11, 1),
                 "vencimento": datetime(2026, 11, 12), "vencimento_original": datetime(2026, 11, 12), "aberto": D(3000), "original": D(3000),
                 "portador": "9999", "nome_portador": "PROPRIA EMPRESA", "nome": "CLIENTE DOIS", "cidade": "CHAPECO", "uf": "SC"},
            ]
        elif name == "credits":
            rows[name] = [{"tipo": "DEV", "descricao": "DEVOLUÇÃO", "titulos": 1, "aberto": D(50)}]
        elif name == "paid":
            rows[name] = [{"mes": datetime(2026, 10, 1), "titulos": 10, "pago": D(1000), "pago_em_dia": D(800), "atraso_ponderado": D(500), "prazo_ponderado": D(60000)}]
        elif name == "billed":
            rows[name] = [{"emitido": D(9000)}]
        elif name == "items":
            rows[name] = [
                # no prazo e completo
                {"empresa": 1, "filial": 1, "pedido": 1, "sequencia": 1, "entrega": datetime(2026, 10, 10), "emissao": datetime(2026, 9, 1), "cliente": 1, "representante": 10004,
                 "situacao": 4, "familia": "SACO IMPRESSO", "produto": "SACO", "qtd_pedida": D(100), "qtd_cancelada": D(0), "qtd_aberta": D(0), "preco": D(2), "peso_unitario": D(1),
                 "qtd_faturada": D(100), "qtd_no_prazo": D(100), "ultima_nota": datetime(2026, 10, 9)},
                # atrasado 5 dias
                {"empresa": 1, "filial": 1, "pedido": 2, "sequencia": 1, "entrega": datetime(2026, 10, 10), "emissao": datetime(2026, 9, 1), "cliente": 1, "representante": 10004,
                 "situacao": 4, "familia": "SACO IMPRESSO", "produto": "SACO", "qtd_pedida": D(100), "qtd_cancelada": D(0), "qtd_aberta": D(0), "preco": D(2), "peso_unitario": D(1),
                 "qtd_faturada": D(100), "qtd_no_prazo": D(0), "ultima_nota": datetime(2026, 10, 15)},
                # vencido e ainda aberto
                {"empresa": 1, "filial": 1, "pedido": 3, "sequencia": 1, "entrega": datetime(2026, 11, 1), "emissao": datetime(2026, 10, 1), "cliente": 2, "representante": 10001,
                 "situacao": 1, "familia": "SACO IMPRESSO", "produto": "SACO", "qtd_pedida": D(50), "qtd_cancelada": D(0), "qtd_aberta": D(50), "preco": D(2), "peso_unitario": D(1),
                 "qtd_faturada": D(0), "qtd_no_prazo": D(0), "ultima_nota": None},
                # entrega futura: fora do indicador
                {"empresa": 1, "filial": 1, "pedido": 4, "sequencia": 1, "entrega": datetime(2026, 11, 30), "emissao": datetime(2026, 11, 1), "cliente": 2, "representante": 10001,
                 "situacao": 1, "familia": "SACO IMPRESSO", "produto": "SACO", "qtd_pedida": D(50), "qtd_cancelada": D(0), "qtd_aberta": D(50), "preco": D(2), "peso_unitario": D(1),
                 "qtd_faturada": D(0), "qtd_no_prazo": D(0), "ultima_nota": None},
            ]
        elif name == "complaints":
            rows[name] = [
                {"data": datetime(2026, 11, 6), "cliente": 1, "classe": "G", "problema": "Solda não conforme", "maquina": "SOLDADEIRA 3", "volume": D(10), "unidade": "KG"},
                {"data": datetime(2026, 5, 6), "cliente": 2, "classe": None, "problema": None, "maquina": None, "volume": None, "unidade": None},
            ]
        elif name == "returns":
            rows[name] = [{"data": datetime(2026, 10, 20), "cliente": 1, "valor": D(300), "problema": "Impressão", "setor": "IMPRESSÃO", "filial": "MATRIZ"}]
        elif name == "revenue":
            rows[name] = [{"mes": datetime(2026, 10, 1), "liquido": D(10000)}]
        else:
            rows[name] = []
    return rows


class PanelRulesTests(SimpleTestCase):
    def test_business_days(self):
        days = panels.business_days(date(2026, 11, 1), TODAY)
        self.assertEqual((days["total"], days["elapsed"], days["days"]), (21, 6, 30))
        self.assertIn(1, days["weekend"])  # 1/11/2026 é domingo
        self.assertEqual(panels.business_days(date(2026, 10, 1), TODAY)["elapsed"], 22)

    def test_aging_and_delay_buckets(self):
        self.assertEqual([panels.aging_bucket(date(2026, 11, 9) - __import__("datetime").timedelta(days=d), TODAY) for d in (91, 61, 31, 1, 0)],
                         ["v90", "v61", "v31", "v1", "a7"])
        self.assertEqual(panels.aging_bucket(date(2026, 12, 20), TODAY), "a60")
        self.assertEqual([panels.delay_bucket(days) for days in (-2, 0, 3, 7, 15, 30, 31)], ["early", "ontime", "d3", "d7", "d15", "d30", "d30p"])

    def test_service_item_flags(self):
        base = {"qtd_pedida": D(100), "qtd_cancelada": D(10), "qtd_faturada": D(85), "qtd_no_prazo": D(81), "entrega": date(2026, 10, 10),
                "emissao": date(2026, 9, 1), "ultima_nota": date(2026, 10, 12), "peso_unitario": D(2), "situacao": 4, "qtd_aberta": D(0), "preco": D(1)}
        item = panels.service_item(base, TODAY)
        self.assertTrue(item["full"])  # 85 de 90 pedidos (≥ 90%)
        self.assertTrue(item["otif"])  # 81 no prazo (≥ 81)
        self.assertEqual((item["delay"], item["lead"], item["kg"]), (2, 41, D(180)))
        short = panels.service_item({**base, "qtd_faturada": D(50), "qtd_no_prazo": D(50)}, TODAY)
        self.assertFalse(short["full"] or short["otif"] or short["pending"])
        self.assertIsNone(panels.service_item({**base, "qtd_pedida": D(10), "qtd_cancelada": D(10)}, TODAY))

    def test_sales_and_monthly_facts(self):
        rows = fake_rows(["daily", "monthly"])
        facts = panels.sales_facts(rows["daily"], date(2026, 11, 1))
        self.assertEqual([fact["y"] for fact in facts], [0, 0, 1])
        self.assertEqual(facts[0]["g"], "Bobina Mono Impresso")
        self.assertEqual(facts[1]["f"], "1-2")
        monthly = panels.monthly_facts(rows["monthly"])
        self.assertEqual(len(monthly), 1)  # as duas famílias caem no mesmo grupo
        self.assertEqual(monthly[0]["n"], 150.0)

    def test_receivable_and_quality_facts(self):
        facts, customers, holders = panels.receivable_facts(fake_rows(["titles"])["titles"], TODAY)
        self.assertEqual([fact["b"] for fact in facts], ["v31", "a7"])
        self.assertEqual(facts[0]["late"], 39)
        self.assertTrue(facts[0]["renegotiated"])
        self.assertEqual(holders["341"], "Banco Itau")
        complaints, returns, codes = panels.quality_facts(fake_rows(["complaints"])["complaints"], fake_rows(["returns"])["returns"])
        self.assertEqual([row["s"] for row in complaints], ["G", "L"])  # gravidade vazia vira leve
        self.assertEqual(complaints[1]["p"], "Não informado")
        self.assertEqual(codes, {1, 2})

    def test_cache_version_is_bumped(self):
        cache.clear()
        calls = []
        loader = lambda: calls.append(1) or len(calls)  # noqa: E731
        self.assertEqual(panels.cached("x", 60, loader), 1)
        self.assertEqual(panels.cached("x", 60, loader), 1)
        panels.clear_cache()
        self.assertEqual(panels.cached("x", 60, loader), 2)


class PanelPayloadTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        patcher = mock.patch.object(panels.dna, "_run", side_effect=fake_rows)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_service_counts_overdue_items_as_failures(self):
        payload = panels.service_payload(TODAY)
        buckets = {}
        for fact in payload["facts"]:
            buckets[fact["b"]] = buckets.get(fact["b"], 0) + fact["i"]
        # Nota de 09/10 para entrega em 10/10 = adiantado; atraso de 5 dias = 4 a 7; o vencido aberto conta como falha.
        self.assertEqual(buckets, {"early": 1, "d7": 1, "open": 1})
        self.assertEqual(sum(fact["o"] for fact in payload["facts"]), 1)
        self.assertEqual([row["late"] for row in payload["overdue"]], [8])

    def test_receivables_and_sales_payloads(self):
        receivables = panels.receivables_payload(TODAY)
        self.assertEqual(receivables["daily_billing"], 100.0)  # 9.000 ÷ 90
        self.assertEqual(receivables["paid"][0]["term"], 60.0)
        sales = panels.sales_payload(date(2026, 11, 1), TODAY, [{"year": 2026, "month": 11, "rep_code": 10004, "weight_kg": D(1000)}])
        self.assertTrue(sales["month"]["is_current"])
        self.assertEqual(sales["goals"], {"10004": 1000.0})
        self.assertEqual(sales["names"]["customers"]["1"]["name"], "FRIGORIFICO UM")
        quality = panels.quality_payload(TODAY)
        self.assertEqual(quality["days_since_grave"], 3)

    def test_sales_payload_for_several_months(self):
        goals = [{"year": 2026, "month": 11, "rep_code": 10004, "weight_kg": D(1000)},
                 {"year": 2026, "month": 10, "rep_code": 10004, "weight_kg": D(500)},
                 {"year": 2026, "month": 9, "rep_code": 10004, "weight_kg": D(999)}]
        sales = panels.sales_payload([date(2026, 11, 1), date(2026, 10, 1)], TODAY, goals)
        october, november = 2026 * 12 + 9, 2026 * 12 + 10
        self.assertTrue(sales["month"]["multi"])
        self.assertEqual(sales["month"]["keys"], ["2026-10", "2026-11"])
        self.assertEqual(sales["month"]["label"], "out/26 a nov/26")
        self.assertEqual(sales["month"]["current_key"], november)
        self.assertEqual(sales["selected_months"], [october, november])
        # Meta: só os meses escolhidos; dias úteis somados; fatos sem o dia, com o mês.
        self.assertEqual(sales["goals"], {"10004": 1500.0})
        self.assertEqual(sales["business"]["total"], sum(month["total"] for month in sales["business"]["months"]))
        self.assertTrue(sales["facts"] and all("mo" in fact and "d" not in fact for fact in sales["facts"]))
        self.assertEqual(sales["months"][-1]["key"], november)

    def test_collapse_sales_cuts_last_year_at_the_same_day(self):
        row = {"c": 1, "r": 10004, "f": "1-1", "g": "Grupo", "u": "PR", "k": 1}
        facts = [{**row, "d": 3, "y": 0, "n": 10}, {**row, "d": 3, "y": 1, "n": 5}, {**row, "d": 20, "y": 1, "n": 7}]
        self.assertEqual({fact["y"]: fact["n"] for fact in panels.collapse_sales(facts, 99, cut_day=10)}, {0: 10, 1: 5, 2: 7})
        self.assertEqual({fact["y"]: fact["n"] for fact in panels.collapse_sales(facts, 99)}, {0: 10, 1: 12})
        self.assertEqual(panels.months_label([2026 * 12 + 8, 2026 * 12 + 10]), "set/26 e nov/26")
        self.assertEqual(panels.months_label([2026 * 12 + 8]), "set/26")


@override_settings(ALLOWED_HOSTS=["testserver"])
class PanelPagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(username="painel", password="x", userId="painel", email="p@example.com", is_system_admin=True)
        self.login(self.admin)
        patcher = mock.patch.object(panels.dna, "_run", side_effect=fake_rows)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_pages_render_and_json(self):
        for name, text in (("dashesSalesPanel", "Painel de Vendas"), ("dashesReceivablesPanel", "Contas a Receber"),
                           ("dashesServicePanel", "Entregas (OTIF)"), ("dashesQualityPanel", "Dias sem reclamação grave")):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, text)
            self.assertContains(response, "maxisysLive.js")
        payload = self.client.get(reverse("dashesSalesPanel") + "?formato=json").json()
        self.assertIn("facts", payload)
        self.assertEqual(payload["month"]["key"], date.today().replace(day=1).strftime("%Y-%m"))

    def test_several_months_in_the_address(self):
        current = date.today().replace(day=1)
        previous = panels.dna._add_months(current, -1)
        query = f"?mes={previous:%Y-%m},{current:%Y-%m},{current:%Y-%m},xx"
        payload = self.client.get(reverse("dashesSalesPanel") + query + "&formato=json").json()
        self.assertEqual(payload["month"]["keys"], [f"{previous:%Y-%m}", f"{current:%Y-%m}"])
        page = self.client.get(reverse("dashesSalesPanel") + query)
        self.assertContains(page, "Faturamento por mês")
        self.assertContains(page, "Faturamento no período")

    def test_month_outside_the_window_is_clamped(self):
        response = self.client.get(reverse("dashesSalesPanel") + "?mes=1999-01&formato=json")
        oldest = panels.dna._add_months(date.today().replace(day=1), -23)
        self.assertEqual(response.json()["month"]["key"], oldest.strftime("%Y-%m"))
        self.assertEqual(self.client.get(reverse("dashesSalesPanel") + "?mes=xx").status_code, 200)

    def test_each_panel_needs_its_own_permission(self):
        user = User.objects.create_user(username="vend", password="x", userId="vend", email="v@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="sales-panel"))
        self.login(user)
        self.assertEqual(self.client.get(reverse("dashesSalesPanel")).status_code, 200)
        for name in ("dashesReceivablesPanel", "dashesServicePanel", "dashesQualityPanel"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_erp_failure_is_reported(self):
        with mock.patch.object(panels.dna, "_run", side_effect=RuntimeError("sem rede")):
            cache.clear()
            response = self.client.get(reverse("dashesReceivablesPanel"))
            json_response = self.client.get(reverse("dashesReceivablesPanel") + "?formato=json")
        self.assertContains(response, "Não foi possível consultar o ERP Senior")
        self.assertEqual(json_response.status_code, 502)
