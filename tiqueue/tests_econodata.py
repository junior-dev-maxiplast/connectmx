"""Integração Econodata: cache, teto mensal, auditoria e leitura dos dados (sem rede)."""

import os
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import commercial_dna, econodata
from .models import Dashboard, DashboardAccess, EconodataConsulta, EconodataContagem, EconodataEmpresa


User = get_user_model()


ENV = {
    "ECONODATA_API_KEY": "ek_live_teste",
    "ECONODATA_API_URL": "https://api.exemplo.test/v4",
    "ECONODATA_MONTHLY_TOKENS": "1000",
    "ECONODATA_CACHE_DAYS": "180",
}


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._body


class FakeApi:
    """Responde como a API v4: estimativa (X-Estimate-Only) ou lote (POST /companies)."""

    def __init__(self, found=None, per_company=20, status=200):
        self.found = found or {}
        self.per_company = per_company
        self.status = status
        self.calls = []

    def __call__(self, method, url, headers, payload):
        self.calls.append({"method": method, "url": url, "headers": headers, "payload": payload})
        estimate = headers.get("X-Estimate-Only") == "true"
        if url.endswith("/account/balance"):
            return FakeResponse(200, {"saldoTokens": 20000}, {"X-Tokens-Charged": "0"})
        if url.endswith("/companies/calc_list"):
            if estimate:
                return FakeResponse(200, {"tokensEstimados": 100}, {"X-Tokens-Charged": "0"})
            return FakeResponse(200, {"totalRegistros": 321}, {"X-Tokens-Charged": "100"})
        cnpjs = payload["cnpjs"]
        hits = [cnpj for cnpj in cnpjs if cnpj in self.found]
        if estimate:
            return FakeResponse(200, {"tokensEstimados": self.per_company * len(hits)}, {"X-Tokens-Charged": "0"})
        if self.status != 200:
            return FakeResponse(self.status, {"detail": "erro"}, {"X-Tokens-Charged": "0"})
        if not hits:
            return FakeResponse(404, {"detail": "nenhum encontrado"}, {"X-Tokens-Charged": "0"})
        empresas = [{"cnpj": econodata.format_cnpj(cnpj), **self.found[cnpj]} for cnpj in hits]
        erros = [{"cnpj": cnpj, "motivo": "not_found"} for cnpj in cnpjs if cnpj not in self.found]
        return FakeResponse(200, {"empresas": empresas, "erros": erros}, {"X-Tokens-Charged": str(self.per_company * len(hits))})

    def paid_calls(self):
        return [call for call in self.calls if call["headers"].get("X-Estimate-Only") != "true"]


FRIGORIFICO = {
    "cadastro": {
        "cnaePrimario": {"codigo": "C-1012-1/01", "descricao": "Abate de aves"},
        "situacao": "ATIVA",
        "nivelAtividade": "Alto",
    }
}
BAIXADA = {"cadastro": {"cnaePrimario": {"codigo": "G-4639-7/01", "descricao": "Comércio atacadista"}, "situacao": "BAIXADA", "nivelAtividade": "Inativo"}}


class NormalizationTests(SimpleTestCase):
    def test_cnpj_formats(self):
        self.assertEqual(econodata.normalize_cnpj("92.791.243/0002-94"), "92791243000294")
        self.assertEqual(econodata.normalize_cnpj(2791243000294), "02791243000294")  # ERP perde o zero
        self.assertEqual(econodata.normalize_cnpj("12.abc.345/01de-35"), "12ABC34501DE35")  # alfanumérico
        self.assertEqual(econodata.normalize_cnpj("123.456.789-09"), "")  # CPF
        self.assertEqual(econodata.cnpj_root("92791243000294"), "92791243")
        self.assertEqual(econodata.format_cnpj("92791243000294"), "92.791.243/0002-94")

    def test_approximate_costs_match_the_measured_table(self):
        self.assertEqual(econodata.approx_tokens(econodata.PROFILES["basico"]["campos"]), 20)
        self.assertEqual(econodata.approx_tokens(econodata.PROFILES["cadastral"]["campos"]), 50)
        self.assertEqual(econodata.approx_tokens(econodata.PROFILES["estrategico"]["campos"]), 710)
        self.assertEqual(econodata.approx_tokens(econodata.PROFILES["basico"]["campos"], 401), 8020)

    def test_cnae_division(self):
        self.assertEqual(econodata.cnae_division("C-1012-1/01"), "10")
        self.assertEqual(econodata.cnae_division(""), "")

    def test_check_digits(self):
        self.assertTrue(econodata.is_valid_cnpj("92.791.243/0002-94"))
        self.assertTrue(econodata.is_valid_cnpj("12.ABC.345/01DE-35"))  # exemplo oficial da Receita
        self.assertFalse(econodata.is_valid_cnpj("92.791.243/0002-95"))
        self.assertFalse(econodata.is_valid_cnpj("11.111.111/1111-11"))
        self.assertFalse(econodata.is_valid_cnpj("123.456.789-09"))


@mock.patch.dict(os.environ, ENV)
class EnrichTests(TestCase):
    def test_without_confirmation_only_free_estimates_are_made(self):
        api = FakeApi(found={"92791243000294": FRIGORIFICO})
        with mock.patch.object(econodata, "_send", api):
            result = econodata.enrich(["92.791.243/0002-94"], "basico")
        self.assertEqual(result["estimated_tokens"], 20)
        self.assertEqual(api.paid_calls(), [])
        self.assertFalse(EconodataEmpresa.objects.exists())
        consulta = EconodataConsulta.objects.get()
        self.assertTrue(consulta.estimativa)
        self.assertEqual(consulta.tokens_cobrados, 0)
        payload = api.calls[0]["payload"]
        self.assertEqual(payload["campos"], econodata.PROFILES["basico"]["campos"])  # nunca blocos inteiros
        self.assertNotIn("incluir", payload)

    def test_confirmed_batch_stores_found_and_not_found(self):
        api = FakeApi(found={"92791243000294": FRIGORIFICO})
        with mock.patch.object(econodata, "_send", api):
            result = econodata.enrich(["92791243000294", "11111111000191"], "basico", confirm=True)
        self.assertEqual((result["found"], result["not_found"], result["charged_tokens"]), (1, 1, 20))
        self.assertEqual(len(api.paid_calls()), 1)
        record = EconodataEmpresa.objects.get(cnpj="92791243000294")
        self.assertEqual((record.cnae_codigo, record.situacao, record.nivel_atividade), ("C-1012-1/01", "ATIVA", "Alto"))
        self.assertEqual(record.raiz, "92791243")
        self.assertFalse(EconodataEmpresa.objects.get(cnpj="11111111000191").encontrado)
        self.assertEqual(econodata.tokens_used_this_month(), 20)

    def test_cache_avoids_paying_twice_including_not_found(self):
        api = FakeApi(found={"92791243000294": FRIGORIFICO})
        with mock.patch.object(econodata, "_send", api):
            econodata.enrich(["92791243000294", "11111111000191"], "basico", confirm=True)
            again = econodata.enrich(["92791243000294", "11111111000191"], "basico", confirm=True)
        self.assertEqual((again["cached"], again["to_fetch"], again["calls"]), (2, 0, 0))
        self.assertEqual(len(api.paid_calls()), 1)

    def test_richer_profile_requests_only_the_missing_fields(self):
        api = FakeApi(found={"92791243000294": FRIGORIFICO})
        with mock.patch.object(econodata, "_send", api):
            econodata.enrich(["92791243000294"], "basico", confirm=True)
            plan = econodata.plan_enrichment(["92791243000294"], "cadastral")
        missing = plan["calls"][0][0]
        self.assertNotIn("cadastro.cnaePrimario", missing)
        self.assertIn("cadastro.dataAbertura", missing)

    def test_expired_cache_is_fetched_again(self):
        EconodataEmpresa.objects.create(
            cnpj="92791243000294", raiz="92791243", campos=econodata.PROFILES["basico"]["campos"],
            consultado_em=timezone.now() - timedelta(days=181),
        )
        plan = econodata.plan_enrichment(["92791243000294"], "basico")
        self.assertEqual(plan["to_fetch"], 1)

    def test_monthly_budget_blocks_the_paid_call(self):
        api = FakeApi(found={"92791243000294": FRIGORIFICO}, per_company=600)
        with mock.patch.dict(os.environ, {"ECONODATA_MONTHLY_TOKENS": "500"}), mock.patch.object(econodata, "_send", api):
            with self.assertRaises(econodata.EconodataBudgetExceeded):
                econodata.enrich(["92791243000294"], "basico", confirm=True)
        self.assertEqual(api.paid_calls(), [])
        self.assertFalse(EconodataEmpresa.objects.exists())

    def test_batch_with_nothing_found_is_cached_as_not_found(self):
        api = FakeApi(found={})
        with mock.patch.object(econodata, "_send", api):
            result = econodata.enrich(["11111111000191"], "basico", confirm=True)
        self.assertEqual(result["not_found"], 1)
        self.assertFalse(EconodataEmpresa.objects.get(cnpj="11111111000191").encontrado)

    def test_api_error_is_audited_and_raised(self):
        api = FakeApi(found={"92791243000294": FRIGORIFICO}, status=402)
        with mock.patch.object(econodata, "_send", api), mock.patch.object(econodata.time, "sleep"):
            with self.assertRaises(econodata.EconodataError) as context:
                econodata.enrich(["92791243000294"], "basico", confirm=True)
        self.assertEqual(context.exception.status, 402)
        self.assertTrue(EconodataConsulta.objects.filter(sucesso=False, status_code=402).exists())

    def test_wrong_check_digit_is_never_sent(self):
        api = FakeApi(found={"92791243000294": FRIGORIFICO})
        with mock.patch.object(econodata, "_send", api):
            result = econodata.enrich(["92791243000295", "92791243000294"], "basico", confirm=True)
        self.assertEqual((result["invalid"], result["total"], result["found"]), (1, 1, 1))
        self.assertEqual(api.calls[0]["payload"]["cnpjs"], ["92791243000294"])
        self.assertFalse(EconodataEmpresa.objects.filter(cnpj="92791243000295").exists())

    def test_network_failure_becomes_an_econodata_error(self):
        import requests

        with mock.patch.object(econodata, "_send", side_effect=requests.ConnectionError("sem rede")):
            with self.assertRaises(econodata.EconodataError):
                econodata.enrich(["92791243000294"], "basico")
        self.assertTrue(EconodataConsulta.objects.filter(sucesso=False).exists())

    def test_missing_key_is_reported(self):
        with mock.patch.dict(os.environ, {"ECONODATA_API_KEY": ""}):
            with self.assertRaises(econodata.EconodataNotConfigured):
                econodata.enrich(["92791243000294"], "basico")

    def test_count_is_cached(self):
        api = FakeApi()
        filtros = {"uf": ["PR"], "cnaePrimario": ["C-1012"], "situacao": ["ATIVA"]}
        with mock.patch.object(econodata, "_send", api):
            first = econodata.count_companies(filtros, confirm=True)
            second = econodata.count_companies(filtros, confirm=True)
        self.assertEqual((first["total"], first["charged_tokens"]), (321, 100))
        self.assertTrue(second["cached"])
        self.assertEqual(len(api.paid_calls()), 1)
        self.assertEqual(EconodataContagem.objects.get().total, 321)


@mock.patch.dict(os.environ, ENV)
class MarketViewTests(TestCase):
    def test_reads_only_the_cache(self):
        now = timezone.now()
        main = EconodataEmpresa(cnpj="92791243000294", raiz="92791243", dados=FRIGORIFICO,
                                campos=econodata.PROFILES["basico"]["campos"], consultado_em=now)
        econodata._apply_columns(main)
        main.faturamento_grupo = Decimal("10000000")
        main.campos = main.campos + ["estrategico.faturamentoMatrizFiliais"]
        main.save()
        branch = EconodataEmpresa(cnpj="92791243000375", raiz="92791243", dados=BAIXADA,
                                  campos=econodata.PROFILES["basico"]["campos"], consultado_em=now)
        econodata._apply_columns(branch)
        branch.save()
        with mock.patch.object(econodata, "_send", side_effect=AssertionError("não pode chamar a API")):
            view = econodata.market_view(["92791243000294", "92791243000375", "92791243000456"], our_revenue=Decimal("500000"))
        self.assertEqual((view["total"], view["cached"], view["missing"]), (3, 2, 1))
        self.assertEqual(view["main"]["cnae_codigo"], "C-1012-1/01")
        self.assertEqual(len(view["irregular"]), 1)
        self.assertEqual(round(view["share_pct"], 1), Decimal("5.0"))
        self.assertTrue(view["has_strategic"])
        # Botões com o custo do que falta: básico só para a unidade sem dados,
        # estratégico só para o CNPJ principal, e só pelos campos que faltam.
        profiles = {profile["key"]: profile for profile in view["profiles"]}
        self.assertEqual((profiles["basico"]["to_fetch"], profiles["basico"]["approx_tokens"]), (1, 20))
        self.assertEqual((profiles["estrategico"]["to_fetch"], profiles["estrategico"]["approx_tokens"]), (1, 410))

    def test_wrong_check_digit_is_reported_apart(self):
        view = econodata.market_view(["92791243000295"])
        self.assertEqual((view["missing"], view["invalid"], view["valid"]), (0, ["92.791.243/0002-95"], 0))
        self.assertEqual(econodata.market_view([])["valid"], 0)  # pessoa física: sem botões


@override_settings(ALLOWED_HOSTS=["testserver"])
@mock.patch.dict(os.environ, ENV)
class EnrichViewTests(TestCase):
    """Botão do DNA do Cliente: só administrador, só POST, e volta para a tela."""

    def login(self, **extra):
        user = User.objects.create_user(username="u", password="x", userId="u", email="u@example.com", **extra)
        if not extra.get("is_system_admin"):
            DashboardAccess.objects.create(user=user, dashboard=Dashboard.objects.get(slug="customer-dna"))
        self.client.force_login(user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()
        return user

    def post(self, **data):
        payload = {"cliente": "10832", "perfil": "basico", "next": "/dashes/dna-cliente/v2/?cliente=10832", **data}
        return self.client.post(reverse("dashesCustomerDnaEconodata"), payload)

    def test_admin_buys_only_for_the_customer_on_screen(self):
        user = self.login(is_system_admin=True)
        result = {"profile": "basico", "total": 1, "invalid": 0, "cached": 0, "to_fetch": 1, "calls": 1,
                  "approx_tokens": 20, "estimated_tokens": 20, "charged_tokens": 20, "found": 1, "not_found": 0,
                  "confirmed": True}
        with mock.patch.object(commercial_dna, "resolve_customer_cnpjs", return_value=["92791243000294"]) as resolve, \
                mock.patch.object(econodata, "enrich", return_value=result) as enrich:
            response = self.post(view="group", unidade="10833")
        self.assertRedirects(response, "/dashes/dna-cliente/v2/?cliente=10832", fetch_redirect_response=False)
        resolve.assert_called_once_with(10832, "group", 10833)
        enrich.assert_called_once_with(["92791243000294"], "basico", confirm=True, user=user, origem="dna-cliente")
        self.assertIn("20 tokens cobrados", str(list(get_messages(response.wsgi_request))[0]))

    def test_strategic_profile_queries_only_the_main_cnpj(self):
        self.login(is_system_admin=True)
        with mock.patch.object(commercial_dna, "resolve_customer_cnpjs", return_value=["92791243000294", "92791243000375"]), \
                mock.patch.object(econodata, "enrich", return_value={"to_fetch": 0, "invalid": 0}) as enrich:
            self.post(perfil="estrategico")
        self.assertEqual(enrich.call_args.args[0], ["92791243000294"])

    def test_regular_user_cannot_spend_tokens(self):
        self.login()
        with mock.patch.object(econodata, "enrich") as enrich:
            response = self.post()
        self.assertEqual(response.status_code, 302)
        enrich.assert_not_called()

    def test_get_and_foreign_redirect_are_ignored(self):
        self.login(is_system_admin=True)
        with mock.patch.object(econodata, "enrich") as enrich:
            response = self.client.get(reverse("dashesCustomerDnaEconodata"))
            foreign = self.post(perfil="xyz", next="https://evil.example/")
        enrich.assert_not_called()
        self.assertRedirects(response, reverse("dashesCustomerDnaV2Page"), fetch_redirect_response=False)
        self.assertRedirects(foreign, reverse("dashesCustomerDnaV2Page"), fetch_redirect_response=False)

    def test_budget_block_is_shown_as_a_message(self):
        self.login(is_system_admin=True)
        with mock.patch.object(commercial_dna, "resolve_customer_cnpjs", return_value=["92791243000294"]), \
                mock.patch.object(econodata, "enrich", side_effect=econodata.EconodataBudgetExceeded("teto")):
            response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertIn("teto", str(list(get_messages(response.wsgi_request))[0]))
