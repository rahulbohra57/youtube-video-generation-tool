# app/routes/admin.py

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.config import ADMIN_DASHBOARD_SECRET
from app.services import firestore_service, youtube_service

router = APIRouter()


def _parse_iso(ts: str | None):
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _require_admin(request: Request):
    if not ADMIN_DASHBOARD_SECRET:
        return
    provided = request.headers.get("X-Admin-Secret") or request.query_params.get("secret", "")
    if provided != ADMIN_DASHBOARD_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


def _hours_ago(hours: int) -> float:
    return datetime.now(timezone.utc).timestamp() - (hours * 3600)


def _channel_id(raw: str) -> str:
    return "stories" if (raw or "").strip().lower() == "stories" else "news"


def _social_key(channel_id: str) -> str:
    return "youtube_stories" if channel_id == "stories" else "youtube"


def _genre_performance(jobs: list[dict], hours: int = 24 * 14) -> list[dict]:
    cutoff = _hours_ago(hours)
    grouped: dict[str, dict] = {}
    for j in jobs:
        if j.get("status") != "completed":
            continue
        updated = _parse_iso(j.get("updated_at"))
        if not updated or updated.timestamp() < cutoff:
            continue
        genre = (j.get("genre") or "unknown").strip().lower()
        analytics = j.get("analytics") or {}
        views = int(analytics.get("view_count", 0) or 0)
        comments = int(analytics.get("comment_count", 0) or 0)
        row = grouped.setdefault(
            genre,
            {
                "genre": genre,
                "videos": 0,
                "total_views": 0,
                "total_comments": 0,
            },
        )
        row["videos"] += 1
        row["total_views"] += views
        row["total_comments"] += comments

    out = []
    for row in grouped.values():
        videos = row["videos"] or 1
        out.append(
            {
                **row,
                "avg_views": round(row["total_views"] / videos, 1),
                "avg_comments": round(row["total_comments"] / videos, 1),
            }
        )
    out.sort(key=lambda x: (x["total_views"], x["total_comments"]), reverse=True)
    return out


@router.get("/admin")
def admin_page(request: Request):
    _require_admin(request)
    return FileResponse("app/static/admin.html")


@router.get("/admin/metrics/summary")
def admin_summary(request: Request, channel_id: str = "news"):
    _require_admin(request)
    channel_id = _channel_id(channel_id)
    pipeline = firestore_service.get_pipeline_state(channel_id=channel_id)
    queue = firestore_service.get_queue_snapshot(channel_id=channel_id)
    lock = firestore_service.get_current_lock()
    quota = firestore_service.get_quota_usage_snapshot()
    social = firestore_service.get_social_metrics(_social_key(channel_id))
    jobs = [
        j for j in firestore_service.list_recent_jobs(limit=200)
        if j.get("channel_id", "news") == channel_id
    ]

    cutoff = _hours_ago(24)
    total_24h = 0
    completed_24h = 0
    failed_24h = 0
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

    return {
        "channel_id": channel_id,
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
    }


@router.get("/admin/metrics/jobs")
def admin_jobs(request: Request, limit: int = 50, channel_id: str = "news"):
    _require_admin(request)
    channel_id = _channel_id(channel_id)
    safe_limit = max(1, min(limit, 200))
    jobs = [
        j for j in firestore_service.list_recent_jobs(limit=500)
        if j.get("channel_id", "news") == channel_id
    ][:safe_limit]
    return {"channel_id": channel_id, "jobs": jobs}


@router.get("/admin/metrics/failures")
def admin_failures(request: Request, hours: int = 24, channel_id: str = "news"):
    _require_admin(request)
    channel_id = _channel_id(channel_id)
    cutoff = _hours_ago(max(1, min(hours, 168)))
    jobs = [
        j for j in firestore_service.list_recent_jobs(limit=500)
        if j.get("channel_id", "news") == channel_id
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
    return {"channel_id": channel_id, "by_error_type": grouped, "failures": failures}


@router.post("/admin/metrics/refresh-social")
def refresh_social(request: Request, channel_id: str = "news"):
    _require_admin(request)
    channel_id = _channel_id(channel_id)
    try:
        stats = youtube_service.get_channel_stats(channel_id=channel_id)
        key = _social_key(channel_id)
        firestore_service.save_social_metrics(key, stats)
        latest = firestore_service.get_social_metrics(key)
        return {"status": "ok", "channel_id": channel_id, "youtube": latest}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"YouTube stats refresh failed: {e}")
