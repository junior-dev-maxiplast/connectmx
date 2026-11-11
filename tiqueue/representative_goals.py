"""
Metas de venda por representante, vindas do PBIX "2. ORÇAMENTO 2026".

No PBIX as metas não estão no Oracle: são tabelas digitadas no próprio
relatório ("Inserir dados"), uma por representante e outra por representante
no Big Bag, guardadas como JSON comprimido dentro do código M:

    Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText("<base64>",
        BinaryEncoding.Base64), Compression.Deflate)), let _t = ... in type table [...])

Este módulo decodifica esse formato e normaliza as linhas para o modelo
`RepresentativeGoal`. A migration que cria o modelo carrega o JSON gerado a
partir da extração (`tiqueue/data/representative_goals.json`); quando o PBIX
mudar, o comando `importar_metas_pbix` refaz a carga a partir da pasta
`consultas` extraída.
"""

import base64
import json
import os
import re
import zlib
from decimal import Decimal, InvalidOperation


_B64_RE = re.compile(r'Binary\.FromText\("([^"]*)"\s*,\s*BinaryEncoding\.Base64\)')
_TYPE_RE = re.compile(r"in type table \[(.*?)\]\)", re.S)
_COLUMN_RE = re.compile(r'(?:#"((?:[^"]|"")*)"|([^\s=,\[\]]+))\s*=\s*_t')

# Colunas da tabela de meta por representante, na ordem em que o PBIX as declara.
GOAL_COLUMNS = ("1", "CD_REPRESENTANTE", "DS_REPRESENTANTE", "ANO", "PESO", "FAMILIA", "COD", "MES", "MES.1")

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "data", "representative_goals.json")


def decode_embedded_table(m_source):
    """Devolve {"columns": [...], "rows": [[...], ...]} ou None se não houver tabela embutida."""
    match = _B64_RE.search(m_source or "")
    if not match:
        return None
    payload = zlib.decompress(base64.b64decode(match.group(1)), -15)
    rows = json.loads(payload.decode("utf-8"))
    columns = []
    type_match = _TYPE_RE.search(m_source)
    if type_match:
        for quoted, bare in _COLUMN_RE.findall(type_match.group(1)):
            columns.append((quoted or bare).replace('""', '"'))
    return {"columns": columns, "rows": rows}


def _decimal(value):
    text = str(value or "").strip().replace(".", "").replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _int(value):
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def normalize_goal_rows(table):
    """Linhas de meta com peso positivo, no formato do modelo.

    A coluna "1" do PBIX é a unidade do orçamento (1 Matriz, 2 Ráfia, 3 Big Bag,
    4 Serafina, 5 Adesivado, 7 São Paulo) e "MES" vem como 01/MM/AAAA. Linha sem
    peso é célula em branco na planilha do PBIX: não é meta zero, é ausência de
    meta, e por isso fica de fora.
    """
    columns = table.get("columns") or []
    if tuple(columns[: len(GOAL_COLUMNS)]) != GOAL_COLUMNS:
        return []
    goals = []
    for raw in table.get("rows") or []:
        record = dict(zip(columns, raw))
        weight = _decimal(record.get("PESO"))
        unit = _int(record.get("1"))
        rep_code = _int(record.get("CD_REPRESENTANTE"))
        year = _int(record.get("ANO"))
        month = _int(record.get("MES.1"))
        if not weight or weight <= 0 or unit is None or rep_code is None or not year or not month:
            continue
        goals.append(
            {
                "year": year,
                "month": month,
                "unit_code": unit,
                "rep_code": rep_code,
                "rep_name": " ".join(str(record.get("DS_REPRESENTANTE") or "").split()),
                "family": " ".join(str(record.get("FAMILIA") or "").upper().split()),
                "group_code": _int(record.get("COD")),
                "weight_kg": str(weight),
            }
        )
    return goals


def goals_from_consultas_dir(path):
    """Lê todos os `.m` da pasta `consultas` da extração e junta as metas por representante."""
    goals = []
    for name in sorted(os.listdir(path)):
        if not name.endswith(".m"):
            continue
        with open(os.path.join(path, name), encoding="utf-8") as handle:
            table = decode_embedded_table(handle.read())
        if table:
            goals.extend(normalize_goal_rows(table))
    return dedupe_goals(goals)


def dedupe_goals(goals):
    """Uma meta por (ano, mês, unidade, representante, família): a última lida vence."""
    unique = {}
    for goal in goals:
        key = (goal["year"], goal["month"], goal["unit_code"], goal["rep_code"], goal["family"])
        unique[key] = goal
    return [unique[key] for key in sorted(unique)]


def load_fixture(path=FIXTURE_PATH):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_fixture(goals, path=FIXTURE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(goals, handle, ensure_ascii=False, indent=1)


def replace_goals(model, goals, source="pbix-orcamento-2026"):
    """Troca as metas dos anos presentes em `goals` pelas novas (idempotente)."""
    years = sorted({goal["year"] for goal in goals})
    model.objects.filter(year__in=years).delete()
    model.objects.bulk_create(
        [
            model(
                year=goal["year"],
                month=goal["month"],
                unit_code=goal["unit_code"],
                rep_code=goal["rep_code"],
                rep_name=goal["rep_name"],
                family=goal["family"],
                group_code=goal["group_code"],
                weight_kg=Decimal(goal["weight_kg"]),
                source=source,
            )
            for goal in goals
        ],
        batch_size=500,
    )
    return len(goals), years
