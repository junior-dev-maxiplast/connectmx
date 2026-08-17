from django.conf import settings
from django.shortcuts import redirect
from django.shortcuts import resolve_url
from django.contrib.auth import get_user_model
from django.db.models import F
from django.utils import timezone
from datetime import timedelta

from .models import ScreenVisit
from .navigation import known_url_names


class RequireLoginMiddleware:
    """
    Enforce authentication for ConnectMX pages.
    Keeps external modules (e.g. /sede) with their own auth flow untouched.
    """

    EXEMPT_PREFIXES = (
        "/main/login/",
        "/main/forgot-password/",
        "/accounts/login/",
        "/main/logout/",
        "/admin/login/",
        "/portal/entrar/",
        "/dashes/entrar/",
        "/static/",
        "/portal/chamados/api/ai/",
        "/sede/",
        "/almoco/",
        "/logistica/",
    )

    # Requester-facing area: unauthenticated visitors go to the portal's own
    # sign-in page instead of the internal ConnectMX login.
    PORTAL_PREFIX = "/portal/"
    PORTAL_LOGIN_URL = "/portal/entrar/"
    DASHES_PREFIX = "/dashes/"
    DASHES_LOGIN_URL = "/dashes/entrar/"

    # Onde uma conta exclusiva do Dashes pode navegar.
    DASHES_ONLY_PREFIXES = (
        "/dashes/",
        "/static/",
        "/media/",
        "/main/logout/",
        "/accounts/logout/",
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self.user_model = get_user_model()

    def _is_dashes_only_allowed(self, path):
        return any(path.startswith(prefix) for prefix in self.DASHES_ONLY_PREFIXES)

    def __call__(self, request):
        path = request.path or "/"

        is_exempt = any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)
        is_admin_area = path.startswith("/admin/")
        is_authenticated = bool(getattr(request, "user", None) and request.user.is_authenticated)

        if not is_authenticated and not is_exempt and not is_admin_area:
            if path.startswith(self.DASHES_PREFIX):
                login_url = self.DASHES_LOGIN_URL
            elif path.startswith(self.PORTAL_PREFIX):
                login_url = self.PORTAL_LOGIN_URL
            else:
                login_url = resolve_url(settings.LOGIN_URL)
            return redirect(f"{login_url}?next={request.get_full_path()}")

        # Contas exclusivas do Dashes autenticam normalmente, mas não abrem o
        # ConnectMX interno. Sair continua permitido, senão a conta fica presa.
        if (
            is_authenticated
            and not getattr(request.user, "can_access_internal", True)
            and not self._is_dashes_only_allowed(path)
        ):
            return redirect(self.DASHES_PREFIX)

        response = self.get_response(request)

        # Usage audit only for authenticated ConnectMX requests.
        if is_authenticated and not is_exempt and not is_admin_area:
            user = request.user
            now = timezone.now()

            # Update last access with simple throttling to avoid write on every request.
            last_access = getattr(user, "last_access_at", None)
            if not last_access or (now - last_access) >= timedelta(minutes=5):
                self.user_model.objects.filter(pk=user.pk).update(last_access_at=now)

            # Mark last change time on successful write operations.
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and response.status_code < 400:
                self.user_model.objects.filter(pk=user.pk).update(last_data_change_at=now)

            self._record_screen_visit(request, response)

        return response

    def _record_screen_visit(self, request, response):
        """Contabiliza a abertura de uma tela do menu, para favoritos/recentes.

        Só conta GET que renderizou página (200 e HTML): redirect, POST, JSON e
        chamadas de API não são "abrir uma tela" e distorceriam o ranking.
        """
        if request.method != "GET" or response.status_code != 200:
            return
        if "text/html" not in response.get("Content-Type", ""):
            return

        match = getattr(request, "resolver_match", None)
        url_name = getattr(match, "url_name", None) if match else None
        if not url_name or url_name not in known_url_names():
            return

        visit, created = ScreenVisit.objects.get_or_create(
            user=request.user,
            url_name=url_name,
            defaults={"visit_count": 1},
        )
        if not created:
            ScreenVisit.objects.filter(pk=visit.pk).update(
                visit_count=F("visit_count") + 1,
                last_visited_at=timezone.now(),
            )
