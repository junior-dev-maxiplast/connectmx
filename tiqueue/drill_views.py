"""Endpoints do "Expandir dados" do menu dos gráficos do Dashes (static/js/maxisysDrill.js).

Cada um devolve em JSON as linhas por trás de um ponto de um gráfico, montadas em
`chart_drill` (DNA e ficha do cliente) ou `bi_drill` (BIs do TI, Viagens, Romaneios e
Compras), com a mesma permissão do painel de onde o gráfico vem.
"""

from datetime import date, timedelta

from django.http import JsonResponse
from django.urls import reverse

from . import bi_drill, center_drill, chart_drill, intel_drill, management_drill, page_filters, panel_drill
from . import commercial_dna as dna
from . import customer_intel as intel
from . import management_intel as mgmt
from .management_views import _management_filters
from .models import RepresentativeGoal
from .views import (
    _compras_bi_scope_from_source,
    _customer_dna_scope_from_request,
    _dashes_access_required,
    _dashes_session_required,
    _romaneio_bi_scope_from_source,
    _travel_bi_scope_from_source,
    allowed_dashboards,
)


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _customer_url():
    base = reverse("dashesCustomerDnaV2Page")
    return lambda code: f"{base}?cliente={code}"


REP_VISUALS = ("meta", "faturamento", "ultima-compra", "cliente-periodo", "carteira-cliente",
               "periodo", "mix-grupo", "entrada", "meta-acumulado", "meta-ano", "meta-familia")
REP_PERIOD_VISUALS = ("cliente-periodo", "periodo", "mix-grupo", "entrada")


@_dashes_access_required("representative-dna")
def dashesRepresentativeDnaDetail(request):
    """?representante=10004&visual=meta|faturamento|ultima-compra&mes=2026-11[&cliente=...][&grupo=...]
    · visual=cliente-periodo&cliente=...&inicio=AAAA-MM-DD&fim=AAAA-MM-DD (linha de "Clientes da carteira")
    · visual=carteira-cliente&cliente=... (linha de "Carteira a faturar")
    · visual=periodo&foco=faturamento|sem-impostos|volume|fator|margem|pedidos|devolucoes|prazo&inicio=&fim= (cards Faturamento)
    · visual=mix-grupo&familia=<grupo>&inicio=&fim= (linha do Mix por grupo) · visual=entrada&inicio=&fim= (Entrada de pedidos)
    · visual=meta-acumulado|meta-ano (cards da meta) · visual=meta-familia&familia=<grupo> (linha de Meta por família)"""
    raw_rep = (request.GET.get("representante") or "").strip()
    visual = (request.GET.get("visual") or "meta").strip()
    month_start = chart_drill.parse_month(request.GET.get("mes"))
    raw_customer = (request.GET.get("cliente") or "").strip()
    monthly = visual in ("meta", "faturamento", "ultima-compra")
    if not raw_rep.isdigit() or visual not in REP_VISUALS or (monthly and month_start is None):
        return _error("Pedido inválido: informe o representante, o mês (AAAA-MM) e o gráfico.")
    if visual in ("ultima-compra", "cliente-periodo", "carteira-cliente") and not raw_customer.isdigit():
        return _error("Pedido inválido: informe o cliente.")
    primary = dna.primary_rep(int(raw_rep))
    groups = page_filters.values(request.GET, "grupo")
    family = (request.GET.get("familia") or "").strip()[:80]
    if visual in ("mix-grupo", "meta-familia") and not family:
        return _error("Pedido inválido: informe o grupo.")
    if visual in REP_PERIOD_VISUALS:
        try:
            start = date.fromisoformat((request.GET.get("inicio") or "").strip()[:10])
            last = date.fromisoformat((request.GET.get("fim") or "").strip()[:10])
        except ValueError:
            return _error("Pedido inválido: informe o período (início e fim em AAAA-MM-DD).")
        if last < start or (last - start).days > 3 * 366:
            return _error("Pedido inválido: período fora do permitido.")
        end = last + timedelta(days=1)
        try:
            if visual == "periodo":
                result = chart_drill.representative_period_detail(primary, start, end, groups=groups,
                                                                  focus=(request.GET.get("foco") or "").strip(), customer_url=_customer_url())
            elif visual == "mix-grupo":
                result = chart_drill.representative_mix_detail(primary, start, end, family, customer_url=_customer_url())
            elif visual == "entrada":
                result = chart_drill.representative_intake_detail(primary, start, end, customer_url=_customer_url())
            else:
                result = chart_drill.representative_customer_detail(
                    primary, int(raw_customer), start, end, groups=groups, period_label=f"{start:%d/%m/%Y} a {last:%d/%m/%Y}",
                )
        except Exception as exc:
            return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
        return JsonResponse(result)
    if visual in ("meta-acumulado", "meta-ano", "meta-familia"):
        # Os cards da meta são do ano corrente: o acumulado vai de janeiro ao fim do mês atual.
        today = date.today()
        start = date(today.year, 1, 1)
        end = dna._add_months(today.replace(day=1), 1) if visual == "meta-acumulado" else date(today.year + 1, 1, 1)
        months = dna.MONTH_NAMES
        title = (f"Acumulado de jan a {months[today.month - 1]} de {today.year}" if visual == "meta-acumulado"
                 else f"{family.title()} · meta de {today.year}" if visual == "meta-familia" else f"Meta de {today.year}")
        goals = list(RepresentativeGoal.objects.filter(rep_code=primary, year=today.year))
        try:
            result = chart_drill.representative_goal_detail(
                primary, start, end, title, goals=goals, groups=groups, today=today, customer_url=_customer_url(),
                family=family if visual == "meta-familia" else None,
                period_note=" · carteira inclui as entregas dos próximos meses do ano" if visual != "meta-acumulado" else "",
            )
        except Exception as exc:
            return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
        return JsonResponse(result)
    if visual == "carteira-cliente":
        try:
            result = chart_drill.representative_customer_orders(primary, int(raw_customer))
        except Exception as exc:
            return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
        return JsonResponse(result)
    goals = []
    if visual == "meta":
        goals = list(RepresentativeGoal.objects.filter(rep_code=primary, year=month_start.year, month=month_start.month))
    try:
        result = chart_drill.representative_detail(
            primary, visual, month_start, goals=goals,
            groups=page_filters.values(request.GET, "grupo"), customer_url=_customer_url(),
            customer_code=int(raw_customer) if raw_customer.isdigit() else None,
        )
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
    return JsonResponse(result)


CUSTOMER_VISUALS = ("periodo", "mix-grupo", "produto", "carteira")


@_dashes_access_required("customer-dna")
def dashesCustomerDnaDetail(request):
    """?cliente=10832&mes=2026-11[&view=group&unidade=...][&grupo=...]: um mês dos gráficos;
    ?visual=periodo&foco=...|mix-grupo&familia=...|produto&produto=...|carteira, com &inicio=&fim=
    (AAAA-MM-DD, o recorte da página): cards do período, linhas do mix e dos produtos, carteira."""
    raw_customer = (request.GET.get("cliente") or "").strip()
    visual = (request.GET.get("visual") or "").strip()
    month_start = chart_drill.parse_month(request.GET.get("mes"))
    if not raw_customer.isdigit() or (visual not in CUSTOMER_VISUALS and month_start is None):
        return _error("Pedido inválido: informe o cliente e o mês (AAAA-MM).")
    start = last = None
    if visual:
        try:
            start = date.fromisoformat((request.GET.get("inicio") or "").strip()[:10])
            last = date.fromisoformat((request.GET.get("fim") or "").strip()[:10])
        except ValueError:
            return _error("Pedido inválido: informe o período (início e fim em AAAA-MM-DD).")
        if last < start or (last - start).days > 3 * 366:
            return _error("Pedido inválido: período fora do permitido.")
    customer_id = int(raw_customer)
    view_mode, member_customer_id = _customer_dna_scope_from_request(request)
    groups = page_filters.values(request.GET, "grupo")
    try:
        scope = dna.legacy._resolve_customer_scope(customer_id, view_mode, member_customer_id)
        codes = scope["customer_codes"]
        if scope["mode"] == "group" and len(codes) > 1:
            title = f"Grupo {scope.get('root_display') or customer_id} · {len(codes)} CNPJs"
        else:
            member = next((item for item in scope["members"] if item["code"] == codes[0]), None)
            title = f"{member['name']} ({codes[0]})" if member else f"Cliente {codes[0]}"
        customer_url = _customer_url() if len(codes) > 1 else None
        end = last + timedelta(days=1) if last else None
        if visual == "periodo":
            result = chart_drill.customer_period_detail(codes, start, end, groups=groups, focus=(request.GET.get("foco") or "").strip(),
                                                        customer_url=customer_url, title_name=title, scope_info=scope)
        elif visual == "mix-grupo":
            family = (request.GET.get("familia") or "").strip()[:80]
            result = chart_drill.customer_period_detail(codes, start, end, groups=[family], customer_url=customer_url, title_name=title)
            result["title"] = (family or "Sem grupo").title()
        elif visual == "produto":
            result = chart_drill.customer_product_detail(codes, start, end, (request.GET.get("produto") or "").strip()[:40],
                                                         customer_url=customer_url, title_name=title)
        elif visual == "carteira":
            result = chart_drill.customer_orders_detail(codes, start, end, title_name=title)
        else:
            result = chart_drill.customer_detail(codes, month_start, groups=groups, customer_url=customer_url, title_name=title)
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
    return JsonResponse(result)


@_dashes_access_required("customer-portfolio")
def dashesCustomerProfileDetail(request, customer_code):
    """Ficha do Cliente (o cliente vem do endereço): ?mes=2026-11 (um mês); ?grupo=<grupo> (linha de
    "O que compra", 12 meses); ?cruzada=<grupo> (linha de "Venda cruzada": quem do segmento compra)."""
    group = (request.GET.get("grupo") or "").strip()[:80]
    cross = (request.GET.get("cruzada") or "").strip()[:80]
    try:
        if cross:
            from .intel_views import _prepare

            base, _window, metrics = _prepare((request.GET.get("periodo") or intel.DEFAULT_PERIOD).strip())
            item = metrics.get(customer_code)
            if item is None:
                return _error("Cliente fora da base da carteira.", status=404)
            return JsonResponse(intel_drill.cross_sell_detail(item, base, metrics, cross, customer_url=_profile_url()))
        if group:
            # Mesmo recorte do "O que compra": do 1º dia de 11 meses atrás até hoje.
            today = date.today()
            start = dna._add_months(today.replace(day=1), -(intel.MIX_MONTHS - 1))
            key = group.upper()
            result = chart_drill.customer_period_detail([customer_code], start, today + timedelta(days=1), groups=[key], title_name=f"Cliente {customer_code}")
            result["title"] = group.title()
            result["subtitle"] = f"Notas do grupo nos últimos 12 meses · {result['subtitle'].split(' · ', 1)[-1]}"
            return JsonResponse(result)
        month_start = chart_drill.parse_month(request.GET.get("mes"))
        if month_start is None:
            return _error("Pedido inválido: informe o mês (AAAA-MM).")
        return JsonResponse(chart_drill.customer_detail([customer_code], month_start))
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)


# ---------------------------------------------------------------------------
# BIs do TI, Viagens, Romaneios e Compras: ?<recorte da página>&dim=...&valor=...&rotulo=...
# ---------------------------------------------------------------------------

def _reference_date(raw):
    """`?ref=AAAA-MM-DD`: dia do snapshot dos quadros de análise processada (a janela do
    período é contada a partir dele). Data inválida ou no futuro vale como hoje."""
    try:
        reference = date.fromisoformat((raw or "").strip()[:10])
    except ValueError:
        return None
    return reference if date(2000, 1, 1) <= reference < date.today() else None


def _bi_detail(request, loader, source):
    dim = (request.GET.get("dim") or "").strip()
    value = request.GET.get("valor")
    label = (request.GET.get("rotulo") or "").strip()[:200]
    if not dim or value is None:
        return _error("Pedido inválido: informe o gráfico e o ponto clicado.")
    today = _reference_date(request.GET.get("ref"))
    try:
        return JsonResponse(loader(dim, value.strip(), label, today))
    except bi_drill.UnknownDimension as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"Não foi possível consultar {source}: {exc}", status=502)


@_dashes_access_required("ti-bi")
def dashesItBiDetail(request):
    period_key = (request.GET.get("periodo") or "12").strip()
    company_key = (request.GET.get("empresa") or "all").strip()
    attendant_key = (request.GET.get("atendente") or "all").strip()
    return _bi_detail(
        request, lambda dim, value, label, today: bi_drill.it_detail(period_key, company_key, attendant_key, dim, value, label, today),
        "o SM (helpdesk)",
    )


@_dashes_access_required("viagens")
def dashesTravelBiDetail(request):
    period_key, carrier_key, situation_key = _travel_bi_scope_from_source(request.GET)
    return _bi_detail(
        request, lambda dim, value, label, today: bi_drill.travel_detail(period_key, carrier_key, situation_key, dim, value, label, today),
        "o ERP Senior",
    )


@_dashes_access_required("romaneios")
def dashesRomaneioBiDetail(request):
    period_key, branch_key, stage_key, matricula_key = _romaneio_bi_scope_from_source(request.GET)
    return _bi_detail(
        request,
        lambda dim, value, label, today: bi_drill.romaneio_detail(period_key, branch_key, stage_key, matricula_key, dim, value, label, today),
        "o ERP Senior",
    )


@_dashes_access_required("compras")
def dashesComprasBiDetail(request):
    scope = _compras_bi_scope_from_source(request.GET)
    return _bi_detail(request, lambda dim, value, label, today: bi_drill.compras_detail(*scope, dim, value, label, today), "o ERP Senior")


def _profile_url():
    """Ficha do cliente, como os rankings dos painéis animados abrem."""
    base = reverse("dashesCustomerProfilePage", args=[0])
    return lambda code: base.replace("/0/", f"/{code}/")


@_dashes_access_required("sales-panel")
def dashesSalesPanelDetail(request):
    """?dia=2026-11-17: notas do dia do calendário de faturamento do Painel de Vendas."""
    try:
        day = date.fromisoformat((request.GET.get("dia") or "").strip()[:10])
    except ValueError:
        return _error("Pedido inválido: informe o dia (AAAA-MM-DD).")
    today = date.today()
    if not today - timedelta(days=7 * 60) <= day <= today:
        return _error("Este dia está fora do calendário de faturamento.")
    try:
        result = panel_drill.sales_day_detail(day, customer_url=_profile_url())
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
    return JsonResponse(result)


@_dashes_access_required("receivables-panel")
def dashesReceivablesPanelDetail(request):
    """?mes=2026-10: títulos liquidados no mês (gráfico Recebido por mês do Contas a Receber);
    ?de=2026-09&ate=2026-11: os meses dos cards Prazo médio e Pontualidade; ?creditos=1: card
    Créditos de clientes."""
    today = date.today()
    oldest = dna._add_months(today.replace(day=1), -24)
    valid = lambda month: month is not None and oldest <= month <= today  # noqa: E731
    try:
        if request.GET.get("creditos"):
            return JsonResponse(panel_drill.credit_titles_detail(today=today, customer_url=_profile_url()))
        if request.GET.get("de") or request.GET.get("ate"):
            first, last = chart_drill.parse_month(request.GET.get("de")), chart_drill.parse_month(request.GET.get("ate"))
            if not (valid(first) and valid(last)) or first > last:
                return _error("Pedido inválido: informe os meses (AAAA-MM) dos últimos dois anos.")
            return JsonResponse(panel_drill.paid_range_detail(first, last, today=today, customer_url=_profile_url(),
                                                              focus=(request.GET.get("foco") or "").strip() or None))
        month_start = chart_drill.parse_month(request.GET.get("mes"))
        if not valid(month_start):
            return _error("Pedido inválido: informe um mês (AAAA-MM) dos últimos dois anos.")
        return JsonResponse(panel_drill.paid_month_detail(month_start, today=today, customer_url=_profile_url()))
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)


@_dashes_access_required("management-cockpit")
def dashesManagementDetail(request):
    """?visual=ponte-receita|ponte-lucro|retencao|matriz|quadrante|grupo|calor-margem|sazonalidade|rep|projecao
    &ponto=...: o ponto de um gráfico do Painel Gerencial, com o mesmo recorte da página (periodo, rep, grupo, meses)."""
    visual = (request.GET.get("visual") or "").strip()
    point = (request.GET.get("ponto") or "").strip()[:200]
    if not visual:
        return _error("Pedido inválido: informe o gráfico e o ponto clicado.")
    filters = _management_filters(request.GET)
    try:
        full = mgmt.load_base()
        base = mgmt.filter_base(full, filters.get("reps"), filters.get("groups"))
        window = mgmt.month_window(filters["months"], full["today"]) if filters.get("months") else mgmt.closed_window(
            (request.GET.get("periodo") or mgmt.DEFAULT_PERIOD).strip(), full["today"])
        result = management_drill.management_detail(full, base, window, visual, point, filters=filters, customer_url=_profile_url())
    except management_drill.UnknownPoint as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
    return JsonResponse(result)


def _customer_detail(request, history=None):
    """Mês a mês e notas de um cliente da base da carteira (?cliente=&periodo=[&historico=1])."""
    raw_customer = (request.GET.get("cliente") or "").strip()
    if not raw_customer.isdigit():
        return _error("Pedido inválido: informe o cliente.")
    period_key = (request.GET.get("periodo") or intel.DEFAULT_PERIOD).strip()
    try:
        base = intel.load_base()
        window = intel.resolve_window(period_key, today=base["today"])
        history = request.GET.get("historico") == "1" if history is None else history
        result = chart_drill.portfolio_customer_detail(base, window, int(raw_customer), history=history)
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
    return JsonResponse(result)


@_dashes_access_required("customer-portfolio")
def dashesCustomerPortfolioDetail(request):
    """?cliente=10832&periodo=12[&historico=1]: linha da tabela da Carteira de Clientes;
    ?indicador=lucro|concentracao|margem-negativa|clientes|ritmo|rfm:<segmento>&periodo=&uf=&rep=&segmento=&rfm=:
    card do resumo ou segmento RFM, com os filtros da página."""
    point = (request.GET.get("indicador") or "").strip()[:40]
    if not point:
        return _customer_detail(request)
    from .intel_views import _filters, _prepare

    filters = _filters(request.GET)
    try:
        _base, window, metrics = _prepare((request.GET.get("periodo") or intel.DEFAULT_PERIOD).strip())
        rows_all = intel.filter_rows(list(metrics.values()), state=filters["state"], rep=filters["rep"], segment=filters["segment"])
        rows = intel.filter_rfm(rows_all, filters.get("rfm"))
        result = intel_drill.portfolio_detail(rows, rows_all, window, point, customer_url=_profile_url())
    except intel_drill.UnknownPoint as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
    return JsonResponse(result)


@_dashes_access_required("customer-map")
def dashesCustomerMapDetail(request):
    """?ponto=resumo|uf:PR|cidade:<chave>|regiao:S|perdidas|perdida:<chave>|segmento:PR|<código>&periodo=&rep=&segmento=:
    card, área do mapa ou linha do ranking, com os filtros do mapa; ?cliente=: mês a mês e notas do cliente."""
    point = (request.GET.get("ponto") or "").strip()[:80]
    if not point:
        return _customer_detail(request)
    from .intel_views import _filters, _prepare

    filters = _filters(request.GET)
    try:
        _base, window, metrics = _prepare((request.GET.get("periodo") or intel.DEFAULT_PERIOD).strip())
        rows = intel.filter_rows(list(metrics.values()), rep=filters["rep"], segment=filters["segment"])
        intel.rank_by_profit([item for item in rows if item["net"] > 0])
        result = intel_drill.map_detail(rows, window, point, customer_url=_profile_url())
    except intel_drill.UnknownPoint as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
    return JsonResponse(result)


@_dashes_access_required("sales-agenda")
def dashesSalesAgendaDetail(request):
    """?indicador=previstas|risco|cruzada|reativar|contatos&rep=&dias=: card do resumo da Agenda;
    ?cliente=: compras do cliente nos últimos 24 meses (linha das prioridades)."""
    from django.utils import timezone

    from . import sales_agenda
    from .management_views import _agenda
    from .models import SalesAgendaContact

    point = (request.GET.get("indicador") or "").strip()[:40]
    if not point:
        return _customer_detail(request, history=True)
    try:
        base, rep_code, _horizon, agenda, _reps = _agenda(request)
        if point == "contatos":
            contacts = SalesAgendaContact.objects.filter(created_at__gte=timezone.now() - timedelta(days=7)).select_related("user")
            if rep_code is not None:
                contacts = contacts.filter(rep_code=rep_code)
            metrics = {entry["item"]["code"]: entry["item"] for entry in agenda["items"]}
            names = {}
            for contact in contacts:
                item = metrics.get(contact.customer_code) or (base["customers"].get(contact.customer_code) or {})
                names[contact.customer_code] = {
                    "name": item.get("name"), "rep": item.get("rep_name"),
                    "place": f"{item.get('city')}/{item.get('state')}" if item.get("city") else item.get("state") or "",
                }
            result = intel_drill.contacts_detail(list(contacts), names, sales_agenda.REASONS, customer_url=_profile_url())
        else:
            result = intel_drill.agenda_detail(agenda, point, sales_agenda.REASONS, customer_url=_profile_url())
    except intel_drill.UnknownPoint as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"Não foi possível consultar o ERP Senior: {exc}", status=502)
    return JsonResponse(result)


# ---------------------------------------------------------------------------
# Central de Decisão: cartão do DNA do Cliente e ações (os cartões dos BIs, o sparkline e os
# alertas usam os endpoints de detalhe de cada BI, com o recorte da Central)
# ---------------------------------------------------------------------------

@_dashes_session_required
def dashesDecisionCenterDetail(request):
    """?card=customer-dna&ponto=analisados|critico · ?acoes=1 (todas as pendentes) · ?acao=<id>.
    Só os painéis liberados para o usuário, como a própria Central."""
    from .models import DashDecisionAction

    dashboards = list(allowed_dashboards(request.user))
    slugs = {dashboard.slug for dashboard in dashboards}
    try:
        if request.GET.get("card"):
            if request.GET.get("card") != "customer-dna" or "customer-dna" not in slugs:
                return _error("Você não tem acesso aos dados deste cartão.", status=403)
            base = reverse("dashesCustomerDnaV2Page")
            return JsonResponse(center_drill.customer_dna_detail((request.GET.get("ponto") or "").strip(),
                                                                 dna_url=lambda code: f"{base}?cliente={code}"))
        if request.GET.get("acoes"):
            return JsonResponse(center_drill.pending_actions_detail(dashboards))
        raw_action = (request.GET.get("acao") or "").strip()
        if raw_action.isdigit():
            action = (
                DashDecisionAction.objects.filter(pk=int(raw_action), dashboard__in=dashboards)
                .select_related("dashboard", "assigned_to", "created_by", "origin_alert").first()
            )
            if action is None:
                return _error("Ação não encontrada nos painéis liberados para você.", status=404)
            return JsonResponse(center_drill.action_detail(action))
    except center_drill.UnknownPoint as exc:
        return _error(str(exc))
    return _error("Pedido inválido: informe o cartão, as ações ou a ação.")
