from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0030_userqueue_notes"),
    ]

    operations = [
        migrations.AddField(
            model_name="userqueue",
            name="kanban_sort_order",
            field=models.IntegerField(default=0),
        ),
    ]

