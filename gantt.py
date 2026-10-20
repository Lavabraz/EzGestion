"""Planning and rendering utilities for WBS Gantt charts."""
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
import math
import os
import re
from functools import lru_cache
from typing import Iterable

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import Normalize
from matplotlib.dates import DateFormatter
from matplotlib.figure import Figure
from matplotlib.patches import Patch, Rectangle


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
    for date_format in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"Date Google Sheets invalide : {value!r}.")


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
    activity_rows: list[tuple[str, str, str]],
    period_start: date,
    period_end: date,
) -> tuple[dict[str, dict[date, float]], list[str]]:
    name_to_tasks: dict[str, list[WbsTask]] = defaultdict(list)
    id_to_task = {task.task_id: task for task in tasks}
    for task in tasks:
        name_to_tasks[task.name.casefold()].append(task)

    totals: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    unmatched: dict[str, float] = defaultdict(float)
    for raw_day, raw_hours, raw_name in activity_rows:
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
        for row_day, row_hours, _ in activity_rows:
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


def render_gantt(
    tasks: list[WbsTask],
    activity_rows: list[tuple[str, str, str]],
    period_start: date,
    period_end: date,
    mode: str,
    calendar: WorkCalendar,
    hours_per_person_per_day: float = 7.0,
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

    figure_height = max(4.2, 0.42 * len(display_tasks) + 2.2)
    figure = Figure(figsize=(14, figure_height), constrained_layout=True)
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(1, 1, 1)
    y_positions = list(range(len(display_tasks)))
    labels = [
        f"{task.task_id} — {task.name}"
        + (" (CP prévu)" if mode == "reel" and task.task_id in path_ids else "")
        for task in display_tasks
    ]
    axis.set_yticks(y_positions, labels)
    axis.invert_yaxis()
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
    axis.set_title(
        f"Gantt { 'prévisionnel' if mode == 'prevu' else 'réel' } "
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
            item.task.task_id: item
            for item in planned
            if item.task.task_id in y_by_id
        }
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
            axis.text(
                item.start + timedelta(days=width / 2),
                y_by_id[task.task_id],
                f"{task.effort_hours:g} h",
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
                axis.annotate(
                    "",
                    xy=(target.start, y_by_id[task.task_id]),
                    xytext=(source.finish + timedelta(days=1), y_by_id[dependency]),
                    arrowprops={"arrowstyle": "->", "color": "#444444", "lw": 0.8},
                )
        axis.legend(handles=[
            Patch(color="#d1495b", label="Chemin critique"),
            Patch(color="#377eb8", label="Autres tâches"),
        ], loc="upper right")
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
                axis.text(
                    datetime.combine(actual_day, datetime.min.time()) + timedelta(hours=12),
                    y_by_id[task.task_id],
                    f"{hours:g} h",
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

    subtitle = f"Période {period_start:%d/%m/%Y} au {period_end:%d/%m/%Y}. {subtitle}"
    figure.text(0.01, 0.005, subtitle, fontsize=8, wrap=True)
    output = BytesIO()
    figure.savefig(output, format="png", dpi=150)
    output.seek(0)
    return output, subtitle
