"""BI de Viagens: saneamento dos dados e ciclo de indicadores.

O ERP não entra nestes testes — a conexão Oracle é substituída por linhas
controladas. O que se fixa aqui é a leitura que o painel faz do cadastro: onde
a base mente (data 1900-12-31, hodômetro no lugar da diferença, previsão
reescrita na baixa), o indicador precisa continuar dizendo a verdade.
"""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from . import travel_bi
from .models import Dashboard, DashboardAccess, TravelBiInsightSnapshot


User = get_user_model()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        password="secret123",
        userId=extra.pop("userId", username[:18]),
        email=extra.pop("email", f"{username}@example.com"),
        **extra,
    )


CARRIERS = [
    {"key": "1001", "code": 1001, "name": "Frota Matriz", "cnpj": "01.731.676/0001-18",
     "label": "Frota 1001", "total": 3},
]

DRIVERS = {"1001-7": "JOSE TARCISIO CORREA"}

ROUTES = {
    "1": {"key": "1", "name": "ROTA 01 SAO PAULO", "opportunistic": False,
          "type": "Rotas Principais"},
    "6": {"key": "6", "name": "ROTA 06 SANTA CATARINA", "opportunistic": True,
          "type": "Rotas de Aproveitamento"},
}

VEHICLES = {
    "ABC1D23": {"fleet": 1001, "model_code": 1, "model": "Volvo VM 270 6x2R",
                "year": "2021", "age": 5, "age_bucket": "de_3_5", "age_label": "Entre 3 e 5 anos"},
    "XYZ9K88": {"fleet": 1001, "model_code": 2, "model": "MERCEDES BENS 914 C",
                "year": "2001", "age": 25, "age_bucket": "acima_15", "age_label": "Mais de 15 anos"},
}

# Duas competências, uma fechada e outra em aberto. `_fleet_cost` compara a
# competência com o mês corrente, então o mês aberto é montado na hora do teste.
def cost_rows(today):
    closed = (today.replace(day=1) - timedelta(days=1)).strftime("%m%Y")
    return [
        {"emp_transp": "1-1001", "competencia": closed, "valor": 40000},
        {"emp_transp": "1-1001", "competencia": today.strftime("%m%Y"), "valor": 500},
    ]


# Recorte mínimo que exercita cada agregação sem precisar do banco.
FAKE_DATA = {
    "totals": [{
        "viagens": 10, "finalizadas": 8, "abertas": 2, "placas": 3, "motoristas": 2, "frotas": 1,
        "km_total": 8000, "km_medio": 1000, "viagens_com_km": 8,
        "litros_total": 2400, "viagens_com_litros": 8,
        "km_consumo": 8000, "litros_consumo": 2400, "viagens_consumo": 8,
        "adiantamento_total": 2400, "adiantamento_medio": 300, "viagens_com_adiantamento": 8,
        "duracao_media": 2.5, "viagens_com_duracao": 8, "com_erro": 2,
    }],
    "monthly": [],  # preenchido em `_monthly_rows`, que depende do mês corrente
    "validation": [{"rotulo": "Cadastro correto", "total": 8},
                   {"rotulo": "KM de chegada menor que o de saída", "total": 2}],
    "carriers": [{"codigo": 1001, "viagens": 10, "km": 8000, "km_consumo": 8000,
                  "litros_consumo": 2400, "adiantamento": 2400, "duracao": 2.5,
                  "com_erro": 2, "placas": 3, "motoristas": 2}],
    "drivers": [{"chave": "1001-7", "frota": 1001, "motorista": 7, "viagens": 6, "km": 5000,
                 "km_consumo": 5000, "litros_consumo": 1500, "adiantamento": 1500,
                 "duracao": 2.0, "com_erro": 1}],
    "vehicles": [
        {"placa": "ABC1D23", "frota": 1001, "viagens": 6, "km": 6000, "km_consumo": 6000,
         "litros_consumo": 1500, "km_medio": 1000, "com_erro": 1, "hodometro": 500000},
        {"placa": "XYZ9K88", "frota": 1001, "viagens": 2, "km": 2000, "km_consumo": 2000,
         "litros_consumo": 900, "km_medio": 1000, "com_erro": 1, "hodometro": 300000},
    ],
    "companies": [{"codigo": 1, "total": 10, "km": 8000}],
    "cargo_totals": [{"viagens_com_carga": 8, "peso": 80000, "pallets": 144, "pernas": 24,
                      "pernas_invalidas": 1, "viagens_multirota": 2,
                      "km_com_carga": 8000, "peso_com_km": 80000}],
    "cargo_bands": [{"faixa": "de_5_15t", "total": 8, "media": 10000, "pallets": 18,
                     "km_medio": 1000}],
    "routes": [
        {"rota": "1", "viagens": 6, "peso": 60000, "pallets": 108, "pernas": 18,
         "placas": 2, "motoristas": 2},
        {"rota": "6", "viagens": 4, "peso": 20000, "pallets": 36, "pernas": 6,
         "placas": 1, "motoristas": 1},
    ],
    "situations": [{"rotulo": "Finalizada", "total": 8}, {"rotulo": "Aberta", "total": 2}],
    "km_buckets": [{"faixa": "de_801_1500", "total": 8, "media": 1000, "km": 8000}],
    "duration_buckets": [{"faixa": "de_1_3d", "total": 8, "media": 2.5, "km_medio": 1000}],
    "forecast": [{"com_previsao": 8, "atrasadas": 3, "no_prazo": 5, "iguais": 5, "atraso_medio": 2}],
    "open_totals": [{"abertas": 4, "no_prazo": 1, "ate_7": 1, "de_8_a_30": 1, "acima_30": 0,
                     "sem_previsao": 1, "adiantamento": 900}],
    "open_trips": [{"empresa": 1, "viagem": 42, "frota": 1001, "motorista": 7,
                    "chv_motorista": "1001-7", "placa": "ABC1D23", "titulo": "T-1",
                    "data_geracao": None, "dt_saida": None, "dt_prev_saida": None,
                    "dt_prev_chegada": None, "adiantamento": 300, "dias_vencida": 45,
                    "dias_aberta": 60}],
}


def _cargo_monthly_rows(today):
    closed = today.replace(day=1) - timedelta(days=1)
    return [
        {"competencia": closed.strftime("%Y-%m"), "viagens": 6, "peso": 50000, "pallets": 90},
        {"competencia": today.strftime("%Y-%m"), "viagens": 2, "peso": 30000, "pallets": 54},
    ]


def _monthly_rows(today):
    """Série mensal ancorada no mês corrente, para casar com o custo."""
    closed = today.replace(day=1) - timedelta(days=1)
    return [
        {"competencia": closed.strftime("%Y-%m"), "viagens": 6, "finalizadas": 5, "km": 5000,
         "litros": 1500, "adiantamento": 1500, "com_erro": 1},
        {"competencia": today.strftime("%Y-%m"), "viagens": 4, "finalizadas": 3, "km": 3000,
         "litros": 900, "adiantamento": 900, "com_erro": 1},
    ]


def fake_query_many(statements, params):
    data = dict(
        FAKE_DATA,
        monthly=_monthly_rows(date.today()),
        cargo_monthly=_cargo_monthly_rows(date.today()),
    )
    return {name: data.get(name, []) for name in statements}


class TravelSanitizationTests(TestCase):
    """O saneamento não é detalhe de implementação: é o que separa o indicador
    do lixo do cadastro, então cada regra tem um teste."""

    def test_sentinel_date_is_treated_as_missing(self):
        # 1900-12-31 é o "não informado" do cadastro. Se o corte subir de ano,
        # datas reais começam a sumir; se descer, a sentinela volta a contar.
        self.assertEqual(travel_bi.DATE_FLOOR_SQL, "DATE '1901-01-01'")
        self.assertIn("A.USU_DATSAI > DATE '1901-01-01'", travel_bi._BASE_SQL)
        self.assertIn("A.USU_DATCHE > DATE '1901-01-01'", travel_bi._BASE_SQL)
        self.assertIn("A.USU_DTPCHE > DATE '1901-01-01'", travel_bi._BASE_SQL)

    def test_km_outside_the_sane_range_is_excluded_from_distance(self):
        # Hodômetro digitado no lugar da diferença marca meio milhão de km.
        self.assertIn(
            f"CASE WHEN V.KM_RODADO BETWEEN {travel_bi.KM_MIN} AND {travel_bi.KM_MAX} "
            "THEN V.KM_RODADO END AS KM_VALIDO",
            travel_bi._BASE_SQL,
        )

    def test_consumption_only_uses_coherent_pairs(self):
        # KM e litros precisam fechar entre si: um dos dois errado inventaria
        # um consumo que nenhum caminhão faz.
        self.assertIn(
            f"V.KM_RODADO / V.LITROS BETWEEN {travel_bi.CONSUMPTION_MIN} "
            f"AND {travel_bi.CONSUMPTION_MAX}",
            travel_bi._BASE_SQL,
        )

    def test_open_trip_with_zero_km_is_not_a_registration_error(self):
        # Viagem que ainda não voltou tem KM zerado por definição; marcar isso
        # como erro encheria o painel de falso positivo.
        self.assertIn(
            "WHEN V.KM_RODADO = 0 AND V.COD_SITUACAO = 'F' THEN 'KM de chegada igual ao de saída'",
            travel_bi._BASE_SQL,
        )

    def test_weekday_grouping_does_not_depend_on_database_locale(self):
        # TO_CHAR(data, 'D') muda de 1=domingo para 1=segunda conforme o NLS do
        # banco; TRUNC(...,'IW') é sempre segunda-feira.
        with patch.object(travel_bi, "_query_many", side_effect=lambda s, p: {
            "departure_heat": [{"dia": 0, "hora": 7, "total": 5}],
            "monthly_efficiency": [], "km_band_consumption": [],
            "driver_efficiency": [], "advance_bands": [],
        }), patch.object(travel_bi, "list_carriers", return_value=CARRIERS):
            deep = travel_bi.compute_deep_analytics("12", "all", "all")

        monday = deep["heatmap"]["rows"][0]
        self.assertEqual(monday["label"], "Seg")
        self.assertEqual(monday["cells"][1]["total"], 5)


class TravelDashboardTests(TestCase):
    def load(self, period="12", carrier="all", situation="all"):
        with patch.object(travel_bi, "_query_many", side_effect=fake_query_many):
            return travel_bi.load_travel_dashboard(
                period, carrier, situation,
                carriers_available=CARRIERS,
                drivers_catalog=DRIVERS,
                vehicles_catalog=VEHICLES,
                routes_catalog=ROUTES,
                cost_rows=cost_rows(date.today()),
            )

    def test_headline_metrics(self):
        dashboard = self.load()
        metrics = dashboard["metrics"]

        self.assertEqual(metrics["trips_display"], "10")
        self.assertEqual(metrics["km_total_display"], "8.000 km")
        # 8000 km / 2400 L
        self.assertEqual(metrics["consumption_display"], "3,33")
        # R$ 2.400 de adiantamento sobre 8.000 km. Este e o adiantamento por km,
        # nao o custo da frota: o custo contabil tem indicador proprio.
        self.assertEqual(metrics["advance_per_km_display"], "R$ 0,30")
        self.assertEqual(metrics["duration_average_display"], "2d 12h")

    def test_metrics_carry_the_base_they_were_measured_on(self):
        # Uma média sem a base vira número solto: quem lê precisa saber que o
        # consumo saiu de 8 das 10 viagens.
        metrics = self.load()["metrics"]

        self.assertEqual(metrics["consumption_base"], 8)
        self.assertEqual(metrics["consumption_base_pct"], 80.0)
        self.assertEqual(metrics["km_coverage_pct"], 80.0)

    def test_validation_shares_are_over_the_failures_not_the_total(self):
        validation = self.load()["validation"]

        self.assertEqual(validation["correct"], 8)
        self.assertEqual(validation["correct_pct"], 80.0)
        self.assertEqual(validation["wrong"], 2)
        self.assertEqual([item["share_pct"] for item in validation["reasons"]], [100.0])

    def test_forecast_ignores_the_rows_rewritten_on_closing(self):
        # A chegada prevista é reescrita na baixa: comparar as 8 diria que 62%
        # chegaram no prazo, o que é a reescrita falando, não a operação.
        forecast = self.load()["forecast"]

        self.assertEqual(forecast["measured"], 8)
        self.assertEqual(forecast["same_date"], 5)
        self.assertEqual(forecast["comparable"], 3)
        self.assertEqual(forecast["late"], 3)
        self.assertEqual(forecast["late_pct"], 100.0)

    def test_open_backlog_buckets_add_up_to_the_total(self):
        backlog = self.load()["open_backlog"]

        self.assertEqual(backlog["total"], 4)
        self.assertEqual(sum(backlog["counts"].values()), 4)
        self.assertEqual(backlog["counts"]["sem_previsao"], 1)

    def test_open_trip_without_forecast_shows_a_dash_not_a_fake_date(self):
        item = self.load()["open_backlog"]["items"][0]

        self.assertEqual(item["forecast_display"], "-")
        self.assertEqual(item["days_overdue"], 45)
        self.assertEqual(item["tone"], "red")

    def test_outliers_need_a_minimum_history(self):
        # XYZ9K88 consome 2,22 km/l contra 3,33 da frota (-33%) e seria o maior
        # desvio da lista, mas tem só 2 viagens: com essa base a média não se
        # sustenta e a placa fica fora. ABC1D23, com 6 viagens e +20%, entra.
        outliers = self.load()["outliers"]

        self.assertEqual([item["plate"] for item in outliers["items"]], ["ABC1D23"])
        self.assertEqual(outliers["reference"], 3.33)

    def test_outlier_is_reported_when_the_history_is_enough(self):
        data = {name: list(rows) for name, rows in FAKE_DATA.items()}
        data["vehicles"] = [
            dict(FAKE_DATA["vehicles"][0]),
            dict(FAKE_DATA["vehicles"][1], viagens=9),
        ]
        data["monthly"] = _monthly_rows(date.today())
        with patch.object(travel_bi, "_query_many", side_effect=lambda s, p: {
            name: data.get(name, []) for name in s
        }):
            dashboard = travel_bi.load_travel_dashboard(
                "12", "all", "all",
                carriers_available=CARRIERS,
                drivers_catalog=DRIVERS,
                vehicles_catalog=VEHICLES,
                routes_catalog=ROUTES,
                cost_rows=cost_rows(date.today()),
            )

        outlier = dashboard["outliers"]["items"][0]
        self.assertEqual(outlier["plate"], "XYZ9K88")
        self.assertEqual(outlier["tone"], "red")
        self.assertLess(outlier["deviation_pct"], 0)

    def test_unknown_filter_values_fall_back_instead_of_raising(self):
        scope = self.load(period="nao-existe", carrier="9999", situation="Z")["scope"]

        self.assertEqual(scope["period"]["key"], "12")
        self.assertEqual(scope["carrier"]["key"], "all")
        self.assertEqual(scope["situation"]["key"], "all")

    def test_fingerprint_changes_with_the_numbers(self):
        dashboard = self.load()
        before = travel_bi.travel_dashboard_fingerprint(dashboard)

        dashboard["metrics"]["trips"] = 11
        self.assertNotEqual(before, travel_bi.travel_dashboard_fingerprint(dashboard))

    def test_ai_payload_declares_the_data_it_had_to_discard(self):
        dashboard = self.load()
        payload = travel_bi.build_travel_ai_payload(dashboard, "fp")
        notes = " ".join(payload["data_quality_notes"])

        self.assertIn("1900-12-31", notes)
        self.assertIn("10000 km", notes.replace(".", ""))
        self.assertIn("reescrita", notes)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class TravelBiPageTests(TestCase):
    def setUp(self):
        self.dashboard = Dashboard.objects.get(slug="viagens")
        self.url = reverse("dashesTravelBiPage")
        self.user = make_user("logistica")
        DashboardAccess.objects.create(user=self.user, dashboard=self.dashboard)

    def sign_in(self, user=None):
        self.client.force_login(user or self.user)
        session = self.client.session
        session["dashes_authenticated"] = True
        session.save()

    def stubbed_erp(self):
        """Todas as idas ao ERP do painel, trocadas por dados controlados."""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.object(travel_bi, "_query_many", side_effect=fake_query_many))
        stack.enter_context(patch.object(travel_bi, "list_carriers", return_value=CARRIERS))
        stack.enter_context(patch.object(travel_bi, "load_drivers_catalog", return_value=DRIVERS))
        stack.enter_context(patch.object(travel_bi, "load_vehicles_catalog", return_value=VEHICLES))
        stack.enter_context(patch.object(travel_bi, "load_routes_catalog", return_value=ROUTES))
        stack.enter_context(
            patch.object(travel_bi, "load_fleet_cost", return_value=cost_rows(date.today()))
        )
        return stack

    def test_migration_registers_the_panel_in_the_catalog(self):
        self.assertEqual(self.dashboard.url_name, "dashesTravelBiPage")
        self.assertTrue(self.dashboard.is_active)

    def test_panel_requires_explicit_permission(self):
        outsider = make_user("semacesso")
        other = Dashboard.objects.get(slug="ti-bi")
        DashboardAccess.objects.create(user=outsider, dashboard=other)
        self.sign_in(outsider)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "tiqueue/dashes_denied.html")

    def test_page_renders_the_dashboard(self):
        self.sign_in()
        with self.stubbed_erp():
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tiqueue/travel_bi.html")
        self.assertIsNone(response.context["data_error"])
        self.assertContains(response, "Qualidade do cadastro")
        self.assertContains(response, "Viagens em aberto")

    def test_erp_failure_shows_a_message_instead_of_a_stack_trace(self):
        self.sign_in()
        with patch.object(travel_bi, "list_carriers", side_effect=RuntimeError("ORA-12541")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("ORA-12541", response.context["data_error"])
        self.assertIsNone(response.context["dashboard"])

    def test_prepare_stores_one_snapshot_per_scope(self):
        self.sign_in()
        with self.stubbed_erp(), \
             patch("tiqueue.views.compute_travel_deep_analytics", return_value={"heatmap": {}}):
            first = self.client.post(reverse("travelBiPrepareInsights"),
                                     {"periodo": "12", "frota": "all", "situacao": "all"})
            second = self.client.post(reverse("travelBiPrepareInsights"),
                                      {"periodo": "6", "frota": "all", "situacao": "all"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        # Recortes diferentes não se sobrescrevem: cada um guarda o seu.
        self.assertEqual(TravelBiInsightSnapshot.objects.count(), 2)
        snapshot = TravelBiInsightSnapshot.objects.get(period_key="12")
        self.assertEqual(snapshot.status, TravelBiInsightSnapshot.STATUS_PREPARED)
        self.assertTrue(snapshot.ai_payload)
        self.assertEqual(snapshot.created_by, self.user)

    def test_prepare_reports_an_erp_failure_as_503(self):
        self.sign_in()
        with patch.object(travel_bi, "list_carriers", side_effect=RuntimeError("ORA-12541")):
            response = self.client.post(reverse("travelBiPrepareInsights"),
                                        {"periodo": "12", "frota": "all", "situacao": "all"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")

    def test_ai_is_refused_before_the_indicators_exist(self):
        self.sign_in()

        response = self.client.post(reverse("travelBiRequestAiInsights"),
                                    {"periodo": "12", "frota": "all", "situacao": "all"})

        self.assertEqual(response.status_code, 409)

    def test_ai_respects_the_daily_quota(self):
        # Cota estourada nem chega a chamar a OpenAI — o teto é por usuário e
        # vale para todos os painéis do Dashes.
        self.user.dashes_ai_daily_limit = 0
        self.user.save(update_fields=["dashes_ai_daily_limit"])
        self.sign_in()
        TravelBiInsightSnapshot.objects.create(
            period_key="12", carrier_key="all", situation_key="all",
            source_fingerprint="fp", ai_payload={"x": 1},
        )

        with patch("tiqueue.views.generate_customer_insights") as ai_call:
            response = self.client.post(reverse("travelBiRequestAiInsights"),
                                        {"periodo": "12", "frota": "all", "situacao": "all"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "ai_daily_limit")
        ai_call.assert_not_called()

    def test_pdf_is_refused_before_the_indicators_exist(self):
        self.sign_in()

        response = self.client.get(reverse("travelBiExportPdf") + "?periodo=12&frota=all&situacao=all")

        self.assertEqual(response.status_code, 409)


class SupportCatalogTests(TestCase):
    """Cadastros de apoio: motorista, veículo e custo contábil.

    O risco destas fontes não é o valor, é a junção. O cadastro de veículo
    repete a mesma placa e chegou a inflar 4.596 viagens para 8.509 linhas; o
    contábil traz competência que a viagem não tem. Cada armadilha tem um teste.
    """

    def test_driver_name_comes_from_the_catalog(self):
        self.assertEqual(travel_bi._driver_name({"1001-7": "JOSE TARCISIO"}, "1001-7", 7), "JOSE TARCISIO")

    def test_driver_without_registration_falls_back_to_the_code(self):
        # Motorista novo pode rodar antes de ser cadastrado; a linha não pode
        # virar um espaço em branco na tabela.
        self.assertEqual(travel_bi._driver_name({}, "1001-9", 9), "Motorista 9")
        self.assertEqual(travel_bi._driver_name({"1001-9": "   "}, "1001-9", 9), "Motorista 9")

    def test_vehicle_catalog_keeps_one_row_per_plate(self):
        # `ROW_NUMBER` é o que impede a junção de multiplicar as viagens: a mesma
        # placa está cadastrada em mais de uma transportadora e em mais de uma
        # grafia. O registro ativo vem na frente.
        self.assertIn("ROW_NUMBER() OVER (", travel_bi.VEHICLES_SQL)
        self.assertIn("CASE WHEN A.SITVEI = 'A' THEN 0 ELSE 1 END", travel_bi.VEHICLES_SQL)
        self.assertIn("WHERE RN = 1", travel_bi.VEHICLES_SQL)

    def test_vehicle_age_buckets(self):
        today = date(2026, 8, 21)
        for year, expected in (
            ("2026", "ate_3"), ("2023", "ate_3"), ("2022", "de_3_5"),
            # Idade 5 fecha a faixa "Entre 3 e 5", como na regra de origem;
            # 2020 (6 anos) e o primeiro da faixa seguinte.
            ("2021", "de_3_5"), ("2020", "de_5_10"),
            ("2013", "de_10_15"), ("2001", "acima_15"),
        ):
            self.assertEqual(travel_bi._vehicle_age(year, today)[1], expected, year)

    def test_impossible_model_year_is_not_a_brand_new_truck(self):
        # O cadastro tem um veículo com ANOVEI 2029. Na conta original ele saía
        # com idade -3 e caía em "Menos de 3 anos", entrando na frota nova.
        age, bucket = travel_bi._vehicle_age("2029", date(2026, 8, 21))

        self.assertIsNone(age)
        self.assertEqual(bucket, "indefinido")

    def test_non_numeric_model_year_does_not_break_the_panel(self):
        # ANOVEI é VARCHAR2: lixo na coluna não pode derrubar a tela inteira.
        self.assertEqual(travel_bi._vehicle_age("n/d", date(2026, 8, 21)), (None, "indefinido"))
        self.assertEqual(travel_bi._vehicle_age(None, date(2026, 8, 21)), (None, "indefinido"))

    def test_unregistered_plate_stays_out_of_the_age_buckets(self):
        # Placa sem cadastro não tem "idade não determinada": ela não tem
        # cadastro, e misturar as duas coisas esconde a segunda.
        vehicles = [
            {"registered": True, "age_bucket": "de_3_5", "trips": 5, "km": 5000.0,
             "liters": 1500.0, "model": "Volvo VM"},
            {"registered": False, "age_bucket": "indefinido", "trips": 2, "km": 1000.0,
             "liters": 300.0, "model": "Placa sem cadastro"},
        ]

        profile = travel_bi._fleet_profile(vehicles, trips=7)

        self.assertEqual([item["key"] for item in profile["ages"]], ["de_3_5"])
        self.assertEqual(profile["plates"], 1)
        self.assertEqual(profile["unregistered_plates"], 1)
        self.assertEqual(profile["unregistered_trips"], 2)


class FleetCostTests(TestCase):
    TODAY = date(2026, 8, 21)

    def build(self, rows, monthly=None):
        return travel_bi._fleet_cost(
            rows,
            monthly if monthly is not None else [
                {"competencia": "2026-07", "km": 100000.0},
                {"competencia": "2026-08", "km": 20000.0},
            ],
            km_total=120000.0,
            advance_total=30000.0,
            carrier_names={"1001": {"label": "Frota 1001"}},
            today=self.TODAY,
        )

    def test_open_competencia_stays_out_of_the_cost_per_km(self):
        # O custo do mês corrente só entra no fechamento: em 21/08 valia R$ 14
        # mil contra R$ 780 mil de um mês fechado. Na média, derruba o R$/km.
        cost = self.build([
            {"emp_transp": "1-1001", "competencia": "072026", "valor": 400000},
            {"emp_transp": "1-1001", "competencia": "082026", "valor": 15000},
        ])

        self.assertEqual(cost["closed_months"], 1)
        self.assertEqual(cost["open_month"], "08/2026")
        # 400.000 / 100.000 km — o mês aberto não entra em cima nem embaixo.
        self.assertEqual(cost["per_km"], 4.0)
        self.assertEqual(cost["closed_total"], 400000)
        # O total bruto continua somando tudo, para a série não mentir.
        self.assertEqual(cost["total"], 415000)

    def test_open_competencia_is_flagged_in_the_series(self):
        cost = self.build([
            {"emp_transp": "1-1001", "competencia": "072026", "valor": 400000},
            {"emp_transp": "1-1001", "competencia": "082026", "valor": 15000},
        ])

        self.assertEqual([item["is_open"] for item in cost["months"]], [False, True])
        self.assertEqual([item["label"] for item in cost["months"]], ["07/2026", "08/2026"])

    def test_advance_is_a_slice_of_the_cost_not_an_addition(self):
        # O adiantamento já está lançado no contábil; somá-lo contaria duas vezes.
        cost = self.build([{"emp_transp": "1-1001", "competencia": "072026", "valor": 400000}])

        self.assertEqual(cost["advance_share_pct"], 7.5)

    def test_series_is_ordered_by_calendar_not_by_the_mmyyyy_string(self):
        # A competência é 'MMYYYY': ordenar como texto colocaria 01/2026 antes
        # de 12/2025.
        cost = self.build(
            [
                {"emp_transp": "1-1001", "competencia": "012026", "valor": 10},
                {"emp_transp": "1-1001", "competencia": "122025", "valor": 20},
            ],
            monthly=[],
        )

        self.assertEqual([item["label"] for item in cost["months"]], ["12/2025", "01/2026"])

    def test_cost_without_a_closed_month_reports_a_dash(self):
        cost = self.build([{"emp_transp": "1-1001", "competencia": "082026", "valor": 15000}])

        self.assertFalse(cost["has_closed_month"])
        self.assertEqual(cost["per_km_display"], "-")

    def test_future_dated_entries_are_excluded_in_sql(self):
        # O razão tem lançamento com data no futuro (provisão): 09/2026 e
        # 10/2026 apareciam com custo e zero km, inventando um R$/km.
        self.assertIn("AND A.DATLCT <= TRUNC(SYSDATE)", travel_bi.COST_SQL)

    def test_carrier_filter_follows_the_ledger_branch(self):
        # O CNPJ da frota vem da filial do rateio: 1-4 é a 1004, o resto é 1001.
        period = travel_bi.resolve_period("all")
        for key, expected in (("1004", "= '1-4'"), ("1001", "<> '1-4'")):
            carrier = travel_bi.resolve_carrier(key, [{"key": key, "code": int(key), "label": key, "cnpj": ""}])
            scope, _params = travel_bi._cost_scope_sql(period, carrier)
            self.assertIn(expected, scope)

    def test_closed_month_scope_binds_the_same_range_as_the_trips(self):
        period = travel_bi.resolve_period("mes-anterior", today=self.TODAY)
        scope, params = travel_bi._cost_scope_sql(period, travel_bi.resolve_carrier("all"))

        self.assertIn("A.DATLCT >= :cost_start AND A.DATLCT < :cost_end", scope)
        self.assertEqual(params, {"cost_start": date(2026, 7, 1), "cost_end": date(2026, 8, 1)})


class ConsumptionBaseTests(TestCase):
    """Numerador e denominador do consumo têm de vir do mesmo conjunto.

    O painel guarda dois quilômetros por placa: o total válido e o do
    subconjunto que também tem litros coerentes. Dividir o primeiro pelo
    segundo é o tipo de erro que não parece erro — só aparece quando uma faixa
    da frota mostra 36 km/l num caminhão.
    """

    def test_age_bucket_consumption_pairs_km_with_its_own_liters(self):
        vehicles = [
            # Roda 10.000 km, mas só 6.000 têm abastecimento lançado.
            {"registered": True, "age_bucket": "de_3_5", "trips": 10, "model": "Volvo VM",
             "km": 10000.0, "km_consumption": 6000.0, "liters": 2000.0},
        ]

        profile = travel_bi._fleet_profile(vehicles, trips=10)

        # 6.000 / 2.000 = 3,0. Usar os 10.000 km daria 5,0 — consumo que o
        # caminhão não faz, saído de uma base que não existe.
        self.assertEqual(profile["ages"][0]["consumption"], 3.0)
        # O km exibido continua sendo o total rodado, que é o que a frota andou.
        self.assertEqual(profile["ages"][0]["km_display"], "10.000 km")

    def test_bucket_consumption_reconciles_with_the_fleet_average(self):
        vehicles = [
            {"registered": True, "age_bucket": "de_3_5", "trips": 4, "model": "A",
             "km": 4000.0, "km_consumption": 4000.0, "liters": 1000.0},
            {"registered": True, "age_bucket": "acima_15", "trips": 2, "model": "B",
             "km": 2000.0, "km_consumption": 2000.0, "liters": 400.0},
        ]

        profile = travel_bi._fleet_profile(vehicles, trips=6)
        total_km = sum(item["km_consumption"] for item in vehicles)
        total_liters = sum(item["liters"] for item in vehicles)

        self.assertEqual([item["consumption"] for item in profile["ages"]], [4.0, 5.0])
        # A média da frota é o agregado, não a média das faixas.
        self.assertEqual(round(total_km / total_liters, 2), 4.29)

    def test_vehicle_without_fuel_records_does_not_inflate_the_bucket(self):
        vehicles = [
            {"registered": True, "age_bucket": "de_3_5", "trips": 5, "model": "A",
             "km": 5000.0, "km_consumption": 5000.0, "liters": 1500.0},
            # Placa que rodou e nunca teve litros lançado: entra no km da faixa
            # e fica fora do consumo dela.
            {"registered": True, "age_bucket": "de_3_5", "trips": 3, "model": "A",
             "km": 3000.0, "km_consumption": 0.0, "liters": 0.0},
        ]

        bucket = travel_bi._fleet_profile(vehicles, trips=8)["ages"][0]

        self.assertEqual(bucket["consumption"], round(5000 / 1500, 2))
        self.assertEqual(bucket["km_display"], "8.000 km")
        self.assertEqual(bucket["plates"], 2)


class CargoAndRoutesTests(TestCase):
    """Roteiro e rotas.

    O roteiro tem várias linhas por viagem e mais de uma rota por viagem: os
    dois fatos decidem o que pode ser somado e o que não pode. E a tabela só
    passou a ser preenchida em setembro de 2025, então cobertura não é detalhe,
    é o que separa "caiu o volume" de "não tem cadastro".
    """

    def build(self, routes=None, totals=None):
        data = {
            "cargo_totals": [totals or {
                "viagens_com_carga": 8, "peso": 80000, "pallets": 144,
                "pernas": 24, "pernas_invalidas": 1, "viagens_multirota": 2,
            }],
            "cargo_bands": [{"faixa": "de_5_15t", "total": 8, "media": 10000,
                             "pallets": 18, "km_medio": 1000}],
            "routes": routes if routes is not None else [
                {"rota": "1", "viagens": 6, "peso": 60000, "pallets": 108,
                 "pernas": 18, "placas": 2, "motoristas": 2},
                {"rota": "6", "viagens": 4, "peso": 20000, "pallets": 36,
                 "pernas": 6, "placas": 1, "motoristas": 1},
            ],
        }
        return travel_bi._cargo(data, trips=10, routes_catalog=ROUTES)

    def test_route_number_is_normalized_on_both_sides(self):
        # O cadastro guarda o número dentro do nome ("ROTA 06") e o roteiro
        # grava '6' ou '06'. Sem tirar o zero dos dois lados, a rota 6 aparecia
        # partida em duas, uma delas "sem cadastro".
        self.assertIn("LTRIM(TRIM(SUBSTR(A.DESROE, 6, 2)), '0')", travel_bi.ROUTES_SQL)
        self.assertIn("LTRIM(TRIM(T.USU_ROTCID), '0')", travel_bi._ROUTES_CTE)

    def test_route_type_follows_the_source_report(self):
        cargo = self.build()

        by_key = {item["key"]: item for item in cargo["routes"]}
        self.assertFalse(by_key["1"]["opportunistic"])
        self.assertTrue(by_key["6"]["opportunistic"])
        self.assertEqual(by_key["6"]["type"], "Rotas de Aproveitamento")

    def test_weight_splits_between_route_kinds(self):
        kinds = {item["label"]: item for item in self.build()["kinds"]}

        self.assertEqual(kinds["Rotas Principais"]["weight"], 60000)
        self.assertEqual(kinds["Rotas de Aproveitamento"]["weight"], 20000)
        self.assertEqual(kinds["Rotas de Aproveitamento"]["share_pct"], 25.0)

    def test_trips_per_route_may_exceed_the_trip_total(self):
        # Uma viagem de duas rotas conta em cada uma. Somar a coluna e comparar
        # com o total de viagens é o erro que a nota da tela previne.
        cargo = self.build()

        self.assertEqual(sum(item["trips"] for item in cargo["routes"]), 10)
        self.assertEqual(cargo["trips_with_cargo"], 8)
        self.assertEqual(cargo["multi_route_trips"], 2)

    def test_route_without_registration_still_shows_up(self):
        cargo = self.build(routes=[
            {"rota": "99", "viagens": 1, "peso": 1000, "pallets": 2,
             "pernas": 1, "placas": 1, "motoristas": 1},
        ])

        route = cargo["routes"][0]
        self.assertEqual(route["name"], "Rota 99")
        self.assertFalse(route["registered"])
        self.assertEqual(cargo["unregistered_routes"], ["99"])

    def test_coverage_is_reported_against_all_trips(self):
        # 8 de 10 viagens têm roteiro; a carga por viagem é sobre as 8, não 10.
        cargo = self.build()

        self.assertEqual(cargo["coverage_pct"], 80.0)
        self.assertEqual(cargo["weight_display"], "80,0 t")
        self.assertEqual(cargo["weight_per_trip_display"], "10,0 t")

    def test_impossible_leg_weight_is_dropped_in_sql(self):
        # Uma perna com 720.398 kg para 18 paletes: a base pratica ~537 kg por
        # palete, então a carga real era ~10 t.
        self.assertIn(
            f"SUM(CASE WHEN T.USU_PESCAR BETWEEN 0 AND {travel_bi.CARGO_MAX_KG}",
            travel_bi._ROUTES_CTE,
        )
        self.assertIn(
            f"SUM(CASE WHEN T.USU_PESCAR > {travel_bi.CARGO_MAX_KG} THEN 1 ELSE 0 END)",
            travel_bi._ROUTES_CTE,
        )

    def test_routes_are_aggregated_before_touching_the_trips(self):
        # É o que impede a viagem de ser multiplicada: o roteiro vira uma linha
        # por viagem antes de encostar em BASE.
        self.assertIn("ROTEIRO_VIAGEM AS (", travel_bi._ROUTES_CTE)
        self.assertIn("GROUP BY EMPRESA, VIAGEM", travel_bi._ROUTES_CTE)

    def test_empty_routing_reports_no_data_instead_of_zeros(self):
        cargo = travel_bi._cargo({}, trips=10, routes_catalog=ROUTES)

        self.assertFalse(cargo["has_data"])
        self.assertEqual(cargo["coverage_pct"], 0.0)
        self.assertEqual(cargo["weight_per_trip_display"], "-")


class CostPerTonTests(TestCase):
    TODAY = date(2026, 8, 21)

    def test_cost_per_ton_only_counts_competencias_that_have_cargo(self):
        # O roteiro começa em setembro de 2025: nas competências anteriores o
        # denominador seria zero, não baixo, e o R$/t iria para o infinito.
        cost = travel_bi._fleet_cost(
            [
                {"emp_transp": "1-1001", "competencia": "062026", "valor": 300000},
                {"emp_transp": "1-1001", "competencia": "072026", "valor": 400000},
            ],
            monthly=[{"competencia": "2026-06", "km": 80000.0},
                     {"competencia": "2026-07", "km": 100000.0}],
            km_total=180000.0,
            advance_total=30000.0,
            carrier_names={},
            today=self.TODAY,
            # Só julho tem carga registrada.
            cargo_monthly=[{"competencia": "2026-07", "peso": 400000}],
        )

        self.assertEqual(cost["cargo_months"], 1)
        # R$ 400.000 sobre 400 t — junho fica fora dos dois lados da conta.
        self.assertEqual(cost["per_ton"], 1000.0)
        # O custo por km continua olhando as duas competências fechadas.
        self.assertEqual(cost["per_km"], round(700000 / 180000, 2))

    def test_cost_per_ton_is_a_dash_without_cargo(self):
        cost = travel_bi._fleet_cost(
            [{"emp_transp": "1-1001", "competencia": "072026", "valor": 400000}],
            monthly=[{"competencia": "2026-07", "km": 100000.0}],
            km_total=100000.0, advance_total=0, carrier_names={},
            today=self.TODAY, cargo_monthly=[],
        )

        self.assertFalse(cost["has_cargo"])
        self.assertEqual(cost["per_ton_display"], "-")
