"""Pré-carga das metas de peso (USU_TORCMET) a partir da planilha de projeção de vendas por família.

    manage.py carregar_metas_planilha --arquivo "Projeção de Vendas 2027 por Família.xlsx"
    manage.py carregar_metas_planilha --arquivo ... --gravar
    manage.py carregar_metas_planilha --arquivo ... --conferir

Sem --gravar só mostra o que mudaria (nada é escrito no ERP). A carga é
idempotente: rodar de novo não duplica lançamentos, só ajusta o que mudou na
planilha. Células sem meta na planilha não apagam o que foi digitado na tela.
Ver `tiqueue/sales_goals.py`.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from tiqueue import sales_goals


def _kg(value):
    return sales_goals.format_kg(value, 3)


class Command(BaseCommand):
    help = "Carrega na USU_TORCMET as metas de peso da planilha de projeção (sem --gravar, só simula)."

    def add_arguments(self, parser):
        parser.add_argument("--arquivo", required=True, help="Planilha .xlsx de projeção de vendas por família.")
        parser.add_argument("--ano", type=int, help="Ano das metas (padrão: o ano no nome da aba).")
        parser.add_argument("--esquema", help=f"Esquema da USU_TORCMET (padrão: {sales_goals.SCHEMA_ENV} ou {sales_goals.DEFAULT_SCHEMA}).")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--gravar", action="store_true", help="Grava no ERP. Sem isso, só mostra o que mudaria.")
        mode.add_argument("--conferir", action="store_true", help="Só compara a tabela com a planilha, célula a célula.")

    def handle(self, *args, **options):
        try:
            projection = sales_goals.read_projection(options["arquivo"])
            mapped = sales_goals.map_projection(projection, options["ano"])
            table = sales_goals.table_ref(options["esquema"])
        except sales_goals.SalesGoalsError as exc:
            raise CommandError(str(exc)) from exc

        self._print_projection(projection, mapped, table)
        if mapped.blocking:
            lines = "; ".join(f"{unit} / {group} ({_kg(total)})" for unit, group, total in mapped.blocking)
            raise CommandError(f"Linhas da planilha sem filial ou família conhecida: {lines}. Nada foi gravado.")

        try:
            if options["conferir"]:
                self._verify(mapped, options["esquema"])
                return
            plan = sales_goals.preload(mapped, dry_run=not options["gravar"], schema_name=options["esquema"])
        except sales_goals.SalesGoalsError as exc:
            raise CommandError(str(exc)) from exc

        counts = plan.counts()
        self.stdout.write("")
        self.stdout.write(
            f"{'Gravação' if options['gravar'] else 'Simulação'} em {table}: {counts['inserir']} a inserir, "
            f"{counts['atualizar']} a atualizar, {counts['apagar']} a apagar, {counts['iguais']} já iguais."
        )
        for operation in plan.operations:
            if operation.kind == "insert":
                continue
            self.stdout.write(
                f"  {operation.kind:<7} {operation.company}/{operation.branch} {operation.family} mês {operation.month:02d} "
                f"lançamento {operation.codlan}: {_kg(operation.previous or 0)} -> {_kg(operation.value or 0)}"
            )
        if not options["gravar"]:
            self.stdout.write(self.style.WARNING("Nada foi gravado. Para gravar, rode de novo com --gravar."))
            return
        self._verify(mapped, options["esquema"])

    def _print_projection(self, projection, mapped, table):
        out = self.stdout.write
        out(f"Planilha: aba {projection.sheet!r} ({projection.layout}), {len(projection.rows)} linhas, ano {mapped.year}.")
        out(f"Destino: {table}{' (ambiente de simulação)' if sales_goals.is_simulation(table.split('.')[0]) else ''}.")
        out("")
        out("Família por filial (kg/mês de jan a dez; grupos da planilha somados):")
        for (company, branch, family), months in mapped.cells.items():
            total = sum(months, Decimal(0))
            if not total:
                continue
            groups = " + ".join(mapped.sources.get((company, branch, family), []))
            values = " | ".join(f"{value:.3f}" for value in months)
            out(f"  {company}/{branch} {family} [{groups}] {values} = {_kg(total)}")
        empty = [key for key, months in mapped.cells.items() if not any(months)]
        if empty:
            out(f"  Sem meta na planilha (não entram): {', '.join(f'{c}/{b} {f}' for c, b, f in empty)}")
        for unit, group, total in mapped.unmapped:
            out(f"  Sem mapeamento: {unit} / {group} ({_kg(total)})")
        out("")
        out("Total por filial: " + " · ".join(f"{c}/{b} {_kg(total)}" for (c, b), total in mapped.branch_totals().items()))
        out("Total por mês: " + " · ".join(
            f"{sales_goals.MONTH_LABELS[index]} {_kg(total)}" for index, total in enumerate(mapped.month_totals())
        ))
        out(
            f"Total: {_kg(mapped.total)} em {mapped.goal_cells()} células "
            f"(planilha: {_kg(mapped.sheet_total)}; diferença {_kg(mapped.total - mapped.sheet_total)} "
            "vem do arredondamento a 3 casas)."
        )

    def _verify(self, mapped, schema_name):
        checked, differences = sales_goals.verify(mapped, schema_name=schema_name)
        if differences:
            self.stdout.write(self.style.ERROR(f"Conferência: {len(differences)} de {checked} células diferem da planilha:"))
            for item in differences[:50]:
                self.stdout.write(
                    f"  {item['empresa']}/{item['filial']} {item['familia']} mês {item['mes']:02d}: "
                    f"planilha {item['planilha']}, tabela {item['tabela']} ({item['lancamentos']} lançamento(s))"
                )
            raise CommandError("A tabela não bate com a planilha.")
        self.stdout.write(self.style.SUCCESS(f"Conferência: as {checked} células da planilha batem com a tabela (um lançamento cada)."))
