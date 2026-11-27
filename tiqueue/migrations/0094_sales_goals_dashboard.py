"""Painel "Metas de Vendas" no catálogo do Dashes.

Grade de metas de peso (kg) por família e mês que grava na USU_TORCMET do ERP
Senior (`tiqueue/sales_goals.py`). Liberado para os administradores; os demais
pela tela de Cadastro interno, como os outros painéis. Sem tabela nova: as
metas ficam no ERP.
"""

from django.db import migrations

DASHBOARD = {
    "slug": "sales-goals",
    "name": "Metas de Vendas",
    "description": "Metas de peso (kg) por família e mês, gravadas no ERP",
    "url_name": "dashesSalesGoalsPage",
    "display_order": 11,
    "icon_path": '<circle cx="12" cy="12" r="8"></circle><circle cx="12" cy="12" r="4.5"></circle><circle cx="12" cy="12" r="1"></circle>',
}


def seed(apps, schema_editor):
    Dashboard = apps.get_model("tiqueue", "Dashboard")
    DashboardAccess = apps.get_model("tiqueue", "DashboardAccess")
    User = apps.get_model("accounts", "User")
    dashboard, _ = Dashboard.objects.update_or_create(
        slug=DASHBOARD["slug"], defaults={key: value for key, value in DASHBOARD.items() if key != "slug"}
    )
    admins = (User.objects.filter(is_system_admin=True) | User.objects.filter(is_superuser=True)).distinct()
    for user in admins:
        DashboardAccess.objects.get_or_create(user=user, dashboard=dashboard)


def unseed(apps, schema_editor):
    Dashboard = apps.get_model("tiqueue", "Dashboard")
    Dashboard.objects.filter(slug=DASHBOARD["slug"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0093_helpdesk_canal_assistente"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
