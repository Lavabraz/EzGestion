import asyncio
from dataclasses import replace
import logging
import math
import os
import threading
from datetime import date, datetime, time
from io import BytesIO
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from gantt import (
    WorkCalendar,
    configured_vacations,
    french_public_holidays,
    infer_period_bounds,
    parse_gantt_data,
    render_gantt,
    render_timeline,
)
from parser import parse_activity
from recurring_activities import activities_for_date
from sheets import GoogleSheetWriter

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("activity-bot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "").strip()
PARIS_TIMEZONE = ZoneInfo("Europe/Paris")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN est absent du fichier .env")
if not CHANNEL_ID.isdigit():
    raise RuntimeError("DISCORD_CHANNEL_ID doit être l'identifiant numérique du salon Discord.")

_writer: GoogleSheetWriter | None = None
_writer_lock = threading.Lock()
_participation_synced = False


def _get_writer() -> GoogleSheetWriter:
    global _writer
    with _writer_lock:
        if _writer is None:
            log.info("Connexion à Google Sheets...")
            _writer = GoogleSheetWriter()
            log.info("Connexion à Google Sheets établie.")
        return _writer


def _refresh_wbs_tasks() -> None:
    _get_writer().refresh_wbs_tasks()


def _write_activity(record: dict, wbs_row: int) -> str:
    return _get_writer().write_activity(record, wbs_row)


intents = discord.Intents.default()


@tasks.loop(minutes=5)
async def refresh_wbs_task_cache():
    try:
        await asyncio.to_thread(_refresh_wbs_tasks)
    except Exception:
        log.exception("Impossible de rafraîchir les tâches WBS pour l'autocomplétion")


def _record_recurring_activities(day: date) -> list[tuple[str, str, int]]:
    writer = _get_writer()
    holidays = french_public_holidays(day.year)
    calendar = WorkCalendar(holidays, configured_vacations())
    activities = activities_for_date(day, calendar)
    if not activities:
        return []

    task_rows = {
        task_name: writer.resolve_recurring_wbs_task(task_name)
        for task_name, _ in activities
    }
    recorded = []
    for task_name, minutes in activities:
        for member in writer.members.values():
            record = {
                "date": day.isoformat(),
                "duration": f"{minutes}min",
                "duration_hours": minutes / 60,
                "member": member["display"],
            }
            try:
                if writer.write_recurring_activity(record, task_rows[task_name]):
                    recorded.append((member["display"], task_name, minutes))
            except Exception:
                log.exception(
                    "Échec d'écriture de l'activité récurrente %r pour %s le %s",
                    task_name,
                    member["display"],
                    day,
                )
    log.info(
        "Activités récurrentes du %s traitées : %d nouvelle(s) entrée(s)",
        day,
        len(recorded),
    )
    return recorded


@tasks.loop(time=time(hour=23, minute=55, tzinfo=PARIS_TIMEZONE))
async def recurring_activity_task():
    day = datetime.now(PARIS_TIMEZONE).date()
    try:
        recorded = await asyncio.to_thread(_record_recurring_activities, day)
    except Exception:
        log.exception("Échec de l'enregistrement des activités récurrentes du %s", day)
        return

    if not recorded:
        return

    try:
        channel = await client.fetch_channel(int(CHANNEL_ID))
    except Exception:
        log.exception(
            "Impossible de récupérer le salon pour confirmer les activités du %s",
            day,
        )
        return

    for member, task_name, minutes in recorded:
        try:
            await channel.send(
                f"Activité automatique enregistrée : **{minutes} min** sur "
                f"**{task_name}** pour **{member}**, le **{day:%d/%m/%Y}**."
            )
        except Exception:
            log.exception(
                "Impossible de confirmer dans Discord l'activité %r pour %s le %s",
                task_name,
                member,
                day,
            )


@recurring_activity_task.before_loop
async def before_recurring_activity_task():
    await client.wait_until_ready()


class ActivityBot(commands.Bot):
    async def setup_hook(self):
        log.info("Connexion à Discord et synchronisation des commandes...")
        channel = await self.fetch_channel(int(CHANNEL_ID))
        guild = getattr(channel, "guild", None)
        if guild is None:
            raise RuntimeError(
                "DISCORD_CHANNEL_ID doit désigner un salon appartenant à un serveur."
            )
        await asyncio.to_thread(_get_writer)
        guild_ref = discord.Object(id=guild.id)
        self.tree.copy_global_to(guild=guild_ref)
        synced = await self.tree.sync(guild=guild_ref)
        refresh_wbs_task_cache.start()
        recurring_activity_task.start()
        log.info(
            "%d commande(s) Discord synchronisée(s) dans le serveur %s",
            len(synced),
            guild.id,
        )


client = ActivityBot(command_prefix="!", intents=intents)


@client.event
async def on_ready():
    global _participation_synced
    log.info("Connecté à Discord en tant que %s (ID %s)", client.user, client.user.id)
    log.info("Commandes /activite et /frise disponibles dans le salon Discord ID %s", CHANNEL_ID)
    if not _participation_synced:
        try:
            count = await asyncio.to_thread(
                _get_writer().rebuild_wbs_participation_from_hours
            )
            _participation_synced = True
            log.info("Synchronisation historique de la participation terminée (%d tâches).", count)
        except Exception:
            log.exception(
                "Impossible de reconstruire la participation WBS depuis la feuille Heures"
            )


@client.tree.command(name="activite", description="Enregistrer du temps sur une tâche WBS")
@app_commands.describe(
    duree="Durée travaillée, par exemple 1h30 ou 45min",
    tache="Tâche à sélectionner dans la liste WBS",
    date="Facultative : JJ/MM/AAAA, AAAA-MM-JJ, JJ/MM, aujourd'hui ou hier",
)
async def activite(
    interaction: discord.Interaction,
    duree: str,
    tache: str,
    date: str | None = None,
):
    if str(interaction.channel_id) != CHANNEL_ID:
        await interaction.response.send_message(
            "Cette commande est disponible uniquement dans le salon configuré.",
        )
        return

    wbs_row = _get_writer().resolve_wbs_selection(tache)
    if wbs_row is None:
        await interaction.response.send_message(
            "Sélectionne une tâche dans les suggestions ou saisis son nom exact "
            "(les doublons doivent être choisis dans les suggestions).",
        )
        return

    parsed = parse_activity(f"{duree} activité", activity_date=date)
    if parsed is None:
        await interaction.response.send_message(
            "Durée ou date invalide. Utilise une durée comme `1h30` et, si besoin, "
            "une date comme `21/10/2026`, `2026-10-21`, `21/10`, `aujourd'hui` "
            "ou `hier`.",
        )
        return

    record = {
        "date": parsed.activity_date,
        "duration": parsed.duration_text,
        "duration_hours": parsed.duration_minutes / 60,
        "member": interaction.user.display_name,
        "discord_user_id": str(interaction.user.id),
        "message_url": "",
        "message_id": str(interaction.id),
        "created_at": interaction.created_at.isoformat(),
    }

    await interaction.response.defer(thinking=True)
    try:
        task_name = await asyncio.to_thread(_write_activity, record, wbs_row)
    except Exception:
        log.exception(
            "Échec d'écriture Google Sheets pour interaction_id=%s",
            interaction.id,
        )
        await interaction.followup.send(
            "Échec de l'écriture dans Google Sheets. Il est possible qu'une seule des "
            "deux feuilles ait été mise à jour; vérifie avant de réessayer. "
            "L'erreur a été consignée dans les logs.",
        )
        return

    await interaction.followup.send(
        f"Activité enregistrée : **{parsed.duration_text}** sur **{task_name}** "
        f"le {datetime.fromisoformat(parsed.activity_date):%d/%m/%Y}.",
    )


@activite.autocomplete("tache")
async def autocomplete_tache(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if str(interaction.channel_id) != CHANNEL_ID:
        return []
    if _writer is None:
        return []
    return [
        app_commands.Choice(name=f"{row} — {task_name}"[:100], value=str(row))
        for row, task_name in _writer.get_wbs_task_choices(current)
    ]


def _parse_gantt_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError as error:
        raise ValueError(
            f"La date « {field_name} » doit être au format JJ/MM/AAAA."
        ) from error



def _make_timeline(
    start: date | None,
    end: date | None,
    mode: str,
    sprint: str | None = None,
    person: str | None = None,
) -> tuple[BytesIO, str | None, str, str | None]:
    sheet_writer = _get_writer()
    wbs_rows, activity_rows = sheet_writer.read_gantt_data()
    sprint_rows, sprint_task_rows, milestone_rows = sheet_writer.read_sprint_management_data()
    tasks, activity_rows = parse_gantt_data(
        wbs_rows,
        activity_rows,
        sheet_writer.wbs_start_row,
        tolerate_planning_errors=mode == "reel",
    )
    if mode == "global":
        if sprint or person:
            raise ValueError("La vue globale couvre tout le projet : ne renseignez pas sprint ou personne.")
        from gantt import parse_sheet_date
        global_milestones = [
            (item["name"], parse_sheet_date(item["date"]))
            for item in milestone_rows
            if parse_sheet_date(item["date"]) is not None
        ]
        # Quand les dates ne sont pas imposées par l'utilisateur, inclure aussi
        # les jalons dans les bornes afin qu'ils ne soient pas coupés de la frise.
        milestone_dates = [day for _, day in global_milestones]
        if start is None and milestone_dates:
            start = min(milestone_dates)
        if end is None and milestone_dates:
            end = max(milestone_dates)
        start, end = infer_period_bounds("prevu", tasks, activity_rows, start, end)
        years = range(start.year, end.year + 1)
        holidays = set().union(*(french_public_holidays(year) for year in years))
        calendar = WorkCalendar(holidays, configured_vacations())
        hours_per_person_per_day = float(os.getenv("GANTT_HOURS_PER_PERSON_PER_DAY", "7"))
        if not math.isfinite(hours_per_person_per_day) or hours_per_person_per_day <= 0:
            raise ValueError("GANTT_HOURS_PER_PERSON_PER_DAY doit être supérieur à zéro.")
        image, description = render_timeline(
            tasks,
            start,
            end,
            calendar,
            hours_per_person_per_day,
            mode="prevu",
            activity_rows=activity_rows,
            milestones=global_milestones,
        )
        try:
            sheet_url, _ = sheet_writer.write_global_timeline_sheet(
                tasks, start, end, calendar, hours_per_person_per_day, global_milestones
            )
            sheet_error = None
        except Exception as error:
            log.exception("Image de frise globale générée, mais écriture Google Sheets échouée")
            sheet_url = None
            sheet_error = str(error)
        return image, sheet_url, description, sheet_error
    selected_sprint = next(
        (item for item in sprint_rows if item["sprint"].casefold() == sprint.casefold()),
        None,
    ) if sprint else None
    if sprint and selected_sprint is None:
        available = ", ".join(item["sprint"] for item in sprint_rows) or "aucun sprint"
        raise ValueError(f"Sprint {sprint!r} introuvable dans SprintManagement. Sprints disponibles : {available}.")
    from gantt import parse_sheet_date
    selected_milestones = [
        (item["name"], parse_sheet_date(item["date"]))
        for item in milestone_rows
        if parse_sheet_date(item["date"]) is not None
        and (not sprint or item["sprint"].casefold() == sprint.casefold())
    ]
    selected_ids = None
    if selected_sprint:
        selected_ids = {
            str(item["task_id"]).strip().removesuffix(".0")
            for item in sprint_task_rows
            if item["sprint"].casefold() == sprint.casefold()
        }
        if not any(task.task_id in selected_ids for task in tasks):
            raise ValueError(f"Aucune tâche WBS correspondante au sprint {sprint!r}.")
        sprint_assignments = {
            str(item["task_id"]).strip().removesuffix(".0"): tuple(
                name.strip() for name in str(item.get("assignees", "")).split(",")
                if name.strip()
            )
            for item in sprint_task_rows
            if item["sprint"].casefold() == sprint.casefold()
        }
        tasks = [
            replace(task, assignees=sprint_assignments[task.task_id])
            if task.task_id in sprint_assignments and sprint_assignments[task.task_id]
            else task
            for task in tasks
        ]
        from gantt import parse_sheet_date
        sprint_start = parse_sheet_date(selected_sprint["start"])
        sprint_end = parse_sheet_date(selected_sprint["end"])
        if start is None:
            start = sprint_start
        if end is None:
            end = sprint_end
    # Étendre automatiquement la période aux jalons si l'utilisateur n'a pas
    # fixé explicitement les dates. Sinon, respecter strictement son intervalle.
    milestone_dates = [day for _, day in selected_milestones]
    if start is None and milestone_dates:
        start = min(milestone_dates)
    if end is None and milestone_dates:
        end = max(milestone_dates)
    start, end = infer_period_bounds(mode, tasks, activity_rows, start, end)
    if (end - start).days > 365:
        raise ValueError("La période demandée ne peut pas dépasser 366 jours.")
    years = range(start.year, end.year + 1)
    holidays = set().union(*(french_public_holidays(year) for year in years))
    calendar = WorkCalendar(holidays, configured_vacations())
    hours_per_person_per_day = float(
        os.getenv("GANTT_HOURS_PER_PERSON_PER_DAY", "7")
    )
    if not math.isfinite(hours_per_person_per_day) or hours_per_person_per_day <= 0:
        raise ValueError("GANTT_HOURS_PER_PERSON_PER_DAY doit être supérieur à zéro.")
    image, description = render_timeline(
        tasks,
        start,
        end,
        calendar,
        hours_per_person_per_day,
        mode=mode,
        activity_rows=activity_rows,
        person=person,
        milestones=selected_milestones,
        selected_task_ids=selected_ids,
    )
    try:
        sheet_url, _ = sheet_writer.write_timeline_sheet(
            tasks,
            start,
            end,
            calendar,
            hours_per_person_per_day,
            mode,
            activity_rows,
            person,
            selected_milestones,
            selected_ids,
            sprint,
        )
        sheet_error = None
    except Exception as error:
        log.exception("Image de frise générée, mais écriture Google Sheets échouée")
        sheet_url = None
        sheet_error = str(error)
    return image, sheet_url, description, sheet_error



@client.tree.command(
    name="frise",
    description="Générer une frise prévue, réelle ou globale",
)
@app_commands.describe(
    mode="Frise prévisionnelle, basée sur les heures réelles, ou synthèse globale",
    sprint="Sprint à afficher depuis SprintManagement",
    personne="Filtrer la frise sur un membre de l'équipe",
    debut="Début au format JJ/MM/AAAA (sinon déduit du sprint ou des données)",
    fin="Fin au format JJ/MM/AAAA (sinon déduit du sprint ou des données)",
)
@app_commands.choices(mode=[
    app_commands.Choice(name="Prévu", value="prevu"),
    app_commands.Choice(name="Réel", value="reel"),
    app_commands.Choice(name="Globale du projet", value="global"),
])
async def frise(
    interaction: discord.Interaction,
    mode: str = "prevu",
    sprint: str | None = None,
    personne: str | None = None,
    debut: str | None = None,
    fin: str | None = None,
):
    if str(interaction.channel_id) != CHANNEL_ID:
        await interaction.response.send_message(
            "Cette commande est disponible uniquement dans le salon configuré."
        )
        return

    try:
        period_start = _parse_gantt_date(debut, "début") if debut else None
        period_end = _parse_gantt_date(fin, "fin") if fin else None
        if period_start and period_end:
            if period_end < period_start:
                raise ValueError(
                    "La date de fin doit être égale ou postérieure à la date de début."
                )
            if mode != "global" and (period_end - period_start).days > 365:
                raise ValueError("La période demandée ne peut pas dépasser 366 jours.")
    except ValueError as error:
        await interaction.response.send_message(f"Paramètres invalides : {error}")
        return

    await interaction.response.defer(thinking=True)
    try:
        image, sheet_url, description, sheet_error = await asyncio.to_thread(
            _make_timeline, period_start, period_end, mode, sprint, personne
        )
    except Exception as error:
        log.exception(
            "Échec de génération de la frise, interaction_id=%s",
            interaction.id,
        )
        await interaction.followup.send(f"Impossible de générer la frise : {error}")
        return

    sheet_status = (
        f"Version Google Sheets : {sheet_url}"
        if sheet_url
        else "⚠️ L’image a été générée, mais la mise à jour Google Sheets a échoué."
    )
    if sheet_error:
        log.error("Erreur Google Sheets pendant /frise : %s", sheet_error)
    await interaction.followup.send(
        content=f"{description[:1100]}\n{sheet_status}",
        file=discord.File(fp=image, filename="frise.png"),
        allowed_mentions=discord.AllowedMentions.none(),
    )


@frise.autocomplete("sprint")
async def autocomplete_frise_sprint(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if str(interaction.channel_id) != CHANNEL_ID or _writer is None:
        return []
    try:
        sprints, _, _ = await asyncio.to_thread(_writer.read_sprint_management_data)
    except Exception:
        log.exception("Impossible de charger les sprints depuis SprintManagement")
        return []
    query = current.strip().casefold()
    return [
        app_commands.Choice(name=item["sprint"][:100], value=item["sprint"][:100])
        for item in sprints
        if not query or query in item["sprint"].casefold()
    ][:25]


@frise.autocomplete("personne")
async def autocomplete_frise_personne(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if str(interaction.channel_id) != CHANNEL_ID or _writer is None:
        return []
    query = current.strip().casefold()
    return [
        app_commands.Choice(name=member["display"][:100], value=member["display"])
        for member in _get_writer().members.values()
        if not query or query in member["display"].casefold()
    ][:25]


client.run(DISCORD_TOKEN)
