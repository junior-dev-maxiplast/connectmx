from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_id_sm"),
        ("tiqueue", "0023_queue_project_link"),
    ]

    operations = [
        migrations.CreateModel(
            name="KnowledgeCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=90, unique=True)),
                ("description", models.TextField(blank=True, null=True)),
                ("sort_order", models.IntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["sort_order", "name", "id"],
            },
        ),
        migrations.CreateModel(
            name="KnowledgeEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=180)),
                ("trigger", models.TextField(help_text="Situacao/problema que gerou a anotacao.")),
                ("description", models.TextField(help_text="Descricao detalhada do problema.")),
                ("impact", models.TextField(blank=True, null=True)),
                ("workaround", models.TextField(blank=True, null=True)),
                ("root_cause", models.TextField(blank=True, null=True)),
                ("resolution", models.TextField(blank=True, null=True)),
                ("tags", models.CharField(blank=True, max_length=220, null=True)),
                ("is_resolved", models.BooleanField(default=False)),
                ("inserted_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entries", to="tiqueue.knowledgecategory")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="accounts.user")),
            ],
            options={
                "ordering": ["-inserted_at", "-id"],
            },
        ),
    ]

