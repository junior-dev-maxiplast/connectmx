"""Testes do fluxo de helpdesk do portal de chamados.

Cobrem o que passou a existir com o papel de atendente: quem pode atender sem
ser administrador, a pausa de SLA enquanto o chamado espera o solicitante, a
reabertura, o setor gravado na abertura e a abertura em nome de terceiros.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    PortalDemand,
    PortalDemandLog,
    PortalDemandMessage,
    PortalDemandSlaPolicy,
    PortalRequesterAccount,
    PortalRequesterCollaborator,
    PortalRequesterSector,
    TaskGroup,
    TaskType,
    userQueue,
)


class PortalWorkflowTestCase(TestCase):
    """Base com um solicitante cadastrado, um atendente puro e um admin."""

    def setUp(self):
        User = get_user_model()

        self.attendant = User.objects.create_user(
            userId="52001",
            username="fila.atendente",
            email="fila.atendente@example.com",
            nameUser="Atendente da Fila",
            password="pw123456",
            is_support_attendant=True,
        )
        self.admin = User.objects.create_user(
            userId="52002",
            username="fila.admin",
            email="fila.admin@example.com",
            nameUser="Administrador",
            password="pw123456",
            is_system_admin=True,
        )
        self.outsider = User.objects.create_user(
            userId="52003",
            username="fila.outro",
            email="fila.outro@example.com",
            nameUser="Usuario Comum",
            password="pw123456",
        )

        self.sector = PortalRequesterSector.objects.create(name="Expedição")
        self.collaborator = PortalRequesterCollaborator.objects.create(
            sector=self.sector,
            full_name="Solicitante da Expedicao",
            registration_code="EXP-001",
            email="solicitante.exp@example.com",
            role_title="Conferente",
            phone="4321",
        )
        self.requester = User.objects.create_user(
            userId="52004",
            username="fila.solicitante",
            email="fila.solicitante@example.com",
            nameUser="Solicitante da Expedicao",
            password="pw123456",
        )
        PortalRequesterAccount.objects.create(collaborator=self.collaborator, user=self.requester)

        # O atendente também precisa de cadastro: assim que existe uma conta de
        # solicitante, o portal passa a exigir cadastro de quem abre chamado.
        self.attendant_sector = PortalRequesterSector.objects.create(name="Tecnologia")
        self.attendant_collaborator = PortalRequesterCollaborator.objects.create(
            sector=self.attendant_sector,
            full_name="Atendente da Fila",
            registration_code="TI-001",
            email="atendente.ti@example.com",
        )
        PortalRequesterAccount.objects.create(
            collaborator=self.attendant_collaborator, user=self.attendant
        )

        self.group = TaskGroup.objects.create(name="Suporte")
        self.task_type = TaskType.objects.create(group=self.group, name="Acesso", color="#4567aa")

    def _open_demand(self, **overrides):
        payload = {
            "requester": self.requester,
            "requester_sector": self.sector,
            "title": "Coletor não conecta na rede",
            "description": "O coletor da doca 3 perdeu a conexão.",
            "task_group": self.group,
            "task_type": self.task_type,
            "priority_level": userQueue.PRIORITY_MEDIUM,
        }
        payload.update(overrides)
        return PortalDemand.objects.create(**payload)

    def _assume_as(self, demand, user):
        self.client.force_login(user)
        response = self.client.post(reverse("portalDemandAssume", args=[demand.id]))
        demand.refresh_from_db()
        return response

    def _workflow(self, demand, action, **extra):
        payload = {"form_type": "workflow", "workflow_action": action}
        payload.update(extra)
        response = self.client.post(demand.get_absolute_url(), payload, follow=True)
        demand.refresh_from_db()
        return response


class AttendantRoleTests(PortalWorkflowTestCase):
    def test_attendant_can_open_the_queue_without_being_admin(self):
        self.client.force_login(self.attendant)
        response = self.client.get(reverse("portalPendingDemandsPage"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_manage"])
        self.assertTrue(response.context["can_attend"])
        self.assertFalse(response.context["can_configure"])

    def test_attendant_cannot_reach_portal_configuration(self):
        self.client.force_login(self.attendant)
        for url_name in (
            "portalDemandFieldsConfigPage",
            "portalDemandSlaConfigPage",
            "portalDemandResponsesConfigPage",
            "portalRequesterAdminPage",
        ):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["can_manage"])
                self.assertIsNotNone(response.context["access_denied_message"])

    def test_attendant_assumes_demand_and_gets_queue_item(self):
        demand = self._open_demand()
        self._assume_as(demand, self.attendant)

        self.assertEqual(demand.status, PortalDemand.STATUS_ASSUMED)
        self.assertEqual(demand.assigned_to_id, self.attendant.id)
        self.assertIsNotNone(demand.linked_queue_item_id)
        self.assertTrue(
            PortalDemandLog.objects.filter(demand=demand, event_type=PortalDemandLog.EVENT_ASSUMED).exists()
        )

    def test_plain_user_cannot_assume(self):
        demand = self._open_demand()
        self.client.force_login(self.outsider)
        self.client.post(reverse("portalDemandAssume", args=[demand.id]))
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_PENDING)

    def test_attendant_reply_closes_first_response_sla(self):
        """Antes do papel de atendente, resposta de não-admin não fechava a meta."""
        demand = self._open_demand()
        self._assume_as(demand, self.attendant)

        self.client.post(
            demand.get_absolute_url(),
            {"form_type": "reply", "message": "Já estou verificando o coletor."},
            follow=True,
        )
        demand.refresh_from_db()

        message = demand.messages.latest("id")
        self.assertEqual(message.author_role, PortalDemandMessage.ROLE_ATTENDANT)
        self.assertIsNotNone(demand.first_response_at)

    def test_attendant_sees_the_queue_shortcut_on_the_portal_home(self):
        """A home checava is_system_admin no template e escondia o atalho."""
        self.client.force_login(self.attendant)
        response = self.client.get(reverse("portalDemandPage"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_manage_portal"])
        self.assertContains(response, reverse("portalPendingDemandsPage"))

    def test_attendant_can_write_an_internal_note(self):
        demand = self._open_demand()
        self._assume_as(demand, self.attendant)

        self.client.post(
            demand.get_absolute_url(),
            {"form_type": "reply", "message": "Trocar o switch da doca.", "is_internal": "on"},
            follow=True,
        )
        message = demand.messages.latest("id")
        self.assertTrue(message.is_internal)

        # Nota interna não pode vazar para o solicitante.
        self.client.force_login(self.requester)
        response = self.client.get(demand.get_absolute_url())
        self.assertNotContains(response, "Trocar o switch da doca.")

    def test_attendant_appears_as_transfer_target(self):
        demand = self._open_demand()
        self._assume_as(demand, self.admin)

        self.client.force_login(self.admin)
        response = self.client.get(demand.get_absolute_url())
        targets = list(response.context["transfer_form"].fields["target_attendant"].queryset)
        self.assertIn(self.attendant, targets)


class WaitingRequesterTests(PortalWorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.policy = PortalDemandSlaPolicy.objects.create(
            name="Padrão suporte",
            first_response_minutes=60,
            resolution_minutes=240,
        )

    def _demand_in_service(self):
        demand = self._open_demand(sla_policy=self.policy)
        demand.first_response_due_at = timezone.now() + timedelta(minutes=60)
        demand.resolution_due_at = timezone.now() + timedelta(minutes=240)
        demand.save(update_fields=["first_response_due_at", "resolution_due_at"])
        self._assume_as(demand, self.attendant)
        return demand

    def test_waiting_pauses_and_resume_pushes_the_deadline(self):
        demand = self._demand_in_service()
        original_resolution = demand.resolution_due_at

        self.client.force_login(self.attendant)
        self._workflow(demand, "wait")
        self.assertEqual(demand.status, PortalDemand.STATUS_WAITING)
        self.assertIsNotNone(demand.waiting_since)

        # Simula meia hora parada esperando o solicitante.
        PortalDemand.objects.filter(pk=demand.pk).update(
            waiting_since=timezone.now() - timedelta(minutes=30)
        )
        demand.refresh_from_db()

        self._workflow(demand, "resume")

        self.assertEqual(demand.status, PortalDemand.STATUS_ASSUMED)
        self.assertIsNone(demand.waiting_since)
        self.assertGreaterEqual(demand.sla_paused_minutes, 29)
        pushed = (demand.resolution_due_at - original_resolution).total_seconds() / 60
        self.assertGreaterEqual(pushed, 29)

    def test_requester_reply_resumes_the_demand_automatically(self):
        demand = self._demand_in_service()
        self.client.force_login(self.attendant)
        self._workflow(demand, "wait")

        self.client.force_login(self.requester)
        self.client.post(
            demand.get_absolute_url(),
            {"form_type": "reply", "message": "Reiniciei o coletor e continua igual."},
            follow=True,
        )
        demand.refresh_from_db()

        self.assertEqual(demand.status, PortalDemand.STATUS_ASSUMED)
        self.assertTrue(
            PortalDemandLog.objects.filter(demand=demand, event_type=PortalDemandLog.EVENT_RESUMED).exists()
        )

    def test_waiting_demand_is_not_flagged_as_breached(self):
        demand = self._demand_in_service()
        self.client.force_login(self.attendant)
        self._workflow(demand, "wait")

        # Prazo no passado: sem a pausa a demanda apareceria como crítica.
        PortalDemand.objects.filter(pk=demand.pk).update(
            first_response_due_at=timezone.now() - timedelta(hours=3),
            resolution_due_at=timezone.now() - timedelta(hours=2),
        )

        response = self.client.get(reverse("portalPendingDemandsPage"))
        self.assertEqual(response.context["header_stats"]["breached"], 0)

        response = self.client.get(reverse("portalPendingDemandsPage"), {"view": "abertos", "sla": "vencido"})
        self.assertEqual(list(response.context["tickets"]), [])

    def test_waiting_view_lists_the_paused_demand(self):
        demand = self._demand_in_service()
        self.client.force_login(self.attendant)
        self._workflow(demand, "wait")

        response = self.client.get(reverse("portalPendingDemandsPage"), {"view": "aguardando"})
        self.assertEqual([row.id for row in response.context["tickets"]], [demand.id])
        self.assertEqual(response.context["view_counts"]["aguardando"], 1)


class ReopenTests(PortalWorkflowTestCase):
    def _completed_demand(self):
        demand = self._open_demand(
            sla_policy=PortalDemandSlaPolicy.objects.create(
                name="Padrão", first_response_minutes=30, resolution_minutes=120
            )
        )
        self._assume_as(demand, self.attendant)
        self.client.force_login(self.attendant)
        self._workflow(demand, "complete")
        return demand

    def test_requester_can_reopen_a_completed_demand(self):
        demand = self._completed_demand()
        self.assertEqual(demand.status, PortalDemand.STATUS_COMPLETED)
        self.assertIsNone(demand.linked_queue_item_id)

        self.client.force_login(self.requester)
        self._workflow(demand, "reopen", reopen_reason="O problema voltou hoje de manhã.")

        self.assertEqual(demand.status, PortalDemand.STATUS_ASSUMED)
        self.assertEqual(demand.reopen_count, 1)
        self.assertIsNone(demand.completed_at)
        self.assertIsNotNone(demand.reopened_at)
        # Concluir arquiva o item da fila pessoal: reabrir precisa devolvê-lo.
        self.assertIsNotNone(demand.linked_queue_item_id)
        self.assertGreater(demand.resolution_due_at, timezone.now())
        self.assertTrue(
            PortalDemandLog.objects.filter(demand=demand, event_type=PortalDemandLog.EVENT_REOPENED).exists()
        )

    def test_outsider_cannot_reopen(self):
        demand = self._completed_demand()
        self.client.force_login(self.outsider)
        self.client.post(
            demand.get_absolute_url(),
            {"form_type": "workflow", "workflow_action": "reopen"},
            follow=True,
        )
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_COMPLETED)

    def test_reopening_releases_the_pending_feedback_lock(self):
        """Concluída sem nota trava a abertura; reaberta, ela sai do bloqueio."""
        demand = self._completed_demand()

        self.client.force_login(self.requester)
        blocked = self.client.get(reverse("portalDemandCreatePage"))
        self.assertEqual(blocked.status_code, 302)

        self._workflow(demand, "reopen")
        released = self.client.get(reverse("portalDemandCreatePage"))
        self.assertEqual(released.status_code, 200)


class RequesterSectorTests(PortalWorkflowTestCase):
    def test_sector_is_stored_when_the_demand_is_opened(self):
        self.client.force_login(self.requester)
        self.client.post(
            reverse("portalDemandCreatePage"),
            {
                "title": "Etiquetadora parada",
                "description": "A etiquetadora da expedição não imprime.",
                "task_group": str(self.group.id),
                "task_type": str(self.task_type.id),
                "priority_level": userQueue.PRIORITY_HIGH,
            },
            follow=True,
        )

        demand = PortalDemand.objects.get(title="Etiquetadora parada")
        self.assertEqual(demand.requester_sector_id, self.sector.id)
        self.assertEqual(demand.requester_id, self.requester.id)

    def test_create_page_shows_the_requester_record(self):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("portalDemandCreatePage"))
        profile = response.context["requester_profile"]

        self.assertTrue(profile["has_record"])
        self.assertEqual(profile["sector"], "Expedição")
        self.assertEqual(profile["role_title"], "Conferente")
        self.assertEqual(profile["phone"], "4321")

    def test_attendant_opens_on_behalf_of_a_collaborator(self):
        self.client.force_login(self.attendant)
        response = self.client.get(reverse("portalDemandCreatePage"))
        self.assertTrue(response.context["can_open_on_behalf"])

        self.client.post(
            reverse("portalDemandCreatePage"),
            {
                "title": "Chamado aberto por telefone",
                "description": "Solicitante ligou para o ramal do TI.",
                "task_group": str(self.group.id),
                "task_type": str(self.task_type.id),
                "priority_level": userQueue.PRIORITY_MEDIUM,
                "on_behalf_of": str(self.collaborator.id),
            },
            follow=True,
        )

        demand = PortalDemand.objects.get(title="Chamado aberto por telefone")
        self.assertEqual(demand.requester_id, self.requester.id)
        self.assertEqual(demand.requester_sector_id, self.sector.id)

    def test_requester_cannot_open_on_behalf_of_others(self):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("portalDemandCreatePage"))
        self.assertFalse(response.context["can_open_on_behalf"])
        self.assertNotIn("on_behalf_of", response.context["form"].fields)

    def test_sector_filter_narrows_the_queue(self):
        mine = self._open_demand()
        other_sector = PortalRequesterSector.objects.create(name="Financeiro")
        theirs = self._open_demand(requester_sector=other_sector, title="Outro setor")

        self.client.force_login(self.attendant)
        response = self.client.get(
            reverse("portalPendingDemandsPage"), {"view": "todos", "sector": str(self.sector.id)}
        )
        listed = [row.id for row in response.context["tickets"]]
        self.assertIn(mine.id, listed)
        self.assertNotIn(theirs.id, listed)


class PortalTopbarTests(PortalWorkflowTestCase):
    """A barra do portal só oferece o que o papel de quem está logado alcança."""

    def _topbar_links(self, url_name):
        response = self.client.get(reverse(url_name))
        self.assertEqual(response.status_code, 200)
        return response, response.content.decode()

    def test_requester_never_sees_the_queue_or_the_internal_panel(self):
        self.client.force_login(self.requester)
        for url_name in ("portalDemandPage", "portalMyDemandsPage"):
            with self.subTest(url_name=url_name):
                response, html = self._topbar_links(url_name)
                self.assertFalse(response.context["can_manage_portal"])
                self.assertFalse(response.context["can_open_internal_panel"])
                self.assertNotIn(reverse("portalPendingDemandsPage"), html)
                self.assertNotIn('href="%s"' % reverse("index"), html)
                # O que é dele continua lá.
                self.assertIn(reverse("portalMyDemandsPage"), html)

    def test_attendant_sees_the_queue_and_the_internal_panel(self):
        self.client.force_login(self.attendant)
        response, html = self._topbar_links("portalDemandPage")
        self.assertTrue(response.context["can_manage_portal"])
        self.assertTrue(response.context["can_open_internal_panel"])
        self.assertIn(reverse("portalPendingDemandsPage"), html)
        self.assertIn('href="%s"' % reverse("index"), html)

    def test_internal_user_without_portal_record_keeps_the_internal_panel(self):
        """Quem usa o ConnectMX e não é do portal não perde o caminho de volta."""
        self.client.force_login(self.outsider)
        response, html = self._topbar_links("portalMyDemandsPage")
        self.assertTrue(response.context["can_open_internal_panel"])
        self.assertIn('href="%s"' % reverse("index"), html)

    def test_create_page_hides_navigation_in_focus_mode(self):
        self.client.force_login(self.requester)
        response, html = self._topbar_links("portalDemandCreatePage")
        self.assertTrue(response.context["portal_topbar_focus"])
        self.assertIn("Voltar ao portal", html)
        # Nada de sair do formulário sem querer por um link da barra.
        self.assertNotIn(reverse("portalMyDemandsPage"), html)
        self.assertNotIn(reverse("portalPendingDemandsPage"), html)

    def test_attendant_create_page_is_also_focused(self):
        self.client.force_login(self.attendant)
        response, html = self._topbar_links("portalDemandCreatePage")
        self.assertTrue(response.context["portal_topbar_focus"])
        self.assertNotIn(reverse("portalPendingDemandsPage"), html)


class PausedSlaDisplayTests(PortalWorkflowTestCase):
    def test_paused_demand_reports_the_deadline_as_paused_not_late(self):
        """Pausada com prazo estourado não pode continuar anunciando atraso."""
        policy = PortalDemandSlaPolicy.objects.create(
            name="Padrão", first_response_minutes=60, resolution_minutes=240
        )
        demand = self._open_demand(sla_policy=policy)
        self._assume_as(demand, self.attendant)
        self.client.force_login(self.attendant)
        self._workflow(demand, "wait")

        PortalDemand.objects.filter(pk=demand.pk).update(
            first_response_due_at=timezone.now() - timedelta(hours=5),
            resolution_due_at=timezone.now() - timedelta(hours=4),
        )

        response = self.client.get(demand.get_absolute_url())
        rendered = response.context["demand"]
        self.assertEqual(rendered.sla_first_response["status"], "Pausado")
        self.assertEqual(rendered.sla_resolution["status"], "Pausado")
        self.assertEqual(rendered.triage_label, "Pausada")
