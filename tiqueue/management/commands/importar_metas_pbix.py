"""Recarrega as metas por representante a partir da extração do PBIX de Orçamento.

Uso:
    python manage.py importar_metas_pbix --pasta C:\\ConnectMX\\extracao_pbix\\2_ORCAMENTO_2026\\consultas
    python manage.py importar_metas_pbix --pasta ... --exportar   (também regrava o JSON versionado)
    python manage.py importar_metas_pbix                         (usa o JSON versionado)

As metas dos anos presentes na carga são substituídas por inteiro; anos que não
estão na carga ficam como estão.
"""

import os
from django.core.management.base import BaseCommand, CommandError

from tiqueue.models import RepresentativeGoal
from tiqueue.representative_goals import FIXTURE_PATH, goals_from_consultas_dir, load_fixture, replace_goals, write_fixture


class Command(BaseCommand):
    help = "Importa as metas de venda por representante embutidas no PBIX de Orçamento."

    def add_arguments(self, parser):
        parser.add_argument("--pasta", help="Pasta `consultas` da extração do PBIX (arquivos .m).")
        parser.add_argument("--arquivo", help="JSON de metas no formato de tiqueue/data/representative_goals.json (o arquivo não fica no repositório).")
        parser.add_argument(
            "--exportar",
            action="store_true",
            help="Regrava tiqueue/data/representative_goals.json com o que foi lido da pasta.",
        )

    def handle(self, *args, **options):
        folder = options.get("pasta")
        if folder:
            goals = goals_from_consultas_dir(folder)
            if not goals:
                raise CommandError("Nenhuma meta encontrada nos arquivos .m da pasta informada.")
            if options.get("exportar"):
                write_fixture(goals)
                self.stdout.write("JSON de metas regravado.")
        elif options.get("arquivo"):
            if not os.path.exists(options["arquivo"]):
                raise CommandError(f"Arquivo não encontrado: {options['arquivo']}")
            goals = load_fixture(options["arquivo"])
        else:
            if not os.path.exists(FIXTURE_PATH):
                raise CommandError("Sem o JSON de metas: copie-o para tiqueue/data/representative_goals.json ou use --arquivo ou --pasta.")
            goals = load_fixture()

        total, years = replace_goals(RepresentativeGoal, goals)
        representatives = len({goal["rep_code"] for goal in goals})
        self.stdout.write(
            self.style.SUCCESS(
                f"{total} metas importadas ({representatives} representantes, ano(s) {', '.join(map(str, years))})."
            )
        )
