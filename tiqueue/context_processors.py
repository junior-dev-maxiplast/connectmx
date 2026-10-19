from .models import DashAlert, Dashboard, SystemConfig


def system_config(request):
    cfg = SystemConfig.objects.order_by("-updated_at", "-id").first()
    return {
        "sidebar_system_version": (cfg.system_version if cfg and cfg.system_version else ""),
    }


def dashes_alerts(request):
    """Contador de alertas ativos para o selo da Central de Decisão na
    sidebar. Roda em toda página do ConnectMX (registrado globalmente em
    `settings.py`, ao lado de `system_config`), mas sai cedo e sem consulta
    fora de uma sessão do Dashes — nenhuma das 4 views de painel precisa
    saber que isso existe.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and request.session.get("dashes_authenticated")):
        return {"dashes_alert_count": 0}

    if getattr(user, "is_system_admin", False) or user.is_superuser:
        dashboards = Dashboard.objects.filter(is_active=True)
    else:
        dashboards = Dashboard.objects.filter(is_active=True, accesses__user=user).distinct()

    count = DashAlert.objects.filter(status=DashAlert.STATUS_ACTIVE, dashboard__in=dashboards).count()
    return {"dashes_alert_count": count}

