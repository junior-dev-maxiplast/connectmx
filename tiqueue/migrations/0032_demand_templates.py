from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0031_userqueue_kanban_sort_order"),
    ]

    operations = [
        migrations.CreateModel(
            name="DemandTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("description", models.CharField(blank=True, max_length=240, null=True)),
                ("predicted_start_offset_hours", models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ("predicted_end_offset_hours", models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "linked_project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="demand_templates",
                        to="tiqueue.project",
                    ),
                ),
                (
                    "task_group",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="demand_templates",
                        to="tiqueue.taskgroup",
                    ),
                ),
                (
                    "task_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="demand_templates",
                        to="tiqueue.tasktype",
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="DemandTemplateDetail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("description", models.CharField(max_length=240)),
                ("sort_order", models.IntegerField(default=0)),
                (
                    "template",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="details", to="tiqueue.demandtemplate"),
                ),
            ],
            options={"ordering": ["sort_order", "id"]},
        ),
    ]
