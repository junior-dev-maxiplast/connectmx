"""Carga do orçamento de vendas (kg e R$ por unidade, família e mês) no ConnectMX.

    manage.py importar_orcamento --pbix-consultas "C:\\ConnectMX\\extracao_pbix\\2_ORCAMENTO_2026\\consultas"
                                 --planilha "Projeção de Vendas 2027 por Família.xlsx"
    manage.py importar_orcamento --arquivo tiqueue/data/sales_budget.json

Lê as tabelas digitadas no PBIX (2025 e 2026, com o ponto de equilíbrio) e a planilha de
projeção (2027), grava o JSON em `tiqueue/data/sales_budget.json` e troca o orçamento dos anos
lidos no banco do ConnectMX (o ERP não é tocado). Rodar de novo só substitui os mesmos anos.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from tiqueue import sales_budget, sales_goals
from tiqueue.models import SalesBreakEven, SalesBudget


class Command(BaseCommand):
    help = "Carrega o orçamento de vendas (kg e R$) do PBIX e da planilha de projeção no ConnectMX."

    def add_arguments(self, parser):
        parser.add_argument("--pbix-consultas", dest="pbix", help="Pasta 'consultas' da extração do PBIX de Orçamento.")
        parser.add_argument("--planilha", help="Planilha de projeção de vendas por família (ano seguinte).")
        parser.add_argument("--arquivo", help="JSON já gerado (em vez do PBIX e da planilha).")
        parser.add_argument("--sem-json", action="store_true", dest="no_json", help="Não regrava tiqueue/data/sales_budget.json.")

    def handle(self, *args, **options):
        if not (options["pbix"] or options["planilha"] or options["arquivo"]):
            raise CommandError("Informe --pbix-consultas e/ou --planilha, ou --arquivo.")
        try:
            if options["arquivo"]:
                data = sales_budget.load_fixture(options["arquivo"])
            else:
                data = {"budget": [], "break_even": []}
                for loader, source in ((sales_budget.budget_from_pbix, options["pbix"]), (sales_budget.budget_from_projection, options["planilha"])):
                    if not source:
                        continue
                    part = loader(source)
                    for unit, group, *rest in part["unmapped"]:
                        self.stdout.write(self.style.WARNING(f"Sem família de meta (fica de fora): {unit} {group} {rest}"))
                    data["budget"].extend(part["budget"])
                    data["break_even"].extend(part["break_even"])
        except (OSError, ValueError, sales_goals.SalesGoalsError) as exc:
            raise CommandError(str(exc)) from exc
        if not data["budget"]:
            raise CommandError("Nenhuma linha de orçamento encontrada.")

        totals = defaultdict(lambda: [Decimal(0), Decimal(0), set()])
        for row in data["budget"]:
            item = totals[(row["year"], row["branch"])]
            item[0] += Decimal(row["weight_kg"])
            item[1] += Decimal(row["value"])
            item[2].add(row["basis"])
        for (year, branch), (weight, value, bases) in sorted(totals.items()):
            money = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            self.stdout.write(
                f"{year} unidade {branch} ({sales_budget.UNIT_NAMES.get(branch, '?')}): "
                f"{sales_goals.format_kg(weight, 3)}, R$ {money} [{', '.join(sorted(bases))}]"
            )
        if not options["no_json"] and not options["arquivo"]:
            sales_budget.write_fixture(data)
            self.stdout.write(f"JSON gravado em {sales_budget.FIXTURE_PATH}.")
        budget_rows, break_even_rows, years = sales_budget.replace_budget(SalesBudget, SalesBreakEven, data)
        self.stdout.write(self.style.SUCCESS(
            f"Orçamento de {', '.join(str(year) for year in years)}: {budget_rows} linhas e {break_even_rows} meses de ponto de equilíbrio."
        ))
