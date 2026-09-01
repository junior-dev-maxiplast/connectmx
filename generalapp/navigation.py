"""Catálogo de navegação do ConnectMX.

Fonte única do menu: a sidebar, o command palette (Ctrl+K) e o contador de
telas usadas leem daqui. Antes disso o menu vivia só no HTML, o que impedia
ordenar por uso, filtrar por permissão ou buscar sem duplicar a lista.

Agrupamento por **domínio**. O menu antigo tinha 7 atalhos e um grupo
"Funcionalidades" com 39 itens em 12 subcategorias que misturavam domínio
("Fila"), ação ("Cadastro"), público ("Usuário") e origem ("Sistemas
externos") — por isso a configuração da fila morava longe da fila e um módulo
interno como a Logística aparecia sob "Sistemas externos".
"""

from django.urls import NoReverseMatch, reverse


# Cada item: (url_name, rótulo, palavras-chave extras para a busca).
# O rótulo é o nome pelo qual a pessoa procura a tela, não o nome técnico.
NAV_GROUPS = [
    {
        # `flat`: sai como link direto no topo, sem dropdown. O Dashboard é a
        # tela mais aberta do sistema; enterrá-la um clique seria retrocesso.
        "key": "inicio",
        "label": "Início",
        "icon": "icon-home",
        "tone": "dashboard",
        "flat": True,
        "items": [
            ("index", "Dashboard", "painel inicio home", None, "icon-home"),
            ("hubPage", "HUB", "ferramentas atalhos links", None, "icon-grid"),
        ],
    },
    {
        "key": "atendimento",
        "label": "Atendimento",
        "icon": "icon-inbox",
        "tone": "queue",
        "items": [
            ("queueUserPage", "Minha fila", "tarefas minhas"),
            ("queueMainPage", "Fila geral", "tarefas equipe"),
            ("portalPendingDemandsPage", "Entrada de chamados", "pendentes triagem", "admin"),
            ("portalDemandPage", "Portal de chamados", "solicitante abrir chamado"),
            ("queueConcludedPage", "Tarefas concluídas", "finalizadas historico"),
            ("myAgendaPage", "Minha agenda", "compromissos"),
            ("listCreateStatus", "Status da fila", "configuracao"),
            ("manageTaskTypes", "Tipos de tarefa", "configuracao"),
            ("manageDemandTemplates", "Modelos de demanda", "configuracao template"),
            ("contractsPage", "Contratos", "sla acordo"),
        ],
    },
    {
        "key": "projetos",
        "label": "Projetos",
        "icon": "icon-kanban",
        "tone": "projects",
        "items": [
            ("projectCatalogPage", "Projetos em aberto", "andamento"),
            ("projectCatalogConcludedPage", "Projetos concluídos", "finalizados"),
            ("projectCalendarPage", "Calendário de projetos", "agenda geral"),
            ("projectTimelinePage", "Timeline", "gantt cronograma"),
            ("manageProjects", "Cadastro de projetos", "criar"),
        ],
    },
    {
        "key": "operacao",
        "label": "Operação",
        "icon": "icon-hex-bolt",
        "tone": "maintenance",
        "items": [
            ("maintenanceCalendarPage", "Calendário de manutenções", "agenda"),
            ("maintenanceSchedulePage", "Agendamentos", "manutencao"),
            ("maintenanceOutagePage", "Indisponibilidades", "parada downtime"),
            ("maintenanceCatalogPage", "Cadastro de manutenção", "rapido"),
            ("wifiVoucherPage", "Vouchers de wifi", "internet visitante"),
            ("tires_dashboard", "Logística — Pneus", "caminhao frota pneu"),
            ("logistics_romaneio", "Logística — Romaneios", "expedicao carga"),
            ("logistics_romaneio_mobile", "Logística — Contagem de pallets", "celular camera codigo de barras leitura pallet"),
            ("hqbooking_login", "Reserva da sede", "sala espaco"),
            ("lunch_booking", "Reserva de almoço", "refeicao"),
        ],
    },
    {
        "key": "conhecimento",
        "label": "Conhecimento",
        "icon": "icon-book",
        "tone": "knowledge",
        "items": [
            ("knowledgeConsultPage", "Consultar base", "buscar artigo"),
            ("knowledgeEntriesPage", "Registros da base", "artigos"),
            ("knowledgeCategoriesPage", "Categorias da base", "organizar"),
            ("seniorUpdatesPage", "Atualizações Senior", "erp versao"),
            ("maxibotPage", "Maxibot", "assistente bot ia"),
        ],
    },
    {
        "key": "dashes",
        "label": "ConnectMX Dashes",
        "icon": "icon-dna",
        "tone": "customer-dna",
        "items": [
            ("dashesHome", "Painéis do Dashes", "indicadores gestao dna cliente"),
        ],
    },
    {
        "key": "administracao",
        "label": "Administração",
        "icon": "icon-settings",
        "tone": "system",
        "items": [
            ("createUser", "Usuários", "cadastro interno acesso permissao", "admin"),
            ("portalRequesterAdminPage", "Usuários do portal", "solicitante", "admin"),
            ("manageHubTools", "Cadastro do HUB", "ferramentas"),
            ("manageMyHubTools", "Meu HUB", "ferramentas pessoais"),
            ("systemSettingsPage", "Configurações", "sistema"),
            ("serviceAgentPage", "Serviços", "agente"),
            ("dataModelerPage", "Modelagem BD", "banco de dados"),
        ],
    },
]


def _is_admin(user):
    return bool(
        getattr(user, "is_system_admin", False) or getattr(user, "is_superuser", False)
    )


def _build_item(entry, user):
    """Resolve um item do catálogo, ou None se ele não se aplica ao usuário."""
    url_name, label = entry[0], entry[1]
    keywords = entry[2] if len(entry) > 2 else ""
    requires = entry[3] if len(entry) > 3 else None
    icon = entry[4] if len(entry) > 4 else None

    if requires == "admin" and not _is_admin(user):
        return None

    try:
        url = reverse(url_name)
    except NoReverseMatch:
        # Rota removida do projeto: some do menu em vez de derrubar a página.
        return None

    return {
        "url_name": url_name,
        "label": label,
        "url": url,
        "keywords": keywords,
        "icon": icon,
    }


def build_menu(user):
    """Grupos do menu já resolvidos e filtrados para este usuário."""
    groups = []
    for group in NAV_GROUPS:
        items = [item for item in (_build_item(entry, user) for entry in group["items"]) if item]
        if not items:
            continue
        groups.append(
            {
                "key": group["key"],
                "label": group["label"],
                "icon": group["icon"],
                "tone": group["tone"],
                "flat": group.get("flat", False),
                "items": items,
            }
        )
    return groups


def flatten(groups):
    """Lista plana para o command palette, cada item com o grupo de origem."""
    destinations = []
    for group in groups:
        for item in group["items"]:
            destinations.append(
                {
                    "label": item["label"],
                    "url": item["url"],
                    "url_name": item["url_name"],
                    "group": group["label"],
                    "keywords": item["keywords"],
                }
            )
    return destinations


def destination_by_url_name(groups, url_name):
    for group in groups:
        for item in group["items"]:
            if item["url_name"] == url_name:
                return {**item, "group": group["label"]}
    return None


def known_url_names():
    """Todos os url_names do catálogo, sem filtro de usuário."""
    return {entry[0] for group in NAV_GROUPS for entry in group["items"]}
