"""Recortes de período dos painéis do Dashes.

O que se fixa aqui é a diferença entre os dois tipos de recorte: janela móvel
("1 mês" é de hoje para trás) e mês fechado ("mês passado" tem começo e fim e
não se mexe mais). Confundir os dois faz o número de fechamento mudar sozinho
de um dia para o outro.
"""

from datetime import date

from django.test import TestCase

from . import bi_periods, it_bi, travel_bi


class PreviousMonthRangeTests(TestCase):
    def test_range_starts_on_the_first_and_ends_on_the_next_first(self):
        start, end = bi_periods.previous_month_range(date(2026, 8, 21))

        self.assertEqual(start, date(2026, 7, 1))
        # Fim exclusivo: pega 31/07 às 23h59 sem depender do tamanho do mês.
        self.assertEqual(end, date(2026, 8, 1))

    def test_january_falls_back_to_december_of_the_previous_year(self):
        start, end = bi_periods.previous_month_range(date(2026, 1, 9))

        self.assertEqual(start, date(2025, 12, 1))
        self.assertEqual(end, date(2026, 1, 1))

    def test_march_first_reaches_february_not_a_missing_day(self):
        # Subtrair 30 dias de 01/03 cairia em janeiro; o cálculo anda por mês.
        start, end = bi_periods.previous_month_range(date(2026, 3, 1))

        self.assertEqual(start, date(2026, 2, 1))
        self.assertEqual(end, date(2026, 3, 1))

    def test_leap_february_is_covered_whole(self):
        start, end = bi_periods.previous_month_range(date(2024, 3, 15))

        self.assertEqual(start, date(2024, 2, 1))
        self.assertEqual(end, date(2024, 3, 1))


class ResolvePeriodTests(TestCase):
    def test_one_month_is_a_rolling_window(self):
        period = bi_periods.resolve_period("1")

        self.assertEqual(period["months"], 1)
        self.assertIsNone(period["start"])
        self.assertEqual(period["label"], "1 mês")

    def test_previous_month_is_a_closed_range(self):
        period = bi_periods.resolve_period("mes-anterior", today=date(2026, 8, 21))

        self.assertIsNone(period["months"])
        self.assertEqual(period["start"], date(2026, 7, 1))
        self.assertEqual(period["end"], date(2026, 8, 1))

    def test_previous_month_label_names_the_month_it_measured(self):
        # "Mês passado" guardado num snapshot vira ambíguo quando o mês vira: o
        # rótulo completo é o que vai para o PDF, para a IA e para o histórico.
        period = bi_periods.resolve_period("mes-anterior", today=date(2026, 1, 5))

        self.assertEqual(period["label"], "Mês passado")
        self.assertEqual(period["full_label"], "Mês passado (12/2025)")

    def test_unknown_value_falls_back_to_twelve_months(self):
        for raw in ("", None, "nao-existe", "99"):
            self.assertEqual(bi_periods.resolve_period(raw)["key"], "12")

    def test_both_panels_share_the_same_vocabulary(self):
        # As duas listas eram cópias e saíram de sincronia com facilidade.
        self.assertIs(it_bi.resolve_period, bi_periods.resolve_period)
        self.assertIs(travel_bi.resolve_period, bi_periods.resolve_period)
        self.assertEqual(
            [choice["key"] for choice in bi_periods.period_choices()],
            ["1", "3", "6", "12", "24", "mes-anterior", "all"],
        )


class ItBiScopeSqlTests(TestCase):
    """O BI do TI monta SQL de MySQL com marcador `%s`."""

    def scope(self, period_key):
        period = bi_periods.resolve_period(period_key, today=date(2026, 8, 21))
        company = it_bi.resolve_company("all")
        return it_bi._scope_sql(period, company)

    def test_rolling_window_uses_date_sub(self):
        where, params = self.scope("1")

        self.assertIn("A.date_open >= DATE_SUB(CURDATE(), INTERVAL %s MONTH)", where)
        self.assertEqual(params, [1])

    def test_previous_month_binds_a_bounded_range(self):
        where, params = self.scope("mes-anterior")

        self.assertIn("A.date_open >= %s AND A.date_open < %s", where)
        self.assertEqual(params, [date(2026, 7, 1), date(2026, 8, 1)])

    def test_previous_month_sql_has_no_percent_of_its_own(self):
        # `DATE_FORMAT(..., '%Y-%m-01')` quebraria a interpolação do driver: o
        # único `%` que pode existir aqui é o marcador de parâmetro.
        where, params = self.scope("mes-anterior")

        self.assertEqual(where.count("%"), len(params))

    def test_all_time_scope_has_no_date_clause(self):
        where, params = it_bi._scope_sql(bi_periods.ALL_TIME, it_bi.resolve_company("all"))

        self.assertNotIn("date_open", where)
        self.assertEqual(params, [])


class TravelBiScopeSqlTests(TestCase):
    """O BI de Viagens monta SQL de Oracle com marcador nomeado."""

    def scope(self, period_key, **kwargs):
        period = bi_periods.resolve_period(period_key, today=date(2026, 8, 21))
        carrier = travel_bi.resolve_carrier("all")
        situation = travel_bi.resolve_situation("all")
        return travel_bi._scope_sql(period, carrier, situation, **kwargs)

    def test_rolling_window_uses_add_months(self):
        where, params = self.scope("1")

        self.assertIn("ADD_MONTHS(TRUNC(SYSDATE), -:months)", where)
        self.assertEqual(params, {"months": 1})

    def test_previous_month_binds_a_bounded_range(self):
        where, params = self.scope("mes-anterior")

        self.assertIn("A.USU_DATGER >= :period_start AND A.USU_DATGER < :period_end", where)
        self.assertEqual(params, {"period_start": date(2026, 7, 1), "period_end": date(2026, 8, 1)})

    def test_open_trips_ignore_the_closed_month_too(self):
        # A fila de viagens abertas é a de hoje: filtrar por julho esconderia a
        # viagem que saiu em maio e nunca voltou.
        where, params = self.scope("mes-anterior", ignore_period=True)

        self.assertNotIn("USU_DATGER", where)
        self.assertEqual(params, {})
