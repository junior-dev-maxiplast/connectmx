from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0022_my_hub_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="concludedtasks",
            name="linked_project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="concluded_queue_items",
                to="tiqueue.project",
            ),
        ),
        migrations.AddField(
            model_name="concludedtasks",
            name="linked_roadmap_item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="concluded_queue_items",
                to="tiqueue.projectroadmapitem",
            ),
        ),
        migrations.AddField(
            model_name="userqueue",
            name="linked_project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="queue_items",
                to="tiqueue.project",
            ),
        ),
        migrations.AddField(
            model_name="userqueue",
            name="linked_roadmap_item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="queue_items",
                to="tiqueue.projectroadmapitem",
            ),
        ),
    ]

