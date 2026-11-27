"""Detalhe ("Expandir dados") dos cards da Central de Decisão que não vêm de um BI.

O cartão do DNA do Cliente (a última análise de IA salva de cada cliente) e as ações (pendentes e
concluídas) leem o banco do ConnectMX, com as mesmas consultas que a Central usa para os números
da tela. Os números dos cartões dos BIs, o sparkline e os alertas abrem os endpoints de detalhe de
cada BI (`bi_drill`), com o recorte que a Central usa (os últimos 30 dias).
"""

from django.utils import timezone

from . import decision_center
from .models import CustomerInsightSnapshot, DashDecisionAction

HEALTH = {"critico": ("Crítico", "bad"), "atencao": ("Atenção", "warn"), "saudavel": ("Saudável", "good")}


class UnknownPoint(ValueError):
    """Card ou ponto que o detalhe da Central não conhece."""


def _when(value):
    return timezone.localtime(value).isoformat() if value else None


def _person(user):
    return (user.get_full_name() or user.username) if user else ""


def customer_dna_detail(point, dna_url=None):
    """Cartão do DNA do Cliente: "analisados" (a última análise de cada cliente, entre as 500 mais
    recentes, como o cartão conta) ou "critico" (só os de saúde crítica)."""
    if point not in ("analisados", "critico"):
        raise UnknownPoint("Ponto do cartão do DNA desconhecido.")
    latest = {}
    queryset = (
        CustomerInsightSnapshot.objects.filter(status=CustomerInsightSnapshot.STATUS_COMPLETED)
        .order_by("-created_at")
        .only("customer_code", "customer_name", "ai_response", "created_at")[:500]
    )
    for snapshot in queryset:
        latest.setdefault(snapshot.customer_code, snapshot)
    counts = {key: 0 for key in HEALTH}
    rows = []
    for snapshot in latest.values():
        response = snapshot.ai_response or {}
        health = response.get("health")
        if health in counts:
            counts[health] += 1
        if point == "critico" and health != "critico":
            continue
        label, tone = HEALTH.get(health, ("Sem leitura", None))
        rows.append({
            "customer": snapshot.customer_name or f"Cliente {snapshot.customer_code}",
            "customer_sub": str(snapshot.customer_code),
            "customer_url": dna_url(snapshot.customer_code) if dna_url else None,
            "health": label,
            "summary": response.get("executive_summary") or "",
            "analyzed": _when(snapshot.created_at),
            "__tone": tone if tone in ("bad", "warn") else None,
        })
    columns = [
        {"key": "customer", "label": "Cliente", "href": "customer_url", "sub": "customer_sub"},
        {"key": "health", "label": "Saúde"},
        {"key": "analyzed", "label": "Análise de", "type": "datetime", "nowrap": True},
        {"key": "summary", "label": "Resumo da IA", "wrap": True},
    ]
    return {
        "title": "Clientes em estado crítico" if point == "critico" else "Clientes analisados",
        "subtitle": "A última análise de IA salva de cada cliente (entre as 500 mais recentes), como o cartão conta",
        "summary": [
            {"label": "Clientes analisados", "value": str(len(latest))},
            {"label": "Crítico", "value": str(counts["critico"]), "tone": "bad" if counts["critico"] else None},
            {"label": "Atenção", "value": str(counts["atencao"]), "tone": "warn" if counts["atencao"] else None},
            {"label": "Saudável", "value": str(counts["saudavel"]), "tone": "good" if counts["saudavel"] else None},
        ],
        "sections": [{
            "key": "customers", "label": "Clientes", "columns": columns, "rows": rows, "sort": {"key": "analyzed", "dir": "desc"},
            "groupBy": ["health"], "empty": "Nenhum cliente com análise de IA nesta situação.",
        }],
    }


ACTION_COLUMNS = [
    {"key": "dashboard", "label": "Painel"},
    {"key": "description", "label": "Ação", "wrap": True},
    {"key": "status", "label": "Situação"},
    {"key": "origin", "label": "Origem", "sub": "alert"},
    {"key": "assigned", "label": "Responsável"},
    {"key": "due", "label": "Prazo", "type": "date", "nowrap": True},
    {"key": "created", "label": "Criada em", "type": "datetime", "nowrap": True, "sub": "created_by"},
]


def _action_row(action):
    return {
        "dashboard": action.dashboard.name,
        "description": action.description,
        "status": action.get_status_display(),
        "origin": action.get_origin_display(),
        "alert": action.origin_alert.title if action.origin_alert_id and action.origin_alert else "",
        "assigned": _person(action.assigned_to) or "-",
        "due": action.due_date.isoformat() if action.due_date else None,
        "created": _when(action.created_at),
        "created_by": _person(action.created_by),
        "__tone": "warn" if action.due_date and action.due_date < timezone.localdate() else None,
    }


def pending_actions_detail(dashboards):
    """Painel "Ações pendentes" inteiro (a tela pagina): as mesmas ações que a contagem do título."""
    actions = list(
        DashDecisionAction.objects.filter(dashboard__in=dashboards)
        .exclude(status__in=[DashDecisionAction.STATUS_DONE, DashDecisionAction.STATUS_DISMISSED])
        .select_related("dashboard", "assigned_to", "created_by", "origin_alert")
    )
    by_origin = {}
    for action in actions:
        by_origin[action.get_origin_display()] = by_origin.get(action.get_origin_display(), 0) + 1
    late = sum(1 for action in actions if action.due_date and action.due_date < timezone.localdate())
    return {
        "title": "Ações pendentes",
        "subtitle": "Todas as ações em aberto dos painéis liberados para você (a tela mostra uma página por vez)",
        "summary": [{"label": "Pendentes", "value": str(len(actions))},
                    {"label": "Com prazo vencido", "value": str(late), "tone": "warn" if late else None}]
                   + [{"label": f"Origem: {origin}", "value": str(count)} for origin, count in sorted(by_origin.items(), key=lambda pair: -pair[1])[:3]],
        "sections": [{
            "key": "actions", "label": "Ações", "columns": ACTION_COLUMNS, "rows": [_action_row(action) for action in actions],
            "sort": {"key": "created", "dir": "desc"}, "groupBy": ["dashboard", "status", "origin", "assigned"], "empty": "Nenhuma ação pendente.",
        }],
    }


def action_detail(action):
    """Uma ação: o registro, o alerta de onde ela veio e, se já foi concluída com indicador, o antes
    e o agora (a mesma conta da seção "Isso realmente melhorou?", com o painel lido de novo)."""
    facts = [
        {"label": "Situação", "value": action.get_status_display()},
        {"label": "Origem", "value": action.get_origin_display()},
        {"label": "Painel", "value": action.dashboard.name},
    ]
    progress = None
    if action.status == DashDecisionAction.STATUS_DONE and action.resolved_metric_value is not None:
        cache = {}
        try:
            cache[("current", action.dashboard.slug)] = decision_center._load_dashboard(action.dashboard.slug, decision_center.CURRENT_PERIOD_KEY)
        except Exception:  # painel indisponível agora: a ação aparece sem o "agora"
            cache = {}
        progress = decision_center.resolved_action_progress(action, cache)
    if progress:
        verdict = "Melhorou" if progress["improved"] is True else "Piorou" if progress["improved"] is False else "Estável"
        facts.append({"label": "Antes → agora", "value": f"{progress['before']:.0f} → {progress['after']:.0f}".replace(".", ","),
                      "note": verdict, "tone": "good" if progress["improved"] is True else "bad" if progress["improved"] is False else None})
    fields = [
        ("Ação", action.description),
        ("Painel", action.dashboard.name),
        ("Situação", action.get_status_display()),
        ("Origem", action.get_origin_display()),
        ("Responsável", _person(action.assigned_to) or "-"),
        ("Prazo", action.due_date.strftime("%d/%m/%Y") if action.due_date else "-"),
        ("Criada por", _person(action.created_by) or "-"),
        ("Criada em", timezone.localtime(action.created_at).strftime("%d/%m/%Y %H:%M") if action.created_at else "-"),
        ("Concluída em", timezone.localtime(action.completed_at).strftime("%d/%m/%Y %H:%M") if action.completed_at else "-"),
    ]
    alert = action.origin_alert
    if alert:
        fields += [
            ("Alerta de origem", alert.title),
            ("Mensagem do alerta", alert.message),
            ("Valor no alerta", f"{alert.current_value:.1f}".replace(".", ",") if alert.current_value is not None else "-"),
            ("Referência (média do trimestre)", f"{alert.baseline_value:.1f}".replace(".", ",") if alert.baseline_value is not None else "-"),
            ("Situação do alerta", alert.get_status_display()),
        ]
    if action.resolved_metric_value is not None:
        fields.append(("Indicador ao concluir", f"{action.resolved_metric_value:.1f}".replace(".", ",")))
    return {
        "title": action.description[:90] + ("…" if len(action.description) > 90 else ""),
        "subtitle": f"Ação da Central de Decisão · {action.dashboard.name}",
        "summary": facts,
        "sections": [{
            "key": "fields", "label": "Registro", "rows": [{"field": field, "value": value} for field, value in fields],
            "columns": [{"key": "field", "label": "Campo"}, {"key": "value", "label": "Valor", "wrap": True}], "empty": "Sem dados.",
        }],
    }
