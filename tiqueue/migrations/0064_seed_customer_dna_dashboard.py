"""Semeia o catálogo do Dashes com o painel que já existia.

Até aqui qualquer usuário autenticado entrava no Dashes. A partir do momento em
que o acesso passa a exigir registro em DashboardAccess, ninguém entraria — por
isso a migration já concede o DNA do Cliente aos administradores do sistema,
que ficam com a chave para liberar os demais pela tela de Cadastro interno.
"""

from django.db import migrations


CUSTOMER_DNA = {
    "slug": "customer-dna",
    "name": "DNA do Cliente",
    "description": "Visão 360º comercial",
    "url_name": "dashesCustomerDnaPage",
    "icon_path": (
        '<path d="M12 3a4 4 0 0 0-4 4c0 1.2.5 2.3 1.3 3A5 5 0 0 0 5 15v2a4 4 0 0 0 4 4h6a4 4 0 0 0 4-4v-2'
        'a5 5 0 0 0-4.3-5A4 4 0 0 0 12 3Z"></path><path d="M9 14h6M12 11v6"></path>'
    ),
    "display_order": 1,
}


def seed(apps, schema_editor):
    Dashboard = apps.get_model("tiqueue", "Dashboard")
    DashboardAccess = apps.get_model("tiqueue", "DashboardAccess")
    User = apps.get_model("accounts", "User")

    dashboard, _created = Dashboard.objects.get_or_create(
        slug=CUSTOMER_DNA["slug"],
        defaults={key: value for key, value in CUSTOMER_DNA.items() if key != "slug"},
    )

    admins = User.objects.filter(is_system_admin=True) | User.objects.filter(is_superuser=True)
    for user in admins.distinct():
        DashboardAccess.objects.get_or_create(user=user, dashboard=dashboard)


def unseed(apps, schema_editor):
    Dashboard = apps.get_model("tiqueue", "Dashboard")
    Dashboard.objects.filter(slug=CUSTOMER_DNA["slug"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0063_dashboard_dashboardaccess_and_more"),
        ("accounts", "0007_user_can_access_internal"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
