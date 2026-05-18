from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_user_admin_audit_fields"),
        ("tiqueue", "0035_maintenance_module"),
    ]

    operations = [
        migrations.CreateModel(
            name="MyAgendaReminder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=180)),
                ("description", models.TextField(blank=True, null=True)),
                ("reminder_date", models.DateField()),
                ("reminder_time", models.TimeField(blank=True, null=True)),
                (
                    "priority",
                    models.CharField(
                        choices=[("low", "Baixa"), ("medium", "Média"), ("high", "Alta")],
                        default="medium",
                        max_length=10,
                    ),
                ),
                ("is_done", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agenda_reminders",
                        to="accounts.user",
                    ),
                ),
            ],
            options={"ordering": ["reminder_date", "reminder_time", "id"]},
        ),
    ]
