"""
Vocabulário de período dos painéis do ConnectMX Dashes.

O BI do TI (MySQL do SM) e o BI de Viagens (Oracle do ERP) ofereciam a mesma
lista de recortes em duas cópias. Aqui ela é uma só — e é o lugar onde entra a
distinção que os dois painéis precisam:

* **janela móvel** (`months`): "3 meses" é de hoje para trás, e o resultado muda
  todo dia. É o recorte de tendência.
* **mês fechado** (`start`/`end`): "mês passado" é um intervalo com começo e fim,
  que não se mexe mais depois que o mês vira. É o recorte de fechamento — o que
  se leva para uma reunião e se compara com o mês anterior.

O intervalo do mês fechado é calculado aqui, em Python, e vai para o banco como
parâmetro. Fazer essa conta em SQL exigiria uma versão para o MySQL e outra para
o Oracle, e a do MySQL precisaria de `DATE_FORMAT(..., '%Y-%m-01')` — cujo `%`
colide com o marcador de parâmetro do driver.
"""

from datetime import date


PERIOD_CHOICES = [
    ("1", "1 mês", 1),
    ("3", "3 meses", 3),
    ("6", "6 meses", 6),
    ("12", "12 meses", 12),
    ("24", "24 meses", 24),
    ("mes-anterior", "Mês passado", None),
    ("all", "Tudo", None),
]

PREVIOUS_MONTH_KEY = "mes-anterior"
DEFAULT_PERIOD_KEY = "12"

# Recortes de um mês: nesses a série do painel é diária. Agrupar por competência
# renderiza uma barra só, que não diz nada que o KPI logo acima já não diga — a
# leitura útil de um mês é o ritmo dentro dele.
DAILY_SERIES_KEYS = {"1", PREVIOUS_MONTH_KEY}

# Recorte sem filtro de data, para as agregações que ignoram o período de
# propósito (o backlog do BI do TI, a fila de viagens abertas).
ALL_TIME = {
    "key": "all", "label": "Tudo", "months": None,
    "start": None, "end": None, "granularity": "month",
}


def previous_month_range(today=None):
    """Primeiro dia do mês passado e primeiro dia deste mês (fim exclusivo).

    O fim é exclusivo para não depender de quantos dias o mês teve nem da hora
    gravada no registro: `>= 01/07` e `< 01/08` pega julho inteiro, incluindo um
    lançamento de 31/07 às 23h59.
    """
    # `date.today()` e não `timezone.localdate()`: o projeto roda com TIME_ZONE
    # em UTC, e quem pede "mês passado" às 21h de 31/08 no Brasil não quer ver
    # setembro porque em UTC o dia já virou.
    today = today or date.today()
    first_of_this_month = today.replace(day=1)
    previous_day = first_of_this_month - _ONE_DAY
    return previous_day.replace(day=1), first_of_this_month


_ONE_DAY = date(2000, 1, 2) - date(2000, 1, 1)


def resolve_period(raw_value, today=None):
    """Normaliza o filtro de período vindo da querystring.

    Devolve sempre as mesmas chaves, para quem consome não precisar saber de que
    tipo é o recorte: `months` preenchido é janela móvel, `start`/`end`
    preenchidos são mês fechado, e nenhum dos dois é "tudo".
    """
    value = (raw_value or DEFAULT_PERIOD_KEY).strip()

    if value == PREVIOUS_MONTH_KEY:
        start, end = previous_month_range(today)
        range_label = start.strftime("%m/%Y")
        return {
            "key": PREVIOUS_MONTH_KEY,
            "label": "Mês passado",
            "range_label": range_label,
            "full_label": f"Mês passado ({range_label})",
            "months": None,
            "start": start,
            "end": end,
            "granularity": "day",
        }

    for key, label, months in PERIOD_CHOICES:
        if key == value:
            return {
                "key": key,
                "label": label,
                "range_label": "",
                "full_label": label,
                "months": months,
                "start": None,
                "end": None,
                "granularity": "day" if key in DAILY_SERIES_KEYS else "month",
            }

    return resolve_period(DEFAULT_PERIOD_KEY, today)


def period_choices():
    """Opções para o filtro da tela, na ordem em que aparecem."""
    return [{"key": key, "label": label} for key, label, _months in PERIOD_CHOICES]


def series_label(value, granularity, year_digits=4):
    """Rótulo curto de um ponto da série: '15/07' no diário, '07/2026' no mensal.

    Aceita o valor como veio do banco — `date` do MySQL ou texto do Oracle — e
    devolve sempre texto, porque quem chama só quer pintar o eixo. `year_digits`
    existe porque cada painel já tinha o seu formato de competência e mudá-lo
    seria mexer numa tela que ninguém pediu para mexer.
    """
    text = str(value or "")
    if granularity == "day":
        parts = text.split("-")
        return f"{parts[2][:2]}/{parts[1]}" if len(parts) >= 3 else text
    year, _, month = text.partition("-")
    if not (year and month):
        return text
    return f"{month}/{year[-year_digits:]}"


def mark_series_ticks(series, max_labels=12):
    """Marca quais pontos da série levam rótulo no eixo.

    Uma série diária de 31 pontos não cabe com um rótulo embaixo de cada barra:
    "15/07" em coluna de 30px vira borrão. Em vez de encolher a fonte até o
    ilegível, só uma barra a cada N mostra a data — as outras continuam lá, com
    a informação no `title` da barra.
    """
    total = len(series)
    step = max(1, -(-total // max_labels))
    for index, item in enumerate(series):
        item["tick"] = index % step == 0 or index == total - 1
    return series
