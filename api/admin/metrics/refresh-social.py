import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from api._shared import setup_credentials, require_admin, json_response
setup_credentials()

from app.config import ADMIN_DASHBOARD_SECRET
from app.routes.admin import _channel_id, _social_key
from app.services import firestore_service, youtube_service


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not require_admin(dict(self.headers), params, ADMIN_DASHBOARD_SECRET):
            json_response(self, 403, {"error": "Forbidden"})
            return

        channel = _channel_id(params.get("channel_id", ["news"])[0])
        try:
            stats = youtube_service.get_channel_stats(channel_id=channel)
            key = _social_key(channel)
            firestore_service.save_social_metrics(key, stats)
            latest = firestore_service.get_social_metrics(key)
            json_response(self, 200, {"status": "ok", "channel_id": channel, "youtube": latest})
        except Exception as e:
            json_response(self, 500, {"error": f"YouTube stats refresh failed: {e}"})

    def log_message(self, *args):
        pass
