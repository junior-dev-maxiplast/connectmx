from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0027_seniorsystemupdate_drive_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("system_version", models.CharField(blank=True, max_length=40, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]

