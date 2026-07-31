from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("tiqueue", "0048_projectroadmapitem_responsible"),
    ]

    operations = [
        migrations.CreateModel(
            name="MaxiTetrisHighScore",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("best_score", models.PositiveIntegerField(default=0)),
                ("best_lines", models.PositiveIntegerField(default=0)),
                ("best_level", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="maxi_tetris_highscore",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "ordering": ["-best_score", "-best_lines", "-best_level", "id"],
            },
        ),
    ]
