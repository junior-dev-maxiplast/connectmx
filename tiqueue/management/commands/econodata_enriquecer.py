"""Enriquece clientes do ERP com a Econodata, em lote e com cache.

Sem --confirmar, só estima o custo (grátis) e mostra o plano. Exemplos:
    python manage.py econodata_enriquecer --escopo sem-ramo
    python manage.py econodata_enriquecer --escopo sem-ramo --confirmar
    python manage.py econodata_enriquecer --escopo ativos --limite 50 --perfil cadastral
    python manage.py econodata_enriquecer --cnpj 92791243000294 --perfil estrategico --confirmar
"""

from django.core.management.base import BaseCommand, CommandError

from tiqueue import econodata


class Command(BaseCommand):
    help = "Consulta a Econodata para clientes ativos (só estima, a menos que receba --confirmar)."

    def add_arguments(self, parser):
        parser.add_argument("--perfil", default="basico", choices=sorted(econodata.PROFILES))
        parser.add_argument(
            "--escopo", default="sem-ramo", choices=["sem-ramo", "ativos"],
            help="sem-ramo: clientes ativos sem ramo de atividade no ERP; ativos: todos com venda no período.",
        )
        parser.add_argument("--meses", type=int, default=12, help="Janela de clientes ativos (padrão 12).")
        parser.add_argument("--limite", type=int, default=0, help="Só os N maiores clientes por faturamento.")
        parser.add_argument("--cnpj", action="append", default=[], help="CNPJ específico (pode repetir).")
        parser.add_argument("--confirmar", action="store_true", help="Faz a consulta paga (respeitando o teto mensal).")
        parser.add_argument("--forcar", action="store_true", help="Ignora o cache e consulta de novo.")

    def handle(self, *args, **options):
        if not econodata.is_configured():
            raise CommandError("Chave ECONODATA_API_KEY não configurada em connect/.env.")
        if options["cnpj"]:
            cnpjs = options["cnpj"]
            origem = "comando:cnpj"
        else:
            customers = econodata.active_customers(options["meses"], without_sector=options["escopo"] == "sem-ramo")
            if options["limite"]:
                customers = customers[: options["limite"]]
            cnpjs = [customer["cnpj"] for customer in customers]
            origem = f"comando:{options['escopo']}"
            self.stdout.write(f"Clientes selecionados ({options['escopo']}, {options['meses']} meses): {len(cnpjs)}")
        try:
            result = econodata.enrich(
                cnpjs, options["perfil"], confirm=options["confirmar"], force=options["forcar"], origem=origem,
            )
        except econodata.EconodataError as exc:
            raise CommandError(str(exc))
        self.stdout.write(
            f"Perfil {result['profile']}: {result['total']} CNPJ(s), {result['cached']} já no cache, "
            f"{result['to_fetch']} a consultar em {result['calls']} chamada(s)."
        )
        self.stdout.write(
            f"Custo estimado pela Econodata: {result['estimated_tokens']} tokens "
            f"(aproximação local: {result['approx_tokens']}). "
            f"Restam {econodata.tokens_left_this_month()} no teto do mês."
        )
        if result["confirmed"]:
            self.stdout.write(self.style.SUCCESS(
                f"Consultado: {result['found']} encontrado(s), {result['not_found']} não encontrado(s), "
                f"{result['charged_tokens']} tokens cobrados."
            ))
        elif result["to_fetch"]:
            self.stdout.write("Nada foi cobrado. Rode de novo com --confirmar para consultar.")
