from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("tiqueue", "0047_alter_contractrecord_id_alter_datamodelfield_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectroadmapitem",
            name="responsible",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="roadmap_responsibilities",
                to="accounts.user",
            ),
        ),
    ]
