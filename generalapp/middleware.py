from django.conf import settings
from django.shortcuts import redirect


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

    def __call__(self, request):
        path = request.path or "/"

        is_exempt = any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)
        is_admin_area = path.startswith("/admin/")
        is_authenticated = bool(getattr(request, "user", None) and request.user.is_authenticated)

        if not is_authenticated and not is_exempt and not is_admin_area:
            return redirect(f"{settings.LOGIN_URL}?next={request.get_full_path()}")

        return self.get_response(request)

