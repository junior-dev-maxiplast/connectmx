"""Semeia as empresas do grupo que hoje vivem no SM (tabela `organization`).

Os ids abaixo sao os mesmos `organization_id` usados em
`n8n/query_para_colar_no_node.sql` e `tiqueue/it_bi.py` (COMPANY_NAMES) para
mapear os chamados do SM por empresa. Guardar aqui o mesmo id evita ter que
casar por nome (frágil) quando os chamados do SM forem importados.

A organizacao 6 (Maxiplast-BigBag) foi confirmada direto na consulta ao SM
durante a Fase 2 — nao estava na lista inicial de 4 empresas, mas tem chamado
aberto de verdade, entao entra junto.
"""

from django.db import migrations


COMPANIES = [
    (2, "Magnaplast"),
    (3, "Matriz"),
    (4, "Ráfia"),
    (5, "Serafina"),
    (6, "BigBag"),
]


def seed(apps, schema_editor):
    PortalCompany = apps.get_model("tiqueue", "PortalCompany")
    for sm_organization_id, name in COMPANIES:
        PortalCompany.objects.get_or_create(
            sm_organization_id=sm_organization_id,
            defaults={"name": name},
        )


def unseed(apps, schema_editor):
    PortalCompany = apps.get_model("tiqueue", "PortalCompany")
    PortalCompany.objects.filter(sm_organization_id__in=[sm_id for sm_id, _ in COMPANIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tiqueue', '0076_portalcompany_portaldemand_company'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
