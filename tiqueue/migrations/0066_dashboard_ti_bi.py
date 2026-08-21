from django.db import migrations


DASHBOARD = {
    "slug": "ti-bi",
    "name": "BI do TI",
    "description": "Indicadores do helpdesk",
    "url_name": "dashesItBiPage",
    "display_order": 20,
    "is_active": True,
    "icon_path": (
        '<path d="M4 19h16"/><rect x="6" y="11" width="3" height="6" rx="1"/>'
        '<rect x="11" y="7" width="3" height="10" rx="1"/>'
        '<rect x="16" y="13" width="3" height="4" rx="1"/>'
    ),
}


def create_dashboard(apps, schema_editor):
    """Registra o painel no catálogo — é ele que monta a sidebar e as permissões."""
    Dashboard = apps.get_model("tiqueue", "Dashboard")
    Dashboard.objects.update_or_create(slug=DASHBOARD["slug"], defaults=DASHBOARD)


def remove_dashboard(apps, schema_editor):
    Dashboard = apps.get_model("tiqueue", "Dashboard")
    Dashboard.objects.filter(slug=DASHBOARD["slug"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0065_customerinsightsnapshot_member_customer_id_and_more"),
    ]

    operations = [
        migrations.RunPython(create_dashboard, remove_dashboard),
    ]
