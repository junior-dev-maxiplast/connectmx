"""
Orçamento de vendas (peso e valor) por unidade, família e mês, e de onde vêm as metas de cada ano.

As metas de peso ficam no ERP (USU_TORCMET por unidade e família, USU_TORCGER por
representante; ver `sales_goals.py` e `sales_goals_reps.py`). O ERP não guarda o valor (R$),
então o orçamento em R$ fica no modelo `SalesBudget` (peso, valor e a base do valor), com o
ponto de equilíbrio em `SalesBreakEven`. Origens:

- 2025 e 2026: as tabelas digitadas no PBIX "2. ORÇAMENTO 2026" ("ORÇAMENTO 2025 (2)",
  "ORÇAMENTO 2026" e "PONTO DE EQUILIBRIO"), com o fator R$/kg de cada grupo. O valor do PBIX é
  comparado com o faturamento com impostos, líquido de devolução (VL_FAT-DEV): base "bruto".
- 2027: a planilha "Projeção de Vendas 2027 por Família" (kg e R$/kg sem impostos): base
  "sem_impostos".

O orçamento vem por grupo de produto (BOBINA MONO IMPRESSO...) e é guardado pela família da
meta, com o mesmo mapeamento da pré-carga da USU_TORCMET (`sales_goals.family_for`).

`year_goals(ano)` monta as metas de um ano para os painéis: peso da unidade pela USU_TORCMET
quando o ano está lá (senão pelo `SalesBudget`), valor = peso × R$/kg do `SalesBudget`, e meta
por representante pela USU_TORCGER (senão pela `RepresentativeGoal`, as metas do PBIX).
"""

import json
import os
import re
import unicodedata
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from . import commercial_dna as dna
from . import representative_goals
from . import sales_goals
from .sales_goals import SalesGoalsError

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "data", "sales_budget.json")
BASIS_GROSS = "bruto"
BASIS_NET = "sem_impostos"
BASIS_LABELS = {
    BASIS_GROSS: "R$ com impostos, líquido de devolução (VL_FAT-DEV do PBIX)",
    BASIS_NET: "R$ sem impostos (ICMS, PIS e COFINS)",
}
UNIT_NAMES = {code[1]: name for name, code in sales_goals.UNIT_BRANCHES.items() if code[0] == 1}
PBIX_BUDGET_QUERIES = ("12_ORÇAMENTO 2025 (2).m", "16_ORÇAMENTO 2026.m", "56_ORÇAMENTO 2026 - BIG BAG.m", "55_ORÇAMENTO SP.m")
PBIX_BREAK_EVEN_QUERY = "11_PONTO DE EQUILIBRIO.m"
MONTHS_PT = {
    "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "ABRIL": 4, "MAIO": 5, "JUNHO": 6,
    "JULHO": 7, "AGOSTO": 8, "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12,
}
CENTS = Decimal("0.01")


def _norm(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.upper().split())


def _decimal(text):
    text = str(text if text is not None else "").strip()
    if not text:
        return Decimal(0)
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return Decimal(text)


# Na Ráfia e no Big Bag o PBIX chama de "MONO" a ráfia sem laminação, que no DNA (e nas famílias do ERP)
# é a "CONVENCIONAL": SACARIA MONO → SACO CONVENCIONAL (800013/800015), BOBINA MONO → BOBINA CONVENCIONAL (800017).
RAFFIA_UNITS = (2, 3)


def pbix_group(grupo, subgrupo, branch=None):
    """GRUPO e SUBGRUPO 2 do orçamento do PBIX → grupo de produto do DNA (`commercial_dna.product_group`).

    Ex.: ("SACARIA LAMINADA", "IMPRESSO") → "SACO LAMINADO IMPRESSO"; ("FOLHA", "LISO") → "FOLHA MONO LISO";
    na Ráfia (unidade 2), ("SACARIA MONO", "LISO") → "SACO CONVENCIONAL LISO".
    """
    group, finish = _norm(grupo), _norm(subgrupo)
    if group.startswith(("SACARIA", "SACO")):
        first = "SACO"
    elif group.startswith("BOBINA"):
        first = "BOBINA"
    elif group.startswith("FOLHA"):
        first = "FOLHA"
    elif group.startswith("REFUGO"):
        first = "REFUGO"
    else:
        return None
    if "BI LAMINAD" in group:
        structure = "BI LAMINADO"
    elif "LAMINAD" in group:
        structure = "LAMINADO"
    elif "ADESIV" in group:
        structure = "ADESIVADO"
    elif "CONVENCIONAL" in group:
        structure = "CONVENCIONAL"
    else:
        structure = "MONO"
    if structure == "MONO" and first in ("SACO", "BOBINA") and dna._int(branch) in RAFFIA_UNITS:
        structure = "CONVENCIONAL"
    return f"{first} {structure} {'IMPRESSO' if finish.startswith('IMPRESS') else 'LISO'}"


def family_of(branch, group):
    """Família da meta de um grupo de produto na unidade de orçamento (None se o grupo não tem meta)."""
    unit = UNIT_NAMES.get(branch)
    return sales_goals.family_for(unit, group) if unit and group else None


class _Bucket:
    __slots__ = ("weight", "value", "groups")

    def __init__(self):
        self.weight, self.value, self.groups = Decimal(0), Decimal(0), []


def _budget_rows(buckets, basis, source):
    rows = []
    for (year, month, branch, family), bucket in sorted(buckets.items()):
        if bucket.weight <= 0 and bucket.value <= 0:
            continue
        rows.append({
            "year": year, "month": month, "company": 1, "branch": branch, "family": family,
            "groups": " + ".join(sorted(set(bucket.groups))),
            "weight_kg": str(bucket.weight.quantize(sales_goals.WEIGHT_STEP, rounding=ROUND_HALF_UP)),
            "value": str(bucket.value.quantize(CENTS, rounding=ROUND_HALF_UP)),
            "basis": basis, "source": source,
        })
    return rows


def budget_from_pbix(folder):
    """Orçamento (kg e R$) e ponto de equilíbrio das tabelas embutidas no PBIX, já pela família da meta."""
    # Cada consulta traz linhas próprias (2025, 2026, Big Bag e SP): no PBIX elas são acrescentadas
    # umas às outras, então cada linha conta uma vez. Linhas iguais dentro de uma tabela são
    # sub-orçamentos (ex.: dois "BOBINA MONO LISO" da Matriz em 2025) e somam.
    buckets, unmapped = defaultdict(_Bucket), []
    for name in PBIX_BUDGET_QUERIES:
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            table = representative_goals.decode_embedded_table(handle.read())
        if not table:
            continue
        for raw in table["rows"]:
            row = dict(zip(table["columns"], raw))
            year = dna._int(row.get("ANO"))
            month = MONTHS_PT.get(_norm(row.get("MES")))
            branch = dna._int(row.get("UNIDADE"))
            weight = _decimal(row.get("PESO"))
            if not year or not month or not branch or weight <= 0:
                continue
            group = pbix_group(row.get("GRUPO"), row.get("SUBGRUPO 2"), branch)
            family = family_of(branch, group)
            if family is None:
                unmapped.append((year, branch, row.get("GRUPO"), row.get("SUBGRUPO 2"), str(weight)))
                continue
            bucket = buckets[(year, month, branch, family)]
            bucket.weight += weight
            bucket.value += weight * _decimal(row.get("FATOR KG"))
            bucket.groups.append(group)
    break_even = []
    path = os.path.join(folder, PBIX_BREAK_EVEN_QUERY)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            table = representative_goals.decode_embedded_table(handle.read())
        for raw in (table or {}).get("rows", []):
            row = dict(zip(table["columns"], raw))
            value = _decimal(row.get("VALOR"))
            year, month, branch = dna._int(row.get("ANO")), dna._int(row.get("MES")), dna._int(row.get("CD_UNIDADE"))
            if year and month and branch and value > 0:
                break_even.append({"year": year, "month": month, "company": 1, "branch": branch,
                                   "value": str(value.quantize(CENTS)), "source": "pbix-orcamento-2026"})
    return {"budget": _budget_rows(buckets, BASIS_GROSS, "pbix-orcamento-2026"), "break_even": break_even, "unmapped": unmapped}


def _price_column(header_labels):
    """Coluna do R$/kg do ano na aba "Projeção": "R$/kg ex-imp 2027" (não a do ano anterior, sem o ano)."""
    for index, label in enumerate(header_labels):
        text = _norm(label)
        if text.startswith("R$/KG") and re.search(r"\b20\d{2}\b", text):
            return index
    return None


def budget_from_projection(path, year=None):
    """Orçamento do ano da planilha de projeção: kg de cada mês × R$/kg sem impostos do grupo."""
    import openpyxl

    projection = sales_goals.read_projection(path)
    mapped = sales_goals.map_projection(projection, year)
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    prices = {}
    try:
        sheet = workbook[projection.sheet]
        rows = [tuple(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()
    for index, row in enumerate(rows):
        labels = [_norm(value) for value in row or ()]
        if "UNIDADE" in labels and "FAMILIA" in labels:
            column = _price_column(row)
            if column is None:
                raise SalesGoalsError(f"A aba {projection.sheet!r} não tem a coluna de R$/kg do ano.")
            unit_col, group_col = labels.index("UNIDADE"), labels.index("FAMILIA")
            for data in rows[index + 1:]:
                unit, group = _norm(data[unit_col] if unit_col < len(data) else ""), _norm(data[group_col] if group_col < len(data) else "")
                if unit == "TOTAL":
                    break
                if unit and group:
                    value = data[column] if column < len(data) else None
                    prices[(unit, group)] = _decimal(value) if value not in (None, "") else Decimal(0)
            break
    buckets = defaultdict(_Bucket)
    for row in projection.rows:
        branch = sales_goals.UNIT_BRANCHES.get(row.unit, (None, None))[1]
        family = sales_goals.family_for(row.unit, row.group)
        if branch is None or family is None:
            continue
        price = prices.get((row.unit, row.group), Decimal(0))
        for month, weight in enumerate(row.months, start=1):
            if not weight:
                continue
            bucket = buckets[(mapped.year, month, branch, family)]
            bucket.weight += weight
            bucket.value += weight * price
            bucket.groups.append(row.group)
    return {"budget": _budget_rows(buckets, BASIS_NET, f"planilha-projecao-{mapped.year}"), "break_even": [], "unmapped": mapped.blocking}


def load_fixture(path=FIXTURE_PATH):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_fixture(data, path=FIXTURE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)


def replace_budget(budget_model, break_even_model, data):
    """Troca o orçamento (e o ponto de equilíbrio) dos anos presentes em `data` pelos novos (idempotente)."""
    years = sorted({row["year"] for row in data.get("budget", [])} | {row["year"] for row in data.get("break_even", [])})
    budget_model.objects.filter(year__in=years).delete()
    break_even_model.objects.filter(year__in=years).delete()
    budget_model.objects.bulk_create([
        budget_model(
            year=row["year"], month=row["month"], company=row["company"], branch=row["branch"], family=row["family"],
            groups=row.get("groups", ""), weight_kg=Decimal(row["weight_kg"]), value=Decimal(row["value"]),
            basis=row.get("basis", BASIS_GROSS), source=row.get("source", ""),
        )
        for row in data.get("budget", [])
    ], batch_size=500)
    break_even_model.objects.bulk_create([
        break_even_model(year=row["year"], month=row["month"], company=row["company"], branch=row["branch"],
                         value=Decimal(row["value"]), source=row.get("source", ""))
        for row in data.get("break_even", [])
    ], batch_size=500)
    return len(data.get("budget", [])), len(data.get("break_even", [])), years


# ---------------------------------------------------------------------------
# Metas de um ano para os painéis
# ---------------------------------------------------------------------------

def _erp_unit_goals(year, connect=None):
    """{(empresa, filial, família, mês): kg} da USU_TORCMET no ano (vazio se o ano não está lá)."""
    table = sales_goals.table_ref()
    connection = (connect or sales_goals._connect)()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            start, end = sales_goals._year_bounds(year)
            cursor.execute(sales_goals.SUMMARY_SQL.format(table=table), {"inicio": start, "fim": end})
            scopes = sorted((int(row[0]), int(row[1])) for row in cursor.fetchall())
            current, _outside = sales_goals._scope_rows(cursor, table, scopes, year)
        finally:
            cursor.close()
            connection.rollback()
    finally:
        connection.close()
    result = {}
    for (company, branch), cells in current.items():
        for (family, month), entries in cells.items():
            result[(company, branch, family, month)] = sum((weight for _, weight in entries), Decimal(0))
    return result


def _erp_rep_goals(year, scopes, connect=None):
    from . import sales_goals_reps

    table = sales_goals_reps.ger_table_ref()
    connection = (connect or sales_goals._connect)()
    result = {}
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            for company, branch in scopes:
                for family, reps in sales_goals_reps._rep_rows(cursor, table, company, branch, year).items():
                    for rep, months in reps.items():
                        for month, entries in months.items():
                            key = (company, branch, family, rep, month)
                            result[key] = result.get(key, Decimal(0)) + sum((entry[2] for entry in entries), Decimal(0))
        finally:
            cursor.close()
            connection.rollback()
    finally:
        connection.close()
    return result


def year_goals(year, *, budget_rows=None, break_even_rows=None, rep_goal_rows=None, connect=None):
    """Metas do ano para os painéis.

    Devolve {"units": {(empresa, filial, família, mês): {"kg", "value"}}, "reps": {(empresa, filial, família,
    rep, mês): kg}, "break_even": {(empresa, filial, mês): R$}, "basis", "sources", "groups": {(filial, família): texto}}.
    `budget_rows`, `break_even_rows` e `rep_goal_rows` (dicts) vêm do banco quando não são passados.
    """
    from .models import RepresentativeGoal, SalesBreakEven, SalesBudget

    if budget_rows is None:
        budget_rows = list(SalesBudget.objects.filter(year=year).values(
            "year", "month", "company", "branch", "family", "groups", "weight_kg", "value", "basis"))
    if break_even_rows is None:
        break_even_rows = list(SalesBreakEven.objects.filter(year=year).values("year", "month", "company", "branch", "value"))
    budget = {}
    groups = {}
    bases = set()
    for row in budget_rows:
        key = (int(row["company"]), int(row["branch"]), str(row["family"]), int(row["month"]))
        weight, value = Decimal(row["weight_kg"]), Decimal(row["value"])
        budget[key] = {"kg": weight, "value": value, "price": (value / weight) if weight else Decimal(0)}
        groups[(key[1], key[2])] = row.get("groups") or groups.get((key[1], key[2]), "")
        bases.add(row.get("basis") or BASIS_GROSS)

    erp_units = _erp_unit_goals(year, connect=connect)
    units, sources = {}, {}
    if erp_units:
        sources["units"] = "erp"
        prices = defaultdict(list)
        for (company, branch, family, month), entry in budget.items():
            prices[(company, branch, family)].append((month, entry["price"]))
        for key, kg in erp_units.items():
            company, branch, family, month = key
            entry = budget.get(key)
            if entry and entry["price"]:
                price = entry["price"]
            else:
                # Mês sem preço no orçamento: usa o preço médio da família no ano.
                known = [p for _m, p in prices.get((company, branch, family), []) if p]
                price = sum(known, Decimal(0)) / len(known) if known else Decimal(0)
            units[key] = {"kg": kg, "value": (kg * price).quantize(CENTS)}
    else:
        sources["units"] = "orcamento" if budget else "nenhuma"
        units = {key: {"kg": entry["kg"], "value": entry["value"]} for key, entry in budget.items()}

    scopes = sorted({(company, branch) for company, branch, _family, _month in units})
    erp_reps = _erp_rep_goals(year, scopes, connect=connect) if scopes else {}
    reps = {}
    if erp_reps:
        sources["reps"] = "erp"
        reps = erp_reps
    else:
        if rep_goal_rows is None:
            rep_goal_rows = list(RepresentativeGoal.objects.filter(year=year).values("year", "month", "unit_code", "rep_code", "family", "weight_kg"))
        for row in rep_goal_rows:
            branch = int(row["unit_code"])
            family = family_of(branch, _norm(row["family"]))
            if family is None:
                continue
            key = (1, branch, family, dna.primary_rep(row["rep_code"]), int(row["month"]))
            reps[key] = reps.get(key, Decimal(0)) + Decimal(str(row["weight_kg"]))
        sources["reps"] = "pbix" if reps else "nenhuma"
    for (company, branch, family, _month) in units:
        groups.setdefault((branch, family), " + ".join(sorted(sales_goals_reps_groups(company, branch, family))))
    break_even = {(int(row["company"]), int(row["branch"]), int(row["month"])): Decimal(row["value"]) for row in break_even_rows}
    return {
        "units": units, "reps": reps, "break_even": break_even, "groups": groups,
        "basis": (bases.pop() if len(bases) == 1 else BASIS_GROSS) if bases else BASIS_GROSS,
        "sources": sources,
    }


def sales_goals_reps_groups(company, branch, family):
    from . import sales_goals_reps

    return sales_goals_reps.groups_for(company, branch, family)
