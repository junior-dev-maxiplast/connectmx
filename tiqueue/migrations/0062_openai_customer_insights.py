from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0061_customerinsightsnapshot"),
    ]

    operations = [
        migrations.AddField(model_name="systemconfig", name="openai_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="systemconfig", name="openai_api_key_encrypted", field=models.TextField(blank=True, null=True)),
        migrations.AddField(model_name="systemconfig", name="openai_base_url", field=models.CharField(default="https://api.openai.com/v1", max_length=255)),
        migrations.AddField(model_name="systemconfig", name="openai_model", field=models.CharField(default="gpt-5.6-sol", max_length=80)),
        migrations.AddField(model_name="systemconfig", name="openai_reasoning_effort", field=models.CharField(default="medium", max_length=20)),
        migrations.AddField(model_name="systemconfig", name="openai_timeout_sec", field=models.PositiveIntegerField(default=120)),
        migrations.AddField(model_name="systemconfig", name="openai_max_output_tokens", field=models.PositiveIntegerField(default=5000)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_response", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_provider", field=models.CharField(blank=True, max_length=40, null=True)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_model", field=models.CharField(blank=True, max_length=80, null=True)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_response_id", field=models.CharField(blank=True, max_length=120, null=True)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_input_tokens", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_output_tokens", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_total_tokens", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_error", field=models.TextField(blank=True, null=True)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_requested_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="customerinsightsnapshot", name="ai_completed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(
            model_name="customerinsightsnapshot",
            name="status",
            field=models.CharField(
                choices=[
                    ("prepared", "Preparado"),
                    ("processing", "Processando na IA"),
                    ("sent", "Enviado para IA"),
                    ("completed", "Concluído"),
                    ("error", "Erro"),
                ],
                default="prepared",
                max_length=20,
            ),
        ),
    ]
