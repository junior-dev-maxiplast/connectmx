"""Detalhe ("Expandir dados") dos cards da Carteira de Clientes, do Mapa de Clientes e da Agenda
Comercial.

As linhas são os clientes do mesmo recorte da página (a base de `customer_intel` em cache, com os
mesmos filtros), então as somas do detalhe fecham com o card clicado. Nada aqui consulta o ERP:
as notas de um cliente saem de `chart_drill.portfolio_customer_detail`.
"""

import statistics
from decimal import Decimal

from django.utils import timezone

from . import commercial_dna as dna
from . import customer_intel as intel

ZERO = Decimal("0")


class UnknownPoint(ValueError):
    """Card ou ponto que o detalhe não conhece."""


def _f(value, digits=2):
    return None if value is None else round(float(value), digits)


def _money(value):
    return dna._money(value, compact=False)


def _count(value):
    return dna.int_display(value)


# ---------------------------------------------------------------------------
# Linhas de cliente
# ---------------------------------------------------------------------------

CUSTOMER_COLUMNS = [
    {"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"},
    {"key": "rep", "label": "Representante"},
    {"key": "segment", "label": "Segmento"},
    {"key": "net", "label": "Faturamento", "type": "money", "total": True, "strong": True},
    {"key": "kg", "label": "Kg", "type": "kg", "total": True},
    {"key": "profit", "label": "Lucro bruto", "type": "money", "total": True},
    {"key": "margin", "label": "Margem", "type": "pct", "ratio": ["profit", "tsi_known"]},
    {"key": "tsi_known", "label": "Sem impostos (com custo)", "type": "money", "hidden": True},
    {"key": "previous_net", "label": "Ano anterior", "type": "money", "total": True},
    {"key": "growth", "label": "Cresc.", "type": "pct", "digits": 0},
    {"key": "last", "label": "Última compra", "type": "date", "nowrap": True, "sub": "since"},
    {"key": "rhythm", "label": "Ritmo"},
    {"key": "rfm", "label": "RFM"},
]
GROUP_BY = ["rep", "segment", "rhythm", "rfm"]


def customer_row(item, customer_url=None, extra=None):
    """Um cliente da base (`customer_metrics`): lucro e margem só quando o custo é conhecido, como
    os cards somam (`portfolio_summary`)."""
    known = item["margin"] is not None
    rhythm = item.get("rhythm") or {}
    place = f"{item['city']}/{item['state']}" if item.get("city") else item.get("state") or ""
    row = {
        "customer": item["name"],
        "customer_sub": " · ".join(part for part in (str(item["code"]), place) if part),
        "customer_url": customer_url(item["code"]) if customer_url else None,
        "rep": item.get("rep_name") or "-",
        "segment": intel.segment_label(item),
        "state": item.get("state") or "-",
        "net": _f(item["net"]),
        "kg": _f(item["kg"], 1),
        "profit": _f(item["profit"]) if known else None,
        "margin": _f(item["margin"], 4) if known else None,
        "tsi_known": _f(item["tsi"]) if known else None,
        "previous_net": _f(item["previous_net"]) if item.get("previous_net") else None,
        "growth": _f(item["growth"], 4) if item.get("growth") is not None else None,
        "last": item["last_purchase"].isoformat() if item.get("last_purchase") else None,
        "since": f"há {item['days_since']} dias" if item.get("days_since") is not None else "",
        "rhythm": rhythm.get("label") or "",
        "rfm": (item.get("rfm") or {}).get("label") or "-",
        "__tone": "bad" if known and item["margin"] < 0 else None,
    }
    row.update(extra or {})
    return row


def _section(key, label, items, customer_url, sort="net", empty="Nenhum cliente.", columns=None, extra=None):
    return {
        "key": key, "label": label, "columns": columns or CUSTOMER_COLUMNS,
        "rows": [customer_row(item, customer_url, extra(item) if extra else None) for item in items],
        "sort": {"key": sort, "dir": "desc"}, "groupBy": GROUP_BY, "empty": empty,
    }


def _facts(items):
    """Faturamento, lucro, margem e clientes de uma lista, com as regras do `portfolio_summary`."""
    active = [item for item in items if item["net"] > 0]
    known = [item for item in active if item["margin"] is not None]
    net = sum((item["net"] for item in active), ZERO)
    profit = sum((item["profit"] for item in known), ZERO)
    tsi = sum((item["tsi"] for item in known), ZERO)
    return [
        {"label": "Faturamento", "value": _money(net), "note": intel.kg(sum((item["kg"] for item in active), ZERO))},
        {"label": "Lucro bruto", "value": _money(profit) if known else "–", "tone": "bad" if profit < 0 else None},
        {"label": "Margem", "value": intel.pct(profit / tsi) if tsi else "–"},
        {"label": "Clientes", "value": _count(len(active))},
    ]


# ---------------------------------------------------------------------------
# Carteira de Clientes
# ---------------------------------------------------------------------------

PORTFOLIO_POINTS = ("lucro", "concentracao", "margem-negativa", "clientes", "ritmo")


def portfolio_detail(rows, rows_all, window, point, customer_url=None):
    """`rows`: a carteira com todos os filtros da página; `rows_all`: sem o filtro de RFM (a
    distribuição dos segmentos RFM mostra todos)."""
    period = f"{window['label']} · {window['range_label']}"
    active = [item for item in rows if item["net"] > 0]
    if point.startswith("rfm:"):
        key = point[4:]
        if key not in intel.RFM_SEGMENTS:
            raise UnknownPoint("Segmento RFM desconhecido.")
        label, _tone, description = intel.RFM_SEGMENTS[key]
        items = [item for item in rows_all if item["net"] > 0 and (item.get("rfm") or {}).get("key") == key]
        return {
            "title": label,
            "subtitle": f"{description} · {period}",
            "summary": _facts(items),
            "sections": [_section("customers", "Clientes do segmento", items, customer_url, empty="Nenhum cliente neste segmento.")],
        }
    if point == "lucro":
        net, profit, margin, customers = _facts(active)
        return {
            "title": "Lucro bruto",
            "subtitle": f"Clientes que compraram no recorte · lucro e margem dos que têm custo conhecido · {period}",
            "summary": [profit, margin, net, customers],
            "sections": [_section("customers", "Clientes", active, customer_url, sort="profit")],
        }
    if point == "concentracao":
        known = sorted((item for item in active if item["margin"] is not None and item["profit"] > 0), key=lambda item: item["profit"], reverse=True)
        stats = intel.pareto_stats([item["profit"] for item in active if item["margin"] is not None])
        total = sum((item["profit"] for item in known), ZERO)
        running = ZERO
        ranked = []
        for position, item in enumerate(known, start=1):
            running += item["profit"]
            ranked.append((item, {"position": position, "share": _f(item["profit"] / total, 4) if total else None,
                                  "cumulative": _f(running / total, 4) if total else None}))
        columns = [{"key": "position", "label": "Posição", "type": "int"}] + CUSTOMER_COLUMNS[:1] + [
            {"key": "profit", "label": "Lucro bruto", "type": "money", "total": True, "strong": True},
            {"key": "share", "label": "Participação no lucro", "type": "pct", "total": True},
            {"key": "cumulative", "label": "Acumulado", "type": "pct"},
        ] + [column for column in CUSTOMER_COLUMNS if column["key"] in ("rep", "segment", "net", "margin", "tsi_known")]
        top = stats["top20_count"]
        section = lambda key, label, pairs: {  # noqa: E731
            "key": key, "label": label, "columns": columns, "sort": {"key": "position", "dir": "asc"}, "groupBy": GROUP_BY,
            "rows": [customer_row(item, customer_url, extra) for item, extra in pairs], "empty": "Nenhum cliente com lucro.",
        }
        return {
            "title": "Concentração do lucro",
            "subtitle": f"Clientes com lucro, do maior para o menor · {period}",
            "summary": [
                {"label": "20% maiores", "value": intel.pct(stats["top20_share"]) if stats["top20_share"] is not None else "–", "note": f"{_count(top)} cliente(s)"},
                {"label": "Fazem 80% do lucro", "value": f"{_count(stats['count_for_80'])} cliente(s)" if stats["count_for_80"] else "–"},
                {"label": "Lucro bruto dos clientes com lucro", "value": _money(total), "note": f"{_count(len(known))} cliente(s)"},
            ],
            "sections": [section("top", "Os 20% maiores", ranked[:top]), section("all", "Todos com lucro", ranked)],
        }
    if point == "margem-negativa":
        known = [item for item in active if item["margin"] is not None]
        negative = [item for item in known if item["margin"] < 0]
        low = [item for item in known if ZERO <= item["margin"] < intel.LOW_MARGIN]
        suspect = [item for item in known if item["margin"] >= intel.SUSPECT_MARGIN]
        return {
            "title": "Margem negativa",
            "subtitle": f"Clientes vendidos sem lucro, abaixo de 5% de margem e com custo zerado a conferir · {period}",
            "summary": [
                {"label": "Margem negativa", "value": f"{_count(len(negative))} cliente(s)", "note": f"{_money(sum((item['net'] for item in negative), ZERO))} vendidos", "tone": "bad" if negative else "good"},
                {"label": "Abaixo de 5%", "value": f"{_count(len(low))} cliente(s)", "tone": "warn" if low else None},
                {"label": "Custo zerado a conferir", "value": f"{_count(len(suspect))} cliente(s)"},
            ],
            "sections": [
                _section("negative", "Margem negativa", negative, customer_url, sort="profit", empty="Nenhum cliente com margem negativa."),
                _section("low", "Abaixo de 5%", low, customer_url, empty="Nenhum cliente abaixo de 5%."),
                _section("suspect", "Custo zerado a conferir", suspect, customer_url, empty="Nenhum cliente com custo zerado."),
            ],
        }
    if point == "clientes":
        stopped = [item for item in rows if item["net"] <= 0 and item.get("previous_net")]
        previous = sum((item["previous_net"] for item in rows if item.get("previous_net") is not None), ZERO)
        net = sum((item["net"] for item in active), ZERO)
        growth = dna._change(net, previous) if previous else None
        return {
            "title": "Clientes ativos",
            "subtitle": f"Quem comprou no recorte; o crescimento conta também quem parou de comprar · {period}",
            "summary": [
                {"label": "Clientes ativos", "value": _count(len(active))},
                {"label": "Faturamento", "value": _money(net), "note": f"{intel.pct(growth, signed=True)} contra {_money(previous)} no ano anterior" if growth is not None else "", "tone": "good" if growth and growth > 0 else "bad" if growth and growth < 0 else None},
                {"label": "Pararam de comprar", "value": _count(len(stopped)), "note": f"{_money(sum((item['previous_net'] for item in stopped), ZERO))} no ano anterior"},
            ],
            "sections": [
                _section("active", "Ativos", active, customer_url),
                _section("stopped", "Pararam de comprar", stopped, customer_url, sort="previous_net", empty="Nenhum cliente parou de comprar."),
            ],
        }
    if point == "ritmo":
        at_risk = [item for item in active if item["rhythm"]["status"] == "at_risk"]
        late = [item for item in active if item["rhythm"]["status"] == "late"]
        rhythm_columns = CUSTOMER_COLUMNS[:3] + [
            {"key": "typical", "label": "Intervalo típico (dias)", "type": "int"},
            {"key": "next", "label": "Próxima prevista", "type": "date", "nowrap": True},
            {"key": "overdue", "label": "Dias de atraso", "type": "int", "strong": True},
        ] + [column for column in CUSTOMER_COLUMNS if column["key"] in ("last", "net", "profit", "margin", "tsi_known", "rfm")]
        extra = lambda item: {  # noqa: E731
            "typical": item["rhythm"].get("typical_days"), "overdue": item["rhythm"].get("overdue_days"),
            "next": item["rhythm"]["next_purchase"].isoformat() if item["rhythm"].get("next_purchase") else None,
        }
        return {
            "title": "Ritmo de compra",
            "subtitle": f"Clientes atrasados em relação ao próprio intervalo entre compras · {period}",
            "summary": [
                {"label": "Risco de parada", "value": _count(len(at_risk)), "note": "mais de 2 vezes o intervalo sem comprar", "tone": "bad" if at_risk else "good"},
                {"label": "Atrasados", "value": _count(len(late)), "note": "mais de 1,25 vez o intervalo", "tone": "warn" if late else None},
                {"label": "Pedido típico em jogo", "value": _money(sum((item["typical_value"] for item in at_risk + late), ZERO))},
            ],
            "sections": [
                _section("at_risk", "Risco de parada", at_risk, customer_url, sort="overdue", columns=rhythm_columns, extra=extra, empty="Nenhum cliente em risco."),
                _section("late", "Atrasados", late, customer_url, sort="overdue", columns=rhythm_columns, extra=extra, empty="Nenhum cliente atrasado."),
            ],
        }
    raise UnknownPoint("Card desconhecido.")


# ---------------------------------------------------------------------------
# Mapa de Clientes
# ---------------------------------------------------------------------------

AREA_COLUMNS = [
    {"key": "area", "label": "Local"},
    {"key": "customers", "label": "Clientes", "type": "int", "total": True},
    {"key": "net", "label": "Faturamento", "type": "money", "total": True, "strong": True},
    {"key": "kg", "label": "Kg", "type": "kg", "total": True},
    {"key": "profit", "label": "Lucro bruto", "type": "money", "total": True},
    {"key": "margin", "label": "Margem", "type": "pct", "ratio": ["profit", "tsi_known"]},
    {"key": "tsi_known", "label": "Sem impostos (com custo)", "type": "money", "hidden": True},
    {"key": "previous_net", "label": "Ano anterior", "type": "money", "total": True},
    {"key": "growth", "label": "Cresc.", "type": "pct", "digits": 0},
]


def _state_of(item):
    return item["state"] if item["state"] in intel.STATES else intel.FOREIGN


def _city_key(item):
    return str(item["ibge"]) if item["ibge"] else f"{item['state']}:{intel.normalize_text(item['city'])}"


def _area_rows(items, key_of, name_of):
    """Áreas somadas como `intel.geography` (o recorte só com quem comprou; o ano anterior com todos)."""
    areas = {}
    for item in items:
        if item["net"] <= 0 and not item.get("previous_net"):
            continue
        key = key_of(item)
        area = areas.setdefault(key, {"area": name_of(key, item), "customers": 0, "net": ZERO, "kg": ZERO, "profit": ZERO, "tsi_known": ZERO, "previous_net": ZERO})
        area["previous_net"] += item.get("previous_net") or ZERO
        if item["net"] <= 0:
            continue
        area["customers"] += 1
        area["net"] += item["net"]
        area["kg"] += item["kg"]
        if item["margin"] is not None:
            area["profit"] += item["profit"]
            area["tsi_known"] += item["tsi"]
    rows = []
    for area in areas.values():
        rows.append({
            "area": area["area"], "customers": area["customers"], "net": _f(area["net"]), "kg": _f(area["kg"], 1),
            "profit": _f(area["profit"]) if area["tsi_known"] else None, "tsi_known": _f(area["tsi_known"]) if area["tsi_known"] else None,
            "margin": _f(area["profit"] / area["tsi_known"], 4) if area["tsi_known"] else None,
            "previous_net": _f(area["previous_net"]) if area["previous_net"] else None,
            "growth": _f(dna._change(area["net"], area["previous_net"]), 4) if area["previous_net"] else None,
            "__tone": "bad" if not area["customers"] else None,
        })
    return rows


def _area_summary(items):
    facts = _facts(items)
    previous = sum((item.get("previous_net") or ZERO for item in items), ZERO)
    net = sum((item["net"] for item in items if item["net"] > 0), ZERO)
    growth = dna._change(net, previous) if previous else None
    if growth is not None:
        facts[0]["note"] = f"{intel.pct(growth, signed=True)} contra o ano anterior · {facts[0]['note']}"
        facts[0]["tone"] = "good" if growth > 0 else "bad" if growth < 0 else None
    return facts


def _place_result(title, subtitle, items, customer_url, breakdown=None):
    """Clientes de uma área do mapa: os que compraram e os que pararam (entram no crescimento)."""
    bought = [item for item in items if item["net"] > 0]
    stopped = [item for item in items if item["net"] <= 0 and item.get("previous_net")]
    sections = []
    if breakdown:
        sections.append(breakdown)
    sections += [
        _section("customers", "Clientes com compra", bought, customer_url, empty="Nenhum cliente com compra no recorte."),
        _section("stopped", "Pararam de comprar", stopped, customer_url, sort="previous_net", empty="Nenhum cliente parou de comprar aqui."),
    ]
    return {"title": title, "subtitle": subtitle, "summary": _area_summary(items), "sections": sections}


def map_detail(rows, window, point, customer_url=None, potential=None):
    """`rows`: a carteira com os filtros do mapa (representante e segmento). Pontos:
    resumo, uf:PR, cidade:<chave>, regiao:S, perdidas, perdida:<chave>, segmento:PR|<código>."""
    period = f"{window['label']} · {window['range_label']}"
    scope = [item for item in rows if item["net"] > 0 or item.get("previous_net")]
    kind, _, key = point.partition(":")
    state_name = lambda sigla, _item=None: intel.STATES[sigla][1] if sigla in intel.STATES else "Exterior"  # noqa: E731
    if kind == "resumo":
        # resumo:faturamento|lucro|clientes|volume — o mesmo detalhe, ordenado pelo número do card.
        sort = {"lucro": "profit", "volume": "kg"}.get(key, "net")
        titles = {"faturamento": "Faturamento líquido", "lucro": "Lucro bruto", "clientes": "Clientes ativos", "volume": "Volume"}
        active = [item for item in rows if item["net"] > 0]
        breakdown = {"key": "states", "label": "Por UF", "columns": AREA_COLUMNS, "rows": _area_rows(scope, _state_of, state_name), "sort": {"key": sort, "dir": "desc"}, "empty": "Sem UF."}
        result = _place_result(titles.get(key, "Resumo do mapa"), f"Clientes que compraram no recorte, por UF · {period}", scope, customer_url, breakdown)
        for section in result["sections"][1:2]:
            section["sort"] = {"key": sort, "dir": "desc"}
        states = len({_state_of(item) for item in active if _state_of(item) != intel.FOREIGN})
        cities = len({_city_key(item) for item in active if _state_of(item) != intel.FOREIGN})
        result["summary"][3]["note"] = f"{_count(states)} UF(s) · {_count(cities)} cidade(s)"
        # O número do card clicado vem primeiro.
        summary = result["summary"]
        if key == "lucro":
            result["summary"] = [summary[1], summary[2], summary[0], summary[3]]
        elif key == "clientes":
            result["summary"] = [summary[3], summary[0], summary[1], summary[2]]
        elif key == "volume":
            kg = sum((item["kg"] for item in active), ZERO)
            result["summary"] = [{"label": "Volume", "value": intel.kg(kg), "note": "líquido de devolução"}] + summary
        return result
    if kind == "uf":
        sigla = key.upper()
        if sigla not in intel.STATES and sigla != intel.FOREIGN:
            raise UnknownPoint("UF desconhecida.")
        items = [item for item in scope if _state_of(item) == sigla]
        breakdown = {"key": "cities", "label": "Por município", "columns": AREA_COLUMNS, "sort": {"key": "net", "dir": "desc"}, "empty": "Sem município.",
                     "rows": _area_rows(items, _city_key, lambda _key, item: item["city"] or "Cidade não informada")}
        return _place_result(state_name(sigla), f"Clientes da UF · {period}", items, customer_url, breakdown)
    if kind == "cidade":
        items = [item for item in scope if _state_of(item) in intel.STATES and _city_key(item) == key]
        if not items:
            raise UnknownPoint("Município sem cliente no recorte.")
        first = items[0]
        return _place_result(f"{first['city'] or 'Cidade não informada'}/{first['state']}", f"Clientes do município · {period}", items, customer_url)
    if kind == "regiao":
        if key not in intel.REGIONS:
            raise UnknownPoint("Região desconhecida.")
        region_of = lambda item: intel.STATES[item["state"]][2] if item["state"] in intel.STATES else intel.FOREIGN  # noqa: E731
        items = [item for item in scope if region_of(item) == key]
        breakdown = {"key": "states", "label": "Por UF", "columns": AREA_COLUMNS, "rows": _area_rows(items, _state_of, state_name), "sort": {"key": "net", "dir": "desc"}, "empty": "Sem UF."}
        return _place_result(intel.REGIONS[key], f"Clientes da região · {period}", items, customer_url, breakdown)
    if kind in ("perdidas", "perdida"):
        by_city = {}
        for item in scope:
            if _state_of(item) in intel.STATES:
                by_city.setdefault(_city_key(item), []).append(item)
        lost = {city: items for city, items in by_city.items() if not any(item["net"] > 0 for item in items)}
        if kind == "perdida":
            items = lost.get(key)
            if not items:
                raise UnknownPoint("Cidade sem perda neste recorte.")
            first = items[0]
            return {
                "title": f"{first['city'] or 'Cidade não informada'}/{first['state']}",
                "subtitle": f"Clientes que compraram aqui no ano anterior e não no recorte · {period}",
                "summary": [{"label": "Ano anterior", "value": _money(sum((item['previous_net'] for item in items), ZERO))},
                            {"label": "Clientes", "value": _count(len(items))}],
                "sections": [_section("lost", "Pararam de comprar", items, customer_url, sort="previous_net")],
            }
        items = [item for city in lost.values() for item in city]
        cities = _area_rows(items, _city_key, lambda _key, item: f"{item['city'] or 'Cidade não informada'}/{item['state']}")
        return {
            "title": "Onde perdemos clientes",
            "subtitle": f"Cidades sem compra no recorte que compraram no ano anterior · {period}",
            "summary": [{"label": "Cidades", "value": _count(len(lost))},
                        {"label": "Ano anterior", "value": _money(sum((item['previous_net'] for item in items), ZERO))},
                        {"label": "Clientes", "value": _count(len(items))}],
            "sections": [
                {"key": "cities", "label": "Cidades", "columns": AREA_COLUMNS, "rows": cities, "sort": {"key": "previous_net", "dir": "desc"}, "empty": "Nenhuma cidade perdida."},
                _section("lost", "Clientes", items, customer_url, sort="previous_net"),
            ],
        }
    if kind == "segmento":
        sigla, _, segment = key.partition("|")
        items = [item for item in rows if item["net"] > 0 and _state_of(item) == sigla.upper() and (item.get("segment") or {}).get("code") == segment]
        name = intel.segment_label(items[0]) if items else segment
        return {
            "title": f"{name} · {state_name(sigla.upper())}",
            "subtitle": f"Nossos clientes do segmento na UF · {period}",
            "summary": _facts(items),
            "sections": [_section("customers", "Clientes", items, customer_url)],
        }
    raise UnknownPoint("Ponto do mapa desconhecido.")


# ---------------------------------------------------------------------------
# Agenda Comercial
# ---------------------------------------------------------------------------

AGENDA_POINTS = {
    "previstas": ("Compras previstas", ("due",)),
    "risco": ("Atrasados e em risco", ("late", "at_risk")),
    "cruzada": ("Venda cruzada", ("cross",)),
    "reativar": ("Reativar", ("reactivate",)),
}


def agenda_detail(agenda, point, reasons, customer_url=None):
    """Card da Agenda: os clientes da fila cujo motivo principal é o do card (contando também os já
    contatados, como o número do card; o pedido típico somado só dos que estão na fila)."""
    if point not in AGENDA_POINTS:
        raise UnknownPoint("Card desconhecido.")
    title, keys = AGENDA_POINTS[point]
    entries = [entry for entry in agenda["items"] if entry["reasons"][0]["key"] in keys]
    columns = [{"key": "position", "label": "Prioridade", "type": "int"}] + CUSTOMER_COLUMNS[:3] + [
        {"key": "reason", "label": "Motivo"},
        {"key": "detail", "label": "Detalhe", "wrap": True},
        {"key": "typical", "label": "Pedido típico", "type": "money", "total": True, "strong": True},
        {"key": "next", "label": "Próxima prevista", "type": "date", "nowrap": True},
        {"key": "contact", "label": "Último contato"},
    ] + [column for column in CUSTOMER_COLUMNS if column["key"] in ("last", "profit", "margin", "tsi_known")]
    positions = {id(entry): index for index, entry in enumerate(agenda["items"], start=1)}
    rows = []
    for entry in entries:
        item = entry["item"]
        primary = entry["reasons"][0]
        last = entry["last_contact"]
        rows.append(customer_row(item, customer_url, {
            "position": positions[id(entry)], "reason": reasons[primary["key"]][0],
            "detail": " ".join(reason["text"] for reason in entry["reasons"]), "typical": _f(item["typical_value"]),
            "next": item["rhythm"]["next_purchase"].isoformat() if item["rhythm"].get("next_purchase") else None,
            "contact": f"{last['date']:%d/%m} · {last['outcome_label']}" if last else "",
            "__tone": "info" if entry["snoozed"] else None,
        }))
    pending = [entry for entry in entries if not entry["snoozed"]]
    return {
        "title": title,
        "subtitle": "Clientes da agenda pelo motivo principal · quem já foi contatado nos últimos 7 dias aparece marcado",
        "summary": [
            {"label": title, "value": _count(len(entries)), "note": f"{_count(len(entries) - len(pending))} já contatado(s)"},
            {"label": "Pedido típico somado", "value": _money(sum((entry["item"]["typical_value"] for entry in pending), ZERO)), "note": "dos que estão na fila"},
            {"label": "Lucro bruto no período", "value": _money(sum((entry["item"]["profit"] or ZERO for entry in entries), ZERO))},
        ],
        "sections": [{"key": "entries", "label": "Clientes", "columns": columns, "rows": rows, "sort": {"key": "position", "dir": "asc"},
                      "groupBy": ["rep", "segment", "reason", "rhythm"], "empty": "Nenhum cliente com este motivo."}],
    }


CONTACT_COLUMNS = [
    {"key": "date", "label": "Quando", "type": "date", "nowrap": True},
    {"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"},
    {"key": "rep", "label": "Representante"},
    {"key": "reason", "label": "Estava na agenda por"},
    {"key": "outcome", "label": "Resultado"},
    {"key": "note", "label": "Observação", "wrap": True},
    {"key": "by", "label": "Registrado por"},
]


def contacts_detail(contacts, names, reasons, customer_url=None):
    """Card Contatos nos últimos 7 dias: os registros do período (do ConnectMX, não do ERP)."""
    rows = []
    for contact in contacts:
        info = names.get(contact.customer_code) or {}
        rows.append({
            "date": timezone.localtime(contact.created_at).date().isoformat(),
            "customer": contact.customer_name or info.get("name") or f"Cliente {contact.customer_code}",
            "customer_sub": " · ".join(part for part in (str(contact.customer_code), info.get("place") or "") if part),
            "customer_url": customer_url(contact.customer_code) if customer_url else None,
            "rep": info.get("rep") or "-",
            "reason": reasons.get(contact.reason, (contact.reason or "-",))[0] if contact.reason else "-",
            "outcome": contact.get_outcome_display(),
            "__tone": "good" if contact.outcome == "pedido" else "bad" if contact.outcome == "sem_interesse" else None,
            "note": contact.note or "",
            "by": (contact.user.get_full_name() or contact.user.username) if contact.user else "",
        })
    orders = sum(1 for contact in contacts if contact.outcome == "pedido")
    return {
        "title": "Contatos nos últimos 7 dias",
        "subtitle": "Contatos registrados na agenda (ConnectMX)",
        "summary": [{"label": "Contatos", "value": _count(len(rows))}, {"label": "Com pedido fechado", "value": _count(orders), "tone": "good" if orders else None},
                    {"label": "Clientes", "value": _count(len({contact.customer_code for contact in contacts}))}],
        "sections": [{"key": "contacts", "label": "Contatos", "columns": CONTACT_COLUMNS, "rows": rows, "sort": {"key": "date", "dir": "desc"},
                      "groupBy": ["outcome", "rep", "by", "reason"], "empty": "Nenhum contato registrado nos últimos 7 dias."}],
    }


# ---------------------------------------------------------------------------
# Ficha do Cliente: venda cruzada
# ---------------------------------------------------------------------------

def cross_sell_detail(item, base, metrics, group, customer_url=None):
    """Linha de "Venda cruzada": os clientes do mesmo segmento que compram o grupo (12 meses), com
    quanto compraram dele — a adoção e a compra típica do card saem desta lista."""
    segment = item.get("segment") or {}
    peers = [code for code, groups in base["mix"].items()
             if groups and (metrics.get(code) or {}).get("segment") and metrics[code]["segment"]["code"] == segment.get("code")]
    key = next((name for name in {name for code in peers for name in base["mix"][code]} if name.title() == group.title()), group.upper())
    buyers = [code for code in peers if key in base["mix"][code]]
    values = sorted((base["mix"][code][key]["net"] for code in buyers))
    median = statistics.median(values) if values else ZERO  # como o cross_sell_model
    columns = CUSTOMER_COLUMNS[:3] + [
        {"key": "group_net", "label": f"Comprou de {key.title()} (12 meses)", "type": "money", "total": True, "strong": True},
        {"key": "group_kg", "label": "Kg do grupo", "type": "kg", "total": True},
    ] + [column for column in CUSTOMER_COLUMNS if column["key"] in ("net", "margin", "tsi_known", "profit", "last")]
    rows = [customer_row(metrics[code], customer_url, {"group_net": _f(base["mix"][code][key]["net"]), "group_kg": _f(base["mix"][code][key]["kg"], 1)})
            for code in buyers if code in metrics]
    return {
        "title": f"{key.title()} · venda cruzada",
        "subtitle": f"Clientes de {intel.segment_label(item)} que compram o grupo nos últimos 12 meses · {item['name']} não compra",
        "summary": [
            {"label": "Compram o grupo", "value": f"{_count(len(buyers))} de {_count(len(peers))}", "note": f"{intel.pct(Decimal(len(buyers)) / Decimal(len(peers)))} do segmento" if peers else ""},
            {"label": "Compra típica", "value": _money(median), "note": "mediana em 12 meses"},
            {"label": "Somado no segmento", "value": _money(sum(values, ZERO))},
        ],
        "sections": [{"key": "peers", "label": "Clientes do segmento que compram", "columns": columns, "rows": rows,
                      "sort": {"key": "group_net", "dir": "desc"}, "groupBy": ["rep"], "empty": "Nenhum cliente do segmento compra o grupo."}],
    }
