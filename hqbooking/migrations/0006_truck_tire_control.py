from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("hqbooking", "0005_lunch_reservations"),
    ]

    operations = [
        migrations.CreateModel(
            name="Truck",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("identifier", models.CharField(max_length=80, unique=True)),
                ("tire_count", models.PositiveIntegerField(default=6)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["identifier", "id"]},
        ),
        migrations.CreateModel(
            name="TruckTireChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tire_number", models.PositiveIntegerField()),
                ("changed_on", models.DateField(blank=True, null=True)),
                ("odometer_km", models.PositiveIntegerField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "truck",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tire_changes", to="hqbooking.truck"),
                ),
            ],
            options={"ordering": ["tire_number", "id"], "unique_together": {("truck", "tire_number")}},
        ),
    ]
