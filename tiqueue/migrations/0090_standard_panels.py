"""Painéis-padrão animados: Vendas, Contas a Receber, Entregas (OTIF) e Qualidade.

Registra os quatro no catálogo do Dashes, liberados só para os administradores
(os demais pela tela de Cadastro interno). Sem tabela nova: os dados vêm do ERP.
"""

from django.db import migrations

DASHBOARDS = [
    {"slug": "sales-panel", "name": "Painel de Vendas", "description": "Dia, mês e ano, pedidos e meta",
     "url_name": "dashesSalesPanel", "display_order": 7,
     "icon_path": '<path d="M4 19h16M6 16V9M11 16V5M16 16v-5"></path>'},
    {"slug": "receivables-panel", "name": "Contas a Receber", "description": "Aging, inadimplência e recebimentos",
     "url_name": "dashesReceivablesPanel", "display_order": 8,
     "icon_path": '<rect x="3" y="6" width="18" height="12" rx="2"></rect><circle cx="12" cy="12" r="2.5"></circle><path d="M7 9v6M17 9v6"></path>'},
    {"slug": "service-panel", "name": "Entregas (OTIF)", "description": "No prazo, completo e carteira atrasada",
     "url_name": "dashesServicePanel", "display_order": 9,
     "icon_path": '<path d="M3 7h11v9H3zM14 10h4l3 3v3h-7z"></path><circle cx="7" cy="17.5" r="1.6"></circle><circle cx="17" cy="17.5" r="1.6"></circle>'},
    {"slug": "quality-panel", "name": "Qualidade", "description": "Reclamações, devoluções e causas",
     "url_name": "dashesQualityPanel", "display_order": 10,
     "icon_path": '<path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6l-7-3Z"></path><path d="m9 12 2 2 4-4"></path>'},
]


def seed(apps, schema_editor):
    Dashboard = apps.get_model("tiqueue", "Dashboard")
    DashboardAccess = apps.get_model("tiqueue", "DashboardAccess")
    User = apps.get_model("accounts", "User")
    admins = (User.objects.filter(is_system_admin=True) | User.objects.filter(is_superuser=True)).distinct()
    for item in DASHBOARDS:
        dashboard, _ = Dashboard.objects.update_or_create(
            slug=item["slug"], defaults={key: value for key, value in item.items() if key != "slug"}
        )
        for user in admins:
            DashboardAccess.objects.get_or_create(user=user, dashboard=dashboard)


def unseed(apps, schema_editor):
    Dashboard = apps.get_model("tiqueue", "Dashboard")
    Dashboard.objects.filter(slug__in=[item["slug"] for item in DASHBOARDS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_user_can_access_internal'),
        ('tiqueue', '0089_management_agenda'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
