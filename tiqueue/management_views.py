"""Painel Gerencial e Agenda Comercial (padrão MaxiSys, no Dashes).

* Painel Gerencial (`management-cockpit`): resultado da empresa em meses fechados,
  com as pontes de faturamento e de lucro, retenção, matriz de rentabilidade,
  produtos, sazonalidade com projeção e carteira, representantes, simulador de
  cenários e a leitura da IA (OpenAI).
* Agenda Comercial (`sales-agenda`): quem procurar nos próximos dias e por quê,
  com registro do contato e exportação em CSV.

Abrir as telas não chama a OpenAI nem a Econodata: só o botão da leitura da IA.
"""

import csv
from datetime import timedelta

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from . import commercial_dna
from . import customer_intel as intel
from . import management_intel as mgmt
from . import page_filters
from . import sales_agenda
from .intel_views import (
    _ai_context,
    _back,
    _insight_key,
    _latest_insight,
    _prepare,
    _receita_status,
    _run_insight,
)
from .models import RepresentativeGoal, SalesAgendaContact
from .views import _dashes_access_required, _is_system_admin, _maxisys_shell


# ---------------------------------------------------------------------------
# Painel Gerencial
# ---------------------------------------------------------------------------

def _management_filters(params):
    """Filtros por clique: representantes, grupos e meses escolhidos (`?rep=…&grupo=…&meses=AAAA-MM,…`)."""
    return {
        "reps": page_filters.ints(params, "rep"),
        "groups": page_filters.values(params, "grupo"),
        "months": page_filters.month_keys(params, "meses"),
    }


def _management_analysis(period_key, filters=None):
    filters = filters or {}
    full = mgmt.load_base()
    base = mgmt.filter_base(full, filters.get("reps"), filters.get("groups"))
    window = mgmt.month_window(filters["months"], full["today"]) if filters.get("months") else mgmt.closed_window(period_key, full["today"])
    goals = list(RepresentativeGoal.objects.values("year", "month", "rep_code", "weight_kg"))
    analysis = mgmt.analyze(base, window, goals, full["rep_names"], full["customers"], full_base=full)
    return full, window, analysis


def _management_key(window, filters):
    return _insight_key("management", window["key"], ",".join(str(rep) for rep in filters["reps"]),
                        ",".join(filters["groups"]), ",".join(str(key) for key in window.get("month_keys") or ()))


def _management_chips(page_url, params, base, window, filters):
    return page_filters.chips(page_url, params, [
        ("rep", "Representante", [base["rep_names"].get(rep) or f"Representante {rep}" for rep in filters["reps"]]),
        ("grupo", "Grupo", [group.title() for group in filters["groups"]]),
        ("meses", "Meses", [commercial_dna._month_label(key) for key in window.get("month_keys") or ()]),
    ])


@_dashes_access_required("management-cockpit")
def dashesManagementPage(request):
    period_key = (request.GET.get("periodo") or mgmt.DEFAULT_PERIOD).strip()
    filters = _management_filters(request.GET)
    context = {
        **_maxisys_shell(request, "management-cockpit", "Painel Gerencial"),
        **_ai_context(request),
        "is_admin": _is_system_admin(request.user),
        "data_error": None,
        "page_url": reverse("dashesManagementPage"),
        "refresh_url": reverse("dashesCustomerIntelRefresh"),
    }
    try:
        base, window, analysis = _management_analysis(period_key, filters)
    except Exception as exc:
        context["data_error"] = f"Não foi possível consultar o ERP Senior: {exc}"
        return render(request, "tiqueue/maxisys/management.html", context)
    page_url = context["page_url"]
    chips = _management_chips(page_url, request.GET, base, window, filters)
    customers = base["customers"]
    profile_template = reverse("dashesCustomerProfilePage", args=[0]).replace("/0/", "/__code__/")
    retention_data = analysis["retention"]
    matrix = mgmt.matrix_display(analysis["matrix"], customers)
    forecast = mgmt.forecast_display(analysis["forecast"])
    revenue_bridge = analysis["revenue_bridge"]
    profit_bridge = analysis["profit_bridge"]
    key = _management_key(window, filters)
    context.update({
        "window": window,
        "period_choices": window["choices"],
        "period_links": [
            {"label": choice["label"], "active": window["key"] == choice["key"],
             "url": page_filters.url_with(page_url, request.GET, drop=("meses",), periodo=choice["key"])}
            for choice in window["choices"]
        ],
        "filter_chips": chips,
        "clear_filters_url": page_filters.url_without(page_url, request.GET, "rep", "grupo", "meses"),
        "management_filtered": analysis["filtered"],
        "month_picker": {"months": mgmt.selectable_months(base["today"]), "selected": window.get("month_keys") or []},
        "loaded_at": timezone.localtime(base["loaded_at"]),
        "kpis": mgmt.kpis(analysis),
        "revenue_bridge": revenue_bridge,
        "revenue_rows": mgmt.bridge_rows(revenue_bridge),
        "profit_bridge": profit_bridge,
        "profit_rows": mgmt.bridge_rows(profit_bridge),
        "revenue_change": mgmt.money(revenue_bridge["change"]),
        "revenue_change_pct": mgmt.pct(revenue_bridge["change_pct"], signed=True) if revenue_bridge["change_pct"] is not None else "–",
        "profit_change": mgmt.money(profit_bridge["change"]),
        "profit_change_pct": mgmt.pct(profit_bridge["change_pct"], signed=True) if profit_bridge["change_pct"] is not None else "–",
        "retention": retention_data,
        "retention_display": {
            "nrr": mgmt.pct(retention_data["nrr"]), "grr": mgmt.pct(retention_data["grr"]),
            "logo": mgmt.pct(retention_data["logo_retention"]),
            "churn": mgmt.money(retention_data["churn"]), "contraction": mgmt.money(retention_data["contraction"]),
            "expansion": mgmt.money(retention_data["expansion"]), "new_revenue": mgmt.money(retention_data["new_revenue"]),
        },
        "flows": mgmt.flows_display(retention_data, customers),
        "matrix": matrix,
        "groups": mgmt.groups_display(analysis["groups"]),
        "heatmap": mgmt.heatmap_display(analysis["heatmap"], analysis["now"]["margin"]),
        "seasonality": mgmt.seasonality_display(analysis["seasonality"]),
        "forecast": forecast,
        "reps": mgmt.reps_display(analysis["reps"]),
        "goal_year": analysis["reps"]["goal_year"],
        "goal_month_label": commercial_dna._month_label(window["last_key"]),
        "goal_label": analysis["reps"]["goal_label"],
        "profile_url_template": profile_template,
        "charts": {
            "revenue_bridge": {**mgmt.bridge_chart(revenue_bridge), "start_label": f"Faturamento {window['previous_range_label']}", "end_label": f"Faturamento {window['range_label']}"},
            "profit_bridge": {**mgmt.bridge_chart(profit_bridge), "start_label": f"Lucro {window['previous_range_label']}", "end_label": f"Lucro {window['range_label']}"},
            "retention": {
                "start": mgmt._f(retention_data["start"], 0), "end": mgmt._f(retention_data["end"], 0),
                "steps": [
                    {"label": "Clientes perdidos", "value": -mgmt._f(retention_data["churn"], 0), "detail": f"{retention_data['lost']} cliente(s) que pararam de comprar"},
                    {"label": "Compraram menos", "value": -mgmt._f(retention_data["contraction"], 0), "detail": "clientes que continuam, com menos faturamento"},
                    {"label": "Compraram mais", "value": mgmt._f(retention_data["expansion"], 0), "detail": "clientes que continuam, com mais faturamento"},
                    {"label": "Clientes novos", "value": mgmt._f(retention_data["new_revenue"], 0), "detail": f"{retention_data['new']} cliente(s) novo(s)"},
                ],
                "start_label": "Ano anterior", "end_label": "Período atual",
            },
            "matrix": matrix["chart"],
            "forecast": forecast["chart"],
            "simulator": mgmt.simulator_data(analysis),
            "profile_url_template": profile_template,
        },
        "insight": _latest_insight("management", key),
        "insight_url": reverse("dashesManagementInsight"),
        "insight_fields": {
            "periodo": window["key"] if not window.get("month_keys") else "",
            "rep": ",".join(str(rep) for rep in filters["reps"]),
            "grupo": ",".join(filters["groups"]),
            "meses": ",".join(page_filters.month_param(key) for key in window.get("month_keys") or ()),
        },
    })
    return render(request, "tiqueue/maxisys/management.html", context)


@_dashes_access_required("management-cockpit")
def dashesManagementInsight(request):
    back = _back(request, "dashesManagementPage")
    if request.method != "POST":
        return redirect(back)
    filters = _management_filters(request.POST)
    try:
        base, window, analysis = _management_analysis(request.POST.get("periodo"), filters)
    except Exception as exc:
        messages.error(request, f"Não foi possível consultar o ERP Senior: {exc}")
        return redirect(back)
    data = mgmt.management_ai_data(analysis)
    chips = _management_chips(reverse("dashesManagementPage"), request.POST, base, window, filters)
    if chips:
        data["recorte"] = [f"{chip['label']}: {chip['title']}" for chip in chips]
    key = _management_key(window, filters)
    return _run_insight(request, "management", key, window["key"], data, back)


# ---------------------------------------------------------------------------
# Agenda Comercial
# ---------------------------------------------------------------------------

def _last_contacts(codes):
    """Último contato registrado de cada cliente (últimos 120 dias)."""
    since = timezone.now() - timedelta(days=120)
    contacts = {}
    for contact in (
        SalesAgendaContact.objects.filter(customer_code__in=list(codes), created_at__gte=since)
        .select_related("user")
        .order_by("customer_code", "-created_at")
    ):
        if contact.customer_code in contacts:
            continue
        contacts[contact.customer_code] = {
            "date": timezone.localtime(contact.created_at).date(),
            "outcome": contact.outcome,
            "outcome_label": contact.get_outcome_display(),
            "note": contact.note,
            "by": (contact.user.get_full_name() or contact.user.username) if contact.user else "",
        }
    return contacts


def _agenda(request):
    raw_rep = (request.GET.get("rep") or "").strip()
    rep_code = int(raw_rep) if raw_rep.isdigit() else None
    try:
        horizon = int(request.GET.get("dias") or sales_agenda.DEFAULT_HORIZON)
    except ValueError:
        horizon = sales_agenda.DEFAULT_HORIZON
    horizon = horizon if horizon in sales_agenda.HORIZONS else sales_agenda.DEFAULT_HORIZON
    base, window, metrics = _prepare(intel.DEFAULT_PERIOD)
    model = intel.cross_sell_model(base, metrics)
    codes = [item["code"] for item in metrics.values() if rep_code is None or item["rep_code"] == rep_code]
    contacts = _last_contacts(codes)
    receita = _receita_status([metrics[code]["cnpj"] for code in codes if metrics[code]["cnpj"]])
    agenda = sales_agenda.build_agenda(metrics, base, model, rep_code, horizon, base["today"], contacts, receita)
    reps = {}
    for item in metrics.values():
        if item["rep_code"] is not None and (item["net"] > 0 or item["history_net"] > 0):
            entry = reps.setdefault(item["rep_code"], {"code": item["rep_code"], "name": item["rep_name"], "net": 0})
            entry["net"] += float(item["net"])
    return base, rep_code, horizon, agenda, sorted(reps.values(), key=lambda entry: entry["net"], reverse=True)


@_dashes_access_required("sales-agenda")
def dashesSalesAgendaPage(request):
    context = {
        **_maxisys_shell(request, "sales-agenda", "Agenda Comercial"),
        "data_error": None,
        "page_url": reverse("dashesSalesAgendaPage"),
        "contact_url": reverse("dashesSalesAgendaContact"),
        "outcome_choices": SalesAgendaContact.OUTCOME_CHOICES,
        "horizons": sales_agenda.HORIZONS,
        "profile_url_template": reverse("dashesCustomerProfilePage", args=[0]).replace("/0/", "/__code__/"),
    }
    try:
        base, rep_code, horizon, agenda, reps = _agenda(request)
    except Exception as exc:
        context["data_error"] = f"Não foi possível consultar o ERP Senior: {exc}"
        return render(request, "tiqueue/maxisys/sales_agenda.html", context)
    rep_name = next((rep["name"] for rep in reps if rep["code"] == rep_code), None)
    if request.GET.get("formato") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        filename = f"agenda-{rep_code or 'todos'}-{base['today']:%Y%m%d}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("﻿")  # BOM: o Excel abre com acento
        writer = csv.writer(response, delimiter=";")
        writer.writerow(sales_agenda.CSV_HEADER)
        writer.writerows(sales_agenda.csv_rows([entry for entry in agenda["items"] if not entry["snoozed"]][:200]))
        return response
    since = timezone.now() - timedelta(days=7)
    recent = SalesAgendaContact.objects.filter(created_at__gte=since)
    if rep_code is not None:
        recent = recent.filter(rep_code=rep_code)
    query = f"rep={rep_code}&" if rep_code is not None else ""
    pending = [entry for entry in agenda["items"] if not entry["snoozed"]]
    context.update({
        "loaded_at": timezone.localtime(base["loaded_at"]),
        "rep_code": rep_code,
        "rep_name": rep_name,
        "rep_choices": reps,
        "horizon": horizon,
        "agenda": agenda,
        "entries": [sales_agenda.entry_display(entry) for entry in pending[: sales_agenda.LIST_LIMIT]],
        "more": max(len(pending) - sales_agenda.LIST_LIMIT, 0),
        "contacted": [sales_agenda.entry_display(entry) for entry in agenda["items"] if entry["snoozed"]][:40],
        "expected_display": intel.money(agenda["expected_value"]),
        "at_stake_display": intel.money(agenda["at_stake"]),
        "recent_contacts": recent.count(),
        "recent_orders": recent.filter(outcome="pedido").count(),
        "csv_url": f"{reverse('dashesSalesAgendaPage')}?{query}dias={horizon}&formato=csv",
        "query_prefix": query,
        "reason_labels": [{"key": key, "label": sales_agenda.REASONS[key][0], "tone": sales_agenda.REASONS[key][1], "count": agenda["counts"][key]} for key in sales_agenda.REASON_ORDER],
    })
    return render(request, "tiqueue/maxisys/sales_agenda.html", context)


@_dashes_access_required("sales-agenda")
def dashesSalesAgendaContact(request):
    back = _back(request, "dashesSalesAgendaPage")
    if request.method != "POST":
        return redirect(back)
    raw_code = (request.POST.get("cliente") or "").strip()
    outcome = request.POST.get("resultado")
    if not raw_code.isdigit() or outcome not in dict(SalesAgendaContact.OUTCOME_CHOICES):
        messages.error(request, "Contato inválido: escolha o resultado de novo.")
        return redirect(back)
    raw_rep = (request.POST.get("rep") or "").strip()
    SalesAgendaContact.objects.create(
        customer_code=int(raw_code),
        customer_name=(request.POST.get("nome") or "")[:240],
        rep_code=int(raw_rep) if raw_rep.isdigit() else None,
        reason=(request.POST.get("motivo") or "")[:40],
        outcome=outcome,
        note=(request.POST.get("nota") or "").strip()[:300],
        user=request.user,
    )
    messages.success(
        request,
        f"Contato registrado: {(request.POST.get('nome') or raw_code)[:60]} · {dict(SalesAgendaContact.OUTCOME_CHOICES)[outcome]}. "
        f"O cliente sai da lista por {sales_agenda.SNOOZE_DAYS} dias.",
    )
    return redirect(back)
