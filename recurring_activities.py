"""Recurring meeting activity rules."""
from datetime import date

from gantt import WorkCalendar


CLIENT_MEETING_ANCHOR = date(2026, 10, 19)
GP_MEETING_ANCHOR = date(2026, 11, 2)


def activities_for_date(
    day: date,
    calendar: WorkCalendar,
) -> list[tuple[str, int]]:
    if day.weekday() == 0:
        activities = [
            ("Réunion Hebdo", 35),
            ("Réunion Client", 45),
        ]
        if day >= GP_MEETING_ANCHOR and (
            day - GP_MEETING_ANCHOR
        ).days % 14 == 0:
            activities.append(("Réunion Gp", 30))
        return activities

    if calendar.is_workday(day):
        return [("Réunion Daily", 5)]
    return []
