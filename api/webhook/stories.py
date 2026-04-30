import sys
import os
import json
import logging
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from api._shared import setup_credentials, json_response
setup_credentials()

from app.config import STORIES_CHAT_ID
from app.agents import stories_agent
from app.services.firestore_service import is_duplicate_telegram_update

logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw)
        except Exception:
            json_response(self, 400, {"error": "invalid json"})
            return

        update_id = data.get("update_id")
        message = data.get("message", {})
        text = (message.get("text") or "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        if not text or chat_id != str(STORIES_CHAT_ID):
            json_response(self, 200, {"ok": True})
            return

        if update_id is not None and is_duplicate_telegram_update(update_id, "stories"):
            json_response(self, 200, {"ok": True})
            return

        try:
            stories_agent.handle_reply(chat_id, text)
        except Exception as e:
            logger.exception(f"stories handle_reply failed: {e}")

        json_response(self, 200, {"ok": True})

    def log_message(self, *args):
        pass
