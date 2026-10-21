import unittest

from sheets import GoogleSheetWriter


class FakeWorksheet:
    def __init__(self):
        self.activity_rows = []
        self.wbs_hours = 0.0

    def get(self, range_name, value_render_option=None):
        if range_name == "F4:H":
            return self.activity_rows
        raise AssertionError(f"Unexpected range: {range_name}")

    def update(self, range_name, values, value_input_option=None):
        if range_name == "F4:H4":
            self.activity_rows.append(values[0])
        elif range_name == "K2":
            self.wbs_hours = values[0][0]
        else:
            raise AssertionError(f"Unexpected range: {range_name}")

    def cell(self, row, column):
        return type("Cell", (), {"value": "Réunion Daily"})()

    def acell(self, address):
        return type("Cell", (), {"value": self.wbs_hours})()


class RecurringActivitySheetTests(unittest.TestCase):
    def setUp(self):
        self.writer = object.__new__(GoogleSheetWriter)
        self.writer.start_row = 4
        self.writer.wbs_start_row = 2
        self.writer.wbs_tasks = [(2, "Réunion Daily")]
        self.writer.members = {
            "lucas": {
                "display": "Lucas",
                "date": "F",
                "duration": "G",
                "task": "H",
            }
        }
        self.writer.worksheet = FakeWorksheet()
        self.writer.wbs_worksheet = self.writer.worksheet

    def test_recurring_activity_updates_report_and_wbs_once(self):
        record = {
            "date": "2026-10-21",
            "duration_hours": 5 / 60,
            "member": "Lucas",
        }

        self.assertTrue(self.writer.write_recurring_activity(record, 2))
        self.assertFalse(self.writer.write_recurring_activity(record, 2))

        self.assertEqual(
            self.writer.worksheet.activity_rows,
            [["2026-10-21", 5 / 60, "Réunion Daily"]],
        )
        self.assertAlmostEqual(self.writer.worksheet.wbs_hours, 5 / 60)


if __name__ == "__main__":
    unittest.main()
