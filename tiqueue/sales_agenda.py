"""Agenda Comercial: quem procurar nos próximos dias e por quê.

Usa a base de inteligência de clientes (`customer_intel`, cache de 30 minutos) e
monta, por representante, uma lista de prioridades com o motivo em texto:

* compra prevista: o ritmo do cliente indica nova compra dentro do horizonte;
* atrasado / risco de parada: passou do próprio intervalo típico de compra;
* venda cruzada: grupos que boa parte do segmento compra e o cliente não;
* reativação: sem compra há mais de 180 dias; quem está com o CNPJ irregular na
  Receita (já conferido na Econodata) sai da lista.

A prioridade é o valor típico do pedido (faturamento médio de um mês com compra)
ponderado pela urgência. Um contato registrado tira o cliente da fila por 7 dias.
"""

from datetime import timedelta
from decimal import Decimal

from . import commercial_dna as dna
from . import customer_intel as intel

ZERO = Decimal("0")
SNOOZE_DAYS = 7
HORIZONS = (7, 15, 30)
DEFAULT_HORIZON = 7
LIST_LIMIT = 60

REASONS = {
    "due": ("Compra prevista", "is-info"),
    "late": ("Atrasado", "is-warn"),
    "at_risk": ("Risco de parada", "is-bad"),
    "cross": ("Venda cruzada", "is-good"),
    "reactivate": ("Reativar", ""),
}
REASON_ORDER = ("due", "late", "at_risk", "cross", "reactivate")


def _days(value):
    return "hoje" if value == 0 else "amanhã" if value == 1 else f"em {value} dias"


def reasons_for(item, cross, today, horizon):
    """Motivos (com peso) para o cliente estar na agenda agora."""
    rhythm = item["rhythm"]
    value = item["typical_value"] or ZERO
    reasons = []
    if rhythm["status"] == "on_track" and rhythm["next_purchase"] and rhythm["next_purchase"] <= today + timedelta(days=horizon):
        days = max((rhythm["next_purchase"] - today).days, 0)
        reasons.append({
            "key": "due",
            "text": f"Compra a cada ~{rhythm['typical_days']} dias; a próxima é esperada {_days(days)} ({dna._display_date(rhythm['next_purchase'])}).",
            "score": value,
        })
    elif rhythm["status"] in ("late", "at_risk"):
        overdue = rhythm["overdue_days"] or 0
        weight = min(Decimal(1) + Decimal(overdue) / Decimal(max(rhythm["typical_days"] or 30, 1)), Decimal(3))
        reasons.append({
            "key": rhythm["status"],
            "text": f"Compra a cada ~{rhythm['typical_days']} dias e está há {rhythm['days_since']} dias sem comprar ({overdue} dias de atraso).",
            "score": value * weight,
        })
    elif rhythm["status"] == "inactive" and item["history_net"] > 0:
        reasons.append({
            "key": "reactivate",
            "text": f"Sem compra há {rhythm['days_since']} dias; comprou {intel.money(item['history_net'])} nos últimos 24 meses.",
            "score": item["history_net"] / 24 * Decimal("0.6"),
        })
    if item["net"] > 0 and cross and cross["items"]:
        best = cross["items"][0]
        groups = ", ".join(row["group"] for row in cross["items"][:3])
        reasons.append({
            "key": "cross",
            "text": f"{best['adoption_display']} dos clientes de {intel.segment_label(item)} compram {best['group']}; este não compra {groups}.",
            "score": sum((row["median_net"] for row in cross["items"][:3]), ZERO) / 12 * Decimal("0.4"),
        })
    return reasons


def build_agenda(metrics, base, model, rep_code=None, horizon=DEFAULT_HORIZON, today=None, contacts=None, receita=None):
    """Lista de prioridades. `contacts`: {cliente: último contato}; `receita`: situação já guardada por CNPJ."""
    today = today or base["today"]
    contacts = contacts or {}
    receita = receita or {}
    items = []
    for item in metrics.values():
        if rep_code is not None and item["rep_code"] != rep_code:
            continue
        status = receita.get(item["cnpj"])
        if status and status.get("alerta"):
            continue  # CNPJ baixado, inapto, suspenso ou nulo: não há a quem vender
        cross = intel.cross_sell_for(item, base, model) if item["net"] > 0 else None
        reasons = reasons_for(item, cross, today, horizon)
        if not reasons:
            continue
        reasons.sort(key=lambda reason: reason["score"], reverse=True)
        score = reasons[0]["score"] + sum((reason["score"] for reason in reasons[1:]), ZERO) * Decimal("0.25")
        last = contacts.get(item["code"])
        snoozed = bool(last and (today - last["date"]).days < SNOOZE_DAYS)
        items.append({"item": item, "reasons": reasons, "score": score, "last_contact": last, "snoozed": snoozed, "cross": cross})
    items.sort(key=lambda entry: (entry["snoozed"], -entry["score"]))
    counts = {key: sum(1 for entry in items if entry["reasons"][0]["key"] == key) for key in REASON_ORDER}
    pending = [entry for entry in items if not entry["snoozed"]]
    return {
        "items": items,
        "pending": len(pending),
        "snoozed": len(items) - len(pending),
        "counts": counts,
        "expected_value": sum((entry["item"]["typical_value"] for entry in pending if entry["reasons"][0]["key"] == "due"), ZERO),
        "at_stake": sum((entry["item"]["typical_value"] for entry in pending if entry["reasons"][0]["key"] in ("late", "at_risk")), ZERO),
    }


def entry_display(entry):
    item = entry["item"]
    row = intel.row_display(item)
    reasons = [
        {"key": reason["key"], "label": REASONS[reason["key"]][0], "tone": REASONS[reason["key"]][1], "text": reason["text"]}
        for reason in entry["reasons"]
    ]
    last = entry["last_contact"]
    return {
        **row,
        "reasons": reasons,
        "primary": reasons[0],
        "typical_display": intel.money(item["typical_value"]),
        "score": float(entry["score"]),
        "snoozed": entry["snoozed"],
        "last_contact": last,
    }


CSV_HEADER = [
    "Prioridade", "Cliente", "Código", "Cidade/UF", "Representante", "Segmento", "Motivo", "Detalhe",
    "Pedido típico (R$)", "Última compra", "Próxima prevista", "Lucro bruto no período (R$)", "Margem", "Último contato",
]


def csv_rows(entries):
    rows = []
    for position, entry in enumerate(entries, start=1):
        row = entry_display(entry)
        last = row["last_contact"]
        rows.append([
            position, row["name"], row["code"], row["place"], row["rep_name"], row["segment_label"],
            " / ".join(reason["label"] for reason in row["reasons"]), " ".join(reason["text"] for reason in row["reasons"]),
            f"{entry['item']['typical_value']:.2f}".replace(".", ","), row["last_purchase_display"],
            row["rhythm"]["next_purchase_display"] or "", f"{(entry['item']['profit'] or ZERO):.2f}".replace(".", ","),
            row["margin_display"], f"{last['date']:%d/%m/%Y} {last['outcome_label']}" if last else "",
        ])
    return rows
