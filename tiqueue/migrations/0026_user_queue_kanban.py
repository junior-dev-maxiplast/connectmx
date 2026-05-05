from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_id_sm"),
        ("tiqueue", "0025_knowledge_attachments"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserQueueKanbanColumn",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80)),
                ("color", models.CharField(default="#343955", max_length=7)),
                ("sort_order", models.IntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="queue_kanban_columns", to="accounts.user")),
            ],
            options={
                "ordering": ["sort_order", "id"],
                "unique_together": {("user", "name")},
            },
        ),
        migrations.AddField(
            model_name="userqueue",
            name="kanban_column",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="queue_items", to="tiqueue.userqueuekanbancolumn"),
        ),
    ]

