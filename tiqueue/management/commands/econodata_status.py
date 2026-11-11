"""Situação da integração Econodata, sem gastar tokens (saúde e saldo são grátis).

Uso: python manage.py econodata_status
"""

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth

from tiqueue import econodata
from tiqueue.models import EconodataConsulta


class Command(BaseCommand):
    help = "Mostra saldo, teto mensal, uso do mês e tamanho do cache da Econodata (grátis)."

    def handle(self, *args, **options):
        status = econodata.account_status(origem="comando:econodata_status")
        configured = "sim" if status["configured"] else "NÃO"
        self.stdout.write(f"Chave configurada: {configured} · API: {status['base_url']}")
        if status["error"]:
            self.stdout.write(self.style.ERROR(f"Erro: {status['error']}"))
        if status["health"] is not None:
            self.stdout.write(f"API respondendo: {'sim' if status['health'] else 'não'}")
        if status["balance"] is not None:
            self.stdout.write(f"Saldo na Econodata: {status['balance']} tokens")
        self.stdout.write(
            f"Teto do mês: {status['monthly_limit']} · usado: {status['used_this_month']} · "
            f"restante: {status['left_this_month']} tokens"
        )
        self.stdout.write(f"Empresas em cache: {status['cached_companies']} (validade {status['cache_days']} dias)")
        months = (
            EconodataConsulta.objects.filter(estimativa=False)
            .annotate(mes=TruncMonth("criado_em"))
            .values("mes")
            .annotate(tokens=Sum("tokens_cobrados"), chamadas=Count("id"))
            .order_by("-mes")[:6]
        )
        for month in months:
            self.stdout.write(f"  {month['mes']:%m/%Y}: {month['tokens'] or 0} tokens em {month['chamadas']} chamada(s)")
