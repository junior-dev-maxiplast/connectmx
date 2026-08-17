"""Permissionamento do ConnectMX Dashes.

Antes desta camada, qualquer usuario ativo do ConnectMX entrava no Dashes e via
todos os paineis. Estes testes fixam o comportamento novo: acesso e sempre
explicito, por painel.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Dashboard, DashboardAccess


User = get_user_model()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        password="secret123",
        userId=extra.pop("userId", username[:18]),
        email=extra.pop("email", f"{username}@example.com"),
        **extra,
    )


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class DashesAccessTests(TestCase):
    def setUp(self):
        self.dna = Dashboard.objects.get(slug="customer-dna")
        self.extra = Dashboard.objects.create(
            slug="fleet-costs",
            name="Custos de Frota",
            url_name="dashesCustomerDnaPage",
            display_order=2,
        )
        self.dna_url = reverse("dashesCustomerDnaPage")
        self.login_url = reverse("dashesLoginPage")

    # ---------------------------------------------------------------- login --

    def test_login_is_refused_when_the_user_has_no_dashboard(self):
        make_user("semdash")

        response = self.client.post(
            self.login_url, {"username": "semdash", "password": "secret123"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nenhum painel liberado")
        # Nao pode nem abrir sessao do Dashes.
        self.assertFalse(self.client.session.get("dashes_authenticated"))

    def test_wrong_password_still_says_invalid_credentials(self):
        make_user("comdash")

        response = self.client.post(
            self.login_url, {"username": "comdash", "password": "errada"}
        )

        self.assertContains(response, "Usu")
        self.assertNotContains(response, "nenhum painel liberado")

    def test_login_works_when_a_dashboard_is_granted(self):
        user = make_user("liberado")
        DashboardAccess.objects.create(user=user, dashboard=self.dna)

        response = self.client.post(
            self.login_url, {"username": "liberado", "password": "secret123"}, follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.session.get("dashes_authenticated"))
        self.assertTemplateUsed(response, "tiqueue/customer_dna.html")

    # ----------------------------------------------------------- autorizacao --

    def test_dashboard_without_permission_returns_403(self):
        user = make_user("sofrota")
        DashboardAccess.objects.create(user=user, dashboard=self.extra)
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

        response = self.client.get(self.dna_url)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "tiqueue/dashes_denied.html")

    def test_system_admin_sees_the_whole_catalog(self):
        admin = make_user("chefe", is_system_admin=True)
        self.client.force_login(admin)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

        response = self.client.get(self.dna_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {dash.slug for dash in response.context["dashes_menu"]},
            {"customer-dna", "fleet-costs"},
        )

    def test_sidebar_lists_only_the_granted_dashboards(self):
        user = make_user("umso")
        DashboardAccess.objects.create(user=user, dashboard=self.dna)
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

        response = self.client.get(self.dna_url)

        self.assertContains(response, "DNA do Cliente")
        self.assertNotContains(response, "Custos de Frota")

    def test_inactive_dashboard_is_not_accessible(self):
        user = make_user("inativo")
        DashboardAccess.objects.create(user=user, dashboard=self.dna)
        Dashboard.objects.filter(slug="customer-dna").update(is_active=False)
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

        self.assertEqual(self.client.get(self.dna_url).status_code, 403)

    def test_home_redirects_to_the_first_allowed_dashboard(self):
        user = make_user("home")
        DashboardAccess.objects.create(user=user, dashboard=self.dna)
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

        response = self.client.get(reverse("dashesHome"))

        self.assertRedirects(response, self.dna_url, fetch_redirect_response=False)

    def test_anonymous_is_sent_to_the_dashes_login(self):
        response = self.client.get(self.dna_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.login_url, response["Location"])


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class DashesOnlyAccountTests(TestCase):
    def setUp(self):
        self.dna = Dashboard.objects.get(slug="customer-dna")
        self.user = make_user("sodashes", can_access_internal=False)
        DashboardAccess.objects.create(user=self.user, dashboard=self.dna)
        self.client.force_login(self.user)

    def test_internal_pages_redirect_to_dashes(self):
        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/dashes/")

    def test_the_dashes_area_stays_open(self):
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

        self.assertEqual(self.client.get(reverse("dashesCustomerDnaPage")).status_code, 200)

    def test_a_regular_user_keeps_internal_access(self):
        regular = make_user("interno")
        self.client.force_login(regular)

        self.assertEqual(self.client.get(reverse("index")).status_code, 200)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class DashesAccessManagementTests(TestCase):
    """Concessao de acesso pela tela de Cadastro interno do ConnectMX."""

    def setUp(self):
        self.dna = Dashboard.objects.get(slug="customer-dna")
        self.admin = make_user("admin.geral", is_system_admin=True)
        self.client.force_login(self.admin)
        self.url = reverse("createUser")

    def test_creating_a_dashes_only_user_grants_the_selected_dashboards(self):
        response = self.client.post(
            self.url,
            {
                "form_id": "create",
                "userId": "9001",
                "username": "diretor.comercial",
                "email": "diretor@example.com",
                "nameUser": "Diretor Comercial",
                "password": "secret123",
                "dashboards": ["customer-dna"],
            },
        )

        self.assertEqual(response.status_code, 200)
        created = User.objects.get(username="diretor.comercial")
        # Sem o checkbox marcado, a conta nasce restrita ao Dashes.
        self.assertFalse(created.can_access_internal)
        self.assertEqual(
            list(created.dashboard_accesses.values_list("dashboard__slug", flat=True)),
            ["customer-dna"],
        )

    def test_editing_replaces_the_previous_grants(self):
        target = make_user("alvo")
        DashboardAccess.objects.create(user=target, dashboard=self.dna)

        self.client.post(
            self.url,
            {
                "form_id": "edit",
                "user_pk": target.pk,
                "userId": target.userId,
                "username": target.username,
                "email": target.email,
                "nameUser": "Alvo Editado",
                "can_access_internal": "on",
                # Nenhum dashboard marcado: o acesso deve ser revogado.
            },
        )

        target.refresh_from_db()
        self.assertTrue(target.can_access_internal)
        self.assertEqual(target.dashboard_accesses.count(), 0)

    def test_the_screen_shows_the_catalog_and_who_can_see_what(self):
        target = make_user("visivel")
        DashboardAccess.objects.create(user=target, dashboard=self.dna)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([dash.slug for dash in response.context["dashboards"]], ["customer-dna"])
        row = next(r for r in response.context["user_rows"] if r["user"].pk == target.pk)
        self.assertEqual(row["dashboard_names"], ["DNA do Cliente"])

    def test_a_non_admin_cannot_grant_access(self):
        plain = make_user("comum")
        target = make_user("vitima")
        self.client.force_login(plain)

        self.client.post(
            self.url,
            {
                "form_id": "edit",
                "user_pk": target.pk,
                "userId": target.userId,
                "username": target.username,
                "email": target.email,
                "nameUser": "Vitima",
                "dashboards": ["customer-dna"],
            },
        )

        self.assertEqual(target.dashboard_accesses.count(), 0)
