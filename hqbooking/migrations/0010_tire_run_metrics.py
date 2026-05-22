from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hqbooking", "0009_truck_model_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="trucktirechangehistory",
            name="previous_changed_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trucktirechangehistory",
            name="previous_odometer_km",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trucktirechangehistory",
            name="previous_tire_code",
            field=models.CharField(blank=True, max_length=12, null=True),
        ),
        migrations.AddField(
            model_name="trucktirechangehistory",
            name="run_days",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trucktirechangehistory",
            name="run_km",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
