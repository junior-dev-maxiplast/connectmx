"""
Metas de venda por representante (USU_TORCGER): o % de cada representante na meta de
cada família (USU_TORCMET, ver `sales_goals.py`) e o peso que cabe a ele, mês a mês.

USU_TORCGER (criada com a USU_TORCMET no ambiente de simulação do Senior, em
19/11/2026): USU_CODEMP, USU_CODFIL, USU_CODLAN, USU_ANOREL (01/01/AAAA), USU_CODREP,
USU_CODFAM, USU_MESREL (01/MM/AAAA), USU_PERMET NUMBER(10,4) (o % da meta da família,
de 0 a 100) e USU_PESMET NUMBER(12,3) (meta da família no mês × % / 100). A tabela não
tem chave primária; cada linha representante × família × mês recebe o próprio
lançamento, MAX(USU_CODLAN)+1, como na USU_TORCMET.

O % sugerido é a participação do representante no peso faturado no período-base (por
padrão, o ano anterior ao da meta), dentro da mesma unidade e do mesmo grupo de produto
que a família representa na meta. Ex.: na Matriz a família 800007 é a meta de BOBINA MONO
IMPRESSO, então conta também o que saiu como 800022 (FILME IMPRESSO em metros). Os
códigos que o PBIX consolida num representante só (`commercial_dna.REPRESENTATIVE_ALIASES`)
somam no código principal. O % tem 4 casas e fecha 100% por família (maior resto); o peso
fecha a meta do mês quando a soma dos % é 100.

Quando a meta de uma família muda (tela ou pré-carga), o peso dos representantes dela é
refeito na mesma transação, mantendo o %.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.cache import cache

from . import commercial_dna as dna
from . import sales_goals
from .sales_goals import SalesGoalsError

GER_TABLE_NAME = "USU_TORCGER"
PERCENT_STEP = Decimal("0.0001")
HUNDRED = Decimal(100)
PERCENT_UNITS = 1_000_000  # 100,0000% em passos de 0,0001
BILLING_CACHE_SECONDS = 10 * 60
REPS_CACHE_SECONDS = 15 * 60
MONTHS = range(1, 13)


def ger_table_ref(schema_name=None):
    return f"{sales_goals.schema(schema_name)}.{GER_TABLE_NAME}"


def to_percent(value):
    """% com 4 casas (USU_PERMET); vazio vira None. Aceita "12,5" e "12.5"."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise SalesGoalsError(f"Percentual inválido: {value!r}.")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(repr(value))
    else:
        text = str(value).strip().replace(" ", "").replace("%", "")
        if not text:
            return None
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise SalesGoalsError(f"Percentual inválido: {value!r}.") from None
    if not number.is_finite():
        raise SalesGoalsError(f"Percentual inválido: {value!r}.")
    return number.quantize(PERCENT_STEP, rounding=ROUND_HALF_UP)


def check_percent(percent, where=""):
    if percent is None:
        return None
    if percent < 0 or percent > HUNDRED:
        raise SalesGoalsError(f"Percentual fora de 0 a 100{where}: {percent}.")
    return percent


def percent_text(percent):
    return None if percent is None else f"{percent:.4f}"


def split_percents(weights):
    """{chave: peso} → {chave: % com 4 casas}, somando exatamente 100 (maior resto). Peso ≤ 0 fica de fora."""
    positive = {key: Decimal(value) for key, value in weights.items() if value and Decimal(value) > 0}
    total = sum(positive.values(), Decimal(0))
    if total <= 0:
        return {}
    raw = {key: value * PERCENT_UNITS / total for key, value in positive.items()}
    units = {key: int(value.to_integral_value(rounding=ROUND_FLOOR)) for key, value in raw.items()}
    rest = PERCENT_UNITS - sum(units.values())
    for key in sorted(positive, key=lambda key: (-(raw[key] - units[key]), -positive[key], str(key)))[:rest]:
        units[key] += 1
    return {key: (Decimal(value) / 10000).quantize(PERCENT_STEP) for key, value in units.items() if value > 0}


def split_weight(meta, percents):
    """{chave: %} → {chave: kg} da meta do mês. Com os % somando 100 o total fecha a meta exata."""
    meta = meta or Decimal(0)
    if not percents:
        return {}
    if meta > 0 and sum(percents.values(), Decimal(0)) == HUNDRED:
        grams = int((meta * 1000).to_integral_value(rounding=ROUND_HALF_UP))
        raw = {key: Decimal(grams) * percent / HUNDRED for key, percent in percents.items()}
        floors = {key: int(value.to_integral_value(rounding=ROUND_FLOOR)) for key, value in raw.items()}
        rest = grams - sum(floors.values())
        for key in sorted(percents, key=lambda key: (-(raw[key] - floors[key]), -percents[key], str(key)))[:rest]:
            floors[key] += 1
        return {key: (Decimal(value) / 1000).quantize(sales_goals.WEIGHT_STEP) for key, value in floors.items()}
    return {
        key: (meta * percent / HUNDRED).quantize(sales_goals.WEIGHT_STEP, rounding=ROUND_HALF_UP)
        for key, percent in percents.items()
    }


# ---------------------------------------------------------------------------
# Base do %: peso faturado por representante
# ---------------------------------------------------------------------------

BILLING_BY_REP_SQL = """
SELECT N.CODFIL AS FILIAL, PI.USU_FILPRD AS FILIAL_PRODUCAO, I.CODPRO AS PRODUTO,
       I.CODFAM AS CODFAM, F.DESFAM AS FAMILIA, I.CPLIPV AS COMPLEMENTO, N.CODREP AS REPRESENTANTE,
       SUM(NVL(I.PESLIQ, 0) * (1 - CASE WHEN NVL(I.QTDFAT, 0) > 0
           THEN LEAST(GREATEST(NVL(I.QTDDEV, 0) / I.QTDFAT, 0), 1) ELSE 0 END)) AS PESO
FROM E140NFV N
JOIN E140IPV I ON I.CODEMP = N.CODEMP AND I.CODFIL = N.CODFIL AND I.CODSNF = N.CODSNF AND I.NUMNFV = N.NUMNFV
JOIN E001TNS T ON T.CODEMP = I.CODEMP AND T.CODTNS = I.TNSPRO AND T.VENFAT = 'S'
LEFT JOIN E012FAM F ON F.CODEMP = I.CODEMP AND F.CODFAM = I.CODFAM
LEFT JOIN E120IPD PI ON PI.CODEMP = I.CODEMP AND PI.CODFIL = I.CODFIL AND PI.NUMPED = I.NUMPED AND PI.SEQIPD = I.SEQIPD
WHERE N.CODEMP = :empresa AND N.SITNFV = '2' AND N.CODCLI NOT IN (1001, 1002, 1004)
  AND N.DATEMI >= :inicio AND N.DATEMI < :fim
GROUP BY N.CODFIL, PI.USU_FILPRD, I.CODPRO, I.CODFAM, F.DESFAM, I.CPLIPV, N.CODREP
"""

REPS_SQL = "SELECT CODREP AS CODIGO, NOMREP AS NOME, APEREP AS APELIDO, SITREP AS SITUACAO FROM E090REP ORDER BY CODREP"


def base_options(year, today=None):
    """Períodos-base para o %: o ano anterior (padrão), o retrasado e os dois juntos."""
    today = today or datetime.date.today()
    previous, before = year - 1, year - 2

    def label(first, last):
        text = f"{first}" if first == last else f"{first} e {last}"
        return f"{text} (até hoje)" if last >= today.year else text

    return [
        {"key": f"{previous}", "label": label(previous, previous)},
        {"key": f"{before}-{previous}", "label": label(before, previous)},
        {"key": f"{before}", "label": label(before, before)},
    ]


def parse_base(base, year):
    """"2026" ou "2025-2026" → (primeiro ano, último ano). Sem base, o ano anterior ao da meta."""
    text = str(base or "").strip()
    if not text:
        return year - 1, year - 1
    parts = text.split("-")
    try:
        first, last = int(parts[0]), int(parts[-1])
    except ValueError:
        raise SalesGoalsError(f"Período-base inválido: {base!r}.") from None
    if len(parts) > 2 or first > last or first < 2000 or last >= year + 1:
        raise SalesGoalsError(f"Período-base inválido: {base!r}.")
    return first, last


def billing_index(rows, company):
    """(unidade, grupo, família) → {representante principal: kg faturado}."""
    index = defaultdict(lambda: defaultdict(Decimal))
    for row in rows:
        family_name = (row.get("familia") or "").strip()
        group = dna.product_group(family_name, row.get("complemento"))
        if company == 1:
            unit = dna.budget_unit(family_name, group, row.get("filial_producao"), row.get("filial"), row.get("produto"))
        else:
            unit = dna._int(row.get("filial"))
        rep = dna.primary_rep(row.get("representante"))
        if rep is None or unit is None:
            continue
        index[(unit, group, str(row.get("codfam") or "").strip())][rep] += Decimal(str(row.get("peso") or 0))
    return {key: dict(value) for key, value in index.items()}


def load_billing_index(company, first_year, last_year):
    key = f"sales_goals:billing:{company}:{first_year}:{last_year}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    params = {"empresa": company, "inicio": datetime.datetime(first_year, 1, 1), "fim": datetime.datetime(last_year + 1, 1, 1)}
    index = billing_index(dna._run({"faturamento": (BILLING_BY_REP_SQL, params)})["faturamento"], company)
    cache.set(key, index, BILLING_CACHE_SECONDS)
    return index


def groups_for(company, branch, family):
    """Grupos do orçamento que a família representa na meta desta unidade (vazio se ela não veio da planilha)."""
    if company != 1:
        return set()
    units = [name for name, code in sales_goals.UNIT_BRANCHES.items() if code == (company, branch)]
    return {
        group
        for unit in units
        for group in sales_goals.GROUP_FAMILIES
        if sales_goals.family_for(unit, group) == family
    }


def family_weights(index, company, branch, family):
    """Peso faturado por representante na base da família: os grupos dela na unidade, ou a própria família."""
    groups = groups_for(company, branch, family)
    totals = defaultdict(Decimal)
    for (unit, group, codfam), by_rep in index.items():
        if unit != branch:
            continue
        if (group in groups) if groups else (codfam == family):
            for rep, weight in by_rep.items():
                totals[rep] += weight
    return {rep: weight for rep, weight in totals.items() if weight > 0}


def representatives():
    """Cadastro de representantes (E090REP) com o nome de exibição do DNA. Cache de 15 minutos."""
    cached = cache.get("sales_goals:reps")
    if cached is not None:
        return cached
    result = {}
    for row in dna._run({"representantes": (REPS_SQL, {})})["representantes"]:
        code = int(row["codigo"])
        name = (row.get("nome") or "").strip()
        nickname = (row.get("apelido") or "").strip()
        result[code] = {
            "code": code,
            "name": dna.REPRESENTATIVE_DISPLAY_NAMES.get(code) or nickname or name or f"Representante {code}",
            "full_name": name,
            "active": (row.get("situacao") or "A").strip().upper() == "A",
        }
    cache.set("sales_goals:reps", result, REPS_CACHE_SECONDS)
    return result


def suggested_shares(company, branch, families, base_years, *, exclude=(), index=None):
    """{família: {"percents": {rep: %}, "weights": {rep: kg base}, "groups": [...], "total": kg}} pelo faturamento."""
    index = index if index is not None else load_billing_index(company, *base_years)
    excluded = set(exclude)
    result = {}
    for family in families:
        weights = {rep: weight for rep, weight in family_weights(index, company, branch, family).items() if rep not in excluded}
        result[family] = {
            "percents": split_percents(weights),
            "weights": weights,
            "groups": sorted(groups_for(company, branch, family)),
            "total": sum(weights.values(), Decimal(0)),
        }
    return result


# ---------------------------------------------------------------------------
# USU_TORCGER: leitura, plano e gravação
# ---------------------------------------------------------------------------

GER_SCOPE_SQL = """
SELECT G.USU_CODLAN, TRIM(G.USU_CODFAM), G.USU_CODREP, EXTRACT(MONTH FROM G.USU_MESREL), G.USU_PERMET, G.USU_PESMET
FROM {table} G
WHERE G.USU_CODEMP = :empresa
  AND G.USU_CODFIL = :filial
  AND NVL(G.USU_ANOREL, G.USU_MESREL) >= :inicio
  AND NVL(G.USU_ANOREL, G.USU_MESREL) < :fim
ORDER BY G.USU_CODLAN
"""

GER_MAX_SQL = "SELECT NVL(MAX(USU_CODLAN), 0) FROM {table}"

GER_INSERT_SQL = """
INSERT INTO {table} (USU_CODEMP, USU_CODFIL, USU_CODLAN, USU_ANOREL, USU_CODREP, USU_CODFAM, USU_MESREL, USU_PERMET, USU_PESMET)
VALUES (:empresa, :filial, :lancamento, :ano, :representante, :familia, :mes, :percentual, :peso)
"""

GER_UPDATE_SQL = """
UPDATE {table} SET USU_PERMET = :percentual, USU_PESMET = :peso
WHERE USU_CODEMP = :empresa AND USU_CODFIL = :filial AND USU_CODLAN = :lancamento AND USU_CODREP = :representante
"""

GER_DELETE_SQL = """
DELETE FROM {table}
WHERE USU_CODEMP = :empresa AND USU_CODFIL = :filial AND USU_CODLAN = :lancamento AND USU_CODREP = :representante
"""


@dataclass(frozen=True)
class RepChange:
    family: str
    rep: int
    percent: Decimal | None  # None ou zero = tira o representante da família (apaga os 12 meses)
    original: Decimal | None = None  # o % que a tela mostrava


@dataclass
class RepOperation:
    kind: str  # "insert", "update" ou "delete"
    company: int
    branch: int
    family: str
    rep: int
    month: int
    percent: Decimal | None = None
    weight: Decimal | None = None
    previous_percent: Decimal | None = None
    previous_weight: Decimal | None = None
    codlan: int | None = None


@dataclass
class RepPlan:
    operations: list = field(default_factory=list)
    unchanged: int = 0
    conflicts: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # famílias que já tinham representantes (pré-carga sem --recalcular)
    shares: dict = field(default_factory=dict)  # (empresa, filial, família) -> {"percents", "weights", ...} da pré-carga
    applied: bool = False

    def counts(self):
        kinds = [operation.kind for operation in self.operations]
        return {
            "inserir": kinds.count("insert"),
            "atualizar": kinds.count("update"),
            "apagar": kinds.count("delete"),
            "iguais": self.unchanged,
        }


def _rep_rows(cursor, table, company, branch, year):
    """{família: {representante: {mês: [(lançamento, %, kg)]}}} da filial no ano."""
    start, end = sales_goals._year_bounds(year)
    cursor.execute(GER_SCOPE_SQL.format(table=table), {"empresa": company, "filial": branch, "inicio": start, "fim": end})
    rows = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for codlan, family, rep, month, percent, weight in cursor.fetchall():
        if not family or month is None or rep is None:
            continue
        rows[str(family).strip()][int(rep)][int(month)].append((
            int(codlan),
            None if percent is None else to_percent(percent),
            sales_goals.to_weight(weight) or Decimal(0),
        ))
    return rows


def _meta_months(current, family):
    """Meta da família por mês (12 valores; None = sem meta), a partir das linhas da USU_TORCMET."""
    return [
        sum((weight for _, weight in current.get((family, month), [])), Decimal(0)) if current.get((family, month)) else None
        for month in MONTHS
    ]


def shown_percent(months):
    """O % que a tela mostra para o representante na família: o do primeiro mês que tem %."""
    for month in MONTHS:
        for _codlan, percent, _weight in sorted(months.get(month, [])):
            if percent is not None:
                return percent
    return None


def varies(months):
    values = {percent for month in MONTHS for _codlan, percent, _weight in months.get(month, []) if percent is not None}
    return len(values) > 1


def plan_family(company, branch, family, meta, existing, desired=None):
    """Linhas a gravar para os representantes de uma família.

    `existing`: {rep: {mês: [(lançamento, %, kg)]}}. `desired`: {rep: % ou None} só dos
    representantes alterados (None/zero tira o representante); os demais mantêm o %
    gravado em cada mês. O peso de todos é refeito com a meta do mês (`meta`, 12 valores).
    """
    desired = desired or {}
    targets = {}
    for rep in set(existing) | set(desired):
        if rep in desired:
            percent = desired[rep]
            targets[rep] = {month: percent for month in MONTHS} if percent else {}
        else:
            targets[rep] = {
                month: shown
                for month, rows in existing[rep].items()
                if rows and (shown := sorted(rows)[0][1]) is not None
            }
    operations, unchanged = [], 0
    base = {"company": company, "branch": branch, "family": family}
    for month in MONTHS:
        percents = {rep: target[month] for rep, target in targets.items() if month in target}
        weights = split_weight(meta[month - 1], percents)
        for rep in sorted(targets):
            rows = sorted(existing.get(rep, {}).get(month, []))
            if month not in targets[rep]:
                if rep in desired:
                    for codlan, percent, weight in rows:
                        operations.append(RepOperation("delete", rep=rep, month=month, codlan=codlan,
                                                       previous_percent=percent, previous_weight=weight, **base))
                continue
            percent, weight = targets[rep][month], weights.get(rep, Decimal("0.000"))
            if not rows:
                operations.append(RepOperation("insert", rep=rep, month=month, percent=percent, weight=weight, **base))
                continue
            codlan, old_percent, old_weight = rows[0]
            if old_percent == percent and old_weight == weight and len(rows) == 1:
                unchanged += 1
                continue
            if old_percent != percent or old_weight != weight:
                operations.append(RepOperation("update", rep=rep, month=month, percent=percent, weight=weight, codlan=codlan,
                                               previous_percent=old_percent, previous_weight=old_weight, **base))
            for extra_codlan, extra_percent, extra_weight in rows[1:]:
                operations.append(RepOperation("delete", rep=rep, month=month, codlan=extra_codlan,
                                               previous_percent=extra_percent, previous_weight=extra_weight, **base))
    return operations, unchanged


def _execute_rep_operations(cursor, table, year, operations):
    if not operations:
        return
    cursor.execute(GER_MAX_SQL.format(table=table))
    next_codlan = int(cursor.fetchone()[0] or 0) + 1
    year_date, _ = sales_goals._year_bounds(year)
    for operation in operations:
        params = {"empresa": operation.company, "filial": operation.branch, "representante": operation.rep}
        if operation.kind == "insert":
            if next_codlan > sales_goals.MAX_CODLAN:
                raise SalesGoalsError("O número de lançamento passou do limite da coluna USU_CODLAN.")
            operation.codlan = next_codlan
            next_codlan += 1
            cursor.execute(GER_INSERT_SQL.format(table=table), {
                **params, "lancamento": operation.codlan, "ano": year_date, "familia": operation.family,
                "mes": datetime.datetime(year, operation.month, 1), "percentual": operation.percent, "peso": operation.weight,
            })
            continue
        if operation.kind == "update":
            cursor.execute(GER_UPDATE_SQL.format(table=table), {
                **params, "lancamento": operation.codlan, "percentual": operation.percent, "peso": operation.weight,
            })
        else:
            cursor.execute(GER_DELETE_SQL.format(table=table), {**params, "lancamento": operation.codlan})
        if cursor.rowcount != 1:
            raise SalesGoalsError(
                f"O lançamento {operation.codlan} do representante {operation.rep} mudou durante a gravação; nada foi gravado."
            )


def _lock(cursor, table):
    cursor.execute(f"LOCK TABLE {table} IN EXCLUSIVE MODE WAIT {sales_goals.LOCK_WAIT_SECONDS}")


def recompute_families(cursor, year, families, *, schema_name=None):
    """Depois de mudar a meta (na mesma transação): refaz o peso dos representantes dessas famílias, mantendo o %.

    `families`: {(empresa, filial, família)}. Devolve as operações gravadas.
    """
    if not families:
        return []
    met, ger = sales_goals.table_ref(schema_name), ger_table_ref(schema_name)
    _lock(cursor, ger)
    operations = []
    scopes = defaultdict(set)
    for company, branch, family in families:
        scopes[(company, branch)].add(family)
    for (company, branch), names in sorted(scopes.items()):
        rep_rows = _rep_rows(cursor, ger, company, branch, year)
        if not any(rep_rows.get(name) for name in names):
            continue
        current, _outside = sales_goals._scope_rows(cursor, met, [(company, branch)], year)
        meta_rows = current.get((company, branch), {})
        for family in sorted(names):
            if rep_rows.get(family):
                ops, _unchanged = plan_family(company, branch, family, _meta_months(meta_rows, family), rep_rows[family])
                operations.extend(ops)
    _execute_rep_operations(cursor, ger, year, operations)
    return operations


def save_reps(company, branch, year, changes, *, schema_name=None, connect=None):
    """Grava os % alterados na tela (e o peso de todos os representantes das famílias tocadas).

    Confere se ninguém mudou o % desde que a tela abriu; com conflito nada é gravado.
    """
    met, ger = sales_goals.table_ref(schema_name), ger_table_ref(schema_name)
    connection = (connect or sales_goals._connect)()
    try:
        cursor = connection.cursor()
        try:
            _lock(cursor, ger)
            rep_rows = _rep_rows(cursor, ger, company, branch, year)
            current, _outside = sales_goals._scope_rows(cursor, met, [(company, branch)], year)
            meta_rows = current.get((company, branch), {})
            plan = RepPlan()
            desired = defaultdict(dict)
            unique = {}
            for change in changes:
                unique[(change.family, change.rep)] = change
            for (family, rep), change in unique.items():
                stored = shown_percent(rep_rows.get(family, {}).get(rep, {}))
                if not sales_goals._same(stored, change.original):
                    plan.conflicts.append({
                        "empresa": company, "filial": branch, "familia": family, "representante": rep,
                        "tela": percent_text(change.original), "atual": percent_text(stored),
                    })
                    continue
                desired[family][rep] = change.percent or None
            if plan.conflicts:
                connection.rollback()
                return plan
            for family in sorted(desired):
                ops, unchanged = plan_family(company, branch, family, _meta_months(meta_rows, family),
                                             rep_rows.get(family, {}), desired[family])
                plan.operations.extend(ops)
                plan.unchanged += unchanged
            if not plan.operations:
                connection.rollback()
                return plan
            _execute_rep_operations(cursor, ger, year, plan.operations)
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


def load_rep_grid(company, branch, year, *, schema_name=None, connect=None):
    """Representantes da filial no ano por família, com a meta de cada mês para a tela calcular o peso."""
    met, ger = sales_goals.table_ref(schema_name), ger_table_ref(schema_name)
    connection = (connect or sales_goals._connect)()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            current, _outside = sales_goals._scope_rows(cursor, met, [(company, branch)], year)
            rep_rows = _rep_rows(cursor, ger, company, branch, year)
        finally:
            cursor.close()
            connection.rollback()
    finally:
        connection.close()
    meta_rows = current.get((company, branch), {})
    families = sorted({family for family, _month in meta_rows} | set(rep_rows))
    result = []
    for family in families:
        reps = []
        by_share = sorted(rep_rows.get(family, {}).items(), key=lambda pair: (-(shown_percent(pair[1]) or 0), pair[0]))
        for rep, months in by_share:
            reps.append({
                "rep": rep,
                "percent": shown_percent(months),
                "varies": varies(months),
                "months": [
                    sum((weight for _c, _p, weight in months.get(month, [])), Decimal(0)) if months.get(month) else None
                    for month in MONTHS
                ],
                "launches": sum(len(rows) for rows in months.values()),
            })
        result.append({"family": family, "meta": _meta_months(meta_rows, family), "reps": reps})
    return result


def preload_reps(year, *, company=None, branch=None, base=None, replace=False, exclude_inactive=False,
                 dry_run=True, schema_name=None, connect=None, index_loader=None):
    """Representantes de todas as famílias com meta no ano, pelo faturamento do período-base.

    Família que já tem representante fica como está, a não ser com `replace` (aí os % calculados
    substituem os gravados e quem não faturou na base sai). Sem `dry_run`, grava numa transação só.
    """
    met, ger = sales_goals.table_ref(schema_name), ger_table_ref(schema_name)
    first_year, last_year = parse_base(base, year)
    inactive = {code for code, rep in representatives().items() if not rep["active"]} if exclude_inactive else set()
    loader = index_loader or load_billing_index
    connection = (connect or sales_goals._connect)()
    try:
        cursor = connection.cursor()
        try:
            if dry_run:
                cursor.execute("SET TRANSACTION READ ONLY")
            else:
                _lock(cursor, ger)
            start, end = sales_goals._year_bounds(year)
            cursor.execute(sales_goals.SUMMARY_SQL.format(table=met), {"inicio": start, "fim": end})
            scopes = sorted(
                (int(row[0]), int(row[1])) for row in cursor.fetchall()
                if (company is None or int(row[0]) == company) and (branch is None or int(row[1]) == branch)
            )
            plan = RepPlan()
            indexes = {}
            for scope_company, scope_branch in scopes:
                current, _outside = sales_goals._scope_rows(cursor, met, [(scope_company, scope_branch)], year)
                meta_rows = current.get((scope_company, scope_branch), {})
                rep_rows = _rep_rows(cursor, ger, scope_company, scope_branch, year)
                if scope_company not in indexes:
                    indexes[scope_company] = loader(scope_company, first_year, last_year)
                families = sorted({family for family, _month in meta_rows})
                shares = suggested_shares(scope_company, scope_branch, families, (first_year, last_year),
                                          exclude=inactive, index=indexes[scope_company])
                for family in families:
                    plan.shares[(scope_company, scope_branch, family)] = shares[family]
                    if rep_rows.get(family) and not replace:
                        plan.skipped.append((scope_company, scope_branch, family))
                        continue
                    desired = dict(shares[family]["percents"])
                    for rep in rep_rows.get(family, {}):
                        desired.setdefault(rep, None)
                    ops, unchanged = plan_family(scope_company, scope_branch, family, _meta_months(meta_rows, family),
                                                 rep_rows.get(family, {}), desired)
                    plan.operations.extend(ops)
                    plan.unchanged += unchanged
            if dry_run or not plan.operations:
                connection.rollback()
                return plan
            _execute_rep_operations(cursor, ger, year, plan.operations)
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


def verify_reps(year, *, schema_name=None, connect=None):
    """Confere a USU_TORCGER contra a USU_TORCMET: por família e mês, soma dos % = 100 e soma do peso = meta.

    Devolve (famílias-mês conferidas, divergências). Família sem representante não entra.
    """
    met, ger = sales_goals.table_ref(schema_name), ger_table_ref(schema_name)
    connection = (connect or sales_goals._connect)()
    differences, checked = [], 0
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            start, end = sales_goals._year_bounds(year)
            cursor.execute(sales_goals.SUMMARY_SQL.format(table=met), {"inicio": start, "fim": end})
            scopes = sorted((int(row[0]), int(row[1])) for row in cursor.fetchall())
            for company, branch in scopes:
                current, _outside = sales_goals._scope_rows(cursor, met, [(company, branch)], year)
                meta_rows = current.get((company, branch), {})
                rep_rows = _rep_rows(cursor, ger, company, branch, year)
                for family, reps in sorted(rep_rows.items()):
                    meta = _meta_months(meta_rows, family)
                    for month in MONTHS:
                        entries = [entry for months in reps.values() for entry in months.get(month, [])]
                        if not entries:
                            continue
                        checked += 1
                        percent = sum((entry[1] or Decimal(0) for entry in entries), Decimal(0))
                        weight = sum((entry[2] for entry in entries), Decimal(0))
                        if percent != HUNDRED or weight != (meta[month - 1] or Decimal(0)):
                            differences.append({
                                "empresa": company, "filial": branch, "familia": family, "mes": month,
                                "percentual": percent_text(percent), "peso": sales_goals.weight_text(weight),
                                "meta": sales_goals.weight_text(meta[month - 1]),
                            })
        finally:
            cursor.close()
            connection.rollback()
    finally:
        connection.close()
    return checked, differences
