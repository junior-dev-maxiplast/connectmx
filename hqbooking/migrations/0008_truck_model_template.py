from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hqbooking", "0007_tire_history_and_layout"),
    ]

    operations = [
        migrations.CreateModel(
            name="TruckModelTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("axle_count", models.PositiveIntegerField(default=1)),
                ("wheel_count", models.PositiveIntegerField(default=2)),
                ("structure_json", models.TextField(default="[]")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name", "id"]},
        ),
    ]
