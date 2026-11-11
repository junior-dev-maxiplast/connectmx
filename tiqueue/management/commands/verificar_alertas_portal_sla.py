from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from tiqueue.models import PortalDemand, PortalNotification
from tiqueue.views import _portal_notify


class Command(BaseCommand):
    help = (
        "Avisa o atendente responsável quando o SLA de um chamado do portal está "
        "vencendo ou estourado. Agende para rodar a cada 10 minutos."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--horas",
            type=float,
            default=1.0,
            help="Antecedência do aviso de vencimento, em horas (padrão: 1).",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        window = timedelta(hours=options["horas"])
        # Chamado em espera do solicitante não corre SLA, então fica de fora.
        demands = PortalDemand.objects.filter(
            status__in=[PortalDemand.STATUS_PENDING, PortalDemand.STATUS_ASSUMED]
        ).select_related("assigned_to", "requester", "sla_policy__default_attendant")

        sent = 0
        for demand in demands:
            recipient = demand.assigned_to
            if recipient is None and demand.sla_policy_id:
                recipient = demand.sla_policy.default_attendant
            if recipient is None:
                continue

            alerts = []
            due = demand.resolution_due_at
            if due and due < now:
                alerts.append(("SLA de resolução estourado", f"Prazo era {timezone.localtime(due):%d/%m %H:%M}."))
            elif due and due - now <= window:
                alerts.append(("SLA de resolução vence em breve", f"Prazo: {timezone.localtime(due):%d/%m %H:%M}."))
            first = demand.first_response_due_at
            if first and demand.first_response_at is None and first < now:
                alerts.append(("1ª resposta atrasada", f"Prazo era {timezone.localtime(first):%d/%m %H:%M}."))

            for title, detail in alerts:
                # Um aviso por tipo e por chamado: rodar o comando de novo não repete.
                if PortalNotification.objects.filter(recipient=recipient, demand=demand, title=title).exists():
                    continue
                _portal_notify(recipient, demand, title, f"{demand.protocol} — {demand.title}. {detail}")
                sent += 1

        self.stdout.write(self.style.SUCCESS(f"{sent} aviso(s) de SLA enviado(s)."))
