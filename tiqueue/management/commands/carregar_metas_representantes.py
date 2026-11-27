"""Pré-carga das metas por representante (USU_TORCGER) a partir da meta das famílias (USU_TORCMET).

    manage.py carregar_metas_representantes --ano 2027
    manage.py carregar_metas_representantes --ano 2027 --gravar
    manage.py carregar_metas_representantes --ano 2027 --conferir

O % de cada representante é a participação dele no peso faturado no período-base (padrão: o
ano anterior ao da meta), na mesma filial do orçamento e no mesmo grupo de produto que a
família representa; o peso de cada mês é a meta da família × %. Sem --gravar só mostra o
que mudaria. Família que já tem representantes fica como está; --recalcular troca os %
gravados pelos calculados. Ver `tiqueue/sales_goals_reps.py`.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from tiqueue import sales_goals, sales_goals_reps


def _kg(value):
    return sales_goals.format_kg(value, 3)


def _pct(value):
    return f"{Decimal(value or 0):.4f}".replace(".", ",") + "%"


class Command(BaseCommand):
    help = "Carrega na USU_TORCGER o % e o peso de cada representante na meta de cada família (sem --gravar, só simula)."

    def add_arguments(self, parser):
        parser.add_argument("--ano", type=int, required=True, help="Ano das metas.")
        parser.add_argument("--base", help="Período-base do %%: um ano (2026) ou um intervalo (2025-2026). Padrão: o ano anterior.")
        parser.add_argument("--empresa", type=int, help="Só esta empresa.")
        parser.add_argument("--filial", type=int, help="Só esta filial.")
        parser.add_argument("--recalcular", action="store_true", help="Troca os %% das famílias que já têm representantes.")
        parser.add_argument("--sem-inativos", action="store_true", dest="sem_inativos",
                            help="Deixa de fora os representantes inativos no ERP e redistribui o %% entre os demais.")
        parser.add_argument("--esquema", help=f"Esquema das tabelas (padrão: {sales_goals.SCHEMA_ENV} ou {sales_goals.DEFAULT_SCHEMA}).")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--gravar", action="store_true", help="Grava no ERP. Sem isso, só mostra o que mudaria.")
        mode.add_argument("--conferir", action="store_true", help="Só confere: por família e mês, soma dos %% = 100 e dos pesos = meta.")

    def handle(self, *args, **options):
        year, schema_name = options["ano"], options["esquema"]
        try:
            table = sales_goals_reps.ger_table_ref(schema_name)
            if options["conferir"]:
                self._verify(year, schema_name)
                return
            first, last = sales_goals_reps.parse_base(options["base"], year)
            reps = sales_goals_reps.representatives()
            plan = sales_goals_reps.preload_reps(
                year, company=options["empresa"], branch=options["filial"], base=options["base"],
                replace=options["recalcular"], exclude_inactive=options["sem_inativos"],
                dry_run=not options["gravar"], schema_name=schema_name,
            )
        except sales_goals.SalesGoalsError as exc:
            raise CommandError(str(exc)) from exc

        out = self.stdout.write
        base_label = f"{first}" if first == last else f"{first} a {last}"
        out(f"Metas por representante de {year} em {table}; % pelo peso faturado em {base_label}.")
        skipped = set(plan.skipped)
        by_key = defaultdict(list)
        for operation in plan.operations:
            by_key[(operation.company, operation.branch, operation.family)].append(operation)
        inactive_rows, without_billing = [], []
        for (company, branch, family), share in sorted(plan.shares.items()):
            percents = share["percents"]
            status = " (já tinha representantes: mantida)" if (company, branch, family) in skipped else ""
            groups = " + ".join(share["groups"]) or "a própria família"
            out("")
            out(f"{company}/{branch} {family} [{groups}]: base {_kg(share['total'])}, {len(percents)} representante(s){status}")
            if not percents:
                without_billing.append(f"{company}/{branch} {family}")
                continue
            ops = by_key.get((company, branch, family), [])
            yearly = defaultdict(Decimal)
            for operation in ops:
                if operation.kind != "delete":
                    yearly[operation.rep] += operation.weight or Decimal(0)
            for rep, percent in sorted(percents.items(), key=lambda pair: (-pair[1], pair[0])):
                info = reps.get(rep) or {}
                name = info.get("name") or f"Representante {rep}"
                flag = "" if info.get("active", True) else "  [INATIVO no ERP]"
                if info and not info.get("active", True):
                    inactive_rows.append(f"{company}/{branch} {family} {rep} {name} {_pct(percent)}")
                weight = f" -> {_kg(yearly[rep])} no ano" if rep in yearly else ""
                out(f"   {rep:>6} {name[:38]:<38} {_pct(percent):>10}  (faturou {_kg(share['weights'][rep])}){weight}{flag}")
            out(f"   {'':>6} {'soma':<38} {_pct(sum(percents.values(), Decimal(0))):>10}")

        counts = plan.counts()
        out("")
        out(
            f"{'Gravação' if options['gravar'] else 'Simulação'} em {table}: {counts['inserir']} a inserir, "
            f"{counts['atualizar']} a atualizar, {counts['apagar']} a apagar, {counts['iguais']} já iguais."
        )
        if skipped:
            out(f"Famílias que já tinham representantes e ficaram como estão: {len(skipped)} (use --recalcular para trocar).")
        if without_billing:
            out(self.style.WARNING(f"Sem faturamento no período-base (ficam sem representante): {', '.join(without_billing)}."))
        if inactive_rows:
            out(self.style.WARNING("Representantes inativos no ERP que receberam %: " + "; ".join(inactive_rows) + "."))
        if not options["gravar"]:
            out(self.style.WARNING("Nada foi gravado. Para gravar, rode de novo com --gravar."))
            return
        self._verify(year, schema_name)

    def _verify(self, year, schema_name):
        checked, differences = sales_goals_reps.verify_reps(year, schema_name=schema_name)
        if differences:
            self.stdout.write(self.style.ERROR(f"Conferência: {len(differences)} de {checked} famílias-mês não fecham:"))
            for item in differences[:50]:
                self.stdout.write(
                    f"  {item['empresa']}/{item['filial']} {item['familia']} mês {item['mes']:02d}: "
                    f"soma {item['percentual']}% e {item['peso']} kg; meta {item['meta']} kg"
                )
            raise CommandError("Os representantes não fecham a meta das famílias.")
        self.stdout.write(self.style.SUCCESS(
            f"Conferência: em {checked} famílias-mês os % somam 100 e o peso dos representantes fecha a meta."
        ))
