from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_user_admin_audit_fields"),
        ("tiqueue", "0032_demand_templates"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="developer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="projects",
                to="accounts.user",
            ),
        ),
    ]
