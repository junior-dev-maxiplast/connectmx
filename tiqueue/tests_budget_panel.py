"""Painel "Orçamento" do Dashes: orçamento do PBIX e da projeção (sales_budget), fatos do painel
(budget_panel), metas do ano vindas da USU_TORCMET/USU_TORCGER (Oracle simulado em memória), as telas
e o comando `importar_orcamento`. Nenhum teste toca o ERP."""

import base64
import copy
import datetime
import json
import os
import zlib
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import budget_panel, commercial_dna, sales_budget, sales_goals
from .models import Dashboard, DashboardAccess, RepresentativeGoal, SalesBreakEven, SalesBudget
from .tests_sales_goals import FakeOracle, TempDirMixin, build_workbook

D = Decimal
User = get_user_model()
TODAY = datetime.date(2026, 11, 24)
BUDGET_COLUMNS = ["MES/ANO", "MES", "ANO", "GRUPO", "Nº", "PESO", "FATOR KG", "UNIDADE", "SUBGRUPO 2"]


def m_table(columns, rows):
    """Tabela "Inserir dados" do PBIX: JSON comprimido (deflate) em base64 dentro do código M."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    payload = compressor.compress(json.dumps(rows).encode("utf-8")) + compressor.flush()
    encoded = base64.b64encode(payload).decode("ascii")
    declared = ", ".join(f'#"{column}" = _t' for column in columns)
    return (
        'let\n    Fonte = Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText('
        f'"{encoded}", BinaryEncoding.Base64), Compression.Deflate)), '
        f'let _t = ((type nullable text) meta [Serialized.Text = true]) in type table [{declared}])\nin\n    Fonte'
    )


def write_pbix_queries(folder):
    rows = [
        ["01/2026", "JANEIRO", "2026", "BOBINA MONO", "0", "1000", "20,00", "1", "IMPRESSO"],
        # Sub-orçamento da mesma família no mesmo mês: soma.
        ["01/2026", "JANEIRO", "2026", "BOBINA MONO", "0", "500", "22,00", "1", "IMPRESSO"],
        # Na Ráfia, "MONO" é a ráfia convencional.
        ["02/2026", "FEVEREIRO", "2026", "SACARIA MONO", "0", "300", "15,50", "2", "LISO"],
        ["02/2026", "FEVEREIRO", "2026", "SACARIA LAMINADA", "0", "200", "18", "2", "IMPRESSO"],
        ["03/2026", "MARÇO", "2026", "BOBINA LAMINADA", "0", "400", "25", "4", "IMPRESSO"],
        ["04/2026", "ABRIL", "2026", "XYZ", "0", "100", "10", "1", "LISO"],
        ["04/2026", "ABRIL", "2026", "BOBINA MONO", "0", "0", "10", "1", "LISO"],
    ]
    with open(os.path.join(folder, "16_ORÇAMENTO 2026.m"), "w", encoding="utf-8") as handle:
        handle.write(m_table(BUDGET_COLUMNS, rows))
    with open(os.path.join(folder, sales_budget.PBIX_BREAK_EVEN_QUERY), "w", encoding="utf-8") as handle:
        handle.write(m_table(["CD_UNIDADE", "DS_UNIDADE", "VALOR", "MES", "ANO"], [
            ["1", "MATRIZ", "1.234.567,89", "1", "2026"],
            ["2", "RAFIA", "0", "1", "2026"],
        ]))
    return folder


def store_data(year=2030, weight="100.000"):
    """JSON do `importar_orcamento` (o mesmo formato de tiqueue/data/sales_budget.json)."""
    return {
        "budget": [
            {"year": year, "month": 1, "company": 1, "branch": 1, "family": "800007", "groups": "BOBINA MONO IMPRESSO",
             "weight_kg": weight, "value": "2000.00", "basis": "bruto", "source": "teste"},
            {"year": year, "month": 2, "company": 1, "branch": 2, "family": "800013", "groups": "SACO CONVENCIONAL LISO",
             "weight_kg": "50.000", "value": "750.00", "basis": "bruto", "source": "teste"},
        ],
        "break_even": [{"year": year, "month": 1, "company": 1, "branch": 1, "value": "9000.00", "source": "teste"}],
    }


class EmptyBudgetMixin:
    """A migration carrega o orçamento local (tiqueue/data/sales_budget.json) quando ele existe: os testes
    começam sem orçamento, ponto de equilíbrio nem metas por representante."""

    def setUp(self):
        super().setUp()
        SalesBudget.objects.all().delete()
        SalesBreakEven.objects.all().delete()
        RepresentativeGoal.objects.all().delete()


# ---------------------------------------------------------------------------
# ERP simulado para o painel (commercial_dna._run)
# ---------------------------------------------------------------------------

def billing_row(**values):
    row = {"mes": datetime.datetime(2026, 3, 1), "empresa": 1, "filial": 1, "filial_producao": 1, "codfam": "80007",
           "familia": "FILME IMPRESSO (KG)", "bi_laminado": 0, "serafina": 0, "representante": 10048, "cliente": 501,
           "peso": 1000, "bruto": 30000, "sem_impostos": 25000}
    row.update(values)
    return row


BILLING_ROWS = [
    billing_row(representante=10042),  # código antigo do mesmo representante (10042 → 10048)
    billing_row(),
    # Laminado faturado na Matriz e produzido na Serafina: conta na unidade 4.
    billing_row(filial_producao=4, familia="FILME LAMINADO IMPRESSO (KG)", cliente=502, peso=500, bruto=20000, sem_impostos=16000),
    # Fora do orçamento: grupo sem meta e outra empresa.
    billing_row(familia="APARAS LISAS FLEXIVEIS", peso=300, bruto=900, sem_impostos=800),
    billing_row(empresa=2, peso=200, bruto=5000, sem_impostos=4000),
]
PREVIOUS_ROWS = [billing_row(mes=datetime.datetime(2025, 3, 1), peso=1500, bruto=33000, sem_impostos=27000)]


def order_row(**values):
    row = {"empresa": 1, "filial": 1, "pedido": 900, "sequencia": 1, "emissao": datetime.datetime(2026, 10, 1),
           "entrega": datetime.datetime(2026, 11, 10), "situacao_item": 2, "cliente": 501, "representante": 10042,
           "filial_producao": 1, "produto": 1, "descricao_produto": "FILME IMPRESSO", "familia": "FILME IMPRESSO (KG)",
           "qtd_pedida": 1000, "qtd_faturada": 200, "qtd_cancelada": 0, "qtd_aberta": 800, "preco_unitario": 30, "peso_unitario": 1}
    row.update(values)
    return row


ORDER_ROWS = [
    order_row(),  # atrasado (entrega 10/11), 800 kg a faturar, 300 kg prontos
    order_row(pedido=901, entrega=datetime.datetime(2026, 12, 5), situacao_item=1, qtd_pedida=500, qtd_faturada=0, qtd_aberta=500, preco_unitario=20),
    order_row(pedido=902, situacao_item=3),  # fechado
    order_row(pedido=903, familia="APARAS LISAS FLEXIVEIS"),  # sem família de meta
]
PRODUCTION_ROWS = [{"empresa": 1, "filial": 1, "pedido": 900, "sequencia": 1, "qtd_produzida": 500}]
INVOICE_ROWS = [
    {"empresa": 1, "filial": 1, "nota": 100, "data": datetime.datetime(2026, 3, 5), "cliente": 501, "representante": 10042,
     "filial_producao": 1, "pedido": 900, "produto": 1, "descricao": "FILME IMPRESSO 30X40", "codfam": "80007",
     "familia": "FILME IMPRESSO (KG)", "bi_laminado": 0, "serafina": 0, "peso": 1000, "bruto": 30000, "sem_impostos": 25000},
    {"empresa": 1, "filial": 1, "nota": 101, "data": datetime.datetime(2026, 3, 20), "cliente": 502, "representante": 10048,
     "filial_producao": 4, "pedido": 950, "produto": 2, "descricao": "FILME LAMINADO", "codfam": "80008",
     "familia": "FILME LAMINADO IMPRESSO (KG)", "bi_laminado": 0, "serafina": 0, "peso": 500, "bruto": 20000, "sem_impostos": 16000},
    {"empresa": 1, "filial": 1, "nota": 102, "data": datetime.datetime(2026, 4, 2), "cliente": 501, "representante": 10048,
     "filial_producao": 1, "pedido": 901, "produto": 1, "descricao": "FILME IMPRESSO 30X40", "codfam": "80007",
     "familia": "FILME IMPRESSO (KG)", "bi_laminado": 0, "serafina": 0, "peso": 700, "bruto": 21000, "sem_impostos": 17500},
    {"empresa": 1, "filial": 1, "nota": 103, "data": datetime.datetime(2026, 3, 7), "cliente": 501, "representante": 10048,
     "filial_producao": 1, "pedido": 904, "produto": 3, "descricao": "APARAS", "codfam": "X",
     "familia": "APARAS LISAS FLEXIVEIS", "bi_laminado": 0, "serafina": 0, "peso": 50, "bruto": 100, "sem_impostos": 90},
]
ERP_DATA = {
    "billing": BILLING_ROWS,
    "previous": PREVIOUS_ROWS,
    "families": [
        {"empresa": 1, "codigo": "800007", "nome": "FILME IMPRESSO (KG)"},
        {"empresa": 1, "codigo": "800008", "nome": "FILME IMPRESSO LAMINADO (KG)"},
    ],
    "orders": ORDER_ROWS,
    "production": PRODUCTION_ROWS,
    "reps": [{"codigo": 10048, "nome": "REPRESENTANTE UM"}, {"codigo": 10042, "nome": "REPRESENTANTE UM ANTIGO"}],
    "branches": [{"empresa": 1, "filial": 1, "sigla": "MATRIZ"}, {"empresa": 1, "filial": 4, "sigla": "SERAFINA (F04)"}],
    "customers": [
        {"cliente": 501, "nome": "CLIENTE A", "cidade": "CAXIAS DO SUL", "uf": "RS"},
        {"cliente": 502, "nome": "CLIENTE B", "cidade": "SERAFINA CORREA", "uf": "RS"},
    ],
    "lines": INVOICE_ROWS,
}


class FakeRun:
    """Responde ao `commercial_dna._run` com linhas prontas e guarda o SQL e os parâmetros pedidos."""

    def __init__(self, data=ERP_DATA):
        self.data = data
        self.calls = []

    def __call__(self, statements):
        self.calls.append(statements)
        return {name: copy.deepcopy(self.data.get(name, [])) for name in statements}


# ---------------------------------------------------------------------------
# Orçamento: PBIX, projeção, banco e metas do ano
# ---------------------------------------------------------------------------

class BudgetMappingTests(SimpleTestCase):
    def test_pbix_groups_are_the_dna_product_groups(self):
        cases = [
            (("SACARIA LAMINADA", "IMPRESSO", 1), "SACO LAMINADO IMPRESSO"),
            (("SACARIA BI LAMINADA", "IMPRESSO", 4), "SACO BI LAMINADO IMPRESSO"),
            (("BOBINA MONO", "IMPRESSO", 1), "BOBINA MONO IMPRESSO"),
            (("FOLHA", "LISO", 1), "FOLHA MONO LISO"),
            (("BOBINA ADESIVADA", "LISO", 5), "BOBINA ADESIVADO LISO"),
            # Ráfia: o "MONO" do orçamento é a ráfia convencional; refugo continua "MONO".
            (("SACARIA MONO", "LISO", 2), "SACO CONVENCIONAL LISO"),
            (("BOBINA MONO", "LISO", 2), "BOBINA CONVENCIONAL LISO"),
            (("SACARIA MONO", "IMPRESSO", 3), "SACO CONVENCIONAL IMPRESSO"),
            (("REFUGO", "LISO", 2), "REFUGO MONO LISO"),
            (("XYZ", "LISO", 1), None),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(sales_budget.pbix_group(*args), expected)

    def test_family_of_follows_the_goal_mapping(self):
        self.assertEqual(sales_budget.family_of(1, "BOBINA MONO IMPRESSO"), "800007")
        self.assertEqual(sales_budget.family_of(2, "SACO CONVENCIONAL LISO"), "800013")
        self.assertEqual(sales_budget.family_of(2, "SACO LAMINADO IMPRESSO"), "800016")
        self.assertEqual(sales_budget.family_of(3, "SACO CONVENCIONAL IMPRESSO"), "800040")
        self.assertIsNone(sales_budget.family_of(9, "BOBINA MONO IMPRESSO"))
        self.assertIsNone(sales_budget.family_of(1, None))


class BudgetImportTests(TempDirMixin, SimpleTestCase):
    def test_budget_from_pbix_maps_sums_and_prices(self):
        folder = write_pbix_queries(self.tmp.name)
        result = sales_budget.budget_from_pbix(folder)
        rows = {(row["year"], row["month"], row["branch"], row["family"]): row for row in result["budget"]}
        self.assertEqual(sorted(rows), [(2026, 1, 1, "800007"), (2026, 2, 2, "800013"), (2026, 2, 2, "800016"), (2026, 3, 4, "800008")])
        matriz = rows[(2026, 1, 1, "800007")]
        self.assertEqual((matriz["weight_kg"], matriz["value"]), ("1500.000", "31000.00"))
        self.assertEqual(matriz["groups"], "BOBINA MONO IMPRESSO")
        self.assertEqual(rows[(2026, 2, 2, "800013")]["value"], "4650.00")
        self.assertEqual({row["basis"] for row in result["budget"]}, {sales_budget.BASIS_GROSS})
        self.assertEqual(len(result["unmapped"]), 1)
        self.assertEqual(result["unmapped"][0][:3], (2026, 1, "XYZ"))
        self.assertEqual(result["break_even"], [{"year": 2026, "month": 1, "company": 1, "branch": 1, "value": "1234567.89", "source": "pbix-orcamento-2026"}])

    def test_budget_from_projection_uses_the_price_without_taxes(self):
        path = build_workbook(os.path.join(self.tmp.name, "projecao.xlsx"))
        result = sales_budget.budget_from_projection(path)
        rows = {(row["year"], row["month"], row["branch"], row["family"]): row for row in result["budget"]}
        jan = rows[(2027, 1, 1, "800007")]
        self.assertEqual(jan["weight_kg"], "231648.360")
        self.assertEqual(jan["value"], "4632967.20")  # 231.648,36 kg × R$ 20/kg (coluna "R$/kg 2027")
        self.assertEqual({row["basis"] for row in result["budget"]}, {sales_budget.BASIS_NET})
        self.assertEqual(result["break_even"], [])

    def test_price_column_is_the_one_with_the_year(self):
        self.assertEqual(sales_budget._price_column(["Unidade", "R$/kg", "% Reajuste", "R$/kg ex-imp 2027"]), 3)
        self.assertIsNone(sales_budget._price_column(["Unidade", "R$/kg"]))


class BudgetStoreTests(EmptyBudgetMixin, TestCase):
    def test_replace_budget_only_touches_the_years_in_the_file(self):
        SalesBudget.objects.create(year=2029, month=1, company=1, branch=1, family="800007", weight_kg=D("1"), value=D("1"))
        self.assertEqual(sales_budget.replace_budget(SalesBudget, SalesBreakEven, store_data()), (2, 1, [2030]))
        self.assertEqual(sales_budget.replace_budget(SalesBudget, SalesBreakEven, store_data(weight="120.000")), (2, 1, [2030]))
        self.assertEqual(SalesBudget.objects.filter(year=2030).count(), 2)
        self.assertEqual(SalesBudget.objects.get(year=2030, month=1).weight_kg, D("120.000"))
        self.assertEqual(SalesBudget.objects.filter(year=2029).count(), 1)
        self.assertEqual(SalesBreakEven.objects.get(year=2030).value, D("9000.00"))

    def test_available_years(self):
        SalesBudget.objects.create(year=2025, month=1, company=1, branch=1, family="800007", weight_kg=D("1"), value=D("1"))
        SalesBudget.objects.create(year=2031, month=1, company=1, branch=1, family="800007", weight_kg=D("1"), value=D("1"))
        self.assertEqual(budget_panel.available_years(TODAY), [2025, 2026])


class YearGoalsTests(EmptyBudgetMixin, TestCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {sales_goals.SCHEMA_ENV: ""})
        env.start()
        self.addCleanup(env.stop)
        self.db = FakeOracle()
        for month, value in ((1, "20000.00"), (2, "22000.00")):
            SalesBudget.objects.create(year=2027, month=month, company=1, branch=1, family="800007", groups="BOBINA MONO IMPRESSO",
                                       weight_kg=D("1000"), value=D(value), basis="sem_impostos")

    def test_unit_and_rep_goals_come_from_the_erp_when_the_year_is_there(self):
        self.db.add(1, 1, 1, "800007", 2027, 1, "1200")
        self.db.add(1, 1, 2, "800007", 2027, 3, "900")
        self.db.add_rep(1, 1, 1, 10048, "800007", 2027, 1, "50", "600")
        goals = sales_budget.year_goals(2027, connect=self.db.connect)
        self.assertEqual(goals["sources"], {"units": "erp", "reps": "erp"})
        self.assertEqual(goals["units"][(1, 1, "800007", 1)], {"kg": D("1200"), "value": D("24000.00")})
        # Março não tem preço no orçamento: usa a média dos preços da família no ano (R$ 21/kg).
        self.assertEqual(goals["units"][(1, 1, "800007", 3)]["value"], D("18900.00"))
        self.assertEqual(goals["reps"], {(1, 1, "800007", 10048, 1): D("600")})
        self.assertEqual(goals["basis"], "sem_impostos")
        self.assertEqual(goals["groups"][(1, "800007")], "BOBINA MONO IMPRESSO")
        self.assertEqual(self.db.dml(), [])  # só leitura
        self.assertEqual(self.db.commits, 0)

    def test_budget_and_pbix_rep_goals_when_the_erp_has_nothing(self):
        rep_rows = [
            {"year": 2027, "month": 1, "unit_code": 1, "rep_code": 10042, "family": "BOBINA MONO IMPRESSO", "weight_kg": "300"},
            {"year": 2027, "month": 1, "unit_code": 1, "rep_code": 10048, "family": "BOBINA MONO IMPRESSO", "weight_kg": "100"},
            {"year": 2027, "month": 1, "unit_code": 1, "rep_code": 10048, "family": "GRUPO SEM META", "weight_kg": "5"},
        ]
        goals = sales_budget.year_goals(2027, rep_goal_rows=rep_rows, connect=self.db.connect)
        self.assertEqual(goals["sources"], {"units": "orcamento", "reps": "pbix"})
        self.assertEqual(goals["units"][(1, 1, "800007", 2)], {"kg": D("1000"), "value": D("22000.00")})
        self.assertEqual(goals["reps"], {(1, 1, "800007", 10048, 1): D("400")})

    def test_nothing_registered(self):
        goals = sales_budget.year_goals(2040, rep_goal_rows=[], connect=self.db.connect)
        self.assertEqual(goals["sources"], {"units": "nenhuma", "reps": "nenhuma"})
        self.assertEqual((goals["units"], goals["reps"], goals["break_even"]), ({}, {}, {}))


# ---------------------------------------------------------------------------
# Fatos do painel
# ---------------------------------------------------------------------------

class BudgetFactsTests(SimpleTestCase):
    def test_classify_unit_and_goal_family(self):
        group = commercial_dna.product_group("FILME IMPRESSO (KG)", "")
        self.assertEqual(budget_panel.classify(1, 1, 1, "FILME IMPRESSO (KG)", group), (1, "800007"))
        self.assertEqual(budget_panel.classify(2, 1, 1, "FILME IMPRESSO (KG)", group), (None, None))
        laminated = commercial_dna.product_group("FILME LAMINADO IMPRESSO (KG)", "")
        self.assertEqual(budget_panel.classify(1, 1, 4, "FILME LAMINADO IMPRESSO (KG)", laminated), (4, "800008"))
        raffia = commercial_dna.product_group("SACO RAFIA LISO CONVENCIONAL (MILHEIRO)", "")
        self.assertEqual(budget_panel.classify(1, 2, 2, "SACO RAFIA LISO CONVENCIONAL (MILHEIRO)", raffia), (2, "800013"))
        bag = commercial_dna.product_group("SACO BIG BAG IMPRESSO CONVENCIONAL (UND)", "")
        self.assertEqual(budget_panel.classify(1, 3, None, "SACO BIG BAG IMPRESSO CONVENCIONAL (UND)", bag), (3, "800040"))
        scraps = commercial_dna.product_group("APARAS LISAS FLEXIVEIS", "")
        self.assertEqual(budget_panel.classify(1, 1, 1, "APARAS LISAS FLEXIVEIS", scraps), (1, None))

    def test_billing_facts_sum_by_key_and_keep_the_rest_outside(self):
        facts, outside = budget_panel.billing_facts(BILLING_ROWS)
        by_unit = {fact["u"]: fact for fact in facts}
        self.assertEqual(len(facts), 2)
        self.assertEqual(by_unit[1], {"m": 3, "u": 1, "f": "800007", "r": 10048, "c": 501, "b": "1-1", "k": 2000.0, "n": 60000.0, "t": 50000.0})
        self.assertEqual((by_unit[4]["f"], by_unit[4]["b"], by_unit[4]["k"]), ("800008", "1-1", 500.0))
        self.assertEqual(outside, {"k": 500.0, "n": 5900.0, "t": 4800.0})

    def test_order_facts_to_bill_ready_and_late(self):
        facts = budget_panel.order_facts(ORDER_ROWS, PRODUCTION_ROWS, TODAY)
        self.assertEqual(len(facts), 2)
        late, december = sorted(facts, key=lambda fact: fact["mk"])
        self.assertEqual(late, {
            "mk": 2026 * 12 + 10, "u": 1, "f": "800007", "r": 10048, "c": 501, "late": 1, "k": 800.0, "v": 24000.0, "p": 300.0,
            "o": "1-1-900", "s": 1, "d": "2026-11-10", "e": "2026-10-01",
        })
        self.assertEqual((december["mk"], december["late"], december["k"], december["v"], december["p"]), (2026 * 12 + 11, 0, 500.0, 10000.0, 0.0))

    def test_goal_facts_price_rep_goals_at_the_unit_price(self):
        goals = {
            "units": {(1, 1, "800007", 3): {"kg": D("1000"), "value": D("25000")}, (2, 1, "800007", 3): {"kg": D("10"), "value": D("10")}},
            "reps": {(1, 1, "800007", 10048, 3): D("400"), (1, 1, "800007", 10048, 4): D("100"), (2, 1, "800007", 1, 3): D("9")},
            "break_even": {(1, 1, 3): D("50000"), (2, 1, 3): D("1")},
        }
        facts = budget_panel.goal_facts(goals)
        self.assertEqual(facts["units"], [{"m": 3, "u": 1, "f": "800007", "k": 1000.0, "v": 25000.0}])
        self.assertEqual(facts["reps"], [
            {"m": 3, "u": 1, "f": "800007", "r": 10048, "k": 400.0, "v": 10000.0},
            {"m": 4, "u": 1, "f": "800007", "r": 10048, "k": 100.0, "v": 0.0},
        ])
        self.assertEqual(facts["break_even"], [{"m": 3, "u": 1, "v": 50000.0}])

    def test_business_months(self):
        months = budget_panel.business_months(2026, TODAY)
        self.assertEqual(len(months), 12)
        self.assertEqual(months[0]["elapsed"], months[0]["total"])
        self.assertEqual(months[11]["elapsed"], 0)
        self.assertTrue(0 < months[10]["elapsed"] < months[10]["total"])

    def test_family_labels(self):
        goals = {"groups": {(1, "800007"): "BOBINA MONO IMPRESSO", (4, "800004"): "SACO BI LAMINADO IMPRESSO + SACO LAMINADO IMPRESSO"}}
        labels = budget_panel.family_labels(goals, {"800007": "FILME IMPRESSO (KG)", "999": "FAMILIA NOVA"}, {"800013", "999", "800007", None})
        self.assertEqual(labels["800007"]["label"], "Bobina Mono Impresso (800007)")
        self.assertEqual(labels["800004"]["label"], "Saco Bi Laminado Impresso + Saco Laminado Impresso (800004)")
        self.assertIn("Saco Convencional Liso", labels["800013"]["groups"])
        self.assertEqual(labels["999"]["label"], "FAMILIA NOVA (999)")
        self.assertNotIn(None, labels)


class BudgetPayloadTests(EmptyBudgetMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        env = mock.patch.dict(os.environ, {sales_goals.SCHEMA_ENV: ""})
        env.start()
        self.addCleanup(env.stop)
        self.run = FakeRun()
        patcher = mock.patch.object(commercial_dna, "_run", side_effect=self.run)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = FakeOracle()
        SalesBudget.objects.create(year=2026, month=3, company=1, branch=1, family="800007", groups="BOBINA MONO IMPRESSO",
                                   weight_kg=D("2500"), value=D("50000"), basis="bruto")
        SalesBreakEven.objects.create(year=2026, month=3, company=1, branch=1, value=D("40000"))

    def test_payload_for_the_current_year(self):
        data = budget_panel.budget_payload(2026, TODAY, connect=self.db.connect)
        self.assertEqual((data["year"], data["is_current"], data["is_future"], data["current_month"]), (2026, True, False, 11))
        self.assertEqual(data["sources"], {"units": "orcamento", "reps": "nenhuma"})
        self.assertEqual((data["basis"], data["basis_label"]), ("bruto", sales_budget.BASIS_LABELS["bruto"]))
        self.assertEqual(len(data["billing"]), 2)
        self.assertEqual(data["outside"]["k"], 500.0)
        self.assertEqual(data["previous"], [{"m": 3, "u": 1, "f": "800007", "r": 10048, "k": 1500.0, "n": 33000.0, "t": 27000.0}])
        self.assertEqual(len(data["orders"]), 2)
        self.assertEqual(data["goals"]["units"], [{"m": 3, "u": 1, "f": "800007", "k": 2500.0, "v": 50000.0}])
        self.assertEqual(data["goals"]["break_even"], [{"m": 3, "u": 1, "v": 40000.0}])
        self.assertEqual(data["families"]["800007"]["label"], "Bobina Mono Impresso (800007)")
        self.assertIn("800008", data["families"])  # só no faturamento, mas com nome
        self.assertEqual(data["names"]["customers"]["501"]["name"], "CLIENTE A")
        self.assertIn("10048", data["names"]["reps"])
        self.assertEqual(data["names"]["units"]["4"], commercial_dna.BUDGET_UNITS[4])
        self.assertEqual(len(data["business"]), 12)
        json.dumps(data)  # vai para o json_script do template

    def test_payload_of_a_closed_year_does_not_read_the_backlog(self):
        data = budget_panel.budget_payload(2025, TODAY, connect=self.db.connect)
        asked = set().union(*(set(statements) for statements in self.run.calls))
        self.assertNotIn("orders", asked)
        self.assertEqual(data["orders"], [])
        self.assertEqual((data["is_current"], data["current_month"]), (False, 12))

    def test_payload_is_cached(self):
        budget_panel.budget_payload(2026, TODAY, connect=self.db.connect)
        calls = len(self.run.calls)
        budget_panel.budget_payload(2026, TODAY, connect=self.db.connect)
        self.assertEqual(len(self.run.calls), calls)


class InvoiceDetailTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.run = FakeRun()
        patcher = mock.patch.object(commercial_dna, "_run", side_effect=self.run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def lines_statement(self):
        return next(statements["lines"] for statements in self.run.calls if "lines" in statements)

    def test_filters_month_unit_and_family(self):
        lines = budget_panel.invoice_detail(2026, [3], [1], [], [], [])
        self.assertEqual([line["invoice"] for line in lines], ["100"])
        line = lines[0]
        self.assertEqual((line["unit"], line["family"], line["group"], line["kg"], line["value"]), ("Matriz", "800007", "Bobina Mono Impresso", 1000.0, 30000.0))
        self.assertEqual((line["customer"], line["customer_name"], line["date"]), (501, "CLIENTE A", "2026-03-05"))
        sql, params = self.lines_statement()
        self.assertEqual((params["inicio"], params["fim"]), (datetime.datetime(2026, 3, 1), datetime.datetime(2026, 4, 1)))
        self.assertNotIn("N.CODREP IN", sql)

    def test_value_without_taxes_and_family_filter(self):
        lines = budget_panel.invoice_detail(2026, [], [], ["800008"], [], [], net=True)
        self.assertEqual([(line["invoice"], line["unit"], line["value"]) for line in lines], [("101", "Serafina", 16000.0)])

    def test_rep_filter_includes_old_codes_and_sorts_newest_first(self):
        lines = budget_panel.invoice_detail(2026, [], [], [], [10048], [])
        self.assertEqual([line["invoice"] for line in lines], ["102", "101", "100"])  # a nota 103 (aparas) não tem meta
        sql, params = self.lines_statement()
        self.assertIn("N.CODREP IN", sql)
        self.assertIn(10042, params.values())
        self.assertEqual((params["inicio"], params["fim"]), (datetime.datetime(2026, 1, 1), datetime.datetime(2027, 1, 1)))

    def test_customer_filter(self):
        budget_panel.invoice_detail(2026, [12], [], [], [], [501])
        sql, params = self.lines_statement()
        self.assertIn("N.CODCLI IN", sql)
        self.assertEqual(params["fim"], datetime.datetime(2027, 1, 1))


# ---------------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------------

SAMPLE_PAYLOAD = {
    "billing": [], "outside": {"k": 0, "n": 0, "t": 0}, "previous": [], "orders": [],
    "goals": {"units": [], "reps": [], "break_even": []}, "families": {}, "basis": "bruto", "sources": {"units": "orcamento", "reps": "nenhuma"},
    "year": 2026, "today": "2026-11-24", "current_month": 11, "is_current": True, "is_future": False, "business": [],
    "basis_label": "com impostos", "names": {"reps": {}, "branches": {}, "units": {}, "customers": {}}, "loaded_at": "10:00:00",
}
DETAIL_LINES = [
    {"invoice": "100", "date": "2026-03-05", "customer": 501, "customer_name": "CLIENTE A", "rep": "Representante Um", "unit": "Matriz",
     "branch": "Matriz", "family": "800007", "group": "Bobina Mono Impresso", "order": 900, "product": "FILME", "kg": 1000.0, "value": 30000.0},
    {"invoice": "101", "date": "2026-03-20", "customer": 502, "customer_name": "CLIENTE B", "rep": "Representante Um", "unit": "Matriz",
     "branch": "Matriz", "family": "800007", "group": "Bobina Mono Impresso", "order": 950, "product": "FILME", "kg": 500.0, "value": 15000.5},
]


class BudgetPanelPageTests(EmptyBudgetMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = User.objects.create_user(username="orcamento", password="x", userId="orcamento", email="o@example.com", is_system_admin=True)
        self.login(self.admin)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def page(self, query="", payload=SAMPLE_PAYLOAD, **kwargs):
        with mock.patch.object(budget_panel, "budget_payload", return_value=payload, **kwargs) as loader:
            response = self.client.get(reverse("dashesBudgetPanel") + query)
        return response, loader

    def test_dashboard_is_in_the_catalog(self):
        dashboard = Dashboard.objects.get(slug="budget-panel")
        self.assertEqual((dashboard.name, dashboard.url_name, dashboard.is_active), ("Orçamento", "dashesBudgetPanel", True))

    def test_page_renders_the_panel(self):
        response, loader = self.page()
        self.assertEqual(response.status_code, 200)
        year = datetime.date.today().year
        loader.assert_called_once()
        self.assertEqual(loader.call_args.args[0], year)
        self.assertContains(response, f"Orçamento {year}")
        self.assertContains(response, "budgetPanel.js")
        self.assertContains(response, "budgetPanel.css")
        self.assertContains(response, 'id="bgData"')
        self.assertContains(response, 'id="bgConfig"')
        self.assertContains(response, 'class="mx-tab is-active" href="/dashes/orcamento/"', html=False)
        config = response.context["page_config"]
        self.assertEqual(config["detail_url"], reverse("dashesBudgetPanelDetail"))
        self.assertIn("__code__", config["profile_url_template"])

    def test_year_must_be_an_available_year(self):
        SalesBudget.objects.create(year=2025, month=1, company=1, branch=1, family="800007", weight_kg=D("1"), value=D("1"))
        response, loader = self.page("?ano=2025")
        self.assertEqual(loader.call_args.args[0], 2025)
        self.assertEqual(response.context["years"][0], 2025)
        response, loader = self.page("?ano=1999")
        self.assertEqual(loader.call_args.args[0], datetime.date.today().year)

    def test_json_format(self):
        response, _loader = self.page("?formato=json")
        self.assertEqual(response.json()["loaded_at"], "10:00:00")

    def test_erp_failure_is_shown(self):
        response, _loader = self.page(payload=None, side_effect=RuntimeError("ORA-12541"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Não foi possível consultar o ERP Senior: ORA-12541")
        self.assertNotContains(response, "budgetPanel.js")
        response, _loader = self.page("?formato=json", payload=None, side_effect=RuntimeError("ORA-12541"))
        self.assertEqual(response.status_code, 502)

    def test_needs_the_dashes_session(self):
        self.client.logout()
        response = self.client.get(reverse("dashesBudgetPanel"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashesLoginPage"), response["Location"])

    def test_needs_the_dashboard_permission(self):
        user = User.objects.create_user(username="vend2", password="x", userId="vend2", email="v2@example.com")
        self.login(user)
        response, _loader = self.page()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse("dashesBudgetPanelDetail")).status_code, 403)
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="budget-panel"))
        response, _loader = self.page()
        self.assertEqual(response.status_code, 200)

    def test_detail_returns_the_invoices(self):
        with mock.patch.object(budget_panel, "invoice_detail", return_value=DETAIL_LINES) as detail:
            response = self.client.get(reverse("dashesBudgetPanelDetail") + "?ano=2026&m=3,4&m=4&u=1&f=800007&r=10048&c=501&base=sem_impostos&titulo=Bobina")
        self.assertEqual(response.status_code, 200)
        detail.assert_called_once_with(2026, [3, 4, 4], [1], ["800007"], [10048], [501], net=True)
        data = response.json()
        self.assertEqual(data["title"], "Bobina")
        self.assertIn("R$ sem impostos", data["subtitle"])
        self.assertEqual({item["label"]: item["value"] for item in data["summary"]}, {"Volume": "1.500 kg", "Valor": "R$ 45.000,50", "Notas": "2", "Clientes": "2"})
        section = data["sections"][0]
        self.assertEqual((section["key"], len(section["rows"])), ("notas", 2))
        self.assertIn("groupBy", section)

    def test_detail_validation_and_erp_failure(self):
        self.assertEqual(self.client.get(reverse("dashesBudgetPanelDetail") + "?ano=1990").status_code, 400)
        with mock.patch.object(budget_panel, "invoice_detail", side_effect=RuntimeError("ORA-03113")):
            response = self.client.get(reverse("dashesBudgetPanelDetail") + "?ano=2026")
        self.assertEqual(response.status_code, 502)
        self.assertIn("ORA-03113", response.json()["error"])

    def test_detail_caps_the_lines(self):
        many = [dict(DETAIL_LINES[0], invoice=str(number)) for number in range(budget_panel.MAX_INVOICE_LINES + 5)]
        with mock.patch.object(budget_panel, "invoice_detail", return_value=many):
            data = self.client.get(reverse("dashesBudgetPanelDetail") + "?ano=2026").json()
        self.assertEqual(len(data["sections"][0]["rows"]), budget_panel.MAX_INVOICE_LINES)
        self.assertIn(f"as {budget_panel.MAX_INVOICE_LINES} mais recentes", data["sections"][0]["label"])
        self.assertEqual(next(item["value"] for item in data["summary"] if item["label"] == "Notas"), str(budget_panel.MAX_INVOICE_LINES + 5))

    def test_menu_links_to_the_panel(self):
        with mock.patch.object(budget_panel, "budget_payload", return_value=SAMPLE_PAYLOAD):
            response = self.client.get(reverse("dashesBudgetPanel"))
        self.assertContains(response, 'href="/dashes/orcamento/"')
        user = User.objects.create_user(username="vend3", password="x", userId="vend3", email="v3@example.com")
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="budget-panel"))
        self.login(user)
        with mock.patch.object(budget_panel, "budget_payload", return_value=SAMPLE_PAYLOAD):
            response = self.client.get(reverse("dashesBudgetPanel"))
        self.assertContains(response, "Orçamento")
        self.assertNotContains(response, f'href="{reverse("dashesSalesGoalsPage")}?ano=')


# ---------------------------------------------------------------------------
# Comando
# ---------------------------------------------------------------------------

class ImportBudgetCommandTests(EmptyBudgetMixin, TempDirMixin, TestCase):
    def test_needs_a_source(self):
        with self.assertRaises(CommandError):
            call_command("importar_orcamento", stdout=StringIO())

    def test_loads_the_json_file_and_is_idempotent(self):
        path = os.path.join(self.tmp.name, "orcamento.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(store_data(2030), handle)
        for _ in range(2):
            out = StringIO()
            call_command("importar_orcamento", "--arquivo", path, stdout=out)
        self.assertEqual(SalesBudget.objects.filter(year=2030).count(), 2)
        self.assertEqual(SalesBreakEven.objects.filter(year=2030).count(), 1)
        self.assertIn("2030 unidade 1 (MATRIZ): 100,000 kg, R$ 2.000,00 [bruto]", out.getvalue())
        self.assertIn("2 linhas e 1 meses de ponto de equilíbrio", out.getvalue())

    def test_reads_the_pbix_and_the_projection(self):
        folder = write_pbix_queries(os.path.join(self.tmp.name))
        sheet = build_workbook(os.path.join(self.tmp.name, "projecao.xlsx"))
        out = StringIO()
        call_command("importar_orcamento", "--pbix-consultas", folder, "--planilha", sheet, "--sem-json", stdout=out)
        self.assertEqual(set(SalesBudget.objects.values_list("year", flat=True).distinct()), {2026, 2027})
        self.assertTrue(SalesBudget.objects.filter(year=2026, branch=2, family="800013").exists())
        self.assertEqual(set(SalesBudget.objects.filter(year=2027).values_list("basis", flat=True)), {"sem_impostos"})
        self.assertIn("Sem família de meta (fica de fora): 2026 1", out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "sales_budget.json")))

    def test_empty_source_is_refused(self):
        os.makedirs(os.path.join(self.tmp.name, "vazia"))
        with self.assertRaises(CommandError):
            call_command("importar_orcamento", "--pbix-consultas", os.path.join(self.tmp.name, "vazia"), stdout=StringIO())
