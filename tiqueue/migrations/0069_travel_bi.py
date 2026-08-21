import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


DASHBOARD = {
    "slug": "viagens",
    "name": "BI de Viagens",
    "description": "Frota, quilometragem e adiantamentos",
    "url_name": "dashesTravelBiPage",
    "display_order": 30,
    "is_active": True,
    "icon_path": (
        '<path d="M3 16V8a1 1 0 0 1 1-1h9v9H3z"/>'
        '<path d="M13 10h4l3 3v3h-7v-6z"/>'
        '<circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/>'
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
        ("tiqueue", "0068_remove_itbiinsightsnapshot_unique_it_bi_insight_scope_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TravelBiInsightSnapshot",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period_key", models.CharField(max_length=8)),
                ("carrier_key", models.CharField(default="all", max_length=20)),
                ("situation_key", models.CharField(default="all", max_length=8)),
                ("scope_label", models.CharField(blank=True, max_length=160, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("prepared", "Preparado"),
                            ("processing", "Processando na IA"),
                            ("completed", "Concluído"),
                            ("error", "Erro"),
                        ],
                        default="prepared",
                        max_length=20,
                    ),
                ),
                ("source_fingerprint", models.CharField(db_index=True, max_length=64)),
                ("metrics", models.JSONField(default=dict)),
                ("ai_payload", models.JSONField(default=dict)),
                ("ai_response", models.JSONField(blank=True, default=dict)),
                ("ai_model", models.CharField(blank=True, max_length=80, null=True)),
                ("ai_response_id", models.CharField(blank=True, max_length=120, null=True)),
                ("ai_input_tokens", models.PositiveIntegerField(default=0)),
                ("ai_output_tokens", models.PositiveIntegerField(default=0)),
                ("ai_total_tokens", models.PositiveIntegerField(default=0)),
                ("ai_error", models.TextField(blank=True, null=True)),
                ("ai_requested_at", models.DateTimeField(blank=True, null=True)),
                ("ai_completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="travel_bi_insight_snapshots",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="travelbiinsightsnapshot",
            constraint=models.UniqueConstraint(
                fields=("period_key", "carrier_key", "situation_key"),
                name="unique_travel_bi_insight_scope",
            ),
        ),
        migrations.RunPython(create_dashboard, remove_dashboard),
    ]
