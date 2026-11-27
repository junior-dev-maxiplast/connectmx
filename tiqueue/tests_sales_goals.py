"""Metas de Vendas (USU_TORCMET): leitura da planilha, mapeamento grupo → família, gravação no Oracle
(simulado em memória), a tela do Dashes e o comando de pré-carga."""

import datetime
import json
import os
import tempfile
from decimal import Decimal
from io import StringIO
from unittest import mock

import openpyxl
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import commercial_dna, sales_goals
from .models import Dashboard, DashboardAccess

D = Decimal
User = get_user_model()
TABLE = "SENIORERPSIMULACOES.USU_TORCMET"
MONTH_HEADERS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
FULL_MONTHS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

RAMP = [180000] * 3 + [213333] * 3 + [246667] * 3 + [280000] * 3
SAMPLE_ROWS = [
    ("MATRIZ", "BOBINA MONO IMPRESSO", [231648.36] * 12),
    ("MATRIZ", "SACO LAMINADO IMPRESSO", [0] * 12),
    ("RAFIA", "SACO LAMINADO IMPRESSO", [111500] * 12),
    ("BIG BAG", "SACO CONVENCIONAL IMPRESSO", [28264.4934] * 12),
    ("SERAFINA", "SACO BI LAMINADO IMPRESSO", [1899.9999999999998] * 12),
    ("SERAFINA", "SACO LAMINADO IMPRESSO", [23630] * 12),
    ("ADESIVADO", "BOBINA ADESIVADO LISO", RAMP),
]

# As 31 linhas (unidade, grupo) da planilha "Projeção de Vendas 2027 por Família".
SHEET_2027_GROUPS = [
    ("ADESIVADO", "BOBINA ADESIVADO IMPRESSO"), ("ADESIVADO", "BOBINA ADESIVADO LISO"),
    ("BIG BAG", "BOBINA CONVENCIONAL LISO"), ("BIG BAG", "BOBINA LAMINADO LISO"),
    ("BIG BAG", "SACO CONVENCIONAL IMPRESSO"), ("BIG BAG", "SACO CONVENCIONAL LISO"),
    ("MATRIZ", "BOBINA LAMINADO IMPRESSO"), ("MATRIZ", "BOBINA MONO IMPRESSO"), ("MATRIZ", "BOBINA MONO LISO"),
    ("MATRIZ", "FOLHA MONO LISO"), ("MATRIZ", "SACO LAMINADO IMPRESSO"), ("MATRIZ", "SACO MONO IMPRESSO"),
    ("MATRIZ", "SACO MONO LISO"),
    ("RAFIA", "BOBINA CONVENCIONAL LISO"), ("RAFIA", "BOBINA LAMINADO LISO"), ("RAFIA", "REFUGO MONO LISO"),
    ("RAFIA", "SACO CONVENCIONAL IMPRESSO"), ("RAFIA", "SACO CONVENCIONAL LISO"), ("RAFIA", "SACO LAMINADO IMPRESSO"),
    ("RAFIA", "SACO LAMINADO LISO"),
    ("SERAFINA", "BOBINA BI LAMINADO IMPRESSO"), ("SERAFINA", "BOBINA LAMINADO IMPRESSO"),
    ("SERAFINA", "BOBINA LAMINADO LISO"), ("SERAFINA", "BOBINA MONO IMPRESSO"), ("SERAFINA", "BOBINA MONO LISO"),
    ("SERAFINA", "FOLHA MONO LISO"), ("SERAFINA", "SACO BI LAMINADO IMPRESSO"), ("SERAFINA", "SACO LAMINADO IMPRESSO"),
    ("SERAFINA", "SACO LAMINADO LISO"), ("SERAFINA", "SACO MONO IMPRESSO"), ("SERAFINA", "SACO MONO LISO"),
]


# ---------------------------------------------------------------------------
# Oracle em memória
# ---------------------------------------------------------------------------

class FakeOracle:
    """USU_TORCMET em memória. Entende só os comandos que `sales_goals` usa e devolve NUMBER como float,
    como o driver. Grava só dentro de uma transação travada (LOCK TABLE) e fora do modo somente leitura."""

    def __init__(self, table=TABLE):
        self.table = table
        self.rows = {}  # (empresa, filial, lançamento) -> {"familia", "ano", "mes", "peso"}
        self.rep_rows = {}  # USU_TORCGER: (empresa, filial, lançamento) -> {"rep", "familia", "ano", "mes", "percentual", "peso"}
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on = None

    @property
    def ger_table(self):
        return self.table.replace("USU_TORCMET", "USU_TORCGER")

    def add_rep(self, company, branch, codlan, rep, family, year, month, percent, weight):
        self.rep_rows[(company, branch, codlan)] = {
            "rep": rep,
            "familia": family,
            "ano": datetime.datetime(year, 1, 1),
            "mes": datetime.datetime(year, month, 1),
            "percentual": None if percent is None else D(str(percent)),
            "peso": None if weight is None else D(str(weight)),
        }

    def rep_cells(self):
        """{(empresa, filial, família, representante, mês): (%, kg)}."""
        result = {}
        for (company, branch, _codlan), row in self.rep_rows.items():
            key = (company, branch, row["familia"], row["rep"], row["mes"].month)
            if key in result:
                raise AssertionError(f"representante repetido no mesmo mês: {key}")
            result[key] = (row["percentual"], row["peso"])
        return result

    def add(self, company, branch, codlan, family, year, month, weight):
        self.rows[(company, branch, codlan)] = {
            "familia": family,
            "ano": datetime.datetime(year, 1, 1) if year else None,
            "mes": datetime.datetime(year or 2027, month, 1) if month else None,
            "peso": None if weight is None else D(str(weight)),
        }

    def connect(self):
        return _FakeConnection(self)

    def cells(self):
        result = {}
        for (company, branch, _codlan), row in self.rows.items():
            key = (company, branch, row["familia"], row["mes"].month if row["mes"] else None)
            result[key] = result.get(key, D(0)) + (row["peso"] or D(0))
        return result

    def dml(self):
        return [sql for sql, _ in self.statements if sql.startswith(("INSERT", "UPDATE", "DELETE"))]


class _FakeConnection:
    def __init__(self, db):
        self.db = db
        self.work = {key: dict(value) for key, value in db.rows.items()}
        self.rep_work = {key: dict(value) for key, value in db.rep_rows.items()}
        self.read_only = False
        self.locked = False
        self.ger_locked = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.db.rows = {key: dict(value) for key, value in self.work.items()}
        self.db.rep_rows = {key: dict(value) for key, value in self.rep_work.items()}
        self.db.commits += 1
        self.read_only = self.locked = self.ger_locked = False

    def rollback(self):
        self.work = {key: dict(value) for key, value in self.db.rows.items()}
        self.rep_work = {key: dict(value) for key, value in self.db.rep_rows.items()}
        self.db.rollbacks += 1
        self.read_only = self.locked = self.ger_locked = False

    def close(self):
        pass


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = []
        self.rowcount = 0

    def _in_year(self, row, params):
        reference = row["ano"] or row["mes"]
        return reference is not None and params["inicio"] <= reference < params["fim"]

    def _check_write(self):
        if self.connection.read_only:
            raise RuntimeError("ORA-01456: operação não permitida em transação somente de leitura")
        if not self.connection.locked:
            raise AssertionError("gravou sem travar a tabela")

    def execute(self, sql, params=None):
        params = dict(params or {})
        db, work = self.connection.db, self.connection.work
        text = " ".join(sql.split()).upper()
        db.statements.append((text, params))
        if db.fail_on and text.startswith(db.fail_on):
            raise RuntimeError("ORA-03113: fim de arquivo no canal de comunicação (simulado)")
        self.result, self.rowcount = [], 0
        if text == "SET TRANSACTION READ ONLY":
            self.connection.read_only = True
            return
        if db.ger_table in text:
            self._execute_ger(text, params)
            return
        if db.table not in text:
            raise AssertionError(f"tabela errada: {text}")
        if text.startswith("LOCK TABLE"):
            self.connection.locked = True
            return
        if text.startswith("SELECT A.USU_CODLAN"):
            self.result = [
                (codlan, row["familia"], row["mes"].month if row["mes"] else None, None if row["peso"] is None else float(row["peso"]))
                for (company, branch, codlan), row in sorted(work.items())
                if company == params["empresa"] and branch == params["filial"] and self._in_year(row, params)
            ]
        elif text.startswith("SELECT A.USU_CODEMP"):
            groups = {}
            for (company, branch, _codlan), row in work.items():
                if self._in_year(row, params):
                    entry = groups.setdefault((company, branch), [0, set(), D(0)])
                    entry[0] += 1
                    entry[1].add(row["familia"])
                    entry[2] += row["peso"] or D(0)
            self.result = [(c, b, n, len(f), float(w)) for (c, b), (n, f, w) in sorted(groups.items())]
        elif text.startswith("SELECT DISTINCT EXTRACT(YEAR"):
            years = {(row["ano"] or row["mes"]).year for row in work.values() if row["ano"] or row["mes"]}
            self.result = [(year,) for year in years]
        elif text.startswith("SELECT NVL(MAX(USU_CODLAN), 0)"):
            self.result = [(max((key[2] for key in work), default=0),)]
        elif text.startswith("INSERT INTO"):
            self._check_write()
            key = (params["empresa"], params["filial"], params["lancamento"])
            if key in work:
                raise RuntimeError("ORA-00001: restrição exclusiva (CP_USU_TORCMET) violada")
            work[key] = {"familia": params["familia"], "ano": params["ano"], "mes": params["mes"], "peso": params["peso"]}
            self.rowcount = 1
        elif text.startswith("UPDATE"):
            self._check_write()
            key = (params["empresa"], params["filial"], params["lancamento"])
            if key in work:
                work[key]["peso"] = params["peso"]
                self.rowcount = 1
        elif text.startswith("DELETE FROM"):
            self._check_write()
            key = (params["empresa"], params["filial"], params["lancamento"])
            if work.pop(key, None) is not None:
                self.rowcount = 1
        else:
            raise AssertionError(f"SQL inesperado: {text}")

    def _execute_ger(self, text, params):
        """Comandos da USU_TORCGER (metas por representante)."""
        work = self.connection.rep_work
        if text.startswith("LOCK TABLE"):
            self.connection.ger_locked = True
            return
        if text.startswith("SELECT G.USU_CODLAN"):
            self.result = [
                (codlan, row["familia"], row["rep"], row["mes"].month if row["mes"] else None,
                 None if row["percentual"] is None else float(row["percentual"]),
                 None if row["peso"] is None else float(row["peso"]))
                for (company, branch, codlan), row in sorted(work.items())
                if company == params["empresa"] and branch == params["filial"] and self._in_year(row, params)
            ]
            return
        if text.startswith("SELECT NVL(MAX(USU_CODLAN), 0)"):
            self.result = [(max((key[2] for key in work), default=0),)]
            return
        if text.startswith(("INSERT", "UPDATE", "DELETE")):
            if self.connection.read_only:
                raise RuntimeError("ORA-01456: operação não permitida em transação somente de leitura")
            if not self.connection.ger_locked:
                raise AssertionError("gravou na USU_TORCGER sem travar a tabela")
            key = (params["empresa"], params["filial"], params["lancamento"])
            if text.startswith("INSERT"):
                if key in work:
                    raise RuntimeError("lançamento repetido na USU_TORCGER")
                work[key] = {"rep": params["representante"], "familia": params["familia"], "ano": params["ano"],
                             "mes": params["mes"], "percentual": params["percentual"], "peso": params["peso"]}
                self.rowcount = 1
            elif key in work and work[key]["rep"] == params["representante"]:
                if text.startswith("UPDATE"):
                    work[key]["percentual"], work[key]["peso"] = params["percentual"], params["peso"]
                else:
                    del work[key]
                self.rowcount = 1
            return
        raise AssertionError(f"SQL inesperado na USU_TORCGER: {text}")

    def fetchall(self):
        return list(self.result)

    def fetchone(self):
        return self.result[0] if self.result else None

    def close(self):
        pass


FAKE_REPS = [
    {"codigo": 1001, "nome": "VENDA DIRETA", "apelido": "", "situacao": "A"},
    {"codigo": 10004, "nome": "LJM REPRESENTACOES", "apelido": "MARCELO LJM", "situacao": "A"},
    {"codigo": 10009, "nome": "REPRESENTANTE INATIVO", "apelido": "INATIVA", "situacao": "I"},
    {"codigo": 10048, "nome": "TAMYELLEN", "apelido": "", "situacao": "A"},
    {"codigo": 10066, "nome": "SERAFINA REP", "apelido": "", "situacao": "A"},
    {"codigo": 10085, "nome": "JBVS REPRESENTACAO", "apelido": "", "situacao": "A"},
]


def fake_catalog(statements):
    """Responde ao `commercial_dna._run` do cadastro (E070EMP, E070FIL, E012FAM, E090REP)."""
    data = _fake_catalog_data()
    data["representantes"] = FAKE_REPS
    return {name: data[name] for name in statements}


def _fake_catalog_data():
    return {
        "empresas": [{"codigo": 1, "nome": "MAXIPLAST"}, {"codigo": 2, "nome": "MAGNAPLAST"}],
        "filiais": [
            {"empresa": 1, "codigo": 1, "sigla": "MATRIZ", "nome": "MAXIPLAST"},
            {"empresa": 1, "codigo": 4, "sigla": "SERAFINA (F04)", "nome": "MAXIPLAST"},
            {"empresa": 1, "codigo": 5, "sigla": "MAXIPLAST BARRACAO", "nome": "MAXIPLAST"},
            {"empresa": 2, "codigo": 1, "sigla": "MAGNAPLAST", "nome": "MAGNAPLAST"},
        ],
        "familias": [
            {"empresa": 1, "codigo": "800004", "nome": "SACO IMPRESSO LAMINADO (MILHEIRO)"},
            {"empresa": 1, "codigo": "800007", "nome": "FILME IMPRESSO (KG)"},
            {"empresa": 1, "codigo": "800031", "nome": "FILME ADESIVADO LISO (KG)"},
            {"empresa": 1, "codigo": "OUT", "nome": "OUTROS SERVICOS"},
            {"empresa": 2, "codigo": "800001", "nome": "MATERIAL RECUPERADO PE"},
        ],
    }


# ---------------------------------------------------------------------------
# Planilhas de teste
# ---------------------------------------------------------------------------

def build_workbook(path, rows=SAMPLE_ROWS, *, flat=True, grouped=True, formulas=False, extra=None):
    """Planilha no layout da "Projeção de Vendas 2027 por Família": a aba oculta em tabela e a "Visão Mensal"."""
    workbook = openpyxl.Workbook()
    view = workbook.active
    view.title = "Visão Mensal 2027"
    if grouped:
        view.append(["Meta 2027 (kg)"])
        view.append(["Unidade / Família", *FULL_MONTHS, "Média mensal", "Total ano (kg)"])
        for unit in dict.fromkeys(unit for unit, _group, _values in rows):
            members = [values for row_unit, _group, values in rows if row_unit == unit]
            view.append([unit, *[sum(values[index] for values in members) for index in range(12)]])
            for row_unit, group, values in rows:
                if row_unit == unit:
                    view.append([group.title(), *values, sum(values) / 12, sum(values)])
        view.append(["TOTAL", *[sum(values[index] for _u, _g, values in rows) for index in range(12)]])
    if flat:
        sheet = workbook.create_sheet("Projeção 2027")
        sheet.append(["Crescimento geral de volume 2027", 0.03])
        sheet.append(["Reajuste geral de preço 2027", 0])
        sheet.append(["Base 2027", "texto livre"])
        sheet.append([])
        sheet.append(["Projeção de vendas 2027 por família"])
        sheet.append(["Unidade", "Família", "Real 2025 (kg/mês)", "Orçado 2026 (kg/mês)", "Real 2026 (kg/mês)",
                      "% Cresc. 2027", "Projeção 2027 (kg/mês)", "Total 2027 (kg)", "Var. vs Orç. 2026",
                      "R$/kg", "% Reajuste", "R$/kg 2027", "Faturamento", *MONTH_HEADERS])
        for line, (unit, group, values) in enumerate(rows, start=7):
            months = [f"=G{line}"] * 12 if formulas else list(values)
            total = sum(value for value in values if isinstance(value, (int, float)))
            sheet.append([unit, group, 1, 2, 3, 0.1, total / 12, total, None, 20, 0, 20, 0, *months])
        for unit, group, values in extra or []:
            sheet.append([unit, group, 0, 0, 0, 0, 0, 0, None, 0, 0, 0, 0, *values])
        sheet.append(["TOTAL", None, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, *[999] * 12])
        sheet.append(["DEPOIS DO TOTAL", "BOBINA MONO LISO", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, *[5] * 12])
        sheet.sheet_state = "hidden"
    workbook.save(path)
    return path


class TempDirMixin:
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name="projecao.xlsx"):
        return os.path.join(self.tmp.name, name)


# ---------------------------------------------------------------------------
# Leitura da planilha
# ---------------------------------------------------------------------------

class SpreadsheetReadingTests(TempDirMixin, SimpleTestCase):
    def test_reads_the_hidden_table_sheet(self):
        projection = sales_goals.read_projection(build_workbook(self.path()))
        self.assertEqual(projection.sheet, "Projeção 2027")
        self.assertEqual(projection.layout, "tabela")
        self.assertEqual(projection.year, 2027)
        self.assertEqual([(row.unit, row.group) for row in projection.rows], [(u, g) for u, g, _v in SAMPLE_ROWS])
        first = projection.rows[0]
        self.assertEqual(first.months, [D("231648.36")] * 12)
        self.assertEqual(projection.rows[4].months[0].quantize(D("0.001")), D("1900.000"))
        self.assertEqual(projection.rows[-1].months, [D(value) for value in RAMP])
        expected = sum(sum(values) for _u, _g, values in SAMPLE_ROWS)
        self.assertAlmostEqual(float(projection.total), expected, places=6)

    def test_stops_at_the_total_row(self):
        projection = sales_goals.read_projection(build_workbook(self.path()))
        self.assertNotIn("DEPOIS DO TOTAL", [row.unit for row in projection.rows])
        self.assertNotIn(D(999), projection.rows[-1].months)

    def test_falls_back_to_the_monthly_view(self):
        flat = sales_goals.read_projection(build_workbook(self.path("a.xlsx")))
        view = sales_goals.read_projection(build_workbook(self.path("b.xlsx"), flat=False))
        self.assertEqual(view.layout, "visao")
        self.assertEqual(view.sheet, "Visão Mensal 2027")
        self.assertEqual(view.year, 2027)
        self.assertEqual([(row.unit, row.group, row.months) for row in view.rows],
                         [(row.unit, row.group, row.months) for row in flat.rows])

    def test_text_numbers_with_brazilian_format(self):
        rows = [("MATRIZ", "BOBINA MONO LISO", ["1.234,5"] + [""] * 10 + ["12"])]
        projection = sales_goals.read_projection(build_workbook(self.path(), rows, grouped=False))
        self.assertEqual(projection.rows[0].months[0], D("1234.5"))
        self.assertEqual(projection.rows[0].months[1], D(0))
        self.assertEqual(projection.rows[0].months[11], D(12))

    def test_non_numeric_cell_is_reported(self):
        rows = [("MATRIZ", "BOBINA MONO LISO", ["abc"] + [1] * 11)]
        with self.assertRaisesMessage(sales_goals.SalesGoalsError, "Valor não numérico em Projeção 2027!linha 7"):
            sales_goals.read_projection(build_workbook(self.path(), rows, grouped=False))

    def test_formulas_without_saved_values_are_refused(self):
        with self.assertRaisesMessage(sales_goals.SalesGoalsError, "não tem valores calculados"):
            sales_goals.read_projection(build_workbook(self.path(), grouped=False, formulas=True))

    def test_workbook_without_the_layout_is_refused(self):
        workbook = openpyxl.Workbook()
        workbook.active.append(["Cliente", "Valor"])
        workbook.active.append(["X", 10])
        workbook.save(self.path())
        with self.assertRaisesMessage(sales_goals.SalesGoalsError, "Não encontrei na planilha"):
            sales_goals.read_projection(self.path())

    def test_missing_file_is_reported(self):
        with self.assertRaisesMessage(sales_goals.SalesGoalsError, "Não foi possível abrir a planilha"):
            sales_goals.read_projection(self.path("nao-existe.xlsx"))


# ---------------------------------------------------------------------------
# Grupo da planilha → família do ERP
# ---------------------------------------------------------------------------

class FamilyMappingTests(TempDirMixin, SimpleTestCase):
    def mapped(self, rows=SAMPLE_ROWS, **kwargs):
        return sales_goals.map_projection(sales_goals.read_projection(build_workbook(self.path(), rows, grouped=False, **kwargs)))

    def test_units_and_groups_go_to_branch_and_family(self):
        mapped = self.mapped()
        self.assertEqual(mapped.year, 2027)
        self.assertEqual(mapped.cells[(1, 1, "800007")], [D("231648.360")] * 12)
        self.assertEqual(mapped.cells[(1, 1, "800004")], [D("0.000")] * 12)
        self.assertEqual(mapped.cells[(1, 2, "800016")], [D("111500.000")] * 12)  # Ráfia fatura em outra família
        self.assertEqual(mapped.cells[(1, 3, "800040")], [D("28264.493")] * 12)  # Big Bag idem
        self.assertEqual(mapped.cells[(1, 5, "800031")], [D(value).quantize(D("0.001")) for value in RAMP])  # Adesivado = unidade 5

    def test_two_groups_in_the_same_family_are_summed(self):
        mapped = self.mapped()
        self.assertEqual(mapped.cells[(1, 4, "800004")], [D("25530.000")] * 12)
        self.assertEqual(mapped.sources[(1, 4, "800004")], ["SACO BI LAMINADO IMPRESSO", "SACO LAMINADO IMPRESSO"])

    def test_totals_keep_the_sheet_total_up_to_rounding(self):
        mapped = self.mapped()
        self.assertEqual(mapped.goal_cells(), 5 * 12)
        self.assertLess(abs(mapped.total - mapped.sheet_total), D("0.001") * mapped.goal_cells())
        self.assertEqual(mapped.branch_totals()[(1, 5)], D(sum(RAMP)))
        self.assertEqual(mapped.month_totals()[0], D("231648.360") + D("111500") + D("28264.493") + D("25530") + D(180000))

    def test_unknown_unit_or_group_is_reported_and_blocks_only_with_goal(self):
        extra = [("FILIAL NOVA", "BOBINA MONO LISO", [10] * 12), ("MATRIZ", "GRUPO NOVO", [0] * 12)]
        mapped = self.mapped(extra=extra)
        self.assertEqual([(unit, group) for unit, group, _total in mapped.unmapped], [("FILIAL NOVA", "BOBINA MONO LISO"), ("MATRIZ", "GRUPO NOVO")])
        self.assertEqual(mapped.blocking, [("FILIAL NOVA", "BOBINA MONO LISO", D(120))])

    def test_year_is_required_when_the_sheet_has_none(self):
        projection = sales_goals.Projection(sheet="Metas", layout="tabela", year=None, rows=[])
        with self.assertRaisesMessage(sales_goals.SalesGoalsError, "informe --ano"):
            sales_goals.map_projection(projection)
        self.assertEqual(sales_goals.map_projection(projection, 2028).year, 2028)

    def test_every_line_of_the_2027_sheet_has_a_branch_and_family(self):
        for unit, group in SHEET_2027_GROUPS:
            self.assertIn(unit, sales_goals.UNIT_BRANCHES, unit)
            family = sales_goals.family_for(unit, group)
            self.assertRegex(family or "", r"^800\d{3}$", f"{unit} / {group}")

    def test_group_names_are_the_dna_product_groups(self):
        firsts, structures, finishes = {"BOBINA", "SACO", "FOLHA", "REFUGO"}, {"BI LAMINADO", "LAMINADO", "CONVENCIONAL", "ADESIVADO", "MONO"}, {"LISO", "IMPRESSO"}
        for group in sales_goals.GROUP_FAMILIES:
            first, _, rest = group.partition(" ")
            structure, _, finish = rest.rpartition(" ")
            self.assertIn(first, firsts, group)
            self.assertIn(structure, structures, group)
            self.assertIn(finish, finishes, group)
        self.assertIn(commercial_dna.product_group("FILME IMPRESSO LAMINADO (KG)", "A+B+A+C"), sales_goals.GROUP_FAMILIES)

    def test_weight_parsing(self):
        self.assertEqual(sales_goals.to_weight("231.648,36"), D("231648.360"))
        self.assertEqual(sales_goals.to_weight("231648.3604"), D("231648.360"))
        self.assertEqual(sales_goals.to_weight(28264.4935), D("28264.494"))
        self.assertIsNone(sales_goals.to_weight("  "))
        with self.assertRaises(sales_goals.SalesGoalsError):
            sales_goals.to_weight("12kg")
        with self.assertRaises(sales_goals.SalesGoalsError):
            sales_goals.check_weight(D("-1"))
        with self.assertRaises(sales_goals.SalesGoalsError):
            sales_goals.check_weight(D("1000000000"))
        self.assertEqual(sales_goals.format_kg(D("6849228.48")), "6.849.228 kg")


# ---------------------------------------------------------------------------
# Plano de gravação
# ---------------------------------------------------------------------------

class PlanTests(SimpleTestCase):
    def plan(self, current, changes, check=True):
        return sales_goals.plan_changes({(1, 1): current}, {(1, 1): changes}, check=check)

    def test_insert_update_delete_and_unchanged(self):
        current = {("800007", 1): [(10, D("5.000"))], ("800007", 2): [(11, D("6.000"))], ("800007", 3): [(12, D("7.000"))]}
        plan = self.plan(current, [
            sales_goals.Change("800007", 1, D("5.000"), D("5.000")),
            sales_goals.Change("800007", 2, D("8.000"), D("6.000")),
            sales_goals.Change("800007", 3, None, D("7.000")),
            sales_goals.Change("800007", 4, D("9.000"), None),
        ])
        self.assertEqual(plan.counts(), {"inserir": 1, "atualizar": 1, "apagar": 1, "iguais": 1})
        kinds = {(op.kind, op.month): op for op in plan.operations}
        self.assertEqual(kinds[("update", 2)].codlan, 11)
        self.assertEqual(kinds[("update", 2)].previous, D("6.000"))
        self.assertEqual(kinds[("delete", 3)].codlan, 12)
        self.assertIsNone(kinds[("insert", 4)].codlan)

    def test_zero_means_no_goal(self):
        plan = self.plan({("800007", 1): [(10, D("5.000"))]}, [sales_goals.Change("800007", 1, D("0.000"), D("5.000"))])
        self.assertEqual([op.kind for op in plan.operations], ["delete"])
        plan = self.plan({}, [sales_goals.Change("800007", 2, D("0.000"), None)])
        self.assertEqual(plan.counts()["iguais"], 1)
        self.assertEqual(plan.operations, [])

    def test_duplicated_launches_become_one(self):
        current = {("800007", 1): [(30, D("2.000")), (10, D("1.000"))]}
        plan = self.plan(current, [sales_goals.Change("800007", 1, D("4.000"), D("3.000"))])
        self.assertEqual([(op.kind, op.codlan) for op in plan.operations], [("update", 10), ("delete", 30)])
        plan = self.plan({("800007", 1): [(10, D("4.000")), (30, D("1.000"))]}, [sales_goals.Change("800007", 1, D("4.000"), D("5.000"))])
        self.assertEqual([(op.kind, op.codlan) for op in plan.operations], [("delete", 30)])

    def test_conflict_when_someone_saved_after_the_screen_opened(self):
        current = {("800007", 1): [(10, D("5.000"))]}
        plan = self.plan(current, [sales_goals.Change("800007", 1, D("8.000"), None), sales_goals.Change("800007", 2, D("1.000"), D("0"))])
        self.assertEqual(plan.conflicts, [{"empresa": 1, "filial": 1, "familia": "800007", "mes": 1, "tela": None, "atual": "5.000"}])
        self.assertEqual([op.kind for op in plan.operations], ["insert"])
        self.assertEqual(self.plan(current, [sales_goals.Change("800007", 1, D("8.000"))], check=False).counts()["atualizar"], 1)

    def test_repeated_cell_in_the_request_keeps_the_last(self):
        plan = self.plan({}, [sales_goals.Change("800007", 1, D("1.000")), sales_goals.Change("800007", 1, D("2.000"))])
        self.assertEqual([(op.kind, op.value) for op in plan.operations], [("insert", D("2.000"))])


# ---------------------------------------------------------------------------
# Gravação no Oracle (simulado)
# ---------------------------------------------------------------------------

class OracleWriteTests(TempDirMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.db = FakeOracle()

    def mapped(self):
        return sales_goals.map_projection(sales_goals.read_projection(build_workbook(self.path(), grouped=False)))

    def test_insert_takes_the_next_launch_and_the_reference_dates(self):
        self.db.add(1, 4, 41, "800004", 2026, 5, 10)
        plan = sales_goals.save_grid(1, 1, 2027, [
            sales_goals.Change("800007", 1, D("231648.360")),
            sales_goals.Change("800007", 2, D("100.000")),
        ], connect=self.db.connect)
        self.assertTrue(plan.applied)
        self.assertEqual(self.db.statements[0][0], f"LOCK TABLE {TABLE} IN EXCLUSIVE MODE WAIT 10")
        inserts = [params for sql, params in self.db.statements if sql.startswith("INSERT")]
        self.assertEqual([params["lancamento"] for params in inserts], [42, 43])
        self.assertEqual(inserts[0]["ano"], datetime.datetime(2027, 1, 1))
        self.assertEqual(inserts[1]["mes"], datetime.datetime(2027, 2, 1))
        self.assertEqual(inserts[0]["peso"], D("231648.360"))
        self.assertTrue(all(f"INTO {TABLE} " in sql for sql in self.db.dml()))
        self.assertEqual(self.db.commits, 1)
        self.assertEqual(self.db.cells()[(1, 1, "800007", 1)], D("231648.360"))

    def test_only_changed_cells_are_touched(self):
        self.db.add(1, 1, 1, "800007", 2027, 1, "10")
        self.db.add(1, 1, 2, "800007", 2027, 2, "20")
        self.db.add(1, 1, 3, "800007", 2027, 3, "30")
        plan = sales_goals.save_grid(1, 1, 2027, [
            sales_goals.Change("800007", 2, D("25.000"), D("20.000")),
            sales_goals.Change("800007", 3, None, D("30.000")),
        ], connect=self.db.connect)
        self.assertEqual(plan.counts(), {"inserir": 0, "atualizar": 1, "apagar": 1, "iguais": 0})
        self.assertEqual(self.db.cells(), {(1, 1, "800007", 1): D(10), (1, 1, "800007", 2): D(25)})

    def test_conflict_writes_nothing(self):
        self.db.add(1, 1, 7, "800007", 2027, 1, "100")
        plan = sales_goals.save_grid(1, 1, 2027, [sales_goals.Change("800007", 1, D("50.000"), None)], connect=self.db.connect)
        self.assertFalse(plan.applied)
        self.assertEqual(len(plan.conflicts), 1)
        self.assertEqual(self.db.dml(), [])
        self.assertEqual(self.db.commits, 0)
        self.assertEqual(self.db.cells(), {(1, 1, "800007", 1): D(100)})

    def test_failure_in_the_middle_rolls_everything_back(self):
        self.db.add(1, 1, 1, "800007", 2027, 1, "10")
        self.db.fail_on = "UPDATE"
        with self.assertRaises(RuntimeError):
            sales_goals.save_grid(1, 1, 2027, [
                sales_goals.Change("800007", 2, D("5.000"), None),
                sales_goals.Change("800007", 1, D("11.000"), D("10.000")),
            ], connect=self.db.connect)
        self.assertEqual(self.db.commits, 0)
        self.assertGreaterEqual(self.db.rollbacks, 1)
        self.assertEqual(self.db.cells(), {(1, 1, "800007", 1): D(10)})

    def test_dry_run_only_reads(self):
        plan = sales_goals.preload(self.mapped(), dry_run=True, connect=self.db.connect)
        self.assertEqual(plan.counts(), {"inserir": 60, "atualizar": 0, "apagar": 0, "iguais": 0})
        self.assertFalse(plan.applied)
        sqls = [sql for sql, _ in self.db.statements]
        self.assertEqual(sqls[0], "SET TRANSACTION READ ONLY")
        self.assertFalse(any(sql.startswith(("LOCK", "INSERT", "UPDATE", "DELETE")) for sql in sqls))
        self.assertEqual(self.db.rows, {})

    def test_preload_is_idempotent_and_verified(self):
        mapped = self.mapped()
        first = sales_goals.preload(mapped, dry_run=False, connect=self.db.connect)
        self.assertEqual(first.counts()["inserir"], 60)
        self.assertEqual(len(self.db.rows), 60)
        self.assertEqual(sorted(key[2] for key in self.db.rows), list(range(1, 61)))
        second = sales_goals.preload(mapped, dry_run=False, connect=self.db.connect)
        self.assertEqual(second.counts(), {"inserir": 0, "atualizar": 0, "apagar": 0, "iguais": 60})
        self.assertEqual(len(self.db.rows), 60)
        self.assertEqual(self.db.commits, 1)
        checked, differences = sales_goals.verify(mapped, connect=self.db.connect)
        self.assertEqual((checked, differences), (60, []))
        self.assertEqual(self.db.cells()[(1, 4, "800004", 6)], D("25530.000"))
        self.assertEqual(sum(self.db.cells().values()), mapped.total)

    def test_preload_updates_what_changed_and_keeps_what_was_typed_on_screen(self):
        mapped = self.mapped()
        sales_goals.preload(mapped, dry_run=False, connect=self.db.connect)
        self.db.add(1, 1, 500, "800004", 2027, 1, "77")  # digitado na tela; zerado na planilha
        lan = next(key for key, row in self.db.rows.items() if key[:2] == (1, 1) and row["familia"] == "800007" and row["mes"].month == 3)
        self.db.rows[lan]["peso"] = D("1.000")
        plan = sales_goals.preload(mapped, dry_run=False, connect=self.db.connect)
        self.assertEqual(plan.counts(), {"inserir": 0, "atualizar": 1, "apagar": 0, "iguais": 59})
        self.assertEqual(self.db.cells()[(1, 1, "800007", 3)], D("231648.360"))
        self.assertEqual(self.db.cells()[(1, 1, "800004", 1)], D(77))

    def test_verify_reports_differences(self):
        mapped = self.mapped()
        sales_goals.preload(mapped, dry_run=False, connect=self.db.connect)
        lan = next(key for key, row in self.db.rows.items() if key[:2] == (1, 5) and row["mes"].month == 12)
        self.db.rows[lan]["peso"] = D("1.000")
        checked, differences = sales_goals.verify(mapped, connect=self.db.connect)
        self.assertEqual(checked, 60)
        self.assertEqual(differences, [{"empresa": 1, "filial": 5, "familia": "800031", "mes": 12,
                                        "planilha": "280000.000", "tabela": "1.000", "lancamentos": 1}])

    def test_schema_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {sales_goals.SCHEMA_ENV: "sapiens"}):
            self.assertEqual(sales_goals.table_ref(), "SAPIENS.USU_TORCMET")
            self.assertFalse(sales_goals.is_simulation())
        with mock.patch.dict(os.environ, {sales_goals.SCHEMA_ENV: ""}):
            self.assertEqual(sales_goals.table_ref(), TABLE)
            self.assertTrue(sales_goals.is_simulation())
        for bad in ("x;drop table", "1ABC", "A.B"):
            with self.assertRaises(sales_goals.SalesGoalsError):
                sales_goals.table_ref(bad)

    def test_grid_groups_launches_by_family_and_month(self):
        self.db.add(1, 1, 1, "800007", 2027, 1, "10")
        self.db.add(1, 1, 2, "800007", 2027, 1, "5")
        self.db.add(1, 1, 3, "800001", 2027, 12, "7")
        self.db.add(1, 1, 4, None, 2027, 2, "3")
        self.db.add(1, 4, 5, "800004", 2027, 1, "100")
        self.db.add(1, 1, 6, "800007", 2026, 1, "999")
        grid = sales_goals.load_grid(1, 1, connect=self.db.connect, today=datetime.date(2026, 11, 19))
        self.assertEqual(grid["year"], 2027)
        self.assertEqual(grid["years"], [2026, 2027])
        self.assertEqual([row["family"] for row in grid["rows"]], ["800001", "800007"])
        self.assertEqual(grid["rows"][1]["months"][0], D("15.000"))
        self.assertEqual(grid["rows"][1]["launches"][0], [1, 2])
        self.assertIsNone(grid["rows"][1]["months"][1])
        self.assertEqual(grid["outside"], 1)
        self.assertEqual([(item["filial"], item["peso"]) for item in grid["summary"]], [(1, D("25.000")), (4, D("100.000"))])
        self.assertEqual(self.db.dml(), [])

    def test_empty_table_opens_next_year(self):
        grid = sales_goals.load_grid(1, 1, connect=self.db.connect, today=datetime.date(2026, 11, 19))
        self.assertEqual((grid["year"], grid["rows"], grid["years"]), (2027, [], []))


# ---------------------------------------------------------------------------
# Tela do Dashes
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=["testserver"])
class SalesGoalsPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.db = FakeOracle()
        self.db.add(1, 1, 1, "800007", 2027, 1, "231648.36")
        self.db.add(1, 4, 2, "800004", 2027, 1, "25530")
        for target, value in ((sales_goals.dna, "_run"), (sales_goals, "_connect")):
            patcher = mock.patch.object(target, value, side_effect=fake_catalog if value == "_run" else self.db.connect)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {sales_goals.SCHEMA_ENV: ""})
        env.start()
        self.addCleanup(env.stop)
        self.admin = User.objects.create_user(username="metas", password="x", userId="metas", email="m@example.com", is_system_admin=True)
        self.login(self.admin)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def post(self, payload, raw=None):
        return self.client.post(reverse("dashesSalesGoalsSave"), data=raw if raw is not None else json.dumps(payload), content_type="application/json")

    def test_dashboard_is_in_the_catalog(self):
        dashboard = Dashboard.objects.get(slug="sales-goals")
        self.assertEqual((dashboard.name, dashboard.url_name, dashboard.is_active), ("Metas de Vendas", "dashesSalesGoalsPage", True))

    def test_page_renders_the_grid(self):
        response = self.client.get(reverse("dashesSalesGoalsPage") + "?empresa=1&filial=1&ano=2027")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Metas de Vendas")
        self.assertContains(response, TABLE)
        self.assertContains(response, "Ambiente de simulação do Senior")
        self.assertContains(response, "salesGoals.js")
        self.assertContains(response, 'id="mxgData"')
        self.assertContains(response, "5 · MAXIPLAST BARRACAO — Adesivado")
        self.assertContains(response, "1 · MATRIZ · 231.648 kg")
        self.assertContains(response, "4 · SERAFINA (F04) · 25.530 kg")
        self.assertContains(response, reverse("dashesSalesGoalsPage"))
        payload = response.context["grid_payload"]
        self.assertEqual(payload["rows"], [{"family": "800007", "name": "FILME IMPRESSO (KG)", "months": ["231648.360"] + [None] * 11, "launches": [[1]] + [[]] * 11}])
        self.assertEqual([item["code"] for item in response.context["families_payload"]], ["800004", "800007", "800031", "OUT"])

    def test_json_format_and_unknown_branch_falls_back(self):
        data = self.client.get(reverse("dashesSalesGoalsPage") + "?empresa=1&filial=4&ano=2027&formato=json").json()
        self.assertEqual(data["rows"][0]["family"], "800004")
        response = self.client.get(reverse("dashesSalesGoalsPage") + "?empresa=2&filial=9")
        self.assertEqual((response.context["company"], response.context["branch"]), (2, 1))

    def test_needs_the_dashes_session(self):
        self.client.logout()
        response = self.client.get(reverse("dashesSalesGoalsPage"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashesLoginPage"), response["Location"])

    def test_needs_the_dashboard_permission(self):
        user = User.objects.create_user(username="vend", password="x", userId="vend", email="v@example.com")
        self.login(user)
        self.assertEqual(self.client.get(reverse("dashesSalesGoalsPage")).status_code, 403)
        self.assertEqual(self.post({"empresa": 1, "filial": 1, "ano": 2027, "celulas": []}).status_code, 403)
        DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="sales-goals"))
        response = self.client.get(reverse("dashesSalesGoalsPage"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="mx-tab is-active"')

    def test_save_writes_the_changed_cells(self):
        response = self.post({"empresa": 1, "filial": 1, "ano": 2027, "celulas": [
            {"familia": "800007", "mes": 1, "valor": "231700.000", "original": "231648.360"},
            {"familia": "800031", "mes": 2, "valor": "180000", "original": None},
        ]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"ok": True, "counts": {"inserir": 1, "atualizar": 1, "apagar": 0, "iguais": 0}, "applied": True, "reps_updated": 0})
        self.assertEqual(self.db.cells()[(1, 1, "800007", 1)], D("231700.000"))
        self.assertEqual(self.db.cells()[(1, 1, "800031", 2)], D("180000.000"))

    def test_save_conflict_returns_409_and_writes_nothing(self):
        response = self.post({"empresa": 1, "filial": 1, "ano": 2027, "celulas": [
            {"familia": "800007", "mes": 1, "valor": "1", "original": None},
        ]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["conflicts"][0]["atual"], "231648.360")
        self.assertEqual(self.db.dml(), [])

    def test_save_validation(self):
        base = {"empresa": 1, "filial": 1, "ano": 2027}
        cases = [
            ({**base, "celulas": [{"familia": "800007", "mes": 1, "valor": "-5", "original": None}]}, "negativo"),
            ({**base, "celulas": [{"familia": "800007", "mes": 1, "valor": "1000000000", "original": None}]}, "limite"),
            ({**base, "celulas": [{"familia": "800007", "mes": 13, "valor": "1", "original": None}]}, "Mês inválido"),
            ({**base, "celulas": [{"familia": "800999", "mes": 1, "valor": "1", "original": None}]}, "não existe no cadastro"),
            ({**base, "celulas": [{"familia": "80;07", "mes": 1, "valor": "1", "original": None}]}, "Família inválida"),
            ({**base, "celulas": [{"familia": "800007", "mes": 1, "valor": "x", "original": None}]}, "Peso inválido"),
            ({**base, "celulas": [{"familia": "800007", "mes": 2, "valor": "1"}, {"familia": "800007", "mes": 2, "valor": "2"}]}, "repetida"),
            ({**base, "celulas": []}, "Nenhuma célula"),
            ({**base, "filial": 3, "celulas": [{"familia": "800007", "mes": 1, "valor": "1"}]}, "não existe na empresa"),
            ({"empresa": 1, "filial": 1, "celulas": [{"familia": "800007", "mes": 1, "valor": "1"}]}, "Informe empresa"),
        ]
        for payload, message in cases:
            response = self.post(payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn(message, response.json()["error"], payload)
        self.assertEqual(self.post(None, raw="{nada").status_code, 400)
        self.assertEqual(self.db.dml(), [])

    def test_clearing_a_cell_of_a_family_out_of_the_catalog_is_allowed(self):
        self.db.add(1, 1, 9, "799999", 2027, 3, "4")
        response = self.post({"empresa": 1, "filial": 1, "ano": 2027, "celulas": [{"familia": "799999", "mes": 3, "valor": None, "original": "4"}]})
        self.assertEqual(response.json()["counts"]["apagar"], 1)

    def test_save_only_accepts_post(self):
        self.assertEqual(self.client.get(reverse("dashesSalesGoalsSave")).status_code, 405)

    def test_erp_failure_is_reported(self):
        with mock.patch.object(sales_goals, "_connect", side_effect=lambda: (_ for _ in ()).throw(RuntimeError("sem rede"))):
            page = self.client.get(reverse("dashesSalesGoalsPage"))
            data = self.client.get(reverse("dashesSalesGoalsPage") + "?formato=json")
            with self.assertLogs("tiqueue.sales_goals_views", level="ERROR") as logs:
                saved = self.post({"empresa": 1, "filial": 1, "ano": 2027, "celulas": [{"familia": "800007", "mes": 2, "valor": "1"}]})
        self.assertIn("Falha ao gravar metas 1/1/2027", logs.output[0])
        self.assertContains(page, "Não foi possível consultar o ERP Senior: sem rede")
        self.assertEqual(data.status_code, 502)
        self.assertEqual(saved.status_code, 502)
        self.assertIn("Não foi possível gravar no ERP Senior", saved.json()["error"])


# ---------------------------------------------------------------------------
# Comando de pré-carga
# ---------------------------------------------------------------------------

class PreloadCommandTests(TempDirMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.db = FakeOracle()
        patcher = mock.patch.object(sales_goals, "_connect", side_effect=self.db.connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {sales_goals.SCHEMA_ENV: ""})
        env.start()
        self.addCleanup(env.stop)

    def run_command(self, *args):
        out = StringIO()
        call_command("carregar_metas_planilha", "--arquivo", build_workbook(self.path()), *args, stdout=out)
        return out.getvalue()

    def test_without_gravar_nothing_is_written(self):
        output = self.run_command()
        self.assertIn("Simulação em SENIORERPSIMULACOES.USU_TORCMET: 60 a inserir", output)
        self.assertIn("1/4 800004 [SACO BI LAMINADO IMPRESSO + SACO LAMINADO IMPRESSO]", output)
        self.assertIn("Sem meta na planilha (não entram): 1/1 800004", output)
        self.assertIn("Nada foi gravado", output)
        self.assertEqual(self.db.rows, {})

    def test_gravar_writes_verifies_and_is_idempotent(self):
        output = self.run_command("--gravar")
        self.assertIn("Gravação em SENIORERPSIMULACOES.USU_TORCMET: 60 a inserir", output)
        self.assertIn("as 60 células da planilha batem com a tabela", output)
        self.assertEqual(len(self.db.rows), 60)
        again = self.run_command("--gravar")
        self.assertIn("0 a inserir, 0 a atualizar, 0 a apagar, 60 já iguais", again)
        self.assertEqual(len(self.db.rows), 60)
        self.assertIn("batem com a tabela", self.run_command("--conferir"))

    def test_other_schema(self):
        self.db.table = "SAPIENS.USU_TORCMET"
        output = self.run_command("--esquema", "sapiens")
        self.assertIn("Destino: SAPIENS.USU_TORCMET.", output)

    def test_unmapped_rows_with_goal_block_the_load(self):
        path = build_workbook(self.path("x.xlsx"), SAMPLE_ROWS + [("FILIAL NOVA", "BOBINA MONO LISO", [10] * 12)], grouped=False)
        with self.assertRaisesMessage(CommandError, "FILIAL NOVA / BOBINA MONO LISO"):
            call_command("carregar_metas_planilha", "--arquivo", path, "--gravar", stdout=StringIO())
        self.assertEqual(self.db.rows, {})

    def test_conferir_reports_differences(self):
        self.run_command("--gravar")
        next(iter(self.db.rows.values()))["peso"] = D("1")
        with self.assertRaisesMessage(CommandError, "não bate com a planilha"):
            self.run_command("--conferir")
