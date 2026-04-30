import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from api._shared import setup_credentials, require_admin, json_response
setup_credentials()

from app.config import ADMIN_DASHBOARD_SECRET
from app.routes.admin import _channel_id
from app.services import firestore_service


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not require_admin(dict(self.headers), params, ADMIN_DASHBOARD_SECRET):
            json_response(self, 403, {"error": "Forbidden"})
            return

        channel = _channel_id(params.get("channel_id", ["news"])[0])
        raw_limit = params.get("limit", ["50"])[0]
        safe_limit = max(1, min(int(raw_limit), 200))

        jobs = [
            j for j in firestore_service.list_recent_jobs(limit=500)
            if j.get("channel_id", "news") == channel
        ][:safe_limit]

        json_response(self, 200, {"channel_id": channel, "jobs": jobs})

    def log_message(self, *args):
        pass
