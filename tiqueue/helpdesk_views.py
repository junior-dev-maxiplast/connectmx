"""Painel do Helpdesk no Dashes: a tela inicial, com filtro cruzado (os dados
vêm de `helpdesk_bi`, só da base do ConnectMX)."""

import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from . import helpdesk_bi
from .views import _dashes_access_required, _is_system_admin, _maxisys_shell

logger = logging.getLogger(__name__)


@_dashes_access_required("helpdesk")
def dashesHelpdeskPage(request):
    context = {
        **_maxisys_shell(request, "helpdesk-panel", "Painel do Helpdesk"),
        "is_admin": _is_system_admin(request.user),
        "data_error": None,
        "page_url": reverse("dashesHelpdeskPage"),
        "queue_url": reverse("portalPendingDemandsPage"),
        "can_access_internal": bool(getattr(request.user, "can_access_internal", False)),
    }
    try:
        payload = helpdesk_bi.helpdesk_payload()
    except Exception as exc:  # banco fora do ar: a tela explica em vez de quebrar
        logger.exception("Falha ao montar o Painel do Helpdesk")
        message = f"Não foi possível ler a base de chamados: {exc}"
        if request.GET.get("formato") == "json":
            return JsonResponse({"error": message}, status=502)
        context["data_error"] = message
        return render(request, "tiqueue/maxisys/panel_helpdesk.html", context)

    if request.GET.get("formato") == "json":
        return JsonResponse(payload)
    context["payload"] = payload
    return render(request, "tiqueue/maxisys/panel_helpdesk.html", context)


@_dashes_access_required("helpdesk")
def dashesHelpdeskTickets(request):
    """Protocolo e assunto dos chamados listados na tabela do painel (`?ids=1,2`)."""
    ids = (request.GET.get("ids") or "").split(",")
    return JsonResponse({"tickets": helpdesk_bi.ticket_details(ids)})
