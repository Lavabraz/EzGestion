import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

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
        await self.tree.sync()
        refresh_wbs_task_cache.start()
        log.info("Commandes Discord synchronisées")


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


client.run(DISCORD_TOKEN)
