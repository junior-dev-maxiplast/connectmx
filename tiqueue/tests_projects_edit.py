"""Edição rápida de projeto e de item do roadmap (formulário montado à mão)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Project, ProjectRoadmapItem


@override_settings(ALLOWED_HOSTS=["testserver"])
class ManageProjectsInlineEditTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="gestor", password="pw", userId="70001", email="gestor@example.com"
        )
        self.client.force_login(self.user)
        self.project = Project.objects.create(
            name="Projeto Alfa", status="active", developer=self.user, start_date=date(2026, 1, 5)
        )
        self.item = ProjectRoadmapItem.objects.create(
            project=self.project, title="Planejar", status="planned", sort_order=1
        )

    def _post(self, **data):
        return self.client.post(reverse("manageProjects"), data)

    def test_invalid_values_do_not_break_the_project_save(self):
        response = self._post(
            form_id="edit_project", project_id=self.project.id, name="Projeto Alfa",
            status="inventado", start_date="31/12/2026", end_date="abc",
            developer_id="999999", participants_ids=["999998", str(self.user.id)],
        )

        self.assertLess(response.status_code, 500)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "active")  # status inválido mantém o atual
        self.assertIsNone(self.project.start_date)
        self.assertIsNone(self.project.developer)
        self.assertEqual(list(self.project.participants.all()), [self.user])

    def test_valid_values_are_saved(self):
        self._post(
            form_id="edit_project", project_id=self.project.id, name="Projeto Alfa",
            status="paused", start_date="2026-02-01", developer_id=str(self.user.id),
        )

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "paused")
        self.assertEqual(self.project.start_date, date(2026, 2, 1))
        self.assertEqual(self.project.developer, self.user)

    def test_invalid_values_do_not_break_the_roadmap_item_save(self):
        response = self._post(
            form_id="edit_roadmap", item_id=self.item.id, title="Planejar",
            status="xyz", start_date="ontem", sort_order="primeiro",
        )

        self.assertLess(response.status_code, 500)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "planned")
        self.assertEqual(self.item.sort_order, 1)
        self.assertIsNone(self.item.start_date)
