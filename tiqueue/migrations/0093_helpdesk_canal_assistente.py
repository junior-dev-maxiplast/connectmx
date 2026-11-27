"""Helpdesk medido no Dashes.

- `PortalDemand.opened_via`: por onde o chamado entrou (formulário, conversa com
  o assistente, atendente em nome de alguém ou histórico do SM). Os chamados
  importados do SM já saem marcados.
- `PortalAssistantConversation`: rastro mínimo de cada conversa com o
  assistente de abertura, para medir quantas resolvem sem chamado.
- Painel "Helpdesk" no catálogo do Dashes, primeiro da lista (é a tela inicial),
  liberado para administradores e atendentes da fila de TI.
- Histórico do chamado ganha os eventos "Avaliação" e "Documentação na base".
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

HELPDESK_DASHBOARD = {
    "slug": "helpdesk",
    "name": "Painel do Helpdesk",
    "description": "Chamados, SLA, avaliações, assistente de IA e base de conhecimento",
    "url_name": "dashesHelpdeskPage",
    "display_order": 0,
    "icon_path": (
        '<path d="M4 13a8 8 0 0 1 16 0"></path>'
        '<rect x="3" y="13" width="4" height="6" rx="1.5"></rect>'
        '<rect x="17" y="13" width="4" height="6" rx="1.5"></rect>'
        '<path d="M19 19c0 1.5-2 2.5-5 2.5"></path>'
    ),
}


def mark_sm_and_seed(apps, schema_editor):
    PortalDemand = apps.get_model("tiqueue", "PortalDemand")
    PortalDemand.objects.filter(sm_ticket_id__isnull=False).update(opened_via="sm")

    Dashboard = apps.get_model("tiqueue", "Dashboard")
    DashboardAccess = apps.get_model("tiqueue", "DashboardAccess")
    User = apps.get_model("accounts", "User")
    dashboard, _ = Dashboard.objects.update_or_create(
        slug=HELPDESK_DASHBOARD["slug"],
        defaults={key: value for key, value in HELPDESK_DASHBOARD.items() if key != "slug"},
    )
    staff = User.objects.filter(is_active=True).filter(
        models.Q(is_system_admin=True) | models.Q(is_superuser=True) | models.Q(is_support_attendant=True)
    )
    for user in staff:
        DashboardAccess.objects.get_or_create(user=user, dashboard=dashboard)


def unseed(apps, schema_editor):
    Dashboard = apps.get_model("tiqueue", "Dashboard")
    Dashboard.objects.filter(slug=HELPDESK_DASHBOARD["slug"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tiqueue', '0092_bigautofield_modelos_novos'),
        ('accounts', '0010_user_is_support_attendant'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='portaldemand',
            name='opened_via',
            field=models.CharField(choices=[('portal', 'Formulário do portal'), ('chat', 'Conversa com o assistente'), ('attendant', 'Aberto pelo atendente'), ('sm', 'Histórico do SM')], db_index=True, default='portal', max_length=12),
        ),
        migrations.CreateModel(
            name='PortalAssistantConversation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('turns', models.PositiveIntegerField(default=0)),
                ('suggested_solution', models.BooleanField(default=False)),
                ('outcome', models.CharField(choices=[('open', 'Sem conclusão'), ('solution', 'Solução sugerida'), ('resolved', 'Resolvida sem chamado'), ('summary', 'Resumo não enviado'), ('ticket', 'Virou chamado')], db_index=True, default='open', max_length=12)),
                ('started_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('last_turn_at', models.DateTimeField(blank=True, null=True)),
                ('company', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assistant_conversations', to='tiqueue.portalcompany')),
                ('demand', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assistant_conversations', to='tiqueue.portaldemand')),
                ('sector', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assistant_conversations', to='tiqueue.portalrequestersector')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='portal_assistant_conversations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Conversa com o assistente',
                'verbose_name_plural': 'Conversas com o assistente',
                'ordering': ['-started_at', '-id'],
            },
        ),
        # Avaliação e documentação na base passam a entrar no histórico do chamado.
        migrations.AlterField(
            model_name='portaldemandlog',
            name='event_type',
            field=models.CharField(choices=[('assumed', 'Assunção'), ('transferred', 'Transferência'), ('worklog', 'Apontamento'), ('waiting', 'Aguardando solicitante'), ('resumed', 'Retomada'), ('completed', 'Conclusão'), ('reopened', 'Reabertura'), ('cancelled', 'Cancelamento'), ('ai_routed', 'Roteamento IA'), ('feedback', 'Avaliação'), ('documented', 'Documentação na base')], max_length=20),
        ),
        migrations.RunPython(mark_sm_and_seed, unseed),
    ]
