import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from api._shared import setup_credentials, require_admin, json_response
setup_credentials()

from app.config import ADMIN_DASHBOARD_SECRET
from app.routes.admin import _channel_id, _hours_ago, _parse_iso
from app.services import firestore_service


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not require_admin(dict(self.headers), params, ADMIN_DASHBOARD_SECRET):
            json_response(self, 403, {"error": "Forbidden"})
            return

        channel = _channel_id(params.get("channel_id", ["news"])[0])
        raw_hours = params.get("hours", ["24"])[0]
        cutoff = _hours_ago(max(1, min(int(raw_hours), 168)))

        jobs = [
            j for j in firestore_service.list_recent_jobs(limit=500)
            if j.get("channel_id", "news") == channel
        ]
        grouped = {}
        failures = []
        for j in jobs:
            if j.get("status") != "failed":
                continue
            updated = _parse_iso(j.get("updated_at"))
            if not updated or updated.timestamp() < cutoff:
                continue
            err = j.get("error_type", "unknown")
            grouped[err] = grouped.get(err, 0) + 1
            failures.append(j)

        json_response(self, 200, {
            "channel_id": channel,
            "by_error_type": grouped,
            "failures": failures,
        })

    def log_message(self, *args):
        pass
