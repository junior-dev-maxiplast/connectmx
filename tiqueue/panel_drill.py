"""Detalhe ("Expandir dados") dos pontos dos painéis animados que a página não tem linha a linha.

O resto dos painéis abre os registros dos fatos que já vieram na página; aqui ficam os dois
gráficos que mostram só totais: o calendário de faturamento (26 semanas, um valor por dia) e o
recebido por mês. Cada consulta repete o filtro do gráfico, para o total do detalhe bater com
o ponto clicado.
"""

from datetime import date, timedelta
from decimal import Decimal as D

from . import commercial_dna as dna
from .chart_drill import BILLING_GROUP_BY, _billing_summary, billing_columns, billing_rows, month_title

ZERO = dna.ZERO
WEEKDAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

# Faturamento do dia: o mesmo recorte do SALES_CALENDAR_SQL (nota fechada, transação que fatura,
# sem os clientes internos). `BILLING_SQL` já traz essas regras e o valor líquido de devolução.
SALES_DAY_SCOPE = "1 = 1"


def sales_day_detail(day, customer_url=None):
    """Notas de um dia do calendário de faturamento (empresa toda, como o calendário)."""
    rows = dna._run({
        "billing": (dna.BILLING_SQL.format(scope=SALES_DAY_SCOPE), {"start_date": day, "end_date": day + timedelta(days=1)}),
    })["billing"]
    lines = [line for line in (dna.billing_line(row) for row in rows) if line["net"] > 0]
    lines.sort(key=lambda line: line["net"], reverse=True)
    return {
        "title": f"{WEEKDAYS[day.weekday()]}, {day:%d/%m/%Y}",
        "subtitle": "Notas faturadas no dia · empresa toda, como o calendário · líquido de devolução",
        "summary": _billing_summary(lines),
        "sections": [{
            "key": "billed", "label": "Notas do dia", "columns": billing_columns(with_rep=True),
            "rows": billing_rows(lines, customer_url), "groupBy": BILLING_GROUP_BY + ["rep"],
            "sort": {"key": "net", "dir": "desc"}, "empty": "Nenhuma nota faturada neste dia.",
        }],
    }


# Recebido por mês: os mesmos títulos do PAID_MONTHLY_SQL (débito liquidado, pelo último pagamento).
PAID_TITLES_SQL = """
SELECT T.CODEMP AS EMPRESA, T.CODFIL AS FILIAL, T.NUMTIT AS TITULO, T.CODTPT AS TIPO, T.CODCLI AS CLIENTE,
       T.CODREP AS REPRESENTANTE, T.DATEMI AS EMISSAO, NVL(T.VCTPRO, T.VCTORI) AS VENCIMENTO,
       T.ULTPGT AS PAGAMENTO, T.VLRORI AS VALOR, T.CODPOR AS PORTADOR, P.DESPOR AS NOME_PORTADOR,
       C.NOMCLI AS NOME, C.CIDCLI AS CIDADE, C.SIGUFS AS UF, R.NOMREP AS NOME_REPRESENTANTE
FROM E301TCR T
JOIN E002TPT TP ON TP.CODTPT = T.CODTPT AND TP.RECSOM = 'D'
LEFT JOIN E039POR P ON P.CODEMP = T.CODEMP AND P.CODPOR = T.CODPOR
LEFT JOIN E085CLI C ON C.CODCLI = T.CODCLI
LEFT JOIN E090REP R ON R.CODREP = T.CODREP
WHERE T.SITTIT = 'LQ' AND T.ULTPGT >= :start_date AND T.ULTPGT < :end_date
"""

PAID_COLUMNS = [
    {"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"},
    {"key": "title", "label": "Título", "nowrap": True, "sub": "kind"},
    {"key": "rep", "label": "Representante"},
    {"key": "holder", "label": "Portador"},
    {"key": "issued", "label": "Emissão", "type": "date", "nowrap": True},
    {"key": "due", "label": "Vencimento", "type": "date", "nowrap": True},
    {"key": "paid_on", "label": "Pagamento", "type": "date", "nowrap": True, "badge": "status", "tone": "status_tone"},
    {"key": "late", "label": "Dias após o vencimento", "type": "int"},
    {"key": "term", "label": "Prazo (dias)", "type": "int"},
    {"key": "value", "label": "Valor", "type": "money", "total": True, "strong": True},
]


def _place(city, state):
    city = (city or "").strip().title()
    state = (state or "").strip()
    return f"{city}/{state}" if city and state else city or state


def _delay_fact(days):
    """Atraso médio ponderado pelo valor (como o gráfico): negativo é pago antes do vencimento."""
    if days is None:
        return {"label": "Atraso médio", "value": "–"}
    text = f"{abs(float(days)):.1f} dia(s)".replace(".", ",")
    return {
        "label": "Atraso médio",
        "value": text,
        "note": f"{'antes' if days < 0 else 'depois'} do vencimento · ponderado pelo valor",
        "tone": "good" if days <= 0 else "warn",
    }


def paid_month_detail(month_start, today=None, customer_url=None):
    """Títulos liquidados no mês (pelo último pagamento), com em dia x com atraso."""
    today = today or date.today()
    end = min(dna._add_months(month_start, 1), today + timedelta(days=1))
    return paid_titles_detail(month_start, end, month_title(month_start),
                              "Títulos liquidados no mês, pelo último pagamento · empresa toda, como o gráfico", customer_url,
                              empty="Nenhum título liquidado neste mês.", all_on_time="Todos os títulos do mês foram pagos em dia.")


def paid_range_detail(first_month, last_month, today=None, customer_url=None, focus=None):
    """Cards Prazo médio de recebimento e Pontualidade: os títulos liquidados nos meses do card
    (os 3 últimos do gráfico Recebido por mês), com o prazo e o atraso ponderados pelo valor."""
    today = today or date.today()
    end = min(dna._add_months(last_month, 1), today + timedelta(days=1))
    label = month_title(first_month) if first_month == last_month else f"{month_title(first_month)} a {month_title(last_month)}"
    result = paid_titles_detail(first_month, end, f"Recebido de {label}",
                                "Títulos liquidados nos meses dos cards Prazo médio e Pontualidade, pelo último pagamento · empresa toda",
                                customer_url, empty="Nenhum título liquidado nestes meses.", all_on_time="Todos os títulos foram pagos em dia.")
    for section in result["sections"]:
        section["groupBy"] = ["paid_month"] + section["groupBy"]
    facts = {fact["label"]: fact for fact in result["summary"]}
    if focus == "prazo":
        result["summary"] = [facts["Prazo médio"]] + [fact for fact in result["summary"] if fact["label"] != "Prazo médio"]
    elif focus == "pontualidade":
        # Pontualidade = valor pago em dia ÷ valor recebido (como o card).
        rows = result["sections"][0]["rows"]
        total = sum((D(str(row["value"])) for row in rows), ZERO)
        on_time = sum((D(str(row["value"])) for row in rows if not row["__tone"]), ZERO)
        result["summary"] = [{"label": "Pontualidade", "value": dna._pct(on_time / total, decimals=0) if total else "–",
                              "note": f"do valor pago em dia ({facts['Em dia']['value']})", "tone": "good"}] + [
            fact for fact in result["summary"] if fact["label"] != "Em dia"]
    return result


def paid_titles_detail(start, end, heading, subtitle, customer_url=None, empty="", all_on_time=""):
    rows = dna._run({"paid": (PAID_TITLES_SQL, {"start_date": start, "end_date": end})})["paid"]
    titles = []
    for row in rows:
        due = dna._date(row.get("vencimento"))
        paid_on = dna._date(row.get("pagamento"))
        issued = dna._date(row.get("emissao"))
        value = dna._num(row.get("valor"))
        customer = dna._int(row.get("cliente"))
        late = (paid_on - due).days if paid_on and due else None
        on_time = late is not None and late <= 0
        rep = dna.primary_rep(row.get("representante"))
        titles.append({
            "customer": (row.get("nome") or "").strip() or f"Cliente {customer}",
            "customer_sub": " · ".join(part for part in (str(customer or ""), _place(row.get("cidade"), row.get("uf"))) if part),
            "customer_url": customer_url(customer) if customer_url and customer else None,
            "title": f"{dna._int(row.get('empresa'))}-{dna._int(row.get('filial'))}-{(row.get('titulo') or '').strip()}",
            "kind": (row.get("tipo") or "").strip(),
            "rep": dna.REPRESENTATIVE_DISPLAY_NAMES.get(rep) or (row.get("nome_representante") or "").strip().title() or "-",
            "holder": (row.get("nome_portador") or row.get("portador") or "-").strip().title(),
            "issued": issued.isoformat() if issued else None,
            "due": due.isoformat() if due else None,
            "paid_on": paid_on.isoformat() if paid_on else None,
            "status": "em dia" if on_time else (f"{late} dia{'s' if late != 1 else ''} de atraso" if late else "sem vencimento"),
            "status_tone": "good" if on_time else ("bad" if late and late > 30 else "warn"),
            "late": late,
            "term": (paid_on - issued).days if paid_on and issued else None,
            "paid_month": month_title(paid_on.replace(day=1)) if paid_on else None,
            "value": round(float(value), 2),
            "__tone": None if on_time else ("bad" if late and late > 30 else "warn"),
            "_on_time": on_time,
            "_value": value,
        })
    total = sum((title["_value"] for title in titles), ZERO)
    on_time_value = sum((title["_value"] for title in titles if title["_on_time"]), ZERO)
    late_titles = [title for title in titles if not title["_on_time"]]
    weighted_delay = sum((title["_value"] * title["late"] for title in titles if title["late"] is not None), ZERO)
    weighted_term = sum((title["_value"] * title["term"] for title in titles if title["term"] is not None), ZERO)
    for title in titles:
        del title["_on_time"], title["_value"]
    titles.sort(key=lambda title: title["value"], reverse=True)
    section = {"columns": PAID_COLUMNS, "groupBy": ["customer", "rep", "holder", "paid_on"], "sort": {"key": "value", "dir": "desc"}}
    term = weighted_term / total if total else None
    return {
        "title": heading,
        "subtitle": subtitle,
        "summary": [
            {"label": "Recebido", "value": dna._money(total, compact=False), "note": f"{len(titles)} título(s)"},
            {"label": "Em dia", "value": dna._money(on_time_value, compact=False),
             "note": f"{dna._pct(on_time_value / total)} do valor" if total else "", "tone": "good"},
            {"label": "Com atraso", "value": dna._money(total - on_time_value, compact=False),
             "note": f"{len(late_titles)} título(s)", "tone": "warn" if late_titles else None},
            _delay_fact(weighted_delay / total if total else None),
            {"label": "Prazo médio", "value": f"{float(term):.1f} dia(s)".replace(".", ",") if term is not None else "–",
             "note": "da emissão ao pagamento · ponderado pelo valor"},
        ],
        "sections": [
            {"key": "all", "label": "Todos", "rows": titles, "empty": empty, **section},
            {"key": "late", "label": "Com atraso", "rows": [title for title in titles if title["__tone"]], "empty": all_on_time, **section},
        ],
    }


# Card Créditos de clientes: os mesmos títulos do CREDITS_SQL (crédito em aberto: devolução,
# adiantamento, pagamento antecipado), um por linha.
CREDIT_TITLES_SQL = """
SELECT T.CODEMP AS EMPRESA, T.CODFIL AS FILIAL, T.NUMTIT AS TITULO, T.CODTPT AS TIPO, TP.DESTPT AS DESCRICAO,
       T.CODCLI AS CLIENTE, T.CODREP AS REPRESENTANTE, T.DATEMI AS EMISSAO, NVL(T.VCTPRO, T.VCTORI) AS VENCIMENTO,
       T.VLRABE AS ABERTO, T.VLRORI AS ORIGINAL, C.NOMCLI AS NOME, C.CIDCLI AS CIDADE, C.SIGUFS AS UF,
       R.NOMREP AS NOME_REPRESENTANTE
FROM E301TCR T
JOIN E002TPT TP ON TP.CODTPT = T.CODTPT AND TP.RECSOM = 'C'
LEFT JOIN E085CLI C ON C.CODCLI = T.CODCLI
LEFT JOIN E090REP R ON R.CODREP = T.CODREP
WHERE T.SITTIT IN ('AB', 'AC', 'AP') AND T.VLRABE > 0
"""

CREDIT_COLUMNS = [
    {"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"},
    {"key": "title", "label": "Título", "nowrap": True},
    {"key": "kind", "label": "Tipo"},
    {"key": "rep", "label": "Representante"},
    {"key": "issued", "label": "Emissão", "type": "date", "nowrap": True},
    {"key": "age", "label": "Dias em aberto", "type": "int"},
    {"key": "original", "label": "Valor original", "type": "money", "total": True},
    {"key": "open", "label": "Em aberto", "type": "money", "total": True, "strong": True},
]


def credit_titles_detail(today=None, customer_url=None):
    today = today or date.today()
    rows = dna._run({"credits": (CREDIT_TITLES_SQL, {})})["credits"]
    titles, by_kind = [], {}
    for row in rows:
        value = dna._num(row.get("aberto"))
        if value <= 0:
            continue
        customer = dna._int(row.get("cliente"))
        issued = dna._date(row.get("emissao"))
        kind = (row.get("descricao") or row.get("tipo") or "").strip().title() or "-"
        rep = dna.primary_rep(row.get("representante"))
        by_kind[kind] = by_kind.get(kind, ZERO) + value
        titles.append({
            "customer": (row.get("nome") or "").strip() or f"Cliente {customer}",
            "customer_sub": " · ".join(part for part in (str(customer or ""), _place(row.get("cidade"), row.get("uf"))) if part),
            "customer_url": customer_url(customer) if customer_url and customer else None,
            "title": f"{dna._int(row.get('empresa'))}-{dna._int(row.get('filial'))}-{(row.get('titulo') or '').strip()}",
            "kind": kind,
            "rep": dna.REPRESENTATIVE_DISPLAY_NAMES.get(rep) or (row.get("nome_representante") or "").strip().title() or "-",
            "issued": issued.isoformat() if issued else None,
            "age": (today - issued).days if issued else None,
            "original": round(float(dna._num(row.get("original"))), 2),
            "open": round(float(value), 2),
            "_value": value,
        })
    total = sum((title.pop("_value") for title in titles), ZERO)
    kinds = sorted(by_kind.items(), key=lambda pair: pair[1], reverse=True)
    summary = [{"label": "Créditos em aberto", "value": dna._money(total, compact=False), "note": f"{len(titles)} título(s)"},
               {"label": "Clientes", "value": dna.int_display(len({title["customer_sub"] for title in titles}))}]
    summary += [{"label": kind, "value": dna._money(value, compact=False), "note": f"{dna._pct(value / total)} dos créditos" if total else ""}
                for kind, value in kinds[:2]]
    return {
        "title": "Créditos de clientes",
        "subtitle": "Títulos de crédito em aberto (devolução, adiantamento, pagamento antecipado) a compensar · empresa toda",
        "summary": summary,
        "sections": [{
            "key": "credits", "label": "Créditos", "columns": CREDIT_COLUMNS, "rows": titles,
            "groupBy": ["kind", "customer", "rep"], "sort": {"key": "open", "dir": "desc"}, "empty": "Nenhum crédito em aberto.",
        }],
    }
