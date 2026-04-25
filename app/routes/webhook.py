# app/routes/webhook.py

import logging
from fastapi import APIRouter, Request
from app.agents import whatsapp_agent
from app.config import TELEGRAM_CHAT_ID
from app.services.firestore_service import is_duplicate_telegram_update

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    data = await request.json()
    update_id = data.get("update_id")
    message = data.get("message", {})
    text = (message.get("text") or "").strip()
    chat_id = str(message.get("chat", {}).get("id", ""))

    # Only process real text messages from the configured chat
    if not text or chat_id != str(TELEGRAM_CHAT_ID):
        return {"ok": True}

    # Deduplicate via Firestore — survives cold starts, safe with min-instances=0
    if update_id is not None and is_duplicate_telegram_update(update_id, "news"):
        logger.info(f"Skipping duplicate update_id={update_id}")
        return {"ok": True}

    try:
        whatsapp_agent.handle_reply(chat_id, text)
    except Exception as e:
        logger.exception(f"handle_reply failed for chat_id={chat_id} text={text!r}: {e}")

    return {"ok": True}
