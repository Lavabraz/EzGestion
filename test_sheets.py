import unittest

from sheets import GoogleSheetWriter


class FakeWorksheet:
    def __init__(self):
        self.activity_rows = []
        self.thomas_activity_rows = []
        self.wbs_hours = 0.0
        self.wbs_hours_by_row = {}
        self.wbs_tasks = {2: "Réunion Daily"}
        self.participants = {}

    def get(self, range_name, value_render_option=None):
        if range_name == "F4:H":
            return self.activity_rows
        if range_name == "B4:D":
            return self.thomas_activity_rows
        if range_name == "J4:L":
            return []
        raise AssertionError(f"Unexpected range: {range_name}")

    def update(self, range_name, values, value_input_option=None):
        if range_name == "F4:H4":
            self.activity_rows.append(values[0])
        elif range_name.startswith("K"):
            self.wbs_hours = values[0][0]
            self.wbs_hours_by_row[range_name] = values[0][0]
        else:
            raise AssertionError(f"Unexpected range: {range_name}")

    def batch_update(self, data, value_input_option=None):
        for update in data:
            row = int(update["range"][1:])
            self.participants[row] = update["values"][0][0]

    def cell(self, row, column):
        return type("Cell", (), {"value": self.wbs_tasks.get(row, "")})()

    def acell(self, address):
        value = self.wbs_hours_by_row.get(address, self.wbs_hours)
        return type("Cell", (), {"value": value})()


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

    def test_write_activity_rebuilds_participants_from_all_member_history(self):
        self.writer.wbs_tasks = [
            (2, "Réunion Daily"),
            (3, "Développement"),
        ]
        self.writer.members = {
            "thomas": {
                "display": "Thomas",
                "date": "B",
                "duration": "C",
                "task": "D",
            },
            "lucas": {
                "display": "Lucas",
                "date": "F",
                "duration": "G",
                "task": "H",
            },
        }
        self.writer.worksheet.thomas_activity_rows = [
            ["2026-10-20", 1, "Développement"],
        ]
        self.writer.worksheet.wbs_tasks[3] = "Développement"
        self.writer._next_row = lambda date_col, duration_col, task_col: 4

        task_name = self.writer.write_activity(
            {
                "date": "2026-10-21",
                "duration_hours": 1.5,
                "member": "Lucas",
            },
            3,
        )

        self.assertEqual(task_name, "Développement")
        self.assertEqual(self.writer.worksheet.participants[2], "")
        self.assertEqual(self.writer.worksheet.participants[3], "Lucas, Thomas")


if __name__ == "__main__":
    unittest.main()
