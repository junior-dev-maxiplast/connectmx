from datetime import date, datetime

from django.test import SimpleTestCase

from .customer_dna import (
    DNA_RANGE_KEY,
    _filter_rows_by_period,
    parse_dna_range,
    resolve_dna_range,
)


class ParseDnaRangeTests(SimpleTestCase):
    def test_without_dates_the_preset_period_keeps_working(self):
        self.assertEqual(parse_dna_range("", ""), (None, None, None))
        self.assertEqual(parse_dna_range(None, None), (None, None, None))

    def test_valid_range_is_parsed_as_dates(self):
        start, end, error = parse_dna_range("2026-03-01", "2026-03-31")
        self.assertEqual((start, end, error), (date(2026, 3, 1), date(2026, 3, 31), None))

    def test_inverted_range_is_rejected_with_a_message(self):
        start, end, error = parse_dna_range("2026-05-01", "2026-04-01")
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertIn("posterior", error)

    def test_only_one_date_is_rejected(self):
        start, end, error = parse_dna_range("2026-03-01", "")
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertIsNotNone(error)

    def test_malformed_date_is_rejected(self):
        start, end, error = parse_dna_range("01/03/2026", "31/03/2026")
        self.assertIsNone(start)
        self.assertIsNotNone(error)


class ResolveDnaRangeTests(SimpleTestCase):
    def test_range_is_identified_and_carries_its_url_query(self):
        period = resolve_dna_range(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(period["key"], DNA_RANGE_KEY)
        self.assertEqual(period["query"], "inicio=2026-03-01&fim=2026-03-31")
        self.assertEqual(period["full_label"], "Personalizado (01/03/2026 a 31/03/2026)")

    def test_end_date_is_inclusive_for_the_user(self):
        period = resolve_dna_range(date(2026, 3, 1), date(2026, 3, 31))
        # O filtro usa fim exclusivo: o último dia escolhido entra por inteiro.
        self.assertEqual(period["end"], date(2026, 4, 1))

    def test_short_range_is_daily_and_long_range_is_monthly(self):
        short = resolve_dna_range(date(2026, 3, 1), date(2026, 3, 31))
        long = resolve_dna_range(date(2025, 1, 1), date(2026, 3, 31))
        self.assertEqual(short["granularity"], "day")
        self.assertEqual(long["granularity"], "month")

    def test_single_day_range_works(self):
        period = resolve_dna_range(date(2026, 3, 15), date(2026, 3, 15))
        rows = [{"data_geracao": date(2026, 3, 15)}, {"data_geracao": date(2026, 3, 16)}]
        kept = _filter_rows_by_period(rows, period, "data_geracao")
        self.assertEqual(kept, [{"data_geracao": date(2026, 3, 15)}])


class FilterRowsByRangeTests(SimpleTestCase):
    def test_rows_outside_the_range_are_dropped_and_boundaries_are_kept(self):
        period = resolve_dna_range(date(2026, 3, 10), date(2026, 3, 20))
        rows = [
            {"data_geracao": date(2026, 3, 9)},   # antes do início
            {"data_geracao": date(2026, 3, 10)},  # primeiro dia: entra
            {"data_geracao": date(2026, 3, 20)},  # último dia: entra
            {"data_geracao": date(2026, 3, 21)},  # depois do fim
        ]
        kept = _filter_rows_by_period(rows, period, "data_geracao")
        self.assertEqual(
            [row["data_geracao"] for row in kept],
            [date(2026, 3, 10), date(2026, 3, 20)],
        )

    def test_range_accepts_datetimes_like_the_erp_returns(self):
        # O Oracle devolve DATE como datetime (com hora): o dia tem que contar.
        period = resolve_dna_range(date(2026, 3, 10), date(2026, 3, 20))
        rows = [
            {"data_geracao": datetime(2026, 3, 20, 23, 59)},
            {"data_geracao": datetime(2026, 3, 21, 0, 0)},
        ]
        kept = _filter_rows_by_period(rows, period, "data_geracao")
        self.assertEqual(kept, [{"data_geracao": datetime(2026, 3, 20, 23, 59)}])
