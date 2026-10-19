import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


DASHBOARD = {
    "slug": "compras",
    "name": "BI de Compras",
    "description": "Prazo de pagamento, entrega e variação de preço",
    "url_name": "dashesComprasBiPage",
    "display_order": 50,
    "is_active": True,
    "icon_path": (
        '<path d="M4 7h16l-1.5 11a2 2 0 0 1-2 1.8H7.5a2 2 0 0 1-2-1.8L4 7z"/>'
        '<path d="M8 7V6a4 4 0 0 1 8 0v1"/>'
        '<path d="M9 11v2M15 11v2"/>'
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
        ("tiqueue", "0083_taskgroup_is_active_tasktype_is_active"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ComprasBiInsightSnapshot",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("view_key", models.CharField(default="pagamento", max_length=20)),
                ("period_key", models.CharField(max_length=20)),
                ("fornecedor_key", models.CharField(default="all", max_length=20)),
                ("filial_key", models.CharField(default="all", max_length=20)),
                ("familia_key", models.CharField(default="all", max_length=20)),
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
                        related_name="compras_bi_insight_snapshots",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="comprasbiinsightsnapshot",
            constraint=models.UniqueConstraint(
                fields=("view_key", "period_key", "fornecedor_key", "filial_key", "familia_key"),
                name="unique_compras_bi_insight_scope",
            ),
        ),
        migrations.RunPython(create_dashboard, remove_dashboard),
    ]
