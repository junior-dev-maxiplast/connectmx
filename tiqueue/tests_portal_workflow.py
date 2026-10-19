"""Testes do fluxo de helpdesk do portal de chamados.

Cobrem o que passou a existir com o papel de atendente: quem pode atender sem
ser administrador, a pausa de SLA enquanto o chamado espera o solicitante, a
reabertura, o setor gravado na abertura e a abertura em nome de terceiros.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

_TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="connectmx-test-media-")

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
from .openai_insights import (
    PORTAL_CHAT_RESPONSE_SCHEMA,
    OpenAIInsightError,
    generate_portal_chat_reply,
)
from .models import (
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeEntryAttachment,
    PortalChatUpload,
    PortalDemandAttachment,
)
from .views import (
    _portal_candidate_score,
    _portal_chat_knowledge_candidates,
    _portal_find_similar_resolved,
    _portal_similarity_tokens,
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


_FAKE_AI_CONFIG = {
    "enabled": True,
    "api_key": "sk-fake",
    "api_key_configured": True,
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-5.6-sol",
    "reasoning_effort": "medium",
    "timeout": 30,
    "max_output_tokens": 800,
}

# "Desligado" mockado de propósito, em vez de confiar no .env real do
# ambiente onde o teste roda — senão o teste vira falso positivo (ou falso
# negativo) dependendo de alguém ter ligado a IA pra testar manualmente.
_FAKE_AI_CONFIG_DISABLED = {**_FAKE_AI_CONFIG, "enabled": False}


class PortalChatIntakeTests(PortalWorkflowTestCase):
    """Assistente de abertura por conversa: só rascunha título/descrição —
    quem cria o chamado continua sendo o formulário de sempre."""

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG_DISABLED)
    def test_chat_banner_hidden_on_manual_form_when_ai_is_not_configured(self, _mock_config):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("portalDemandCreatePage"))
        self.assertNotContains(response, "newticket-chat-banner")

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_chat_banner_shown_on_manual_form_when_ai_is_configured(self, _mock_config):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("portalDemandCreatePage"))
        self.assertContains(response, "newticket-chat-banner")
        self.assertContains(response, reverse("portalDemandChatPage"))

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG_DISABLED)
    def test_chat_page_redirects_to_manual_form_when_ai_is_not_configured(self, _mock_config):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("portalDemandChatPage"))
        self.assertRedirects(response, reverse("portalDemandCreatePage"))

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_chat_page_renders_whatsapp_style_shell_when_ai_is_configured(self, _mock_config):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("portalDemandChatPage"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "aichat-shell")
        self.assertContains(response, reverse("portalDemandChatMessageApi"))

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_chat_page_blocks_requester_with_pending_feedback(self, _mock_config):
        demand = self._open_demand(status=PortalDemand.STATUS_COMPLETED)
        self.client.force_login(self.requester)
        response = self.client.get(reverse("portalDemandChatPage"))
        self.assertRedirects(response, f"{reverse('portalMyDemandsPage')}?feedback_required=1")

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_message_endpoint_returns_follow_up_question(self, _mock_config):
        self.client.force_login(self.requester)
        reply = {
            "action": "ask",
            "question": "Isso te impede de trabalhar agora?",
            "title": None,
            "description": None,
            "priority_level": None,
        }
        with patch("tiqueue.views.generate_portal_chat_reply", return_value=reply):
            response = self.client.post(
                reverse("portalDemandChatMessageApi"),
                data=json.dumps({"message": "minha impressora não imprime"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {**reply, "source_id": None, "solution_source": None})
        self.assertEqual(len(self.client.session["portal_chat_history"]), 2)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_message_endpoint_returns_final_summary(self, _mock_config):
        self.client.force_login(self.requester)
        reply = {
            "action": "summary",
            "question": None,
            "title": "Impressora do RH não imprime",
            "description": "Parou de imprimir ontem e ninguém do RH consegue usar.",
            "priority_level": "high",
        }
        with patch("tiqueue.views.generate_portal_chat_reply", return_value=reply):
            response = self.client.post(
                reverse("portalDemandChatMessageApi"),
                data=json.dumps({"message": "ninguém do RH consegue imprimir"}),
                content_type="application/json",
            )
        self.assertEqual(response.json()["action"], "summary")
        self.assertEqual(response.json()["title"], reply["title"])

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_message_endpoint_surfaces_ai_errors_without_crashing(self, _mock_config):
        self.client.force_login(self.requester)
        with patch(
            "tiqueue.views.generate_portal_chat_reply",
            side_effect=OpenAIInsightError("Timeout ao falar com a OpenAI."),
        ):
            response = self.client.post(
                reverse("portalDemandChatMessageApi"),
                data=json.dumps({"message": "oi"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["action"], "error")

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG_DISABLED)
    def test_message_endpoint_refuses_when_ai_disabled(self, _mock_config):
        self.client.force_login(self.requester)
        response = self.client.post(
            reverse("portalDemandChatMessageApi"),
            data=json.dumps({"message": "oi"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_message_endpoint_rejects_empty_message(self, _mock_config):
        self.client.force_login(self.requester)
        response = self.client.post(
            reverse("portalDemandChatMessageApi"),
            data=json.dumps({"message": "   "}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_reset_endpoint_clears_session_history(self, _mock_config):
        self.client.force_login(self.requester)
        with patch(
            "tiqueue.views.generate_portal_chat_reply",
            return_value={"action": "ask", "question": "Desde quando?", "title": None, "description": None, "priority_level": None},
        ):
            self.client.post(
                reverse("portalDemandChatMessageApi"),
                data=json.dumps({"message": "oi"}),
                content_type="application/json",
            )
        self.assertIn("portal_chat_history", self.client.session)

        response = self.client.post(reverse("portalDemandChatResetApi"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("portal_chat_history", self.client.session)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_revisiting_the_create_page_starts_a_fresh_conversation(self, _mock_config):
        """GET novo = conversa nova; só um POST com erro de validação (mesma
        página renderizada de novo) pode preservar o que já foi conversado."""
        self.client.force_login(self.requester)
        with patch(
            "tiqueue.views.generate_portal_chat_reply",
            return_value={"action": "ask", "question": "Desde quando?", "title": None, "description": None, "priority_level": None},
        ):
            self.client.post(
                reverse("portalDemandChatMessageApi"),
                data=json.dumps({"message": "oi"}),
                content_type="application/json",
            )
        self.assertIn("portal_chat_history", self.client.session)

        self.client.get(reverse("portalDemandCreatePage"))
        self.assertNotIn("portal_chat_history", self.client.session)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_summary_reply_pre_fills_the_manual_form_once(self, _mock_config):
        """O resumo vira rascunho de sessão na hora em que a IA devolve
        `action: summary`; a próxima visita ao formulário manual já chega com
        título/descrição/impacto preenchidos — e o rascunho é consumido uma
        única vez, não sobra pra uma segunda visita."""
        self.client.force_login(self.requester)
        reply = {
            "action": "summary",
            "question": None,
            "title": "Impressora do RH não imprime",
            "description": "Parou de imprimir ontem e ninguém do RH consegue usar.",
            "priority_level": "high",
        }
        with patch("tiqueue.views.generate_portal_chat_reply", return_value=reply):
            self.client.post(
                reverse("portalDemandChatMessageApi"),
                data=json.dumps({"message": "ninguém do RH consegue imprimir"}),
                content_type="application/json",
            )
        self.assertEqual(self.client.session["portal_chat_draft"]["title"], reply["title"])

        response = self.client.get(reverse("portalDemandCreatePage"))
        self.assertEqual(response.context["form"].initial.get("title"), reply["title"])
        self.assertEqual(response.context["form"].initial.get("description"), reply["description"])
        self.assertEqual(response.context["form"].initial.get("priority_level"), reply["priority_level"])
        self.assertNotIn("portal_chat_draft", self.client.session)

        # Segunda visita, sem rascunho novo: formulário volta a ficar em branco.
        response2 = self.client.get(reverse("portalDemandCreatePage"))
        self.assertNotIn("title", response2.context["form"].initial)

    def test_home_page_always_offers_the_chatbot_button(self):
        """Botão fixo na home — diferente do banner do formulário manual,
        este não some quando a IA está desligada; quem clicar sem IA
        configurada só cai no formulário manual (ver `portalDemandChatPage`)."""
        self.client.force_login(self.requester)
        response = self.client.get(reverse("portalDemandPage"))
        self.assertContains(response, "Chatbot de Ajuda")
        self.assertContains(response, reverse("portalDemandChatPage"))


_CHAT_REPLY_BASE = {
    "action": "ask",
    "question": None,
    "title": None,
    "description": None,
    "priority_level": None,
    "request_attachment": False,
    "keywords": [],
    "source_id": None,
    "capability": "not_applicable",
    "steps": [],
    "quick_replies": [],
}


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class PortalChatAttachmentTests(PortalWorkflowTestCase):
    def _upload(self, name="erro.txt", content=b"ORA-12154: TNS erro ao conectar"):
        return self.client.post(
            reverse("portalDemandChatUploadApi"),
            {"file": SimpleUploadedFile(name, content)},
        )

    def _message(self, payload, reply):
        with patch("tiqueue.views.generate_portal_chat_reply", side_effect=reply) as generate:
            response = self.client.post(
                reverse("portalDemandChatMessageApi"),
                data=json.dumps(payload),
                content_type="application/json",
            )
        return response, generate

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_upload_refuses_unsupported_file_types(self, _cfg):
        self.client.force_login(self.requester)
        response = self._upload(name="programa.exe", content=b"MZ")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PortalChatUpload.objects.exists())

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_upload_stores_the_file_for_its_sender(self, _cfg):
        self.client.force_login(self.requester)
        response = self._upload()
        self.assertEqual(response.status_code, 200)
        upload = PortalChatUpload.objects.get(pk=response.json()["id"])
        self.assertEqual(upload.user, self.requester)
        self.assertEqual(upload.original_name, "erro.txt")
        self.assertEqual(response.json()["kind"], "text")

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG_DISABLED)
    def test_upload_is_refused_while_the_assistant_is_off(self, _cfg):
        self.client.force_login(self.requester)
        self.assertEqual(self._upload().status_code, 503)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_text_attachment_content_reaches_the_model(self, _cfg):
        self.client.force_login(self.requester)
        upload_id = self._upload().json()["id"]
        captured = {}

        def reply(history, extra_parts=None, **_kwargs):
            captured["parts"] = extra_parts or []
            return dict(_CHAT_REPLY_BASE, question="Desde quando?")

        response, _ = self._message({"message": "Não conecta", "attachment_ids": [upload_id]}, reply)
        self.assertEqual(response.status_code, 200)
        texts = " ".join(part.get("text", "") for part in captured["parts"])
        self.assertIn("ORA-12154", texts)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_images_are_sent_to_the_model_as_input_images(self, _cfg):
        self.client.force_login(self.requester)
        upload_id = self._upload(name="print.png", content=b"\x89PNG\r\n\x1a\nfake").json()["id"]
        captured = {}

        def reply(history, extra_parts=None, **_kwargs):
            captured["parts"] = extra_parts or []
            return dict(_CHAT_REPLY_BASE, question="Certo.")

        self._message({"message": "Veja o print", "attachment_ids": [upload_id]}, reply)
        images = [part for part in captured["parts"] if part.get("type") == "input_image"]
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0]["image_url"].startswith("data:image/png;base64,"))

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_uploads_of_another_user_are_ignored(self, _cfg):
        self.client.force_login(self.requester)
        upload_id = self._upload().json()["id"]
        self.client.force_login(self.outsider)
        captured = {}

        def reply(history, extra_parts=None, **_kwargs):
            captured["parts"] = extra_parts or []
            return dict(_CHAT_REPLY_BASE, question="Certo.")

        self._message({"message": "oi", "attachment_ids": [upload_id]}, reply)
        self.assertEqual(captured["parts"], [])

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_assistant_can_ask_for_an_attachment(self, _cfg):
        self.client.force_login(self.requester)
        reply = dict(_CHAT_REPLY_BASE, question="Pode anexar um print da tela?", request_attachment=True)
        response, _ = self._message({"message": "deu erro"}, lambda history, **_kwargs: reply)
        self.assertTrue(response.json()["request_attachment"])

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_summary_points_to_similar_resolved_problems_by_visibility(self, _cfg):
        own_past = self._open_demand(
            title="Impressora do RH sem imprimir",
            description="A impressora do RH não imprime nada desde ontem.",
            status=PortalDemand.STATUS_COMPLETED,
        )
        other_past = self._open_demand(
            requester=self.outsider,
            title="Impressora do financeiro parada",
            description="Impressora do financeiro não imprime.",
            status=PortalDemand.STATUS_COMPLETED,
        )
        summary = dict(
            _CHAT_REPLY_BASE,
            action="summary",
            title="Impressora não imprime",
            description="A impressora do RH não imprime.",
            priority_level="high",
            keywords=["impressora"],
        )

        self.client.force_login(self.requester)
        response, _ = self._message({"message": "não imprime"}, lambda history, **_kwargs: summary)
        titles = [item["title"] for item in response.json()["similar"]]
        self.assertIn(own_past.title, titles)
        self.assertNotIn(other_past.title, titles)

        self.client.force_login(self.attendant)
        response, _ = self._message({"message": "não imprime"}, lambda history, **_kwargs: summary)
        titles = [item["title"] for item in response.json()["similar"]]
        self.assertIn(own_past.title, titles)
        self.assertIn(other_past.title, titles)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_chat_attachments_travel_with_the_opened_demand(self, _cfg):
        self.client.force_login(self.requester)
        upload_id = self._upload(name="print.png", content=b"\x89PNG\r\n\x1a\nfake").json()["id"]
        summary = dict(
            _CHAT_REPLY_BASE,
            action="summary",
            title="Etiquetadora parada",
            description="A etiquetadora da expedição não imprime.",
            priority_level="high",
        )
        self._message({"message": "veja o print", "attachment_ids": [upload_id]}, lambda history, **_kwargs: summary)

        page = self.client.get(reverse("portalDemandCreatePage"))
        self.assertEqual([upload.id for upload in page.context["chat_uploads"]], [upload_id])

        self.client.post(
            reverse("portalDemandCreatePage"),
            {
                "title": "Etiquetadora parada",
                "description": "A etiquetadora da expedição não imprime.",
                "task_group": str(self.group.id),
                "task_type": str(self.task_type.id),
                "priority_level": userQueue.PRIORITY_HIGH,
                "chat_upload_ids": [str(upload_id)],
            },
            follow=True,
        )
        demand = PortalDemand.objects.get(title="Etiquetadora parada")
        self.assertEqual(list(demand.attachments.values_list("original_name", flat=True)), ["print.png"])
        self.assertEqual(PortalChatUpload.objects.get(pk=upload_id).claimed_demand_id, demand.id)


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class PortalDocumentationTests(PortalWorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.completed = self._open_demand(
            title="Impressora do RH sem imprimir",
            description="A impressora do RH parou de imprimir na terça.",
            status=PortalDemand.STATUS_COMPLETED,
        )
        PortalDemandAttachment.objects.create(
            demand=self.completed,
            uploaded_by=self.requester,
            original_name="erro.png",
            file="portal_demands/erro.png",
        )

    def _draft(self, demand, payload):
        return self.client.post(
            reverse("portalDemandDocumentDraftApi", args=[demand.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _save(self, demand, **overrides):
        data = {
            "title": "Impressora sem impressão após queda do driver",
            "trigger": "Impressora do RH não imprime desde a terça.",
            "impact": "Relatórios do RH travados.",
            "root_cause": "Driver corrompido no servidor de impressão.",
            "resolution": "1. Remover o driver\n2. Reinstalar a versão homologada",
            "workaround": "",
            "tags": "impressora, driver",
            "category": "",
            "include_attachments": "on",
        }
        data.update(overrides)
        return self.client.post(reverse("portalDemandDocumentSaveApi", args=[demand.id]), data)

    def test_requester_cannot_draft_or_save_documentation(self):
        self.client.force_login(self.requester)
        draft = self._draft(self.completed, {"how_it_was_solved": "Reinstalei o driver no servidor."})
        self.assertEqual(draft.status_code, 403)
        self.assertEqual(self._save(self.completed).status_code, 403)
        self.assertFalse(KnowledgeEntry.objects.exists())

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_only_completed_demands_can_be_documented(self, _cfg):
        pending = self._open_demand(title="Chamado em aberto")
        self.client.force_login(self.attendant)
        response = self._draft(pending, {"how_it_was_solved": "Reinstalei o driver no servidor."})
        self.assertEqual(response.status_code, 400)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_draft_asks_for_a_real_description_of_the_solution(self, _cfg):
        self.client.force_login(self.attendant)
        response = self._draft(self.completed, {"how_it_was_solved": "ok"})
        self.assertEqual(response.status_code, 400)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG_DISABLED)
    def test_draft_is_refused_while_the_assistant_is_off(self, _cfg):
        self.client.force_login(self.attendant)
        response = self._draft(self.completed, {"how_it_was_solved": "Reinstalei o driver no servidor."})
        self.assertEqual(response.status_code, 503)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_draft_returns_fields_built_from_the_whole_record(self, _cfg):
        self.client.force_login(self.attendant)
        generated = {
            "title": "Impressora sem impressão após queda do driver",
            "trigger": "Impressora do RH não imprime.",
            "impact": "Relatórios travados.",
            "root_cause": "Driver corrompido.",
            "resolution": "1. Reinstalar o driver.",
            "workaround": "Imprimir pelo financeiro.",
            "tags": "impressora, driver",
        }
        with patch("tiqueue.views.generate_demand_documentation", return_value=generated) as generate:
            response = self._draft(self.completed, {"how_it_was_solved": "Reinstalei o driver no servidor."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resolution"], "1. Reinstalar o driver.")
        record, how = generate.call_args.args
        self.assertIn(self.completed.protocol, record)
        self.assertIn("Reinstalei o driver no servidor.", how)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_saving_creates_a_knowledge_entry_linked_to_the_demand(self, _cfg):
        self.client.force_login(self.attendant)
        response = self._save(self.completed)
        self.assertEqual(response.status_code, 201)
        entry = KnowledgeEntry.objects.get(source_demand=self.completed)
        self.assertEqual(entry.category.name, "Documentação de chamados")
        self.assertEqual(entry.created_by, self.attendant)
        self.assertEqual(
            list(KnowledgeEntryAttachment.objects.filter(entry=entry).values_list("original_name", flat=True)),
            ["erro.png"],
        )
        self.assertIn("documented=1", response.json()["redirect"])

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_a_demand_is_documented_only_once(self, _cfg):
        self.client.force_login(self.attendant)
        self._save(self.completed)
        second = self._save(self.completed)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(KnowledgeEntry.objects.filter(source_demand=self.completed).count(), 1)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_missing_resolution_is_reported_field_by_field(self, _cfg):
        self.client.force_login(self.attendant)
        response = self._save(self.completed, resolution="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("resolution", response.json()["errors"])
        self.assertFalse(KnowledgeEntry.objects.exists())

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_documentation_button_is_shown_only_to_attendants_before_saving(self, _cfg):
        self.client.force_login(self.attendant)
        before = self.client.get(self.completed.get_absolute_url()).content.decode()
        self.assertIn('data-open-modal="docModal"', before)

        self._save(self.completed)
        after = self.client.get(self.completed.get_absolute_url()).content.decode()
        self.assertNotIn('data-open-modal="docModal"', after)
        self.assertIn("Documentada", after)

        self.client.force_login(self.requester)
        as_requester = self.client.get(self.completed.get_absolute_url()).content.decode()
        self.assertNotIn('data-open-modal="docModal"', as_requester)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_documentation_is_suggested_to_the_requester_without_exposing_other_demands(self, _cfg):
        self.client.force_login(self.attendant)
        self._save(self.completed)
        other = self._open_demand(
            requester=self.outsider,
            title="Impressora do financeiro travada",
            description="Impressora do financeiro não imprime.",
            status=PortalDemand.STATUS_COMPLETED,
        )
        titles = [item["title"] for item in _portal_find_similar_resolved(self.requester, "Impressora não imprime", "")]
        self.assertIn("Impressora sem impressão após queda do driver", titles)
        self.assertNotIn(other.title, titles)


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class PortalChatKnowledgeTests(PortalWorkflowTestCase):
    """Busca na base e em chamados resolvidos a cada turno, e a análise de
    capacidade: passo só chega à pessoa com registro real por trás e quando
    ela consegue executá-lo."""

    def _kb_entry(self, title, trigger, resolution, tags="", is_resolved=True):
        category, _ = KnowledgeCategory.objects.get_or_create(name="Procedimentos de usuário")
        return KnowledgeEntry.objects.create(
            category=category,
            title=title,
            trigger=trigger,
            description=trigger,
            resolution=resolution,
            tags=tags,
            is_resolved=is_resolved,
            created_by=self.attendant,
        )

    def _post_with_reply(self, text, reply):
        with patch("tiqueue.views.generate_portal_chat_reply", side_effect=reply):
            return self.client.post(
                reverse("portalDemandChatMessageApi"),
                data=json.dumps({"message": text}),
                content_type="application/json",
            )

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_each_turn_passes_the_matching_resolved_solutions_to_the_model(self, _cfg):
        entry = self._kb_entry(
            title="Outlook não abre e fica carregando",
            trigger="O Outlook trava na abertura e fica carregando sem fim.",
            resolution="1. Feche o Outlook pelo Gerenciador de Tarefas.\n2. Abra o Outlook de novo.",
            tags="outlook, e-mail",
        )
        captured = {}

        def reply(history, extra_parts=None, knowledge_context=None):
            captured["context"] = knowledge_context
            return dict(_CHAT_REPLY_BASE, question="Desde quando acontece?")

        self.client.force_login(self.requester)
        self._post_with_reply("meu outlook fica carregando e não abre", reply)
        self.assertIn(f"[KB-{entry.id}]", captured["context"])
        self.assertIn("Feche o Outlook", captured["context"])

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_unresolved_entries_are_not_offered_as_solutions(self, _cfg):
        entry = self._kb_entry(
            title="Outlook não abre",
            trigger="Outlook não abre.",
            resolution="Reinicie.",
            is_resolved=False,
        )
        captured = {}

        def reply(history, extra_parts=None, knowledge_context=None):
            captured["context"] = knowledge_context
            return dict(_CHAT_REPLY_BASE, question="Desde quando?")

        self.client.force_login(self.requester)
        self._post_with_reply("outlook não abre", reply)
        self.assertNotIn(f"[KB-{entry.id}]", captured["context"])
        self.assertIn("nenhum registro resolvido", captured["context"])

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_other_requesters_demands_stay_out_of_the_context_and_internal_notes_never_leak(self, _cfg):
        own = self._open_demand(
            title="Coletor sem rede na doca",
            description="Coletor da doca 3 sem rede.",
            status=PortalDemand.STATUS_COMPLETED,
        )
        PortalDemandMessage.objects.create(
            demand=own,
            author_role=PortalDemandMessage.ROLE_ATTENDANT,
            author_name="Equipe",
            message="Reiniciei o ponto de acesso e a rede voltou.",
        )
        PortalDemandMessage.objects.create(
            demand=own,
            author_role=PortalDemandMessage.ROLE_ATTENDANT,
            author_name="Equipe",
            is_internal=True,
            message="Senha do ponto de acesso: rotina interna.",
        )
        other = self._open_demand(
            requester=self.outsider,
            title="Coletor sem rede no financeiro",
            description="Coletor do financeiro sem rede.",
            status=PortalDemand.STATUS_COMPLETED,
        )
        captured = {}

        def reply(history, extra_parts=None, knowledge_context=None):
            captured["context"] = knowledge_context
            return dict(_CHAT_REPLY_BASE, question="Desde quando?")

        self.client.force_login(self.requester)
        self._post_with_reply("coletor sem rede", reply)
        self.assertIn(f"[CH-{own.id}]", captured["context"])
        self.assertIn("Reiniciei o ponto de acesso", captured["context"])
        self.assertNotIn("rotina interna", captured["context"])
        self.assertNotIn(f"[CH-{other.id}]", captured["context"])

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_solution_is_linked_to_its_source_and_carried_into_the_summary(self, _cfg):
        entry = self._kb_entry(
            title="Outlook não abre e fica carregando",
            trigger="O Outlook trava na abertura.",
            resolution="1. Feche o Outlook pelo Gerenciador de Tarefas.",
            tags="outlook",
        )
        self.client.force_login(self.requester)
        solution = dict(
            _CHAT_REPLY_BASE,
            action="solution",
            question="Consegue fazer esses passos? Como ficou?",
            source_id=f"KB-{entry.id}",
            capability="yes",
            steps=["Feche o Outlook pelo Gerenciador de Tarefas.", "Abra o Outlook de novo."],
            quick_replies=["Resolveu", "Não resolveu"],
        )
        response = self._post_with_reply("outlook fica carregando", lambda history, **_kwargs: solution)
        data = response.json()
        self.assertEqual(data["action"], "solution")
        self.assertEqual(data["source_id"], f"KB-{entry.id}")
        self.assertEqual(data["solution_source"]["url"], reverse("knowledgeEntryDetailPage", args=[entry.id]))

        summary = dict(
            _CHAT_REPLY_BASE,
            action="summary",
            title="Outlook não abre",
            description="Outlook fica carregando. Tentei reiniciar e não resolveu.",
            priority_level="medium",
        )
        self._post_with_reply("não resolveu", lambda history, **_kwargs: summary)
        draft_description = self.client.session["portal_chat_draft"]["description"]
        self.assertIn("Solução da base consultada: Outlook não abre e fica carregando", draft_description)
        self.assertIn(reverse("knowledgeEntryDetailPage", args=[entry.id]), draft_description)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_source_id_the_knowledge_base_does_not_have_is_dropped(self, _cfg):
        self._kb_entry(title="Outlook não abre", trigger="Outlook não abre.", resolution="1. Reinicie.")
        self.client.force_login(self.requester)
        invented = dict(
            _CHAT_REPLY_BASE,
            action="solution",
            question="Tente isto.",
            source_id="KB-999999",
            capability="yes",
            steps=["Reinicie o Outlook."],
        )
        response = self._post_with_reply("outlook não abre", lambda history, **_kwargs: invented)
        self.assertIsNone(response.json()["source_id"])
        self.assertIsNone(response.json()["solution_source"])

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_resolved_turn_ends_without_opening_a_ticket(self, _cfg):
        self.client.force_login(self.requester)
        summary = dict(
            _CHAT_REPLY_BASE,
            action="summary",
            title="Coletor sem rede",
            description="Coletor sem rede.",
            priority_level="low",
        )
        self._post_with_reply("coletor sem rede", lambda history, **_kwargs: summary)
        self.assertIn("portal_chat_draft", self.client.session)
        demands_before = PortalDemand.objects.count()

        resolved = dict(_CHAT_REPLY_BASE, action="resolved", question="Que bom! Então não precisa abrir chamado.")
        response = self._post_with_reply("consegui, obrigado", lambda history, **_kwargs: resolved)
        self.assertEqual(response.json()["action"], "resolved")
        self.assertNotIn("portal_chat_draft", self.client.session)
        self.assertEqual(PortalDemand.objects.count(), demands_before)

    def test_model_cannot_suggest_steps_without_confirming_the_user_can_run_them(self):
        unsafe = dict(
            _CHAT_REPLY_BASE,
            action="solution",
            question="Tente isto.",
            source_id="KB-1",
            capability="no",
            steps=["Reinstale o driver no servidor."],
        )
        with patch("tiqueue.openai_insights._structured_response", return_value=unsafe):
            with self.assertRaises(OpenAIInsightError):
                generate_portal_chat_reply(
                    [{"role": "user", "content": "impressora parada"}],
                    runtime_config=_FAKE_AI_CONFIG,
                )

    def test_knowledge_context_reaches_the_model_as_developer_guidance(self):
        ask = dict(_CHAT_REPLY_BASE, question="Desde quando?")
        with patch("tiqueue.openai_insights._structured_response", return_value=ask) as structured:
            generate_portal_chat_reply(
                [{"role": "user", "content": "impressora parada"}],
                runtime_config=_FAKE_AI_CONFIG,
                knowledge_context="Base consultada: [KB-7] Impressora sem impressão",
            )
        input_messages = structured.call_args.args[1]
        developer_texts = [item["content"][0]["text"] for item in input_messages if item["role"] == "developer"]
        self.assertTrue(any("[KB-7]" in text for text in developer_texts))

    def test_response_schema_requires_every_property_for_strict_mode(self):
        schema = PORTAL_CHAT_RESPONSE_SCHEMA["schema"]
        self.assertEqual(set(schema["properties"]), set(schema["required"]))

    def test_similarity_matches_whole_words_not_fragments(self):
        """'mail' não pode puxar um chamado só porque o e-mail da assinatura
        tem 'mailto', e termos genéricos ('abre', 'fica') não contam."""
        self.assertEqual(_portal_candidate_score(["mail"], "Curso ITC", "Ana <mailto:ana.etges@maxiplast.com.br>"), 0)
        self.assertEqual(_portal_candidate_score(["mail"], "Curso ITC", "envio do e-mail do curso"), 1)
        self.assertEqual(_portal_candidate_score(["impressora"], "Impressoras do RH"), 3)
        self.assertEqual(_portal_similarity_tokens("Outlook fica carregando e não abre"), ["outlook", "carregando"])

    def test_chamado_is_offered_only_with_a_title_match_and_two_distinct_terms(self):
        generic = self._open_demand(
            title="Desligamento de colaborador",
            description="Conferir equipamentos do colaborador que saiu hoje.",
            status=PortalDemand.STATUS_COMPLETED,
        )
        relevant = self._open_demand(
            title="Outlook travado ao abrir",
            description="O Outlook fica carregando e não abre a caixa de entrada.",
            status=PortalDemand.STATUS_COMPLETED,
        )
        refs = [
            row["ref"]
            for row in _portal_chat_knowledge_candidates(
                self.requester,
                ["O colaborador fechou o programa e o outlook continua carregando"],
            )
        ]
        self.assertIn(f"CH-{relevant.id}", refs)
        self.assertNotIn(f"CH-{generic.id}", refs)

    def test_summary_wording_does_not_pull_unrelated_email_chamados(self):
        """Caso real: o resumo do Outlook casava com chamados de e-mails
        encaminhados só por 'caixa de entrada', 'foi' e 'pelo'."""
        tokens = _portal_similarity_tokens(
            "O Outlook fica carregando continuamente e não abre a caixa de entrada. "
            "Foi encerrado pelo Gerenciador de Tarefas, mas o problema continuou."
        )
        self.assertNotIn("foi", tokens)
        self.assertNotIn("pelo", tokens)
        score = _portal_candidate_score(
            tokens,
            "Curso ITC",
            "Bom dia! Segue abaixo o curso do e-mail. Caixa de entrada, foi pelo RH.",
        )
        self.assertLess(score, 3)
