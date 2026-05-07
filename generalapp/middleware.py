from django.conf import settings
from django.shortcuts import redirect
from django.shortcuts import resolve_url
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta


class RequireLoginMiddleware:
    """
    Enforce authentication for ConnectMX pages.
    Keeps external modules (e.g. /sede) with their own auth flow untouched.
    """

    EXEMPT_PREFIXES = (
        "/main/login/",
        "/accounts/login/",
        "/main/logout/",
        "/admin/login/",
        "/static/",
        "/sede/",
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self.user_model = get_user_model()

    def __call__(self, request):
        path = request.path or "/"

        is_exempt = any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)
        is_admin_area = path.startswith("/admin/")
        is_authenticated = bool(getattr(request, "user", None) and request.user.is_authenticated)

        if not is_authenticated and not is_exempt and not is_admin_area:
            login_url = resolve_url(settings.LOGIN_URL)
            return redirect(f"{login_url}?next={request.get_full_path()}")

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

        return response
