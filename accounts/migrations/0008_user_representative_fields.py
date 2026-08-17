from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_user_can_access_internal"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_representative",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="representative_code",
            field=models.CharField(blank=True, db_index=True, max_length=30, null=True),
        ),
    ]
