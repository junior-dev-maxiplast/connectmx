"""Catalogo de navegacao, favoritos/recentes e command palette."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .models import ScreenVisit
from .navigation import NAV_GROUPS, build_menu, flatten, known_url_names


User = get_user_model()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        password="secret123",
        userId=extra.pop("userId", username[:18]),
        email=extra.pop("email", f"{username}@example.com"),
        **extra,
    )


class NavigationCatalogTests(TestCase):
    def test_every_destination_resolves(self):
        """Um url_name errado no catalogo derrubaria a sidebar em toda pagina."""
        broken = []
        for url_name in known_url_names():
            try:
                reverse(url_name)
            except NoReverseMatch:
                broken.append(url_name)
        self.assertEqual(broken, [])

    def test_there_are_no_duplicated_destinations(self):
        """O menu antigo repetia 3 destinos com nomes diferentes."""
        names = [entry[0] for group in NAV_GROUPS for entry in group["items"]]
        duplicated = {name for name in names if names.count(name) > 1}
        self.assertEqual(duplicated, set())

    def test_labels_are_unique_so_the_palette_is_unambiguous(self):
        labels = [entry[1] for group in NAV_GROUPS for entry in group["items"]]
        duplicated = {label for label in labels if labels.count(label) > 1}
        self.assertEqual(duplicated, set())

    def test_admin_only_items_are_hidden_from_regular_users(self):
        regular = make_user("comum")
        admin = make_user("chefe", is_system_admin=True)

        regular_names = {item["url_name"] for group in build_menu(regular) for item in group["items"]}
        admin_names = {item["url_name"] for group in build_menu(admin) for item in group["items"]}

        self.assertNotIn("createUser", regular_names)
        self.assertIn("createUser", admin_names)
        self.assertLess(len(regular_names), len(admin_names))

    def test_menu_is_grouped_by_domain(self):
        admin = make_user("dominio", is_system_admin=True)
        labels = [group["label"] for group in build_menu(admin)]

        self.assertEqual(
            labels,
            ["Início", "Atendimento", "Projetos", "Operação", "Conhecimento", "ConnectMX Dashes", "Administração"],
        )
        # Nenhum grupo vira o balde que "Funcionalidades" era (39 itens).
        for group in build_menu(admin):
            self.assertLessEqual(len(group["items"]), 12, group["label"])

    def test_flatten_carries_the_group_for_the_palette(self):
        admin = make_user("plano", is_system_admin=True)
        destinations = flatten(build_menu(admin))

        tires = next(item for item in destinations if item["url_name"] == "tires_dashboard")
        self.assertEqual(tires["group"], "Operação")
        self.assertEqual(tires["url"], reverse("tires_dashboard"))


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class ScreenVisitTests(TestCase):
    def setUp(self):
        self.user = make_user("visitante")
        self.client.force_login(self.user)

    def test_opening_a_screen_counts_a_visit(self):
        self.client.get(reverse("hubPage"))
        self.client.get(reverse("hubPage"))

        visit = ScreenVisit.objects.get(user=self.user, url_name="hubPage")
        self.assertEqual(visit.visit_count, 2)

    def test_posts_and_redirects_do_not_count(self):
        before = ScreenVisit.objects.count()
        self.client.post(reverse("hubPage"), {})
        self.assertEqual(ScreenVisit.objects.count(), before)

    def test_screens_outside_the_catalog_are_ignored(self):
        self.client.get(reverse("logoutPage"))
        self.assertFalse(ScreenVisit.objects.filter(url_name="logoutPage").exists())


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class NavigationContextTests(TestCase):
    def setUp(self):
        self.user = make_user("contexto")
        self.client.force_login(self.user)

    def test_the_sidebar_exposes_menu_and_palette_data(self):
        response = self.client.get(reverse("hubPage"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("nav_groups", response.context)
        palette = json.loads(response.context["nav_palette_json"].replace("\\u003c", "<"))
        self.assertTrue(palette)
        self.assertIn("group", palette[0])
        # O gatilho e o palette chegam no HTML.
        self.assertContains(response, "sidebarPaletteTrigger")
        self.assertContains(response, "cmxPaletteData")

    def test_visiting_a_lot_does_not_create_a_favourite(self):
        """Favorito e escolha explicita: acesso frequente so alimenta Recentes."""
        ScreenVisit.objects.create(user=self.user, url_name="tires_dashboard", visit_count=40)

        response = self.client.get(reverse("hubPage"))

        self.assertEqual(response.context["nav_favorites"], [])
        recents = [item["url_name"] for item in response.context["nav_recents"]]
        self.assertIn("tires_dashboard", recents)

    def test_recents_are_ordered_by_last_visit(self):
        old = ScreenVisit.objects.create(user=self.user, url_name="tires_dashboard", visit_count=1)
        recent = ScreenVisit.objects.create(user=self.user, url_name="wifiVoucherPage", visit_count=1)
        # last_visited_at e auto_now, entao so um update escreve a data desejada.
        now = timezone.now()
        ScreenVisit.objects.filter(pk=old.pk).update(last_visited_at=now - timedelta(days=2))
        ScreenVisit.objects.filter(pk=recent.pk).update(last_visited_at=now)

        response = self.client.get(reverse("hubPage"))

        recents = [item["url_name"] for item in response.context["nav_recents"]]
        self.assertEqual(recents, ["wifiVoucherPage", "tires_dashboard"])

    def test_quick_access_hides_screens_the_user_cannot_open(self):
        """Uma estrela antiga numa tela de admin nao vira atalho para quem perdeu o acesso."""
        ScreenVisit.objects.create(
            user=self.user, url_name="createUser", visit_count=20, is_favorite=True
        )

        response = self.client.get(reverse("hubPage"))

        favourites = [item["url_name"] for item in response.context["nav_favorites"]]
        self.assertNotIn("createUser", favourites)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class FavoriteStarTests(TestCase):
    def setUp(self):
        self.user = make_user("estrela")
        self.client.force_login(self.user)
        self.toggle_url = reverse("toggleFavoriteScreen")

    def _toggle(self, url_name):
        return self.client.post(
            self.toggle_url,
            data=json.dumps({"url_name": url_name}),
            content_type="application/json",
        )

    def test_star_turns_a_screen_into_a_favourite(self):
        response = self._toggle("tires_dashboard")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["is_favorite"])
        self.assertEqual(payload["group"], "Opera\u00e7\u00e3o")

        visit = ScreenVisit.objects.get(user=self.user, url_name="tires_dashboard")
        self.assertTrue(visit.is_favorite)
        self.assertIsNotNone(visit.favorited_at)

    def test_clicking_the_star_again_removes_the_favourite(self):
        self._toggle("tires_dashboard")
        response = self._toggle("tires_dashboard")

        self.assertFalse(response.json()["is_favorite"])
        visit = ScreenVisit.objects.get(user=self.user, url_name="tires_dashboard")
        self.assertFalse(visit.is_favorite)
        self.assertIsNone(visit.favorited_at)

    def test_favouriting_keeps_the_visit_history(self):
        ScreenVisit.objects.create(user=self.user, url_name="wifiVoucherPage", visit_count=7)

        self._toggle("wifiVoucherPage")

        visit = ScreenVisit.objects.get(user=self.user, url_name="wifiVoucherPage")
        self.assertEqual(visit.visit_count, 7)
        self.assertTrue(visit.is_favorite)

    def test_unknown_screens_are_refused(self):
        response = self._toggle("naoExiste")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ScreenVisit.objects.count(), 0)

    def test_cannot_favourite_a_screen_the_user_cannot_open(self):
        """createUser so aparece no menu de administrador."""
        response = self._toggle("createUser")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ScreenVisit.objects.count(), 0)

    def test_admin_can_favourite_an_admin_screen(self):
        admin = make_user("chefe.estrela", is_system_admin=True)
        self.client.force_login(admin)

        response = self._toggle("createUser")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_favorite"])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.toggle_url).status_code, 405)

    def test_favourites_replace_the_old_most_used_section(self):
        """Muitos acessos ja nao bastam: agora vale a estrela."""
        ScreenVisit.objects.create(user=self.user, url_name="wifiVoucherPage", visit_count=40)
        self._toggle("tires_dashboard")

        response = self.client.get(reverse("hubPage"))

        favourites = [item["url_name"] for item in response.context["nav_favorites"]]
        self.assertEqual(favourites, ["tires_dashboard"])
        self.assertContains(response, "Favoritos")
        self.assertNotContains(response, "Mais usadas")

    def test_the_menu_marks_which_items_are_starred(self):
        self._toggle("tires_dashboard")

        response = self.client.get(reverse("hubPage"))

        flags = {
            item["url_name"]: item["is_favorite"]
            for group in response.context["nav_groups"]
            for item in group["items"]
        }
        self.assertTrue(flags["tires_dashboard"])
        self.assertFalse(flags["wifiVoucherPage"])
        # A estrela ligada chega no HTML com a classe que a pinta de amarelo.
        self.assertContains(response, 'aria-pressed="true"')
        self.assertContains(response, "sidebar_star is-on")

    def test_a_favourite_is_not_repeated_under_recents(self):
        self._toggle("tires_dashboard")
        self.client.get(reverse("tires_dashboard"))

        response = self.client.get(reverse("hubPage"))

        favourites = [item["url_name"] for item in response.context["nav_favorites"]]
        recents = [item["url_name"] for item in response.context["nav_recents"]]
        self.assertIn("tires_dashboard", favourites)
        self.assertNotIn("tires_dashboard", recents)
