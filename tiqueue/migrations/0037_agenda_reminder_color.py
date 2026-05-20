from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0036_my_agenda_reminder"),
    ]

    operations = [
        migrations.AddField(
            model_name="myagendareminder",
            name="color",
            field=models.CharField(blank=True, max_length=7, null=True),
        ),
    ]
