"""
Congela, em ConnectMX, o retrato agregado (empresa + mês) dos ~10 mil
chamados já FECHADOS no SM — para o BI do TI não morrer no dia em que o SM
for desligado.

    python manage.py snapshotar_historico_sm            só mostra o relatório (não grava nada)
    python manage.py snapshotar_historico_sm --aplicar   grava/atualiza de verdade

Decisão da Fase 3: chamado fechado NÃO entra como `PortalDemand` (10 mil
registros que ninguém vai reabrir não valem a duplicação) — só como número
agregado, num grão fino (empresa + mês) que qualquer recorte de período do
painel consegue somar depois.

É seguro rodar de novo: cada linha é por (empresa, mês), e `--aplicar`
SOBRESCREVE a linha existente com os números recalculados, não soma em cima.
Isso é o que se quer se o comando for rodado outra vez antes do corte final —
os números de um mês que ainda está fechando podem mudar.
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date

from tiqueue.it_bi import SLA_MAX_SECONDS, SLA_MIN_SECONDS, _query
from tiqueue.models import PortalCompany, SmHistoricalSnapshot

# Só chamado de verdade fechado. Lixeira (6) não é trabalho concluído, e
# Mesclado (13) já teve o conteúdo absorvido pelo chamado em que foi
# mesclado — contar os dois separadamente duplicaria o mesmo atendimento.
STATUS_FECHADO = 5

QUERY_TOTAL = f"""
    SELECT
        A.organization_id AS organization_id,
        CONCAT(YEAR(A.date_open), '-', LPAD(MONTH(A.date_open), 2, '0'), '-01') AS mes,
        COUNT(*) AS total_tickets,
        AVG(CASE WHEN A.total_attending_time BETWEEN {SLA_MIN_SECONDS} AND {SLA_MAX_SECONDS}
                 THEN A.total_attending_time END) AS avg_attending_seconds
    FROM helpdesk.helpdesk A
    WHERE A.status_id = {STATUS_FECHADO}
    GROUP BY A.organization_id, mes
"""

QUERY_RATING = f"""
    SELECT
        A.organization_id AS organization_id,
        CONCAT(YEAR(A.date_open), '-', LPAD(MONTH(A.date_open), 2, '0'), '-01') AS mes,
        AVG(E.rating_average) AS avg_rating
    FROM helpdesk.helpdesk A
    JOIN helpdesk.rating E ON E.helpdesk_id = A.id
    WHERE A.status_id = {STATUS_FECHADO}
    GROUP BY A.organization_id, mes
"""

QUERY_FIRST_RESPONSE = f"""
    SELECT
        A.organization_id AS organization_id,
        CONCAT(YEAR(A.date_open), '-', LPAD(MONTH(A.date_open), 2, '0'), '-01') AS mes,
        AVG(CASE WHEN S.helpdesk_first_response_time BETWEEN {SLA_MIN_SECONDS} AND {SLA_MAX_SECONDS}
                 THEN S.helpdesk_first_response_time END) AS avg_first_response_seconds
    FROM helpdesk.helpdesk A
    JOIN helpdesk.helpdesk_has_sla S ON S.helpdesk_id = A.id
    WHERE A.status_id = {STATUS_FECHADO}
    GROUP BY A.organization_id, mes
"""

QUERY_BY_PRIORITY = f"""
    SELECT A.organization_id AS organization_id,
           CONCAT(YEAR(A.date_open), '-', LPAD(MONTH(A.date_open), 2, '0'), '-01') AS mes,
           COALESCE(P.name, 'Não definida') AS chave, COUNT(*) AS qtd
    FROM helpdesk.helpdesk A LEFT JOIN helpdesk.priority P ON P.id = A.priority_id
    WHERE A.status_id = {STATUS_FECHADO}
    GROUP BY A.organization_id, mes, chave
"""

QUERY_BY_TYPE = f"""
    SELECT A.organization_id AS organization_id,
           CONCAT(YEAR(A.date_open), '-', LPAD(MONTH(A.date_open), 2, '0'), '-01') AS mes,
           COALESCE(T.name, 'Sem tipo') AS chave, COUNT(*) AS qtd
    FROM helpdesk.helpdesk A LEFT JOIN helpdesk.type T ON T.id = A.type_id
    WHERE A.status_id = {STATUS_FECHADO}
    GROUP BY A.organization_id, mes, chave
"""

QUERY_BY_DEPARTMENT = f"""
    SELECT A.organization_id AS organization_id,
           CONCAT(YEAR(A.date_open), '-', LPAD(MONTH(A.date_open), 2, '0'), '-01') AS mes,
           COALESCE(G.name, 'Sem departamento') AS chave, COUNT(*) AS qtd
    FROM helpdesk.helpdesk A LEFT JOIN helpdesk.service_departament G ON G.id = A.departament_id
    WHERE A.status_id = {STATUS_FECHADO}
    GROUP BY A.organization_id, mes, chave
"""


def _minutes(seconds):
    return round(seconds / 60, 1) if seconds is not None else None


class Command(BaseCommand):
    help = "Congela em ConnectMX o retrato agregado (empresa + mês) dos chamados fechados no SM."

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Grava/atualiza de verdade. Sem esta flag, só mostra o relatório.",
        )

    def handle(self, *args, **options):
        aplicar = options["aplicar"]

        try:
            totais = _query(QUERY_TOTAL)
            avaliacoes = _query(QUERY_RATING)
            respostas = _query(QUERY_FIRST_RESPONSE)
            por_prioridade = _query(QUERY_BY_PRIORITY)
            por_tipo = _query(QUERY_BY_TYPE)
            por_departamento = _query(QUERY_BY_DEPARTMENT)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Não consegui consultar o SM: {exc}"))
            return

        buckets = {}
        for linha in totais:
            chave = (linha["organization_id"], linha["mes"])
            buckets[chave] = {
                "total_tickets": linha["total_tickets"],
                "avg_attending_minutes": _minutes(linha["avg_attending_seconds"]),
                "avg_rating": None,
                "avg_first_response_minutes": None,
                "by_priority": {},
                "by_type": {},
                "by_department": {},
            }

        for linha in avaliacoes:
            chave = (linha["organization_id"], linha["mes"])
            if chave in buckets:
                buckets[chave]["avg_rating"] = round(linha["avg_rating"], 2) if linha["avg_rating"] is not None else None

        for linha in respostas:
            chave = (linha["organization_id"], linha["mes"])
            if chave in buckets:
                buckets[chave]["avg_first_response_minutes"] = _minutes(linha["avg_first_response_seconds"])

        for destino, linhas in (("by_priority", por_prioridade), ("by_type", por_tipo), ("by_department", por_departamento)):
            for linha in linhas:
                chave = (linha["organization_id"], linha["mes"])
                if chave in buckets:
                    buckets[chave][destino][linha["chave"]] = linha["qtd"]

        empresas = {c.sm_organization_id: c for c in PortalCompany.objects.exclude(sm_organization_id__isnull=True)}
        sem_empresa = set()
        por_empresa = defaultdict(int)
        for (organization_id, _mes) in buckets:
            if organization_id in empresas:
                por_empresa[empresas[organization_id].name] += 1
            else:
                sem_empresa.add(organization_id)

        self._imprimir_relatorio(buckets, por_empresa, sem_empresa)

        if not aplicar:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Nada foi gravado — rode de novo com --aplicar quando estiver de acordo com o relatório acima."
            ))
            return

        gravados = 0
        for (organization_id, mes), dados in buckets.items():
            company = empresas.get(organization_id)
            if company is None:
                continue
            SmHistoricalSnapshot.objects.update_or_create(
                company=company,
                month=parse_date(mes),
                defaults=dados,
            )
            gravados += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{gravados} retrato(s) mensal(is) gravado(s) ou atualizado(s)."))

    def _imprimir_relatorio(self, buckets, por_empresa, sem_empresa):
        largura = 78
        total_chamados = sum(b["total_tickets"] for b in buckets.values())
        self.stdout.write("=" * largura)
        self.stdout.write("  RELATÓRIO — retrato histórico dos chamados fechados no SM")
        self.stdout.write("=" * largura)
        self.stdout.write(f"  Chamados fechados cobertos:        {total_chamados}")
        self.stdout.write(f"  Retratos (empresa x mês) a gravar: {len(buckets)}")
        self.stdout.write("")
        for nome, qtd in sorted(por_empresa.items()):
            self.stdout.write(f"    {nome}: {qtd} mês(es)")
        if sem_empresa:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR(
                f"  organization_id sem PortalCompany cadastrada: {sorted(sem_empresa)} "
                "— esses meses ficam de fora até a empresa ser cadastrada."
            ))
