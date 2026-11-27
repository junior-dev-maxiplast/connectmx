"""
Metas de venda em peso (kg) por empresa, filial, família e mês, gravadas na
tabela USU_TORCMET do ERP Senior.

A tabela nasceu no ambiente de simulação do Senior (esquema
SENIORERPSIMULACOES, criada em 19/11/2026) e ainda não existe no esquema de
produção (SAPIENS). O esquema vem da variável ERP_METAS_SCHEMA: quando a
tabela for publicada em produção, basta apontá-la para SAPIENS e rodar a
pré-carga de novo (`manage.py carregar_metas_planilha`).

Colunas: USU_CODEMP, USU_CODFIL e USU_CODLAN formam a chave primária;
USU_ANOREF (DATE, gravado como 01/01/AAAA), USU_CODFAM (E012FAM.CODFAM),
USU_MESREF (DATE, 01/MM/AAAA), USU_PESMET NUMBER(12,3) e USU_PLCHOL (fica
nulo, como nas outras USU_ que têm essa coluna). Não há sequence nem trigger:
cada célula família × mês é um lançamento, numerado com MAX(USU_CODLAN)+1 com
a tabela travada durante a gravação.

A planilha de projeção ("Projeção de Vendas 2027 por Família") vem por unidade
de orçamento e grupo de produto do PBIX (ex.: MATRIZ / BOBINA MONO IMPRESSO,
ver `commercial_dna.product_group`), que não são as famílias do ERP. Cada grupo
vai para a família que mais faturou nele em 2026 (`GROUP_FAMILIES`); dois
grupos que caem na mesma família são somados.
"""

import datetime
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.cache import cache

from . import commercial_dna as dna
from . import customer_dna as legacy


TABLE_NAME = "USU_TORCMET"
SCHEMA_ENV = "ERP_METAS_SCHEMA"
DEFAULT_SCHEMA = "SENIORERPSIMULACOES"
PRODUCTION_SCHEMA = "SAPIENS"
_IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")

WEIGHT_STEP = Decimal("0.001")
MAX_WEIGHT = Decimal("999999999.999")  # USU_PESMET NUMBER(12,3)
MAX_CODLAN = 999_999_999  # USU_CODLAN NUMBER(9)
LOCK_WAIT_SECONDS = 10
CATALOG_CACHE_SECONDS = 15 * 60

MONTH_LABELS = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
_MONTH_KEYS = tuple(label.upper() for label in MONTH_LABELS)
_MONTH_NAMES = (
    "JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

# Unidade de orçamento da planilha → (empresa, filial) no ERP. A numeração é a
# do PBIX ("FILIAL ORÇ", `commercial_dna.BUDGET_UNITS`): o Adesivado é a
# unidade 5 do orçamento, embora fature pela filial 1.
UNIT_BRANCHES = {
    "MATRIZ": (1, 1),
    "RAFIA": (1, 2),
    "BIG BAG": (1, 3),
    "SERAFINA": (1, 4),
    "ADESIVADO": (1, 5),
    "SAO PAULO": (1, 7),
}

# Grupo de produto do PBIX → família do ERP (E012FAM.CODFAM da empresa 1): a
# família que mais faturou no grupo em 2026 (participação no peso do grupo).
GROUP_FAMILIES = {
    "BOBINA ADESIVADO IMPRESSO": "800038",  # FILME ADESIVADO IMPRESSO (MT), 100%
    "BOBINA ADESIVADO LISO": "800031",  # FILME ADESIVADO LISO (KG), 95% (5% em 800033, M²)
    "BOBINA BI LAMINADO IMPRESSO": "800023",  # FILME IMPRESSO LAMINADO (METROS), 79% (21% em 800008)
    "BOBINA CONVENCIONAL LISO": "800017",  # TECIDO RAFIA LISO CONVENCIONAL (KG), 89% na Ráfia
    "BOBINA LAMINADO IMPRESSO": "800008",  # FILME IMPRESSO LAMINADO (KG), 82% (18% em 800023)
    "BOBINA LAMINADO LISO": "800018",  # TECIDO RAFIA LISO LAMINADO (KG), 100% na Ráfia
    "BOBINA MONO IMPRESSO": "800007",  # FILME IMPRESSO (KG), 62% na Matriz (38% em 800022, METROS), 94% na Serafina
    "BOBINA MONO LISO": "800005",  # FILME LISO (KG), 64% na Matriz, 74% na Serafina
    "FOLHA MONO LISO": "800009",  # FOLHA LISA (MILHEIRO), 55% na Matriz (43% em 800028, KG), 95% na Serafina
    "REFUGO MONO LISO": "800030",  # REFUGO DE RAFIA (KG), 100%
    "SACO BI LAMINADO IMPRESSO": "800004",  # SACO IMPRESSO LAMINADO (MILHEIRO), 100%
    "SACO CONVENCIONAL IMPRESSO": "800015",  # SACO RAFIA IMPRESSO CONVENCIONAL (MILHEIRO), 100%
    "SACO CONVENCIONAL LISO": "800013",  # SACO RAFIA LISO CONVENCIONAL (MILHEIRO), 100%
    "SACO LAMINADO IMPRESSO": "800004",  # SACO IMPRESSO LAMINADO (MILHEIRO), 95% na Serafina
    "SACO LAMINADO LISO": "800014",  # SACO RAFIA LISO LAMINADO (MILHEIRO), 100%
    "SACO MONO IMPRESSO": "800003",  # SACO IMPRESSO (MILHEIRO), 100%
    "SACO MONO LISO": "800001",  # SACO LISO (MILHEIRO), 100%
}

# Onde uma unidade fatura o grupo em outra família.
UNIT_GROUP_FAMILIES = {
    ("BIG BAG", "BOBINA CONVENCIONAL LISO"): "800046",  # TECIDO BAG TUBULAR LISO CONVENCIONAL (KG), 86%
    ("BIG BAG", "SACO CONVENCIONAL IMPRESSO"): "800040",  # SACO BIG BAG IMPRESSO CONVENCIONAL (UND), 99%
    ("BIG BAG", "SACO CONVENCIONAL LISO"): "800041",  # SACO BIG BAG LISO CONVENCIONAL (UND), 100%
    ("RAFIA", "SACO LAMINADO IMPRESSO"): "800016",  # SACO RAFIA IMPRESSO LAMINADO (MILHEIRO), 100%
    ("SERAFINA", "BOBINA LAMINADO LISO"): "800006",  # FILME LISO LAMINADO (KG), 54%
}


class SalesGoalsError(Exception):
    """Erro de uso (planilha fora do layout, esquema ou valor inválido) com mensagem pronta para a tela."""


# ---------------------------------------------------------------------------
# Esquema e valores
# ---------------------------------------------------------------------------

def schema(value=None):
    name = str(value or os.getenv(SCHEMA_ENV) or DEFAULT_SCHEMA).strip().upper()
    if not _IDENTIFIER_RE.match(name):
        raise SalesGoalsError(f"Esquema inválido para a {TABLE_NAME}: {name!r}.")
    return name


def table_ref(schema_name=None):
    return f"{schema(schema_name)}.{TABLE_NAME}"


def is_simulation(schema_name=None):
    return schema(schema_name) != PRODUCTION_SCHEMA


def to_weight(value):
    """Peso em kg com 3 casas (USU_PESMET); vazio vira None. Aceita "1.234,5" e "1234.5"."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise SalesGoalsError(f"Peso inválido: {value!r}.")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(repr(value))
    else:
        text = str(value).strip().replace(" ", "").replace(" ", "")
        if not text:
            return None
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise SalesGoalsError(f"Peso inválido: {value!r}.") from None
    if not number.is_finite():
        raise SalesGoalsError(f"Peso inválido: {value!r}.")
    return number.quantize(WEIGHT_STEP, rounding=ROUND_HALF_UP)


def check_weight(weight, where=""):
    if weight is None:
        return None
    if weight < 0:
        raise SalesGoalsError(f"Peso negativo{where}: {weight}.")
    if weight > MAX_WEIGHT:
        raise SalesGoalsError(f"Peso acima do limite da coluna (999.999.999,999 kg){where}: {weight}.")
    return weight


def weight_text(weight):
    """Peso para JSON: texto com 3 casas, sem perder precisão; vazio vira None."""
    return None if weight is None else f"{weight:.3f}"


def format_kg(value, decimals=0):
    """Peso por extenso em kg, no padrão brasileiro (6.849.228 kg)."""
    text = f"{Decimal(value or 0):,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".") + " kg"


def _same(left, right):
    """Mesmo peso, contando vazio e zero como "sem meta"."""
    return (left or Decimal(0)) == (right or Decimal(0))


def _norm(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.upper().split())


# ---------------------------------------------------------------------------
# Planilha de projeção
# ---------------------------------------------------------------------------

@dataclass
class ProjectionRow:
    unit: str
    group: str
    months: list  # 12 Decimal (kg), como estão na planilha

    @property
    def total(self):
        return sum(self.months, Decimal(0))


@dataclass
class Projection:
    sheet: str
    layout: str  # "tabela" (Unidade | Família | meses) ou "visao" (unidade como título, famílias abaixo)
    year: int | None
    rows: list

    @property
    def total(self):
        return sum((row.total for row in self.rows), Decimal(0))


def _cell(row, index):
    return row[index] if row is not None and index is not None and index < len(row) else None


def _sheet_number(value, where):
    """Número de uma célula: None continua None (célula vazia ou fórmula sem valor salvo)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise SalesGoalsError(f"Valor não numérico em {where}: {value!r}.")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    text = str(value).strip().replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise SalesGoalsError(f"Valor não numérico em {where}: {value!r}.") from None


def _month_columns(labels):
    """Índices das colunas Jan..Dez (abreviadas ou por extenso), ou None se faltar algum mês."""
    positions = []
    for abbreviation, full in zip(_MONTH_KEYS, _MONTH_NAMES):
        index = next((i for i, label in enumerate(labels) if label in (abbreviation, full)), None)
        if index is None:
            return None
        positions.append(index)
    return positions


def _year_from(title, rows):
    match = _YEAR_RE.search(title or "")
    if match:
        return int(match.group(1))
    for row in rows[:12]:
        for value in row or ():
            match = _YEAR_RE.search(str(value or ""))
            if match:
                return int(match.group(1))
    return None


def _finish(title, layout, header_rows, parsed, filled):
    if not parsed:
        raise SalesGoalsError(f"A aba {title!r} tem o cabeçalho de metas, mas nenhuma linha de família abaixo dele.")
    if not filled:
        raise SalesGoalsError(
            f"A aba {title!r} não tem valores calculados nos meses (fórmulas sem resultado salvo). "
            "Abra a planilha no Excel, salve e tente de novo."
        )
    return Projection(sheet=title, layout=layout, year=_year_from(title, header_rows), rows=parsed)


def _read_table_layout(title, rows):
    """Aba com cabeçalho Unidade | Família | Jan..Dez (a aba "Projeção 2027", oculta)."""
    for index, row in enumerate(rows):
        labels = [_norm(value) for value in row or ()]
        if "UNIDADE" not in labels or "FAMILIA" not in labels:
            continue
        months = _month_columns(labels)
        if not months:
            continue
        unit_col, group_col = labels.index("UNIDADE"), labels.index("FAMILIA")
        parsed, filled = [], False
        for offset, data in enumerate(rows[index + 1:], start=index + 2):
            unit, group = _norm(_cell(data, unit_col)), _norm(_cell(data, group_col))
            if unit == "TOTAL" or group == "TOTAL":
                break
            if not unit or not group:
                continue
            values = [_sheet_number(_cell(data, col), f"{title}!linha {offset}") for col in months]
            filled = filled or any(value is not None for value in values)
            parsed.append(ProjectionRow(unit, group, [value or Decimal(0) for value in values]))
        return _finish(title, "tabela", rows[: index + 1], parsed, filled)
    return None


def _read_grouped_layout(title, rows):
    """Aba "Visão Mensal": a unidade é uma linha-título e as famílias vêm logo abaixo dela."""
    for index, row in enumerate(rows):
        labels = [_norm(value) for value in row or ()]
        if not labels or "UNIDADE" not in labels[0] or "FAMILIA" not in labels[0]:
            continue
        months = _month_columns(labels)
        if not months:
            continue
        parsed, filled, unit = [], False, None
        for offset, data in enumerate(rows[index + 1:], start=index + 2):
            label = _norm(_cell(data, 0))
            if not label:
                continue
            if label == "TOTAL":
                break
            if label in UNIT_BRANCHES:
                unit = label
                continue
            if unit is None:
                continue
            values = [_sheet_number(_cell(data, col), f"{title}!linha {offset}") for col in months]
            filled = filled or any(value is not None for value in values)
            parsed.append(ProjectionRow(unit, label, [value or Decimal(0) for value in values]))
        return _finish(title, "visao", rows[: index + 1], parsed, filled)
    return None


def read_projection(source):
    """Lê a planilha de projeção (caminho ou arquivo aberto). Prefere a aba em tabela à "Visão Mensal"."""
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - openpyxl está no requirements
        raise SalesGoalsError("openpyxl não está instalado.") from exc
    try:
        workbook = openpyxl.load_workbook(source, data_only=True, read_only=True)
    except Exception as exc:
        raise SalesGoalsError(f"Não foi possível abrir a planilha: {exc}") from exc
    try:
        sheets = [(sheet.title, [tuple(row) for row in sheet.iter_rows(values_only=True)]) for sheet in workbook.worksheets]
    finally:
        workbook.close()
    for reader in (_read_table_layout, _read_grouped_layout):
        for title, rows in sheets:
            projection = reader(title, rows)
            if projection:
                return projection
    raise SalesGoalsError(
        "Não encontrei na planilha uma tabela com Unidade, Família e os 12 meses "
        "(como a aba \"Projeção 2027\") nem a \"Visão Mensal\" por unidade."
    )


# ---------------------------------------------------------------------------
# Grupo da planilha → família do ERP
# ---------------------------------------------------------------------------

def family_for(unit, group):
    return UNIT_GROUP_FAMILIES.get((unit, group)) or GROUP_FAMILIES.get(group)


@dataclass
class MappedProjection:
    year: int
    cells: dict  # (empresa, filial, família) -> [12 Decimal com 3 casas]
    sources: dict  # (empresa, filial, família) -> grupos da planilha somados nela
    unmapped: list  # (unidade, grupo, total) sem filial ou família conhecida
    sheet_total: Decimal

    @property
    def blocking(self):
        """Linhas sem mapeamento que têm meta: impedem a carga (as zeradas só são avisadas)."""
        return [item for item in self.unmapped if item[2]]

    @property
    def total(self):
        return sum((sum(months, Decimal(0)) for months in self.cells.values()), Decimal(0))

    def goal_cells(self):
        return sum(1 for months in self.cells.values() for value in months if value)

    def branch_totals(self):
        totals = defaultdict(Decimal)
        for (company, branch, _family), months in self.cells.items():
            totals[(company, branch)] += sum(months, Decimal(0))
        return dict(sorted(totals.items()))

    def month_totals(self):
        return [sum((months[index] for months in self.cells.values()), Decimal(0)) for index in range(12)]


def map_projection(projection, year=None):
    year = year or projection.year
    if not year:
        raise SalesGoalsError("Não achei o ano na planilha; informe --ano.")
    raw = defaultdict(lambda: [Decimal(0)] * 12)
    sources = defaultdict(list)
    unmapped = []
    for row in projection.rows:
        branch = UNIT_BRANCHES.get(row.unit)
        family = family_for(row.unit, row.group)
        if branch is None or family is None:
            unmapped.append((row.unit, row.group, row.total))
            continue
        key = (*branch, family)
        for index, value in enumerate(row.months):
            raw[key][index] += value
        sources[key].append(row.group)
    cells = {
        key: [value.quantize(WEIGHT_STEP, rounding=ROUND_HALF_UP) for value in months]
        for key, months in sorted(raw.items())
    }
    return MappedProjection(year=year, cells=cells, sources=dict(sources), unmapped=unmapped, sheet_total=projection.total)


# ---------------------------------------------------------------------------
# Plano de gravação (puro, sem Oracle)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Change:
    family: str
    month: int
    value: Decimal | None  # None ou zero = sem meta (apaga a célula)
    original: Decimal | None = None  # o que a tela mostrava; só conta quando há conferência


@dataclass
class Operation:
    kind: str  # "insert", "update" ou "delete"
    company: int
    branch: int
    family: str
    month: int
    value: Decimal | None = None
    previous: Decimal | None = None
    codlan: int | None = None


@dataclass
class Plan:
    operations: list = field(default_factory=list)
    unchanged: int = 0
    conflicts: list = field(default_factory=list)
    applied: bool = False
    rep_operations: list = field(default_factory=list)  # peso dos representantes refeito (USU_TORCGER)

    def counts(self):
        kinds = [operation.kind for operation in self.operations]
        return {
            "inserir": kinds.count("insert"),
            "atualizar": kinds.count("update"),
            "apagar": kinds.count("delete"),
            "iguais": self.unchanged,
        }


def plan_changes(current, scoped_changes, check=True):
    """O que gravar para cada célula ficar com o valor pedido.

    `current`: {(empresa, filial): {(família, mês): [(lançamento, peso), ...]}}, as
    linhas que já estão na tabela. `scoped_changes`: {(empresa, filial): [Change]}.
    Com `check`, a célula cujo valor atual difere do que a tela mostrava (`original`)
    vira conflito: alguém gravou depois que a tela abriu. Uma célula com mais de um
    lançamento é somada; ao gravar, fica um lançamento só.
    """
    plan = Plan()
    for (company, branch), changes in scoped_changes.items():
        existing = current.get((company, branch), {})
        unique = {}
        for change in changes:
            unique[(change.family, change.month)] = change
        for (family, month), change in unique.items():
            rows = sorted(existing.get((family, month), []))
            current_total = sum((weight or Decimal(0) for _, weight in rows), Decimal(0)) if rows else None
            target = change.value or None
            if check and not _same(current_total, change.original):
                plan.conflicts.append({
                    "empresa": company, "filial": branch, "familia": family, "mes": month,
                    "tela": weight_text(change.original), "atual": weight_text(current_total),
                })
                continue
            base = {"company": company, "branch": branch, "family": family, "month": month}
            if target is None:
                if not rows:
                    plan.unchanged += 1
                for codlan, weight in rows:
                    plan.operations.append(Operation("delete", previous=weight, codlan=codlan, **base))
                continue
            if not rows:
                plan.operations.append(Operation("insert", value=target, **base))
                continue
            first_codlan, first_weight = rows[0]
            if len(rows) == 1 and first_weight == target:
                plan.unchanged += 1
                continue
            if first_weight != target:
                plan.operations.append(Operation("update", value=target, previous=first_weight, codlan=first_codlan, **base))
            for codlan, weight in rows[1:]:
                plan.operations.append(Operation("delete", previous=weight, codlan=codlan, **base))
    return plan


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

SCOPE_SQL = """
SELECT A.USU_CODLAN, TRIM(A.USU_CODFAM), EXTRACT(MONTH FROM A.USU_MESREF), A.USU_PESMET
FROM {table} A
WHERE A.USU_CODEMP = :empresa
  AND A.USU_CODFIL = :filial
  AND NVL(A.USU_ANOREF, A.USU_MESREF) >= :inicio
  AND NVL(A.USU_ANOREF, A.USU_MESREF) < :fim
ORDER BY A.USU_CODLAN
"""

SUMMARY_SQL = """
SELECT A.USU_CODEMP, A.USU_CODFIL, COUNT(*), COUNT(DISTINCT A.USU_CODFAM), SUM(A.USU_PESMET)
FROM {table} A
WHERE NVL(A.USU_ANOREF, A.USU_MESREF) >= :inicio
  AND NVL(A.USU_ANOREF, A.USU_MESREF) < :fim
GROUP BY A.USU_CODEMP, A.USU_CODFIL
ORDER BY A.USU_CODEMP, A.USU_CODFIL
"""

YEARS_SQL = """
SELECT DISTINCT EXTRACT(YEAR FROM NVL(A.USU_ANOREF, A.USU_MESREF))
FROM {table} A
WHERE NVL(A.USU_ANOREF, A.USU_MESREF) IS NOT NULL
"""

MAX_CODLAN_SQL = "SELECT NVL(MAX(USU_CODLAN), 0) FROM {table}"

INSERT_SQL = """
INSERT INTO {table} (USU_CODEMP, USU_CODFIL, USU_CODLAN, USU_ANOREF, USU_CODFAM, USU_MESREF, USU_PESMET)
VALUES (:empresa, :filial, :lancamento, :ano, :familia, :mes, :peso)
"""

UPDATE_SQL = """
UPDATE {table} SET USU_PESMET = :peso
WHERE USU_CODEMP = :empresa AND USU_CODFIL = :filial AND USU_CODLAN = :lancamento
"""

DELETE_SQL = """
DELETE FROM {table}
WHERE USU_CODEMP = :empresa AND USU_CODFIL = :filial AND USU_CODLAN = :lancamento
"""

CATALOG_SQL = {
    "empresas": ("SELECT CODEMP AS CODIGO, NOMEMP AS NOME FROM E070EMP ORDER BY CODEMP", {}),
    "filiais": ("SELECT CODEMP AS EMPRESA, CODFIL AS CODIGO, SIGFIL AS SIGLA, NOMFIL AS NOME FROM E070FIL ORDER BY CODEMP, CODFIL", {}),
    "familias": ("SELECT CODEMP AS EMPRESA, CODFAM AS CODIGO, DESFAM AS NOME FROM E012FAM ORDER BY CODEMP, CODFAM", {}),
}


def _connect():
    """Conexão do ERP Senior (mesmo login dos BIs: `sapiens`, que enxerga o esquema de simulação)."""
    return legacy._oracle_connection_safe()


def _year_bounds(year):
    return datetime.datetime(year, 1, 1), datetime.datetime(year + 1, 1, 1)


def _scope_rows(cursor, table, scopes, year):
    """Linhas atuais de cada (empresa, filial) no ano, por (família, mês); conta as que não têm família ou mês."""
    start, end = _year_bounds(year)
    current, outside = {}, {}
    for company, branch in scopes:
        cursor.execute(SCOPE_SQL.format(table=table), {"empresa": company, "filial": branch, "inicio": start, "fim": end})
        cells, loose = defaultdict(list), 0
        for codlan, family, month, weight in cursor.fetchall():
            if not family or month is None:
                loose += 1
                continue
            cells[(str(family).strip(), int(month))].append((int(codlan), to_weight(weight) or Decimal(0)))
        current[(company, branch)] = dict(cells)
        outside[(company, branch)] = loose
    return current, outside


def _execute_operations(cursor, table, year, operations):
    cursor.execute(MAX_CODLAN_SQL.format(table=table))
    next_codlan = int(cursor.fetchone()[0] or 0) + 1
    year_date, _ = _year_bounds(year)
    for operation in operations:
        params = {"empresa": operation.company, "filial": operation.branch}
        if operation.kind == "insert":
            if next_codlan > MAX_CODLAN:
                raise SalesGoalsError("O número de lançamento passou do limite da coluna USU_CODLAN.")
            operation.codlan = next_codlan
            next_codlan += 1
            cursor.execute(INSERT_SQL.format(table=table), {
                **params, "lancamento": operation.codlan, "ano": year_date, "familia": operation.family,
                "mes": datetime.datetime(year, operation.month, 1), "peso": operation.value,
            })
            continue
        if operation.kind == "update":
            cursor.execute(UPDATE_SQL.format(table=table), {**params, "lancamento": operation.codlan, "peso": operation.value})
        else:
            cursor.execute(DELETE_SQL.format(table=table), {**params, "lancamento": operation.codlan})
        if cursor.rowcount != 1:
            raise SalesGoalsError(f"O lançamento {operation.codlan} mudou durante a gravação; nada foi gravado.")


def apply_changes(scoped_changes, year, *, check=True, dry_run=False, schema_name=None, connect=None):
    """Planeja e (sem `dry_run`) grava numa transação só, com a tabela travada.

    Com conflito nada é gravado: o plano volta com `conflicts` preenchido.
    """
    table = table_ref(schema_name)
    connection = (connect or _connect)()
    try:
        cursor = connection.cursor()
        try:
            if dry_run:
                cursor.execute("SET TRANSACTION READ ONLY")
            else:
                cursor.execute(f"LOCK TABLE {table} IN EXCLUSIVE MODE WAIT {LOCK_WAIT_SECONDS}")
            current, _outside = _scope_rows(cursor, table, list(scoped_changes), year)
            plan = plan_changes(current, scoped_changes, check=check)
            if dry_run or plan.conflicts or not plan.operations:
                connection.rollback()
                return plan
            _execute_operations(cursor, table, year, plan.operations)
            # O peso dos representantes é % × meta: muda junto com a meta, na mesma transação.
            from . import sales_goals_reps

            affected = {(operation.company, operation.branch, operation.family) for operation in plan.operations}
            plan.rep_operations = sales_goals_reps.recompute_families(cursor, year, affected, schema_name=schema_name)
            connection.commit()
            plan.applied = True
            return plan
        except BaseException:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            cursor.close()
    finally:
        connection.close()


def save_grid(company, branch, year, changes, *, schema_name=None, connect=None):
    """Grava as células alteradas na tela, conferindo se ninguém gravou nelas desde que a tela abriu."""
    return apply_changes({(company, branch): list(changes)}, year, check=True, schema_name=schema_name, connect=connect)


def preload_changes(mapped):
    """Células da planilha com meta, por (empresa, filial). As zeradas ficam de fora: a carga cria e
    atualiza, nunca apaga o que alguém digitou na tela."""
    scoped = defaultdict(list)
    for (company, branch, family), months in mapped.cells.items():
        for index, value in enumerate(months):
            if value:
                scoped[(company, branch)].append(Change(family, index + 1, value))
    return dict(scoped)


def preload(mapped, *, dry_run=True, schema_name=None, connect=None):
    return apply_changes(preload_changes(mapped), mapped.year, check=False, dry_run=dry_run, schema_name=schema_name, connect=connect)


def verify(mapped, *, schema_name=None, connect=None):
    """Compara a tabela com a planilha, célula a célula. Devolve (células conferidas, divergências)."""
    table = table_ref(schema_name)
    expected = preload_changes(mapped)
    connection = (connect or _connect)()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            current, _outside = _scope_rows(cursor, table, list(expected), mapped.year)
        finally:
            cursor.close()
            connection.rollback()
    finally:
        connection.close()
    checked, differences = 0, []
    for (company, branch), changes in expected.items():
        cells = current.get((company, branch), {})
        for change in changes:
            rows = cells.get((change.family, change.month), [])
            found = sum((weight for _, weight in rows), Decimal(0)) if rows else None
            checked += 1
            if not _same(found, change.value) or len(rows) != 1:
                differences.append({
                    "empresa": company, "filial": branch, "familia": change.family, "mes": change.month,
                    "planilha": weight_text(change.value), "tabela": weight_text(found), "lancamentos": len(rows),
                })
    return checked, differences


def load_grid(company, branch, year=None, *, schema_name=None, connect=None, today=None):
    """Grade da tela: famílias × meses da filial no ano, o resumo do ano por filial e os anos com meta.

    Sem `year`, abre o último ano que tem meta (ou o ano que vem, com a tabela vazia).
    """
    today = today or datetime.date.today()
    table = table_ref(schema_name)
    connection = (connect or _connect)()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(YEARS_SQL.format(table=table))
            years = sorted({int(row[0]) for row in cursor.fetchall() if row and row[0] is not None})
            year = year or (years[-1] if years else today.year + 1)
            current, outside = _scope_rows(cursor, table, [(company, branch)], year)
            start, end = _year_bounds(year)
            cursor.execute(SUMMARY_SQL.format(table=table), {"inicio": start, "fim": end})
            summary = [
                {"empresa": int(row[0]), "filial": int(row[1]), "linhas": int(row[2] or 0),
                 "familias": int(row[3] or 0), "peso": to_weight(row[4]) or Decimal(0)}
                for row in cursor.fetchall()
            ]
        finally:
            cursor.close()
            connection.rollback()
    finally:
        connection.close()
    cells = current.get((company, branch), {})
    rows = []
    for family in sorted({family for family, _ in cells}):
        months, launches = [], []
        for month in range(1, 13):
            entries = cells.get((family, month), [])
            months.append(sum((weight for _, weight in entries), Decimal(0)) if entries else None)
            launches.append([codlan for codlan, _ in entries])
        rows.append({"family": family, "months": months, "launches": launches})
    return {
        "year": year,
        "years": years,
        "rows": rows,
        "outside": outside.get((company, branch), 0),
        "summary": summary,
    }


def catalog():
    """Empresas, filiais e famílias do ERP (esquema de produção), guardadas 15 minutos em cache."""
    cached = cache.get("sales_goals:catalog")
    if cached is not None:
        return cached
    result = dna._run(CATALOG_SQL)
    companies = [{"code": int(row["codigo"]), "name": (row["nome"] or "").strip()} for row in result["empresas"]]
    branches = defaultdict(list)
    for row in result["filiais"]:
        company, code = int(row["empresa"]), int(row["codigo"])
        label = f"{code} · {(row['sigla'] or row['nome'] or '').strip()}"
        unit = dna.BUDGET_UNITS.get(code) if company == 1 else None
        if unit and _norm(unit) not in _norm(label):
            label = f"{label} — {unit}"
        branches[company].append({"code": code, "label": label})
    families = defaultdict(list)
    for row in result["familias"]:
        families[int(row["empresa"])].append({"code": str(row["codigo"]).strip(), "name": (row["nome"] or "").strip()})
    data = {"companies": companies, "branches": dict(branches), "families": dict(families)}
    cache.set("sales_goals:catalog", data, CATALOG_CACHE_SECONDS)
    return data
