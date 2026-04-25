# app/routes/stories_webhook.py
#
# Telegram webhook for the Short Tales stories bot (separate from the news bot).
# Registered via: POST https://api.telegram.org/bot<STORIES_BOT_TOKEN>/setWebhook
# with url = https://autoframe-.../webhook/telegram/stories

import logging
from fastapi import APIRouter, Request
from app.agents import stories_agent
from app.config import STORIES_CHAT_ID
from app.services.firestore_service import is_duplicate_telegram_update

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/webhook/telegram/stories")
async def telegram_stories_webhook(request: Request):
    data = await request.json()
    update_id = data.get("update_id")
    message = data.get("message", {})
    text = (message.get("text") or "").strip()
    chat_id = str(message.get("chat", {}).get("id", ""))

    # Only process real text messages from the configured stories chat
    if not text or chat_id != str(STORIES_CHAT_ID):
        return {"ok": True}

    # Deduplicate via Firestore — survives cold starts, safe with min-instances=0
    if update_id is not None and is_duplicate_telegram_update(update_id, "stories"):
        logger.info(f"Skipping duplicate stories update_id={update_id}")
        return {"ok": True}

    try:
        stories_agent.handle_reply(chat_id, text)
    except Exception as e:
        logger.exception(f"stories handle_reply failed for chat_id={chat_id} text={text!r}: {e}")

    return {"ok": True}
