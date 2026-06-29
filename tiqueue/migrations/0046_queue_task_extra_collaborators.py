from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_user_id_erp"),
        ("tiqueue", "0045_userqueuecustomcolumn_color_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="concludedtasks",
            name="extra_collaborators",
            field=models.ManyToManyField(blank=True, related_name="extra_concluded_queue_collaborations", to="accounts.user"),
        ),
        migrations.AddField(
            model_name="userqueue",
            name="extra_collaborators",
            field=models.ManyToManyField(blank=True, related_name="extra_queue_collaborations", to="accounts.user"),
        ),
    ]
