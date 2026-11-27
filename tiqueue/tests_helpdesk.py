"""Testes do Helpdesk no padrão dos Dashes.

- Painel do Helpdesk (tela inicial do Dashes): acesso, dados por chamado para o
  filtro cruzado e detalhes sob demanda da tabela.
- Rastro das conversas com o assistente de IA e o canal de abertura do chamado.
- Avaliação e documentação entrando no histórico do chamado.
- Base de conhecimento: só quem atende (ou usa o ConnectMX interno) escreve; erro
  de validação volta para a tela; registro pode ser editado na própria ficha.
- Menu lateral do Helpdesk por papel.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from . import helpdesk_bi
from .models import (
    Dashboard,
    DashboardAccess,
    KnowledgeCategory,
    KnowledgeEntry,
    PortalAssistantConversation,
    PortalDemand,
    PortalDemandLog,
    PortalDemandSlaPolicy,
    PortalNotification,
    userQueue,
)
from .tests_portal_workflow import _CHAT_REPLY_BASE, _FAKE_AI_CONFIG, PortalWorkflowTestCase


def _dashes_login(client, user):
    client.force_login(user)
    session = client.session
    session["dashes_authenticated"] = True
    session.save()


def _rows(payload):
    """Linhas do painel como dicionários, com os índices de `enums` já traduzidos."""
    rows = []
    for values in payload["rows"]:
        row = dict(zip(payload["fields"], values))
        for field, options in payload["enums"].items():
            if row.get(field) is not None:
                row[field] = options[row[field]]
        rows.append(row)
    return rows


class HelpdeskPanelTests(PortalWorkflowTestCase):
    def test_helpdesk_panel_is_first_in_the_catalog_and_the_dashes_home(self):
        dashboard = Dashboard.objects.get(slug="helpdesk")
        self.assertEqual(dashboard.url_name, "dashesHelpdeskPage")
        _dashes_login(self.client, self.admin)
        response = self.client.get(reverse("dashesHome"))
        self.assertRedirects(response, reverse("dashesHelpdeskPage"), fetch_redirect_response=False)

    def test_panel_requires_the_helpdesk_permission(self):
        _dashes_login(self.client, self.attendant)
        denied = self.client.get(reverse("dashesHelpdeskPage"))
        self.assertEqual(denied.status_code, 403)

        DashboardAccess.objects.create(user=self.attendant, dashboard=Dashboard.objects.get(slug="helpdesk"))
        allowed = self.client.get(reverse("dashesHelpdeskPage"))
        self.assertEqual(allowed.status_code, 200)
        self.assertContains(allowed, 'id="mxPanelData"')
        self.assertContains(allowed, "helpdeskPanel.js")

    def test_panel_needs_the_dashes_session(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashesHelpdeskPage"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashesLoginPage"), response["Location"])

    def test_payload_has_one_row_per_ticket_with_the_filter_dimensions(self):
        policy = PortalDemandSlaPolicy.objects.create(name="Padrão", first_response_minutes=60, resolution_minutes=240)
        late = self._open_demand(title="Atrasado", sla_policy=policy)
        PortalDemand.objects.filter(pk=late.pk).update(resolution_due_at=timezone.now() - timedelta(hours=2))
        done = self._open_demand(
            title="Resolvido no prazo",
            status=PortalDemand.STATUS_COMPLETED,
            assigned_to=self.attendant,
            feedback_rating=9,
            reopen_count=1,
            opened_via=PortalDemand.OPENED_VIA_CHAT,
        )
        created = timezone.now() - timedelta(hours=5)
        PortalDemand.objects.filter(pk=done.pk).update(
            created_at=created,
            first_response_at=created + timedelta(minutes=30),
            completed_at=created + timedelta(hours=3),
            resolution_due_at=created + timedelta(hours=4),
        )
        old = self._open_demand(title="Antigo e fechado", status=PortalDemand.STATUS_COMPLETED)
        PortalDemand.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=900))
        category = KnowledgeCategory.objects.create(name="Rede")
        KnowledgeEntry.objects.create(
            category=category, title="Doc", trigger="t", description="d", resolution="r", is_resolved=True, source_demand=done,
        )

        payload = helpdesk_bi.helpdesk_payload()
        rows = {row["id"]: row for row in _rows(payload)}

        self.assertIn(late.id, rows)
        self.assertIn(done.id, rows)
        self.assertNotIn(old.id, rows, "chamado fechado fora dos 24 meses não entra")
        self.assertEqual(len(payload["months"]), 24)

        self.assertEqual(rows[late.id]["st"], PortalDemand.STATUS_PENDING)
        self.assertEqual(rows[late.id]["sla"], "late")
        self.assertIsNone(rows[late.id]["cm"])

        row = rows[done.id]
        self.assertEqual(row["sla"], "met")
        self.assertEqual(row["r"], 9)
        self.assertEqual(row["fr"], 30)
        self.assertEqual(row["rt"], 180)
        self.assertEqual(row["ro"], 1)
        self.assertEqual(row["kb"], 1)
        self.assertEqual(row["o"], PortalDemand.OPENED_VIA_CHAT)
        self.assertEqual(row["a"], self.attendant.id)
        self.assertEqual(payload["names"]["a"][str(self.attendant.id)], "Atendente da Fila")
        self.assertEqual(payload["names"]["s"][str(self.sector.id)], "Expedição")
        self.assertEqual(payload["knowledge"]["rows"][0][2], 1, "registro veio de chamado")
        # Assunto e protocolo não vão nas linhas (pesariam a página): vêm sob demanda.
        self.assertNotIn("title", payload["fields"])

    def test_json_format_and_ticket_details(self):
        demand = self._open_demand(title="Impressora parada")
        _dashes_login(self.client, self.admin)
        data = self.client.get(reverse("dashesHelpdeskPage"), {"formato": "json"}).json()
        self.assertIn(demand.id, [values[0] for values in data["rows"]])

        details = self.client.get(reverse("dashesHelpdeskTickets"), {"ids": f"{demand.id},abc,{demand.id}"}).json()
        self.assertEqual(details["tickets"][str(demand.id)]["title"], "Impressora parada")
        self.assertEqual(details["tickets"][str(demand.id)]["code"], demand.protocol)

    def test_ticket_details_follow_the_panel_permission(self):
        demand = self._open_demand()
        _dashes_login(self.client, self.requester)
        response = self.client.get(reverse("dashesHelpdeskTickets"), {"ids": str(demand.id)})
        self.assertEqual(response.status_code, 403)


class AssistantTrackingTests(PortalWorkflowTestCase):
    def _say(self, reply):
        with patch("tiqueue.views.generate_portal_chat_reply", return_value={**_CHAT_REPLY_BASE, **reply}):
            return self.client.post(
                reverse("portalDemandChatMessageApi"),
                data=json.dumps({"message": "a impressora do RH não imprime"}),
                content_type="application/json",
            )

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_conversation_that_becomes_a_ticket(self, _config):
        self.client.force_login(self.requester)
        self.client.get(reverse("portalDemandChatPage"))
        self._say({"action": "ask", "question": "Desde quando?"})
        self._say({
            "action": "summary", "title": "Impressora do RH sem imprimir",
            "description": "Ninguém do RH consegue imprimir desde ontem.", "priority_level": "high",
        })

        conversation = PortalAssistantConversation.objects.get()
        self.assertEqual(conversation.user, self.requester)
        self.assertEqual(conversation.turns, 2)
        self.assertEqual(conversation.outcome, PortalAssistantConversation.OUTCOME_SUMMARY)
        self.assertEqual(conversation.sector, self.sector)

        page = self.client.get(reverse("portalDemandCreatePage"))
        self.assertEqual(page.context["chat_conversation_id"], conversation.id)
        self.assertContains(page, 'name="chat_conversation"')

        self.client.post(reverse("portalDemandCreatePage"), {
            "title": "Impressora do RH sem imprimir",
            "description": "Ninguém do RH consegue imprimir desde ontem.",
            "task_group": str(self.group.id),
            "task_type": str(self.task_type.id),
            "priority_level": userQueue.PRIORITY_HIGH,
            "chat_conversation": str(conversation.id),
        })
        demand = PortalDemand.objects.get(title="Impressora do RH sem imprimir")
        conversation.refresh_from_db()
        self.assertEqual(demand.opened_via, PortalDemand.OPENED_VIA_CHAT)
        self.assertEqual(conversation.demand, demand)
        self.assertEqual(conversation.outcome, PortalAssistantConversation.OUTCOME_TICKET)

    @patch("tiqueue.views.get_openai_runtime_config", return_value=_FAKE_AI_CONFIG)
    def test_conversation_resolved_without_a_ticket(self, _config):
        self.client.force_login(self.requester)
        self._say({"action": "resolved"})
        conversation = PortalAssistantConversation.objects.get()
        self.assertEqual(conversation.outcome, PortalAssistantConversation.OUTCOME_RESOLVED)
        self.assertNotIn("portal_chat_conversation", self.client.session)
        # A próxima mensagem já é outra conversa.
        self._say({"action": "ask", "question": "Mais alguma coisa?"})
        self.assertEqual(PortalAssistantConversation.objects.count(), 2)

    def test_someone_elses_conversation_does_not_mark_the_ticket(self):
        conversation = PortalAssistantConversation.objects.create(user=self.attendant)
        self.client.force_login(self.requester)
        self.client.post(reverse("portalDemandCreatePage"), {
            "title": "Formulário comum",
            "description": "Aberto pelo formulário.",
            "task_group": str(self.group.id),
            "task_type": str(self.task_type.id),
            "priority_level": userQueue.PRIORITY_MEDIUM,
            "chat_conversation": str(conversation.id),
        })
        demand = PortalDemand.objects.get(title="Formulário comum")
        self.assertEqual(demand.opened_via, PortalDemand.OPENED_VIA_PORTAL)
        conversation.refresh_from_db()
        self.assertIsNone(conversation.demand)

    def test_attendant_opening_on_behalf_is_its_own_channel(self):
        self.client.force_login(self.attendant)
        self.client.post(reverse("portalDemandCreatePage"), {
            "title": "Aberto por telefone",
            "description": "Ligou no ramal.",
            "task_group": str(self.group.id),
            "task_type": str(self.task_type.id),
            "priority_level": userQueue.PRIORITY_MEDIUM,
            "on_behalf_of": str(self.collaborator.id),
        })
        self.assertEqual(PortalDemand.objects.get(title="Aberto por telefone").opened_via, PortalDemand.OPENED_VIA_ATTENDANT)


class FeedbackHistoryTests(PortalWorkflowTestCase):
    def test_rating_enters_the_history_and_reaches_the_attendant(self):
        demand = self._open_demand()
        self._assume_as(demand, self.attendant)
        self._workflow(demand, "complete")

        self.client.force_login(self.requester)
        self.client.post(demand.get_absolute_url(), {
            "form_type": "feedback", "feedback_rating": "6", "feedback_comment": "Demorou, mas resolveu.",
        })
        demand.refresh_from_db()
        self.assertEqual(demand.feedback_rating, 6)
        log = PortalDemandLog.objects.get(demand=demand, event_type=PortalDemandLog.EVENT_FEEDBACK)
        self.assertIn("6/10", log.summary)
        notice = PortalNotification.objects.filter(recipient=self.attendant, demand=demand).latest("id")
        self.assertIn("6/10", notice.title)


class KnowledgeBaseTests(PortalWorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.category = KnowledgeCategory.objects.create(name="Impressão")

    def _entry_payload(self, **overrides):
        payload = {
            "form_id": "entry",
            "kentry-category": str(self.category.id),
            "kentry-title": "Spooler travado",
            "kentry-trigger": "Ninguém imprime no setor.",
            "kentry-description": "O serviço de spooler para depois da atualização.",
            "kentry-resolution": "Reiniciar o spooler e reinstalar o driver.",
        }
        payload.update(overrides)
        return payload

    def test_requester_reads_but_does_not_write_the_base(self):
        self.client.force_login(self.requester)
        self.assertRedirects(self.client.get(reverse("knowledgeEntriesPage")), reverse("knowledgeConsultPage"))
        self.client.post(reverse("knowledgeEntriesPage"), self._entry_payload())
        self.assertFalse(KnowledgeEntry.objects.exists())
        self.assertEqual(self.client.get(reverse("knowledgeConsultPage")).status_code, 200)

    def test_invalid_entry_comes_back_with_the_errors(self):
        self.client.force_login(self.attendant)
        response = self.client.post(reverse("knowledgeEntriesPage"), self._entry_payload(**{"kentry-trigger": ""}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ainda não foi salvo")
        self.assertFalse(KnowledgeEntry.objects.exists())

    def test_valid_entry_opens_its_record_and_can_be_edited_there(self):
        self.client.force_login(self.attendant)
        response = self.client.post(reverse("knowledgeEntriesPage"), self._entry_payload())
        entry = KnowledgeEntry.objects.get()
        self.assertRedirects(response, f"{reverse('knowledgeEntryDetailPage', args=[entry.id])}?saved=1")
        self.assertFalse(entry.is_resolved)

        self.client.post(reverse("knowledgeEntryDetailPage", args=[entry.id]), {
            "kedit-category": str(self.category.id),
            "kedit-title": "Spooler travado",
            "kedit-trigger": "Ninguém imprime no setor.",
            "kedit-description": "O serviço de spooler para depois da atualização.",
            "kedit-resolution": "Reiniciar o spooler e reinstalar o driver.",
            "kedit-is_resolved": "on",
        })
        entry.refresh_from_db()
        self.assertTrue(entry.is_resolved)

    def test_requester_cannot_edit_a_record(self):
        entry = KnowledgeEntry.objects.create(
            category=self.category, title="Registro", trigger="t", description="d", is_resolved=False,
        )
        self.client.force_login(self.requester)
        page = self.client.get(reverse("knowledgeEntryDetailPage", args=[entry.id]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Salvar alterações")
        response = self.client.post(reverse("knowledgeEntryDetailPage", args=[entry.id]), {
            "kedit-category": str(self.category.id), "kedit-title": "Mudou", "kedit-trigger": "t",
            "kedit-description": "d", "kedit-is_resolved": "on",
        })
        self.assertEqual(response.status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.title, "Registro")


class HelpdeskShellTests(PortalWorkflowTestCase):
    def test_attendant_sees_the_queue_knowledge_and_panel_links(self):
        DashboardAccess.objects.create(user=self.attendant, dashboard=Dashboard.objects.get(slug="helpdesk"))
        self.client.force_login(self.attendant)
        html = self.client.get(reverse("portalPendingDemandsPage")).content.decode()
        self.assertIn("MaxiSys · Helpdesk", html)
        self.assertIn(reverse("knowledgeConsultPage"), html)
        self.assertIn(reverse("maxibotPage"), html)
        self.assertIn(reverse("dashesHelpdeskPage"), html)
        # Parâmetros são só de administrador.
        self.assertNotIn(reverse("portalDemandSlaConfigPage"), html)

    def test_requester_menu_has_only_what_is_theirs(self):
        self.client.force_login(self.requester)
        html = self.client.get(reverse("portalDemandPage")).content.decode()
        self.assertIn(reverse("portalMyDemandsPage"), html)
        self.assertNotIn(reverse("knowledgeEntriesPage"), html)
        self.assertNotIn(reverse("portalDemandSlaConfigPage"), html)
        self.assertNotIn(reverse("dashesHelpdeskPage"), html)

    def test_admin_sees_the_parameters(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("portalDemandSlaConfigPage")).content.decode()
        self.assertIn(reverse("portalCompanyConfigPage"), html)
        self.assertIn(reverse("portalDemandResponsesConfigPage"), html)
