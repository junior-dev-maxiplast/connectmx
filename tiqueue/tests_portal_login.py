"""Destino do login do portal (/portal/entrar/).

Solicitantes vão para o portal; quem atende a fila vai para o painel interno.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


User = get_user_model()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        password="secret123",
        userId=username[:18],
        email=f"{username}@example.com",
        **extra,
    )


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class PortalLoginLandingTests(TestCase):
    def setUp(self):
        self.login_url = reverse("portalLoginPage")

    def post_login(self, username, **extra):
        return self.client.post(
            self.login_url, {"login": username, "password": "secret123", **extra}
        )

    def test_a_requester_lands_on_the_portal(self):
        make_user("solicitante")

        response = self.post_login("solicitante")

        self.assertRedirects(response, reverse("portalDemandPage"), fetch_redirect_response=False)

    def test_an_attendant_lands_on_the_internal_panel(self):
        make_user("atendente", is_support_attendant=True)

        response = self.post_login("atendente")

        self.assertRedirects(response, reverse("index"), fetch_redirect_response=False)

    def test_a_system_admin_lands_on_the_internal_panel(self):
        make_user("admin.sistema", is_system_admin=True)

        response = self.post_login("admin.sistema")

        self.assertRedirects(response, reverse("index"), fetch_redirect_response=False)

    def test_an_explicit_next_still_wins_for_attendants(self):
        make_user("atendente", is_support_attendant=True)

        response = self.post_login("atendente", next=reverse("portalDemandPage"))

        self.assertRedirects(response, reverse("portalDemandPage"), fetch_redirect_response=False)

    def test_an_attendant_already_signed_in_goes_to_the_internal_panel(self):
        make_user("atendente", is_support_attendant=True)
        self.client.login(username="atendente", password="secret123")

        response = self.client.get(self.login_url)

        self.assertRedirects(response, reverse("index"), fetch_redirect_response=False)
