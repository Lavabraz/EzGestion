import asyncio
import logging
import math
import os
from datetime import date, datetime
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from gantt import (
    WorkCalendar,
    configured_vacations,
    french_public_holidays,
    infer_period_bounds,
    parse_wbs_tasks,
    render_gantt,
)
from parser import parse_activity
from sheets import GoogleSheetWriter

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("activity-bot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "").strip()

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN est absent du fichier .env")
if not CHANNEL_ID.isdigit():
    raise RuntimeError("DISCORD_CHANNEL_ID doit être l'identifiant numérique du salon Discord.")

writer = GoogleSheetWriter()
intents = discord.Intents.default()


@tasks.loop(minutes=5)
async def refresh_wbs_task_cache():
    try:
        await asyncio.to_thread(writer.refresh_wbs_tasks)
    except Exception:
        log.exception("Impossible de rafraîchir les tâches WBS pour l'autocomplétion")


class ActivityBot(commands.Bot):
    async def setup_hook(self):
        channel = await self.fetch_channel(int(CHANNEL_ID))
        guild = getattr(channel, "guild", None)
        if guild is None:
            raise RuntimeError(
                "DISCORD_CHANNEL_ID doit désigner un salon appartenant à un serveur."
            )
        guild_ref = discord.Object(id=guild.id)
        self.tree.copy_global_to(guild=guild_ref)
        synced = await self.tree.sync(guild=guild_ref)
        refresh_wbs_task_cache.start()
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
    date="Date de l'activité au format JJ/MM/AAAA (facultatif)",
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

    try:
        wbs_row = int(tache)
    except ValueError:
        await interaction.response.send_message(
            "Sélectionne une tâche dans les suggestions d'autocomplétion.",
        )
        return

    activity_input = f"{duree} activité"
    if date:
        activity_input += f" {date}"
    parsed = parse_activity(activity_input)
    if parsed is None:
        await interaction.response.send_message(
            "Durée ou date invalide. Utilise une durée comme `1h30` et une date "
            "facultative au format `JJ/MM/AAAA`.",
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
        task_name = await asyncio.to_thread(writer.write_activity, record, wbs_row)
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
        f"le {parsed.activity_date}.",
    )


@activite.autocomplete("tache")
async def autocomplete_tache(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if str(interaction.channel_id) != CHANNEL_ID:
        return []
    return [
        app_commands.Choice(name=f"{row} — {task_name}"[:100], value=str(row))
        for row, task_name in writer.get_wbs_task_choices(current)
    ]


def _parse_gantt_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError as error:
        raise ValueError(
            f"La date « {field_name} » doit être au format JJ/MM/AAAA."
        ) from error


def _make_gantt(
    start: date | None,
    end: date | None,
    mode: str,
) -> tuple[BytesIO, str]:
    wbs_rows, activity_rows = writer.read_gantt_data()
    tasks = parse_wbs_tasks(
        wbs_rows,
        writer.wbs_start_row,
        tolerate_planning_errors=mode == "reel",
    )
    start, end = infer_period_bounds(mode, tasks, activity_rows, start, end)
    if (end - start).days > 365:
        raise ValueError("La période demandée ne peut pas dépasser 366 jours.")
    years = range(start.year, end.year + 1)
    holidays = set().union(*(french_public_holidays(year) for year in years))
    calendar = WorkCalendar(holidays, configured_vacations())
    return render_gantt(
        tasks,
        activity_rows,
        start,
        end,
        mode,
        calendar,
        float(os.getenv("GANTT_HOURS_PER_PERSON_PER_DAY", "7")),
    )


@client.tree.command(name="gantt", description="Générer un Gantt WBS sur une période")
@app_commands.describe(
    mode="Heures prévues ou heures réellement enregistrées",
    debut="Début au format JJ/MM/AAAA (par défaut : première date du WBS ou des activités)",
    fin="Fin au format JJ/MM/AAAA (par défaut : dernière date du WBS ou des activités)",
)
@app_commands.choices(mode=[
    app_commands.Choice(name="Prévu", value="prevu"),
    app_commands.Choice(name="Réel", value="reel"),
])
async def gantt(
    interaction: discord.Interaction,
    mode: str,
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
                raise ValueError("La date de fin doit être égale ou postérieure à la date de début.")
            if (period_end - period_start).days > 365:
                raise ValueError("La période demandée ne peut pas dépasser 366 jours.")
        hours_per_person_per_day = float(os.getenv("GANTT_HOURS_PER_PERSON_PER_DAY", "7"))
        if not math.isfinite(hours_per_person_per_day) or hours_per_person_per_day <= 0:
            raise ValueError("GANTT_HOURS_PER_PERSON_PER_DAY doit être supérieur à zéro.")
    except ValueError as error:
        await interaction.response.send_message(f"Paramètres invalides : {error}")
        return

    await interaction.response.defer(thinking=True)
    try:
        image, description = await asyncio.to_thread(
            _make_gantt, period_start, period_end, mode
        )
    except Exception as error:
        log.exception("Échec de génération du Gantt, interaction_id=%s", interaction.id)
        await interaction.followup.send(f"Impossible de générer le Gantt : {error}")
        return

    await interaction.followup.send(
        content=description[:1900],
        file=discord.File(image, filename="gantt.png"),
    )


client.run(DISCORD_TOKEN)
