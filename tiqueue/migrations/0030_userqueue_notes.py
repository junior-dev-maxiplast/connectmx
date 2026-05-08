from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0029_userqueue_demand_detail"),
    ]

    operations = [
        migrations.AddField(
            model_name="userqueue",
            name="a_notes",
            field=models.TextField(blank=True, null=True),
        ),
    ]

