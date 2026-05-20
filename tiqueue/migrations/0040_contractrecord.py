from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tiqueue", "0039_data_modeler"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContractRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference_month", models.CharField(blank=True, max_length=20, null=True)),
                ("company", models.CharField(blank=True, max_length=80, null=True)),
                ("cnpj", models.CharField(blank=True, max_length=30, null=True)),
                ("supplier", models.CharField(blank=True, max_length=180, null=True)),
                ("invoice_number", models.CharField(blank=True, max_length=60, null=True)),
                ("issue_date", models.DateField(blank=True, null=True)),
                ("due_date", models.DateField(blank=True, null=True)),
                ("amount", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("item", models.CharField(blank=True, max_length=80, null=True)),
                ("request_code", models.CharField(blank=True, max_length=80, null=True)),
                ("contract_code", models.CharField(blank=True, max_length=80, null=True)),
                ("transaction_type", models.CharField(blank=True, max_length=120, null=True)),
                ("cost_center", models.CharField(blank=True, max_length=160, null=True)),
                ("observation", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-id"],
            },
        ),
    ]
