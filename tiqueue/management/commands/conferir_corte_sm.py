"""
Conferência pós-corte: compara o que está aberto no SM agora com o que já foi
importado pro ConnectMX (`PortalDemand.sm_ticket_id`), pra flagrar dois
problemas bem diferentes:

    1. Chamado aberto no SM que NÃO tem PortalDemand correspondente.
       Se `date_open` for ANTES do corte, é sobra da importação (id_sm ainda
       faltando pra alguém). Se for DEPOIS do corte, é sinal de que um canal
       de entrada do SM (portal, e-mail ou abertura manual) não foi fechado
       de verdade — chamado novo continuando a nascer lá.

    2. PortalDemand com sm_ticket_id preenchido cujo chamado, no SM, já não
       está mais na lista de abertos (foi fechado por lá depois de importado)
       — não é erro, só um aviso pra fechar o espelho aqui também.

Uso:
    python manage.py conferir_corte_sm --corte "2026-09-16 09:00"

Sem --corte, so mostra a comparacao sem separar "antes/depois do corte" na
secao 1.
"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tiqueue.it_bi import _query
from tiqueue.models import PortalDemand

SM_ABERTOS_QUERY = """
    SELECT id AS sm_id, date_open, subject
    FROM helpdesk.helpdesk
    WHERE status_id NOT IN (5, 6, 13)
"""


class Command(BaseCommand):
    help = "Compara chamados abertos no SM com o que já foi importado pro ConnectMX."

    def add_arguments(self, parser):
        parser.add_argument(
            "--corte",
            help='Data/hora do corte ("AAAA-MM-DD HH:MM"), pra separar sobra de importação de chamado novo pós-corte.',
        )

    def handle(self, *args, **options):
        corte = None
        if options.get("corte"):
            try:
                corte = timezone.make_aware(datetime.strptime(options["corte"], "%Y-%m-%d %H:%M"))
            except ValueError:
                raise CommandError('Formato de --corte inválido. Use "AAAA-MM-DD HH:MM".')

        try:
            abertos_sm = _query(SM_ABERTOS_QUERY)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Não consegui consultar o SM: {exc}"))
            return

        importados = set(
            PortalDemand.objects.filter(sm_ticket_id__isnull=False).values_list("sm_ticket_id", flat=True)
        )
        ids_abertos_sm = {linha["sm_id"] for linha in abertos_sm}

        nao_importados = [linha for linha in abertos_sm if linha["sm_id"] not in importados]
        fechados_no_sm_depois = importados - ids_abertos_sm

        largura = 78
        self.stdout.write("=" * largura)
        self.stdout.write("  CONFERÊNCIA — SM (aberto agora) x ConnectMX (importado)")
        self.stdout.write("=" * largura)
        self.stdout.write(f"  Aberto no SM agora:                 {len(ids_abertos_sm)}")
        self.stdout.write(f"  Já importados (sm_ticket_id):       {len(importados)}")
        self.stdout.write(self.style.WARNING(f"  Aberto no SM, sem PortalDemand:      {len(nao_importados)}"))
        if fechados_no_sm_depois:
            self.stdout.write(f"  Importado, já fechado no SM depois:  {len(fechados_no_sm_depois)} (normal, sem ação)")

        if nao_importados:
            self.stdout.write("")
            if corte:
                antes = [l for l in nao_importados if _tornar_aware(l["date_open"]) < corte]
                depois = [l for l in nao_importados if _tornar_aware(l["date_open"]) >= corte]
                if antes:
                    self.stdout.write(self.style.WARNING(
                        f"  {len(antes)} chamado(s) ANTES do corte ainda sem PortalDemand "
                        "(sobra de id_sm faltando — rode provisionar_pessoas_sm/importar_chamados_sm de novo):"
                    ))
                    for linha in antes[:20]:
                        self.stdout.write(f"    {linha['sm_id']} — {linha['date_open']} — {linha['subject']}")
                if depois:
                    self.stdout.write(self.style.ERROR(
                        f"  {len(depois)} chamado(s) DEPOIS do corte — canal de entrada do SM ainda está "
                        "aberto, chamado novo continua nascendo lá:"
                    ))
                    for linha in depois[:20]:
                        self.stdout.write(f"    {linha['sm_id']} — {linha['date_open']} — {linha['subject']}")
            else:
                self.stdout.write("  Chamados abertos no SM sem PortalDemand correspondente:")
                for linha in nao_importados[:20]:
                    self.stdout.write(f"    {linha['sm_id']} — {linha['date_open']} — {linha['subject']}")
                if len(nao_importados) > 20:
                    self.stdout.write(f"    ... e mais {len(nao_importados) - 20}")


def _tornar_aware(momento):
    if momento and timezone.is_naive(momento):
        return timezone.make_aware(momento)
    return momento
