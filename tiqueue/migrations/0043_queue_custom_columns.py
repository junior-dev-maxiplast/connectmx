from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0042_queue_priority_effort"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserQueueCustomColumn",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=60)),
                ("sort_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="queue_custom_columns", to="accounts.user")),
            ],
            options={
                "ordering": ["sort_order", "id"],
                "unique_together": {("user", "name")},
            },
        ),
        migrations.CreateModel(
            name="UserQueueCustomValue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.CharField(blank=True, max_length=250, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("column", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="values", to="tiqueue.userqueuecustomcolumn")),
                ("queue_item", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="custom_values", to="tiqueue.userqueue")),
            ],
            options={
                "unique_together": {("queue_item", "column")},
            },
        ),
    ]
