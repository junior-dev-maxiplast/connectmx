"""
Recalcula os alertas proativos e importa as ações da IA de todos os painéis
ativos do catálogo do Dashes (não por usuário — o alerta é do painel, não
pessoal).

Hoje nada agenda este comando: não existe Celery/cron/Task Scheduler
configurado para o ConnectMX (ver docstring de `decision_center.py`). A
Central de Decisão já roda esta mesma checagem sozinha a cada carregamento de
página, então rodar este comando não é necessário para a tela funcionar —
ele existe para o dia em que alguém configurar um agendamento no servidor
(Windows Task Scheduler) e quiser os alertas frescos mesmo que ninguém abra a
tela, e é o lugar natural para plugar o envio por e-mail mais tarde
(reaproveitando o padrão de webhook fire-and-forget de
`_portal_send_ai_routing_webhook`, em `views.py`), sem precisar mexer na
view.

    python manage.py verificar_alertas_dashes
"""

from django.core.management.base import BaseCommand

from ...decision_center import import_ai_actions, refresh_alerts_for_dashboards
from ...models import Dashboard


class Command(BaseCommand):
    help = "Recalcula os alertas proativos e importa ações de IA de todos os painéis ativos do Dashes."

    def handle(self, *args, **options):
        dashboards = list(Dashboard.objects.filter(is_active=True))
        if not dashboards:
            self.stdout.write("Nenhum painel ativo no catálogo.")
            return

        refresh_alerts_for_dashboards(dashboards)
        created = import_ai_actions(dashboards)

        self.stdout.write(self.style.SUCCESS(
            f"Alertas recalculados para {len(dashboards)} painel(is); {created} ação(ões) nova(s) importada(s) da IA."
        ))
