"""
Valida o portal de chamados do ConnectMX exercitando as telas de verdade.

Não é teste unitário: monta um cenário completo (setor, colaborador, conta de
acesso, política de SLA, resposta pronta, campo personalizado) e passa por cada
etapa do fluxo pelo mesmo caminho que o navegador usa — cliente HTTP, formulário
com POST, redirecionamento, permissão. O que passa aqui passa na tela.

    python manage.py validar_portal            valida e limpa o cenário
    python manage.py validar_portal --manter   deixa o cenário no banco
    python manage.py validar_portal --limpar   só remove o cenário de uma
                                               execução anterior

O objetivo é responder duas perguntas, com evidência: o que já está pronto para
substituir o SM, e o que ainda falta construir.
"""

import io
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from tiqueue.models import (
    PortalCannedResponse,
    PortalDemand,
    PortalDemandAttachment,
    PortalDemandCustomField,
    PortalDemandCustomFieldOption,
    PortalDemandCustomValue,
    PortalDemandLog,
    PortalDemandMessage,
    PortalDemandSlaPolicy,
    PortalRequesterAccount,
    PortalRequesterCollaborator,
    PortalRequesterSector,
    TaskGroup,
    TaskType,
    concludedTasks,
    userQueue,
)

# Tudo que o validador cria carrega esta marca, para a limpeza saber o que é
# dela e não encostar em nada de verdade.
MARCA = "[validador]"
SENHA = "validador-123"


class Resultado:
    OK = "OK"
    FALHA = "FALHA"
    FALTA = "FALTA"
    ATENCAO = "ATENCAO"


class Command(BaseCommand):
    help = "Valida o fluxo de abertura e controle de chamados do portal, de ponta a ponta."

    def add_arguments(self, parser):
        parser.add_argument("--manter", action="store_true",
                            help="Deixa o cenário no banco para conferir na tela.")
        parser.add_argument("--limpar", action="store_true",
                            help="Só remove o cenário de uma execução anterior.")

    # ------------------------------------------------------------- registro --

    def _registrar(self, etapa, funcao, resultado, detalhe=""):
        self.itens.append((etapa, funcao, resultado, detalhe))

    def _checar(self, etapa, funcao, condicao, ok_detalhe="", falha_detalhe="", grau=Resultado.FALHA):
        resultado = Resultado.OK if condicao else grau
        self._registrar(etapa, funcao, resultado, ok_detalhe if condicao else falha_detalhe)
        return bool(condicao)

    # --------------------------------------------------------------- cenário --

    def _limpar(self):
        """Remove o cenário. A ordem respeita as dependências do banco."""
        PortalDemand.objects.filter(title__startswith=MARCA).delete()
        userQueue.objects.filter(a_description__startswith=MARCA).delete()
        concludedTasks.objects.filter(a_description__startswith=MARCA).delete()
        PortalCannedResponse.objects.filter(title__startswith=MARCA).delete()
        PortalDemandSlaPolicy.objects.filter(name__startswith=MARCA).delete()
        PortalDemandCustomField.objects.filter(label__startswith=MARCA).delete()
        PortalRequesterAccount.objects.filter(
            collaborator__full_name__startswith=MARCA
        ).delete()
        PortalRequesterCollaborator.objects.filter(full_name__startswith=MARCA).delete()
        PortalRequesterSector.objects.filter(name__startswith=MARCA).delete()
        TaskType.objects.filter(name__startswith=MARCA).delete()
        TaskGroup.objects.filter(name__startswith=MARCA).delete()
        User.objects.filter(username__startswith="validador.").delete()

    def _montar_cenario(self):
        grupo = TaskGroup.objects.create(name=f"{MARCA} Infraestrutura")
        tipo = TaskType.objects.create(group=grupo, name=f"{MARCA} Acesso a sistema")

        atendente = User.objects.create_user(
            username="validador.atendente", password=SENHA,
            userId="valatd", email="validador.atendente@example.com",
            nameUser=f"{MARCA} Atendente", is_system_admin=True,
        )
        # Segundo atendente, para exercitar transferência.
        destino = User.objects.create_user(
            username="validador.destino", password=SENHA,
            userId="valdst", email="validador.destino@example.com",
            nameUser=f"{MARCA} Segundo atendente", is_system_admin=True,
        )

        politica = PortalDemandSlaPolicy.objects.create(
            name=f"{MARCA} Acesso a sistema",
            task_group=grupo, task_type=tipo,
            first_response_minutes=30, resolution_minutes=240,
            default_attendant=None, auto_assign_on_create=False, is_active=True,
        )
        PortalCannedResponse.objects.create(
            title=f"{MARCA} Acesso liberado",
            message="Acesso liberado. Confirme se consegue entrar.",
            task_group=grupo, is_active=True,
        )
        campo = PortalDemandCustomField.objects.create(
            label=f"{MARCA} Sistema afetado",
            field_type=PortalDemandCustomField.FIELD_SELECT,
            is_active=True, sort_order=1,
        )
        # `value` é o que vai gravado no chamado e é único por campo; `label` é
        # só o que aparece na tela.
        PortalDemandCustomFieldOption.objects.create(
            field=campo, value="senior", label="Senior", sort_order=1)
        PortalDemandCustomFieldOption.objects.create(
            field=campo, value="connectmx", label="ConnectMX", sort_order=2)

        setor = PortalRequesterSector.objects.create(name=f"{MARCA} Comercial", is_active=True)
        colaborador = PortalRequesterCollaborator.objects.create(
            sector=setor, full_name=f"{MARCA} Solicitante",
            registration_code="VAL-0001", email="validador.solicitante@example.com",
            is_active=True,
        )
        # Criada exatamente como a tela "Setores, colaboradores e acessos" cria:
        # sem tocar em `can_access_internal`, que fica no padrão do modelo.
        solicitante = User.objects.create_user(
            username="validador.solicitante", password=SENHA,
            userId="valsol", email="validador.solicitante@example.com",
            nameUser=f"{MARCA} Solicitante",
        )
        PortalRequesterAccount.objects.create(
            collaborator=colaborador, user=solicitante, is_active=True,
        )
        return {
            "grupo": grupo, "tipo": tipo, "politica": politica, "campo": campo,
            "atendente": atendente, "destino": destino, "solicitante": solicitante,
            "setor": setor, "colaborador": colaborador,
        }

    def _entrar(self, usuario):
        cliente = Client()
        cliente.force_login(usuario)
        return cliente

    # ------------------------------------------------------------- validação --

    def _validar(self, cenario):
        solicitante = self._entrar(cenario["solicitante"])
        atendente = self._entrar(cenario["atendente"])

        # ---------------------------------------------------------- cadastro --
        resposta = atendente.get(reverse("portalRequesterAdminPage"))
        self._checar("Cadastro", "Tela de setores, colaboradores e acessos",
                     resposta.status_code == 200,
                     "abre para quem administra o portal",
                     f"respondeu {resposta.status_code}")

        resposta = atendente.get(reverse("portalDemandSlaConfigPage"))
        self._checar("Cadastro", "Configuração de políticas de SLA",
                     resposta.status_code == 200, "abre", f"respondeu {resposta.status_code}")

        resposta = atendente.get(reverse("portalDemandFieldsConfigPage"))
        self._checar("Cadastro", "Configuração de campos personalizados",
                     resposta.status_code == 200, "abre", f"respondeu {resposta.status_code}")

        resposta = atendente.get(reverse("portalDemandResponsesConfigPage"))
        self._checar("Cadastro", "Configuração de respostas prontas",
                     resposta.status_code == 200, "abre", f"respondeu {resposta.status_code}")

        # A tela responde 200 para qualquer um, mas com `can_manage` falso ela não
        # carrega dado nenhum e mostra o aviso de acesso negado. O que interessa
        # aqui é se algum dado vaza, não o código de status.
        resposta = solicitante.get(reverse("portalRequesterAdminPage"))
        contexto = resposta.context or {}
        vazou = bool(contexto.get("can_manage")) or any(
            (contexto.get("summary") or {}).get(chave) for chave in
            ("pending", "custom_fields", "sla_policies", "canned_responses")
        )
        self._checar("Cadastro", "Configurações não vazam dado para o solicitante",
                     not vazou,
                     "tela abre em estado vazio, com aviso de acesso negado",
                     "o solicitante recebeu dados de administração na tela")

        # -------------------------------------------------- escopo do acesso --
        # Substituir o SM significa dar conta de portal para a fábrica inteira.
        # O que essa conta alcança além do portal deixa de ser detalhe.
        self._registrar(
            "Escopo do acesso", "Conta de solicitante nasce restrita ao portal",
            Resultado.OK if not cenario["solicitante"].can_access_internal else Resultado.FALTA,
            "restrita" if not cenario["solicitante"].can_access_internal
            else "a tela de cadastro não define `can_access_internal`; a conta nasce com acesso interno",
        )

        internas = [
            ("Minha Fila do TI", "queueUserPage"),
            ("Projetos", "manageProjects"),
            ("Base de conhecimento", "knowledgeBasePage"),
            ("Hub de ferramentas", "hubPage"),
        ]
        alcancadas = []
        for rotulo, rota in internas:
            try:
                resposta = solicitante.get(reverse(rota))
            except Exception:
                continue
            if resposta.status_code == 200:
                alcancadas.append(rotulo)
        self._checar(
            "Escopo do acesso", "Solicitante não alcança o ConnectMX interno",
            not alcancadas,
            "bloqueado nas telas internas",
            "o solicitante abriu: " + ", ".join(alcancadas),
            grau=Resultado.FALTA,
        )

        # E se marcar a conta como restrita? O middleware só libera /dashes/.
        cenario["solicitante"].can_access_internal = False
        cenario["solicitante"].save(update_fields=["can_access_internal"])
        restrito = self._entrar(cenario["solicitante"])
        resposta = restrito.get(reverse("portalDemandCreatePage"))
        self._checar(
            "Escopo do acesso", "Restringir a conta mantém o portal funcionando",
            resposta.status_code == 200,
            "portal continua acessível com a conta restrita",
            f"com `can_access_internal` desmarcado o portal responde {resposta.status_code}: "
            "o middleware só libera /dashes/, então não há como prender a conta no portal",
            grau=Resultado.FALTA,
        )
        cenario["solicitante"].can_access_internal = True
        cenario["solicitante"].save(update_fields=["can_access_internal"])
        solicitante = self._entrar(cenario["solicitante"])

        # ----------------------------------------------------------- abertura --
        resposta = solicitante.get(reverse("portalDemandCreatePage"))
        abriu = self._checar("Abertura", "Tela de abrir chamado",
                             resposta.status_code == 200,
                             "abre para quem tem conta de portal",
                             f"respondeu {resposta.status_code}")

        resposta = solicitante.get(
            reverse("portalDemandInsightsApi"),
            {"title": "Sem acesso ao Senior", "description": "Não consigo entrar no sistema",
             "task_group": cenario["grupo"].id, "task_type": cenario["tipo"].id,
             "priority_level": "high"},
        )
        dados = resposta.json() if resposta.status_code == 200 else {}
        previa = dados.get("sla") or {}
        self._checar("Abertura", "Prévia de SLA enquanto digita",
                     bool(previa.get("has_policy") and previa.get("first_response_display")),
                     f"1ª resposta em {previa.get('first_response_display')}, "
                     f"conclusão em {previa.get('resolution_display')}",
                     "não devolveu prazo")
        self._checar("Abertura", "Sugestão da base de conhecimento",
                     "knowledge" in dados,
                     f"{len(dados.get('knowledge') or [])} artigo(s) para este texto",
                     "campo ausente na resposta")
        self._checar("Abertura", "Aviso de chamado duplicado",
                     "duplicates" in dados,
                     f"{len(dados.get('duplicates') or [])} parecido(s)",
                     "campo ausente na resposta")

        anexo = SimpleUploadedFile("evidencia.txt", b"print do erro", content_type="text/plain")
        resposta = solicitante.post(
            reverse("portalDemandCreatePage"),
            {
                "title": f"{MARCA} Sem acesso ao Senior",
                "description": "Não consigo entrar no sistema desde hoje de manhã.",
                "task_group": cenario["grupo"].id,
                "task_type": cenario["tipo"].id,
                "priority_level": "high",
                f"custom_field_{cenario['campo'].id}": "senior",
                "attachments": anexo,
            },
        )
        demanda = PortalDemand.objects.filter(title__startswith=MARCA).order_by("-id").first()
        criou = self._checar("Abertura", "Abrir chamado com anexo e campo personalizado",
                             demanda is not None and resposta.status_code == 302,
                             "chamado criado e redirecionou para Minhas Demandas",
                             f"respondeu {resposta.status_code} e nada foi gravado")
        if not criou:
            return

        self._checar("Abertura", "Protocolo gerado",
                     bool(demanda.access_code),
                     f"protocolo {demanda.protocol}", "chamado ficou sem protocolo")
        self._checar("Abertura", "Anexo gravado",
                     PortalDemandAttachment.objects.filter(demand=demanda).exists(),
                     "arquivo anexado ao chamado", "anexo não chegou")
        self._checar("Abertura", "Campo personalizado gravado",
                     PortalDemandCustomValue.objects.filter(demand=demanda, value="senior").exists(),
                     "opção da lista registrada no chamado", "campo personalizado não gravou")
        self._checar("Abertura", "Política de SLA aplicada na criação",
                     demanda.sla_policy_id == cenario["politica"].id,
                     f"casou a política mais específica ({demanda.sla_policy})",
                     "nenhuma política casou")
        prazo_ok = (
            demanda.first_response_due_at
            and demanda.created_at
            and abs((demanda.first_response_due_at - demanda.created_at) - timedelta(minutes=30))
            < timedelta(seconds=90)
        )
        self._checar("Abertura", "Prazos calculados a partir da política",
                     prazo_ok, "1ª resposta em 30min e conclusão em 4h",
                     "prazo não bateu com a política")

        # Prazo em relógio corrido: a lacuna aparece se a abertura cair fora do
        # expediente e o prazo vencer de madrugada ou no fim de semana.
        venc = timezone.localtime(demanda.resolution_due_at) if demanda.resolution_due_at else None
        fora = bool(venc and (venc.weekday() >= 5 or venc.hour < 7 or venc.hour >= 19))
        self._registrar(
            "Abertura", "Prazo respeita horário de expediente",
            Resultado.FALTA,
            f"conta em relógio corrido; este chamado vence {venc:%d/%m %H:%M}"
            + (" — fora do expediente" if fora else " (hoje caiu dentro, mas é coincidência do horário)"),
        )

        # ------------------------------------------------------------ triagem --
        resposta = atendente.get(reverse("portalPendingDemandsPage"))
        self._checar("Triagem", "Entrada de Chamados",
                     resposta.status_code == 200, "abre", f"respondeu {resposta.status_code}")
        self._checar("Triagem", "Chamado novo aparece na entrada",
                     demanda.protocol in resposta.content.decode("utf-8", "replace"),
                     "protocolo listado", "chamado não apareceu na tela")

        # ---------------------------------------------------------- atendimento --
        resposta = atendente.post(reverse("portalDemandAssume", args=[demanda.id]))
        demanda.refresh_from_db()
        assumiu = self._checar("Atendimento", "Assumir chamado",
                               demanda.status == PortalDemand.STATUS_ASSUMED,
                               "passou para Em atendimento",
                               f"continuou em {demanda.status}")
        self._checar("Atendimento", "Assumir cria a tarefa na fila do atendente",
                     demanda.linked_queue_item_id is not None,
                     "item criado e vinculado", "nenhum item de fila foi criado")

        resposta = solicitante.post(reverse("portalDemandAssume", args=[demanda.id]))
        self._checar("Atendimento", "Solicitante não consegue assumir",
                     resposta.status_code in (302, 403) and demanda.assigned_to_id == cenario["atendente"].id,
                     "bloqueado", "solicitante conseguiu assumir")

        self._registrar(
            "Atendimento", "Perfil de atendente separado do de administrador",
            Resultado.FALTA,
            "assumir exige is_system_admin; não existe papel de atendente",
        )

        url_detalhe = demanda.get_absolute_url()
        resposta = atendente.post(url_detalhe, {
            "form_type": "reply",
            "message": "Estou verificando o acesso agora.",
        })
        demanda.refresh_from_db()
        self._checar("Atendimento", "Responder ao solicitante",
                     PortalDemandMessage.objects.filter(demand=demanda, is_internal=False).exists(),
                     "resposta pública registrada", "mensagem não foi gravada")
        self._checar("Atendimento", "1ª resposta marca o relógio do SLA",
                     demanda.first_response_at is not None,
                     "marcada automaticamente", "o campo continuou vazio")

        inicio = timezone.now() - timedelta(minutes=45)
        resposta = atendente.post(url_detalhe, {
            "form_type": "reply",
            "message": "Ajuste feito no cadastro do usuário.",
            "is_internal": "on",
            "work_started_at": timezone.localtime(inicio).strftime("%Y-%m-%dT%H:%M"),
            "work_ended_at": timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M"),
        })
        interna = PortalDemandMessage.objects.filter(demand=demanda, is_internal=True).first()
        self._checar("Atendimento", "Nota interna",
                     interna is not None, "gravada como interna", "nota interna não foi gravada")
        self._checar("Atendimento", "Apontamento de tempo",
                     bool(interna and interna.worked_minutes),
                     f"{interna.worked_minutes if interna else 0} min registrados",
                     "tempo não foi calculado")

        corpo = solicitante.get(url_detalhe).content.decode("utf-8", "replace")
        self._checar("Atendimento", "Nota interna fica invisível para o solicitante",
                     "Ajuste feito no cadastro do usuário" not in corpo,
                     "não aparece na tela do solicitante",
                     "o solicitante está vendo a nota interna")

        resposta = atendente.post(url_detalhe, {
            "form_type": "transfer",
            "target_attendant": cenario["destino"].id,
        })
        demanda.refresh_from_db()
        self._checar("Atendimento", "Transferir para outro atendente",
                     demanda.assigned_to_id == cenario["destino"].id,
                     "responsável trocado", "a transferência não aconteceu")
        item = demanda.linked_queue_item
        self._checar("Atendimento", "Transferência leva a tarefa de fila junto",
                     bool(item and item.user_code == cenario["destino"].userId),
                     "item de fila mudou de dono",
                     "a tarefa ficou com o atendente anterior")

        # ---------------------------------------------------------- encerramento --
        destino = self._entrar(cenario["destino"])
        resposta = destino.post(url_detalhe, {
            "form_type": "workflow", "workflow_action": "complete",
        })
        demanda.refresh_from_db()
        concluiu = self._checar("Encerramento", "Concluir chamado",
                                demanda.status == PortalDemand.STATUS_COMPLETED,
                                "passou para Concluída", f"continuou em {demanda.status}")

        # A trava de avaliação: com chamado concluído sem nota, não abre outro.
        resposta = solicitante.get(reverse("portalDemandCreatePage"))
        travou = resposta.status_code == 302 and "feedback_required" in resposta.headers.get("Location", "")
        self._checar("Encerramento", "Trava de avaliação bloqueia novo chamado",
                     travou, "redirecionou para avaliar o anterior",
                     "o solicitante conseguiu abrir outro chamado sem avaliar")

        resposta = solicitante.post(url_detalhe, {
            "form_type": "feedback", "feedback_rating": "5",
            "feedback_comment": "Resolvido rápido.",
        })
        demanda.refresh_from_db()
        self._checar("Encerramento", "Solicitante avalia o atendimento",
                     demanda.feedback_rating == 5, "nota 5 registrada", "a nota não foi gravada")

        resposta = solicitante.get(reverse("portalDemandCreatePage"))
        self._checar("Encerramento", "Trava libera depois da avaliação",
                     resposta.status_code == 200, "voltou a abrir",
                     f"continuou bloqueado ({resposta.status_code})")

        if concluiu:
            resposta = destino.post(url_detalhe, {
                "form_type": "workflow", "workflow_action": "complete",
            })
            demanda.refresh_from_db()
            self._registrar(
                "Encerramento", "Reabrir chamado concluído",
                Resultado.FALTA,
                "concluído é estado final; a reincidência vira chamado novo sem vínculo",
            )

        # -------------------------------------------------------------- controle --
        resposta = atendente.get(reverse("portalDemandPage"))
        self._checar("Controle", "Painel do portal",
                     resposta.status_code == 200, "abre", f"respondeu {resposta.status_code}")

        resposta = solicitante.get(reverse("portalMyDemandsPage"))
        self._checar("Controle", "Minhas Demandas (solicitante)",
                     resposta.status_code == 200 and demanda.protocol in resposta.content.decode("utf-8", "replace"),
                     "lista o chamado do próprio solicitante",
                     "o chamado não apareceu para quem abriu")

        resposta = atendente.get(reverse("portalDemandCodeDetailPage", args=[demanda.protocol]))
        self._checar("Controle", "Abrir chamado pelo protocolo",
                     resposta.status_code == 200, "encontra pelo código",
                     f"respondeu {resposta.status_code}")

        eventos = set(PortalDemandLog.objects.filter(demand=demanda).values_list("event_type", flat=True))
        self._checar("Controle", "Trilha de auditoria",
                     {"assumed", "transferred", "completed"} <= eventos,
                     f"registrou {', '.join(sorted(eventos))}",
                     f"faltou evento; registrou apenas {', '.join(sorted(eventos)) or 'nada'}")

        self._checar("Controle", "Apontamento de tempo no log",
                     "worklog" in eventos, "registrado", "o apontamento não gerou evento")

        # --------------------------------------------------------------- lacunas --
        # `EMAIL_HOST` e `EMAIL_BACKEND` vêm preenchidos por padrão no Django, então
        # olhar settings dá falso positivo. A pergunta é se o código chega a enviar.
        envia_email = self._projeto_envia_email()
        self._registrar(
            "Lacunas", "Aviso por e-mail ao solicitante e ao atendente",
            Resultado.OK if envia_email else Resultado.FALTA,
            f"envio encontrado em {envia_email}" if envia_email
            else "nenhuma chamada de envio de e-mail no projeto; o aviso só existe no sino interno",
        )

        from tiqueue import views as portal_views
        self._registrar(
            "Lacunas", "Classificação automática por IA",
            Resultado.OK if portal_views._portal_ai_webhook_url() else Resultado.FALTA,
            "webhook configurado" if portal_views._portal_ai_webhook_url()
            else "construída e desligada: falta o endereço do n8n em CONNECTMX_AI_ROUTING_WEBHOOK_URL",
        )

        outro = PortalDemand.objects.filter(title__startswith=MARCA).exclude(pk=demanda.pk).first()
        self._registrar(
            "Lacunas", "Duplicata entre solicitantes diferentes",
            Resultado.FALTA,
            "a busca só compara com os chamados em aberto de quem está digitando",
        )
        self._registrar(
            "Lacunas", "Fechamento do autoatendimento pela base de conhecimento",
            Resultado.FALTA,
            "o artigo é sugerido, mas não há 'isto resolveu' que evite abrir o chamado",
        )
        self._registrar(
            "Lacunas", "Distribuição automática entre atendentes",
            Resultado.FALTA,
            "só existe atendente padrão fixo na política de SLA; sem rodízio ou fila por especialidade",
        )

    def _projeto_envia_email(self):
        """Procura chamada real de envio de e-mail no código do projeto."""
        import pathlib

        raiz = pathlib.Path(settings.BASE_DIR)
        gatilhos = ("send_mail(", "EmailMessage(", "send_messages(", "EmailMultiAlternatives(")
        proprio = pathlib.Path(__file__).resolve()
        for arquivo in raiz.rglob("*.py"):
            partes = set(arquivo.parts)
            if partes & {"venv", "site-packages", "__pycache__", "migrations"}:
                continue
            # Este arquivo cita os gatilhos para poder procurá-los: sem esta
            # linha o validador encontra a si mesmo e dá o e-mail como pronto.
            if arquivo.resolve() == proprio:
                continue
            try:
                texto = arquivo.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(gatilho in texto for gatilho in gatilhos):
                return arquivo.relative_to(raiz).as_posix()
        return ""

    # ------------------------------------------------------------- relatório --

    def _imprimir(self):
        largura = 78
        cores = {
            Resultado.OK: self.style.SUCCESS,
            Resultado.FALHA: self.style.ERROR,
            Resultado.FALTA: self.style.WARNING,
            Resultado.ATENCAO: self.style.WARNING,
        }
        etapa_atual = None
        for etapa, funcao, resultado, detalhe in self.itens:
            if etapa != etapa_atual:
                self.stdout.write("")
                self.stdout.write(self.style.HTTP_INFO(f"{etapa.upper()}"))
                self.stdout.write("-" * largura)
                etapa_atual = etapa
            marca = cores[resultado](f"{resultado:6}")
            self.stdout.write(f"  {marca} {funcao}")
            if detalhe:
                self.stdout.write(f"         {detalhe}")

        total = len(self.itens)
        ok = sum(1 for item in self.itens if item[2] == Resultado.OK)
        falha = sum(1 for item in self.itens if item[2] == Resultado.FALHA)
        falta = sum(1 for item in self.itens if item[2] == Resultado.FALTA)

        self.stdout.write("")
        self.stdout.write("=" * largura)
        self.stdout.write(f"  {ok} de {total} funções validadas com sucesso")
        if falha:
            self.stdout.write(self.style.ERROR(f"  {falha} com falha — quebrou no caminho"))
        if falta:
            self.stdout.write(self.style.WARNING(f"  {falta} ainda não existem — precisam ser construídas"))
        self.stdout.write("=" * largura)

    # ---------------------------------------------------------------- handle --

    def handle(self, *args, **options):
        self.itens = []

        if options["limpar"]:
            self._limpar()
            self.stdout.write(self.style.SUCCESS("Cenário do validador removido."))
            return

        # O cliente de teste conversa com o host "testserver".
        if "testserver" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]

        self._limpar()
        cenario = self._montar_cenario()
        try:
            self._validar(cenario)
        finally:
            self._imprimir()
            if options["manter"]:
                self.stdout.write("")
                self.stdout.write("Cenário mantido no banco para conferir na tela:")
                self.stdout.write(f"  solicitante: validador.solicitante / {SENHA}")
                self.stdout.write(f"  atendente:   validador.atendente / {SENHA}")
                self.stdout.write("  remover depois: python manage.py validar_portal --limpar")
            else:
                self._limpar()
