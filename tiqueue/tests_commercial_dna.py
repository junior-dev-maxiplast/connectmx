"""Regras do DNA comercial v2 (portadas dos PBIX de Vendas e Orçamento 2026)."""

import base64
import json
import zlib
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import commercial_dna as dna
from .models import Dashboard, DashboardAccess, RepresentativeGoal
from .representative_goals import decode_embedded_table, normalize_goal_rows


D = Decimal


def billing_row(**overrides):
    row = {
        "empresa": 1, "filial": 1, "nota": 100, "sequencia": 1, "data": date(2026, 10, 10),
        "cliente": 500, "nome_cliente": "CLIENTE TESTE", "cnpj": "12345678000199", "cidade": "CASCAVEL", "uf": "PR",
        "mercado": "I", "representante": 10004, "nome_representante": "LJM", "apelido_representante": "LJM",
        "produto": 80000500482, "descricao_produto": "BOBINA TESTE", "complemento": "BOBINA TESTE",
        "familia": " FILME MONO IMPRESSO", "qtd": D("100"), "qtd_dev": D("0"), "peso": D("100"), "valor": D("2000"),
        "icms": D("240"), "pis": D("33"), "cofins": D("152"), "pedido": 9, "filial_producao": 1,
        "condicao_pagamento": "30/60", "prazo_medio": D("45"), "cpv": D("1200"),
    }
    row.update(overrides)
    return row


class ProductRulesTests(SimpleTestCase):
    def test_product_group_follows_the_pbix_conditional_columns(self):
        self.assertEqual(dna.product_group(" FILME MONO IMPRESSO", "X"), "BOBINA MONO IMPRESSO")
        self.assertEqual(dna.product_group(" SACO LAMINADO LISO", ""), "SACO LAMINADO LISO")
        self.assertEqual(dna.product_group(" TECIDO CONVENCIONAL LISO", ""), "BOBINA CONVENCIONAL LISO")
        self.assertEqual(dna.product_group(" FOLHA", ""), "FOLHA MONO LISO")

    def test_bi_laminado_comes_from_the_text_between_the_second_and_third_plus(self):
        # Text.BetweenDelimiters(DS_PRODUTO, "+", "+", 1, 0) => "PETA"
        self.assertEqual(dna.product_group(" FILME LAMINADO IMPRESSO", "BOB+PE+PETA+PE"), "BOBINA BI LAMINADO IMPRESSO")
        self.assertEqual(dna.product_group(" FILME LAMINADO IMPRESSO", "BOB+PE+PET+PE"), "BOBINA LAMINADO IMPRESSO")

    def test_budget_unit_priority(self):
        self.assertEqual(dna.budget_unit(" SACO BIG BAG", "SACO MONO LISO", 1, 1, 1), 3)
        self.assertEqual(dna.budget_unit(" FILME ADESIVADO", "BOBINA ADESIVADO LISO", 1, 1, 1), 5)
        self.assertEqual(dna.budget_unit(" FILME", "BOBINA MONO LISO", 4, 1, 1), 4)
        self.assertEqual(dna.budget_unit(" FILME", "BOBINA MONO LISO", None, 2, 1), 2)
        self.assertEqual(dna.budget_unit(" FILME", "BOBINA LAMINADO LISO", None, 1, 1), 4)
        self.assertEqual(dna.budget_unit(" FILME", "BOBINA MONO LISO", None, 1, 80000101227), 4)
        self.assertEqual(dna.budget_unit(" FILME", "BOBINA MONO LISO", None, 1, 1), 1)

    def test_representative_aliases_are_consolidated(self):
        self.assertEqual(dna.primary_rep(10042), 10048)
        self.assertEqual(dna.primary_rep("1004"), 1001)
        self.assertEqual(dna.primary_rep(10004), 10004)
        self.assertEqual(dna.rep_codes(10048), [10042, 10048])
        self.assertEqual(dna.rep_team(10065), "Equipe interna")


class BillingLineTests(SimpleTestCase):
    def test_partial_return_discounts_value_weight_and_cost(self):
        line = dna.billing_line(billing_row(qtd_dev=D("25")))
        self.assertEqual(line["returned_value"], D("500"))
        self.assertEqual(line["net"], D("1500"))          # VL_FAT-DEV
        self.assertEqual(line["net_weight"], D("75"))     # VL_PESO -DEV
        self.assertEqual(line["net_of_taxes"], D("1075"))  # 1500 - (240 + 33 + 152)
        self.assertEqual(line["cpv"], D("900"))
        self.assertTrue(line["included"])

    def test_fully_returned_item_leaves_the_sums(self):
        line = dna.billing_line(billing_row(qtd_dev=D("100")))
        self.assertFalse(line["included"])
        totals = dna.summarize([line])
        self.assertEqual(totals["net"], D("0"))
        self.assertEqual(totals["returned_value"], D("2000"))

    def test_negative_net_of_taxes_is_zeroed(self):
        line = dna.billing_line(billing_row(icms=D("5000")))
        self.assertEqual(line["net_of_taxes"], D("0"))

    def test_summary_measures(self):
        totals = dna.summarize([dna.billing_line(billing_row()), dna.billing_line(billing_row(nota=101, pedido=10))])
        self.assertEqual(totals["net"], D("4000"))
        self.assertEqual(totals["fator_kg"], D("20"))                 # FATOR KG
        self.assertEqual(totals["fator_kg_clean"], D("15.75"))        # FATOR KG LIMPO
        self.assertEqual(round(totals["margin"], 4), round(1 - D("2400") / D("3150"), 4))
        self.assertEqual(totals["orders"], 2)
        self.assertEqual(totals["invoices"], 2)
        self.assertEqual(totals["ticket"], D("2000"))


class GroupFilterTests(SimpleTestCase):
    def test_only_groups_keeps_the_chosen_product_groups(self):
        line = dna.billing_line(billing_row())
        other = {**line, "group": "OUTRO GRUPO"}
        self.assertEqual(dna.only_groups([line, other], None), [line, other])
        self.assertEqual(dna.only_groups([line, other], []), [line, other])
        self.assertEqual(dna.only_groups([line, other], [line["group"]]), [line])
        self.assertEqual(dna.only_groups([line, other], [line["group"], "OUTRO GRUPO"]), [line, other])
        self.assertEqual(dna.only_groups([line, other], ["NENHUM"]), [])


class ClassificationTests(SimpleTestCase):
    def test_pareto_is_inclusive_and_dense(self):
        result = dna.pareto({1: 50, 2: 30, 3: 15, 4: 5, 5: 0})
        self.assertEqual(result[1]["class"], "A")   # 50%
        self.assertEqual(result[2]["class"], "A")   # 80%
        self.assertEqual(result[3]["class"], "B")   # 95%
        self.assertEqual(result[4]["class"], "C")
        self.assertNotIn(5, result)
        self.assertEqual(result[3]["position"], 3)

    def test_a_dominant_first_customer_is_b_as_in_the_pbix(self):
        result = dna.pareto({1: 85, 2: 15})
        self.assertEqual(result[1]["class"], "B")

    def test_thresholds(self):
        self.assertEqual(dna.margin_class(D("0.05")), "C")
        self.assertEqual(dna.margin_class(D("-0.3")), "C")
        self.assertEqual(dna.margin_class(D("0.15")), "B")
        self.assertEqual(dna.margin_class(D("0.151")), "A")
        self.assertEqual(dna.lot_class(D("1000")), "C")
        self.assertEqual(dna.lot_class(D("2000")), "B")
        self.assertEqual(dna.lot_class(D("2001")), "A")
        self.assertEqual(dna.recurrence(12, 12)[:2], ("A", "Compra mensal"))
        self.assertEqual(dna.recurrence(12, 4)[:2], ("A", "Compra trimestral"))
        self.assertEqual(dna.recurrence(12, 2)[:2], ("B", "Compra sazonal (até 6 meses)"))
        self.assertEqual(dna.recurrence(12, 1)[:2], ("C", "Compra anual"))
        self.assertEqual(dna.recurrence(12, 0), (None, None, None))

    def test_classification_score_and_orientation(self):
        best = dna.classify({"revenue": "A", "margin": "A", "lot": "A", "recurrence": "A"})
        self.assertEqual(best["code"], "AAAA")
        self.assertEqual(best["score"], D("10.00"))
        self.assertIn("Cliente ideal", best["orientation"])
        worst = dna.classify({"revenue": "C", "margin": "C", "lot": "C", "recurrence": "C"})
        self.assertEqual(worst["score"], D("3.32"))
        incomplete = dna.classify({"revenue": "A", "margin": None, "lot": "B", "recurrence": "A"})
        self.assertFalse(incomplete["complete"])
        self.assertEqual(incomplete["code"], "A–BA")
        self.assertIn("margem", incomplete["orientation"])
        self.assertEqual(len(dna.ORIENTATION), 81)

    def test_inactivity_bands(self):
        self.assertEqual(dna.inactivity_band(60)["key"], "ate-60")
        self.assertEqual(dna.inactivity_band(61)["key"], "60-90")
        self.assertEqual(dna.inactivity_band(181)["key"], "180+")


class OrderItemTests(SimpleTestCase):
    def test_to_bill_ready_and_overdue(self):
        item = dna.order_item(
            {
                "empresa": 1, "filial": 1, "pedido": 7, "sequencia": 1, "emissao": date(2026, 9, 1),
                "entrega": date(2026, 10, 1), "situacao_item": 2, "cliente": 500, "representante": 10004,
                "descricao_produto": "SACO", "familia": " SACO MONO LISO",
                "qtd_pedida": D("100"), "qtd_faturada": D("30"), "qtd_cancelada": D("10"), "qtd_aberta": D("60"),
                "preco_unitario": D("20"), "peso_unitario": D("2"), "qtd_produzida": D("80"),
            },
            today=date(2026, 11, 4),
        )
        self.assertEqual(item["to_bill_kg"], D("120"))      # (100 - 30 - 10) x 2
        self.assertEqual(item["to_bill_value"], D("1200"))  # fator 10 R$/kg
        self.assertEqual(item["ready_kg"], D("80"))         # 160 produzido - 80 atendido
        self.assertTrue(item["is_overdue"])

    def test_closed_item_has_nothing_to_bill(self):
        item = dna.order_item({"situacao_item": 4, "qtd_pedida": D("10"), "peso_unitario": D("1"), "preco_unitario": D("1")})
        self.assertFalse(item["is_open"])
        self.assertEqual(item["to_bill_kg"], D("0"))


class WindowAndGoalTests(SimpleTestCase):
    def test_windows(self):
        today = date(2026, 11, 4)
        twelve = dna.resolve_window("12", today=today)
        self.assertEqual((twelve["start"], twelve["end"], twelve["months"]), (date(2025, 12, 1), date(2026, 11, 5), 12))
        self.assertEqual(twelve["previous_start"], date(2024, 12, 1))
        ytd = dna.resolve_window("ytd", today=today)
        self.assertEqual((ytd["start"], ytd["months"]), (date(2026, 1, 1), 11))
        last_year = dna.resolve_window("ano-anterior", today=today)
        self.assertEqual((last_year["start"], last_year["end"], last_year["months"]), (date(2025, 1, 1), date(2026, 1, 1), 12))
        custom = dna.resolve_window("12", date(2026, 3, 1), date(2026, 3, 31), today=today)
        self.assertEqual((custom["key"], custom["end"], custom["months"]), ("range", date(2026, 4, 1), 1))
        self.assertEqual(dna.resolve_window("xpto", today=today)["key"], "12")

    def test_goal_tracking_adds_billed_and_portfolio(self):
        today = date(2026, 11, 4)
        goals = [
            {"year": 2026, "month": 10, "unit_code": 1, "family": "BOBINA MONO IMPRESSO", "weight_kg": "200"},
            {"year": 2026, "month": 11, "unit_code": 1, "family": "BOBINA MONO IMPRESSO", "weight_kg": "100"},
        ]
        lines = [dna.billing_line(billing_row(data=date(2026, 10, 10)))]  # 100 kg em outubro
        order = dna.order_item(
            {"situacao_item": 1, "qtd_pedida": D("50"), "peso_unitario": D("1"), "preco_unitario": D("10"),
             "entrega": date(2026, 11, 20), "familia": " FILME MONO IMPRESSO", "descricao_produto": "BOB"},
            today=today,
        )
        tracking = dna._goal_tracking(goals, lines, [order], today)
        self.assertTrue(tracking["has_goals"])
        october, november = tracking["months"][9], tracking["months"][10]
        self.assertEqual((october["goal"], october["billed"], october["pct"]), (200.0, 100.0, 0.5))
        self.assertEqual((november["to_bill"], november["pct"]), (50.0, 0.5))
        self.assertEqual(tracking["ytd"]["pct_display"], "50,0%")
        self.assertEqual(tracking["families"][0]["pct"], 50)


class EmbeddedGoalsTests(SimpleTestCase):
    def test_embedded_table_is_decoded_and_blank_weights_are_skipped(self):
        rows = [
            ["1", "10004", " LJM ", "2026", "42600", "BOBINA MONO IMPRESSO", "6", "01/01/2026", "1"],
            ["1", "10004", " LJM ", "2026", "", "BOBINA MONO LISO", "7", "01/01/2026", "1"],
        ]
        raw = zlib.compress(json.dumps(rows).encode("utf-8"))[2:-4]  # deflate cru, como o Power BI grava
        m_source = (
            'let Fonte = Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText("'
            + base64.b64encode(raw).decode()
            + '", BinaryEncoding.Base64), Compression.Deflate)), let _t = ((type nullable text) meta [Serialized.Text = true]) '
            'in type table [#"1" = _t, CD_REPRESENTANTE = _t, DS_REPRESENTANTE = _t, ANO = _t, PESO = _t, FAMILIA = _t, COD = _t, MES = _t, MES.1 = _t]) in Fonte'
        )
        table = decode_embedded_table(m_source)
        self.assertEqual(table["columns"][0], "1")
        goals = normalize_goal_rows(table)
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["rep_code"], 10004)
        self.assertEqual(goals[0]["rep_name"], "LJM")
        self.assertEqual(goals[0]["weight_kg"], "42600")


User = get_user_model()


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class CommercialDnaPagesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="comercial", password="secret123", userId="comercial", email="comercial@example.com",
            is_system_admin=True,
        )
        self.client.force_login(self.admin)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def test_migration_seeds_goals_and_dashboards(self):
        self.assertTrue(RepresentativeGoal.objects.filter(year=2026, rep_code=10004).exists())
        # A versão nova substitui a anterior no catálogo, sem criar outro item.
        self.assertEqual(Dashboard.objects.get(slug="customer-dna").url_name, "dashesCustomerDnaV2Page")
        self.assertFalse(Dashboard.objects.filter(slug="customer-dna-v2").exists())
        self.assertTrue(Dashboard.objects.filter(slug="representative-dna").exists())

    def test_previous_version_stays_reachable_with_the_same_permission(self):
        user = User.objects.create_user(username="dna", password="x", userId="dna", email="dna@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="customer-dna"))
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()
        with mock.patch.object(dna.legacy, "_oracle_connection_safe", side_effect=RuntimeError("sem rede")):
            new_page = self.client.get(reverse("dashesCustomerDnaV2Page"))
            previous_page = self.client.get(reverse("dashesCustomerDnaPage"))
        self.assertEqual(new_page.status_code, 200)
        # Link "DNA (versão anterior)" no menu (o endereço novo tem o antigo como prefixo).
        self.assertContains(new_page, f'href="{reverse("dashesCustomerDnaPage")}"')
        self.assertEqual(previous_page.status_code, 200)
        self.assertRedirects(self.client.get(reverse("dashesHome")), reverse("dashesCustomerDnaV2Page"), fetch_redirect_response=False)

    def test_pages_render_when_the_erp_is_unreachable(self):
        # A conexão é a do DNA original: as duas telas passam por ela.
        with mock.patch.object(dna.legacy, "_oracle_connection_safe", side_effect=RuntimeError("sem rede")):
            for name in ("dashesCustomerDnaV2Page", "dashesRepresentativeDnaPage"):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Não foi possível consultar o ERP Senior")
                self.assertContains(response, "MaxiSys")

    def test_representative_page_requires_its_own_permission(self):
        user = User.objects.create_user(username="vendas", password="x", userId="vendas", email="vendas@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="customer-dna"))
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()
        response = self.client.get(reverse("dashesRepresentativeDnaPage"))
        self.assertEqual(response.status_code, 403)
