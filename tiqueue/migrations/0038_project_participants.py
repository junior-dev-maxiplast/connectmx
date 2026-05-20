from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_user_admin_audit_fields"),
        ("tiqueue", "0037_agenda_reminder_color"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="participants",
            field=models.ManyToManyField(blank=True, related_name="project_participations", to="accounts.user"),
        ),
    ]
