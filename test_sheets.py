import unittest

from sheets import GoogleSheetWriter


class FakeWorksheet:
    def __init__(self):
        self.activity_rows = []
        self.wbs_hours = 0.0
        self.participation = ""

    def get(self, range_name, value_render_option=None):
        if range_name == "F4:H":
            return self.activity_rows
        if range_name in ("B4:D", "J4:L"):
            return []
        raise AssertionError(f"Unexpected range: {range_name}")

    def update(self, range_name, values, value_input_option=None):
        if range_name == "F4:H4":
            self.activity_rows.append(values[0])
        elif range_name == "K2":
            self.wbs_hours = values[0][0]
        elif range_name == "M2":
            self.participation = values[0][0]
        elif range_name == "M2:M2":
            self.participation = values[0][0]
        else:
            raise AssertionError(f"Unexpected range: {range_name}")

    def cell(self, row, column):
        return type("Cell", (), {"value": "Réunion Daily"})()

    def acell(self, address):
        value = self.participation if address == "M2" else self.wbs_hours
        return type("Cell", (), {"value": value})()

    def row_values(self, row):
        return ["ID", "Lot", "Description", "Tâche", "", "", "", "", "", "", "", "", "Participation"]

    def col_values(self, column):
        if column == 4:
            return ["Tâche", "Réunion Daily"]
        raise AssertionError(f"Unexpected column: {column}")


class RecurringActivitySheetTests(unittest.TestCase):
    def setUp(self):
        self.writer = object.__new__(GoogleSheetWriter)
        self.writer.start_row = 4
        self.writer.wbs_start_row = 2
        self.writer.wbs_worksheet_name = "WBS"
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
        self.assertEqual(self.writer.worksheet.participation, "Lucas")

    def test_rebuild_participation_from_existing_hours(self):
        self.writer.worksheet.activity_rows = [
            ["2026-10-21", 1.0, "Réunion Daily"],
            ["2026-10-22", 0.5, "Réunion Daily"],
        ]

        updated = self.writer.rebuild_wbs_participation_from_hours()

        self.assertEqual(updated, 1)
        self.assertEqual(self.writer.worksheet.participation, "Lucas")


if __name__ == "__main__":
    unittest.main()
