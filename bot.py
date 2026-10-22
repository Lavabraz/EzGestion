import asyncio
import logging
import os
import threading
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from gantt import WorkCalendar, configured_vacations, french_public_holidays
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
    log.info("Connecté à Discord en tant que %s (ID %s)", client.user, client.user.id)
    log.info("Commande /activite disponible dans le salon Discord ID %s", CHANNEL_ID)


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


client.run(DISCORD_TOKEN)
