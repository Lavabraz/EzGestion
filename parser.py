"""Analyse les messages du type : 1h30 Développer la connexion 18/10/2026."""
from dataclasses import dataclass
from datetime import date
import re
from typing import Optional

DURATION_RE = re.compile(
    r"^\s*(?:(?P<hours>\d+(?:[.,]\d+)?)\s*h(?:\s*(?P<minutes>\d{1,2})\s*(?:m|min|minutes?)?)?"
    r"|(?P<mins>\d+)\s*(?:m|min|minutes?)"
    r"|(?P<clock>\d{1,2}:\d{2}))\s*$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})\s*$")


@dataclass(frozen=True)
class Activity:
    duration_minutes: int
    duration_text: str
    task: str
    activity_date: str


def _parse_duration(value: str) -> Optional[int]:
    match = DURATION_RE.match(value)
    if not match:
        return None

    if match.group("clock"):
        hours, minutes = map(int, match.group("clock").split(":"))
        if minutes >= 60:
            return None
        total = hours * 60 + minutes
    elif match.group("mins"):
        total = int(match.group("mins"))
    else:
        hours = float(match.group("hours").replace(",", "."))
        minutes = int(match.group("minutes") or 0)
        if minutes >= 60:
            return None
        total = round(hours * 60) + minutes

    if total <= 0 or total > 24 * 60:
        return None
    return total


def _format_duration(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h{minutes:02d}"
    if hours:
        return f"{hours}h"
    return f"{minutes}min"


def parse_activity(
    content: str,
    activity_date: str | None = None,
) -> Optional[Activity]:
    """
    Formats acceptés :
      1h30 Développement du système de connexion
      45min Correction du bug de sauvegarde
      2h Rédaction du rapport 18/10/2026
      01:30 Revue de code 18-10-2026

    La durée est le premier élément. Une date éventuelle doit être le dernier élément.
    Le séparateur entre les éléments est un espace, pas un signe +.
    """
    if not content or not content.strip():
        return None

    text = content.strip()
    # La durée doit être suivie d'un espace et du texte de tâche.
    match = re.match(r"^\s*(\S+)\s+(.+?)\s*$", text)
    if not match:
        return None

    duration_token, remainder = match.groups()
    minutes = _parse_duration(duration_token)
    if minutes is None:
        return None

    task = remainder.strip()

    if activity_date is not None:
        date_match = DATE_RE.fullmatch(activity_date)
        if not date_match:
            return None
        day, month, year = map(int, date_match.groups())
        try:
            resolved_date = date(year, month, day).isoformat()
        except ValueError:
            return None
    else:
        resolved_date = date.today().isoformat()
        # Si le dernier groupe ressemble à une date, valider et retirer ce groupe.
        date_match = re.search(r"(?:^|\s)(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s*$", task)
        if date_match:
            raw_date = date_match.group(1)
            parts = re.split(r"[/-]", raw_date)
            day, month, year = map(int, parts)
            try:
                resolved_date = date(year, month, day).isoformat()
            except ValueError:
                return None
            task = task[:date_match.start()].strip()

    if not task:
        return None

    return Activity(
        duration_minutes=minutes,
        duration_text=_format_duration(minutes),
        task=task,
        activity_date=resolved_date,
    )
