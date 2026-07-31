from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0057_portalrequestercollaborator_portalrequestersector_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectMilestone",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=180)),
                ("description", models.TextField(blank=True, null=True)),
                ("target_date", models.DateField(blank=True, null=True)),
                ("color", models.CharField(default="#5CD6A3", max_length=7)),
                ("is_done", models.BooleanField(default=False)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("sort_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="milestones",
                        to="tiqueue.project",
                    ),
                ),
            ],
            options={
                "ordering": ["sort_order", "target_date", "id"],
            },
        ),
    ]
