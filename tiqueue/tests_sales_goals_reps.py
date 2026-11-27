"""Metas por representante (USU_TORCGER): % pelo faturamento, peso = meta × %, gravação (Oracle simulado),
recálculo quando a meta muda, a grade da tela e o comando de carga."""

import datetime
import json
import os
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import sales_goals, sales_goals_reps
from .models import Dashboard, DashboardAccess
from .tests_sales_goals import FAKE_REPS, FakeOracle, _fake_catalog_data

D = Decimal
User = get_user_model()
GER = "SENIORERPSIMULACOES.USU_TORCGER"


def billing(unit_branch, family, codfam, rep, weight, complement="", product=100):
    """Uma linha do faturamento por representante, no formato do BILLING_BY_REP_SQL."""
    return {"filial": unit_branch, "filial_producao": unit_branch, "produto": product, "codfam": codfam,
            "familia": family, "complemento": complement, "representante": rep, "peso": weight}


BILLING_ROWS = [
    # Matriz, BOBINA MONO IMPRESSO: 800007 (kg) e 800022 (metros) contam juntas para a família 800007.
    billing(1, "FILME IMPRESSO (KG)", "800007", 10004, 600.0),
    billing(1, "FILME IMPRESSO (METROS)", "800022", 10042, 300.0),  # 10042 soma no 10048
    billing(1, "FILME IMPRESSO (KG)", "800007", 10085, 100.0),
    billing(1, "FILME IMPRESSO (KG)", "800007", 10071, -50.0),  # devolução maior que a venda: fora
    billing(4, "FILME IMPRESSO (KG)", "800007", 10066, 999.0),  # Serafina: outra unidade
    # Serafina, 800004 = SACO BI LAMINADO IMPRESSO + SACO LAMINADO IMPRESSO.
    billing(4, "SACO IMPRESSO LAMINADO (MILHEIRO)", "800004", 10066, 100.0, complement="X+Y+A+Z"),
    billing(4, "SACO IMPRESSO LAMINADO (MILHEIRO)", "800004", 10004, 250.0),
    billing(4, "SACO IMPRESSO LAMINADO (MILHEIRO)", "800004", 10009, 50.0),  # inativo no ERP
    # Adesivado (unidade 5 do orçamento, fatura pela filial 1).
    {"filial": 1, "filial_producao": 0, "produto": 7, "codfam": "800031", "familia": "FILME ADESIVADO LISO (KG)",
     "complemento": "", "representante": 1001, "peso": 400.0},
]


def fake_run(statements):
    data = _fake_catalog_data()
    data["representantes"] = FAKE_REPS
    data["faturamento"] = BILLING_ROWS
    return {name: data[name] for name in statements}


def seed_meta(db):
    """Metas de 2027: Matriz 800007 (100 kg/mês, 120 em dezembro), Serafina 800004 (1.000 kg/mês)."""
    codlan = 0
    for month in range(1, 13):
        codlan += 1
        db.add(1, 1, codlan, "800007", 2027, month, "120" if month == 12 else "100")
        codlan += 1
        db.add(1, 4, codlan, "800004", 2027, month, "1000")


class EnvMixin:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        env = mock.patch.dict(os.environ, {sales_goals.SCHEMA_ENV: ""})
        env.start()
        self.addCleanup(env.stop)
        # Os apelidos do PBIX vêm de um arquivo local (fora do repositório): os testes usam só o cadastro.
        names = mock.patch.dict(sales_goals_reps.dna.REPRESENTATIVE_DISPLAY_NAMES, {}, clear=True)
        names.start()
        self.addCleanup(names.stop)
        self.db = FakeOracle()
        for target, name, effect in ((sales_goals.dna, "_run", fake_run), (sales_goals, "_connect", self.db.connect)):
            patcher = mock.patch.object(target, name, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# Contas puras
# ---------------------------------------------------------------------------

class PercentAndWeightTests(SimpleTestCase):
    def test_percents_close_100_with_the_largest_remainder(self):
        self.assertEqual(sales_goals_reps.split_percents({"a": 1, "b": 1, "c": 1}),
                         {"a": D("33.3334"), "b": D("33.3333"), "c": D("33.3333")})
        shares = sales_goals_reps.split_percents({10004: D(600), 10048: D(300), 10085: D(100), 10071: D(-50), 10066: 0})
        self.assertEqual(shares, {10004: D("60.0000"), 10048: D("30.0000"), 10085: D("10.0000")})
        self.assertEqual(sales_goals_reps.split_percents({}), {})
        many = sales_goals_reps.split_percents({code: D(code % 7 + 1) / 3 for code in range(1, 40)})
        self.assertEqual(sum(many.values()), D(100))

    def test_weight_closes_the_meta_when_percents_sum_100(self):
        percents = {"a": D("33.3334"), "b": D("33.3333"), "c": D("33.3333")}
        weights = sales_goals_reps.split_weight(D("100.000"), percents)
        self.assertEqual(weights, {"a": D("33.334"), "b": D("33.333"), "c": D("33.333")})
        self.assertEqual(sum(weights.values()), D("100.000"))
        odd = sales_goals_reps.split_weight(D("231648.360"), {code: share for code, share in
                                            sales_goals_reps.split_percents({1: 7, 2: 11, 3: 13, 4: 17}).items()})
        self.assertEqual(sum(odd.values()), D("231648.360"))

    def test_weight_is_plain_rounding_when_percents_do_not_sum_100(self):
        self.assertEqual(sales_goals_reps.split_weight(D("10.000"), {"a": D("33.3333"), "b": D("50")}),
                         {"a": D("3.333"), "b": D("5.000")})
        self.assertEqual(sales_goals_reps.split_weight(None, {"a": D("100")}), {"a": D("0.000")})
        self.assertEqual(sales_goals_reps.split_weight(D("5"), {}), {})

    def test_percent_parsing(self):
        self.assertEqual(sales_goals_reps.to_percent("22,01234"), D("22.0123"))
        self.assertEqual(sales_goals_reps.to_percent("12.5%"), D("12.5000"))
        self.assertIsNone(sales_goals_reps.to_percent(" "))
        with self.assertRaises(sales_goals.SalesGoalsError):
            sales_goals_reps.to_percent("dez")
        with self.assertRaises(sales_goals.SalesGoalsError):
            sales_goals_reps.check_percent(D("100.0001"))
        with self.assertRaises(sales_goals.SalesGoalsError):
            sales_goals_reps.check_percent(D("-1"))

    def test_base_period(self):
        self.assertEqual(sales_goals_reps.parse_base(None, 2027), (2026, 2026))
        self.assertEqual(sales_goals_reps.parse_base("2025-2026", 2027), (2025, 2026))
        for bad in ("2026-2025", "x", "2028", "2024-2025-2026"):
            with self.assertRaises(sales_goals.SalesGoalsError):
                sales_goals_reps.parse_base(bad, 2027)
        options = sales_goals_reps.base_options(2027, today=datetime.date(2026, 11, 20))
        self.assertEqual([item["key"] for item in options], ["2026", "2025-2026", "2025"])
        self.assertEqual(options[0]["label"], "2026 (até hoje)")
        self.assertEqual(options[2]["label"], "2025")


class BillingShareTests(SimpleTestCase):
    def test_groups_of_each_goal_family(self):
        self.assertEqual(sales_goals_reps.groups_for(1, 1, "800007"), {"BOBINA MONO IMPRESSO"})
        self.assertEqual(sales_goals_reps.groups_for(1, 4, "800004"), {"SACO BI LAMINADO IMPRESSO", "SACO LAMINADO IMPRESSO"})
        self.assertEqual(sales_goals_reps.groups_for(1, 2, "800016"), {"SACO LAMINADO IMPRESSO"})
        self.assertEqual(sales_goals_reps.groups_for(1, 1, "999999"), set())
        self.assertEqual(sales_goals_reps.groups_for(2, 1, "800001"), set())

    def test_weights_follow_unit_group_and_rep_aliases(self):
        index = sales_goals_reps.billing_index(BILLING_ROWS, 1)
        self.assertEqual(sales_goals_reps.family_weights(index, 1, 1, "800007"),
                         {10004: D("600.0"), 10048: D("300.0"), 10085: D("100.0")})
        self.assertEqual(sales_goals_reps.family_weights(index, 1, 4, "800004"),
                         {10066: D("100.0"), 10004: D("250.0"), 10009: D("50.0")})
        self.assertEqual(sales_goals_reps.family_weights(index, 1, 5, "800031"), {1001: D("400.0")})
        self.assertEqual(sales_goals_reps.family_weights(index, 1, 1, "800001"), {})

    def test_family_out_of_the_sheet_uses_its_own_code(self):
        index = sales_goals_reps.billing_index([billing(1, "OUTRA FAMILIA", "777777", 10004, 5.0)], 1)
        self.assertEqual(sales_goals_reps.family_weights(index, 1, 1, "777777"), {10004: D("5.0")})

    def test_suggested_shares_can_leave_out_inactive_reps(self):
        index = sales_goals_reps.billing_index(BILLING_ROWS, 1)
        shares = sales_goals_reps.suggested_shares(1, 4, ["800004"], (2026, 2026), index=index)
        self.assertEqual(shares["800004"]["percents"], {10004: D("62.5000"), 10066: D("25.0000"), 10009: D("12.5000")})
        shares = sales_goals_reps.suggested_shares(1, 4, ["800004"], (2026, 2026), index=index, exclude={10009})
        self.assertEqual(shares["800004"]["percents"], {10004: D("71.4286"), 10066: D("28.5714")})
        self.assertEqual(shares["800004"]["groups"], ["SACO BI LAMINADO IMPRESSO", "SACO LAMINADO IMPRESSO"])


class PlanFamilyTests(SimpleTestCase):
    meta = [D("100.000")] * 11 + [None]

    def test_new_reps_get_twelve_rows_and_the_meta_split(self):
        operations, unchanged = sales_goals_reps.plan_family(1, 1, "800007", self.meta, {}, {10004: D("60"), 10048: D("40")})
        self.assertEqual(unchanged, 0)
        self.assertEqual(len(operations), 24)
        self.assertTrue(all(operation.kind == "insert" for operation in operations))
        january = {operation.rep: operation.weight for operation in operations if operation.month == 1}
        self.assertEqual(january, {10004: D("60.000"), 10048: D("40.000")})
        december = {operation.rep: operation.weight for operation in operations if operation.month == 12}
        self.assertEqual(december, {10004: D("0.000"), 10048: D("0.000")})

    def test_untouched_reps_keep_the_percent_and_get_the_new_weight(self):
        existing = {10004: {month: [(month, D("60"), D("60"))] for month in range(1, 13)},
                    10048: {month: [(100 + month, D("40"), D("40"))] for month in range(1, 13)}}
        meta = [D("200.000")] + [D("100.000")] * 10 + [None]
        operations, unchanged = sales_goals_reps.plan_family(1, 1, "800007", meta, existing)
        self.assertEqual(unchanged, 20)
        self.assertEqual(sorted((op.kind, op.rep, op.month, op.weight) for op in operations), [
            ("update", 10004, 1, D("120.000")), ("update", 10004, 12, D("0.000")),
            ("update", 10048, 1, D("80.000")), ("update", 10048, 12, D("0.000")),
        ])

    def test_removing_a_rep_deletes_its_rows_and_others_are_recomputed(self):
        existing = {10004: {month: [(month, D("60"), D("60"))] for month in range(1, 13)},
                    10048: {month: [(100 + month, D("40"), D("40"))] for month in range(1, 13)}}
        operations, _unchanged = sales_goals_reps.plan_family(1, 1, "800007", self.meta, existing, {10048: None, 10004: D("100")})
        kinds = sorted({(op.kind, op.rep) for op in operations})
        self.assertEqual(kinds, [("delete", 10048), ("update", 10004)])
        self.assertEqual({op.weight for op in operations if op.kind == "update" and op.month == 1}, {D("100.000")})

    def test_duplicated_rows_of_a_month_become_one(self):
        existing = {10004: {1: [(9, D("100"), D("50")), (3, D("100"), D("50"))]}}
        operations, _unchanged = sales_goals_reps.plan_family(1, 1, "800007", [D("100")] + [None] * 11, existing)
        self.assertEqual([(op.kind, op.codlan) for op in operations], [("update", 3), ("delete", 9)])


# ---------------------------------------------------------------------------
# Gravação na USU_TORCGER (Oracle simulado)
# ---------------------------------------------------------------------------

class RepWriteTests(EnvMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        seed_meta(self.db)

    def changes(self, *items):
        return [sales_goals_reps.RepChange(*item) for item in items]

    def test_save_writes_twelve_months_with_percent_weight_and_dates(self):
        self.db.add_rep(1, 9, 70, 10004, "800001", 2027, 1, "100", "5")  # outra filial: só conta para o MAX+1
        plan = sales_goals_reps.save_reps(1, 1, 2027, self.changes(("800007", 10004, D("60")), ("800007", 10048, D("40"))))
        self.assertTrue(plan.applied)
        self.assertEqual(plan.counts(), {"inserir": 24, "atualizar": 0, "apagar": 0, "iguais": 0})
        self.assertEqual(self.db.statements[0][0], f"LOCK TABLE {GER} IN EXCLUSIVE MODE WAIT 10")
        inserts = [params for sql, params in self.db.statements if sql.startswith(f"INSERT INTO {GER}")]
        self.assertEqual(sorted(params["lancamento"] for params in inserts), list(range(71, 95)))
        first = inserts[0]
        self.assertEqual((first["ano"], first["mes"], first["representante"], first["familia"]),
                         (datetime.datetime(2027, 1, 1), datetime.datetime(2027, 1, 1), 10004, "800007"))
        cells = self.db.rep_cells()
        self.assertEqual(cells[(1, 1, "800007", 10004, 1)], (D("60"), D("60.000")))
        self.assertEqual(cells[(1, 1, "800007", 10048, 12)], (D("40"), D("48.000")))
        self.assertEqual(self.db.commits, 1)

    def test_conflict_when_the_percent_changed_after_the_screen_opened(self):
        sales_goals_reps.save_reps(1, 1, 2027, self.changes(("800007", 10004, D("100"))))
        before = dict(self.db.rep_rows)
        plan = sales_goals_reps.save_reps(1, 1, 2027, self.changes(("800007", 10004, D("50"), None)))
        self.assertEqual(plan.conflicts, [{"empresa": 1, "filial": 1, "familia": "800007", "representante": 10004,
                                           "tela": None, "atual": "100.0000"}])
        self.assertFalse(plan.applied)
        self.assertEqual(self.db.rep_rows, before)

    def test_removing_a_rep_recomputes_the_others(self):
        sales_goals_reps.save_reps(1, 1, 2027, self.changes(("800007", 10004, D("60")), ("800007", 10048, D("40"))))
        plan = sales_goals_reps.save_reps(1, 1, 2027, self.changes(("800007", 10048, None, D("40")), ("800007", 10004, D("100"), D("60"))))
        self.assertEqual(plan.counts()["apagar"], 12)
        cells = self.db.rep_cells()
        self.assertEqual({key[3] for key in cells}, {10004})
        self.assertEqual(cells[(1, 1, "800007", 10004, 12)], (D("100"), D("120.000")))

    def test_changing_the_meta_recomputes_the_rep_weight(self):
        sales_goals_reps.save_reps(1, 1, 2027, self.changes(("800007", 10004, D("60")), ("800007", 10048, D("40"))))
        plan = sales_goals.save_grid(1, 1, 2027, [
            sales_goals.Change("800007", 3, D("250.000"), D("100.000")),
            sales_goals.Change("800007", 4, None, D("100.000")),
        ])
        self.assertTrue(plan.applied)
        self.assertEqual(len(plan.rep_operations), 4)
        cells = self.db.rep_cells()
        self.assertEqual(cells[(1, 1, "800007", 10004, 3)], (D("60"), D("150.000")))
        self.assertEqual(cells[(1, 1, "800007", 10048, 3)], (D("40"), D("100.000")))
        self.assertEqual(cells[(1, 1, "800007", 10004, 4)], (D("60"), D("0.000")))
        self.assertEqual(cells[(1, 1, "800007", 10004, 5)], (D("60"), D("60.000")))
        self.assertEqual(self.db.commits, 2)

    def test_meta_change_without_reps_does_not_touch_the_rep_table(self):
        plan = sales_goals.save_grid(1, 1, 2027, [sales_goals.Change("800007", 3, D("250.000"), D("100.000"))])
        self.assertEqual(plan.rep_operations, [])
        self.assertFalse([sql for sql in self.db.dml() if GER in sql])

    def test_grid_shows_percent_weights_and_meta(self):
        sales_goals_reps.save_reps(1, 1, 2027, self.changes(("800007", 10004, D("60")), ("800007", 10048, D("40"))))
        lan = next(key for key, row in self.db.rep_rows.items() if row["rep"] == 10048 and row["mes"].month == 6)
        self.db.rep_rows[lan]["percentual"] = D("35")
        grid = sales_goals_reps.load_rep_grid(1, 1, 2027)
        self.assertEqual([item["family"] for item in grid], ["800007"])
        family = grid[0]
        self.assertEqual(family["meta"][11], D("120.000"))
        reps = {rep["rep"]: rep for rep in family["reps"]}
        self.assertEqual((reps[10004]["percent"], reps[10004]["varies"], reps[10004]["launches"]), (D("60.0000"), False, 12))
        self.assertEqual((reps[10048]["percent"], reps[10048]["varies"]), (D("40.0000"), True))
        self.assertEqual(reps[10004]["months"][11], D("72.000"))


class PreloadRepTests(EnvMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        seed_meta(self.db)
        self.index = sales_goals_reps.billing_index(BILLING_ROWS, 1)

    def preload(self, **kwargs):
        return sales_goals_reps.preload_reps(2027, index_loader=lambda *args: self.index, **kwargs)

    def test_dry_run_plans_and_writes_nothing(self):
        plan = self.preload()
        self.assertEqual(plan.counts(), {"inserir": 72, "atualizar": 0, "apagar": 0, "iguais": 0})
        self.assertEqual(plan.shares[(1, 1, "800007")]["percents"], {10004: D("60.0000"), 10048: D("30.0000"), 10085: D("10.0000")})
        self.assertEqual(self.db.rep_rows, {})
        self.assertFalse(any(sql.startswith("LOCK") for sql, _ in self.db.statements))

    def test_write_verify_and_rerun_keeps_what_is_there(self):
        plan = self.preload(dry_run=False)
        self.assertTrue(plan.applied)
        self.assertEqual(len(self.db.rep_rows), 72)
        self.assertEqual(sales_goals_reps.verify_reps(2027), (24, []))
        cells = self.db.rep_cells()
        self.assertEqual(cells[(1, 4, "800004", 10004, 1)], (D("62.5"), D("625.000")))
        again = self.preload(dry_run=False)
        self.assertEqual(again.counts(), {"inserir": 0, "atualizar": 0, "apagar": 0, "iguais": 0})
        self.assertEqual(sorted(again.skipped), [(1, 1, "800007"), (1, 4, "800004")])
        self.assertEqual(len(self.db.rep_rows), 72)

    def test_recalculate_replaces_and_removes_who_did_not_sell(self):
        self.preload(dry_run=False)
        self.db.add_rep(1, 1, 900, 1001, "800007", 2027, 1, "5", "5")
        plan = self.preload(dry_run=False, replace=True)
        self.assertEqual(plan.counts()["apagar"], 1)
        self.assertEqual(plan.counts()["iguais"], 72)
        self.assertNotIn((1, 1, "800007", 1001, 1), self.db.rep_cells())

    def test_without_inactive_reps(self):
        plan = self.preload(exclude_inactive=True)
        self.assertEqual(plan.shares[(1, 4, "800004")]["percents"], {10004: D("71.4286"), 10066: D("28.5714")})

    def test_verify_finds_a_family_that_does_not_close(self):
        self.preload(dry_run=False)
        lan = next(key for key, row in self.db.rep_rows.items() if row["rep"] == 10085 and row["mes"].month == 2)
        self.db.rep_rows[lan]["peso"] = D("1")
        checked, differences = sales_goals_reps.verify_reps(2027)
        self.assertEqual(checked, 24)
        self.assertEqual(differences, [{"empresa": 1, "filial": 1, "familia": "800007", "mes": 2,
                                        "percentual": "100.0000", "peso": "91.000", "meta": "100.000"}])


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=["testserver"])
class RepScreenTests(EnvMixin, TestCase):
    def setUp(self):
        super().setUp()
        seed_meta(self.db)
        sales_goals_reps.save_reps(1, 1, 2027, [sales_goals_reps.RepChange("800007", 10004, D("60")),
                                               sales_goals_reps.RepChange("800007", 10048, D("40"))])
        self.db.statements.clear()
        self.admin = User.objects.create_user(username="metasrep", password="x", userId="metasrep", email="r@example.com", is_system_admin=True)
        self.login(self.admin)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def post(self, payload):
        return self.client.post(reverse("dashesSalesGoalsRepsSave"), data=json.dumps(payload), content_type="application/json")

    def test_page_has_the_rep_grid(self):
        response = self.client.get(reverse("dashesSalesGoalsPage") + "?empresa=1&filial=1&ano=2027")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="mxReps"')
        self.assertContains(response, "salesGoalsReps.js")
        self.assertContains(response, GER)
        self.assertContains(response, '<option value="2026">2026')
        payload = response.context["grid_payload"]["reps"]
        self.assertEqual(payload[0]["family"], "800007")
        reps = {rep["rep"]: rep for rep in payload[0]["reps"]}
        self.assertEqual((reps[10004]["name"], reps[10004]["percent"], reps[10004]["months"][11]), ("MARCELO LJM", "60.0000", "72.000"))
        catalog = response.context["reps_catalog_payload"]
        self.assertEqual(catalog[-1], {"code": 10009, "name": "INATIVA", "active": False})

    def test_json_includes_the_reps(self):
        data = self.client.get(reverse("dashesSalesGoalsPage") + "?empresa=1&filial=4&ano=2027&formato=json").json()
        self.assertEqual(data["reps"], [{"family": "800004", "name": "SACO IMPRESSO LAMINADO (MILHEIRO)",
                                         "meta": ["1000.000"] * 12, "reps": []}])

    def test_compute_suggests_percents_without_writing(self):
        response = self.client.get(reverse("dashesSalesGoalsRepsCompute") + "?empresa=1&filial=4&ano=2027&base=2026")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["base"], "2026")
        family = data["families"]["800004"]
        self.assertEqual([(rep["rep"], rep["percent"], rep["active"]) for rep in family["reps"]],
                         [(10004, "62.5000", True), (10066, "25.0000", True), (10009, "12.5000", False)])
        self.assertEqual(family["total_kg"], "400.000")
        self.assertEqual(self.db.dml(), [])
        self.assertEqual(self.client.get(reverse("dashesSalesGoalsRepsCompute") + "?empresa=1&filial=4&ano=2027&base=2030").status_code, 400)

    def test_save_and_its_validation(self):
        response = self.post({"empresa": 1, "filial": 1, "ano": 2027, "representantes": [
            {"familia": "800007", "representante": 10085, "percentual": "10", "original": None},
            {"familia": "800007", "representante": 10004, "percentual": "50", "original": "60.0000"},
        ]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["counts"], {"inserir": 12, "atualizar": 12, "apagar": 0, "iguais": 12})
        self.assertEqual(self.db.rep_cells()[(1, 1, "800007", 10085, 12)], (D("10.0000"), D("12.000")))
        base = {"empresa": 1, "filial": 1, "ano": 2027}
        cases = [
            ({**base, "representantes": [{"familia": "800007", "representante": 10004, "percentual": "100,5"}]}, "fora de 0 a 100"),
            ({**base, "representantes": [{"familia": "800007", "representante": 10004, "percentual": "-1"}]}, "fora de 0 a 100"),
            ({**base, "representantes": [{"familia": "800007", "representante": 55555, "percentual": "1"}]}, "não existe no cadastro"),
            ({**base, "representantes": [{"familia": "80-07", "representante": 10004, "percentual": "1"}]}, "Família inválida"),
            ({**base, "representantes": [{"familia": "800007", "representante": "x", "percentual": "1"}]}, "Representante inválido"),
            ({**base, "representantes": [{"familia": "800007", "representante": 10004, "percentual": "1"},
                                         {"familia": "800007", "representante": 10004, "percentual": "2"}]}, "repetido"),
            ({**base, "representantes": []}, "Nenhum representante"),
        ]
        for payload, message in cases:
            response = self.post(payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn(message, response.json()["error"], payload)

    def test_save_conflict_is_409(self):
        response = self.post({"empresa": 1, "filial": 1, "ano": 2027, "representantes": [
            {"familia": "800007", "representante": 10004, "percentual": "70", "original": "55"},
        ]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["conflicts"][0]["atual"], "60.0000")
        self.assertFalse([sql for sql in self.db.dml() if GER in sql])

    def test_meta_save_reports_the_rep_rows_it_recomputed(self):
        response = self.client.post(reverse("dashesSalesGoalsSave"), data=json.dumps({
            "empresa": 1, "filial": 1, "ano": 2027,
            "celulas": [{"familia": "800007", "mes": 1, "valor": "200", "original": "100.000"}],
        }), content_type="application/json")
        self.assertEqual(response.json()["reps_updated"], 2)
        self.assertEqual(self.db.rep_cells()[(1, 1, "800007", 10004, 1)], (D("60"), D("120.000")))

    def test_needs_permission_and_post(self):
        user = User.objects.create_user(username="semacesso", password="x", userId="semacesso", email="s@example.com")
        self.login(user)
        self.assertEqual(self.client.get(reverse("dashesSalesGoalsRepsCompute") + "?empresa=1&filial=1&ano=2027").status_code, 403)
        self.assertEqual(self.post({"empresa": 1, "filial": 1, "ano": 2027, "representantes": []}).status_code, 403)
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="sales-goals"))
        self.assertEqual(self.client.get(reverse("dashesSalesGoalsRepsSave")).status_code, 405)

    def test_erp_failure_on_save_is_502(self):
        with mock.patch.object(sales_goals, "_connect", side_effect=lambda: (_ for _ in ()).throw(RuntimeError("sem rede"))):
            with self.assertLogs("tiqueue.sales_goals_views", level="ERROR"):
                response = self.post({"empresa": 1, "filial": 1, "ano": 2027, "representantes": [
                    {"familia": "800007", "representante": 10004, "percentual": "70", "original": "60"},
                ]})
        self.assertEqual(response.status_code, 502)


# ---------------------------------------------------------------------------
# Comando
# ---------------------------------------------------------------------------

class RepCommandTests(EnvMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        seed_meta(self.db)

    def run_command(self, *args):
        out = StringIO()
        call_command("carregar_metas_representantes", "--ano", "2027", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_lists_reps_and_writes_nothing(self):
        output = self.run_command()
        self.assertIn("% pelo peso faturado em 2026", output)
        self.assertIn("1/4 800004 [SACO BI LAMINADO IMPRESSO + SACO LAMINADO IMPRESSO]: base 400,000 kg, 3 representante(s)", output)
        self.assertIn("62,5000%", output)
        self.assertIn("[INATIVO no ERP]", output)
        self.assertIn("Simulação em SENIORERPSIMULACOES.USU_TORCGER: 72 a inserir", output)
        self.assertIn("Nada foi gravado", output)
        self.assertEqual(self.db.rep_rows, {})

    def test_write_verify_rerun_and_conferir(self):
        output = self.run_command("--gravar")
        self.assertIn("Gravação em SENIORERPSIMULACOES.USU_TORCGER: 72 a inserir", output)
        self.assertIn("em 24 famílias-mês os % somam 100", output)
        again = self.run_command("--gravar")
        self.assertIn("0 a inserir", again)
        self.assertIn("ficaram como estão: 2", again)
        self.assertIn("os % somam 100", self.run_command("--conferir"))
        next(iter(self.db.rep_rows.values()))["peso"] = D("0.001")
        with self.assertRaisesMessage(CommandError, "não fecham a meta"):
            self.run_command("--conferir")

    def test_without_inactive_and_one_branch(self):
        output = self.run_command("--sem-inativos", "--filial", "4")
        self.assertIn("71,4286%", output)
        self.assertNotIn("1/1 800007", output)
        self.assertNotIn("INATIVO", output)
