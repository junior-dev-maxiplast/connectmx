from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0040_contractrecord"),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_key", models.CharField(max_length=80, unique=True)),
                ("title", models.CharField(max_length=180)),
                ("message", models.TextField()),
                ("level", models.CharField(choices=[("info", "Info"), ("warning", "Aviso"), ("error", "Erro")], default="warning", max_length=10)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-updated_at", "-id"]},
        ),
    ]
