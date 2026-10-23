"""Écrit les activités dans les blocs prévus pour chaque membre du Google Sheet."""
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

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
            "GOOGLE_WORKSHEET_NAME", "Copie de HeuresIndividuelles"
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
                activities.append((row[0], row[1], row[2]))
        return wbs_rows, activities

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

    def _refresh_wbs_participation(
        self,
        selected_row: int,
        selected_member: str,
    ):
        """Reconstruit les participants WBS à partir des activités déjà saisies."""
        rows_by_task = {}
        for row, task_name in self.wbs_tasks:
            rows_by_task.setdefault(task_name.casefold(), []).append(row)

        participants_by_row = {}
        for member in self.members.values():
            values = self.worksheet.get(
                f"{member['date']}{self.start_row}:{member['task']}",
                value_render_option="UNFORMATTED_VALUE",
            )
            for activity in values:
                if len(activity) < 3 or not str(activity[2]).strip():
                    continue
                matching_rows = rows_by_task.get(str(activity[2]).strip().casefold(), [])
                if len(matching_rows) == 1:
                    participants_by_row.setdefault(matching_rows[0], set()).add(
                        member["display"]
                    )

        participants_by_row.setdefault(selected_row, set()).add(selected_member)

        updates = []
        for row, _ in self.wbs_tasks:
            names = sorted(
                participants_by_row.get(row, set()),
                key=str.casefold,
            )
            updates.append(
                {
                    "range": f"L{row}",
                    "values": [[", ".join(names)]],
                }
            )

        if updates:
            self.wbs_worksheet.batch_update(
                updates,
                value_input_option="USER_ENTERED",
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
        self._refresh_wbs_participation(wbs_row, member["display"])
        log.info(
            "Activité de %s écrite pour %r dans %s%d:%s%d",
            member["display"], task_name, date_col, row, task_col, row,
        )
        return task_name
