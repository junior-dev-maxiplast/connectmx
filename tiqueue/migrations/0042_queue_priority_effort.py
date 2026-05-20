from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0041_systemnotification"),
    ]

    operations = [
        migrations.AddField(
            model_name="userqueue",
            name="estimated_effort_level",
            field=models.CharField(
                choices=[("small", "Pequeno"), ("medium", "Medio"), ("large", "Grande")],
                default="medium",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="userqueue",
            name="priority_level",
            field=models.CharField(
                choices=[("low", "Baixa"), ("medium", "Media"), ("high", "Alta")],
                default="medium",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="concludedtasks",
            name="estimated_effort_level",
            field=models.CharField(
                choices=[("small", "Pequeno"), ("medium", "Medio"), ("large", "Grande")],
                default="medium",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="concludedtasks",
            name="priority_level",
            field=models.CharField(
                choices=[("low", "Baixa"), ("medium", "Media"), ("high", "Alta")],
                default="medium",
                max_length=10,
            ),
        ),
    ]
