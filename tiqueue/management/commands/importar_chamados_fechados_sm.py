"""
Importa para o ConnectMX Helpdesk os chamados REALMENTE fechados no SM
(status 5 — "Fechado"), como registros completos de `PortalDemand`.

    python manage.py importar_chamados_fechados_sm            so mostra o relatorio (nao grava nada)
    python manage.py importar_chamados_fechados_sm --aplicar   grava de verdade

Irmao de `importar_chamados_sm` (que traz os abertos) — mesma logica de
casamento de solicitante/atendente por `accounts.User.id_sm`, mesmo jeito de
criar Grupo/Tipo que faltar, mesma idempotencia via `sm_ticket_id`. Só muda o
que é específico de "isso já aconteceu e já terminou": status fixo
(Concluída), `completed_at` vindo de `date_close`, e um log de conclusão além
do de assunção.

Por que só status 5, e não 6 (Lixeira) ou 13 (Mesclado): mesma decisão já
tomada em `snapshotar_historico_sm` — Lixeira não é trabalho concluído, e
Mesclado já teve o conteúdo absorvido por outro chamado (contar os dois
duplicaria o mesmo atendimento). Os ~10 mil fechados que o snapshot da Fase 3
mede são exatamente este status 5.

Chamado que foi importado ABERTO antes e fechou no SM depois não é tocado por
este comando: ele já tem `sm_ticket_id` preenchido, então entra na lista de
"já importados" e fica de fora — o status dele no ConnectMX segue sendo
responsabilidade de quem está atendendo agora, não deste import. Rode
`conferir_corte_sm` para ver esses casos.

Decisoes herdadas do importador de abertos (mesmo raciocinio, ver o docstring
de lá para mais detalhes):
- Grupo/Tipo: casa por nome, cria se não existir.
- SLA: não recalculado — chamado fechado não tem prazo pendente mesmo.
- Tempo apontado: uma mensagem de sistema com o total (`total_attending_time`),
  não um apontamento estruturado — o SM não guarda sessão por sessão.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from tiqueue.it_bi import _query
from tiqueue.models import (
    PortalCompany,
    PortalDemand,
    PortalDemandLog,
    PortalDemandMessage,
    PortalRequesterAccount,
    TaskGroup,
    TaskType,
    userQueue,
)
from tiqueue.views import _portal_log_event, _queue_collaborator_display_name

# Só "Fechado" de verdade — ver docstring do módulo.
STATUS_FECHADO = 5

PRIORITY_MAP = {
    "Alta": userQueue.PRIORITY_HIGH,
    "Média": userQueue.PRIORITY_MEDIUM,
    "Baixa": userQueue.PRIORITY_LOW,
}

SM_QUERY = f"""
    SELECT
        A.id                    AS sm_id,
        A.date_open             AS date_open,
        A.date_close            AS date_close,
        A.status_id             AS status_id,
        S.name                  AS status_name,
        A.priority_id           AS priority_id,
        P.name                  AS priority_name,
        A.subject               AS subject,
        A.description_txt       AS description_txt,
        A.organization_id       AS organization_id,
        A.type_id               AS type_id,
        T.name                  AS type_name,
        A.departament_id        AS departament_id,
        G.name                  AS departament_name,
        A.total_attending_time  AS total_attending_time,
        AT.id                   AS attendant_id,
        AT.login                AS attendant_login,
        OW.id                   AS owner_id,
        OW.login                AS owner_login
    FROM helpdesk.helpdesk A
    LEFT JOIN helpdesk.status              S  ON S.id  = A.status_id
    LEFT JOIN helpdesk.priority             P  ON P.id  = A.priority_id
    LEFT JOIN helpdesk.type                 T  ON T.id  = A.type_id
    LEFT JOIN helpdesk.service_departament  G  ON G.id  = A.departament_id
    LEFT JOIN user                          AT ON AT.id = A.user_id_attendent
    LEFT JOIN user                          OW ON OW.id = A.user_id_owner
    WHERE A.status_id = {STATUS_FECHADO}
    ORDER BY A.date_open
"""


def _worked_time_display(total_seconds):
    total_seconds = int(total_seconds or 0)
    hours, minutes = divmod(total_seconds // 60, 60)
    if hours and minutes:
        return f"{hours}h {minutes:02d}min"
    if hours:
        return f"{hours}h"
    return f"{minutes}min"


def _tornar_aware(momento):
    """Os campos de data do SM vêm sem timezone (hora local do servidor)."""
    if momento and timezone.is_naive(momento):
        return timezone.make_aware(momento)
    return momento


class Command(BaseCommand):
    help = "Importa para o ConnectMX Helpdesk os chamados já fechados (status 5) no SM."

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Grava de verdade. Sem esta flag, só mostra o relatório.",
        )

    def _resolver_sector(self, user):
        account = PortalRequesterAccount.objects.select_related("collaborator__sector").filter(user=user).first()
        return getattr(getattr(account, "collaborator", None), "sector", None)

    def _obter_ou_criar_grupo_tipo(self, departament_name, type_name):
        grupo_nome = (departament_name or "Sem grupo (SM)").strip()
        grupo, _ = TaskGroup.objects.get_or_create(name=grupo_nome)
        tipo_nome = (type_name or "Sem tipo (SM)").strip()
        tipo, _ = TaskType.objects.get_or_create(group=grupo, name=tipo_nome)
        return grupo, tipo

    def handle(self, *args, **options):
        aplicar = options["aplicar"]

        try:
            linhas = _query(SM_QUERY)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Não consegui consultar o SM: {exc}"))
            return

        ja_importados = set(
            PortalDemand.objects.filter(sm_ticket_id__isnull=False).values_list("sm_ticket_id", flat=True)
        )

        importaveis = []
        sem_solicitante = []
        ja_importado_antes = []
        empresas_faltando = set()
        grupos_novos = set()
        tipos_novos = set()

        for linha in linhas:
            sm_id = linha["sm_id"]
            if sm_id in ja_importados:
                ja_importado_antes.append(sm_id)
                continue

            requester = User.objects.filter(id_sm=str(linha["owner_id"])).first() if linha["owner_id"] else None
            if requester is None:
                sem_solicitante.append(linha)
                continue

            company = PortalCompany.objects.filter(sm_organization_id=linha["organization_id"]).first()
            if company is None:
                empresas_faltando.add(linha["organization_id"])

            grupo_nome = (linha["departament_name"] or "Sem grupo (SM)").strip()
            if not TaskGroup.objects.filter(name=grupo_nome).exists():
                grupos_novos.add(grupo_nome)
            tipo_nome = (linha["type_name"] or "Sem tipo (SM)").strip()
            if not TaskType.objects.filter(group__name=grupo_nome, name=tipo_nome).exists():
                tipos_novos.add(f"{grupo_nome} / {tipo_nome}")

            importaveis.append((linha, requester, company))

        self._imprimir_relatorio(len(linhas), importaveis, sem_solicitante, ja_importado_antes, grupos_novos, tipos_novos)

        if not aplicar:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Nada foi gravado — rode de novo com --aplicar quando estiver de acordo com o relatório acima."
            ))
            return

        if empresas_faltando:
            self.stderr.write(self.style.ERROR(
                "Existem chamados com organization_id sem PortalCompany correspondente "
                f"({sorted(empresas_faltando)}). Cadastre a empresa antes de aplicar."
            ))
            return

        importados = 0
        with transaction.atomic():
            for linha, requester, company in importaveis:
                attendant = (
                    User.objects.filter(id_sm=str(linha["attendant_id"])).first()
                    if linha["attendant_id"]
                    else None
                )
                grupo, tipo = self._obter_ou_criar_grupo_tipo(linha["departament_name"], linha["type_name"])
                date_open = _tornar_aware(linha["date_open"])
                date_close = _tornar_aware(linha["date_close"])

                demand = PortalDemand(
                    requester=requester,
                    assigned_to=attendant,
                    company=company,
                    requester_sector=self._resolver_sector(requester),
                    task_group=grupo,
                    task_type=tipo,
                    priority_level=PRIORITY_MAP.get(linha["priority_name"], userQueue.PRIORITY_MEDIUM),
                    title=(linha["subject"] or "(sem assunto no SM)")[:180],
                    description=linha["description_txt"] or "",
                    status=PortalDemand.STATUS_COMPLETED,
                    assumed_at=date_open if attendant else None,
                    completed_at=date_close or date_open,
                    sm_ticket_id=linha["sm_id"],
                )
                demand.save()

                if date_open:
                    PortalDemand.objects.filter(pk=demand.pk).update(created_at=date_open)

                if attendant:
                    log_entry = _portal_log_event(
                        demand,
                        PortalDemandLog.EVENT_ASSUMED,
                        actor=None,
                        actor_name="Importação do SM",
                        summary=(
                            f"Chamado importado do SM, atendido por "
                            f"{_queue_collaborator_display_name(attendant)}."
                        ),
                        details=f"Status original no SM: {linha['status_name'] or linha['status_id']}.",
                        to_attendant=attendant,
                    )
                    if date_open:
                        PortalDemandLog.objects.filter(pk=log_entry.pk).update(created_at=date_open)

                log_entry = _portal_log_event(
                    demand,
                    PortalDemandLog.EVENT_COMPLETED,
                    actor=None,
                    actor_name="Importação do SM",
                    summary=f"Chamado {linha['sm_id']} importado do SM já concluído por lá.",
                    to_attendant=attendant,
                )
                log_moment = demand.completed_at
                if log_moment:
                    PortalDemandLog.objects.filter(pk=log_entry.pk).update(created_at=log_moment)

                if linha["total_attending_time"]:
                    PortalDemandMessage.objects.create(
                        demand=demand,
                        author=None,
                        author_name="Importação do SM",
                        author_role=PortalDemandMessage.ROLE_SYSTEM,
                        is_internal=True,
                        message=(
                            f"Chamado {linha['sm_id']} importado do SM. "
                            f"Tempo total apontado por lá: {_worked_time_display(linha['total_attending_time'])}. "
                            "Sem apontamento estruturado porque o SM só guarda o total, não as sessões."
                        ),
                    )
                importados += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{importados} chamado(s) fechado(s) importado(s)."))
        if grupos_novos:
            self.stdout.write(f"Grupos novos criados: {', '.join(sorted(grupos_novos))}")
        if tipos_novos:
            self.stdout.write(f"Tipos novos criados: {', '.join(sorted(tipos_novos))}")

    def _imprimir_relatorio(self, total_sm, importaveis, sem_solicitante, ja_importado_antes, grupos_novos, tipos_novos):
        largura = 78
        self.stdout.write("=" * largura)
        self.stdout.write("  RELATÓRIO — chamados fechados (status 5) no SM")
        self.stdout.write("=" * largura)
        self.stdout.write(f"  Total fechado no SM:               {total_sm}")
        self.stdout.write(self.style.SUCCESS(f"  Prontos para importar:             {len(importaveis)}"))
        self.stdout.write(self.style.WARNING(f"  Sem conta ConnectMX (id_sm):       {len(sem_solicitante)}"))
        if ja_importado_antes:
            self.stdout.write(f"  Já importados antes (ignorados):   {len(ja_importado_antes)}")

        if grupos_novos or tipos_novos:
            self.stdout.write("")
            self.stdout.write("  Grupo/Tipo de tarefa que serão CRIADOS ao aplicar:")
            for nome in sorted(grupos_novos):
                self.stdout.write(f"    grupo novo: {nome}")
            for nome in sorted(tipos_novos):
                self.stdout.write(f"    tipo novo:  {nome}")

        if sem_solicitante:
            logins = sorted({linha["owner_login"] or "(sem login)" for linha in sem_solicitante})
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"  {len(logins)} login(s) de solicitante do SM sem accounts.User.id_sm correspondente:"
            ))
            for login in logins[:30]:
                self.stdout.write(f"    - {login}")
            if len(logins) > 30:
                self.stdout.write(f"    ... e mais {len(logins) - 30}")
            self.stdout.write("")
            self.stdout.write(
                "  Preencha accounts.User.id_sm para essas contas (ou cadastre-as como "
                "colaborador/solicitante do portal) antes de reaplicar este comando — "
                "os chamados delas continuam de fora até isso acontecer. Use `casar_usuarios_sm` "
                "para preencher em lote, ou `provisionar_pessoas_sm` para criar as contas que faltam."
            )
