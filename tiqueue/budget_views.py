"""Painel "Orçamento" do Dashes: meta × faturado × previsão (regras e SQL em `tiqueue/budget_panel.py`)."""

from datetime import date

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from . import budget_panel, commercial_dna as dna
from .views import _dashes_access_required, _is_system_admin, _maxisys_shell

SLUG = "budget-panel"
TITLE = "Orçamento"


def _int(value):
    text = str(value if value is not None else "").strip()
    return int(text) if text.lstrip("-").isdigit() else None


def _ints(request, name):
    values = []
    for raw in request.GET.getlist(name):
        for part in str(raw).split(","):
            number = _int(part)
            if number is not None:
                values.append(number)
    return values


@_dashes_access_required(SLUG)
def dashesBudgetPanel(request):
    today = date.today()
    years = budget_panel.available_years(today)
    year = _int(request.GET.get("ano"))
    if year not in years:
        year = today.year
    detail_url = reverse("dashesBudgetPanelDetail")
    profile_url_template = reverse("dashesCustomerProfilePage", args=[0]).replace("/0/", "/__code__/")
    context = {
        **_maxisys_shell(request, SLUG, TITLE),
        "page_url": reverse("dashesBudgetPanel"),
        "detail_url": detail_url,
        "goals_url": reverse("dashesSalesGoalsPage"),
        "profile_url_template": profile_url_template,
        "years": years,
        "year": year,
        "is_admin": _is_system_admin(request.user),
        "data_error": None,
        # O que o budgetPanel.js precisa além dos dados (json_script no template).
        "page_config": {"detail_url": detail_url, "profile_url_template": profile_url_template, "year": year},
    }
    try:
        payload = budget_panel.budget_payload(year, today)
    except Exception as exc:
        message = f"Não foi possível consultar o ERP Senior: {exc}"
        if request.GET.get("formato") == "json":
            return JsonResponse({"error": message}, status=502)
        context["data_error"] = message
        return render(request, "tiqueue/maxisys/panel_budget.html", context)
    if request.GET.get("formato") == "json":
        return JsonResponse(payload)
    context["payload"] = payload
    return render(request, "tiqueue/maxisys/panel_budget.html", context)


@_dashes_access_required(SLUG)
def dashesBudgetPanelDetail(request):
    """Notas fiscais por trás de um ponto do painel (mesmos filtros: meses, unidades, famílias, representantes e clientes)."""
    today = date.today()
    year = _int(request.GET.get("ano")) or today.year
    if not 2000 <= year <= today.year + 1:
        return JsonResponse({"error": "Ano inválido."}, status=400)
    months = _ints(request, "m")
    units = _ints(request, "u")
    families = [value.strip() for raw in request.GET.getlist("f") for value in raw.split(",") if value.strip()]
    reps = _ints(request, "r")
    customers = _ints(request, "c")
    net = request.GET.get("base") == "sem_impostos"
    try:
        lines = budget_panel.invoice_detail(year, months, units, families, reps, customers, net=net)
    except Exception as exc:
        return JsonResponse({"error": f"Não foi possível consultar o ERP Senior: {exc}"}, status=502)
    shown = lines[: budget_panel.MAX_INVOICE_LINES]
    kg = sum(line["kg"] or 0 for line in lines)
    value = sum(line["value"] or 0 for line in lines)
    title = request.GET.get("titulo") or "Notas fiscais"
    return JsonResponse({
        "title": title[:120],
        "subtitle": f"Notas de {year}{' · ' + ', '.join(dna.MONTH_NAMES[m - 1] for m in sorted(set(months))) if months else ''} · "
                    f"{'R$ sem impostos' if net else 'R$ com impostos, líquido de devolução'}",
        "summary": [
            {"label": "Volume", "value": f"{kg:,.0f} kg".replace(",", ".")},
            {"label": "Valor", "value": f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")},
            {"label": "Notas", "value": str(len({line['invoice'] for line in lines}))},
            {"label": "Clientes", "value": str(len({line['customer'] for line in lines}))},
        ],
        "sections": [{
            "key": "notas",
            "label": "Notas fiscais" + (f" (as {len(shown)} mais recentes)" if len(lines) > len(shown) else ""),
            "columns": [
                {"key": "invoice", "label": "Nota", "nowrap": True},
                {"key": "date", "label": "Emissão", "type": "date", "nowrap": True},
                {"key": "customer_name", "label": "Cliente", "sub": "customer"},
                {"key": "rep", "label": "Representante"},
                {"key": "unit", "label": "Unidade"},
                {"key": "group", "label": "Grupo", "sub": "family"},
                {"key": "product", "label": "Produto", "wrap": True},
                {"key": "order", "label": "Pedido"},
                {"key": "kg", "label": "Kg", "type": "kg", "total": True, "strong": True},
                {"key": "value", "label": "Valor", "type": "money", "total": True},
            ],
            "rows": shown,
            "sort": {"key": "date", "dir": "desc"},
            "groupBy": ["customer_name", "rep", "unit", "group", "invoice"],
            "empty": "Nenhuma nota para este ponto.",
        }],
    })
