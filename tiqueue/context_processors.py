from .models import SystemConfig


def system_config(request):
    cfg = SystemConfig.objects.order_by("-updated_at", "-id").first()
    return {
        "sidebar_system_version": (cfg.system_version if cfg and cfg.system_version else ""),
    }

