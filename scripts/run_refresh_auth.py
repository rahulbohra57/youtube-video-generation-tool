import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import youtube_service, telegram_service, firestore_service
from app.config import get_chat_id

results = {}
for ch in ["news", "stories"]:
    if firestore_service.is_auth_recently_failed(ch):
        # Auth failed recently and user was already notified — skip until re-auth clears the flag.
        print(f"Skipping auth refresh for '{ch}' — auth failed recently, waiting for manual re-auth.")
        results[ch] = "skipped_recent_failure"
        continue
    try:
        youtube_service.get_credentials(channel_id=ch)
        results[ch] = "ok"
        firestore_service.clear_auth_failure(ch)
    except Exception as e:
        results[ch] = str(e)

failed_channels = [ch for ch, status in results.items() if status not in ("ok", "skipped_recent_failure")]

if failed_channels:
    for ch in failed_channels:
        channel_label = "Short Tales" if ch == "stories" else "Kurrent Affairs"
        reauth_url = youtube_service._auth_url(ch)
        alert = (
            f"🔴 *YouTube OAuth token refresh failed\\!*\n\n"
            f"*{channel_label}* \\(`{ch}`\\) needs re\\-authentication\\.\n\n"
            f"Tap to reconnect \\(takes 10 seconds\\):\n"
            f"{reauth_url}\n\n"
            f"_Auto\\-posting will resume after you authenticate\\. "
            f"This alert will not repeat until you do\\._"
        )
        try:
            telegram_service.send_message(get_chat_id(ch), alert, channel_id=ch)
            firestore_service.mark_auth_failure(ch)
        except Exception:
            pass

print(f"Token refresh results: {results}")
