from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class RepresentativeUserManagementTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.admin = self.user_model.objects.create_user(
            userId="90001",
            username="admin.users",
            email="admin.users@example.com",
            nameUser="Admin Users",
            password="test-password",
            is_system_admin=True,
        )
        self.client.force_login(self.admin)
        self.url = reverse("createUser")

    def test_creates_and_displays_representative_user(self):
        response = self.client.post(
            self.url,
            {
                "form_id": "create",
                "nameUser": "Representante Teste",
                "userId": "90002",
                "username": "representante.teste",
                "email": "representante@example.com",
                "password": "initial-password",
                "is_representative": "on",
                "representative_code": "10050",
                "can_access_internal": "on",
            },
        )

        representative = self.user_model.objects.get(username="representante.teste")
        self.assertTrue(representative.is_representative)
        self.assertEqual(representative.representative_code, "10050")
        self.assertContains(response, "REP 10050")

    def test_requires_code_when_representative_is_checked(self):
        response = self.client.post(
            self.url,
            {
                "form_id": "create",
                "nameUser": "Representante Sem Codigo",
                "userId": "90003",
                "username": "representante.sem.codigo",
                "email": "sem.codigo@example.com",
                "password": "initial-password",
                "is_representative": "on",
            },
        )

        self.assertFalse(self.user_model.objects.filter(username="representante.sem.codigo").exists())
        self.assertContains(response, "Informe o codigo do representante.")

    def test_edit_can_remove_representative_link(self):
        representative = self.user_model.objects.create_user(
            userId="90004",
            username="representante.editar",
            email="representante.editar@example.com",
            nameUser="Representante Editar",
            password="initial-password",
            is_representative=True,
            representative_code="10051",
        )

        self.client.post(
            self.url,
            {
                "form_id": "edit",
                "user_pk": representative.pk,
                "nameUser": representative.nameUser,
                "userId": representative.userId,
                "username": representative.username,
                "email": representative.email,
                "representative_code": "10051",
                "can_access_internal": "on",
            },
        )

        representative.refresh_from_db()
        self.assertFalse(representative.is_representative)
        self.assertIsNone(representative.representative_code)
