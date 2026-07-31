from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import UserQueueCreateForm, UserQueueUpdateForm
from .models import (
    MaxiTetrisHighScore,
    Project,
    ProjectRoadmapItem,
    ProjectRoadmapSubtask,
    UserQueueCustomValue,
    UserQueueCustomColumn,
    UserQueueCustomColumnOption,
    UserQueueFieldOption,
    concludedTasks,
    userQueue,
)


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
