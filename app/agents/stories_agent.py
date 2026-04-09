# app/agents/stories_agent.py
#
# Thin wrapper for the Short Tales stories channel.
# All bot commands (STATS, CREATE, STOP, REDO, etc.) delegate to whatsapp_agent
# with channel_id="stories" so they operate on the correct YouTube channel,
# Firestore pipeline state, and Telegram chat.

from datetime import datetime, timezone
from app.services import telegram_service
from app.config import STORIES_CHAT_ID


def handle_reply(chat_id: str, body: str):
    """Entry point for the stories Telegram bot webhook."""
    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply(chat_id, body, channel_id="stories")


def send_post_result(title: str, url: str, public_id: str = "", live_date: str = "", live_time: str = "", mood: str = ""):
    """Notify the stories Telegram channel when a Short Tales video goes live."""
    id_line = f"\nId: `{public_id}`" if public_id else ""
    mood_line = f"\nMood: {mood.title()}" if mood else ""
    date_line = live_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    time_line = live_time or datetime.now(timezone.utc).strftime("%H:%M UTC")
    message = (
        "✅ Your story is live on Short Tales\n"
        f"Live Link: {url}\n"
        f"Date: {date_line}\n"
        f"Time: {time_line}"
        f"{id_line}"
        f"{mood_line}"
    )
    telegram_service.send_message(STORIES_CHAT_ID, message)
