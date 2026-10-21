import unittest
from datetime import date

from parser import parse_activity, parse_activity_date


class ParseActivityTests(unittest.TestCase):
    def test_uses_explicit_activity_date(self):
        parsed = parse_activity("1h30 activité", activity_date="19/10/2026")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.activity_date, "2026-10-19")

    def test_rejects_invalid_explicit_activity_date(self):
        self.assertIsNone(
            parse_activity("1h30 activité", activity_date="31/02/2026")
        )

    def test_keeps_legacy_trailing_activity_date(self):
        parsed = parse_activity("1h30 développement 19/10/2026")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.task, "développement")
        self.assertEqual(parsed.activity_date, "2026-10-19")

    def test_accepts_french_iso_and_short_date_formats(self):
        for value in ("21/10/2026", "21-10-2026", "21.10.2026", "2026-10-21"):
            with self.subTest(value=value):
                self.assertEqual(parse_activity_date(value), date(2026, 10, 21))

        self.assertEqual(
            parse_activity_date("21/10", today=date(2026, 1, 1)),
            date(2026, 10, 21),
        )

    def test_accepts_relative_dates_with_fixed_reference_day(self):
        today = date(2026, 10, 21)

        self.assertEqual(parse_activity_date("aujourd'hui", today), today)
        self.assertEqual(parse_activity_date("aujourd’hui", today), today)
        self.assertEqual(
            parse_activity_date("hier", today),
            date(2026, 10, 20),
        )
        self.assertEqual(
            parse_activity("1h activité hier", today=today).activity_date,
            "2026-10-20",
        )

    def test_accepts_iso_and_relative_dates_at_end_of_message(self):
        self.assertEqual(
            parse_activity("1h développement 2026-10-21").activity_date,
            "2026-10-21",
        )
        self.assertEqual(
            parse_activity(
                "1h développement hier",
                today=date(2026, 10, 21),
            ).activity_date,
            "2026-10-20",
        )

    def test_rejects_invalid_trailing_date(self):
        self.assertIsNone(parse_activity("1h développement 31/02/2026"))


if __name__ == "__main__":
    unittest.main()
