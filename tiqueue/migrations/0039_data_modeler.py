from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_user_admin_audit_fields"),
        ("tiqueue", "0038_project_participants"),
    ]

    operations = [
        migrations.CreateModel(
            name="DataModelLaunch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=140, unique=True)),
                ("description", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="accounts.user"),
                ),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="DataModelTable",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=140)),
                ("x", models.IntegerField(default=40)),
                ("y", models.IntegerField(default=40)),
                ("color", models.CharField(default="#343955", max_length=7)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "launch",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tables", to="tiqueue.datamodellaunch"),
                ),
            ],
            options={"ordering": ["id"], "unique_together": {("launch", "name")}},
        ),
        migrations.CreateModel(
            name="DataModelField",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "data_type",
                    models.CharField(
                        choices=[
                            ("int", "INT"),
                            ("bigint", "BIGINT"),
                            ("varchar", "VARCHAR"),
                            ("text", "TEXT"),
                            ("date", "DATE"),
                            ("datetime", "DATETIME"),
                            ("bool", "BOOLEAN"),
                            ("decimal", "DECIMAL"),
                            ("float", "FLOAT"),
                            ("json", "JSON"),
                        ],
                        default="varchar",
                        max_length=20,
                    ),
                ),
                ("name", models.CharField(max_length=140)),
                ("size", models.CharField(blank=True, max_length=30, null=True)),
                ("is_primary", models.BooleanField(default=False)),
                ("is_nullable", models.BooleanField(default=True)),
                ("sort_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "table",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fields", to="tiqueue.datamodeltable"),
                ),
            ],
            options={"ordering": ["sort_order", "id"], "unique_together": {("table", "name")}},
        ),
        migrations.CreateModel(
            name="DataModelRelation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("relation_type", models.CharField(choices=[("1:1", "1:1"), ("1:N", "1:N"), ("N:1", "N:1"), ("N:N", "N:N")], default="1:N", max_length=3)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "launch",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="relations", to="tiqueue.datamodellaunch"),
                ),
                (
                    "source_field",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="source_relations", to="tiqueue.datamodelfield"),
                ),
                (
                    "target_field",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="target_relations", to="tiqueue.datamodelfield"),
                ),
            ],
            options={"ordering": ["id"]},
        ),
    ]
