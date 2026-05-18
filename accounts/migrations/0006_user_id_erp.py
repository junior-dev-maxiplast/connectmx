from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_user_admin_audit_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="id_erp",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
