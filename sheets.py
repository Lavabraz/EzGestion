"""Écrit les activités dans les blocs prévus pour chaque membre du Google Sheet."""
import logging
import os
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

    def _next_row(self, date_col: str, duration_col: str, task_col: str) -> int:
        """Trouve la première ligne vide dans le bloc d'un membre."""
        end_col = max((date_col, duration_col, task_col), key=_column_number)
        values = self.worksheet.get(f"{date_col}{self.start_row}:{task_col}")
        for offset, row in enumerate(values):
            # Une ligne est libre si les trois cellules du bloc sont vides.
            if not any(str(cell).strip() for cell in row):
                return self.start_row + offset
        return self.start_row + len(values)

    def write_activity(self, record):
        author = str(record.get("member", "")).strip().casefold()
        member = self.members.get(author)
        if member is None:
            raise ValueError(
                f"Le nom Discord {record.get('member')!r} ne correspond à aucun membre configuré. "
                "Configurez MEMBER_THOMAS, MEMBER_LUCAS et MEMBER_NATHAN dans .env."
            )

        date_col, duration_col, task_col = member["date"], member["duration"], member["task"]
        row = self._next_row(date_col, duration_col, task_col)

        # Écriture limitée au bloc de la personne : le récapitulatif et le graphique restent intacts.
        self.worksheet.update(
            range_name=f"{date_col}{row}:{task_col}{row}",
            values=[[
                record["date"],
                record["duration_hours"],
                record["task"],
            ]],
            value_input_option="USER_ENTERED",
        )
        log.info(
            "Activité de %s écrite dans %s%d:%s%d",
            member["display"], date_col, row, task_col, row,
        )
