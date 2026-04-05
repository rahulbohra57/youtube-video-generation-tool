# app/routes/webhook.py

import logging
from fastapi import APIRouter, Request
from app.agents import whatsapp_agent
from app.config import TELEGRAM_CHAT_ID

router = APIRouter()
logger = logging.getLogger(__name__)

# In-memory dedup set — prevents processing the same Telegram update twice
# (Cloud Run min-instances=1 keeps this alive; good enough for single-user bot)
_seen_update_ids: set[int] = set()


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

    # Deduplicate: Telegram retries unacknowledged updates
    if update_id is not None:
        if update_id in _seen_update_ids:
            logger.info(f"Skipping duplicate update_id={update_id}")
            return {"ok": True}
        _seen_update_ids.add(update_id)
        # Keep the set bounded
        if len(_seen_update_ids) > 1000:
            _seen_update_ids.clear()

    # Run synchronously — handle_reply is fast (Firestore read + Cloud Tasks enqueue)
    try:
        whatsapp_agent.handle_reply(chat_id, text)
    except Exception as e:
        logger.exception(f"handle_reply failed for chat_id={chat_id} text={text!r}: {e}")

    return {"ok": True}
