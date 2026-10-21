import unittest
from datetime import date
import struct

from gantt import (
    WorkCalendar,
    aggregate_actuals,
    french_public_holidays,
    infer_period_bounds,
    parse_sheet_date,
    parse_wbs_tasks,
    plan_tasks,
    render_gantt,
    render_timeline,
)


class GanttTests(unittest.TestCase):
    def setUp(self):
        self.calendar = WorkCalendar(
            french_public_holidays(2026),
            [(date(2026, 10, 26), date(2026, 11, 1))],
        )

    def test_parses_wbs_columns_and_multiple_dependencies(self):
        rows = [
            [1, "", "Lot A", "Analyse", 5, 1.5, 6.5, "Lucas", "", "", 0],
            [2, "1", "Lot A", "Développement", 10, 3, 13, "Lucas, Nathan", "", "", 2],
            [3, "1, 2", "Lot B", "Validation", 4, 1, 5, "Nathan", 46300, 46310, 0],
        ]
        tasks = parse_wbs_tasks(rows)
        self.assertEqual(tasks[2].dependencies, ("1", "2"))
        self.assertEqual(tasks[1].assignees, ("Lucas", "Nathan"))
        self.assertEqual(tasks[2].start_deadline, parse_sheet_date(46300))

    def test_french_holiday_and_vacation_are_non_workdays(self):
        self.assertFalse(self.calendar.is_workday(date(2026, 11, 11)))
        self.assertFalse(self.calendar.is_workday(date(2026, 10, 27)))
        self.assertFalse(self.calendar.is_workday(date(2026, 10, 31)))
        self.assertTrue(self.calendar.is_workday(date(2026, 11, 2)))

    def test_plans_dependencies_and_finds_longest_critical_path(self):
        tasks = parse_wbs_tasks([
            [1, "", "", "Analyse", 14, 0, 14, "Lucas", "", "", 0],
            [2, "1", "", "Développement", 14, 0, 14, "Lucas, Nathan", "", "", 0],
            [3, "", "", "Documentation", 7, 0, 7, "Nathan", "", "", 0],
        ])
        planned = plan_tasks(
            tasks, date(2026, 10, 19), date(2026, 11, 6), self.calendar
        )
        by_id = {item.task.task_id: item for item in planned}
        self.assertTrue(by_id["1"].critical)
        self.assertTrue(by_id["2"].critical)
        self.assertFalse(by_id["3"].critical)
        self.assertLess(by_id["1"].finish, by_id["2"].start)

    def test_rejects_dependency_cycles(self):
        cyclic = parse_wbs_tasks([
            [1, "2", "", "A", 1, 0, 1, "Lucas", "", "", 0],
            [2, "1", "", "B", 1, 0, 1, "Lucas", "", "", 0],
        ])
        with self.assertRaisesRegex(ValueError, "Cycle"):
            plan_tasks(cyclic, date(2026, 10, 19), date(2026, 11, 6), self.calendar)

    def test_schedule_respects_start_and_finish_deadlines(self):
        task = parse_wbs_tasks([
            [1, "", "", "Conception", 14, 0, 14, "Lucas", "2026-10-27", "2026-11-06", 0],
        ])
        planned = plan_tasks(
            task, date(2026, 10, 19), date(2026, 11, 6), self.calendar
        )[0]
        self.assertLessEqual(planned.start, date(2026, 10, 27))
        self.assertLessEqual(planned.finish, date(2026, 11, 6))
        self.assertTrue(self.calendar.is_workday(planned.start))
        self.assertTrue(self.calendar.is_workday(planned.finish))

    def test_actual_hours_are_grouped_by_date_and_exact_task(self):
        tasks = parse_wbs_tasks([
            [1, "", "", "Analyse", 7, 0, 7, "Lucas", "", "", 0],
            [2, "", "", "Analyse", 7, 0, 7, "Nathan", "", "", 0],
        ])
        totals, warnings = aggregate_actuals(
            tasks,
            [("2026-10-20", 1.5, "Analyse"), ("2026-10-21", 2, "[1] Analyse")],
            date(2026, 10, 1),
            date(2026, 10, 31),
        )
        self.assertEqual(totals["1"][date(2026, 10, 21)], 2)
        self.assertEqual(warnings, ["Analyse (1.5 h non attribuées)"])

    def test_period_bounds_are_inferred_from_wbs_or_actual_records(self):
        tasks = parse_wbs_tasks([
            [1, "", "", "A", 7, 0, 7, "Lucas", "2026-10-20", "2026-10-25", 0],
            [2, "", "", "B", 7, 0, 7, "Nathan", "2026-10-22", "2026-10-30", 0],
        ])
        wbs_period = infer_period_bounds("prevu", tasks, [], None, date(2026, 11, 2))
        self.assertEqual(wbs_period, (date(2026, 10, 20), date(2026, 11, 2)))
        wbs_end_only = infer_period_bounds("prevu", tasks, [], date(2026, 10, 21), None)
        self.assertEqual(wbs_end_only, (date(2026, 10, 21), date(2026, 10, 30)))

        actuals = [
            ("2026-10-19", 1, "A"),
            ("2026-10-28", 2, "B"),
        ]
        actual_period = infer_period_bounds("reel", tasks, actuals, None, None)
        self.assertEqual(actual_period, (date(2026, 10, 19), date(2026, 10, 28)))
        actual_start_only = infer_period_bounds(
            "reel", tasks, actuals, date(2026, 10, 20), None
        )
        self.assertEqual(actual_start_only, (date(2026, 10, 20), date(2026, 10, 28)))

    def test_period_inference_reports_missing_dates(self):
        tasks = parse_wbs_tasks([
            [1, "", "", "A", 7, 0, 7, "Lucas", "", "", 0],
        ])
        with self.assertRaisesRegex(ValueError, "renseignez le paramètre « debut »"):
            infer_period_bounds("prevu", tasks, [], None, date(2026, 10, 31))
        with self.assertRaisesRegex(ValueError, "renseignez le paramètre « fin »"):
            infer_period_bounds("reel", tasks, [], date(2026, 10, 1), None)

    def test_actual_mode_keeps_working_if_planning_data_is_invalid(self):
        tasks = parse_wbs_tasks(
            [[1, "missing-id", "", "Analyse", "invalid", 0, "invalid", "", "", "", "n/a"]],
            tolerate_planning_errors=True,
        )
        with self.assertRaisesRegex(ValueError, "Données de planning invalides"):
            plan_tasks(
                tasks,
                date(2026, 10, 19),
                date(2026, 10, 30),
                self.calendar,
            )
        image, description = render_gantt(
            tasks,
            [("2026-10-20", 2, "Analyse")],
            date(2026, 10, 19),
            date(2026, 10, 30),
            "reel",
            self.calendar,
        )
        self.assertTrue(image.getvalue().startswith(b"\x89PNG"))
        self.assertIn("indisponible", description)

    def test_renders_png_for_both_modes(self):
        tasks = parse_wbs_tasks([
            [1, "", "", "Analyse", 7, 0, 7, "Lucas", "", "", 0],
        ])
        for mode in ("prevu", "reel"):
            image, description = render_gantt(
                tasks,
                [("2026-10-20", 2, "Analyse")],
                date(2026, 10, 19),
                date(2026, 10, 30),
                mode,
                self.calendar,
            )
            self.assertTrue(image.getvalue().startswith(b"\x89PNG"))
            self.assertGreater(len(image.getvalue()), 1000)
            self.assertTrue(description)
            self.assertIn("Durée estimée indiquée à côté de chaque tâche", description)
            image.close()

    def test_renders_chronological_timeline(self):
        tasks = parse_wbs_tasks([
            [1, "", "", "Analyse", 7, 0, 7, "Lucas", "", "", 0],
            [2, "1", "", "Développement", 14, 0, 14, "Lucas", "", "", 0],
        ])
        image, description = render_timeline(
            tasks,
            date(2026, 10, 19),
            date(2026, 11, 6),
            self.calendar,
        )
        self.assertTrue(image.getvalue().startswith(b"\x89PNG"))
        self.assertGreaterEqual(struct.unpack(">I", image.getvalue()[16:20])[0], 2600)
        self.assertIn("Frise chronologique", description)
        self.assertIn("positionnées sur la flèche", description)
        self.assertIn("durée indiquée sur chaque tâche", description)
        image.close()


if __name__ == "__main__":
    unittest.main()
