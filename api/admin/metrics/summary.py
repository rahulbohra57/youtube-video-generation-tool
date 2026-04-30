import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from api._shared import setup_credentials, require_admin, json_response
setup_credentials()

from app.config import ADMIN_DASHBOARD_SECRET
from app.routes.admin import (
    _channel_id, _social_key, _hours_ago, _genre_performance, _parse_iso
)
from app.services import firestore_service, youtube_service
from datetime import datetime, timezone


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not require_admin(dict(self.headers), params, ADMIN_DASHBOARD_SECRET):
            json_response(self, 403, {"error": "Forbidden"})
            return

        channel = _channel_id(params.get("channel_id", ["news"])[0])
        pipeline = firestore_service.get_pipeline_state(channel_id=channel)
        queue = firestore_service.get_queue_snapshot(channel_id=channel)
        lock = firestore_service.get_current_lock()
        quota = firestore_service.get_quota_usage_snapshot()
        social = firestore_service.get_social_metrics(_social_key(channel))
        jobs = [
            j for j in firestore_service.list_recent_jobs(limit=200)
            if j.get("channel_id", "news") == channel
        ]

        cutoff = _hours_ago(24)
        total_24h = completed_24h = failed_24h = 0
        durations = []
        last_run_at = None
        for j in jobs:
            updated = _parse_iso(j.get("updated_at"))
            if updated and (last_run_at is None or updated > last_run_at):
                last_run_at = updated
            if not updated or updated.timestamp() < cutoff:
                continue
            status = j.get("status")
            if status in ("completed", "failed"):
                total_24h += 1
            if status == "completed":
                completed_24h += 1
            if status == "failed":
                failed_24h += 1
            start = _parse_iso(j.get("started_at"))
            end = _parse_iso(j.get("finished_at"))
            if start and end and end >= start:
                durations.append((end - start).total_seconds())

        success_rate = round((completed_24h / total_24h) * 100, 1) if total_24h else 0.0
        avg_duration = round(sum(durations) / len(durations), 1) if durations else 0.0

        json_response(self, 200, {
            "channel_id": channel,
            "queue": queue,
            "pipeline": pipeline,
            "lock": lock,
            "quota": quota,
            "jobs_24h": {
                "total": total_24h,
                "completed": completed_24h,
                "failed": failed_24h,
                "success_rate_pct": success_rate,
                "avg_duration_seconds": avg_duration,
            },
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "youtube": {
                "subscriber_count": int(social.get("subscriber_count", 0)),
                "view_count": int(social.get("view_count", 0)),
                "video_count": int(social.get("video_count", 0)),
                "updated_at": social.get("updated_at"),
            },
            "genre_performance_14d": _genre_performance(jobs, hours=24 * 14),
        })

    def log_message(self, *args):
        pass
