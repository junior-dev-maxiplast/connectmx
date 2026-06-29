from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0043_queue_custom_columns"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserQueueSavedView",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80)),
                ("filters_json", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="queue_saved_views",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "ordering": ["name", "id"],
                "unique_together": {("user", "name")},
            },
        ),
    ]
