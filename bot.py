import logging
import os

import discord
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
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    log.info("Connecté à Discord en tant que %s (ID %s)", client.user, client.user.id)
    log.info("Surveillance du salon Discord ID %s", CHANNEL_ID)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or str(message.channel.id) != CHANNEL_ID:
        return

    parsed = parse_activity(message.content)
    if parsed is None:
        log.info("Message ignoré (format non reconnu), message_id=%s", message.id)
        return

    record = {
        "date": parsed.activity_date,
        "duration": parsed.duration_text,
        "duration_hours": parsed.duration_minutes / 60,
        "task": parsed.task,
        "member": message.author.display_name,
        "discord_user_id": str(message.author.id),
        "message_url": message.jump_url,
        "message_id": str(message.id),
        "created_at": message.created_at.isoformat(),
    }

    try:
        # Le travail réseau Google Sheets est bloquant : on le déporte du thread Discord.
        import asyncio
        await asyncio.to_thread(writer.write_activity, record)
        log.info("Activité enregistrée : user=%s, message_id=%s", message.author, message.id)
    except Exception:
        log.exception("Échec d'écriture Google Sheets pour message_id=%s", message.id)


client.run(DISCORD_TOKEN)
