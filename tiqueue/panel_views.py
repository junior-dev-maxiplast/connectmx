"""Painéis-padrão de mercado no Dashes (padrão MaxiSys, animados e com filtro cruzado):
Vendas, Contas a Receber, Entregas (OTIF) e Qualidade. Os dados vêm de `panels`."""

from datetime import date

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from . import commercial_dna as dna
from . import panels
from .models import RepresentativeGoal
from .views import _dashes_access_required, _is_system_admin, _maxisys_shell


def _context(request, active, title):
    return {
        **_maxisys_shell(request, active, title),
        "is_admin": _is_system_admin(request.user),
        "data_error": None,
        "refresh_url": reverse("dashesCustomerIntelRefresh"),
        "profile_url_template": reverse("dashesCustomerProfilePage", args=[0]).replace("/0/", "/__code__/"),
    }


def _respond(request, template, context, loader):
    try:
        payload = loader()
    except Exception as exc:
        if request.GET.get("formato") == "json":
            return JsonResponse({"error": f"Não foi possível consultar o ERP Senior: {exc}"}, status=502)
        context["data_error"] = f"Não foi possível consultar o ERP Senior: {exc}"
        return render(request, template, context)
    if request.GET.get("formato") == "json":
        return JsonResponse(payload)
    context["payload"] = payload
    return render(request, template, context)


def _months_from(request, today):
    """`?mes=AAAA-MM`, ou vários separados por vírgula (até 12), entre 23 meses atrás e o mês corrente."""
    current = today.replace(day=1)
    oldest = dna._add_months(current, -23)
    months = []
    for raw in (request.GET.get("mes") or "").split(","):
        try:
            year, month = (int(part) for part in raw.strip().split("-"))
            chosen = date(year, month, 1)
        except (ValueError, TypeError):
            continue
        chosen = min(max(chosen, oldest), current)
        if chosen not in months:
            months.append(chosen)
    return sorted(months)[-panels.MAX_SALES_MONTHS:] or [current]


@_dashes_access_required("sales-panel")
def dashesSalesPanel(request):
    today = date.today()
    months = _months_from(request, today)
    single = len(months) == 1
    previous = dna._add_months(months[0], -1)
    following = dna._add_months(months[-1], 1)
    context = _context(request, "sales-panel", "Painel de Vendas")
    context.update({
        "page_url": reverse("dashesSalesPanel"),
        "previous_month": previous.strftime("%Y-%m") if single and previous >= dna._add_months(today.replace(day=1), -23) else None,
        "next_month": following.strftime("%Y-%m") if single and following <= today.replace(day=1) else None,
        "month_key": ",".join(month.strftime("%Y-%m") for month in months),
    })
    goals = list(
        RepresentativeGoal.objects.filter(year__in={month.year for month in months}, month__in={month.month for month in months})
        .values("year", "month", "rep_code", "weight_kg")
    )
    return _respond(request, "tiqueue/maxisys/panel_sales.html", context, lambda: panels.sales_payload(months, today, goals))


@_dashes_access_required("receivables-panel")
def dashesReceivablesPanel(request):
    context = _context(request, "receivables-panel", "Contas a Receber")
    context["page_url"] = reverse("dashesReceivablesPanel")
    return _respond(request, "tiqueue/maxisys/panel_receivables.html", context, lambda: panels.receivables_payload(date.today()))


@_dashes_access_required("service-panel")
def dashesServicePanel(request):
    context = _context(request, "service-panel", "Entregas (OTIF)")
    context["page_url"] = reverse("dashesServicePanel")
    return _respond(request, "tiqueue/maxisys/panel_service.html", context, lambda: panels.service_payload(date.today()))


@_dashes_access_required("quality-panel")
def dashesQualityPanel(request):
    context = _context(request, "quality-panel", "Qualidade")
    context["page_url"] = reverse("dashesQualityPanel")
    return _respond(request, "tiqueue/maxisys/panel_quality.html", context, lambda: panels.quality_payload(date.today()))
