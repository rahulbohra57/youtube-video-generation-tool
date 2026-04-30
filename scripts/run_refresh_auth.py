import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import youtube_service, telegram_service
from app.config import get_chat_id

results = youtube_service.refresh_all_tokens()
failed_channels = [ch for ch, status in results.items() if status != "ok"]

if failed_channels:
    for ch in failed_channels:
        channel_label = "Short Tales" if ch == "stories" else "Kurrent Affairs"
        reauth_url = youtube_service._auth_url(ch)
        alert = (
            f"🔴 *YouTube OAuth token refresh failed!*\n\n"
            f"*{channel_label}* (`{ch}`) — {results[ch]}\n"
            f"Re-authenticate: {reauth_url}\n\n"
            "_Open the link above in a browser to reconnect._"
        )
        try:
            telegram_service.send_message(get_chat_id(ch), alert, channel_id=ch)
        except Exception:
            pass

print(f"Token refresh results: {results}")
