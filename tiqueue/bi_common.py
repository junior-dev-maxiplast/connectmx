"""Auxiliares compartilhados pelos BIs do ConnectMX Dashes.

Compras, Romaneios, Viagens, TI e DNA do cliente nasceram como cópias uns dos
outros e cada um carregava a própria versão destas funções. Com o tempo as
cópias divergiram (o BI do TI, por exemplo, formatava "1234,5" enquanto os
outros mostravam "1.234,5"). Aqui fica a versão única; os módulos importam
com os nomes privados de sempre (`_int`, `_query`...), então quem faz
`patch.object(modulo, "_query_many")` continua funcionando.
"""

from datetime import date, datetime
from decimal import Decimal


# ------------------------------------------------------------ consultas --

def _rows(cursor):
    columns = [column[0].lower() for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def oracle_query(connect, sql, params=None):
    """Roda uma consulta no Oracle e devolve linhas como dicts de colunas em minúsculas."""
    connection = connect()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(sql, params or {})
        return _rows(cursor)
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def oracle_query_many(connect, statements, params):
    """Roda várias agregações em uma conexão só; `params` é indexado pelo nome da consulta."""
    connection = connect()
    results = {}
    try:
        for name, sql in statements.items():
            cursor = connection.cursor()
            try:
                cursor.execute(sql, params.get(name, {}))
                results[name] = _rows(cursor)
            finally:
                cursor.close()
    finally:
        connection.close()
    return results


# -------------------------------------------------------------- números --

def to_decimal(value):
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def to_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        # Texto como "12.0" não passa direto por int().
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def to_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def share(part, total):
    """Percentual de `part` em `total`, com uma casa."""
    return round(to_float(part) / to_float(total) * 100, 1) if to_float(total) else 0.0


# ------------------------------------------------------------ formatação --

def format_int(value):
    return f"{to_int(value):,}".replace(",", ".")


def format_decimal(value, places=1):
    text = f"{to_float(value):,.{places}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_money(value, compact=False):
    amount = to_float(value)
    suffix = ""
    if compact and abs(amount) >= 1_000_000:
        amount /= 1_000_000
        suffix = " Mi"
    elif compact and abs(amount) >= 1_000:
        amount /= 1_000
        suffix = " mil"
    return f"R$ {format_decimal(amount, 2)}{suffix}"


def format_duration_days(value):
    """Dias fracionários em texto curto: '3d 4h', '18h 30min', '45min'."""
    total_minutes = int(round(to_float(value) * 24 * 60))
    if total_minutes <= 0:
        return "-"
    hours, minutes = divmod(total_minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}min" if minutes else f"{hours}h"
    return f"{minutes}min"


def display_date(value):
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return "-"


def display_datetime(value):
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return "-"


# ---------------------------------------------------------------- filiais --

ALL_BRANCHES = {"key": "all", "label": "Todas as filiais", "empresa": None, "filial": None}


def branches_from_rows(rows):
    """Catálogo do filtro de filial a partir de linhas (empresa, filial, total).

    O rótulo só mostra a empresa quando o catálogo trouxer mais de uma.
    """
    single_company = len({to_int(row.get("empresa")) for row in rows}) <= 1
    branches = []
    for row in rows:
        empresa = to_int(row.get("empresa"))
        filial = to_int(row.get("filial"))
        branches.append(
            {
                "key": f"{empresa}-{filial}",
                "empresa": empresa,
                "filial": filial,
                "label": f"Filial {filial}" if single_company else f"Empresa {empresa} / Filial {filial}",
                "total": to_int(row.get("total")),
            }
        )
    return branches


def resolve_branch(raw_value, available=None):
    value = (raw_value or "all").strip()
    item = {entry["key"]: entry for entry in (available or [])}.get(value)
    if item is None:
        return dict(ALL_BRANCHES)
    return {"key": item["key"], "label": item["label"], "empresa": item["empresa"], "filial": item["filial"]}
