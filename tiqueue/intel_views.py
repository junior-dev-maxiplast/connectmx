"""Telas de inteligência de clientes (padrão MaxiSys, no Dashes).

* Mapa de Clientes (`customer-map`): onde estão os clientes, quanto vendemos e
  quanto lucramos por região, UF e município, com o potencial do território e a
  prospecção de empresas parecidas (Econodata).
* Carteira de Clientes (`customer-portfolio`): os mais lucrativos, o risco de
  parada e a reativação, e a ficha de cada cliente.

Os números vêm de `customer_intel` (base do ERP com cache de 30 minutos). Abrir
uma tela nunca chama a Econodata nem a OpenAI: só os botões, e os que gastam
tokens da Econodata são só de administradores.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from accounts.models import DashesAiUsage
from django.contrib import messages
from django.db import models
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from . import ai_additional, commercial_dna, econodata, ibge, intel_ai, page_filters
from . import customer_intel as intel
from .ai_config import get_openai_runtime_config
from .models import CustomerIntelInsight, CustomerSegmentGuess, EconodataContagem, EconodataEmpresa, EconodataProspect
from .views import (
    DASHES_AI_LIMIT_MESSAGE,
    _consume_dashes_ai_quota,
    _dashes_access_required,
    _dashes_ai_quota,
    _is_system_admin,
    _maxisys_shell,
)

INSIGHT_STALE_DAYS = 7
CLASSIFY_LIMIT = 40
REACTIVATION_CHECK_LIMIT = 20
TABLE_LIMIT = 300  # linhas na tabela da carteira; o resto aparece pelos filtros
PORTFOLIO_VIEWS = (("lucrativos", "Mais lucrativos"), ("risco", "Risco de parada"), ("reativacao", "Reativação"))
MAP_METRICS = (("net", "Faturamento"), ("profit", "Lucro bruto"), ("margin", "Margem"), ("customers", "Clientes"), ("growth", "Crescimento"))


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def _back(request, default_name, *args):
    default = reverse(default_name, args=args or None)
    target = request.POST.get("next") or default
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return default
    return target


def _guesses():
    return {
        guess.customer_code: {"code": guess.sector_code, "name": guess.sector_name, "confidence": guess.confidence}
        for guess in CustomerSegmentGuess.objects.all()
    }


def _filters(source):
    """UF, representante e segmento; cada um aceita vários valores separados por vírgula (clique + Ctrl)."""
    states = [state.upper() for state in page_filters.values(source, "uf")]
    return {
        "state": [state for state in states if state in intel.STATES or state == intel.FOREIGN],
        "rep": page_filters.ints(source, "rep"),
        "segment": [segment[:10] for segment in page_filters.values(source, "segmento")],
        "rfm": [key for key in page_filters.values(source, "rfm") if key in intel.RFM_SEGMENTS],
    }


def _csv(values):
    return ",".join(str(value) for value in values or ())


def _prepare(period_key):
    base = intel.load_base()
    window = intel.resolve_window(period_key, today=base["today"])
    metrics = intel.customer_metrics(base, window)
    intel.apply_segments(metrics, base, _guesses())
    intel.apply_rfm(metrics)
    return base, window, metrics


def _rep_choices(metrics):
    reps = {}
    for item in metrics.values():
        if item["net"] > 0 and item["rep_code"] is not None:
            entry = reps.setdefault(item["rep_code"], {"code": item["rep_code"], "name": item["rep_name"], "net": Decimal("0")})
            entry["net"] += item["net"]
    return sorted(reps.values(), key=lambda entry: entry["net"], reverse=True)


def _segment_choices(metrics):
    segments, without = {}, 0
    for item in metrics.values():
        if item["net"] <= 0:
            continue
        segment = item.get("segment")
        if not segment:
            without += 1
            continue
        entry = segments.setdefault(segment["code"], {"code": segment["code"], "name": segment["name"].title(), "count": 0, "ai": 0})
        entry["count"] += 1
        entry["ai"] += 1 if segment["source"] == "ia" else 0
    choices = sorted(segments.values(), key=lambda entry: entry["count"], reverse=True)
    if without:
        choices.append({"code": "none", "name": "Sem segmento", "count": without, "ai": 0})
    return choices


def _filter_labels(filters, base, segment_choices):
    labels = []
    for state in filters.get("state") or ():
        labels.append(intel.STATES[state][1] if state in intel.STATES else "Exterior")
    for rep in filters.get("rep") or ():
        labels.append(intel.rep_display_name(rep, base))
    names = {choice["code"]: choice["name"] for choice in segment_choices}
    for segment in filters.get("segment") or ():
        labels.append(names.get(segment, segment))
    for key in filters.get("rfm") or ():
        labels.append(f"RFM {intel.RFM_SEGMENTS[key][0]}")
    return labels


def _insight_key(*parts):
    """Chave do recorte da leitura da IA; listas (filtros com vários valores) entram separadas por vírgula."""
    return "|".join(_csv(part) if isinstance(part, (list, tuple)) else "" if part is None else str(part) for part in parts)[:160]


def _latest_insight(scope, key):
    record = (
        CustomerIntelInsight.objects.filter(scope=scope, scope_key=key, status=CustomerIntelInsight.STATUS_COMPLETED)
        .select_related("requested_by")
        .first()
    )
    if not record:
        return None
    user = record.requested_by
    return {
        "response": record.response,
        "created_at": record.created_at,
        "model": record.ai_model,
        "tokens": record.total_tokens,
        "stale": record.created_at < timezone.now() - timedelta(days=INSIGHT_STALE_DAYS),
        "by": (user.get_full_name() or user.username) if user else "",
    }


def _ai_context(request):
    config = get_openai_runtime_config()
    return {"ai_ready": bool(config["enabled"] and config["api_key_configured"]), "ai_quota": _dashes_ai_quota(request.user)}


def _summary_display(summary):
    pareto = summary["pareto"]
    return {
        **summary,
        "net_display": intel.money(summary["net"]),
        "profit_display": intel.money(summary["profit"]),
        "margin_display": intel.pct(summary["margin"]),
        "growth_display": intel.pct(summary["growth"], signed=True) if summary["growth"] is not None else "–",
        "growth_tone": "is-up" if summary["growth"] and summary["growth"] > 0 else "is-down" if summary["growth"] and summary["growth"] < 0 else "",
        "kg_display": intel.kg(summary["kg"]),
        "top20_display": intel.pct(pareto["top20_share"]) if pareto["top20_share"] is not None else "n/d",
        "negative_net_display": intel.money(summary["negative_net"]),
        "low_net_display": intel.money(summary["low_net"]),
        "rfm_chart": {"labels": [row["label"] for row in summary["rfm"]], "values": [row["count"] for row in summary["rfm"]]},
        "pareto_chart": {
            "labels": [f"{point['customers_pct']:.0f}%".replace(".", ",") for point in pareto["curve"]],
            "values": [point["profit_pct"] for point in pareto["curve"]],
        },
    }


def _receita_status(cnpjs):
    """Situação na Receita já guardada da Econodata (sem chamar a API)."""
    records = EconodataEmpresa.objects.filter(cnpj__in=[cnpj for cnpj in cnpjs if cnpj])
    return {
        record.cnpj: {"situacao": record.situacao, "alerta": record.situacao in econodata.INACTIVE_SITUATIONS, "found": record.encontrado}
        for record in records
    }


def _econodata_context(request):
    is_admin = _is_system_admin(request.user)
    return {
        "econodata_can_enrich": is_admin,
        "econodata_configured": econodata.is_configured(),
        "econodata_enrich_url": reverse("dashesCustomerDnaEconodata"),
        "econodata_tokens_left": commercial_dna.int_display(econodata.tokens_left_this_month()) if is_admin else None,
    }


def _page_context(request, active, title):
    return {
        **_maxisys_shell(request, active, title),
        **_ai_context(request),
        "is_admin": _is_system_admin(request.user),
        "data_error": None,
        "profile_url_template": reverse("dashesCustomerProfilePage", args=[0]).replace("/0/", "/__code__/"),
        "refresh_url": reverse("dashesCustomerIntelRefresh"),
    }


def _window_context(base, window):
    return {
        "window": window,
        "period_choices": window["choices"],
        "loaded_at": timezone.localtime(base["loaded_at"]),
    }


# ---------------------------------------------------------------------------
# Mapa de clientes
# ---------------------------------------------------------------------------

def _market_filters(state, cnaes, prospect=False):
    filtros = {"uf": [state], "situacao": ["ATIVA"], "cnaePrimario": list(cnaes)}
    if prospect:
        filtros["nivelAtividade"] = ["ALTA", "MEDIA"]
    return filtros


def _sector_records(rows):
    """Clientes já enriquecidos (Econodata) por segmento: dão os CNAEs reais do ramo."""
    cnpjs = [item["cnpj"] for item in rows if item["cnpj"]]
    cached = {record.cnpj: record for record in EconodataEmpresa.objects.filter(cnpj__in=cnpjs, encontrado=True).exclude(cnae_codigo="")}
    records = defaultdict(list)
    for item in rows:
        if item.get("segment") and item["cnpj"] in cached:
            records[item["segment"]["code"]].append(cached[item["cnpj"]])
    return records


def _prospect_json(prospect):
    return {
        "name": prospect.nome_fantasia or prospect.razao_social,
        "legal_name": prospect.razao_social,
        "city": prospect.cidade.title(),
        "cnae": prospect.cnae_descricao,
        "size": prospect.enquadramento_porte,
        "activity": prospect.nivel_atividade,
        "segment": prospect.segmento_nome.title(),
        "customer": prospect.ja_cliente,
        "cnpj": econodata.format_cnpj(prospect.cnpj),
    }


def _potential(rows, all_rows):
    """Por UF, os 5 segmentos em que mais vendemos: nossos clientes contra o mercado (Econodata).

    Os CNAEs de cada segmento saem da base inteira (`all_rows`), não do filtro da
    tela: assim a contagem guardada vale para qualquer filtro.
    """
    by_state = defaultdict(dict)
    for item in rows:
        segment = item.get("segment")
        if item["net"] <= 0 or item["state"] not in intel.STATES or not segment or segment["code"] == intel.OTHER_SEGMENT:
            continue
        entry = by_state[item["state"]].setdefault(segment["code"], {"name": segment["name"].title(), "customers": 0, "net": Decimal("0")})
        entry["customers"] += 1
        entry["net"] += item["net"]
    records = _sector_records(all_rows)
    plan = []
    for state, segments in by_state.items():
        for code, entry in sorted(segments.items(), key=lambda pair: pair[1]["net"], reverse=True)[:5]:
            cnaes = intel.sector_cnaes(code, records.get(code, []))
            plan.append((state, code, entry, cnaes, econodata._filters_key(_market_filters(state, cnaes)) if cnaes else None))
    since = timezone.now() - timedelta(days=econodata.cache_days())
    counts = {
        record.chave: record.total
        for record in EconodataContagem.objects.filter(chave__in=[key for *_rest, key in plan if key], consultado_em__gte=since)
    }
    prospects = defaultdict(list)
    for prospect in EconodataProspect.objects.exclude(status="descartado").order_by("-criado_em")[:600]:
        prospects[prospect.uf].append(prospect)
    result = defaultdict(lambda: {"segments": [], "prospects": []})
    for state, code, entry, cnaes, key in plan:
        market = counts.get(key) if key else None
        result[state]["segments"].append({
            "segment": code,
            "name": entry["name"],
            "customers": entry["customers"],
            "net_display": intel.money(entry["net"]),
            "cnaes": cnaes,
            "market": market,
            "penetration_display": intel.pct(Decimal(entry["customers"]) / Decimal(market)) if market else None,
            "prospects": sum(1 for prospect in prospects.get(state, []) if prospect.segmento_codigo == code),
        })
    for state, items in prospects.items():
        result[state]["prospects"] = [_prospect_json(prospect) for prospect in items[:30]]
    return dict(result)


def _map_json(geo):
    states = {}
    for sigla, area in geo["states"].items():
        if sigla in intel.STATES:
            code, name, region = intel.STATES[sigla]
            states[sigla] = {**intel.area_json(area, name), "sigla": sigla, "ibge": code, "region": region}
    cities = {key: {**intel.area_json(area), "state": area["state"], "ibge": area["ibge"]} for key, area in geo["cities"].items()}
    foreign = intel.area_json(geo["states"][intel.FOREIGN], "Exterior") if intel.FOREIGN in geo["states"] else None
    return {"states": states, "cities": cities, "foreign": foreign}


def _regions_display(geo):
    regions = []
    for key in intel.REGION_ORDER:
        area = geo["regions"].get(key)
        if not area:
            continue
        regions.append({
            "key": key,
            "name": intel.REGIONS[key],
            "customers": area["customers"],
            "net_display": intel.money(area["net"]),
            "profit_display": intel.money(area["profit"]),
            "margin_display": intel.pct(area["margin"]),
            "share": intel._f((area["share"] or 0) * 100, 1),
            "share_display": intel.pct(area["share"]),
            "growth_display": intel.pct(area["growth"], signed=True) if area["growth"] is not None else "–",
            "growth_tone": "is-up" if area["growth"] and area["growth"] > 0 else "is-down" if area["growth"] and area["growth"] < 0 else "",
        })
    return regions


def _lost_cities(geo, limit=8):
    lost = [area for area in geo["cities"].values() if area["customers"] == 0 and area["previous_net"] > 0]
    lost.sort(key=lambda area: area["previous_net"], reverse=True)
    return {
        "count": len(lost),
        "previous_display": intel.money(sum((area["previous_net"] for area in lost), Decimal("0"))),
        "items": [{"name": area["name"], "state": area["state"], "previous_display": intel.money(area["previous_net"])} for area in lost[:limit]],
    }


def _map_view_data(period_key, filters):
    base, window, metrics = _prepare(period_key)
    rows = intel.filter_rows(list(metrics.values()), rep=filters["rep"], segment=filters["segment"])
    ranked = intel.rank_by_profit([item for item in rows if item["net"] > 0])
    geo = intel.geography(rows)
    summary = intel.portfolio_summary(rows)
    geo["ranked"] = ranked
    return base, window, metrics, rows, geo, summary


def _query(filters, **extra):
    """Querystring dos filtros da tela (para os links de período e de indicador)."""
    params = {"uf": _csv(filters.get("state")), "rep": _csv(filters.get("rep")), "segmento": _csv(filters.get("segment")),
              "rfm": _csv(filters.get("rfm")), **extra}
    return "&".join(f"{key}={value}" for key, value in params.items() if value not in (None, ""))


@_dashes_access_required("customer-map")
def dashesCustomerMapPage(request):
    filters = _filters(request.GET)
    period_key = (request.GET.get("periodo") or intel.DEFAULT_PERIOD).strip()
    metric = request.GET.get("indicador") if request.GET.get("indicador") in dict(MAP_METRICS) else "net"
    context = {
        **_page_context(request, "customer-map", "Mapa de Clientes"),
        "page_url": reverse("dashesCustomerMapPage"),
        "filters": filters,
        "metric": metric,
        "metric_choices": MAP_METRICS,
    }
    try:
        base, window, metrics, rows, geo, summary = _map_view_data(period_key, filters)
    except Exception as exc:
        context["data_error"] = f"Não foi possível consultar o ERP Senior: {exc}"
        return render(request, "tiqueue/maxisys/customer_map.html", context)
    segment_choices = _segment_choices(metrics)
    key = _insight_key("map", window["key"], filters["rep"], filters["segment"])
    context.update({
        **_window_context(base, window),
        "summary": _summary_display(summary),
        "regions": _regions_display(geo),
        "lost": _lost_cities(geo),
        "map_data": _map_json(geo),
        "potential": _potential(rows, list(metrics.values())),
        "rep_choices": _rep_choices(metrics),
        "segment_choices": segment_choices,
        "filter_labels": _filter_labels(filters, base, segment_choices),
        "cities_count": sum(1 for area in geo["cities"].values() if area["customers"]),
        "states_count": sum(1 for sigla, area in geo["states"].items() if sigla in intel.STATES and area["customers"]),
        "top_customers": [
            {"code": item["code"], "name": item["name"], "city": f"{item['city']}/{item['state']}", "net": intel._f(item["net"]),
             "profit": intel._f(item["profit"]), "margin": intel._f(item["margin"], 4)}
            for item in geo["ranked"][:10]
        ],
        "margin_reference": intel._f(summary["margin"], 4),
        "filter_query": _query({"rep": filters["rep"], "segment": filters["segment"]}),
        "insight_fields": {"periodo": window["key"], "rep": _csv(filters["rep"]), "segmento": _csv(filters["segment"])},
        "map_config": {
            "metric": metric,
            "margin_reference": intel._f(summary["margin"], 4),
            "initial_state": filters["state"][0] if len(filters["state"]) == 1 and filters["state"][0] in intel.STATES else None,
            "geo_url_template": reverse("dashesCustomerMapGeo", args=["BR"]).replace("BR.json", "__key__.json"),
            "profile_url_template": reverse("dashesCustomerProfilePage", args=[0]).replace("/0/", "/__code__/"),
            "state_names": {str(code): name for _sigla, (code, name, _region) in intel.STATES.items()},
            "is_admin": context["is_admin"],
            "econodata_configured": econodata.is_configured(),
            "count_tokens": econodata.COUNT_APPROX_TOKENS,
            "prospect_tokens": econodata.PROSPECT_APPROX_TOKENS,
            "prospect_size": econodata.PROSPECT_PAGE_SIZE,
            "tokens_left": econodata.tokens_left_this_month() if context["is_admin"] else None,
        },
        "insight": _latest_insight("map", key),
        "insight_url": reverse("dashesCustomerMapInsight"),
        "econodata_url": reverse("dashesCustomerMapEconodata"),
        "econodata_configured": econodata.is_configured(),
        "econodata_tokens_left": commercial_dna.int_display(econodata.tokens_left_this_month()) if context["is_admin"] else None,
        "geo_url_template": reverse("dashesCustomerMapGeo", args=["BR"]).replace("BR.json", "__key__.json"),
        "portfolio_url": reverse("dashesCustomerPortfolioPage"),
    })
    return render(request, "tiqueue/maxisys/customer_map.html", context)


@_dashes_access_required("customer-map")
def dashesCustomerMapGeo(request, key):
    key = str(key).upper()
    if key != "BR" and not (key.isdigit() and int(key) in intel.STATE_BY_IBGE):
        return JsonResponse({"error": "Malha inválida."}, status=404)
    try:
        data = ibge.mesh(key)
    except ibge.IbgeError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    response = JsonResponse(data)
    response["Cache-Control"] = "private, max-age=86400"
    return response


@_dashes_access_required("customer-map")
def dashesCustomerMapInsight(request):
    back = _back(request, "dashesCustomerMapPage")
    if request.method != "POST":
        return redirect(back)
    filters = _filters(request.POST)
    try:
        base, window, metrics, rows, geo, summary = _map_view_data(request.POST.get("periodo"), filters)
    except Exception as exc:
        messages.error(request, f"Não foi possível consultar o ERP Senior: {exc}")
        return redirect(back)
    labels = _filter_labels(filters, base, _segment_choices(metrics))
    data = intel.map_ai_data(geo, summary, window, labels)
    key = _insight_key("map", window["key"], filters["rep"], filters["segment"])
    return _run_insight(request, "map", key, window["key"], data, back)


@_dashes_access_required("customer-map")
def dashesCustomerMapEconodata(request):
    """Potencial do território (contagem) e prospecção (lista) na Econodata. Só administradores."""
    back = _back(request, "dashesCustomerMapPage")
    if request.method != "POST":
        return redirect(back)
    if not _is_system_admin(request.user):
        messages.error(request, "Só administradores podem consultar a Econodata, porque a consulta gasta tokens.")
        return redirect(back)
    action = request.POST.get("acao")
    state = (request.POST.get("uf") or "").strip().upper()
    sector = (request.POST.get("segmento") or "").strip()
    if action not in ("contar", "prospectar") or state not in intel.STATES or not sector:
        messages.error(request, "Consulta à Econodata inválida: escolha a UF e o segmento de novo.")
        return redirect(back)
    try:
        base, window, metrics = _prepare(intel.DEFAULT_PERIOD)
    except Exception as exc:
        messages.error(request, f"Não foi possível consultar o ERP Senior: {exc}")
        return redirect(back)
    rows = list(metrics.values())
    names = {item["segment"]["code"]: item["segment"]["name"].title() for item in rows if item.get("segment")}
    cnaes = intel.sector_cnaes(sector, _sector_records(rows).get(sector, []))
    if not cnaes:
        messages.error(request, "Este segmento ainda não tem CNAE de referência para a Econodata.")
        return redirect(back)
    name = names.get(sector, sector)
    try:
        if action == "contar":
            result = econodata.count_companies(_market_filters(state, cnaes), confirm=True, user=request.user, origem="mapa:potencial")
            ours = sum(1 for item in rows if item["net"] > 0 and item["state"] == state and item.get("segment") and item["segment"]["code"] == sector)
            share = intel.pct(Decimal(ours) / Decimal(result["total"])) if result["total"] else "–"
            messages.success(
                request,
                f"Econodata: {commercial_dna.int_display(result['total'])} empresas ativas de {name} em {state} "
                f"(CNAE {', '.join(cnaes)}). Temos {ours} cliente(s): penetração de {share}. "
                f"{result['charged_tokens']} tokens cobrados.",
            )
        else:
            filtros = _market_filters(state, cnaes, prospect=True)
            result = econodata.search_companies(filtros, confirm=True, user=request.user, origem="mapa:prospeccao")
            known_roots = {item["cnpj"][:8] for item in rows if item["cnpj"]}
            saved = econodata.store_prospects(result["companies"], sector_code=sector, sector_name=name, filtros=filtros, known_roots=known_roots)
            new = [prospect for prospect in saved if not prospect.ja_cliente]
            messages.success(
                request,
                f"Econodata: {len(saved)} empresa(s) de {name} em {state} "
                f"(de {commercial_dna.int_display(result['total'] or 0)} no filtro); {len(new)} ainda não são clientes. "
                f"{result['charged_tokens']} tokens cobrados.",
            )
    except econodata.EconodataError as exc:
        messages.error(request, f"Econodata: {exc}")
    return redirect(back)


# ---------------------------------------------------------------------------
# Carteira de clientes
# ---------------------------------------------------------------------------

def _portfolio_view_data(period_key, filters, view):
    base, window, metrics = _prepare(period_key)
    rows_all = intel.filter_rows(list(metrics.values()), state=filters["state"], rep=filters["rep"], segment=filters["segment"])
    # O filtro de RFM vale para a página toda, menos a distribuição dos segmentos: ela segue
    # mostrando todos, para dar para trocar ou somar outro segmento (Ctrl + clique).
    rows = intel.filter_rfm(rows_all, filters.get("rfm"))
    ranked = intel.rank_by_profit([item for item in rows if item["net"] > 0])
    summary = intel.portfolio_summary(rows)
    overall = intel.portfolio_summary(rows_all) if filters.get("rfm") else summary
    summary["rfm"] = overall["rfm"]
    summary["rfm_customers"] = overall["customers"]
    at_risk = [
        item for item in ranked
        if item["rhythm"]["status"] in ("late", "at_risk") or (item.get("rfm") and item["rfm"]["key"] in ("at_risk", "cant_lose"))
    ]
    if view == "risco":
        table = at_risk
    elif view == "reativacao":
        table = intel.reactivation_rows({item["code"]: item for item in rows})
    else:
        table = ranked
    return base, window, metrics, rows, ranked, at_risk, summary, table


@_dashes_access_required("customer-portfolio")
def dashesCustomerPortfolioPage(request):
    filters = _filters(request.GET)
    period_key = (request.GET.get("periodo") or intel.DEFAULT_PERIOD).strip()
    view = request.GET.get("visao") if request.GET.get("visao") in dict(PORTFOLIO_VIEWS) else "lucrativos"
    context = {
        **_page_context(request, "customer-portfolio", "Carteira de Clientes"),
        "page_url": reverse("dashesCustomerPortfolioPage"),
        "filters": filters,
        "view": view,
        "view_choices": PORTFOLIO_VIEWS,
    }
    try:
        base, window, metrics, rows, ranked, at_risk, summary, table = _portfolio_view_data(period_key, filters, view)
    except Exception as exc:
        context["data_error"] = f"Não foi possível consultar o ERP Senior: {exc}"
        return render(request, "tiqueue/maxisys/customer_portfolio.html", context)
    table_total = len(table)
    table = table[:TABLE_LIMIT]
    receita = _receita_status([item["cnpj"] for item in table])
    table_rows = [{**intel.row_display(item), "receita": receita.get(item["cnpj"])} for item in table]
    segment_choices = _segment_choices(metrics)
    guessed = set(CustomerSegmentGuess.objects.values_list("customer_code", flat=True))
    unclassified = [
        item for item in metrics.values()
        if not item["sector_code"] and item["code"] not in guessed and item["history_net"] > 0 and not item["person"]
    ]
    inactive = intel.reactivation_rows(metrics)
    top_inactive = inactive[:REACTIVATION_CHECK_LIMIT]
    known_status = _receita_status([row["cnpj"] for row in top_inactive])
    pending_status = [row for row in top_inactive if row["cnpj"] and row["cnpj"] not in known_status]
    key = _insight_key("portfolio", window["key"], view, filters["state"], filters["rep"], filters["segment"], filters["rfm"])
    chosen_rfm = set(filters["rfm"])
    context.update({
        "rfm_buttons": [{**row, "active": row["key"] in chosen_rfm} for row in summary["rfm"] if row["count"] or row["key"] in chosen_rfm],
        "rfm_clear_url": page_filters.url_without(reverse("dashesCustomerPortfolioPage"), request.GET, "rfm"),
        **_window_context(base, window),
        "summary": _summary_display(summary),
        "table_rows": table_rows,
        "table_total": table_total,
        "table_limited": table_total > TABLE_LIMIT,
        "rep_choices": _rep_choices(metrics),
        "segment_choices": segment_choices,
        "state_choices": sorted(
            ({"code": sigla, "name": name} for sigla, (_code, name, _region) in intel.STATES.items()),
            key=lambda choice: choice["name"],
        ),
        "filter_labels": _filter_labels(filters, base, segment_choices),
        "at_risk_count": len(at_risk),
        "reactivation_count": len(inactive),
        "unclassified_count": len(unclassified),
        "classify_batch": min(CLASSIFY_LIMIT, len(unclassified)),
        "pending_status_count": len(pending_status),
        "pending_status_tokens": commercial_dna.int_display(econodata.approx_tokens(econodata.PROFILES["basico"]["campos"], len(pending_status))),
        "econodata_configured": econodata.is_configured(),
        "econodata_tokens_left": commercial_dna.int_display(econodata.tokens_left_this_month()) if context["is_admin"] else None,
        "filter_query": _query(filters),
        "insight_fields": {"periodo": window["key"], "visao": view, "uf": _csv(filters["state"]), "rep": _csv(filters["rep"]), "segmento": _csv(filters["segment"]), "rfm": _csv(filters["rfm"])},
        "insight": _latest_insight("portfolio", key),
        "insight_url": reverse("dashesCustomerPortfolioInsight"),
        "classify_url": reverse("dashesCustomerSegmentsClassify"),
        "reactivation_url": reverse("dashesCustomerReactivationCheck"),
        "map_url": reverse("dashesCustomerMapPage"),
    })
    return render(request, "tiqueue/maxisys/customer_portfolio.html", context)


@_dashes_access_required("customer-portfolio")
def dashesCustomerPortfolioInsight(request):
    back = _back(request, "dashesCustomerPortfolioPage")
    if request.method != "POST":
        return redirect(back)
    filters = _filters(request.POST)
    view = request.POST.get("visao") if request.POST.get("visao") in dict(PORTFOLIO_VIEWS) else "lucrativos"
    try:
        base, window, metrics, rows, ranked, at_risk, summary, _table = _portfolio_view_data(request.POST.get("periodo"), filters, view)
    except Exception as exc:
        messages.error(request, f"Não foi possível consultar o ERP Senior: {exc}")
        return redirect(back)
    labels = _filter_labels(filters, base, _segment_choices(metrics))
    data = intel.portfolio_ai_data(summary, ranked, at_risk, window, labels)
    key = _insight_key("portfolio", window["key"], view, filters["state"], filters["rep"], filters["segment"], filters["rfm"])
    return _run_insight(request, "portfolio", key, window["key"], data, back)


@_dashes_access_required("customer-portfolio")
def dashesCustomerSegmentsClassify(request):
    """A IA sugere o ramo de quem está sem ramo no ERP (lista fechada: os ramos do ERP)."""
    back = _back(request, "dashesCustomerPortfolioPage")
    if request.method != "POST":
        return redirect(back)
    if not _is_system_admin(request.user):
        messages.error(request, "Só administradores podem classificar segmentos com a IA.")
        return redirect(back)
    try:
        base, window, metrics = _prepare(intel.DEFAULT_PERIOD)
    except Exception as exc:
        messages.error(request, f"Não foi possível consultar o ERP Senior: {exc}")
        return redirect(back)
    guessed = set(CustomerSegmentGuess.objects.values_list("customer_code", flat=True))
    candidates = [
        item for item in metrics.values()
        if not item["sector_code"] and item["code"] not in guessed and item["history_net"] > 0 and not item["person"]
    ]
    candidates.sort(key=lambda item: item["history_net"], reverse=True)
    batch = candidates[:CLASSIFY_LIMIT]
    if not batch:
        messages.info(request, "Todos os clientes com compra já têm segmento (ERP ou IA).")
        return redirect(back)
    cnaes = {
        record.cnpj: f"{record.cnae_codigo} {record.cnae_descricao}".strip()
        for record in EconodataEmpresa.objects.filter(cnpj__in=[item["cnpj"] for item in batch if item["cnpj"]], encontrado=True)
    }
    customers = [
        {
            "code": item["code"], "name": item["name"], "trade_name": item["trade_name"], "city": item["city"], "state": item["state"],
            "groups": [group.title() for group, _values in sorted((base["mix"].get(item["code"]) or {}).items(), key=lambda pair: pair[1]["net"], reverse=True)[:3]],
            "cnae": cnaes.get(item["cnpj"], ""),
        }
        for item in batch
    ]
    blocked = _ai_blocked(request)
    if blocked:
        messages.error(request, blocked)
        return redirect(back)
    quota_record = _consume_dashes_ai_quota(request.user)
    try:
        result = intel_ai.classify_sectors(customers, base["sectors"])
    except Exception as exc:
        _refund_quota(quota_record)
        messages.error(request, f"IA: {exc}")
        return redirect(back)
    names = {item["code"]: item["name"] for item in batch}
    for row in result["results"]:
        CustomerSegmentGuess.objects.update_or_create(
            customer_code=row["code"],
            defaults={
                "customer_name": names.get(row["code"], "")[:240],
                "sector_code": row["sector_code"],
                "sector_name": row["sector_name"],
                "confidence": row["confidence"],
                "reason": row["reason"],
                "source": "ia",
                "ai_model": (result.get("model") or "")[:80],
                "created_by": request.user,
            },
        )
    high = sum(1 for row in result["results"] if row["confidence"] == "alta")
    others = sum(1 for row in result["results"] if not row["sector_code"])
    messages.success(
        request,
        f"IA classificou {len(result['results'])} de {len(batch)} cliente(s): {high} com confiança alta e {others} em OUTROS. "
        f"{result['usage'].get('total_tokens', 0)} tokens da OpenAI. Faltam {max(len(candidates) - len(batch), 0)} sem segmento.",
    )
    return redirect(back)


@_dashes_access_required("customer-portfolio")
def dashesCustomerReactivationCheck(request):
    """Situação na Receita (Econodata, perfil básico) dos inativos de maior lucro. Só administradores."""
    back = _back(request, "dashesCustomerPortfolioPage")
    if request.method != "POST":
        return redirect(back)
    if not _is_system_admin(request.user):
        messages.error(request, "Só administradores podem consultar a Econodata, porque a consulta gasta tokens.")
        return redirect(back)
    try:
        base, window, metrics = _prepare(intel.DEFAULT_PERIOD)
    except Exception as exc:
        messages.error(request, f"Não foi possível consultar o ERP Senior: {exc}")
        return redirect(back)
    cnpjs = [item["cnpj"] for item in intel.reactivation_rows(metrics)[:REACTIVATION_CHECK_LIMIT] if item["cnpj"]]
    if not cnpjs:
        messages.info(request, "Nenhum inativo com CNPJ para conferir.")
        return redirect(back)
    try:
        result = econodata.enrich(cnpjs, "basico", confirm=True, user=request.user, origem="carteira:reativacao")
    except econodata.EconodataError as exc:
        messages.error(request, f"Econodata: {exc}")
        return redirect(back)
    irregular = sum(1 for status in _receita_status(cnpjs).values() if status["alerta"])
    if not result["to_fetch"]:
        messages.info(request, "Econodata: a situação desses inativos já estava guardada. Nada foi cobrado.")
    else:
        messages.success(
            request,
            f"Econodata: {result['found']} inativo(s) conferido(s), {result['not_found']} não encontrado(s); "
            f"{irregular} com situação irregular na Receita (saem da lista de reativação). {result['charged_tokens']} tokens cobrados.",
        )
    return redirect(back)


@_dashes_access_required("customer-portfolio")
def dashesCustomerIntelRefresh(request):
    back = _back(request, "dashesCustomerPortfolioPage")
    if request.method == "POST" and _is_system_admin(request.user):
        from . import management_intel

        from . import panels

        intel.clear_cache()
        management_intel.clear_cache()
        panels.clear_cache()
        messages.info(request, "Os números serão relidos do ERP agora.")
    return redirect(back)


# ---------------------------------------------------------------------------
# Ficha do cliente
# ---------------------------------------------------------------------------

def _profile_data(customer_code, period_key):
    base, window, metrics = _prepare(period_key)
    item = metrics.get(customer_code)
    if item is None:
        return base, window, metrics, None, {}
    intel.rank_by_profit([row for row in metrics.values() if row["net"] > 0])
    benchmarks = intel.segment_benchmarks(metrics)
    benchmark = benchmarks.get(item["segment"]["code"]) if item.get("segment") else None
    model = intel.cross_sell_model(base, metrics)
    cross = intel.cross_sell_for(item, base, model)
    series = intel.monthly_series(base, customer_code, base["today"])
    mix = intel.mix_rows(base, customer_code)
    annual = item["net"] * 12 / window["months"] if window["months"] else item["net"]
    market = commercial_dna.market_display(econodata.market_view([item["cnpj"]] if item["cnpj"] else [], annual, base["today"]))
    return base, window, metrics, item, {"benchmark": benchmark, "cross": cross, "series": series, "mix": mix, "market": market}


def _comparison(value, reference, kind):
    """Texto de comparação com a mediana do segmento."""
    if value is None or reference is None:
        return None
    if kind == "margin":
        delta = (value - reference) * 100
        text = f"{abs(delta):.1f} p.p. {'acima' if delta >= 0 else 'abaixo'} da mediana do segmento".replace(".", ",", 1)
        return {"text": text, "tone": "is-up" if delta >= 0 else "is-down"}
    if not reference:
        return None
    delta = (value - reference) / reference
    text = f"{abs(delta) * 100:.0f}% {'acima' if delta >= 0 else 'abaixo'} da mediana do segmento"
    return {"text": text, "tone": "is-up" if delta >= 0 else "is-down"}


@_dashes_access_required("customer-portfolio")
def dashesCustomerProfilePage(request, customer_code):
    period_key = (request.GET.get("periodo") or intel.DEFAULT_PERIOD).strip()
    context = {
        **_page_context(request, "customer-portfolio", "Ficha do Cliente"),
        **_econodata_context(request),
        "page_url": reverse("dashesCustomerProfilePage", args=[customer_code]),
        "customer_code": customer_code,
        "portfolio_url": reverse("dashesCustomerPortfolioPage"),
        "map_url": reverse("dashesCustomerMapPage"),
    }
    try:
        base, window, metrics, item, extra = _profile_data(customer_code, period_key)
    except Exception as exc:
        context["data_error"] = f"Não foi possível consultar o ERP Senior: {exc}"
        return render(request, "tiqueue/maxisys/customer_profile.html", context)
    context.update(_window_context(base, window))
    if item is None:
        context["data_error"] = f"O cliente {customer_code} não tem faturamento nos últimos 24 meses."
        return render(request, "tiqueue/maxisys/customer_profile.html", context, status=404)
    benchmark = extra["benchmark"]
    display = intel.row_display(item)
    active_count = sum(1 for row in metrics.values() if row["net"] > 0)
    key = _insight_key("customer", customer_code, window["key"])
    context.update({
        "customer": display,
        "item": item,
        "active_count": active_count,
        "benchmark": benchmark,
        "benchmark_margin_display": intel.pct(benchmark["margin"]) if benchmark else None,
        "benchmark_fator_display": intel.money(benchmark["fator"]) if benchmark and benchmark["fator"] is not None else None,
        "margin_vs_segment": _comparison(item["margin"], benchmark["margin"] if benchmark else None, "margin"),
        "fator_vs_segment": _comparison(item["fator"], benchmark["fator"] if benchmark else None, "fator"),
        "cross": extra["cross"],
        "series": extra["series"],
        "mix": extra["mix"],
        "m": extra["market"],
        "customer_id": customer_code,
        "dna_view_mode": "individual",
        "dna_member_customer_id": None,
        "first_purchase_display": commercial_dna._display_date(item["first_purchase"]) if item["first_purchase"] else "-",
        "insight": _latest_insight("customer", key),
        "insight_fields": {"periodo": window["key"]},
        "insight_url": reverse("dashesCustomerProfileInsight", args=[customer_code]),
        "dna_url": f"{reverse('dashesCustomerDnaV2Page')}?cliente={customer_code}",
        "shell_can_dna_link": "customer-dna" in {dash.slug for dash in context["dashes_menu"]},
    })
    return render(request, "tiqueue/maxisys/customer_profile.html", context)


@_dashes_access_required("customer-portfolio")
def dashesCustomerProfileInsight(request, customer_code):
    back = _back(request, "dashesCustomerProfilePage", customer_code)
    if request.method != "POST":
        return redirect(back)
    try:
        base, window, metrics, item, extra = _profile_data(customer_code, request.POST.get("periodo"))
    except Exception as exc:
        messages.error(request, f"Não foi possível consultar o ERP Senior: {exc}")
        return redirect(back)
    if item is None:
        messages.error(request, "Cliente sem faturamento nos últimos 24 meses.")
        return redirect(back)
    data = intel.customer_ai_data(item, window, extra["benchmark"], extra["cross"], extra["mix"], extra["series"], extra["market"])
    key = _insight_key("customer", customer_code, window["key"])
    return _run_insight(request, "customer", key, window["key"], data, back)


# ---------------------------------------------------------------------------
# IA: cota diária, chamada e registro
# ---------------------------------------------------------------------------

def _ai_blocked(request):
    quota = _dashes_ai_quota(request.user)
    if quota["blocked"]:
        return DASHES_AI_LIMIT_MESSAGE
    config = get_openai_runtime_config()
    if not config["enabled"] or not config["api_key_configured"]:
        return "Configure e ative a OpenAI em Sistema → Configurações."
    return None


def _refund_quota(quota_record):
    """Uma chamada que falhou não entregou análise: devolve a cota do dia."""
    DashesAiUsage.objects.filter(pk=quota_record.pk, request_count__gt=0).update(request_count=models.F("request_count") - 1)


def _run_insight(request, scope, key, period_key, data, back):
    blocked = _ai_blocked(request)
    if blocked:
        messages.error(request, blocked)
        return redirect(back)
    quota_record = _consume_dashes_ai_quota(request.user)
    extra = ai_additional.clean(request.POST.get("adicional"))
    try:
        result = intel_ai.generate_insight(scope, data, extra=extra)
    except Exception as exc:
        _refund_quota(quota_record)
        CustomerIntelInsight.objects.create(
            scope=scope, scope_key=key, period_key=period_key or "", status=CustomerIntelInsight.STATUS_ERROR,
            payload={**data, ai_additional.INPUT_KEY: extra} if extra else data, error=str(exc)[:2000], requested_by=request.user,
        )
        messages.error(request, f"IA: {exc}")
        return redirect(back)
    usage = result["usage"]
    CustomerIntelInsight.objects.create(
        scope=scope, scope_key=key, period_key=period_key or "", status=CustomerIntelInsight.STATUS_COMPLETED,
        payload={**data, ai_additional.INPUT_KEY: extra} if extra else data, response=result["response"], ai_model=(result.get("model") or "")[:80],
        input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0), requested_by=request.user,
    )
    messages.success(request, "Leitura da IA pronta.")
    return redirect(back)
