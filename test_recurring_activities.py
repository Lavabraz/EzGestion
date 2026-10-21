import unittest
from datetime import date

from gantt import WorkCalendar, french_public_holidays
from recurring_activities import activities_for_date


class RecurringActivityTests(unittest.TestCase):
    def setUp(self):
        self.calendar = WorkCalendar(
            french_public_holidays(2026),
            [(date(2026, 10, 26), date(2026, 11, 1))],
        )

    def test_daily_activity_excludes_mondays_weekends_holidays_and_vacations(self):
        self.assertEqual(
            activities_for_date(date(2026, 10, 21), self.calendar),
            [("Réunion Daily", 5)],
        )
        for day in (
            date(2026, 10, 27),
            date(2026, 11, 11),
            date(2026, 10, 25),
        ):
            with self.subTest(day=day):
                self.assertEqual(activities_for_date(day, self.calendar), [])
        self.assertEqual(
            activities_for_date(date(2026, 10, 26), self.calendar),
            [("Réunion Hebdo", 35), ("Réunion Client", 45)],
        )

    def test_weekly_meetings_and_biweekly_gp_run_on_mondays(self):
        self.assertEqual(
            activities_for_date(date(2026, 10, 26), self.calendar),
            [("Réunion Hebdo", 35), ("Réunion Client", 45)],
        )
        self.assertEqual(
            activities_for_date(date(2026, 11, 2), self.calendar),
            [
                ("Réunion Hebdo", 35),
                ("Réunion Client", 45),
                ("Réunion Gp", 30),
            ],
        )
        self.assertEqual(
            activities_for_date(date(2026, 11, 9), self.calendar),
            [("Réunion Hebdo", 35), ("Réunion Client", 45)],
        )
        self.assertEqual(
            activities_for_date(date(2026, 11, 16), self.calendar),
            [
                ("Réunion Hebdo", 35),
                ("Réunion Client", 45),
                ("Réunion Gp", 30),
            ],
        )


if __name__ == "__main__":
    unittest.main()
