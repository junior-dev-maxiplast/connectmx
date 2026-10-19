"""
Preenche accounts.User.id_sm com o `user.id` NUMÉRICO do SM (é isso que o
campo guarda hoje — confirmado na única conta que já tinha valor,
junior.ribeiro = '159'), casando a conta pelo username com o login do SM.
Destrava a importação de chamados (`importar_chamados_sm`) e o corte de vez
do SM (Fase 4).

    python manage.py casar_usuarios_sm            so mostra o relatorio (nao grava nada)
    python manage.py casar_usuarios_sm --aplicar   grava de verdade

Casamento so por accounts.User.username == user.login do SM (sem diferenciar
maiusculas). Nao tem fallback por e-mail de proposito: uma primeira tentativa
com e-mail casou contas genericas do SM (ex.: contas de teste) que so
compartilhavam a caixa de e-mail de uma pessoa real, sem ser ela — um
fallback assim erra calado. Quem nao bater por username fica em "sem conta"
para cadastro manual.

So GRAVA em cima de id_sm que estiver vazio. Uma conta que ja tem id_sm
preenchido com outro valor entra como "conflito" e fica de fora — decidir
qual dos dois esta certo e coisa de gente, nao deste comando.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from tiqueue.it_bi import _query

SM_USERS_QUERY = """
    SELECT id, login, fullname, mail, status
    FROM user
    ORDER BY login
"""


class Command(BaseCommand):
    help = "Casa accounts.User.id_sm com o login/e-mail do SM (tabela user)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Grava de verdade. Sem esta flag, só mostra o relatório.",
        )

    def _casar(self, login):
        if not login:
            return None
        return User.objects.filter(username__iexact=login).first()

    def handle(self, *args, **options):
        aplicar = options["aplicar"]

        try:
            linhas = _query(SM_USERS_QUERY)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Não consegui consultar o SM: {exc}"))
            return

        para_atualizar = []   # (login, conta, sm_id)
        ja_correto = []       # login
        conflito = []         # (login, conta, id_sm_atual)
        sem_conta = []        # linha

        vistos = set()
        for linha in linhas:
            login = (linha["login"] or "").strip()
            if not login or login in vistos:
                continue
            vistos.add(login)

            conta = self._casar(login)
            if conta is None:
                sem_conta.append(linha)
                continue

            sm_id = str(linha["id"])
            if not conta.id_sm:
                para_atualizar.append((login, conta, sm_id))
            elif conta.id_sm == sm_id:
                ja_correto.append(login)
            else:
                conflito.append((login, conta, conta.id_sm))

        self._imprimir_relatorio(len(vistos), para_atualizar, ja_correto, conflito, sem_conta)

        if not aplicar:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Nada foi gravado — rode de novo com --aplicar quando estiver de acordo com o relatório acima."
            ))
            return

        with transaction.atomic():
            for _login, conta, sm_id in para_atualizar:
                conta.id_sm = sm_id
                conta.save(update_fields=["id_sm"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{len(para_atualizar)} conta(s) atualizada(s)."))

    def _imprimir_relatorio(self, total_sm, para_atualizar, ja_correto, conflito, sem_conta):
        largura = 78
        self.stdout.write("=" * largura)
        self.stdout.write("  RELATÓRIO — casamento accounts.User.id_sm x SM (tabela user)")
        self.stdout.write("=" * largura)
        self.stdout.write(f"  Logins distintos no SM:             {total_sm}")
        self.stdout.write(self.style.SUCCESS(f"  Prontos para casar (id_sm vazio):   {len(para_atualizar)}"))
        if ja_correto:
            self.stdout.write(f"  Já estavam corretos:                {len(ja_correto)}")
        self.stdout.write(self.style.WARNING(f"  Sem conta no ConnectMX:             {len(sem_conta)}"))
        if conflito:
            self.stdout.write(self.style.ERROR(f"  Conflito (id_sm já tem outro valor): {len(conflito)}"))

        if para_atualizar:
            self.stdout.write("")
            self.stdout.write("  Vão ganhar id_sm ao aplicar:")
            for login, conta, sm_id in para_atualizar[:30]:
                self.stdout.write(f"    {conta.username or conta.email} <- id_sm='{sm_id}' (login SM: {login})")
            if len(para_atualizar) > 30:
                self.stdout.write(f"    ... e mais {len(para_atualizar) - 30}")

        if conflito:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("  Conflitos — id_sm já preenchido com outro login, não tocados:"))
            for login, conta, atual in conflito:
                self.stdout.write(f"    {conta.username or conta.email}: id_sm='{atual}', SM diz '{login}'")

        if sem_conta:
            logins = sorted({linha["login"] for linha in sem_conta})
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"  {len(logins)} login(s) do SM sem conta correspondente no ConnectMX:"))
            for login in logins[:30]:
                self.stdout.write(f"    - {login}")
            if len(logins) > 30:
                self.stdout.write(f"    ... e mais {len(logins) - 30}")
            self.stdout.write("")
            self.stdout.write(
                "  Essas pessoas precisam de uma conta no ConnectMX (cadastro de colaborador/"
                "solicitante do portal, ou usuário interno) antes de aparecerem aqui."
            )
