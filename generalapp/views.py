import json
from datetime import timedelta

from django.contrib.auth import REDIRECT_FIELD_NAME, authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.models import User
from tiqueue.models import Dashboard, DashboardAccess
from .forms import ForgotPasswordForm
from .models import ScreenVisit
from .navigation import build_menu, destination_by_url_name, known_url_names


def _initials(user):
    """Duas letras para o avatar da listagem."""
    source = (user.nameUser or user.username or "").strip()
    parts = [part for part in source.split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _parse_ai_daily_limit(raw_value):
    """Limite diario de IA vindo do formulario. Em branco = sem limite."""
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(parsed, 0)


def _sync_dashboard_access(user, slugs, granted_by=None):
    """Deixa os acessos do usuário exatamente iguais aos slugs marcados."""
    catalog = {dash.slug: dash for dash in Dashboard.objects.filter(is_active=True)}
    wanted = {slug for slug in slugs if slug in catalog}

    DashboardAccess.objects.filter(user=user).exclude(dashboard__slug__in=wanted).delete()
    existing = set(
        DashboardAccess.objects.filter(user=user).values_list("dashboard__slug", flat=True)
    )
    for slug in wanted - existing:
        DashboardAccess.objects.create(
            user=user,
            dashboard=catalog[slug],
            granted_by=granted_by if getattr(granted_by, "pk", None) else None,
        )


def index(request):
    return render(request, "tiqueue/index.html")


def loginPage(request):
    if request.user.is_authenticated:
        return redirect("index")

    error_message = None
    success_message = None
    redirect_to = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME) or ""

    if request.method == "POST":
        accessField = request.POST.get("login")
        passwordField = request.POST.get("password")

        user = authenticate(request, username=accessField, password=passwordField)
        if user:
            login(request, user)
            if redirect_to and url_has_allowed_host_and_scheme(
                url=redirect_to,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(redirect_to)
            return redirect("index")
        error_message = "Login ou senha invalidos."

    if request.GET.get("password_reset") == "1":
        success_message = "Senha atualizada com sucesso. Voce ja pode entrar novamente."

    return render(
        request,
        "general/login.html",
        {
            "erro": error_message,
            "success_message": success_message,
            "next_url": redirect_to,
        },
    )


def forgotPasswordPage(request):
    if request.user.is_authenticated:
        return redirect("index")

    form = ForgotPasswordForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("/main/login/?password_reset=1")

    return render(request, "general/forgotPassword.html", {"form": form})


def logoutPage(request):
    if request.method == "POST":
        logout(request)
    return redirect("loginPage")


@login_required
def createUser(request):
    has_any_system_admin = User.objects.filter(is_system_admin=True).exists()
    can_manage_users = bool(
        getattr(request.user, "is_system_admin", False)
        or getattr(request.user, "is_superuser", False)
        or not has_any_system_admin
    )
    error_message = None
    success_message = None
    access_denied_message = None

    if not can_manage_users:
        access_denied_message = "Voce nao possui acesso a este modulo."

    if request.method == "POST" and can_manage_users:
        form_id = (request.POST.get("form_id") or "create").strip()

        if form_id == "edit":
            user_pk = request.POST.get("user_pk")
            user = User.objects.filter(pk=user_pk).first()
            if not user:
                error_message = "Usuario nao encontrado."
            else:
                user_id = (request.POST.get("userId") or "").strip()
                username = (request.POST.get("username") or "").strip()
                email = (request.POST.get("email") or "").strip()
                name_user = (request.POST.get("nameUser") or "").strip()
                id_sm = (request.POST.get("id_sm") or "").strip()
                id_erp = (request.POST.get("id_erp") or "").strip()
                is_representative = request.POST.get("is_representative") == "on"
                representative_code = (request.POST.get("representative_code") or "").strip()
                is_system_admin = request.POST.get("is_system_admin") == "on"
                password = (request.POST.get("password") or "").strip()

                if not user_id or not username or not email or not name_user:
                    error_message = "Preencha os campos obrigatorios para editar o usuario."
                elif is_representative and not representative_code:
                    error_message = "Informe o codigo do representante."
                elif User.objects.exclude(pk=user.pk).filter(userId=user_id).exists():
                    error_message = "Ja existe um usuario com esta matricula."
                elif User.objects.exclude(pk=user.pk).filter(username=username).exists():
                    error_message = "Ja existe um usuario com este login."
                elif User.objects.exclude(pk=user.pk).filter(email=email).exists():
                    error_message = "Ja existe um usuario com este e-mail."
                else:
                    user.userId = user_id
                    user.username = username
                    user.email = email
                    user.nameUser = name_user
                    user.id_sm = id_sm or None
                    user.id_erp = id_erp or None
                    user.is_representative = is_representative
                    user.representative_code = representative_code if is_representative else None
                    user.is_system_admin = is_system_admin
                    user.can_access_internal = request.POST.get("can_access_internal") == "on"
                    user.dashes_ai_daily_limit = _parse_ai_daily_limit(request.POST.get("dashes_ai_daily_limit"))
                    if password:
                        user.set_password(password)
                    user.save()
                    _sync_dashboard_access(
                        user,
                        request.POST.getlist("dashboards"),
                        granted_by=request.user,
                    )
                    success_message = "Usuario atualizado com sucesso."
        else:
            name_user = (request.POST.get("nameUser") or "").strip()
            user_id = (request.POST.get("userId") or "").strip()
            email = (request.POST.get("email") or "").strip()
            username = (request.POST.get("username") or "").strip()
            id_sm = (request.POST.get("id_sm") or "").strip()
            id_erp = (request.POST.get("id_erp") or "").strip()
            is_representative = request.POST.get("is_representative") == "on"
            representative_code = (request.POST.get("representative_code") or "").strip()
            is_system_admin = request.POST.get("is_system_admin") == "on"
            password = (request.POST.get("password") or "").strip()

            if not name_user or not user_id or not email or not username or not password:
                error_message = "Preencha todos os campos para cadastrar o usuario."
            elif is_representative and not representative_code:
                error_message = "Informe o codigo do representante."
            else:
                try:
                    created = User.objects.create_user(
                        userId=user_id,
                        username=username,
                        email=email,
                        nameUser=name_user,
                        id_sm=id_sm or None,
                        id_erp=id_erp or None,
                        is_representative=is_representative,
                        representative_code=representative_code if is_representative else None,
                        is_system_admin=is_system_admin,
                        can_access_internal=request.POST.get("can_access_internal") == "on",
                        dashes_ai_daily_limit=_parse_ai_daily_limit(request.POST.get("dashes_ai_daily_limit")),
                        password=password,
                    )
                    _sync_dashboard_access(
                        created,
                        request.POST.getlist("dashboards"),
                        granted_by=request.user,
                    )
                    success_message = "Usuario cadastrado com sucesso."
                except IntegrityError:
                    error_message = "Ja existe um usuario com matricula, login ou e-mail informado."

    dashboards = list(Dashboard.objects.filter(is_active=True))
    users = list(User.objects.all().order_by("nameUser", "username"))

    # Um mapa por usuário para o template marcar os checkboxes e a listagem
    # mostrar quais painéis cada um enxerga.
    granted = {}
    for access in DashboardAccess.objects.select_related("dashboard").filter(user__in=users):
        granted.setdefault(access.user_id, []).append(access.dashboard.slug)

    user_rows = []
    for item in users:
        slugs = granted.get(item.id, [])
        is_admin = item.is_system_admin or item.is_superuser
        user_rows.append(
            {
                "user": item,
                "dashboard_slugs": slugs,
                # O administrador enxerga o catálogo inteiro por definição.
                "dashboard_names": (
                    [dash.name for dash in dashboards]
                    if is_admin
                    else [dash.name for dash in dashboards if dash.slug in slugs]
                ),
                "sees_all_dashboards": is_admin,
                "initials": _initials(item),
            }
        )

    # O log responde "quem andou usando", então a ordem é por acesso recente —
    # quem nunca entrou fica no fim, não perdido no meio da ordem alfabética.
    accessed = [row for row in user_rows if row["user"].last_access_at]
    accessed.sort(key=lambda row: row["user"].last_access_at, reverse=True)
    activity_rows = accessed + [row for row in user_rows if not row["user"].last_access_at]

    now = timezone.now()
    active_24h = sum(
        1
        for item in users
        if item.last_access_at and (now - item.last_access_at) <= timedelta(hours=24)
    )
    stats = {
        "total": len(users),
        "admins": sum(1 for item in users if item.is_system_admin or item.is_superuser),
        "dashes_only": sum(1 for item in users if not item.can_access_internal),
        "with_dashboard": sum(
            1
            for row in user_rows
            if row["sees_all_dashboards"] or row["dashboard_slugs"]
        ),
        "active_24h": active_24h,
        "never_accessed": sum(1 for item in users if not item.last_access_at),
    }

    return render(
        request,
        "general/createUser.html",
        {
            "users": users,
            "user_rows": user_rows,
            "activity_rows": activity_rows,
            "dashboards": dashboards,
            "stats": stats,
            "can_manage_users": can_manage_users,
            "access_denied_message": access_denied_message,
            "error_message": error_message,
            "success_message": success_message,
        },
    )


@require_POST
def toggleFavoriteScreen(request):
    """Liga/desliga a estrela de uma tela na sidebar.

    Só aceita destinos do catálogo de navegação: assim a estrela nunca aponta
    para uma rota que o menu não conhece.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"status": "error", "message": "Sessao expirada."}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"status": "error", "message": "Payload invalido."}, status=400)

    url_name = str(payload.get("url_name") or "").strip()
    if url_name not in known_url_names():
        return JsonResponse({"status": "error", "message": "Tela desconhecida."}, status=400)

    # A tela precisa existir no menu DESTE usuário: sem isso daria para favoritar
    # uma tela de administração sem ter acesso a ela.
    destination = destination_by_url_name(build_menu(request.user), url_name)
    if not destination:
        return JsonResponse({"status": "error", "message": "Tela indisponivel."}, status=403)

    visit, _created = ScreenVisit.objects.get_or_create(user=request.user, url_name=url_name)
    visit.is_favorite = not visit.is_favorite
    visit.favorited_at = timezone.now() if visit.is_favorite else None
    visit.save(update_fields=["is_favorite", "favorited_at", "last_visited_at"])

    return JsonResponse(
        {
            "status": "ok",
            "is_favorite": visit.is_favorite,
            "label": destination["label"],
            "url": destination["url"],
            "group": destination["group"],
        }
    )
