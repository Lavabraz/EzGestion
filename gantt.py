"""Planning and rendering utilities for WBS Gantt charts."""
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
import math
import os
import re
from functools import lru_cache
from textwrap import shorten
from typing import Iterable

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import Normalize
from matplotlib.dates import DateFormatter
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, Patch, Rectangle


@dataclass(frozen=True)
class WbsTask:
    task_id: str
    name: str
    lot: str
    effort_hours: float
    assignees: tuple[str, ...]
    dependencies: tuple[str, ...]
    start_deadline: date | None
    finish_deadline: date | None
    actual_total_hours: float
    planning_errors: tuple[str, ...] = ()


def _cell(row: list, index: int):
    if index >= len(row) or row[index] is None:
        return ""
    return row[index]


def _id_text(value) -> str:
    if isinstance(value, (int, float)) and float(value).is_integer():
        return str(int(value))
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def _number(value, field: str, task_id: str, default: float | None = None) -> float:
    if value in (None, ""):
        if default is not None:
            return default
        raise ValueError(f"Tâche {task_id} : la valeur « {field} » est manquante.")
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Tâche {task_id} : valeur invalide pour « {field} » : {value!r}."
        ) from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Tâche {task_id} : « {field} » doit être un nombre positif ou nul.")
    return number


def parse_sheet_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return date(1899, 12, 30) + timedelta(days=int(value))

    text = str(value).strip()
    # Google Sheets peut renvoyer une date formatée avec l'heure, notamment
    # lorsqu'une cellule de jalon contient une date/heure plutôt qu'une date seule.
    for date_format in (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    raise ValueError(
        f"Date Google Sheets invalide : {value!r}. "
        "Vérifiez la cellule source : formats attendus JJ/MM/AAAA ou "
        "JJ/MM/AAAA HH:MM:SS (ex. 23/10/2026 12:00:00)."
    )


def parse_wbs_tasks(
    rows: Iterable[list],
    start_row: int = 2,
    tolerate_planning_errors: bool = False,
) -> list[WbsTask]:
    tasks = []
    for row_number, row in enumerate(rows, start=start_row):
        task_id = _id_text(_cell(row, 0))
        name = str(_cell(row, 3)).strip()
        if not task_id and not name:
            continue
        if not task_id or not name:
            raise ValueError(
                f"Ligne WBS {row_number} : chaque ligne de tâche doit contenir un ID "
                "en A et un intitulé en D."
            )

        raw_dependencies = str(_cell(row, 1)).strip()
        dependencies = tuple(
            _id_text(dependency)
            for dependency in raw_dependencies.split(",")
            if dependency.strip()
        )
        assignees = tuple(dict.fromkeys(
            person.strip()
            for person in str(_cell(row, 7)).split(",")
            if person.strip()
        ))
        planning_errors = []
        try:
            effort = _number(
                _cell(row, 6), "Estimation avec marge (h)", task_id, default=0.0
            )
        except ValueError as error:
            if not tolerate_planning_errors:
                raise
            effort = 0.0
            planning_errors.append(str(error))
        try:
            start_deadline = parse_sheet_date(_cell(row, 8))
            finish_deadline = parse_sheet_date(_cell(row, 9))
        except ValueError as error:
            if not tolerate_planning_errors:
                raise
            start_deadline = None
            finish_deadline = None
            planning_errors.append(str(error))
        try:
            actual_total = _number(
                _cell(row, 10), "Temps réel (h)", task_id, default=0.0
            )
        except ValueError:
            if not tolerate_planning_errors:
                raise
            actual_total = 0.0

        tasks.append(WbsTask(
            task_id=task_id,
            name=name,
            lot=str(_cell(row, 2)).strip(),
            effort_hours=effort,
            assignees=assignees,
            dependencies=dependencies,
            start_deadline=start_deadline,
            finish_deadline=finish_deadline,
            actual_total_hours=actual_total,
            planning_errors=tuple(planning_errors),
        ))

    ids = [task.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Les ID de tâches de la colonne A doivent être uniques.")
    known_ids = set(ids)
    if not tolerate_planning_errors:
        for task in tasks:
            unknown = set(task.dependencies) - known_ids
            if unknown:
                raise ValueError(
                    f"Tâche {task.task_id} : ID de dépendance inconnu(s) : "
                    f"{', '.join(sorted(unknown))}."
                )
    return tasks


def parse_gantt_data(
    wbs_rows: Iterable[list],
    activity_rows: list[tuple[str, str, str]],
    start_row: int = 2,
    tolerate_planning_errors: bool = False,
) -> tuple[list[WbsTask], list[tuple[str, str, str]]]:
    rows = []
    meeting_ids = set()
    for row in wbs_rows:
        task_name = str(_cell(row, 3)).strip()
        if task_name.casefold().startswith("réunion"):
            task_id = _id_text(_cell(row, 0))
            if task_id:
                meeting_ids.add(task_id)
            rows.append([])
        else:
            rows.append(list(row))

    for row in rows:
        if not row:
            continue
        raw_dependencies = str(_cell(row, 1)).strip()
        if raw_dependencies:
            dependencies = [
                _id_text(dependency)
                for dependency in raw_dependencies.split(",")
                if dependency.strip()
            ]
            row[1] = ", ".join(
                dependency
                for dependency in dependencies
                if dependency not in meeting_ids
            )
    tasks = parse_wbs_tasks(rows, start_row, tolerate_planning_errors)

    filtered_activities = []
    for activity in activity_rows:
        task_name = str(activity[2]).strip()
        task_id_match = re.match(r"^\[([^\]]+)\]\s*", task_name)
        if (
            task_name.casefold().startswith("réunion")
            or task_id_match and task_id_match.group(1) in meeting_ids
        ):
            continue
        filtered_activities.append(activity)
    return tasks, filtered_activities


@lru_cache(maxsize=None)
def french_public_holidays(year: int) -> set[date]:
    """Jours fériés nationaux de France métropolitaine."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    easter = date(year, month, day)
    return {
        date(year, 1, 1),
        easter + timedelta(days=1),
        date(year, 5, 1),
        date(year, 5, 8),
        easter + timedelta(days=39),
        easter + timedelta(days=50),
        date(year, 7, 14),
        date(year, 8, 15),
        date(year, 11, 1),
        date(year, 11, 11),
        date(year, 12, 25),
    }


def configured_vacations() -> list[tuple[date, date]]:
    configured = os.getenv("GANTT_VACATION_RANGES", "2026-10-26:2026-11-01")
    periods = []
    for item in configured.split(","):
        if not item.strip():
            continue
        try:
            start_text, end_text = item.split(":", 1)
            start, end = date.fromisoformat(start_text.strip()), date.fromisoformat(end_text.strip())
        except ValueError as error:
            raise ValueError(
                "GANTT_VACATION_RANGES doit contenir des périodes ISO "
                "« AAAA-MM-JJ:AAAA-MM-JJ » séparées par des virgules."
            ) from error
        if end < start:
            raise ValueError(f"Période de vacances invalide : {item!r}.")
        periods.append((start, end))
    return periods


class WorkCalendar:
    def __init__(self, holidays: set[date], vacations: list[tuple[date, date]]):
        self.holidays = holidays
        self.vacations = vacations

    def is_workday(self, day: date) -> bool:
        return (
            day.weekday() < 5
            and day not in self.holidays
            and day not in french_public_holidays(day.year)
            and not any(start <= day <= end for start, end in self.vacations)
        )

    def next_workday(self, day: date) -> date:
        while not self.is_workday(day):
            day += timedelta(days=1)
        return day

    def previous_workday(self, day: date) -> date:
        while not self.is_workday(day):
            day -= timedelta(days=1)
        return day

    def add_workdays(self, day: date, count: int) -> date:
        day = self.next_workday(day)
        for _ in range(max(0, count - 1)):
            day = self.next_workday(day + timedelta(days=1))
        return day

    def subtract_workdays(self, day: date, count: int) -> date:
        day = self.previous_workday(day)
        for _ in range(max(0, count - 1)):
            day = self.previous_workday(day - timedelta(days=1))
        return day

    def workday_distance(self, start: date, end: date) -> int:
        if end < start:
            return -self.workday_distance(end, start)
        count = 0
        current = start
        while current < end:
            if self.is_workday(current):
                count += 1
            current += timedelta(days=1)
        return count


@dataclass(frozen=True)
class PlannedTask:
    task: WbsTask
    start: date
    finish: date
    critical: bool


def _topological_order(tasks: list[WbsTask]) -> list[WbsTask]:
    by_id = {task.task_id: task for task in tasks}
    successors: dict[str, list[str]] = defaultdict(list)
    indegree = {task.task_id: 0 for task in tasks}
    for task in tasks:
        for dependency in task.dependencies:
            if dependency not in by_id:
                raise ValueError(
                    f"Tâche {task.task_id} : dépendance {dependency} absente du WBS."
                )
            successors[dependency].append(task.task_id)
            indegree[task.task_id] += 1
    ready = deque(task.task_id for task in tasks if indegree[task.task_id] == 0)
    order = []
    while ready:
        task_id = ready.popleft()
        order.append(by_id[task_id])
        for successor in successors[task_id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
    if len(order) != len(tasks):
        cyclic = sorted(task_id for task_id, degree in indegree.items() if degree)
        raise ValueError(f"Cycle détecté dans les dépendances WBS : {', '.join(cyclic)}.")
    return order


def plan_tasks(
    tasks: list[WbsTask],
    period_start: date,
    period_end: date,
    calendar: WorkCalendar,
    hours_per_person_per_day: float = 7.0,
) -> list[PlannedTask]:
    if period_end < period_start:
        raise ValueError("La date de fin doit être égale ou postérieure à la date de début.")
    if hours_per_person_per_day <= 0:
        raise ValueError("La capacité journalière doit être supérieure à zéro.")
    invalid_rows = [
        error
        for task in tasks
        for error in task.planning_errors
    ]
    if invalid_rows:
        raise ValueError("Données de planning invalides : " + " ".join(invalid_rows))

    order = _topological_order(tasks)
    duration_days: dict[str, int] = {}
    for task in order:
        if task.effort_hours > 0 and not task.assignees:
            raise ValueError(
                f"Tâche {task.task_id} ({task.name}) : indiquez au moins une personne "
                "dans « Répartition » pour calculer le planning."
            )
        capacity = hours_per_person_per_day * max(1, len(task.assignees))
        duration_days[task.task_id] = (
            max(1, math.ceil(task.effort_hours / capacity))
            if task.effort_hours
            else 0
        )

    successors: dict[str, list[str]] = defaultdict(list)
    for task in order:
        for dependency in task.dependencies:
            successors[dependency].append(task.task_id)

    latest_start: dict[str, date] = {}
    scheduled: dict[str, tuple[date, date]] = {}
    for task in reversed(order):
        successor_starts = [
            latest_start[successor]
            for successor in successors[task.task_id]
        ]
        finish_limit = min(task.finish_deadline or period_end, period_end)
        if successor_starts:
            dependency_finish_limit = calendar.previous_workday(
                min(successor_starts) - timedelta(days=1)
            )
            finish_limit = min(finish_limit, dependency_finish_limit)
        finish = calendar.previous_workday(finish_limit)
        days = duration_days[task.task_id]
        start = calendar.subtract_workdays(finish, days) if days else finish
        if task.start_deadline:
            start_limit = calendar.previous_workday(task.start_deadline)
            if start > start_limit:
                start = start_limit
                finish = calendar.add_workdays(start, days) if days else start
        if finish > finish_limit:
            raise ValueError(
                f"Planning impossible pour la tâche {task.task_id} ({task.name}) : "
                "ses dates limites ne permettent pas de respecter la durée et les dépendances."
            )
        latest_start[task.task_id] = start
        scheduled[task.task_id] = (start, finish)

    longest_to: dict[str, int] = {}
    longest_from: dict[str, int] = {}
    for task in order:
        longest_to[task.task_id] = duration_days[task.task_id] + max(
            (longest_to[dependency] for dependency in task.dependencies),
            default=0,
        )
    for task in reversed(order):
        longest_from[task.task_id] = duration_days[task.task_id] + max(
            (longest_from[successor] for successor in successors[task.task_id]),
            default=0,
        )
    project_duration = max(longest_to.values(), default=0)
    critical_ids = {
        task.task_id
        for task in order
        if project_duration
        and longest_to[task.task_id] + longest_from[task.task_id]
        - duration_days[task.task_id] == project_duration
    }
    return [
        PlannedTask(
            task,
            scheduled[task.task_id][0],
            scheduled[task.task_id][1],
            task.task_id in critical_ids,
        )
        for task in order
    ]


def aggregate_actuals(
    tasks: list[WbsTask],
    activity_rows: list[tuple],
    period_start: date,
    period_end: date,
    person: str | None = None,
) -> tuple[dict[str, dict[date, float]], list[str]]:
    name_to_tasks: dict[str, list[WbsTask]] = defaultdict(list)
    id_to_task = {task.task_id: task for task in tasks}
    for task in tasks:
        name_to_tasks[task.name.casefold()].append(task)

    totals: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    unmatched: dict[str, float] = defaultdict(float)
    for activity in activity_rows:
        raw_day, raw_hours, raw_name = activity[:3]
        activity_person = str(activity[3]).strip() if len(activity) > 3 else ""
        if person and activity_person.casefold() != person.casefold():
            continue
        task_name = str(raw_name).strip()
        if not task_name:
            continue
        try:
            activity_day = parse_sheet_date(raw_day)
            hours = _number(raw_hours, "heures d'activité", task_name)
        except ValueError:
            unmatched[task_name] += 0.0
            continue
        if activity_day is None or not period_start <= activity_day <= period_end:
            continue
        id_match = re.match(r"^\[([^\]]+)\]\s*", task_name)
        if id_match and id_match.group(1) in id_to_task:
            matched_tasks = [id_to_task[id_match.group(1)]]
            task_name = task_name[id_match.end():].strip()
        else:
            matched_tasks = name_to_tasks.get(task_name.casefold(), [])
        if len(matched_tasks) != 1:
            unmatched[task_name] += hours
            continue
        totals[matched_tasks[0].task_id][activity_day] += hours
    warnings = [
        f"{task_name} ({hours:g} h non attribuées)"
        for task_name, hours in sorted(unmatched.items())
    ]
    return totals, warnings


def infer_period_bounds(
    mode: str,
    tasks: list[WbsTask],
    activity_rows: list[tuple[str, str, str]],
    start: date | None,
    end: date | None,
) -> tuple[date, date]:
    if mode == "prevu":
        inferred_starts = [
            task.start_deadline for task in tasks if task.start_deadline is not None
        ]
        inferred_ends = [
            task.finish_deadline for task in tasks if task.finish_deadline is not None
        ]
        earliest = min(inferred_starts, default=None)
        latest = max(inferred_ends, default=None)
        source = "les dates « Commencer avant » et « Finir avant » du WBS"
    else:
        activity_dates = []
        for activity in activity_rows:
            row_day, row_hours = activity[:2]
            if row_day in (None, "") or row_hours in (None, ""):
                continue
            activity_day = parse_sheet_date(row_day)
            if activity_day is not None:
                activity_dates.append(activity_day)
        earliest = min(activity_dates, default=None)
        latest = max(activity_dates, default=None)
        source = "les enregistrements d'heures"

    if start is None:
        if earliest is None:
            raise ValueError(
                f"Impossible de déduire la date de début depuis {source}; "
                "renseignez le paramètre « debut »."
            )
        start = earliest
    if end is None:
        if latest is None:
            raise ValueError(
                f"Impossible de déduire la date de fin depuis {source}; "
                "renseignez le paramètre « fin »."
            )
        end = latest
    if end < start:
        raise ValueError("La date de fin doit être égale ou postérieure à la date de début.")
    return start, end


def _render_gantt(
    tasks: list[WbsTask],
    activity_rows: list[tuple[str, str, str]],
    period_start: date,
    period_end: date,
    mode: str,
    calendar: WorkCalendar,
    hours_per_person_per_day: float = 7.0,
    *,
    chart_title: str,
) -> tuple[BytesIO, str]:
    if not tasks:
        raise ValueError("Aucune tâche avec ID et intitulé n'a été trouvée dans le WBS.")
    if mode not in {"prevu", "reel"}:
        raise ValueError("Le mode doit être « prevu » ou « reel ».")

    planned = []
    path_ids: set[str] = set()
    plan_warning = ""
    try:
        planned = plan_tasks(
            tasks, period_start, period_end, calendar, hours_per_person_per_day
        )
        path_ids = {item.task.task_id for item in planned if item.critical}
    except ValueError as error:
        if mode == "prevu":
            raise
        plan_warning = f"Chemin critique prévisionnel indisponible : {error}"

    actuals: dict[str, dict[date, float]] = {}
    unmatched: list[str] = []
    if mode == "reel":
        actuals, unmatched = aggregate_actuals(
            tasks, activity_rows, period_start, period_end
        )
        display_tasks = [
            task for task in tasks
            if actuals.get(task.task_id) or task.task_id in path_ids
        ]
    else:
        display_tasks = [
            item.task for item in planned
            if item.start <= period_end and item.finish >= period_start
        ]

    planned_by_id = {item.task.task_id: item for item in planned}
    if planned_by_id:
        display_tasks.sort(
            key=lambda task: (
                planned_by_id[task.task_id].start,
                planned_by_id[task.task_id].finish,
                task.task_id,
            )
        )

    figure_height = max(4.2, 0.42 * len(display_tasks) + 2.2)
    figure = Figure(figsize=(18, figure_height))
    FigureCanvasAgg(figure)
    grid = figure.add_gridspec(
        1,
        2,
        width_ratios=(1, 4),
        left=0.025,
        right=0.99,
        top=0.88,
        bottom=0.16,
        wspace=0.025,
    )
    label_axis = figure.add_subplot(grid[0, 0])
    axis = figure.add_subplot(grid[0, 1], sharey=label_axis)
    y_positions = list(range(len(display_tasks)))
    labels = [
        f"{shorten(f'{task.task_id} — {task.name}', width=45, placeholder='...')} "
        f"· {task.effort_hours:g} h"
        + (" (CP prévu)" if mode == "reel" and task.task_id in path_ids else "")
        for task in display_tasks
    ]
    axis.set_ylim(len(display_tasks) - 0.5, -0.5)
    label_axis.set_ylim(axis.get_ylim())
    label_axis.set_xlim(0, 1)
    label_axis.axis("off")
    for y_position, label in zip(y_positions, labels):
        label_axis.text(
            1,
            y_position,
            shorten(label, width=54, placeholder="..."),
            ha="right",
            va="center",
            fontsize=8,
            clip_on=True,
        )
    axis.set_yticks(y_positions)
    axis.tick_params(axis="y", left=False, labelleft=False)
    axis.set_xlim(
        datetime.combine(period_start, datetime.min.time()),
        datetime.combine(period_end + timedelta(days=1), datetime.min.time()),
    )
    axis.xaxis_date()
    date_format = "%d/%m/%Y" if period_start.year != period_end.year else "%d/%m"
    axis.xaxis.set_major_formatter(DateFormatter(date_format))
    axis.grid(axis="x", linestyle=":", alpha=0.45)
    axis.set_xlabel("Date")
    day = period_start
    while day <= period_end:
        if not calendar.is_workday(day):
            axis.axvspan(
                datetime.combine(day, datetime.min.time()),
                datetime.combine(day + timedelta(days=1), datetime.min.time()),
                color="#eeeeee",
                zorder=0,
            )
        day += timedelta(days=1)
    figure.suptitle(
        f"{chart_title} { 'prévisionnel' if mode == 'prevu' else 'réel' } "
        f"— {period_start:%d/%m/%Y} au {period_end:%d/%m/%Y}"
    )

    y_by_id = {task.task_id: y for task, y in zip(display_tasks, y_positions)}
    if not display_tasks:
        axis.text(
            0.5,
            0.5,
            "Aucune tâche planifiée dans cette période"
            if mode == "prevu"
            else "Aucune activité réelle enregistrée dans cette période",
            ha="center",
            va="center",
            transform=axis.transAxes,
        )

    if mode == "prevu":
        planned_by_id = {
            task_id: item for task_id, item in planned_by_id.items()
            if task_id in y_by_id
        }
        plot_width_inches = axis.get_position().width * figure.get_figwidth()
        period_days = (period_end - period_start).days + 1
        for task in display_tasks:
            item = planned_by_id[task.task_id]
            if task.effort_hours == 0:
                axis.plot(item.start, y_by_id[task.task_id], marker="D", color="#6c757d")
                continue
            width = (item.finish - item.start).days + 1
            color = "#d1495b" if item.critical else "#377eb8"
            axis.barh(
                y_by_id[task.task_id],
                width,
                left=item.start,
                height=0.58,
                color=color,
            )
            effort_label = f"{task.effort_hours:g} h"
            if width * plot_width_inches / period_days >= len(effort_label) * 0.065:
                axis.text(
                    item.start + timedelta(days=width / 2),
                    y_by_id[task.task_id],
                    effort_label,
                    ha="center",
                    va="center",
                    color="white",
                    fontsize=8,
                )
        for task in display_tasks:
            for dependency in task.dependencies:
                if dependency not in planned_by_id:
                    continue
                source = planned_by_id[dependency]
                target = planned_by_id[task.task_id]
                source_x = source.finish + timedelta(days=1)
                target_x = target.start
                route_x = source_x + (target_x - source_x) / 2
                source_y = y_by_id[dependency]
                target_y = y_by_id[task.task_id]
                axis.plot(
                    [source_x, route_x, route_x],
                    [source_y, source_y, target_y],
                    color="#667085",
                    lw=0.9,
                    alpha=0.65,
                    zorder=1,
                )
                axis.annotate(
                    "",
                    xy=(target_x, target_y),
                    xytext=(route_x, target_y),
                    arrowprops={
                        "arrowstyle": "-|>",
                        "color": "#667085",
                        "lw": 0.9,
                        "alpha": 0.75,
                        "shrinkA": 0,
                        "shrinkB": 0,
                        "zorder": 1,
                    },
                )
        axis.legend(handles=[
            Patch(color="#d1495b", label="Chemin critique"),
            Patch(color="#377eb8", label="Autres tâches"),
        ], loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2, frameon=False)
        visible_path_ids = path_ids & y_by_id.keys()
        subtitle = (
            "Chemin critique dans la période : "
            + (", ".join(sorted(visible_path_ids)) if visible_path_ids else "aucun")
            + ". Plan calculé au plus tard; capacité théorique de "
            f"{hours_per_person_per_day:g} h/personne/jour. "
            "Les conflits d'affectation entre tâches ne sont pas nivelés."
        )
    else:
        maximum = max(
            (hours for per_day in actuals.values() for hours in per_day.values()),
            default=1.0,
        )
        color_map = matplotlib.colormaps["Blues"]
        plot_width_inches = axis.get_position().width * figure.get_figwidth()
        period_days = (period_end - period_start).days + 1
        for task in display_tasks:
            days = actuals.get(task.task_id, {})
            if not days:
                continue
            for actual_day, hours in days.items():
                color = color_map(0.3 + 0.7 * hours / maximum)
                axis.add_patch(Rectangle(
                    (datetime.combine(actual_day, datetime.min.time()), y_by_id[task.task_id] - 0.29),
                    timedelta(days=0.82),
                    0.58,
                    facecolor=color,
                    edgecolor="#24527a",
                    linewidth=0.5,
                ))
                hours_label = f"{hours:g} h"
                if 0.82 * plot_width_inches / period_days >= len(hours_label) * 0.055:
                    axis.text(
                        datetime.combine(actual_day, datetime.min.time())
                        + timedelta(hours=12),
                        y_by_id[task.task_id],
                        hours_label,
                        ha="center",
                        va="center",
                        fontsize=7,
                        color="#111111",
                    )
        critical_labels = []
        if path_ids:
            critical_labels = [
                f"{task.task_id} — {task.name}"
                for task in display_tasks
                if task.task_id in path_ids
            ]
        subtitle = (
            "Cellules = heures réellement saisies par jour. Le chemin critique prévu "
            "est un repère indépendant des heures réelles : "
            + (", ".join(critical_labels) if critical_labels else "indisponible")
        )
        if unmatched:
            subtitle += ". Activités non attribuées : " + "; ".join(unmatched[:5])
            if len(unmatched) > 5:
                subtitle += f" (+{len(unmatched) - 5})"
        if plan_warning:
            subtitle += f". {plan_warning}"
        figure.colorbar(
            matplotlib.cm.ScalarMappable(
                norm=Normalize(vmin=0, vmax=maximum), cmap=color_map
            ),
            ax=axis,
            label="Heures enregistrées dans une journée",
        )

    subtitle = (
        "Durée estimée indiquée à côté de chaque tâche. "
        f"Période {period_start:%d/%m/%Y} au {period_end:%d/%m/%Y}. {subtitle}"
    )
    figure.text(0.025, 0.025, subtitle, fontsize=8, wrap=True)
    output = BytesIO()
    figure.savefig(output, format="png", dpi=150)
    output.seek(0)
    return output, subtitle


def render_gantt(
    tasks: list[WbsTask],
    activity_rows: list[tuple[str, str, str]],
    period_start: date,
    period_end: date,
    mode: str,
    calendar: WorkCalendar,
    hours_per_person_per_day: float = 7.0,
) -> tuple[BytesIO, str]:
    return _render_gantt(
        tasks,
        activity_rows,
        period_start,
        period_end,
        mode,
        calendar,
        hours_per_person_per_day,
        chart_title="Gantt",
    )


def render_timeline(
    tasks: list[WbsTask],
    period_start: date,
    period_end: date,
    calendar: WorkCalendar,
    hours_per_person_per_day: float = 7.0,
    mode: str = "prevu",
    activity_rows: list[tuple] | None = None,
    person: str | None = None,
    milestones: list[tuple[str, date]] | None = None,
    selected_task_ids: set[str] | None = None,
) -> tuple[BytesIO, str]:
    if not tasks:
        raise ValueError("Aucune tâche avec ID et intitulé n'a été trouvée dans le WBS.")
    if mode not in {"prevu", "reel"}:
        raise ValueError("Le mode doit être « prevu » ou « reel ».")

    timeline_items = []
    unmatched: list[str] = []
    if mode == "prevu":
        planned = plan_tasks(
            tasks, period_start, period_end, calendar, hours_per_person_per_day
        )
        timeline_items = [
            (
                item.task,
                item.start,
                item.finish,
                item.task.effort_hours,
                calendar.workday_distance(item.start, item.finish)
                + int(calendar.is_workday(item.finish))
                if item.task.effort_hours > 0
                else 0,
                item.critical,
                {},
            )
            for item in planned
            if item.start <= period_end and item.finish >= period_start
        ]
        if selected_task_ids is not None:
            timeline_items = [item for item in timeline_items if item[0].task_id in selected_task_ids]
        if person:
            timeline_items = [
                item for item in timeline_items
                if any(name.casefold() == person.casefold() for name in item[0].assignees)
            ]
        timeline_items.sort(key=lambda item: (item[1], item[2], item[0].task_id))
        if not timeline_items:
            raise ValueError("Aucune tâche planifiée dans cette période.")
    else:
        actuals, unmatched = aggregate_actuals(
            tasks, activity_rows or [], period_start, period_end, person
        )
        task_by_id = {task.task_id: task for task in tasks}
        for task_id, days in actuals.items():
            if selected_task_ids is not None and task_id not in selected_task_ids:
                continue
            actual_days = sorted(days)
            if not actual_days:
                continue
            task = task_by_id[task_id]
            timeline_items.append((
                task,
                actual_days[0],
                actual_days[-1],
                sum(days.values()),
                len(actual_days),
                False,
                days,
            ))
        timeline_items.sort(key=lambda item: (item[1], item[2], item[0].task_id))

    labels = []
    for task, start, finish, total_hours, duration_days, _, _ in timeline_items:
        task_name = shorten(
            f"{task.task_id} — {task.name}",
            width=34,
            placeholder="...",
        )
        if mode == "prevu":
            labels.append(
                f"{task_name} · {total_hours:g} h · {duration_days} j ouvrés"
            )
        else:
            date_range = (
                f"{start:%d/%m}" if start == finish
                else f"{start:%d/%m}–{finish:%d/%m}"
            )
            labels.append(
                f"{task_name} · {total_hours:g} h réelles · {date_range}"
            )
    period_days = (period_end - period_start).days + 1
    figure_width = 18
    axes_width_inches = figure_width * 0.94
    label_widths = [
        min(0.32 * period_days, max(2.0, len(label) * 0.055 * period_days / axes_width_inches))
        for label in labels
    ]
    lane_right_edges: list[list[float]] = [[], []]
    lane_assignments: list[tuple[int, int]] = []
    marker_positions = [
        (item[1] - period_start).days + 0.5
        for item in timeline_items
    ]
    for index, (position, label_width) in enumerate(zip(marker_positions, label_widths)):
        side = index % 2
        half_width = label_width / 2
        lane = 0
        while lane < len(lane_right_edges[side]) and (
            position - half_width < lane_right_edges[side][lane] + 0.5
        ):
            lane += 1
        if lane == len(lane_right_edges[side]):
            lane_right_edges[side].append(float("-inf"))
        lane_right_edges[side][lane] = position + half_width
        lane_assignments.append((side, lane))

    lane_count = max((lane + 1 for _, lane in lane_assignments), default=1)
    figure_height = max(5.5, 2.8 + lane_count * 0.9)
    figure = Figure(figsize=(figure_width, figure_height))
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(1, 1, 1)
    axis.set_xlim(
        datetime.combine(period_start - timedelta(days=1), datetime.min.time()),
        datetime.combine(period_end + timedelta(days=2), datetime.min.time()),
    )
    axis.set_ylim(
        -lane_count - 0.8,
        lane_count + (2.0 if milestones else 0.8),
    )
    axis.set_yticks([])
    axis.xaxis_date()
    date_format = "%d/%m/%Y" if period_start.year != period_end.year else "%d/%m"
    axis.xaxis.set_major_formatter(DateFormatter(date_format))
    axis.grid(axis="x", linestyle=":", alpha=0.35)
    axis.set_xlabel("Date")
    title_scope = f" — {person}" if person else ""
    axis.set_title(f"Frise chronologique{title_scope} — {period_start:%d/%m/%Y} au {period_end:%d/%m/%Y}")

    start_x = datetime.combine(period_start, datetime.min.time())
    end_x = datetime.combine(period_end + timedelta(days=1), datetime.min.time())
    axis.annotate(
        "",
        xy=(end_x, 0),
        xytext=(start_x, 0),
        arrowprops={
            "arrowstyle": "-|>",
            "mutation_scale": 18,
            "linewidth": 2.2,
            "color": "#344054",
            "zorder": 2,
        },
    )
    colors = {"critical": "#d1495b", "other": "#377eb8", "actual": "#079455"}
    for item, label, label_width, (side, lane) in zip(
        timeline_items, labels, label_widths, lane_assignments
    ):
        task, task_start, task_finish, _, _, critical, days = item
        marker_x = datetime.combine(task_start, datetime.min.time()) + timedelta(hours=12)
        y = (lane + 1) * (1 if side == 0 else -1)
        color = (
            colors["actual"] if mode == "reel"
            else colors["critical"] if critical
            else colors["other"]
        )
        position = (task_start - period_start).days + 0.5
        half_width = label_width / 2
        alignment = (
            "right" if position + half_width > period_days
            else "left" if position - half_width < 0
            else "center"
        )
        axis.plot(
            marker_x,
            0,
            marker="o",
            markersize=8,
            markerfacecolor=color,
            markeredgecolor="white",
            markeredgewidth=1.2,
            zorder=4,
        )
        if mode == "reel":
            for actual_day in days:
                actual_x = (
                    datetime.combine(actual_day, datetime.min.time())
                    + timedelta(hours=12)
                )
                axis.plot(
                    actual_x,
                    0,
                    marker="o",
                    markersize=5,
                    markerfacecolor=color,
                    markeredgecolor="white",
                    markeredgewidth=0.7,
                    zorder=5,
                )
            if task_finish > task_start:
                axis.plot(
                    [
                        datetime.combine(task_start, datetime.min.time()) + timedelta(hours=12),
                        datetime.combine(task_finish, datetime.min.time()) + timedelta(hours=12),
                    ],
                    [0, 0],
                    color=color,
                    linewidth=3,
                    alpha=0.7,
                    zorder=3,
                )
        axis.plot(
            [marker_x, marker_x],
            [0.08 if side == 0 else -0.08, y * 0.78],
            color=color,
            linewidth=1,
            alpha=0.8,
            zorder=1,
        )
        axis.text(
            marker_x,
            y,
            label,
            ha=alignment,
            va="bottom" if side == 0 else "top",
            fontsize=8,
            color="#101828",
            bbox={
                "boxstyle": "round,pad=0.45",
                "facecolor": (
                    "#ecfdf3" if mode == "reel"
                    else "#fff4f3" if critical
                    else "#eff8ff"
                ),
                "edgecolor": color,
                "linewidth": 0.8,
            },
            zorder=3,
        )

    visible_milestones = [
        (name, day) for name, day in (milestones or [])
        if period_start <= day <= period_end
    ]
    for name, day in visible_milestones:
        x = datetime.combine(day, datetime.min.time()) + timedelta(hours=12)
        axis.axvline(x, color="#b54708", linestyle="--", linewidth=1.2, alpha=0.8)
        axis.text(
            x, lane_count + 0.45, shorten(f"◆ {name}", width=28, placeholder="..."),
            rotation=35, ha="left", va="bottom", fontsize=8, color="#b54708",
        )

    if mode == "prevu":
        axis.legend(handles=[
            Patch(color=colors["critical"], label="Chemin critique"),
            Patch(color=colors["other"], label="Autres tâches"),
        ], loc="upper center", bbox_to_anchor=(0.5, 1.12), ncol=2, frameon=False)
        subtitle = (
            "Frise chronologique prévisionnelle : tâches positionnées selon leur "
            "début prévu; effort et durée ouvrée indiqués sur chaque tâche. "
            f"{len(timeline_items)} tâche(s) sur la période."
        )
    else:
        axis.legend(
            handles=[Patch(color=colors["actual"], label="Activité réelle")],
            loc="upper center",
            bbox_to_anchor=(0.5, 1.12),
            frameon=False,
        )
        subtitle = (
            "Frise chronologique réelle : repères sur les jours avec heures saisies, "
            "positionnés selon la première activité de chaque tâche. "
            f"{len(timeline_items)} tâche(s), heures réelles cumulées et période "
            "d'activité indiquées."
        )
        if unmatched:
            subtitle += ". Activités non attribuées : " + "; ".join(unmatched[:5])
            if len(unmatched) > 5:
                subtitle += f" (+{len(unmatched) - 5})"
        if not timeline_items:
            axis.text(
                0.5,
                0.5,
                "Aucune activité réelle enregistrée dans cette période",
                ha="center",
                va="center",
                transform=axis.transAxes,
                color="#667085",
            )
    if person:
        subtitle = f"Membre : {person}. " + subtitle
    if visible_milestones:
        subtitle += " Jalons : " + ", ".join(
            f"{name} ({day:%d/%m/%Y})" for name, day in visible_milestones
        ) + "."
    figure.subplots_adjust(top=0.84, bottom=0.14, left=0.04, right=0.98)
    figure.text(0.025, 0.025, subtitle, fontsize=8, wrap=True)
    output = BytesIO()
    figure.savefig(output, format="png", dpi=150)
    output.seek(0)
    return output, subtitle
