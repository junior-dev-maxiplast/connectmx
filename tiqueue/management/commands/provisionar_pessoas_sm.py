"""
Cria no ConnectMX as contas que faltam para os solicitantes e atendentes dos
chamados hoje "vivos" no SM (mesmo filtro de status do `importar_chamados_sm`),
com o id_sm certo desde a criação — pré-requisito pra rodar
`importar_chamados_sm --aplicar` de verdade.

    python manage.py provisionar_pessoas_sm            so mostra o relatorio (nao grava nada)
    python manage.py provisionar_pessoas_sm --aplicar   grava de verdade

Escopo desta rodada: só quem tem chamado aberto HOJE (127 no dia em que isto
foi escrito) — não todo o cadastro de usuários do SM (227 no total). Cobre o
suficiente pra destravar a Fase 2; o resto do histórico de usuários entra sob
demanda, quando abrir chamado.

Quem entra como o quê:
- Aparece como ATENDENTE em algum chamado aberto -> ganha accounts.User
  interno (is_support_attendant=True, can_access_internal=True). Sem
  colaborador/conta de portal — atendente não precisa disso pra assumir fila.
- Aparece como SOLICITANTE (dono) em algum chamado aberto -> ganha o trio
  PortalRequesterSector (pelo setor real do SM, tabela `departament` — não
  `service_departament`, que é o catálogo de TI, não o setor da pessoa) +
  PortalRequesterCollaborator + PortalRequesterAccount, com o User por baixo.
  Uma pessoa pode entrar nos dois grupos ao mesmo tempo (ex.: atendente que
  também abre chamado para o próprio setor).

O que este comando NÃO resolve, de proposito:
- Senha: cada conta sai com senha inutilizável (`set_unusable_password`).
  A pessoa define a própria na tela "Esqueci minha senha"
  (`generalapp.forgotPasswordPage`), usando matrícula + e-mail — não é
  preciso distribuir senha nenhuma à mão. Avisar quem foi criado é assunto da
  Fase 6 (Comunicação), não deste comando.
- Matrícula real: o SM não guarda matrícula de RH. A matrícula usada aqui
  (`registration_code`/`userId`) é o próprio `user.id` numérico do SM — único
  e rastreável, mas não é o número de crachá da pessoa. Ajustar isso, se um
  dia importar do ERP, é trabalho de outro comando.
- Empresa do colaborador: o SM não amarra o cadastro da PESSOA a uma empresa
  do grupo (só o chamado tem organization_id). Fica em branco aqui — o
  próprio campo já foi desenhado pra ser preenchido depois, e não bloqueia
  nada (só fica marcado "Sem empresa" na tela de Solicitantes).
- Quem já casou por username (ver `casar_usuarios_sm`) não é tocado de novo.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from tiqueue.it_bi import _query
from tiqueue.models import PortalRequesterAccount, PortalRequesterCollaborator, PortalRequesterSector

SM_PESSOAS_QUERY = """
    SELECT DISTINCT
        u.id            AS sm_id,
        u.login         AS login,
        u.fullname      AS fullname,
        u.mail          AS mail,
        u.status        AS status,
        d.name          AS setor_nome,
        'owner'         AS papel
    FROM helpdesk.helpdesk h
    JOIN user u ON u.id = h.user_id_owner
    LEFT JOIN departament d ON d.id = u.departament_id
    WHERE h.status_id NOT IN (5, 6, 13)

    UNION

    SELECT DISTINCT
        u.id            AS sm_id,
        u.login         AS login,
        u.fullname      AS fullname,
        u.mail          AS mail,
        u.status        AS status,
        d.name          AS setor_nome,
        'attendant'     AS papel
    FROM helpdesk.helpdesk h
    JOIN user u ON u.id = h.user_id_attendent
    LEFT JOIN departament d ON d.id = u.departament_id
    WHERE h.status_id NOT IN (5, 6, 13)
"""


class Command(BaseCommand):
    help = "Cria contas ConnectMX para quem tem chamado aberto hoje no SM, com id_sm já preenchido."

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Grava de verdade. Sem esta flag, só mostra o relatório.",
        )

    def _agrupar_por_pessoa(self, linhas):
        pessoas = {}
        for linha in linhas:
            pessoa = pessoas.setdefault(
                linha["sm_id"],
                {
                    "sm_id": linha["sm_id"],
                    "login": linha["login"],
                    "fullname": linha["fullname"],
                    "mail": linha["mail"],
                    "status": linha["status"],
                    "setor_nome": linha["setor_nome"],
                    "is_owner": False,
                    "is_attendant": False,
                },
            )
            if linha["papel"] == "owner":
                pessoa["is_owner"] = True
            else:
                pessoa["is_attendant"] = True
        return list(pessoas.values())

    def handle(self, *args, **options):
        aplicar = options["aplicar"]

        try:
            linhas = _query(SM_PESSOAS_QUERY)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Não consegui consultar o SM: {exc}"))
            return

        pessoas = self._agrupar_por_pessoa(linhas)

        a_criar = []
        ja_existe = []
        for pessoa in pessoas:
            if User.objects.filter(username__iexact=pessoa["login"]).exists():
                ja_existe.append(pessoa)
            else:
                a_criar.append(pessoa)

        setores_novos = sorted(
            {(p["setor_nome"] or "Sem setor (SM)").strip() for p in a_criar if p["is_owner"]}
            - set(PortalRequesterSector.objects.values_list("name", flat=True))
        )

        emails_substituidos = []
        emails_em_uso = set(User.objects.values_list("email", flat=True))
        for pessoa in a_criar:
            email = (pessoa["mail"] or "").strip().lower()
            if not email or email in emails_em_uso:
                emails_substituidos.append((pessoa["login"], email or "(vazio)"))
                email = f"{pessoa['login']}@sm.invalido"
            emails_em_uso.add(email)
            pessoa["email_resolvido"] = email

        self._imprimir_relatorio(pessoas, a_criar, ja_existe, setores_novos, emails_substituidos)

        if not aplicar:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Nada foi gravado — rode de novo com --aplicar quando estiver de acordo com o relatório acima."
            ))
            return

        criados_atendente = 0
        criados_solicitante = 0
        with transaction.atomic():
            for pessoa in a_criar:
                ativo = pessoa["status"] == "active"
                email = pessoa["email_resolvido"]

                user = User(
                    username=pessoa["login"],
                    userId=str(pessoa["sm_id"]),
                    email=email,
                    nameUser=pessoa["fullname"] or pessoa["login"],
                    id_sm=str(pessoa["sm_id"]),
                    is_active=ativo,
                    is_support_attendant=pessoa["is_attendant"],
                )
                user.set_unusable_password()
                user.save()

                if pessoa["is_attendant"]:
                    criados_atendente += 1

                if pessoa["is_owner"]:
                    setor_nome = (pessoa["setor_nome"] or "Sem setor (SM)").strip()
                    setor, _ = PortalRequesterSector.objects.get_or_create(
                        name=setor_nome, defaults={"is_active": True}
                    )
                    collaborator = PortalRequesterCollaborator.objects.create(
                        sector=setor,
                        full_name=pessoa["fullname"] or pessoa["login"],
                        registration_code=str(pessoa["sm_id"]),
                        email=email,
                        is_active=ativo,
                    )
                    PortalRequesterAccount.objects.create(
                        collaborator=collaborator,
                        user=user,
                        is_active=ativo,
                    )
                    criados_solicitante += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{len(a_criar)} conta(s) criada(s) "
            f"({criados_atendente} como atendente, {criados_solicitante} como solicitante do portal)."
        ))
        if setores_novos:
            self.stdout.write(f"Setores novos criados: {', '.join(setores_novos)}")

    def _imprimir_relatorio(self, pessoas, a_criar, ja_existe, setores_novos, emails_substituidos):
        largura = 78
        self.stdout.write("=" * largura)
        self.stdout.write("  RELATÓRIO — pessoas dos chamados abertos hoje no SM")
        self.stdout.write("=" * largura)
        self.stdout.write(f"  Pessoas distintas (solicitante + atendente): {len(pessoas)}")
        self.stdout.write(self.style.SUCCESS(f"  Vão ganhar conta nova ao aplicar:      {len(a_criar)}"))
        if ja_existe:
            self.stdout.write(f"  Já têm conta (username já existe):     {len(ja_existe)}")

        if setores_novos:
            self.stdout.write("")
            self.stdout.write("  Setores novos que serão CRIADOS ao aplicar:")
            for nome in setores_novos:
                self.stdout.write(f"    - {nome}")

        if emails_substituidos:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"  {len(emails_substituidos)} conta(s) sem e-mail próprio no SM (vazio ou já em uso por outra "
                "conta) — vai receber um e-mail de espera (@sm.invalido). Sem e-mail real, a pessoa não consegue "
                "usar a tela 'Esqueci minha senha'; corrija manualmente antes de avisar quem for de verdade uma "
                "pessoa (contas genéricas de setor podem ficar assim):"
            ))
            for login, email_original in emails_substituidos:
                self.stdout.write(f"    {login}: e-mail no SM era '{email_original}'")

        if a_criar:
            self.stdout.write("")
            self.stdout.write("  Contas a criar:")
            for pessoa in a_criar[:30]:
                papeis = "+".join(
                    filter(None, [pessoa["is_attendant"] and "atendente", pessoa["is_owner"] and "solicitante"])
                )
                self.stdout.write(
                    f"    {pessoa['login']} ({pessoa['fullname']}) — {papeis} — status SM: {pessoa['status']}"
                )
            if len(a_criar) > 30:
                self.stdout.write(f"    ... e mais {len(a_criar) - 30}")
