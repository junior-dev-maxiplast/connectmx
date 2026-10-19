"""
Importa para o ConnectMX Helpdesk os chamados que hoje estao "vivos" no SM
(qualquer status que nao seja Fechado/Lixeira/Mesclado).

    python manage.py importar_chamados_sm            so mostra o relatorio (nao grava nada)
    python manage.py importar_chamados_sm --aplicar   grava de verdade

O relatorio roda sempre, com ou sem --aplicar. Ele responde a pergunta que
importa antes de escrever qualquer linha: quantos chamados dao pra importar
hoje, quantos ficam de fora e por que.

Por que tantos podem ficar de fora: um chamado so entra se o *solicitante*
dele (o dono, nao o atendente) tiver uma conta no ConnectMX com
`accounts.User.id_sm` preenchido com o `user.id` NUMERICO do SM (nao o login
— confirmado na unica conta que ja tinha isso preenchido antes deste
comando existir, junior.ribeiro = '159'; use `casar_usuarios_sm` para
preencher em lote). Sem isso nao tem para quem gravar o chamado
(`PortalDemand.requester` e obrigatorio). Atendente sem essa conta nao trava
a importacao — o chamado so fica sem responsavel atribuido, esperando
alguem assumir na tela.

Decisoes que este comando toma sozinho, e que valem revisar depois de rodar:
- Grupo/Tipo de tarefa: casa por nome com o que ja existe no ConnectMX; se
  nao existir, CRIA um novo com o nome exato do SM (departamento -> grupo,
  tipo -> tipo). Isso tende a criar varios grupos/tipos novos, porque o
  catalogo atual do ConnectMX ainda nao foi pensado para cobrir o SM inteiro.
  O relatorio mostra o que vai ser criado antes de aplicar.
- Status: o SM tem por volta de uma duzia de status "abertos" diferentes; o
  ConnectMX só tem tres (pendente, em atendimento, aguardando solicitante).
  O mapeamento fica em STATUS_MAP abaixo, ajustavel sem tocar no resto do
  comando.
- SLA: NAO é recalculado. Um chamado importado entra sem política e sem
  prazo — inventar um prazo a partir de "agora" para um chamado que já está
  rodando há meses seria pior do que não ter prazo nenhum.
- Tempo apontado: o SM só guarda o total (`total_attending_time`), não o
  histórico de sessões. Isso entra como UMA mensagem de sistema com o total,
  não como um apontamento estruturado (não temos início/fim de sessão para
  preencher `work_started_at`/`work_ended_at` de verdade).
- Log de assunção: quem chega já com atendente (status assumed/waiting) ganha
  um PortalDemandLog sintético de "assunção", com a data do próprio SM (não a
  data da importação) — sem isso a aba Movimentações do detalhe ficava vazia
  para um chamado que claramente já tinha dono.
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

# SM -> PortalDemand.STATUS_*. Os status fechados (5 Fechado, 6 Lixeira,
# 13 Mesclado — confirmados em consulta direta ao SM em 15/09/2026) já ficam
# de fora no WHERE de SM_QUERY, então nem aparecem aqui.
#
# "assumed"/"waiting_requester" só valem se um atendente foi casado (ver
# _resolver_status); sem atendente casado, cai para "pending" independente
# do que estiver aqui.
STATUS_MAP = {
    1: PortalDemand.STATUS_ASSUMED,            # Em atendimento
    2: PortalDemand.STATUS_WAITING,            # Aguardando retorno
    3: PortalDemand.STATUS_PENDING,            # Aguardando atendimento
    4: PortalDemand.STATUS_ASSUMED,            # Atendimento pausado
    7: PortalDemand.STATUS_ASSUMED,            # Manutenção Externa
    8: PortalDemand.STATUS_WAITING,            # Aguardando Retorno Usuário
    9: PortalDemand.STATUS_ASSUMED,            # Aguardando Retorno Fornecedor
    10: PortalDemand.STATUS_ASSUMED,           # Aguardando Retorno Compras
    14: PortalDemand.STATUS_ASSUMED,           # Aguardando Retorno Sancon
    18: PortalDemand.STATUS_ASSUMED,           # Em Desenvolvimento
    19: PortalDemand.STATUS_PENDING,           # Em fila de Desenvolvimento
    22: PortalDemand.STATUS_PENDING,           # Em fila de atendimento
    23: PortalDemand.STATUS_PENDING,           # Aguardando Aprovação de Projeto
}

PRIORITY_MAP = {
    "Alta": userQueue.PRIORITY_HIGH,
    "Média": userQueue.PRIORITY_MEDIUM,
    "Baixa": userQueue.PRIORITY_LOW,
}

SM_QUERY = """
    SELECT
        A.id                    AS sm_id,
        A.date_open             AS date_open,
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
    WHERE A.status_id NOT IN (5, 6, 13)
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
    help = "Importa para o ConnectMX Helpdesk os chamados abertos hoje no SM."

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Grava de verdade. Sem esta flag, só mostra o relatório.",
        )

    def _resolver_status(self, status_id, attendant):
        status = STATUS_MAP.get(status_id, PortalDemand.STATUS_PENDING)
        if status in (PortalDemand.STATUS_ASSUMED, PortalDemand.STATUS_WAITING) and attendant is None:
            return PortalDemand.STATUS_PENDING
        return status

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
                status = self._resolver_status(linha["status_id"], attendant)
                date_open = _tornar_aware(linha["date_open"])

                demand = PortalDemand(
                    requester=requester,
                    assigned_to=attendant if status != PortalDemand.STATUS_PENDING else None,
                    company=company,
                    requester_sector=self._resolver_sector(requester),
                    task_group=grupo,
                    task_type=tipo,
                    priority_level=PRIORITY_MAP.get(linha["priority_name"], userQueue.PRIORITY_MEDIUM),
                    title=(linha["subject"] or "(sem assunto no SM)")[:180],
                    description=linha["description_txt"] or "",
                    status=status,
                    sm_ticket_id=linha["sm_id"],
                )
                if status == PortalDemand.STATUS_ASSUMED:
                    demand.assumed_at = date_open
                elif status == PortalDemand.STATUS_WAITING:
                    demand.waiting_since = timezone.now()
                demand.save()

                if date_open:
                    PortalDemand.objects.filter(pk=demand.pk).update(created_at=date_open)

                if demand.assigned_to_id:
                    log_entry = _portal_log_event(
                        demand,
                        PortalDemandLog.EVENT_ASSUMED,
                        actor=None,
                        actor_name="Importação do SM",
                        summary=(
                            f"Chamado importado do SM já em atendimento com "
                            f"{_queue_collaborator_display_name(attendant)}."
                        ),
                        details=f"Status original no SM: {linha['status_name'] or linha['status_id']}.",
                        to_attendant=attendant,
                    )
                    log_moment = demand.assumed_at or date_open
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
        self.stdout.write(self.style.SUCCESS(f"{importados} chamado(s) importado(s)."))
        if grupos_novos:
            self.stdout.write(f"Grupos novos criados: {', '.join(sorted(grupos_novos))}")
        if tipos_novos:
            self.stdout.write(f"Tipos novos criados: {', '.join(sorted(tipos_novos))}")

    def _imprimir_relatorio(self, total_sm, importaveis, sem_solicitante, ja_importado_antes, grupos_novos, tipos_novos):
        largura = 78
        self.stdout.write("=" * largura)
        self.stdout.write("  RELATÓRIO — chamados abertos no SM")
        self.stdout.write("=" * largura)
        self.stdout.write(f"  Total aberto no SM agora:          {total_sm}")
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
                "os chamados delas continuam de fora até isso acontecer."
            )
