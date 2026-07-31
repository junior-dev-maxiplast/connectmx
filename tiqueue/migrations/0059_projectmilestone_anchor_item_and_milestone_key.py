from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0058_projectmilestone"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectmilestone",
            name="anchor_item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attached_milestones",
                to="tiqueue.projectroadmapitem",
            ),
        ),
        migrations.AddField(
            model_name="projectmilestone",
            name="milestone_key",
            field=models.CharField(
                choices=[
                    ("planning", "Planejamento"),
                    ("analysis", "Analise"),
                    ("development", "Desenvolvimento"),
                    ("validation", "Validacao interna"),
                    ("homologation", "Homologacao"),
                    ("deployment", "Implantacao"),
                    ("delivery", "Entrega"),
                ],
                default="analysis",
                max_length=40,
            ),
        ),
    ]
