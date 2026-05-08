from django.db import migrations, models


def mark_superusers_as_admin(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(is_superuser=True).update(is_system_admin=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_id_sm"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_system_admin",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="last_access_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="last_data_change_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_superusers_as_admin, migrations.RunPython.noop),
    ]
