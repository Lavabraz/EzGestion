import unittest

from parser import parse_activity


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


if __name__ == "__main__":
    unittest.main()
