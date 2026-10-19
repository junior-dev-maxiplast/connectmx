"""
Central de Decisão — visão unificada dos painéis do ConnectMX Dashes.

Este módulo não substitui nenhum painel: ele só lê o que os módulos de BI já
calculam (`romaneio_bi.py`, `travel_bi.py`, `it_bi.py`) e o que a análise de
IA de cada um já salvou em banco (`*InsightSnapshot`). Não chama a OpenAI em
nenhum momento — os botões "Gerar/Atualizar indicadores" e "Pedir/Atualizar
análise à IA" de cada painel continuam sendo o único jeito de gastar cota de
IA. Isso é deliberado: a Central roda a cada carregamento de página (ver
`refresh_alerts_for_user_dashboards`), e se ela chamasse IA sozinha um usuário
poderia estourar a cota diária sem nunca ter clicado em nada.

Comparação usada pelos alertas: `period_key="1"` (últimos 30 dias corridos)
contra `period_key="3"` (últimos 3 meses corridos, normalizado para "média por
mês" dividindo por 3). Não é mês-fechado-contra-mês-fechado — isso exigiria
mudar a assinatura pública dos três módulos de BI, o que este módulo
deliberadamente evita. É uma heurística de tendência, no mesmo espírito
conservador que `romaneio_bi.py` já assume para outros limites: revisar
quando o painel rodar contra um histórico maior.

O sparkline de cada cartão soma uma terceira busca por painel com recorte
(`period_key="12"`, ver `_sparkline_points`) - a Central chega a ~9 consultas
ao Oracle/MySQL por carregamento no pior caso (3 por painel × 3 painéis). É o
mesmo padrão "sem cache" que os 4 painéis já têm sozinhos; aceitável para o
volume de uso interno do Dashes, registrado aqui como o próximo gargalo caso
o uso cresça.
"""

from django.utils import timezone

from .models import (
    CustomerInsightSnapshot,
    DashAlert,
    DashDecisionAction,
    ItBiInsightSnapshot,
    RomaneioBiInsightSnapshot,
    TravelBiInsightSnapshot,
)

CURRENT_PERIOD_KEY = "1"
BASELINE_PERIOD_KEY = "3"
BASELINE_MONTHS = 3
SPARKLINE_PERIOD_KEY = "12"

# Painéis com IA por recorte (período/filial/etc.) — DNA de Cliente fica de
# fora porque a análise ali é por cliente, não por período; ver
# `_dna_customer_summary`.
SCOPED_DASHBOARD_SLUGS = ("romaneios", "viagens", "ti-bi")


# --------------------------------------------------------- carregadores ----

def _load_dashboard(slug, period_key):
    """Carrega o painel de recorte pedido pelo slug.

    Import tardio (dentro da função, não no topo do módulo): `romaneio_bi.py`,
    `travel_bi.py` e `it_bi.py` importam de `customer_dna.py`, que por sua vez
    não importa este módulo — não há ciclo hoje, mas isolar aqui evita criar
    um se a Central crescer e algum desses módulos precisar dela no futuro.
    É também o que faz mockar `tiqueue.romaneio_bi.load_romaneio_dashboard`
    (e equivalentes) funcionar nos testes: guardar a função num dict no
    carregamento do módulo travaria a referência original antes do patch.
    """
    if slug == "romaneios":
        from .romaneio_bi import load_romaneio_dashboard
        return load_romaneio_dashboard(period_key=period_key)
    if slug == "viagens":
        from .travel_bi import load_travel_dashboard
        return load_travel_dashboard(period_key=period_key)
    if slug == "ti-bi":
        from .it_bi import load_it_dashboard
        return load_it_dashboard(period_key=period_key)
    raise KeyError(f"Painel sem carregador na Central de Decisão: {slug}")


_SNAPSHOT_MODELS = {
    "romaneios": RomaneioBiInsightSnapshot,
    "viagens": TravelBiInsightSnapshot,
    "ti-bi": ItBiInsightSnapshot,
}

# KPIs mostrados no cartão de cada painel na Central — só leitura de campos
# que `load_X_dashboard()["metrics"]` já devolve, nenhum cálculo novo.
_CARD_KPIS = {
    "romaneios": [("records_display", "Registros"), ("weight_display", "Peso total")],
    "viagens": [("trips_display", "Viagens"), ("open_display", "Em aberto")],
    "ti-bi": [("tickets_display", "Chamados"), ("backlog_display", "Backlog")],
}

# Cada painel guarda a série mensal sob uma chave própria (romaneio_bi.py
# chama a dela "series"; travel_bi.py e it_bi.py chamam a delas "monthly" -
# nomes que já existiam antes da Central e não valia a pena unificar só por
# isto). Todas já trazem `label` e `height_pct` (0-100, normalizado pelo
# próprio módulo) - é só isso que o sparkline do cartão precisa.
_SPARKLINE_SERIES_KEY = {
    "romaneios": "series",
    "viagens": "monthly",
    "ti-bi": "monthly",
}


# -------------------------------------------------------------- cartões ----

def _sparkline_points(slug, metrics_cache):
    """Últimos pontos da série mensal de 12 meses, só `label` e `height_pct`
    - o suficiente para desenhar a tendência em miniatura no cartão.

    Busca própria (`period_key="12"`), separada da usada nos KPIs (`"1"`) e
    nos alertas (`"3"`): nenhuma delas tem pontos suficientes para uma
    tendência que valha a pena olhar. Erro aqui não derruba o cartão - ele já
    teria os KPIs mesmo sem o sparkline.
    """
    try:
        dashboard = metrics_cache.get(("sparkline", slug)) or _load_dashboard(slug, SPARKLINE_PERIOD_KEY)
    except Exception:
        return []
    metrics_cache[("sparkline", slug)] = dashboard
    series_key = _SPARKLINE_SERIES_KEY.get(slug)
    items = dashboard.get(series_key) or []
    return [
        {"label": item.get("label"), "height_pct": item.get("height_pct") or 0}
        for item in items[-12:]
    ]


def _scoped_dashboard_card(slug, metrics_cache):
    """KPIs + saúde da última IA de um painel com recorte de período.

    Erro de ERP/MySQL vira um cartão "indisponível agora" em vez de derrubar
    a Central inteira — mesmo espírito do `data_error` que cada painel já usa
    sozinho.
    """
    card = {"slug": slug, "available": False, "kpis": [], "error": None}
    try:
        dashboard = metrics_cache.get(("current", slug)) or _load_dashboard(slug, CURRENT_PERIOD_KEY)
        metrics_cache[("current", slug)] = dashboard
    except Exception as exc:
        card["error"] = str(exc)
        return card

    card["available"] = True
    card["kpis"] = [
        {"label": label, "value": dashboard["metrics"].get(key, "-")}
        for key, label in _CARD_KPIS.get(slug, [])
    ]
    card["sparkline"] = _sparkline_points(slug, metrics_cache)

    snapshot_model = _SNAPSHOT_MODELS[slug]
    snapshot = (
        snapshot_model.objects.filter(status=snapshot_model.STATUS_COMPLETED)
        .order_by("-ai_completed_at")
        .first()
    )
    if snapshot and snapshot.ai_response:
        card["ai_health"] = snapshot.ai_response.get("health")
        card["ai_summary"] = snapshot.ai_response.get("executive_summary")
        card["ai_at"] = snapshot.ai_completed_at
    else:
        card["ai_health"] = None
        card["ai_summary"] = None
        card["ai_at"] = None
    return card


def _dna_customer_card():
    """Cartão do DNA de Cliente: não há visão "todos os clientes" no módulo
    (`load_customer_dna` é por cliente), então a Central resume o que já foi
    analisado — o snapshot mais recente de cada cliente que alguém já rodou.

    Limitado às últimas 500 análises: o objetivo é um retrato do que foi
    olhado recentemente, não uma varredura completa da carteira.
    """
    latest_by_customer = {}
    queryset = (
        CustomerInsightSnapshot.objects.filter(status=CustomerInsightSnapshot.STATUS_COMPLETED)
        .order_by("-created_at")
        .only("customer_code", "customer_name", "ai_response", "created_at")[:500]
    )
    for snap in queryset:
        latest_by_customer.setdefault(snap.customer_code, snap)

    health_counts = {"critico": 0, "atencao": 0, "saudavel": 0}
    for snap in latest_by_customer.values():
        health = (snap.ai_response or {}).get("health")
        if health in health_counts:
            health_counts[health] += 1

    return {
        "slug": "customer-dna",
        "available": True,
        "analyzed_count": len(latest_by_customer),
        "health_counts": health_counts,
    }


def collect_dashboard_summaries(dashboards):
    """Um cartão por painel em `dashboards` (já filtrado por
    `allowed_dashboards(user)` em `views.py` — este módulo não decide quem vê
    o quê)."""
    metrics_cache = {}
    cards = {}
    for dashboard in dashboards:
        if dashboard.slug == "customer-dna":
            cards[dashboard.slug] = _dna_customer_card()
        elif dashboard.slug in SCOPED_DASHBOARD_SLUGS:
            cards[dashboard.slug] = _scoped_dashboard_card(dashboard.slug, metrics_cache)
    return cards, metrics_cache


# --------------------------------------------------------- alertas ---------

def _pct_change(current, baseline):
    if not baseline:
        return None
    return round((current - baseline) / baseline * 100, 1)


# Cada regra descreve uma condição sobre `dashboard["metrics"]`. `kind`
# escolhe a comparação:
#   below_pct_of_baseline / above_pct_of_baseline: `metric` do mês corrente
#     contra `ratio` vezes a média mensal do trimestre.
#   below_absolute / above_absolute: `metric` do mês corrente contra um valor
#     fixo, sem olhar o trimestre (piso/teto de qualidade de cadastro).
#   drop_points_below_baseline: `metric` (um percentual) caiu `points` pontos
#     ou mais frente à média trimestral.
ALERT_RULES = {
    "romaneios": [
        {
            "metric_key": "romaneio_registros_queda",
            "kind": "below_pct_of_baseline",
            "metric": "records",
            "ratio": 0.75,
            "good_direction": "up",
            "severity": DashAlert.SEVERITY_ATENCAO,
            "title": "Produtividade de romaneios em queda",
            "message": "Últimos 30 dias: {current:.0f} registros, {pct:.0f}% abaixo da média "
                       "mensal do trimestre ({baseline:.0f}).",
            "suggested_action": "Investigar a queda de registros de romaneio e falar com as filiais envolvidas.",
        },
        {
            "metric_key": "romaneio_peso_ausente",
            "kind": "above_absolute",
            "metric": "missing_weight_pct",
            "threshold": 20,
            "good_direction": "down",
            "severity": DashAlert.SEVERITY_ATENCAO,
            "title": "Muito romaneio sem peso registrado",
            "message": "{current:.0f}% dos registros dos últimos 30 dias estão sem peso "
                       "(acima do limite de {threshold:.0f}%) — provável falha de cadastro no "
                       "aparelho, não de operação.",
            "suggested_action": "Verificar o cadastro de peso no aparelho de leitura das filiais com mais falhas.",
        },
    ],
    "viagens": [
        {
            "metric_key": "travel_fila_aberta",
            "kind": "above_pct_of_baseline",
            "metric": "open",
            "ratio": 1.5,
            "good_direction": "down",
            "severity": DashAlert.SEVERITY_ATENCAO,
            "title": "Fila de viagens em aberto crescendo",
            "message": "Últimos 30 dias: {current:.0f} viagens em aberto, {pct:.0f}% acima da "
                       "média mensal do trimestre ({baseline:.0f}).",
            "suggested_action": "Priorizar o fechamento das viagens em aberto mais antigas.",
        },
        {
            "metric_key": "travel_km_sem_registro",
            "kind": "below_absolute",
            "metric": "km_coverage_pct",
            "threshold": 70,
            "good_direction": "up",
            "severity": DashAlert.SEVERITY_ATENCAO,
            "title": "Muita viagem sem KM registrado",
            "message": "Só {current:.0f}% das viagens dos últimos 30 dias têm KM lançado "
                       "(abaixo do piso de {threshold:.0f}%) — sem isso o custo por KM da "
                       "frota fica subestimado.",
            "suggested_action": "Cobrar o lançamento de KM das viagens recentes sem esse dado.",
        },
    ],
    "ti-bi": [
        {
            "metric_key": "ti_sla_queda",
            "kind": "drop_points_below_baseline",
            "metric": "sla_valid_pct",
            # sla_valid_pct já é um percentual (0-100) em qualquer recorte, não
            # uma soma que precise virar "média por mês" — diferente de
            # `records`/`open`/`backlog`, que são contagens cumulativas.
            "rate_metric": True,
            "points": 15,
            "good_direction": "up",
            "severity": DashAlert.SEVERITY_CRITICO,
            "title": "SLA do helpdesk piorou",
            "message": "SLA cumprido nos últimos 30 dias: {current:.0f}%, {points:.0f} pontos "
                       "abaixo da média do trimestre ({baseline:.0f}%).",
            "suggested_action": "Revisar a fila de chamados perto de estourar o SLA de resposta/conclusão.",
        },
        {
            "metric_key": "ti_backlog_alto",
            "kind": "above_pct_of_baseline",
            "metric": "backlog",
            "ratio": 1.5,
            "good_direction": "down",
            "severity": DashAlert.SEVERITY_ATENCAO,
            "title": "Backlog de chamados crescendo",
            "message": "Backlog atual: {current:.0f} chamados, {pct:.0f}% acima da média "
                       "mensal do trimestre ({baseline:.0f}).",
            "suggested_action": "Reforçar o atendimento até normalizar o backlog de chamados.",
        },
    ],
}


def _evaluate_rule(rule, current_metrics, baseline_metrics):
    current = current_metrics.get(rule["metric"])
    if current is None:
        return None
    current = float(current)

    if rule["kind"] in ("below_absolute", "above_absolute"):
        threshold = rule["threshold"]
        triggered = current > threshold if rule["kind"] == "above_absolute" else current < threshold
        if not triggered:
            return None
        return {
            "current": current, "baseline": None, "change_pct": None,
            "message": rule["message"].format(current=current, threshold=threshold),
        }

    baseline_raw = baseline_metrics.get(rule["metric"]) if baseline_metrics else None
    if baseline_raw is None:
        return None
    baseline = float(baseline_raw) if rule.get("rate_metric") else float(baseline_raw) / BASELINE_MONTHS

    if rule["kind"] == "drop_points_below_baseline":
        drop = baseline - current
        if drop < rule["points"]:
            return None
        return {
            "current": current, "baseline": baseline, "change_pct": _pct_change(current, baseline),
            "message": rule["message"].format(current=current, baseline=baseline, points=drop),
        }

    if baseline <= 0:
        return None
    ratio = current / baseline
    if rule["kind"] == "below_pct_of_baseline" and ratio >= rule["ratio"]:
        return None
    if rule["kind"] == "above_pct_of_baseline" and ratio <= rule["ratio"]:
        return None
    pct = _pct_change(current, baseline)
    return {
        "current": current, "baseline": baseline, "change_pct": pct,
        "message": rule["message"].format(current=current, baseline=baseline, pct=abs(pct or 0)),
    }


def _upsert_alert(dashboard, rule, result, scope_label):
    alert, _created = DashAlert.objects.update_or_create(
        dashboard=dashboard,
        metric_key=rule["metric_key"],
        defaults={
            "severity": rule["severity"],
            "title": rule["title"],
            "message": result["message"],
            "scope_label": scope_label,
            "current_value": result["current"],
            "baseline_value": result["baseline"],
            "change_pct": result["change_pct"],
            "status": DashAlert.STATUS_ACTIVE,
            "resolved_at": None,
        },
    )
    _ensure_alert_action(dashboard, alert, rule.get("suggested_action"))
    return alert


def _ensure_alert_action(dashboard, alert, suggested_action):
    """Todo alerta ativo ganha uma ação de checklist correspondente.

    Sem isso, "fechar o ciclo" (ver `resolved_action_progress`) não teria
    nada pra fechar — hoje só um alerta cria ações com `origin_alert`
    preenchido. Não duplica enquanto a ação anterior do mesmo alerta ainda
    estiver pendente/em andamento; uma vez concluída ou descartada, se a
    condição disparar de novo mais tarde, uma ação nova é criada.
    """
    if not suggested_action:
        return
    already_open = (
        DashDecisionAction.objects.filter(origin_alert=alert)
        .exclude(status__in=[DashDecisionAction.STATUS_DONE, DashDecisionAction.STATUS_DISMISSED])
        .exists()
    )
    if already_open:
        return
    DashDecisionAction.objects.create(
        dashboard=dashboard, origin=DashDecisionAction.ORIGIN_ALERT, origin_alert=alert,
        description=suggested_action,
    )


def _resolve_stale_alerts(dashboard, triggered_metric_keys):
    (
        DashAlert.objects.filter(dashboard=dashboard, status=DashAlert.STATUS_ACTIVE)
        .exclude(metric_key__in=triggered_metric_keys)
        .update(status=DashAlert.STATUS_RESOLVED, resolved_at=timezone.now())
    )


def refresh_alerts_for_scoped_dashboard(dashboard, current_dashboard=None, baseline_dashboard=None):
    """Recalcula os alertas de um painel com recorte de período.

    `current_dashboard`/`baseline_dashboard` deixam reaproveitar o resultado
    de `load_X_dashboard()` já buscado para os cartões, em vez de consultar o
    ERP de novo — só a linha de base (`period_key="3"`) é buscada aqui.
    """
    slug = dashboard.slug
    if slug not in ALERT_RULES:
        return
    current_dashboard = current_dashboard or _load_dashboard(slug, CURRENT_PERIOD_KEY)
    baseline_dashboard = baseline_dashboard or _load_dashboard(slug, BASELINE_PERIOD_KEY)
    current_metrics = current_dashboard["metrics"]
    baseline_metrics = baseline_dashboard["metrics"]

    triggered = []
    for rule in ALERT_RULES[slug]:
        result = _evaluate_rule(rule, current_metrics, baseline_metrics)
        if result is None:
            continue
        triggered.append(rule["metric_key"])
        _upsert_alert(dashboard, rule, result, "Últimos 30 dias vs. média mensal do trimestre")
    _resolve_stale_alerts(dashboard, triggered)


def refresh_dna_alert(dashboard, dna_card):
    """Alerta único do DNA de Cliente: presença de cliente em estado crítico
    na última análise de cada um (não é tendência — é um "isso existe agora
    e merece olhar")."""
    critico = dna_card["health_counts"]["critico"]
    if critico > 0:
        _upsert_alert(
            dashboard,
            {
                "metric_key": "dna_clientes_criticos",
                "severity": DashAlert.SEVERITY_CRITICO,
                "title": "Cliente(s) em estado crítico no DNA",
                "message": (
                    f"{critico} cliente(s) com saúde \"crítico\" na última análise de IA "
                    "salva para eles."
                ),
                "suggested_action": "Revisar os clientes em estado crítico no DNA e definir um plano por conta.",
            },
            {"current": critico, "baseline": None, "change_pct": None,
             "message": (
                 f"{critico} cliente(s) com saúde \"crítico\" na última análise de IA "
                 "salva para eles."
             )},
            "Última análise salva por cliente",
        )
    else:
        _resolve_stale_alerts(dashboard, [])


def refresh_alerts_for_dashboards(dashboards, metrics_cache=None):
    """Recalcula alertas para os painéis dados. `metrics_cache` (de
    `collect_dashboard_summaries`) evita repetir a consulta do mês corrente."""
    metrics_cache = metrics_cache or {}
    dna_card = None
    for dashboard in dashboards:
        if dashboard.slug == "customer-dna":
            dna_card = dna_card or _dna_customer_card()
            refresh_dna_alert(dashboard, dna_card)
        elif dashboard.slug in ALERT_RULES:
            try:
                current = metrics_cache.get(("current", dashboard.slug))
                refresh_alerts_for_scoped_dashboard(dashboard, current_dashboard=current)
            except Exception:
                # ERP fora do ar: não atualiza os alertas deste painel agora,
                # mas não derruba a rodada dos outros. O cartão já mostrou o
                # erro (`_scoped_dashboard_card`); o alerta antigo (se houver)
                # fica como estava até a próxima rodada.
                continue


# ------------------------------------------------------ ações rastreadas ---

def import_ai_actions(dashboards):
    """Importa `recommended_actions` da última análise concluída de cada
    painel com recorte (Romaneios, Viagens, TI) como itens de checklist.

    DNA de Cliente fica de fora: as ações da IA ali são por cliente, e uma
    fila global de ações por cliente teria ruído demais para ser útil aqui —
    elas continuam melhor no lugar de sempre, dentro da própria tela do DNA.
    Deduplicação por (painel, descrição): rodar de novo não duplica.
    """
    created_total = 0
    for dashboard in dashboards:
        snapshot_model = _SNAPSHOT_MODELS.get(dashboard.slug)
        if snapshot_model is None:
            continue
        snapshot = (
            snapshot_model.objects.filter(status=snapshot_model.STATUS_COMPLETED)
            .order_by("-ai_completed_at")
            .first()
        )
        if not snapshot:
            continue
        actions = (snapshot.ai_response or {}).get("recommended_actions") or []
        existing = set(
            DashDecisionAction.objects.filter(dashboard=dashboard, origin=DashDecisionAction.ORIGIN_AI)
            .exclude(status=DashDecisionAction.STATUS_DISMISSED)
            .values_list("description", flat=True)
        )
        for description in actions:
            description = (description or "").strip()
            if not description or description in existing:
                continue
            DashDecisionAction.objects.create(
                dashboard=dashboard, origin=DashDecisionAction.ORIGIN_AI, description=description,
            )
            existing.add(description)
            created_total += 1
    return created_total


def capture_action_resolution(action):
    """Chamado quando uma ação vira "Concluída": guarda o valor do indicador
    do alerta que a originou, no momento do fechamento — sem isso, "fechar o
    ciclo" não teria um "antes" pra comparar com o "depois" mais tarde.

    Ações sem `origin_alert` (manuais, ou vindas de `recommended_actions` da
    IA) não têm indicador pra guardar — ficam sem essa comparação, e não é
    tratado como erro.
    """
    if not action.origin_alert_id:
        return
    action.resolved_metric_key = action.origin_alert.metric_key
    action.resolved_metric_value = action.origin_alert.current_value


def _metric_field_for(slug, metric_key):
    """Campo de `dashboard["metrics"]` por trás de um `metric_key` de alerta
    — só existe pra alertas vindos de `ALERT_RULES` (não o de DNA, que é uma
    contagem de snapshots, não um campo do dashboard)."""
    for rule in ALERT_RULES.get(slug, []):
        if rule["metric_key"] == metric_key:
            return rule["metric"], rule.get("good_direction", "up")
    return None, None


def resolved_action_progress(action, metrics_cache):
    """"Antes → depois" de uma ação concluída vinda de um alerta: o valor do
    indicador quando a ação foi fechada, contra o valor de agora (do mesmo
    `metrics_cache` já usado nos cartões — sem consulta nova ao ERP).

    Devolve `None` quando não há o que comparar (ação sem `origin_alert`,
    alerta de DNA, ou painel indisponível nesta rodada).
    """
    if action.resolved_metric_value is None or not action.resolved_metric_key:
        return None
    metric_field, good_direction = _metric_field_for(action.dashboard.slug, action.resolved_metric_key)
    if metric_field is None:
        return None
    current_dashboard = metrics_cache.get(("current", action.dashboard.slug))
    if not current_dashboard:
        return None
    current_value = current_dashboard["metrics"].get(metric_field)
    if current_value is None:
        return None

    before = action.resolved_metric_value
    after = float(current_value)
    improved = after > before if good_direction == "up" else after < before
    return {
        "before": before,
        "after": after,
        "change_pct": _pct_change(after, before),
        "improved": improved if after != before else None,
    }


def recently_resolved_actions_with_progress(dashboards, metrics_cache, limit=5):
    """Últimas ações concluídas com indicador pra comparar, para a seção
    "Isso realmente melhorou?" da Central de Decisão."""
    actions = (
        DashDecisionAction.objects.filter(
            dashboard__in=dashboards,
            status=DashDecisionAction.STATUS_DONE,
            resolved_metric_value__isnull=False,
        )
        .select_related("dashboard", "origin_alert")
        .order_by("-completed_at")[:limit]
    )
    results = []
    for action in actions:
        progress = resolved_action_progress(action, metrics_cache)
        if progress:
            results.append((action, progress))
    return results
