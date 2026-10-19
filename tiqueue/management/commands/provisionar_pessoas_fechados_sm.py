"""
Cria no ConnectMX as contas que faltam para solicitantes e atendentes dos
chamados já FECHADOS no SM (status 5) — pré-requisito pra rodar
`importar_chamados_fechados_sm --aplicar` de verdade.

    python manage.py provisionar_pessoas_fechados_sm            so mostra o relatorio (nao grava nada)
    python manage.py provisionar_pessoas_fechados_sm --aplicar   grava de verdade

Irmao de `provisionar_pessoas_sm` (que cobre os chamados abertos) — mesma
logica de criacao, só muda o filtro de status. Quem já ganhou conta por lá
(ou por qualquer outro caminho) não é recriado aqui: o teste é sempre
"username já existe", então rodar este comando depois do outro é seguro e não
duplica ninguém.

Quem entra como o quê (igual ao irmão):
- Aparece como ATENDENTE em algum chamado fechado -> ganha accounts.User
  interno (is_support_attendant=True, can_access_internal=True).
- Aparece como SOLICITANTE (dono) em algum chamado fechado -> ganha o trio
  PortalRequesterSector + PortalRequesterCollaborator + PortalRequesterAccount.

Mesmas decisões herdadas do irmão (ver o docstring de lá para mais detalhes):
senha inutilizável (define pela tela "Esqueci minha senha"), matrícula = id
numérico do SM, empresa do colaborador fica em branco.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from tiqueue.it_bi import _query
from tiqueue.models import PortalRequesterAccount, PortalRequesterCollaborator, PortalRequesterSector

STATUS_FECHADO = 5

SM_PESSOAS_QUERY = f"""
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
    WHERE h.status_id = {STATUS_FECHADO}

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
    WHERE h.status_id = {STATUS_FECHADO}
"""


class Command(BaseCommand):
    help = "Cria contas ConnectMX para quem tem chamado fechado (status 5) no SM, com id_sm já preenchido."

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
        self.stdout.write("  RELATÓRIO — pessoas dos chamados fechados (status 5) no SM")
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
