"""Écrit les activités dans les blocs prévus pour chaque membre du Google Sheet."""
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from gantt import aggregate_actuals, plan_tasks

log = logging.getLogger(__name__)
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _column_number(value: str) -> int:
    value = value.strip().upper()
    if not value or not value.isalpha():
        raise ValueError(f"Colonne invalide : {value!r}")
    result = 0
    for char in value:
        result = result * 26 + ord(char) - ord("A") + 1
    return result


class GoogleSheetWriter:
    def __init__(self):
        self.spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
        self.worksheet_name = os.getenv(
            "GOOGLE_WORKSHEET_NAME", "Heures"
        ).strip()
        self.sprint_worksheet_name = os.getenv(
            "GOOGLE_SPRINT_WORKSHEET_NAME", "SprintManagement"
        ).strip()
        self.start_row = int(os.getenv("GOOGLE_DATA_START_ROW", "4"))
        self.wbs_worksheet_name = os.getenv("GOOGLE_WBS_WORKSHEET_NAME", "WBS").strip()
        self.wbs_start_row = int(os.getenv("GOOGLE_WBS_DATA_START_ROW", "2"))
        self.credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service-account.json")

        # Les colonnes correspondent à la capture : B-D, F-H et J-L.
        self.members = {
            os.getenv("MEMBER_THOMAS", "Thomas").strip().casefold(): {
                "display": os.getenv("MEMBER_THOMAS", "Thomas").strip(),
                "date": "B", "duration": "C", "task": "D",
            },
            os.getenv("MEMBER_LUCAS", "Lucas").strip().casefold(): {
                "display": os.getenv("MEMBER_LUCAS", "Lucas").strip(),
                "date": "F", "duration": "G", "task": "H",
            },
            os.getenv("MEMBER_NATHAN", "Nathan").strip().casefold(): {
                "display": os.getenv("MEMBER_NATHAN", "Nathan").strip(),
                "date": "J", "duration": "K", "task": "L",
            },
        }

        if not self.spreadsheet_id:
            raise RuntimeError("Configurez GOOGLE_SPREADSHEET_ID dans .env")
        if not Path(self.credentials_path).is_file():
            raise RuntimeError(
                f"Fichier Google credentials introuvable : {self.credentials_path}. "
                "Placez le JSON du compte de service à cet emplacement."
            )

        credentials = Credentials.from_service_account_file(self.credentials_path, scopes=SCOPES)
        self.client = gspread.authorize(credentials)
        self.sheets_api = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self.spreadsheet = self.client.open_by_key(self.spreadsheet_id)
        self.worksheet = self.spreadsheet.worksheet(self.worksheet_name)
        self.wbs_worksheet = self.spreadsheet.worksheet(self.wbs_worksheet_name)
        self.sprint_worksheet = self.spreadsheet.worksheet(self.sprint_worksheet_name)
        self.wbs_tasks = []
        self.refresh_wbs_tasks()

    def refresh_wbs_tasks(self):
        """Recharge les tâches WBS utilisées par l'autocomplétion Discord."""
        task_values = self.wbs_worksheet.col_values(4)[self.wbs_start_row - 1:]
        self.wbs_tasks = [
            (self.wbs_start_row + offset, task_name.strip())
            for offset, task_name in enumerate(task_values)
            if task_name.strip()
        ]

    def get_wbs_task_choices(self, query: str):
        query = query.strip().casefold()
        return [
            (row, task_name)
            for row, task_name in self.wbs_tasks
            if not query or query in task_name.casefold()
        ][:25]

    def resolve_wbs_selection(self, selection: str) -> int | None:
        """Resolve an autocomplete row value or an unambiguous exact task name."""
        try:
            return int(selection)
        except ValueError:
            normalized = selection.strip().casefold()
            matching_rows = [
                row for row, task_name in self.wbs_tasks
                if task_name.casefold() == normalized
            ]
            return matching_rows[0] if len(matching_rows) == 1 else None

    def resolve_recurring_wbs_task(self, task_name: str) -> int:
        matching_rows = [
            row for row, name in self.wbs_tasks
            if name.casefold() == task_name.casefold()
        ]
        if len(matching_rows) != 1:
            raise ValueError(
                f"La tâche WBS {task_name!r} doit exister une seule fois "
                "pour enregistrer les activités récurrentes."
            )
        return matching_rows[0]

    @staticmethod
    def _activity_date(value) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, (int, float)):
            return date(1899, 12, 30) + timedelta(days=int(value))
        text = str(value).strip()
        for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(text, date_format).date()
            except ValueError:
                continue
        return None

    def write_recurring_activity(self, record, wbs_row: int) -> bool:
        author = str(record.get("member", "")).strip().casefold()
        member = self.members.get(author)
        if member is None:
            raise ValueError(
                f"Le membre {record.get('member')!r} n'est pas configuré "
                "pour l'enregistrement des activités récurrentes."
            )

        task_name = self._wbs_task_at(wbs_row)
        activity_day = self._activity_date(record["date"])
        if activity_day is None:
            raise ValueError(f"Date d'activité récurrente invalide : {record['date']!r}.")

        date_col, duration_col, task_col = member["date"], member["duration"], member["task"]
        existing_rows = self.worksheet.get(
            f"{date_col}{self.start_row}:{task_col}",
            value_render_option="UNFORMATTED_VALUE",
        )
        for row in existing_rows:
            if len(row) < 3:
                continue
            if (
                self._activity_date(row[0]) == activity_day
                and str(row[2]).strip().casefold() == task_name.casefold()
            ):
                log.info(
                    "Activité récurrente déjà présente pour %s le %s : %s",
                    member["display"],
                    activity_day,
                    task_name,
                )
                return False

        row_number = self._next_row(date_col, duration_col, task_col)
        self.worksheet.update(
            range_name=f"{date_col}{row_number}:{task_col}{row_number}",
            values=[[
                activity_day.isoformat(),
                record["duration_hours"],
                task_name,
            ]],
            value_input_option="USER_ENTERED",
        )
        self._add_wbs_hours(wbs_row, record["duration_hours"])
        log.info(
            "Activité récurrente de %s écrite pour %s le %s",
            member["display"],
            task_name,
            activity_day,
        )
        return True

    def read_gantt_data(self):
        wbs_rows = self.wbs_worksheet.get(
            f"A{self.wbs_start_row}:K",
            value_render_option="UNFORMATTED_VALUE",
        )
        activities = []
        for member in self.members.values():
            values = self.worksheet.get(
                f"{member['date']}{self.start_row}:{member['task']}",
                value_render_option="UNFORMATTED_VALUE",
            )
            for row in values:
                if len(row) < 3 or not any(str(cell).strip() for cell in row):
                    continue
                activities.append((row[0], row[1], row[2], member["display"]))
        return wbs_rows, activities

    def read_sprint_management_data(self):
        """Lit les sprints, tâches affectées et jalons depuis SprintManagement."""
        rows = self.sprint_worksheet.get("A1:R", value_render_option="UNFORMATTED_VALUE")

        def cell(row, index):
            return row[index] if index < len(row) else ""

        def normalized(value):
            return str(value or "").strip().casefold()

        sprint_rows, task_rows, milestone_rows = [], [], []
        for index, row in enumerate(rows):
            headers = [normalized(value) for value in row]
            if all(key in headers for key in ("sprint", "début", "fin", "objectif")):
                for data in rows[index + 1:]:
                    name = str(cell(data, headers.index("sprint")) or "").strip()
                    if not name:
                        break
                    sprint_rows.append({
                            "sprint": name,
                            "start": cell(data, headers.index("début")),
                            "end": cell(data, headers.index("fin")),
                            "objective": cell(data, headers.index("objectif")),
                        })
                break
        for index, row in enumerate(rows):
            headers = [normalized(value) for value in row]
            if all(key in headers for key in ("sprint", "id wbs", "tâche")):
                for data in rows[index + 1:]:
                    sprint_name = str(cell(data, headers.index("sprint")) or "").strip()
                    task_id = str(cell(data, headers.index("id wbs")) or "").strip()
                    task_name = str(cell(data, headers.index("tâche")) or "").strip()
                    if not sprint_name or not task_id or not task_name:
                        break
                    task_rows.append({
                            "sprint": sprint_name,
                            "task_id": task_id.removesuffix(".0"),
                            "status": cell(data, headers.index("statut")) if "statut" in headers else "",
                            "name": task_name,
                            "assignees": str(cell(data, headers.index("répartition")) or "").strip()
                                if "répartition" in headers else "",
                        })
                break
        for index, row in enumerate(rows):
            headers = [normalized(value) for value in row]
            if all(key in headers for key in ("sprint", "jalon", "date de livraison")):
                for data in rows[index + 1:]:
                    sprint_name = str(cell(data, headers.index("sprint")) or "").strip()
                    name = str(cell(data, headers.index("jalon")) or "").strip()
                    day = cell(data, headers.index("date de livraison"))
                    if not sprint_name:
                        break
                    if name and day not in ("", None):
                        milestone_rows.append({"sprint": sprint_name, "name": name, "date": day})
                break
        return sprint_rows, task_rows, milestone_rows

    def write_timeline_sheet(
        self,
        tasks,
        period_start,
        period_end,
        calendar,
        hours_per_person_per_day: float,
        mode: str,
        activity_rows,
        person: str | None = None,
        milestones=None,
        selected_task_ids=None,
    ) -> tuple[str, str]:
        """Écrit la frise chronologique dans un onglet Google Sheets dédié."""
        from datetime import timedelta

        sheet_name = os.getenv("GOOGLE_TIMELINE_WORKSHEET_NAME", "Frise").strip()
        try:
            ws = self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(
                title=sheet_name, rows=500, cols=380
            )

        if mode not in {"prevu", "reel"}:
            raise ValueError("Le mode doit être « prevu » ou « reel ».")
        if (period_end - period_start).days > 365:
            raise ValueError("La période demandée ne peut pas dépasser 366 jours.")

        task_by_id = {task.task_id: task for task in tasks}
        rows = []
        warnings = []
        if mode == "prevu":
            planned = plan_tasks(
                tasks, period_start, period_end, calendar, hours_per_person_per_day
            )
            for item in planned:
                task = item.task
                if selected_task_ids is not None and task.task_id not in selected_task_ids:
                    continue
                if person and not any(
                    name.casefold() == person.casefold() for name in task.assignees
                ):
                    continue
                start = task.start_deadline or item.start
                finish = task.finish_deadline or item.finish
                if finish < start:
                    raise ValueError(
                        f"Tâche WBS {task.task_id} : la date de fin précède la date de début."
                    )
                if finish < period_start or start > period_end:
                    continue
                start, finish = max(start, period_start), min(finish, period_end)
                duration = calendar.workday_distance(start, finish) + int(calendar.is_workday(finish))
                rows.append({
                    "task": task, "start": start, "finish": finish,
                    "hours": task.effort_hours, "duration": duration,
                    "critical": item.critical, "days": {},
                })
            rows.sort(key=lambda row: (row["start"], row["finish"], row["task"].task_id))
            if not rows:
                raise ValueError("Aucune tâche planifiée dans cette période avec ces filtres.")
        else:
            actuals, warnings = aggregate_actuals(
                tasks, activity_rows or [], period_start, period_end, person
            )
            for task_id, days in actuals.items():
                if selected_task_ids is not None and task_id not in selected_task_ids:
                    continue
                if not days:
                    continue
                task = task_by_id[task_id]
                actual_days = sorted(days)
                rows.append({
                    "task": task, "start": actual_days[0], "finish": actual_days[-1],
                    "hours": sum(days.values()), "duration": len(actual_days),
                    "critical": False, "days": days,
                })
            rows.sort(key=lambda row: (row["start"], row["finish"], row["task"].task_id))
            if not rows:
                raise ValueError("Aucune activité réelle enregistrée dans cette période avec ces filtres.")

        days = []
        day = period_start
        while day <= period_end:
            days.append(day)
            day += timedelta(days=1)

        # Colonnes A:H contiennent les informations de tâche; la chronologie commence en I.
        values = []
        title_scope = f" — {person}" if person else ""
        mode_title = "prévisionnelle" if mode == "prevu" else "réelle"
        title = f"Frise {mode_title}{title_scope} — {period_start:%d/%m/%Y} au {period_end:%d/%m/%Y}"
        values.append([title])
        values.append([
            f"Sprint : {next((name for name, _ in (milestones or [])), 'non filtré') if milestones else 'non filtré'}",
            f"Mode : {mode_title}",
            f"Tâches : {len(rows)}",
            "Rouge : chemin critique" if mode == "prevu" else "Vert : jours avec activité",
            "Orange : jalons",
        ])
        header = ["ID WBS", "Tâche", "Lot", "Personnes", "Estimation (h)", "Début", "Fin", "Durée (j ouvrés)"]
        header.extend(day.strftime("%a %d/%m") for day in days)
        values.append(header)

        milestone_rows = []
        for name, milestone_day in (milestones or []):
            if milestone_day is None or not (period_start <= milestone_day <= period_end):
                continue
            row = ["", f"◆ {name}", "JALON", "", "", milestone_day.isoformat(), "", ""]
            row.extend("◆" if day == milestone_day else "" for day in days)
            milestone_rows.append((name, milestone_day, row))
        values.extend(item[2] for item in milestone_rows)
        task_start_row = len(values) + 1
        for item in rows:
            task = item["task"]
            row = [
                task.task_id, task.name, task.lot,
                ", ".join(task.assignees), item["hours"],
                item["start"].isoformat(), item["finish"].isoformat(),
                item["duration"],
            ]
            for day in days:
                if mode == "prevu":
                    row.append("■" if item["start"] <= day <= item["finish"] else "")
                else:
                    row.append(item["days"].get(day, ""))
            values.append(row)

        # Élargir la feuille si l'horizon dépasse sa taille actuelle.
        needed_rows, needed_cols = len(values), len(header)
        if ws.row_count < needed_rows or ws.col_count < needed_cols:
            ws.resize(rows=max(ws.row_count, needed_rows), cols=max(ws.col_count, needed_cols))
        ws.clear()
        ws.update(range_name=f"A1", values=values, value_input_option="USER_ENTERED")

        def col_name(number):
            result = ""
            while number:
                number, rem = divmod(number - 1, 26)
                result = chr(65 + rem) + result
            return result

        last_col = col_name(needed_cols)
        formats = [
            {"range": f"A1:{last_col}1", "format": {
                "backgroundColor": {"red": 0.12, "green": 0.18, "blue": 0.29},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}, "fontSize": 14},
                "verticalAlignment": "MIDDLE",
            }},
            {"range": f"A2:{last_col}2", "format": {
                "backgroundColor": {"red": 0.91, "green": 0.94, "blue": 0.97},
                "textFormat": {"fontSize": 9, "foregroundColor": {"red": 0.2, "green": 0.25, "blue": 0.32}},
            }},
            {"range": f"A3:{last_col}3", "format": {
                "backgroundColor": {"red": 0.18, "green": 0.31, "blue": 0.48},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP",
            }},
            {"range": f"A4:{last_col}{needed_rows}", "format": {
                "verticalAlignment": "MIDDLE", "textFormat": {"fontSize": 9},
            }},
            {"range": f"F4:G{needed_rows}", "format": {"numberFormat": {"type": "DATE", "pattern": "dd/mm/yyyy"}}},
            {"range": f"A1:{last_col}{needed_rows}", "format": {"borders": {
                "bottom": {"style": "SOLID", "color": {"red": 0.85, "green": 0.88, "blue": 0.91}},
            }}},
        ]
        for index, (_, milestone_day, _) in enumerate(milestone_rows, start=4):
            formats.append({"range": f"A{index}:{last_col}{index}", "format": {
                "backgroundColor": {"red": 1, "green": 0.96, "blue": 0.88},
                "textFormat": {"bold": True, "foregroundColor": {"red": 0.55, "green": 0.24, "blue": 0.04}},
            }})
        for index, item in enumerate(rows, start=task_start_row):
            if mode == "prevu":
                color = {"red": 0.82, "green": 0.25, "blue": 0.31} if item["critical"] else {"red": 0.22, "green": 0.49, "blue": 0.75}
                formats.append({"range": f"I{index}:{last_col}{index}", "format": {
                    "horizontalAlignment": "CENTER", "textFormat": {"bold": True, "foregroundColor": color},
                }})
                formats.append({"range": f"I{index}:{last_col}{index}", "format": {"backgroundColor": {"red": 0.95, "green": 0.96, "blue": 0.97}}})
                start_col = 9 + (item["start"] - period_start).days
                end_col = 9 + (item["finish"] - period_start).days
                formats.append({"range": f"{col_name(start_col)}{index}:{col_name(end_col)}{index}", "format": {
                    "backgroundColor": color,
                    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    "horizontalAlignment": "CENTER",
                }})
            else:
                formats.append({"range": f"I{index}:{last_col}{index}", "format": {
                    "horizontalAlignment": "CENTER",
                    "backgroundColor": {"red": 0.96, "green": 0.97, "blue": 0.96},
                }})
                for day, hours in item["days"].items():
                    col = col_name(9 + (day - period_start).days)
                    formats.append({"range": f"{col}{index}", "format": {
                        "backgroundColor": {"red": 0.08, "green": 0.62, "blue": 0.42},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                        "horizontalAlignment": "CENTER",
                    }})
        for index, (_, milestone_day, _) in enumerate(milestone_rows, start=4):
            col = col_name(9 + (milestone_day - period_start).days)
            formats.append({"range": f"{col}{index}", "format": {
                "backgroundColor": {"red": 0.97, "green": 0.58, "blue": 0.04},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "horizontalAlignment": "CENTER",
            }})

        ws.batch_format(formats)
        ws.freeze(rows=3, cols=2)
        ws.set_basic_filter(f"A3:{last_col}{needed_rows}")
        ws.format("A:A", {"columnWidth": 80})
        ws.format("B:B", {"columnWidth": 300, "wrapStrategy": "WRAP"})
        ws.format("C:D", {"columnWidth": 130, "wrapStrategy": "WRAP"})
        ws.format("E:H", {"columnWidth": 105})
        ws.format(f"I:{last_col}", {"columnWidth": 42, "horizontalAlignment": "CENTER"})
        ws.format(f"A1:{last_col}{needed_rows}", {"verticalAlignment": "MIDDLE"})
        ws.format(f"A4:H{needed_rows}", {"wrapStrategy": "WRAP"})
        ws.update_title(sheet_name)
        summary = f"{len(rows)} tâche(s) affichée(s) dans l'onglet « {sheet_name} »."
        if mode == "reel" and warnings:
            summary += " Activités non attribuées : " + "; ".join(warnings[:5])
            if len(warnings) > 5:
                summary += f" (+{len(warnings) - 5})"
        if milestone_rows:
            summary += " Jalons : " + ", ".join(
                f"{name} ({day:%d/%m/%Y})" for name, day, _ in milestone_rows
            ) + "."
        return ws.url, summary

    def _wbs_task_at(self, row: int) -> str:
        if row < self.wbs_start_row:
            raise ValueError("La tâche WBS sélectionnée n'est pas valide.")
        cached_task = dict(self.wbs_tasks).get(row)
        task_name = self.wbs_worksheet.cell(row, 4).value
        if not task_name or not str(task_name).strip():
            raise ValueError(
                f"Aucune tâche WBS n'existe en D{row}. "
                "Relancez la commande et sélectionnez une tâche dans l'autocomplétion."
            )
        task_name = str(task_name).strip()
        if cached_task != task_name:
            raise ValueError(
                "La liste WBS a changé depuis l'autocomplétion. "
                "Relancez la commande et sélectionnez à nouveau la tâche."
            )
        return task_name

    def _next_row(self, date_col: str, duration_col: str, task_col: str) -> int:
        """Trouve la première ligne vide dans le bloc d'un membre."""
        end_col = max((date_col, duration_col, task_col), key=_column_number)
        values = self.worksheet.get(f"{date_col}{self.start_row}:{task_col}")
        for offset, row in enumerate(values):
            # Une ligne est libre si les trois cellules du bloc sont vides.
            if not any(str(cell).strip() for cell in row):
                return self.start_row + offset
        return self.start_row + len(values)

    def _add_wbs_hours(self, row: int, duration_hours: float):
        hours_cell = self.wbs_worksheet.acell(f"K{row}")
        current_value = hours_cell.value
        if current_value in (None, ""):
            current_hours = 0.0
        else:
            try:
                current_hours = float(str(current_value).replace(",", "."))
            except ValueError as error:
                raise ValueError(
                    f"La valeur d'heures WBS en K{row} n'est pas numérique : "
                    f"{current_value!r}"
                ) from error

        new_total = current_hours + float(duration_hours)
        self.wbs_worksheet.update(
            range_name=f"K{row}",
            values=[[new_total]],
            value_input_option="USER_ENTERED",
        )
        log.info(
            "Heures WBS ajoutées en K%d : +%s h, total=%s h",
            row,
            duration_hours,
            new_total,
        )

    def write_activity(self, record, wbs_row: int):
        author = str(record.get("member", "")).strip().casefold()
        member = self.members.get(author)
        if member is None:
            raise ValueError(
                f"Le nom Discord {record.get('member')!r} ne correspond à aucun membre configuré. "
                "Configurez MEMBER_THOMAS, MEMBER_LUCAS et MEMBER_NATHAN dans .env."
            )

        task_name = self._wbs_task_at(wbs_row)
        date_col, duration_col, task_col = member["date"], member["duration"], member["task"]
        row = self._next_row(date_col, duration_col, task_col)

        # Écriture limitée au bloc de la personne : le récapitulatif et le graphique restent intacts.
        self.worksheet.update(
            range_name=f"{date_col}{row}:{task_col}{row}",
            values=[[
                record["date"],
                record["duration_hours"],
                task_name,
            ]],
            value_input_option="USER_ENTERED",
        )
        self._add_wbs_hours(wbs_row, record["duration_hours"])
        log.info(
            "Activité de %s écrite pour %r dans %s%d:%s%d",
            member["display"], task_name, date_col, row, task_col, row,
        )
        return task_name
