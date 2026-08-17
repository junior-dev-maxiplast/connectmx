from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tiqueue", "0060_pomodorosession"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerInsightSnapshot",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("customer_code", models.BigIntegerField(db_index=True)),
                ("customer_name", models.CharField(max_length=240)),
                ("status", models.CharField(choices=[("prepared", "Preparado"), ("sent", "Enviado para IA"), ("error", "Erro")], default="prepared", max_length=20)),
                ("source_period_start", models.DateField(blank=True, null=True)),
                ("source_period_end", models.DateField(blank=True, null=True)),
                ("source_fingerprint", models.CharField(db_index=True, max_length=64)),
                ("source_row_count", models.PositiveIntegerField(default=0)),
                ("metrics", models.JSONField(default=dict)),
                ("insight_cards", models.JSONField(default=list)),
                ("ai_payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="customer_insight_snapshots", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="customerinsightsnapshot",
            constraint=models.UniqueConstraint(fields=("customer_code", "source_fingerprint"), name="unique_customer_insight_source"),
        ),
    ]
