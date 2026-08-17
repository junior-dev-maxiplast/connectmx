"""Contexto da navegação, disponível em toda página que renderiza a sidebar."""

import json

from .models import ScreenVisit
from .navigation import build_menu, destination_by_url_name, flatten


FAVORITES_LIMIT = 8
RECENTS_LIMIT = 3


def navigation(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    groups = build_menu(user)
    destinations = flatten(groups)

    visits = list(ScreenVisit.objects.filter(user=user)[:80])

    # Favorito é o que o usuário marcou com a estrela, na ordem em que marcou.
    favorite_names = set()
    favorites = []
    for visit in sorted(
        (item for item in visits if item.is_favorite),
        key=lambda item: (item.favorited_at is None, item.favorited_at, item.url_name),
    ):
        target = destination_by_url_name(groups, visit.url_name)
        if not target:
            # A tela saiu do menu deste usuário: a estrela fica guardada, mas
            # não vira atalho para algo que ele não pode abrir.
            continue
        favorites.append(target)
        favorite_names.add(visit.url_name)
        if len(favorites) >= FAVORITES_LIMIT:
            break

    recents = []
    for visit in sorted(visits, key=lambda item: item.last_visited_at, reverse=True):
        if visit.url_name in favorite_names:
            continue
        target = destination_by_url_name(groups, visit.url_name)
        if target:
            recents.append(target)
        if len(recents) >= RECENTS_LIMIT:
            break

    # Marca quais itens do menu já estão favoritados, para a estrela nascer amarela.
    starred = {item.url_name for item in visits if item.is_favorite}
    for group in groups:
        for item in group["items"]:
            item["is_favorite"] = item["url_name"] in starred

    return {
        "nav_groups": groups,
        "nav_favorites": favorites,
        "nav_recents": recents,
        # Escapa "<" para o JSON poder viver dentro de um <script> sem risco
        # de fechar a tag antes da hora.
        "nav_palette_json": json.dumps(destinations, ensure_ascii=False).replace("<", "\\u003c"),
    }
