from datetime import date, timedelta
import json
import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import UserQueueCreateForm, UserQueueUpdateForm
from .models import (
    MaxiTetrisHighScore,
    KnowledgeCategory,
    KnowledgeEntry,
    PortalCannedResponse,
    PortalDemandAttachment,
    Project,
    ProjectMilestone,
    ProjectRoadmapItem,
    ProjectRoadmapSubtask,
    PortalDemand,
    PortalDemandSlaPolicy,
    PortalDemandMessage,
    PortalDemandCustomField,
    PortalDemandLog,
    PortalDemandCustomValue,
    SystemNotification,
    TaskGroup,
    TaskType,
    PortalRequesterSector,
    PortalRequesterCollaborator,
    PortalRequesterAccount,
    UserQueueCustomValue,
    UserQueueCustomColumn,
    UserQueueCustomColumnOption,
    UserQueueFieldOption,
    UserQueueKanbanColumn,
    concludedTasks,
    userQueue,
)


PORTAL_TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="portal-demand-tests-")


def tearDownModule():
    shutil.rmtree(PORTAL_TEST_MEDIA_ROOT, ignore_errors=True)


class QueuePropertyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            userId="10999",
            username="queue.properties",
            email="queue.properties@example.com",
            nameUser="Queue Properties",
            password="pw123456",
        )
        self.client.login(username="queue.properties", password="pw123456")

    def test_queue_page_seeds_default_field_options(self):
        response = self.client.get(reverse("queueUserPage"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            UserQueueFieldOption.objects.filter(
                user=self.user,
                field_key=UserQueueFieldOption.FIELD_PRIORITY,
                is_active=True,
            ).count(),
            3,
        )
        self.assertEqual(
            UserQueueFieldOption.objects.filter(
                user=self.user,
                field_key=UserQueueFieldOption.FIELD_EFFORT,
                is_active=True,
            ).count(),
            3,
        )

    def test_can_create_custom_priority_option(self):
        response = self.client.post(
            reverse("createUserQueueFieldOption"),
            {
                "field_key": UserQueueFieldOption.FIELD_PRIORITY,
                "label": "Urgente",
                "color": "#b43352",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["option"]["label"], "Urgente")
        self.assertTrue(
            UserQueueFieldOption.objects.filter(
                user=self.user,
                field_key=UserQueueFieldOption.FIELD_PRIORITY,
                label="Urgente",
                color="#b43352",
            ).exists()
        )

    def test_queue_forms_use_user_specific_field_options(self):
        UserQueueFieldOption.objects.create(
            user=self.user,
            field_key=UserQueueFieldOption.FIELD_PRIORITY,
            value="urgent",
            label="Urgente",
            color="#b43352",
            sort_order=50,
        )
        UserQueueFieldOption.objects.create(
            user=self.user,
            field_key=UserQueueFieldOption.FIELD_EFFORT,
            value="gigante",
            label="Gigante",
            color="#6655cc",
            sort_order=60,
        )

        create_form = UserQueueCreateForm(user=self.user)
        update_form = UserQueueUpdateForm(user=self.user)

        self.assertIn(("urgent", "Urgente"), list(create_form.fields["priority_level"].choices))
        self.assertIn(("gigante", "Gigante"), list(create_form.fields["estimated_effort_level"].choices))
        self.assertIn(("urgent", "Urgente"), list(update_form.fields["priority_level"].choices))
        self.assertIn(("gigante", "Gigante"), list(update_form.fields["estimated_effort_level"].choices))

    def test_can_create_select_custom_column_with_initial_option(self):
        response = self.client.post(
            reverse("createUserQueueCustomColumn"),
            {
                "name": "Impacto",
                "field_type": UserQueueCustomColumn.FIELD_SELECT,
                "color": "#4f5f7d",
                "initial_option_label": "Critico",
                "initial_option_color": "#b43352",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        column = UserQueueCustomColumn.objects.get(user=self.user, name="Impacto")
        self.assertEqual(column.field_type, UserQueueCustomColumn.FIELD_SELECT)
        self.assertEqual(column.color, "#4f5f7d")
        self.assertTrue(
            UserQueueCustomColumnOption.objects.filter(
                column=column,
                label="Critico",
                color="#b43352",
            ).exists()
        )
        self.assertEqual(payload["column"]["options"][0]["label"], "Critico")

    def test_can_add_option_to_existing_custom_select_column(self):
        column = UserQueueCustomColumn.objects.create(
            user=self.user,
            name="Sistema",
            field_type=UserQueueCustomColumn.FIELD_SELECT,
            color="#61688c",
        )

        response = self.client.post(
            reverse("createUserQueueCustomColumnOption", args=[column.id]),
            {
                "label": "ERP",
                "color": "#2f9d9c",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(
            UserQueueCustomColumnOption.objects.filter(
                column=column,
                label="ERP",
                color="#2f9d9c",
            ).exists()
        )

    def test_can_delete_field_option_and_reassign_tasks(self):
        old_option = UserQueueFieldOption.objects.create(
            user=self.user,
            field_key=UserQueueFieldOption.FIELD_PRIORITY,
            value="urgent",
            label="Urgente",
            color="#b43352",
            sort_order=40,
        )
        replacement = UserQueueFieldOption.objects.create(
            user=self.user,
            field_key=UserQueueFieldOption.FIELD_PRIORITY,
            value="normal",
            label="Normal",
            color="#2d8f66",
            sort_order=41,
        )
        current_task = userQueue.objects.create(
            user_code=self.user.userId,
            a_description="Fila",
            priority_level=old_option.value,
            estimated_effort_level="medium",
        )
        finished_task = concludedTasks.objects.create(
            user_code=self.user.userId,
            a_description="Concluida",
            priority_level=old_option.value,
            estimated_effort_level="medium",
        )

        response = self.client.post(reverse("deleteUserQueueFieldOption", args=[old_option.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["replacement_value"], replacement.value)
        old_option.refresh_from_db()
        current_task.refresh_from_db()
        finished_task.refresh_from_db()
        self.assertFalse(old_option.is_active)
        self.assertEqual(current_task.priority_level, replacement.value)
        self.assertEqual(finished_task.priority_level, replacement.value)

    def test_cannot_delete_last_field_option(self):
        only_option = UserQueueFieldOption.objects.create(
            user=self.user,
            field_key=UserQueueFieldOption.FIELD_EFFORT,
            value="only",
            label="Unica",
            color="#6655cc",
            sort_order=1,
        )

        response = self.client.post(reverse("deleteUserQueueFieldOption", args=[only_option.id]))

        self.assertEqual(response.status_code, 400)
        only_option.refresh_from_db()
        self.assertTrue(only_option.is_active)

    def test_can_delete_custom_select_option_and_clear_custom_values(self):
        column = UserQueueCustomColumn.objects.create(
            user=self.user,
            name="Sistema",
            field_type=UserQueueCustomColumn.FIELD_SELECT,
            color="#61688c",
        )
        option = UserQueueCustomColumnOption.objects.create(
            column=column,
            value="erp",
            label="ERP",
            color="#2f9d9c",
            sort_order=1,
        )
        task = userQueue.objects.create(
            user_code=self.user.userId,
            a_description="Fila",
            priority_level="medium",
            estimated_effort_level="medium",
        )
        custom_value = UserQueueCustomValue.objects.create(
            queue_item=task,
            column=column,
            value=option.value,
        )

        response = self.client.post(reverse("deleteUserQueueCustomColumnOption", args=[option.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        option.refresh_from_db()
        custom_value.refresh_from_db()
        self.assertFalse(option.is_active)
        self.assertEqual(custom_value.value, "")

    def test_property_payload_reports_usage_and_delete_rules(self):
        priority_option = UserQueueFieldOption.objects.create(
            user=self.user,
            field_key=UserQueueFieldOption.FIELD_PRIORITY,
            value="urgent",
            label="Urgente",
            color="#b43352",
            sort_order=10,
        )
        UserQueueFieldOption.objects.create(
            user=self.user,
            field_key=UserQueueFieldOption.FIELD_PRIORITY,
            value="normal",
            label="Normal",
            color="#2d8f66",
            sort_order=11,
        )
        effort_option = UserQueueFieldOption.objects.create(
            user=self.user,
            field_key=UserQueueFieldOption.FIELD_EFFORT,
            value="gigante",
            label="Gigante",
            color="#6655cc",
            sort_order=10,
        )
        UserQueueFieldOption.objects.create(
            user=self.user,
            field_key=UserQueueFieldOption.FIELD_EFFORT,
            value="medio",
            label="Medio",
            color="#4b668f",
            sort_order=11,
        )
        column = UserQueueCustomColumn.objects.create(
            user=self.user,
            name="Sistema",
            field_type=UserQueueCustomColumn.FIELD_SELECT,
            color="#61688c",
        )
        custom_option = UserQueueCustomColumnOption.objects.create(
            column=column,
            value="erp",
            label="ERP",
            color="#2f9d9c",
            sort_order=1,
        )
        task = userQueue.objects.create(
            user_code=self.user.userId,
            a_description="Fila",
            priority_level=priority_option.value,
            estimated_effort_level=effort_option.value,
        )
        UserQueueCustomValue.objects.create(queue_item=task, column=column, value=custom_option.value)

        response = self.client.post(reverse("queueUserPropertyPayloadData"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        priority_payload = next(item for item in payload["priority_options"] if item["id"] == priority_option.id)
        effort_payload = next(item for item in payload["effort_options"] if item["id"] == effort_option.id)
        custom_column_payload = next(item for item in payload["custom_columns"] if item["id"] == column.id)
        custom_option_payload = next(item for item in custom_column_payload["options"] if item["id"] == custom_option.id)

        self.assertEqual(priority_payload["usage_count"], 1)
        self.assertGreaterEqual(priority_payload["affected_count"], 1)
        self.assertTrue(priority_payload["can_delete"])
        self.assertEqual(effort_payload["usage_count"], 1)
        self.assertEqual(custom_option_payload["usage_count"], 1)
        self.assertEqual(custom_column_payload["option_count"], 1)

    def test_property_payload_status_options_include_name_and_label(self):
        backlog = UserQueueKanbanColumn.objects.create(
            user=self.user,
            name="Backlog",
            color="#343955",
            sort_order=1,
            is_active=True,
        )
        doing = UserQueueKanbanColumn.objects.create(
            user=self.user,
            name="Em andamento",
            color="#4f5f7d",
            sort_order=2,
            is_active=True,
        )
        userQueue.objects.create(
            user_code=self.user.userId,
            a_description="Demanda teste",
            priority_level=userQueue.PRIORITY_MEDIUM,
            estimated_effort_level=userQueue.ESTIMATE_MEDIUM,
            kanban_column=doing,
        )

        response = self.client.post(reverse("queueUserPropertyPayloadData"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        backlog_payload = next(item for item in payload["status_options"] if item["id"] == backlog.id)
        doing_payload = next(item for item in payload["status_options"] if item["id"] == doing.id)
        self.assertEqual(backlog_payload["name"], "Backlog")
        self.assertEqual(backlog_payload["label"], "Backlog")
        self.assertEqual(backlog_payload["value"], str(backlog.id))
        self.assertEqual(doing_payload["name"], "Em andamento")
        self.assertEqual(doing_payload["usage_count"], 1)

    def test_inline_update_persists_kanban_column_without_clearing_task_data(self):
        group = TaskGroup.objects.create(name="Infraestrutura")
        task_type = TaskType.objects.create(group=group, name="Python", color="#5CD6A3")
        backlog = UserQueueKanbanColumn.objects.create(
            user=self.user,
            name="Backlog",
            color="#343955",
            sort_order=1,
            is_active=True,
        )
        doing = UserQueueKanbanColumn.objects.create(
            user=self.user,
            name="Em andamento",
            color="#4f5f7d",
            sort_order=2,
            is_active=True,
        )
        item = userQueue.objects.create(
            user_code=self.user.userId,
            a_ticket="SM-2001",
            a_description="Ajustar integracao",
            priority_level=userQueue.PRIORITY_MEDIUM,
            estimated_effort_level=userQueue.ESTIMATE_MEDIUM,
            task_group=group,
            n_type_group=group.id,
            task_type=task_type,
            n_type_code=task_type.id,
            kanban_column=backlog,
            n_queue_position=1,
            kanban_sort_order=1,
        )

        response = self.client.post(
            reverse("updateQueueItem", args=[item.n_register]),
            {"kanban_column": str(doing.id)},
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.kanban_column_id, doing.id)
        self.assertEqual(item.a_description, "Ajustar integracao")
        self.assertEqual(item.task_type_id, task_type.id)
        self.assertEqual(item.task_group_id, group.id)


class QueueExtraCollaboratorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            userId="21001",
            username="queue.owner",
            email="queue.owner@example.com",
            nameUser="Queue Owner",
            password="pw123456",
        )
        self.collaborator = User.objects.create_user(
            userId="21002",
            username="queue.collab",
            email="queue.collab@example.com",
            nameUser="Queue Collaborator",
            password="pw123456",
        )
        self.second_collaborator = User.objects.create_user(
            userId="21003",
            username="queue.collab.2",
            email="queue.collab2@example.com",
            nameUser="Queue Collaborator 2",
            password="pw123456",
        )
        self.client.login(username="queue.owner", password="pw123456")

    def _create_item(self, description="Demanda com colaboradores"):
        return userQueue.objects.create(
            user_code=self.owner.userId,
            a_ticket="SM-1001",
            a_description=description,
            priority_level=userQueue.PRIORITY_MEDIUM,
            estimated_effort_level=userQueue.ESTIMATE_MEDIUM,
            n_queue_position=1,
            kanban_sort_order=1,
        )

    def test_queue_item_details_includes_extra_collaborators(self):
        item = self._create_item()
        item.extra_collaborators.set([self.collaborator, self.second_collaborator])

        response = self.client.get(reverse("queueItemDetails", args=[item.n_register]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            sorted(payload["extra_collaborators_ids"]),
            sorted([self.collaborator.id, self.second_collaborator.id]),
        )
        self.assertEqual(len(payload["extra_collaborators"]), 2)
        self.assertEqual(payload["extra_collaborators"][0]["initials"], "QC")

    def test_update_queue_item_can_replace_extra_collaborators(self):
        item = self._create_item()
        item.extra_collaborators.set([self.collaborator])

        response = self.client.post(
            reverse("updateQueueItem", args=[item.n_register]),
            {
                "extra_collaborators_present": "1",
                "extra_collaborators": [str(self.second_collaborator.id)],
            },
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(
            list(item.extra_collaborators.order_by("id").values_list("id", flat=True)),
            [self.second_collaborator.id],
        )

    def test_end_queue_item_copies_extra_collaborators_to_concluded(self):
        item = self._create_item(description="Demanda finalizada")
        item.extra_collaborators.set([self.collaborator])

        response = self.client.post(reverse("endQueueItem", args=[item.n_register]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(userQueue.objects.filter(n_register=item.n_register).exists())
        concluded = concludedTasks.objects.get(a_description="Demanda finalizada", user_code=self.owner.userId)
        self.assertEqual(list(concluded.extra_collaborators.values_list("id", flat=True)), [self.collaborator.id])

    def test_demand_detail_page_updates_extra_collaborators(self):
        item = self._create_item(description="Demanda detalhe")

        response = self.client.post(
            reverse("queueDemandDetailPage", args=[item.n_register]),
            {
                "form_id": "main",
                "a_ticket": item.a_ticket,
                "a_description": item.a_description,
                "task_group": "",
                "task_type": "",
                "linked_project": "",
                "predicted_start_dt": "",
                "predicted_end_dt": "",
                "real_start_dt": "",
                "real_end_dt": "",
                "a_demand_detail": "Detalhe importado",
                "extra_collaborators": [str(self.collaborator.id), str(self.second_collaborator.id)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.a_demand_detail, "Detalhe importado")
        self.assertEqual(
            sorted(item.extra_collaborators.values_list("id", flat=True)),
            sorted([self.collaborator.id, self.second_collaborator.id]),
        )


class ProjectCatalogPageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.viewer = User.objects.create_user(
            userId="21991",
            username="project.viewer",
            email="project.viewer@example.com",
            nameUser="Visualizador de Projetos",
            password="pw123456",
        )
        self.responsible = User.objects.create_user(
            userId="21992",
            username="project.owner",
            email="project.owner@example.com",
            nameUser="Responsavel Principal",
            password="pw123456",
        )
        self.other_responsible = User.objects.create_user(
            userId="21993",
            username="project.other",
            email="project.other@example.com",
            nameUser="Outro Responsavel",
            password="pw123456",
        )
        self.participant = User.objects.create_user(
            userId="21994",
            username="project.participant",
            email="project.participant@example.com",
            nameUser="Participante Chave",
            password="pw123456",
        )
        self.other_participant = User.objects.create_user(
            userId="21995",
            username="project.otherparticipant",
            email="project.otherparticipant@example.com",
            nameUser="Participante Extra",
            password="pw123456",
        )
        self.client.login(username="project.viewer", password="pw123456")

        self.matching_project = Project.objects.create(
            name="Projeto Alfa Roadmap",
            description="Projeto de teste com filtro completo.",
            developer=self.responsible,
            status="active",
            color="#343955",
            start_date=date(2026, 7, 10),
            end_date=date(2026, 8, 10),
        )
        self.matching_project.participants.set([self.participant])

        self.other_project = Project.objects.create(
            name="Projeto Beta",
            description="Outro projeto fora do filtro.",
            developer=self.other_responsible,
            status="active",
            color="#00bf63",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 30),
        )
        self.other_project.participants.set([self.other_participant])
        self.roadmap_item = ProjectRoadmapItem.objects.create(
            project=self.matching_project,
            responsible=self.responsible,
            title="Planejar entrega",
            description="Organizar entregas do projeto.",
            status="doing",
            start_date=date(2026, 7, 12),
            end_date=date(2026, 8, 2),
            sort_order=1,
        )
        ProjectRoadmapSubtask.objects.create(
            roadmap_item=self.roadmap_item,
            description="Validar cronograma",
            is_done=True,
            sort_order=1,
        )

    def test_open_project_catalog_filters_projects(self):
        response = self.client.get(
            reverse("projectCatalogPage"),
            {
                "q": "Alfa",
                "responsible": str(self.responsible.id),
                "participant": str(self.participant.id),
                "date_from": "2026-07-01",
                "date_to": "2026-08-31",
            },
        )

        self.assertEqual(response.status_code, 200)
        projects = list(response.context["projects"])
        self.assertEqual([project.id for project in projects], [self.matching_project.id])
        self.assertEqual(response.context["filters"]["q"], "Alfa")

    def test_open_project_catalog_updates_project_color(self):
        response = self.client.post(
            reverse("projectCatalogPage"),
            {
                "form_id": "update_project_color",
                "project_id": str(self.matching_project.id),
                "color": "#112233",
                "return_query": "q=Alfa",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("?q=Alfa"))
        self.matching_project.refresh_from_db()
        self.assertEqual(self.matching_project.color, "#112233")

    def test_open_project_catalog_updates_project_from_popup(self):
        response = self.client.post(
            reverse("projectCatalogPage"),
            {
                "form_id": "edit_project_catalog",
                "project_id": str(self.matching_project.id),
                "return_query": "q=Alfa",
                "name": "Projeto Alfa Atualizado",
                "description": "Descricao ajustada para o projeto.",
                "developer_id": str(self.other_responsible.id),
                "status": "paused",
                "color": "#abcdef",
                "start_date": "2026-07-15",
                "end_date": "2026-08-18",
                "participants_ids": [str(self.participant.id), str(self.other_participant.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("?q=Alfa"))
        self.matching_project.refresh_from_db()
        self.assertEqual(self.matching_project.name, "Projeto Alfa Atualizado")
        self.assertEqual(self.matching_project.description, "Descricao ajustada para o projeto.")
        self.assertEqual(self.matching_project.developer_id, self.other_responsible.id)
        self.assertEqual(self.matching_project.status, "paused")
        self.assertEqual(self.matching_project.color, "#abcdef")
        self.assertEqual(self.matching_project.start_date, date(2026, 7, 15))
        self.assertEqual(self.matching_project.end_date, date(2026, 8, 18))
        self.assertEqual(
            sorted(self.matching_project.participants.values_list("id", flat=True)),
            sorted([self.participant.id, self.other_participant.id]),
        )

    def test_open_project_catalog_can_conclude_project(self):
        response = self.client.post(
            reverse("projectCatalogPage"),
            {
                "form_id": "conclude_project_catalog",
                "project_id": str(self.matching_project.id),
                "return_query": "q=Alfa",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("?q=Alfa"))
        self.matching_project.refresh_from_db()
        self.assertEqual(self.matching_project.status, "done")

    def test_can_export_project_pdf(self):
        response = self.client.get(reverse("projectCatalogExportPdf", args=[self.matching_project.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))


class ProjectRoadmapSubtaskTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            userId="20888",
            username="roadmap.editor",
            email="roadmap.editor@example.com",
            nameUser="Roadmap Editor",
            password="pw123456",
        )
        self.assignee = User.objects.create_user(
            userId="20889",
            username="roadmap.assignee",
            email="roadmap.assignee@example.com",
            nameUser="Roadmap Responsavel",
            password="pw123456",
        )
        self.client.login(username="roadmap.editor", password="pw123456")
        self.project = Project.objects.create(
            name="Projeto Roadmap Teste",
            description="Projeto para validar o roadmap.",
            developer=self.user,
            status="active",
            color="#1c2242",
        )
        self.item = ProjectRoadmapItem.objects.create(
            project=self.project,
            title="Etapa inicial",
            description="Descricao inicial",
            status="planned",
            sort_order=1,
        )

    def test_can_update_roadmap_item(self):
        response = self.client.post(
            reverse("projectRoadmapItemUpdate", args=[self.project.id, self.item.id]),
            data=f'{{"title":"Etapa revisada","description":"Nova descricao","status":"doing","start_date":"2026-06-20","end_date":"2026-06-28","responsible_id":"{self.assignee.id}"}}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.title, "Etapa revisada")
        self.assertEqual(self.item.description, "Nova descricao")
        self.assertEqual(self.item.status, "doing")
        self.assertEqual(self.item.start_date.isoformat(), "2026-06-20")
        self.assertEqual(self.item.end_date.isoformat(), "2026-06-28")
        self.assertEqual(self.item.responsible_id, self.assignee.id)
        self.assertEqual(response.json()["item"]["responsible_name"], "Roadmap Responsavel")

    def test_can_conclude_and_reopen_roadmap_item(self):
        conclude_response = self.client.post(
            reverse("projectRoadmapItemConclude", args=[self.project.id, self.item.id]),
            content_type="application/json",
        )

        self.assertEqual(conclude_response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "done")
        self.assertEqual(conclude_response.json()["item"]["status"], "done")

        reopen_response = self.client.post(
            reverse("projectRoadmapItemReopen", args=[self.project.id, self.item.id]),
            content_type="application/json",
        )

        self.assertEqual(reopen_response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "doing")
        self.assertEqual(reopen_response.json()["item"]["status"], "doing")

    def test_can_add_toggle_update_and_delete_roadmap_subtask(self):
        create_response = self.client.post(
            reverse("projectRoadmapSubtaskCreate", args=[self.project.id, self.item.id]),
            data='{"description":"Validar entrega"}',
            content_type="application/json",
        )

        self.assertEqual(create_response.status_code, 200)
        subtask_id = create_response.json()["subtask"]["id"]
        subtask = ProjectRoadmapSubtask.objects.get(pk=subtask_id)
        self.assertEqual(subtask.description, "Validar entrega")
        self.assertFalse(subtask.is_done)

        toggle_response = self.client.post(
            reverse("projectRoadmapSubtaskToggle", args=[self.project.id, self.item.id, subtask.id]),
            data='{"is_done":true}',
            content_type="application/json",
        )

        self.assertEqual(toggle_response.status_code, 200)
        subtask.refresh_from_db()
        self.assertTrue(subtask.is_done)

        update_response = self.client.post(
            reverse("projectRoadmapSubtaskUpdate", args=[self.project.id, self.item.id, subtask.id]),
            data='{"description":"Validar entrega com usuario"}',
            content_type="application/json",
        )

        self.assertEqual(update_response.status_code, 200)
        subtask.refresh_from_db()
        self.assertEqual(subtask.description, "Validar entrega com usuario")

        delete_response = self.client.post(
            reverse("projectRoadmapSubtaskDelete", args=[self.project.id, self.item.id, subtask.id]),
            content_type="application/json",
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(ProjectRoadmapSubtask.objects.filter(pk=subtask_id).exists())

    def test_roadmap_page_contains_subtasks_payload(self):
        self.item.responsible = self.assignee
        self.item.save(update_fields=["responsible"])
        ProjectRoadmapSubtask.objects.create(
            roadmap_item=self.item,
            description="Subtarefa no payload",
            is_done=True,
            sort_order=1,
        )

        response = self.client.get(reverse("projectRoadmapView", args=[self.project.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Subtarefa no payload")
        self.assertContains(response, "Roadmap Responsavel")

    def test_roadmap_page_marks_overdue_items(self):
        self.item.status = "doing"
        self.item.end_date = timezone.localdate() - timedelta(days=3)
        self.item.save(update_fields=["status", "end_date"])

        response = self.client.get(reverse("projectRoadmapView", args=[self.project.id]))

        self.assertEqual(response.status_code, 200)
        payload = next(item for item in response.context["items"] if item["id"] == self.item.id)
        self.assertEqual(response.context["overdue_count"], 1)
        self.assertTrue(payload["is_overdue"])
        self.assertEqual(payload["overdue_days"], 3)
        self.assertEqual(payload["overdue_label"], "3 dia(s) em atraso")

    def test_can_create_update_and_toggle_project_milestone(self):
        create_response = self.client.post(
            reverse("projectMilestoneCreate", args=[self.project.id]),
            data=f'{{"milestone_key":"homologation","description":"Liberacao final","target_date":"2026-08-10","anchor_item_id":"{self.item.id}"}}',
            content_type="application/json",
        )

        self.assertEqual(create_response.status_code, 200)
        milestone_id = create_response.json()["milestone"]["id"]
        milestone = ProjectMilestone.objects.get(pk=milestone_id)
        self.assertEqual(milestone.title, "Homologacao")
        self.assertEqual(milestone.milestone_key, "homologation")
        self.assertEqual(milestone.description, "Liberacao final")
        self.assertEqual(milestone.target_date.isoformat(), "2026-08-10")
        self.assertEqual(milestone.color, "#f5b55e")
        self.assertEqual(milestone.anchor_item_id, self.item.id)
        self.assertFalse(milestone.is_done)

        update_response = self.client.post(
            reverse("projectMilestoneUpdate", args=[self.project.id, milestone.id]),
            data='{"milestone_key":"deployment","description":"Virada oficial","target_date":"2026-08-12","anchor_item_id":""}',
            content_type="application/json",
        )

        self.assertEqual(update_response.status_code, 200)
        milestone.refresh_from_db()
        self.assertEqual(milestone.title, "Implantacao")
        self.assertEqual(milestone.milestone_key, "deployment")
        self.assertEqual(milestone.description, "Virada oficial")
        self.assertEqual(milestone.target_date.isoformat(), "2026-08-12")
        self.assertEqual(milestone.color, "#ff8b5d")
        self.assertIsNone(milestone.anchor_item_id)

        move_response = self.client.post(
            reverse("projectMilestoneMove", args=[self.project.id, milestone.id]),
            data='{"direction":"right"}',
            content_type="application/json",
        )

        self.assertEqual(move_response.status_code, 200)
        milestone.refresh_from_db()
        self.assertEqual(milestone.anchor_item_id, self.item.id)

        toggle_response = self.client.post(
            reverse("projectMilestoneToggle", args=[self.project.id, milestone.id]),
            data='{"is_done":true}',
            content_type="application/json",
        )

        self.assertEqual(toggle_response.status_code, 200)
        milestone.refresh_from_db()
        self.assertTrue(milestone.is_done)
        self.assertIsNotNone(milestone.completed_at)
        self.assertEqual(toggle_response.json()["milestone"]["status_label"], "Concluido")

    def test_roadmap_page_contains_milestones_payload(self):
        milestone = ProjectMilestone.objects.create(
            project=self.project,
            title="Marco de validacao",
            description="Checklist aprovado",
            target_date=date(2026, 8, 1),
            color="#0f9d58",
            sort_order=1,
        )

        response = self.client.get(reverse("projectRoadmapView", args=[self.project.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Marco de validacao")
        payload = next(entry for entry in response.context["milestones"] if entry["id"] == milestone.id)
        self.assertEqual(payload["title"], "Marco de validacao")
        self.assertEqual(payload["target_date"], "2026-08-01")
        self.assertEqual(payload["color"], "#0f9d58")
        self.assertEqual(payload["milestone_key"], "analysis")
        self.assertEqual(payload["anchor_item_id"], "")


class MaxiTetrisHighScoreTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            userId="30999",
            username="tetris.player",
            email="tetris.player@example.com",
            nameUser="Jogador Atual",
            password="pw123456",
        )
        self.other_user = User.objects.create_user(
            userId="31000",
            username="tetris.rival",
            email="tetris.rival@example.com",
            nameUser="Rival",
            password="pw123456",
        )
        MaxiTetrisHighScore.objects.create(
            user=self.other_user,
            best_score=600,
            best_lines=8,
            best_level=2,
        )
        self.client.login(username="tetris.player", password="pw123456")

    def test_submit_score_creates_personal_best_and_returns_top_ten(self):
        response = self.client.post(
            reverse("maxiTetrisSubmitScore"),
            data='{"score":950,"lines":12,"level":3}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        record = MaxiTetrisHighScore.objects.get(user=self.user)
        self.assertEqual(record.best_score, 950)
        self.assertEqual(record.best_lines, 12)
        self.assertEqual(record.best_level, 3)

        data = response.json()
        self.assertTrue(data["saved"])
        self.assertEqual(data["leaderboard"][0]["user_name"], "Jogador Atual")
        self.assertTrue(data["leaderboard"][0]["is_current_user"])
        self.assertEqual(data["personal_best"]["score"], 950)

    def test_lower_score_does_not_override_existing_record(self):
        MaxiTetrisHighScore.objects.create(
            user=self.user,
            best_score=1200,
            best_lines=16,
            best_level=4,
        )

        response = self.client.post(
            reverse("maxiTetrisSubmitScore"),
            data='{"score":400,"lines":5,"level":2}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        record = MaxiTetrisHighScore.objects.get(user=self.user)
        self.assertEqual(record.best_score, 1200)
        self.assertEqual(record.best_lines, 16)
        self.assertEqual(record.best_level, 4)
        self.assertFalse(response.json()["saved"])

    def test_can_fetch_leaderboard_snapshot(self):
        MaxiTetrisHighScore.objects.create(
            user=self.user,
            best_score=700,
            best_lines=10,
            best_level=3,
        )

        response = self.client.get(reverse("maxiTetrisHighscores"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(len(data["leaderboard"]), 2)
        self.assertEqual(data["personal_best"]["score"], 700)


@override_settings(MEDIA_ROOT=PORTAL_TEST_MEDIA_ROOT)
class PortalDemandFlowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.requester = User.objects.create_user(
            userId="41001",
            username="portal.requester",
            email="portal.requester@example.com",
            nameUser="Solicitante Portal",
            password="pw123456",
        )
        self.admin = User.objects.create_user(
            userId="41002",
            username="portal.admin",
            email="portal.admin@example.com",
            nameUser="Administrador Portal",
            password="pw123456",
            is_system_admin=True,
        )
        self.admin_two = User.objects.create_user(
            userId="41003",
            username="portal.admin.two",
            email="portal.admin.two@example.com",
            nameUser="Atendente Secundario",
            password="pw123456",
            is_system_admin=True,
        )
        self.group = TaskGroup.objects.create(name="Infraestrutura Portal")
        self.task_type = TaskType.objects.create(group=self.group, name="Acesso", color="#4567aa")

    def test_requester_can_create_portal_demand(self):
        self.client.login(username="portal.requester", password="pw123456")

        response = self.client.post(
            reverse("portalDemandCreatePage"),
            {
                "title": "Falha no acesso VPN",
                "description": "Usuário sem acesso à VPN corporativa.",
                "task_group": str(self.group.id),
                "task_type": str(self.task_type.id),
                "priority_level": userQueue.PRIORITY_HIGH,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand = PortalDemand.objects.get()
        self.assertEqual(demand.requester, self.requester)
        self.assertEqual(demand.status, PortalDemand.STATUS_PENDING)
        self.assertEqual(demand.task_group, self.group)
        self.assertEqual(demand.task_type, self.task_type)
        self.assertEqual(demand.priority_level, userQueue.PRIORITY_HIGH)

    def test_portal_home_separates_dashboard_from_actions(self):
        self.client.login(username="portal.requester", password="pw123456")

        response = self.client.get(reverse("portalDemandPage"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("portalDemandCreatePage"))
        self.assertContains(response, reverse("portalMyDemandsPage"))
        self.assertContains(response, "Total de demandas")

    def test_admin_can_create_portal_requester_structure_and_account(self):
        self.client.login(username="portal.admin", password="pw123456")

        sector_response = self.client.post(
            reverse("portalRequesterAdminPage"),
            {
                "form_type": "create_sector",
                "portal_sector-name": "Comercial",
                "portal_sector-description": "Equipe solicitante comercial.",
                "portal_sector-is_active": "on",
            },
        )
        self.assertEqual(sector_response.status_code, 302)

        sector = PortalRequesterSector.objects.get(name="Comercial")
        collaborator_response = self.client.post(
            reverse("portalRequesterAdminPage"),
            {
                "form_type": "create_collaborator",
                "portal_collaborator-sector": str(sector.id),
                "portal_collaborator-full_name": "Maria do Comercial",
                "portal_collaborator-registration_code": "55001",
                "portal_collaborator-email": "maria.comercial@example.com",
                "portal_collaborator-role_title": "Analista",
                "portal_collaborator-phone": "47999999999",
                "portal_collaborator-notes": "Solicitante principal do setor.",
                "portal_collaborator-is_active": "on",
            },
        )
        self.assertEqual(collaborator_response.status_code, 302)

        collaborator = PortalRequesterCollaborator.objects.get(registration_code="55001")
        account_response = self.client.post(
            reverse("portalRequesterAdminPage"),
            {
                "form_type": "create_account",
                "portal_account-collaborator": str(collaborator.id),
                "portal_account-username": "maria.comercial",
                "portal_account-password": "pw123456",
                "portal_account-is_active": "on",
            },
        )
        self.assertEqual(account_response.status_code, 302)

        account = PortalRequesterAccount.objects.select_related("user", "collaborator", "collaborator__sector").get(
            collaborator=collaborator
        )
        self.assertEqual(account.user.username, "maria.comercial")
        self.assertEqual(account.user.userId, "55001")
        self.assertEqual(account.user.email, "maria.comercial@example.com")
        self.assertTrue(account.user.is_active)

    def test_admin_can_update_portal_requester_account_login(self):
        self.client.login(username="portal.admin", password="pw123456")

        sector = PortalRequesterSector.objects.create(name="Financeiro", is_active=True)
        collaborator = PortalRequesterCollaborator.objects.create(
            sector=sector,
            full_name="Paula Financeiro",
            registration_code="55004",
            email="paula.financeiro@example.com",
            is_active=True,
        )
        linked_user = get_user_model().objects.create_user(
            userId="55004",
            username="paula.financeiro",
            email="paula.financeiro@example.com",
            nameUser="Paula Financeiro",
            password="pw123456",
        )
        account = PortalRequesterAccount.objects.create(
            collaborator=collaborator,
            user=linked_user,
            is_active=True,
            created_by=self.admin,
        )

        response = self.client.post(
            reverse("portalRequesterAdminPage"),
            {
                "form_type": "update_account",
                "account_id": str(account.id),
                "portal_account-collaborator": str(collaborator.id),
                "portal_account-username": "paula.portal",
                "portal_account-password": "",
                "portal_account-is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        account.user.refresh_from_db()
        self.assertEqual(account.user.username, "paula.portal")

    def test_unlinked_user_cannot_open_new_demand_when_requester_access_is_enabled(self):
        sector = PortalRequesterSector.objects.create(name="RH", is_active=True)
        collaborator = PortalRequesterCollaborator.objects.create(
            sector=sector,
            full_name="Solicitante Liberado",
            registration_code="55002",
            email="solicitante.liberado@example.com",
            is_active=True,
        )
        linked_user = get_user_model().objects.create_user(
            userId="55002",
            username="solicitante.liberado",
            email="solicitante.liberado@example.com",
            nameUser="Solicitante Liberado",
            password="pw123456",
        )
        PortalRequesterAccount.objects.create(collaborator=collaborator, user=linked_user, is_active=True)

        unauthorized_user = get_user_model().objects.create_user(
            userId="55003",
            username="solicitante.sem.acesso",
            email="solicitante.sem.acesso@example.com",
            nameUser="Solicitante Sem Acesso",
            password="pw123456",
        )

        self.client.login(username="solicitante.sem.acesso", password="pw123456")
        response = self.client.get(reverse("portalDemandCreatePage"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("?requester_denied=1", response.url)

    def test_portal_demand_generates_public_code_and_opens_by_code_link(self):
        self.client.login(username="portal.requester", password="pw123456")

        response = self.client.post(
            reverse("portalDemandCreatePage"),
            {
                "title": "Ajuste de acesso no portal",
                "description": "Liberar acesso para o ambiente de homologação.",
                "task_group": str(self.group.id),
                "task_type": str(self.task_type.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand = PortalDemand.objects.get(title="Ajuste de acesso no portal")
        self.assertRegex(demand.protocol, r"^\d{3}-[A-Z]{5}$")

        detail_response = self.client.get(reverse("portalDemandCodeDetailPage", args=[demand.protocol]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, demand.title)

    def test_existing_portal_demand_without_public_code_is_backfilled_on_access(self):
        self.client.login(username="portal.requester", password="pw123456")
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Demanda legada",
            description="Registro criado antes da padronizaÃ§Ã£o do cÃ³digo pÃºblico.",
            task_group=self.group,
            task_type=self.task_type,
        )
        PortalDemand.objects.filter(pk=demand.pk).update(access_code=None)

        demand.refresh_from_db()
        self.assertIsNone(demand.access_code)

        detail_url = demand.get_absolute_url()
        demand.refresh_from_db()

        self.assertRegex(demand.access_code, r"^\d{3}-[A-Z]{5}$")
        self.assertEqual(detail_url, reverse("portalDemandCodeDetailPage", args=[demand.access_code]))

    def test_admin_can_add_custom_opening_field_and_requester_saves_value(self):
        self.client.login(username="portal.admin", password="pw123456")

        response = self.client.post(
            reverse("portalDemandFieldsConfigPage"),
            {
                "form_type": "create_custom_field",
                "portal_field-label": "Filial solicitante",
                "portal_field-field_type": "text",
                "portal_field-placeholder": "Ex.: Matriz",
                "portal_field-help_text": "Informe a filial que originou a solicitação.",
                "portal_field-is_required": "on",
                "portal_field-initial_option_label": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        custom_field = PortalDemandCustomField.objects.get(label="Filial solicitante")

        self.client.logout()
        self.client.login(username="portal.requester", password="pw123456")
        create_response = self.client.post(
            reverse("portalDemandCreatePage"),
            {
                "title": "Liberar acesso por filial",
                "description": "Solicitação vinda da unidade central.",
                "task_group": str(self.group.id),
                "task_type": str(self.task_type.id),
                f"custom_field_{custom_field.id}": "Joinville",
            },
            follow=True,
        )

        self.assertEqual(create_response.status_code, 200)
        demand = PortalDemand.objects.get(title="Liberar acesso por filial")
        self.assertTrue(
            PortalDemandCustomValue.objects.filter(
                demand=demand,
                field=custom_field,
                value="Joinville",
            ).exists()
        )

    def test_admin_can_transfer_demand_and_log_action(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Transferir atendimento",
            description="Demanda para validar troca de atendente.",
            task_group=self.group,
            task_type=self.task_type,
        )

        self.client.login(username="portal.admin", password="pw123456")
        self.client.post(reverse("portalDemandAssume", args=[demand.id]), {"next": demand.get_absolute_url()}, follow=True)

        response = self.client.post(
            reverse("portalDemandDetailPage", args=[demand.id]),
            {
                "form_type": "transfer",
                "target_attendant": str(self.admin_two.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand.refresh_from_db()
        self.assertEqual(demand.assigned_to, self.admin_two)
        self.assertEqual(demand.status, PortalDemand.STATUS_ASSUMED)
        self.assertIsNotNone(demand.linked_queue_item)
        self.assertEqual(demand.linked_queue_item.user_code, self.admin_two.userId)
        self.assertTrue(
            PortalDemandLog.objects.filter(
                demand=demand,
                event_type=PortalDemandLog.EVENT_TRANSFERRED,
                to_attendant=self.admin_two,
            ).exists()
        )

    def test_attendant_reply_can_record_worked_time(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Apontamento de tempo",
            description="Demanda para validar horas trabalhadas.",
            task_group=self.group,
            task_type=self.task_type,
            assigned_to=self.admin,
            status=PortalDemand.STATUS_ASSUMED,
            assumed_at=timezone.now(),
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.post(
            reverse("portalDemandDetailPage", args=[demand.id]),
            {
                "form_type": "reply",
                "message": "Atendimento executado.",
                "work_started_at": "2026-07-14T08:00",
                "work_ended_at": "2026-07-14T09:30",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        message = PortalDemandMessage.objects.filter(demand=demand).latest("id")
        self.assertEqual(message.worked_minutes, 90)
        self.assertTrue(
            PortalDemandLog.objects.filter(
                demand=demand,
                event_type=PortalDemandLog.EVENT_WORKLOG,
                related_message=message,
            ).exists()
        )

    def test_requester_can_create_portal_demand_with_initial_attachment(self):
        self.client.login(username="portal.requester", password="pw123456")

        response = self.client.post(
            reverse("portalDemandCreatePage"),
            {
                "title": "Erro com anexo",
                "description": "Segue imagem do problema.",
                "task_group": str(self.group.id),
                "task_type": str(self.task_type.id),
                "attachments": [
                    SimpleUploadedFile("evidencia.txt", b"teste do anexo", content_type="text/plain"),
                ],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand = PortalDemand.objects.get(title="Erro com anexo")
        self.assertTrue(
            PortalDemandAttachment.objects.filter(
                demand=demand,
                message__isnull=True,
                original_name="evidencia.txt",
            ).exists()
        )

    def test_admin_can_assume_portal_demand_and_create_queue_item(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Atualizar cadastro do colaborador",
            description="Necessário revisar permissões do usuário no ambiente interno.",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_MEDIUM,
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.post(reverse("portalDemandAssume", args=[demand.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_ASSUMED)
        self.assertEqual(demand.assigned_to, self.admin)
        self.assertIsNotNone(demand.linked_queue_item)
        self.assertEqual(demand.linked_queue_item.user_code, self.admin.userId)
        self.assertEqual(demand.linked_queue_item.a_ticket, demand.protocol)
        self.assertEqual(demand.linked_queue_item.a_description, demand.title)
        self.assertEqual(demand.linked_queue_item.task_type, self.task_type)

    def test_concluding_assumed_queue_item_marks_portal_demand_completed(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Revisar permissões do BI",
            description="Precisa validar acesso de leitura aos painéis.",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_LOW,
        )

        self.client.login(username="portal.admin", password="pw123456")
        self.client.post(reverse("portalDemandAssume", args=[demand.id]))
        demand.refresh_from_db()

        response = self.client.post(reverse("endQueueItem", args=[demand.linked_queue_item.n_register]))

        self.assertEqual(response.status_code, 200)
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_COMPLETED)
        self.assertIsNone(demand.linked_queue_item)
        self.assertIsNotNone(demand.completed_at)
        self.assertTrue(
            concludedTasks.objects.filter(
                user_code=self.admin.userId,
                a_ticket=demand.protocol,
                a_description=demand.title,
            ).exists()
        )

    def test_deleting_assumed_queue_item_returns_portal_demand_to_pending(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Ajustar relatório diário",
            description="Campo de filial está com divergência.",
            task_group=self.group,
            task_type=self.task_type,
        )

        self.client.login(username="portal.admin", password="pw123456")
        self.client.post(reverse("portalDemandAssume", args=[demand.id]))
        demand.refresh_from_db()

        response = self.client.post(reverse("deleteQueueItem", args=[demand.linked_queue_item.n_register]))

        self.assertEqual(response.status_code, 200)
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_PENDING)
        self.assertIsNone(demand.assigned_to)
        self.assertIsNone(demand.linked_queue_item)

    def test_admin_can_complete_portal_demand_from_detail_workflow(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Concluir via workflow",
            description="Fluxo para concluir direto pelo portal.",
            task_group=self.group,
            task_type=self.task_type,
        )

        self.client.login(username="portal.admin", password="pw123456")
        self.client.post(reverse("portalDemandAssume", args=[demand.id]))
        demand.refresh_from_db()
        queue_register = demand.linked_queue_item.n_register

        response = self.client.post(
            reverse("portalDemandDetailPage", args=[demand.id]),
            {
                "form_type": "workflow",
                "workflow_action": "complete",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_COMPLETED)
        self.assertIsNotNone(demand.completed_at)
        self.assertIsNone(demand.linked_queue_item)
        self.assertFalse(userQueue.objects.filter(n_register=queue_register).exists())
        self.assertTrue(
            concludedTasks.objects.filter(
                user_code=self.admin.userId,
                a_ticket=demand.protocol,
                a_description=demand.title,
            ).exists()
        )
        self.assertTrue(
            PortalDemandLog.objects.filter(
                demand=demand,
                event_type=PortalDemandLog.EVENT_COMPLETED,
            ).exists()
        )

    def test_admin_can_cancel_portal_demand_from_detail_workflow(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Cancelar via workflow",
            description="Fluxo para cancelar direto pelo portal.",
            task_group=self.group,
            task_type=self.task_type,
        )

        self.client.login(username="portal.admin", password="pw123456")
        self.client.post(reverse("portalDemandAssume", args=[demand.id]))
        demand.refresh_from_db()
        queue_register = demand.linked_queue_item.n_register

        response = self.client.post(
            reverse("portalDemandDetailPage", args=[demand.id]),
            {
                "form_type": "workflow",
                "workflow_action": "cancel",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_CANCELLED)
        self.assertIsNone(demand.linked_queue_item)
        self.assertIsNone(demand.completed_at)
        self.assertFalse(userQueue.objects.filter(n_register=queue_register).exists())
        self.assertFalse(
            concludedTasks.objects.filter(
                user_code=self.admin.userId,
                a_ticket=demand.protocol,
                a_description=demand.title,
            ).exists()
        )
        self.assertTrue(
            PortalDemandLog.objects.filter(
                demand=demand,
                event_type=PortalDemandLog.EVENT_CANCELLED,
            ).exists()
        )

    def test_pending_page_highlights_triage_urgency(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="VPN sem retorno",
            description="Chamado aguardando triagem urgente.",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_HIGH,
        )
        PortalDemand.objects.filter(pk=demand.pk).update(
            first_response_due_at=timezone.now() - timedelta(minutes=45),
            resolution_due_at=timezone.now() + timedelta(hours=2),
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.get(reverse("portalPendingDemandsPage"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Urg&ecirc;ncia: Critica", html=False)
        self.assertContains(response, "Maior urg&ecirc;ncia", html=False)

    def test_admin_can_update_sla_policy_and_refresh_open_demand_deadlines(self):
        policy = PortalDemandSlaPolicy.objects.create(
            name="Infra padrao",
            description="Regra inicial.",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_HIGH,
            first_response_minutes=60,
            resolution_minutes=240,
            default_attendant=self.admin,
            auto_assign_on_create=False,
            is_active=True,
            sort_order=1,
        )
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Atualizar prazo SLA",
            description="Demanda aberta para validar refresh do SLA.",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_HIGH,
            status=PortalDemand.STATUS_PENDING,
        )
        demand.sla_policy = policy
        demand.first_response_due_at = demand.created_at + timedelta(minutes=60)
        demand.resolution_due_at = demand.created_at + timedelta(minutes=240)
        demand.save(update_fields=["sla_policy", "first_response_due_at", "resolution_due_at", "updated_at"])

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.post(
            reverse("portalDemandSlaConfigPage"),
            {
                "form_type": "update_sla",
                "policy_id": str(policy.id),
                "portal_sla-name": "Infra padrao",
                "portal_sla-description": "Regra revisada.",
                "portal_sla-task_group": str(self.group.id),
                "portal_sla-task_type": str(self.task_type.id),
                "portal_sla-priority_level": userQueue.PRIORITY_HIGH,
                "portal_sla-first_response_minutes": "45",
                "portal_sla-resolution_minutes": "180",
                "portal_sla-default_attendant": str(self.admin.id),
                "portal_sla-is_active": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        policy.refresh_from_db()
        demand.refresh_from_db()
        self.assertEqual(policy.description, "Regra revisada.")
        self.assertEqual(policy.first_response_minutes, 45)
        self.assertEqual(policy.resolution_minutes, 180)
        expected_first_response = demand.created_at + timedelta(minutes=45)
        expected_resolution = demand.created_at + timedelta(minutes=180)
        self.assertLess(abs((demand.first_response_due_at - expected_first_response).total_seconds()), 1)
        self.assertLess(abs((demand.resolution_due_at - expected_resolution).total_seconds()), 1)

    def test_admin_can_toggle_sla_policy_and_clear_matching_open_demand_deadlines(self):
        policy = PortalDemandSlaPolicy.objects.create(
            name="Infra desativavel",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_HIGH,
            first_response_minutes=30,
            resolution_minutes=120,
            is_active=True,
            sort_order=1,
        )
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Remover politica",
            description="Demanda aberta para validar desativacao do SLA.",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_HIGH,
            status=PortalDemand.STATUS_PENDING,
            sla_policy=policy,
            first_response_due_at=timezone.now() + timedelta(minutes=30),
            resolution_due_at=timezone.now() + timedelta(minutes=120),
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.post(
            reverse("portalDemandSlaConfigPage"),
            {
                "form_type": "toggle_sla",
                "policy_id": str(policy.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        policy.refresh_from_db()
        demand.refresh_from_db()
        self.assertFalse(policy.is_active)
        self.assertIsNone(demand.sla_policy)
        self.assertIsNone(demand.first_response_due_at)
        self.assertIsNone(demand.resolution_due_at)

    def test_admin_can_update_and_toggle_canned_response(self):
        canned = PortalCannedResponse.objects.create(
            title="Retorno inicial",
            message="Resposta padrao inicial.",
            task_group=self.group,
            task_type=self.task_type,
            suggest_internal=False,
            is_active=True,
            sort_order=1,
            created_by=self.admin,
        )

        self.client.login(username="portal.admin", password="pw123456")
        update_response = self.client.post(
            reverse("portalDemandResponsesConfigPage"),
            {
                "form_type": "update_canned",
                "canned_id": str(canned.id),
                "portal_canned-title": "Retorno atualizado",
                "portal_canned-message": "Resposta padrao revisada para o atendimento.",
                "portal_canned-task_group": str(self.group.id),
                "portal_canned-task_type": str(self.task_type.id),
                "portal_canned-suggest_internal": "on",
                "portal_canned-is_active": "on",
            },
            follow=True,
        )

        self.assertEqual(update_response.status_code, 200)
        canned.refresh_from_db()
        self.assertEqual(canned.title, "Retorno atualizado")
        self.assertEqual(canned.message, "Resposta padrao revisada para o atendimento.")
        self.assertTrue(canned.suggest_internal)

        toggle_response = self.client.post(
            reverse("portalDemandResponsesConfigPage"),
            {
                "form_type": "toggle_canned",
                "canned_id": str(canned.id),
            },
            follow=True,
        )

        self.assertEqual(toggle_response.status_code, 200)
        canned.refresh_from_db()
        self.assertFalse(canned.is_active)

    def test_pending_page_creates_and_resolves_critical_notification(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="SLA estourado",
            description="Demanda para criar notificacao critica.",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_HIGH,
            status=PortalDemand.STATUS_PENDING,
        )
        PortalDemand.objects.filter(pk=demand.pk).update(
            first_response_due_at=timezone.now() - timedelta(minutes=15),
            resolution_due_at=timezone.now() + timedelta(hours=1),
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.get(reverse("portalPendingDemandsPage"))

        self.assertEqual(response.status_code, 200)
        notification = SystemNotification.objects.get(source_key=f"portal-demand-critical-{demand.id}")
        self.assertTrue(notification.is_active)
        self.assertEqual(notification.level, SystemNotification.LEVEL_ERROR)

        PortalDemand.objects.filter(pk=demand.pk).update(status=PortalDemand.STATUS_COMPLETED)
        response = self.client.get(reverse("portalPendingDemandsPage"))

        self.assertEqual(response.status_code, 200)
        notification.refresh_from_db()
        self.assertFalse(notification.is_active)
        self.assertIsNotNone(notification.resolved_at)

    def test_admin_config_pages_show_operational_critical_overview(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Critica visivel na gerencia",
            description="Demanda para validar painel operacional.",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_HIGH,
            status=PortalDemand.STATUS_PENDING,
        )
        PortalDemand.objects.filter(pk=demand.pk).update(
            first_response_due_at=timezone.now() - timedelta(minutes=10),
            resolution_due_at=timezone.now() + timedelta(hours=2),
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.get(reverse("portalDemandSlaConfigPage"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demandas críticas agora", html=False)
        self.assertContains(response, demand.protocol)
        self.assertContains(response, demand.get_absolute_url())
        self.assertContains(response, "Ver cr&iacute;ticas", html=False)
        self.assertContains(response, "Assumir agora", html=False)

    def test_ai_routing_context_endpoint_returns_allowed_options(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Usuario sem acesso ao ERP",
            description="Nao consegue autenticar no sistema.",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_MEDIUM,
        )

        with patch.dict("os.environ", {"CONNECTMX_AI_ROUTING_TOKEN": "token-n8n-teste"}):
            response = self.client.get(
                reverse("portalDemandAiRoutingContextApi", args=[demand.id]),
                HTTP_X_CONNECTMX_AI_TOKEN="token-n8n-teste",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["demand"]["id"], demand.id)
        self.assertEqual(data["demand"]["task_type_id"], self.task_type.id)
        self.assertTrue(any(row["id"] == self.group.id for row in data["allowed_groups"]))
        self.assertTrue(any(row["id"] == self.task_type.id for row in data["allowed_types"]))
        self.assertTrue(any(row["value"] == userQueue.PRIORITY_HIGH for row in data["priority_options"]))
        self.assertIn("/apply-routing/", data["apply_routing_url"])

    def test_ai_routing_apply_endpoint_updates_and_auto_assigns_demand(self):
        PortalDemandSlaPolicy.objects.create(
            name="Roteamento IA Infra",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_HIGH,
            first_response_minutes=20,
            resolution_minutes=90,
            default_attendant=self.admin,
            auto_assign_on_create=True,
            is_active=True,
            sort_order=1,
        )
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="VPN sem autenticar",
            description="Usuario recebe erro de autenticacao na VPN.",
            status=PortalDemand.STATUS_PENDING,
        )

        with patch.dict("os.environ", {"CONNECTMX_AI_ROUTING_TOKEN": "token-n8n-teste"}):
            response = self.client.post(
                reverse("portalDemandAiRoutingApplyApi", args=[demand.id]),
                data=json.dumps(
                    {
                        "task_group_id": self.group.id,
                        "task_type_id": self.task_type.id,
                        "priority_level": userQueue.PRIORITY_HIGH,
                        "confidence": 0.94,
                        "reason": "Texto menciona autenticacao e VPN, aderente ao tipo de acesso.",
                        "auto_assign": True,
                    }
                ),
                content_type="application/json",
                HTTP_X_CONNECTMX_AI_TOKEN="token-n8n-teste",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        demand.refresh_from_db()

        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["auto_assigned"])
        self.assertEqual(demand.task_group_id, self.group.id)
        self.assertEqual(demand.task_type_id, self.task_type.id)
        self.assertEqual(demand.priority_level, userQueue.PRIORITY_HIGH)
        self.assertEqual(demand.status, PortalDemand.STATUS_ASSUMED)
        self.assertEqual(demand.assigned_to, self.admin)
        self.assertIsNotNone(demand.linked_queue_item)
        self.assertEqual(demand.sla_policy.name, "Roteamento IA Infra")
        self.assertTrue(
            PortalDemandLog.objects.filter(
                demand=demand,
                event_type=PortalDemandLog.EVENT_AI_ROUTED,
                summary="Roteamento automático aplicado",
            ).exists()
        )

    def test_creating_portal_demand_triggers_ai_webhook_after_commit(self):
        self.client.login(username="portal.requester", password="pw123456")

        with patch("tiqueue.views._portal_schedule_ai_routing_webhook") as mocked_schedule:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("portalDemandCreatePage"),
                    {
                        "title": "Nova demanda para roteamento",
                        "description": "Precisa disparar o webhook automaticamente ao criar.",
                        "priority_level": userQueue.PRIORITY_MEDIUM,
                    },
                )

        self.assertEqual(response.status_code, 302)
        demand = PortalDemand.objects.get(title="Nova demanda para roteamento")
        mocked_schedule.assert_called_once_with(demand.id, base_url="http://testserver")

    def test_demand_detail_shows_contextual_quick_canned_responses(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Acesso ao ERP",
            description="Precisa liberar usuario.",
            task_group=self.group,
            task_type=self.task_type,
        )
        PortalCannedResponse.objects.create(
            title="Liberacao padrao",
            message="Vamos validar a liberacao do acesso.",
            task_group=self.group,
            task_type=self.task_type,
            suggest_internal=False,
            is_active=True,
            sort_order=1,
            created_by=self.admin,
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.get(reverse("portalDemandDetailPage", args=[demand.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sugest&otilde;es r&aacute;pidas", html=False)
        self.assertContains(response, "Liberacao padrao")
        self.assertContains(response, "mesmo tipo")

    def test_admin_can_bulk_assume_selected_portal_demands(self):
        demand_one = PortalDemand.objects.create(
            requester=self.requester,
            title="Demanda A",
            description="Primeira demanda.",
            task_group=self.group,
            task_type=self.task_type,
        )
        demand_two = PortalDemand.objects.create(
            requester=self.requester,
            title="Demanda B",
            description="Segunda demanda.",
            task_group=self.group,
            task_type=self.task_type,
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.post(
            reverse("portalDemandBulkAssume"),
            {"selected_ids": f"{demand_one.id},{demand_two.id}"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand_one.refresh_from_db()
        demand_two.refresh_from_db()
        self.assertEqual(demand_one.status, PortalDemand.STATUS_ASSUMED)
        self.assertEqual(demand_two.status, PortalDemand.STATUS_ASSUMED)
        self.assertEqual(demand_one.assigned_to, self.admin)
        self.assertEqual(demand_two.assigned_to, self.admin)

    def test_requester_and_admin_can_reply_with_attachments(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Demanda com conversa",
            description="Texto inicial.",
            task_group=self.group,
            task_type=self.task_type,
        )

        self.client.login(username="portal.admin", password="pw123456")
        self.client.post(
            reverse("portalDemandAssume", args=[demand.id]),
            {"next": reverse("portalDemandDetailPage", args=[demand.id])},
            follow=True,
        )
        response = self.client.post(
            reverse("portalDemandDetailPage", args=[demand.id]),
            {
                "message": "Assumi a demanda e já estou verificando.",
                "attachments": [
                    SimpleUploadedFile("analise.txt", b"primeira resposta", content_type="text/plain"),
                ],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        self.client.logout()
        self.client.login(username="portal.requester", password="pw123456")
        response = self.client.post(
            reverse("portalDemandDetailPage", args=[demand.id]),
            {
                "message": "Perfeito, fico no aguardo.",
                "attachments": [
                    SimpleUploadedFile("retorno.txt", b"retorno do usuario", content_type="text/plain"),
                ],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = list(PortalDemandMessage.objects.filter(demand=demand).order_by("id"))
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].author, self.admin)
        self.assertEqual(messages[0].author_role, PortalDemandMessage.ROLE_ATTENDANT)
        self.assertEqual(messages[1].author, self.requester)
        self.assertEqual(messages[1].author_role, PortalDemandMessage.ROLE_REQUESTER)
        self.assertEqual(
            PortalDemandAttachment.objects.filter(demand=demand, message__isnull=False).count(),
            2,
        )

    def test_requester_can_submit_feedback_after_completion(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Demanda concluída com feedback",
            description="Fluxo de avaliação.",
            task_group=self.group,
            task_type=self.task_type,
        )

        self.client.login(username="portal.admin", password="pw123456")
        self.client.post(reverse("portalDemandAssume", args=[demand.id]), follow=True)
        demand.refresh_from_db()
        self.client.post(reverse("endQueueItem", args=[demand.linked_queue_item.n_register]), follow=True)

        demand.refresh_from_db()
        self.assertEqual(demand.status, PortalDemand.STATUS_COMPLETED)

        self.client.logout()
        self.client.login(username="portal.requester", password="pw123456")
        response = self.client.post(
            reverse("portalDemandDetailPage", args=[demand.id]),
            {
                "form_type": "feedback",
                "feedback_rating": "5",
                "feedback_comment": "Atendimento excelente e resolveu o problema.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand.refresh_from_db()
        self.assertEqual(demand.feedback_rating, 5)
        self.assertEqual(demand.feedback_comment, "Atendimento excelente e resolveu o problema.")
        self.assertIsNotNone(demand.feedback_submitted_at)

    def test_sla_policy_can_auto_assign_new_portal_demand(self):
        PortalDemandSlaPolicy.objects.create(
            name="Infra urgente",
            task_group=self.group,
            task_type=self.task_type,
            priority_level=userQueue.PRIORITY_HIGH,
            first_response_minutes=30,
            resolution_minutes=180,
            default_attendant=self.admin,
            auto_assign_on_create=True,
            sort_order=1,
        )

        self.client.login(username="portal.requester", password="pw123456")
        response = self.client.post(
            reverse("portalDemandCreatePage"),
            {
                "title": "VPN fora do ar",
                "description": "Usuário não consegue conectar na VPN.",
                "task_group": str(self.group.id),
                "task_type": str(self.task_type.id),
                "priority_level": userQueue.PRIORITY_HIGH,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand = PortalDemand.objects.get(title="VPN fora do ar")
        self.assertEqual(demand.sla_policy.name, "Infra urgente")
        self.assertEqual(demand.status, PortalDemand.STATUS_ASSUMED)
        self.assertEqual(demand.assigned_to, self.admin)
        self.assertIsNotNone(demand.linked_queue_item)
        self.assertIsNotNone(demand.first_response_due_at)
        self.assertIsNotNone(demand.resolution_due_at)

    def test_portal_insights_endpoint_returns_knowledge_duplicate_and_sla(self):
        category = KnowledgeCategory.objects.create(name="Infra")
        KnowledgeEntry.objects.create(
            category=category,
            title="Falha VPN",
            trigger="Usuário sem acesso à VPN",
            description="Verificar autenticação e permissão de rede.",
            created_by=self.admin,
        )
        PortalDemand.objects.create(
            requester=self.requester,
            title="Erro VPN matriz",
            description="Falha ao abrir conexão da VPN.",
            task_group=self.group,
            task_type=self.task_type,
        )
        PortalDemandSlaPolicy.objects.create(
            name="Infra padrão",
            task_group=self.group,
            task_type=self.task_type,
            first_response_minutes=60,
            resolution_minutes=240,
            sort_order=1,
        )

        self.client.login(username="portal.requester", password="pw123456")
        response = self.client.get(
            reverse("portalDemandInsightsApi"),
            {
                "title": "Falha VPN",
                "description": "Usuário continua sem acesso à VPN",
                "task_group": str(self.group.id),
                "task_type": str(self.task_type.id),
                "priority_level": userQueue.PRIORITY_MEDIUM,
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["sla"]["title"], "Infra padrão")
        self.assertGreaterEqual(len(data["knowledge"]), 1)
        self.assertGreaterEqual(len(data["duplicates"]), 1)

    def test_internal_note_is_hidden_from_requester(self):
        canned = PortalCannedResponse.objects.create(
            title="Análise interna",
            message="Validando a causa raiz internamente.",
            task_group=self.group,
            task_type=self.task_type,
            suggest_internal=True,
            created_by=self.admin,
        )
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Falha interna",
            description="Demanda para validar anotação interna.",
            task_group=self.group,
            task_type=self.task_type,
            assigned_to=self.admin,
            status=PortalDemand.STATUS_ASSUMED,
            assumed_at=timezone.now(),
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.post(
            reverse("portalDemandDetailPage", args=[demand.id]),
            {
                "form_type": "reply",
                "canned_response": str(canned.id),
                "is_internal": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        message = PortalDemandMessage.objects.get(demand=demand)
        self.assertTrue(message.is_internal)
        self.assertEqual(message.canned_response, canned)
        demand.refresh_from_db()
        self.assertIsNone(demand.first_response_at)

        self.client.logout()
        self.client.login(username="portal.requester", password="pw123456")
        detail_response = self.client.get(reverse("portalDemandDetailPage", args=[demand.id]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(detail_response, "Validando a causa raiz internamente.")

    def test_public_attendant_reply_sets_first_response_at(self):
        demand = PortalDemand.objects.create(
            requester=self.requester,
            title="Primeira resposta pública",
            description="Validar timestamp da primeira resposta.",
            task_group=self.group,
            task_type=self.task_type,
            assigned_to=self.admin,
            status=PortalDemand.STATUS_ASSUMED,
            assumed_at=timezone.now(),
        )

        self.client.login(username="portal.admin", password="pw123456")
        response = self.client.post(
            reverse("portalDemandDetailPage", args=[demand.id]),
            {
                "form_type": "reply",
                "message": "Recebemos a demanda e estamos atuando.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        demand.refresh_from_db()
        self.assertIsNotNone(demand.first_response_at)
