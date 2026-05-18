from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("hqbooking", "0006_truck_tire_control"),
    ]

    operations = [
        migrations.AddField(
            model_name="truck",
            name="layout_model",
            field=models.CharField(default="BASCULANTE_10", max_length=30),
        ),
        migrations.AddField(
            model_name="trucktirechange",
            name="note",
            field=models.CharField(blank=True, max_length=180, null=True),
        ),
        migrations.AddField(
            model_name="trucktirechange",
            name="tire_code",
            field=models.CharField(blank=True, max_length=12, null=True),
        ),
        migrations.CreateModel(
            name="TruckTireChangeHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tire_number", models.PositiveIntegerField()),
                ("tire_code", models.CharField(blank=True, max_length=12, null=True)),
                ("changed_on", models.DateField(blank=True, null=True)),
                ("odometer_km", models.PositiveIntegerField(blank=True, null=True)),
                ("note", models.CharField(blank=True, max_length=180, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "truck",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tire_history", to="hqbooking.truck"),
                ),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
