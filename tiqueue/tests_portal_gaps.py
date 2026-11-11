from datetime import timedelta
from io import BytesIO, StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import PortalDemand, PortalDemandLog, PortalDemandMessage, PortalNotification
from .tests_portal_workflow import PortalWorkflowTestCase
from .views import _portal_flag_possible_duplicates, _portal_log_event, _portal_notify_reply


class PortalRequesterNotificationTests(PortalWorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.requester.email = "solicitante@maxiplast.com.br"
        self.requester.save(update_fields=["email"])
        self.demand = self._open_demand(requester=self.requester, status=PortalDemand.STATUS_ASSUMED)

    def test_waiting_for_an_answer_notifies_the_requester_in_app_and_by_email(self):
        mail.outbox = []
        _portal_log_event(self.demand, PortalDemandLog.EVENT_WAITING, actor=self.attendant, summary="Aguardando resposta")

        notification = PortalNotification.objects.get(recipient=self.requester, demand=self.demand)
        self.assertEqual(notification.title, "Aguardando sua resposta")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["solicitante@maxiplast.com.br"])
        self.assertIn(self.demand.protocol, mail.outbox[0].subject)

    def test_requester_is_not_notified_about_their_own_action(self):
        _portal_log_event(self.demand, PortalDemandLog.EVENT_CANCELLED, actor=self.requester, summary="Motivo")
        self.assertFalse(PortalNotification.objects.filter(recipient=self.requester, demand=self.demand).exists())

    def test_email_failure_does_not_block_the_in_app_notification(self):
        with patch("tiqueue.views.send_mail", side_effect=OSError("smtp fora")):
            _portal_log_event(self.demand, PortalDemandLog.EVENT_WAITING, actor=self.attendant, summary="x")
        self.assertTrue(PortalNotification.objects.filter(recipient=self.requester, demand=self.demand).exists())

    def test_attendant_reply_notifies_requester_but_internal_note_does_not(self):
        public = PortalDemandMessage(demand=self.demand, author_role=PortalDemandMessage.ROLE_ATTENDANT, message="Vou olhar.")
        _portal_notify_reply(self.demand, public, self.attendant)
        self.assertTrue(PortalNotification.objects.filter(recipient=self.requester, title="Nova resposta da equipe de TI").exists())

        note = PortalDemandMessage(
            demand=self.demand, author_role=PortalDemandMessage.ROLE_ATTENDANT, is_internal=True, message="Nota do time."
        )
        before = PortalNotification.objects.count()
        _portal_notify_reply(self.demand, note, self.attendant)
        self.assertEqual(PortalNotification.objects.count(), before)

    def test_requester_reply_notifies_the_responsible_attendant(self):
        self.demand.assigned_to = self.attendant
        self.demand.save(update_fields=["assigned_to"])
        reply = PortalDemandMessage(demand=self.demand, author_role=PortalDemandMessage.ROLE_REQUESTER, message="Segue o print.")
        _portal_notify_reply(self.demand, reply, self.requester)
        self.assertTrue(PortalNotification.objects.filter(recipient=self.attendant, title="Nova resposta do solicitante").exists())


class PortalSlaAlertCommandTests(PortalWorkflowTestCase):
    def test_overdue_sla_alerts_the_attendant_once(self):
        demand = self._open_demand(
            status=PortalDemand.STATUS_ASSUMED,
            assigned_to=self.attendant,
            resolution_due_at=timezone.now() - timedelta(hours=2),
        )
        call_command("verificar_alertas_portal_sla", stdout=StringIO())
        call_command("verificar_alertas_portal_sla", stdout=StringIO())

        alerts = PortalNotification.objects.filter(
            recipient=self.attendant, demand=demand, title="SLA de resolução estourado"
        )
        self.assertEqual(alerts.count(), 1)

    def test_demand_waiting_for_the_requester_does_not_run_sla(self):
        self._open_demand(
            status=PortalDemand.STATUS_WAITING,
            assigned_to=self.attendant,
            resolution_due_at=timezone.now() - timedelta(hours=2),
        )
        call_command("verificar_alertas_portal_sla", stdout=StringIO())
        self.assertFalse(PortalNotification.objects.filter(recipient=self.attendant, title__startswith="SLA").exists())


class RequesterCancelTests(PortalWorkflowTestCase):
    def _cancel(self, demand, reason="abri por engano"):
        return self.client.post(
            demand.get_absolute_url(),
            {"form_type": "workflow", "workflow_action": "requester_cancel", "cancel_reason": reason},
            follow=True,
        )

    def test_requester_cancels_a_pending_demand_with_a_reason(self):
        demand = self._open_demand(requester=self.requester, status=PortalDemand.STATUS_PENDING)
        self.client.force_login(self.requester)
        self._cancel(demand)
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_CANCELLED)
        self.assertTrue(
            PortalDemandLog.objects.filter(demand=demand, event_type=PortalDemandLog.EVENT_CANCELLED, summary__contains="abri por engano").exists()
        )

    def test_requester_cannot_cancel_after_an_attendant_assumed_it(self):
        demand = self._open_demand(requester=self.requester, status=PortalDemand.STATUS_ASSUMED, assigned_to=self.attendant)
        self.client.force_login(self.requester)
        self._cancel(demand)
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_ASSUMED)

    def test_cancel_needs_a_real_reason(self):
        demand = self._open_demand(requester=self.requester, status=PortalDemand.STATUS_PENDING)
        self.client.force_login(self.requester)
        self._cancel(demand, reason="ok")
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_PENDING)

    def test_other_requesters_cannot_cancel_it(self):
        demand = self._open_demand(requester=self.requester, status=PortalDemand.STATUS_PENDING)
        self.client.force_login(self.outsider)
        self._cancel(demand)
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_PENDING)


class PossibleDuplicateTests(PortalWorkflowTestCase):
    def test_similar_open_demand_from_another_requester_is_flagged(self):
        mine = self._open_demand(requester=self.requester, title="Coletor sem rede na doca", status=PortalDemand.STATUS_PENDING)
        other = self._open_demand(requester=self.outsider, title="Coletor da doca sem rede", status=PortalDemand.STATUS_PENDING)
        unrelated = self._open_demand(requester=self.outsider, title="Acesso ao sistema financeiro", status=PortalDemand.STATUS_PENDING)

        _portal_flag_possible_duplicates([mine, unrelated])
        self.assertEqual(mine.possible_duplicate_protocol, other.protocol)
        self.assertEqual(unrelated.possible_duplicate_protocol, "")


class PortalExportTests(PortalWorkflowTestCase):
    def test_attendant_exports_csv_with_semicolon_and_bom(self):
        demand = self._open_demand(requester=self.requester, title="Coletor sem rede")
        self.client.force_login(self.attendant)
        response = self.client.get(reverse("portalDemandsExport"), {"formato": "csv", "status": "todos"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        body = response.content.decode("utf-8-sig")
        self.assertIn(f"{demand.protocol};Coletor sem rede", body)

    def test_attendant_exports_xlsx_with_header_row(self):
        self._open_demand(requester=self.requester, title="Coletor sem rede")
        self.client.force_login(self.attendant)
        response = self.client.get(reverse("portalDemandsExport"), {"formato": "xlsx", "status": "abertos"})

        self.assertIn("spreadsheetml", response["Content-Type"])
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sheet["A1"].value, "Protocolo")

    def test_requester_cannot_export_the_queue(self):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("portalDemandsExport"), {"formato": "csv"})
        self.assertEqual(response.status_code, 403)

    def test_invalid_date_is_rejected_with_a_message(self):
        self.client.force_login(self.attendant)
        response = self.client.get(reverse("portalDemandsExport"), {"inicio": "31/12/2026"})
        self.assertEqual(response.status_code, 400)
