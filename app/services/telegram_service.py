# app/services/telegram_service.py

import logging
import httpx
from app.config import TELEGRAM_BOT_TOKEN, STORIES_BOT_TOKEN, STORIES_CHAT_ID

logger = logging.getLogger(__name__)


def _bot_token_for(chat_id: str) -> str:
    """Return the correct bot token for the given chat_id."""
    if STORIES_CHAT_ID and str(chat_id) == str(STORIES_CHAT_ID):
        return STORIES_BOT_TOKEN
    return TELEGRAM_BOT_TOKEN


def send_message(chat_id: str, text: str) -> bool:
    """Send Telegram message with markdown first, then plain-text fallback."""
    token = _bot_token_for(chat_id)
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as md_err:
        logger.warning(f"Telegram Markdown send failed; retrying plain text: {md_err}")

    try:
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as plain_err:
        logger.exception(f"Telegram plain-text send failed: {plain_err}")
        return False


def send_video_for_manual_post(
    chat_id: str,
    video_path_or_url: str,
    title: str,
    caption: str,
    source_label: str = "",
) -> bool:
    """
    Send a video file (local path or GCS URL) + formatted post caption to Telegram
    for manual YouTube posting. Falls back to GCS download link if file upload fails
    (file too large, Cloud Run temp file gone, etc.).
    """
    label = f"[{source_label.upper()}] " if source_label else ""
    caption_text = (
        f"📹 {label}Manual Post Required\n\n"
        f"*Title:* {title}\n\n"
        f"*Caption:*\n{caption}\n\n"
        f"_(Download video → post manually to YouTube Shorts)_"
    )

    # Telegram caption limit is 1024 chars
    caption_truncated = caption_text[:1024]

    if video_path_or_url.startswith("http"):
        # GCS public URL — send as link so user can download
        return send_message(chat_id, caption_text + f"\n\n🔗 Video: {video_path_or_url}")

    api_url = f"https://api.telegram.org/bot{_bot_token_for(chat_id)}/sendVideo"
    try:
        with open(video_path_or_url, "rb") as f:
            resp = httpx.post(
                api_url,
                data={"chat_id": chat_id, "caption": caption_truncated, "parse_mode": "Markdown"},
                files={"video": f},
                timeout=180,
            )
            resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"Telegram video upload failed ({e}), falling back to text message.")
        return send_message(chat_id, caption_text + "\n\n⚠️ Video file could not be attached.")
