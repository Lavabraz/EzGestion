"""Analyse les messages du type : 1h30 Développer la connexion 18/10/2026."""
from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Optional

DURATION_RE = re.compile(
    r"^\s*(?:(?P<hours>\d+(?:[.,]\d+)?)\s*h(?:\s*(?P<minutes>\d{1,2})\s*(?:m|min|minutes?)?)?"
    r"|(?P<mins>\d+)\s*(?:m|min|minutes?)"
    r"|(?P<clock>\d{1,2}:\d{2}))\s*$",
    re.IGNORECASE,
)
FRENCH_DATE_RE = re.compile(
    r"^\s*(\d{1,2})([./-])(\d{1,2})(?:\2(\d{4}))?\s*$"
)
ISO_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")


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


def parse_activity_date(value: str, today: date | None = None) -> date | None:
    reference_day = today or date.today()
    normalized = value.strip().casefold()
    if normalized in {"aujourd'hui", "aujourd’hui", "today"}:
        return reference_day
    if normalized in {"hier", "yesterday"}:
        return reference_day - timedelta(days=1)

    match = ISO_DATE_RE.fullmatch(value)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = FRENCH_DATE_RE.fullmatch(value)
        if not match:
            return None
        day, month = int(match.group(1)), int(match.group(3))
        year = int(match.group(4)) if match.group(4) else reference_day.year
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_activity(
    content: str,
    activity_date: str | None = None,
    today: date | None = None,
) -> Optional[Activity]:
    """
    Formats acceptés :
      1h30 Développement du système de connexion
      45min Correction du bug de sauvegarde
      2h Rédaction du rapport 18/10/2026
      01:30 Revue de code 18-10-2026

    La durée est le premier élément. Une date éventuelle en fin de message ou
    dans le paramètre activity_date accepte JJ/MM/AAAA, JJ-MM-AAAA, JJ.MM.AAAA,
    AAAA-MM-JJ, JJ/MM (année courante), « aujourd'hui » et « hier ».
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
        resolved_day = parse_activity_date(activity_date, today)
        if resolved_day is None:
            return None
        resolved_date = resolved_day.isoformat()
    else:
        reference_day = today or date.today()
        resolved_date = reference_day.isoformat()
        date_match = re.search(
            r"(?:^|\s)(\d{1,2}[./-]\d{1,2}(?:[./-]\d{4})?"
            r"|\d{4}-\d{1,2}-\d{1,2}|aujourd'hui|aujourd’hui|hier)\s*$",
            task,
            re.IGNORECASE,
        )
        if date_match:
            raw_date = date_match.group(1)
            resolved_day = parse_activity_date(raw_date, reference_day)
            if resolved_day is None:
                return None
            resolved_date = resolved_day.isoformat()
            task = task[:date_match.start()].strip()

    if not task:
        return None

    return Activity(
        duration_minutes=minutes,
        duration_text=_format_duration(minutes),
        task=task,
        activity_date=resolved_date,
    )
