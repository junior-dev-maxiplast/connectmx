from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0028_system_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="userqueue",
            name="a_demand_detail",
            field=models.TextField(blank=True, null=True),
        ),
    ]

