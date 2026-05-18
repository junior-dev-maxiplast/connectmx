from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0033_project_developer"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemconfig",
            name="service_agent_timeout_sec",
            field=models.PositiveIntegerField(default=8),
        ),
        migrations.AddField(
            model_name="systemconfig",
            name="service_agent_token",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="systemconfig",
            name="service_agent_url",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
