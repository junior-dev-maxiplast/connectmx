from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0024_knowledge_base"),
    ]

    operations = [
        migrations.CreateModel(
            name="KnowledgeEntryAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="knowledge_base/")),
                ("original_name", models.CharField(blank=True, max_length=255, null=True)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("entry", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="tiqueue.knowledgeentry")),
            ],
            options={
                "ordering": ["-uploaded_at", "-id"],
            },
        ),
    ]

