from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0026_user_queue_kanban"),
    ]

    operations = [
        migrations.AddField(
            model_name="seniorsystemupdate",
            name="folder_name",
            field=models.CharField(blank=True, max_length=180, null=True),
        ),
        migrations.AddField(
            model_name="seniorsystemupdate",
            name="sent_to_drive",
            field=models.BooleanField(default=False),
        ),
    ]

