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


@router.get("/admin")
def admin_page(request: Request):
    _require_admin(request)
    return FileResponse("app/static/admin.html")


@router.get("/admin/metrics/summary")
def admin_summary(request: Request):
    _require_admin(request)
    pipeline = firestore_service.get_pipeline_state()
    queue = firestore_service.get_queue_snapshot()
    lock = firestore_service.get_current_lock()
    quota = firestore_service.get_quota_usage_snapshot()
    social = firestore_service.get_social_metrics("youtube")
    jobs = firestore_service.list_recent_jobs(limit=200)

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
    }


@router.get("/admin/metrics/jobs")
def admin_jobs(request: Request, limit: int = 50):
    _require_admin(request)
    safe_limit = max(1, min(limit, 200))
    return {"jobs": firestore_service.list_recent_jobs(limit=safe_limit)}


@router.get("/admin/metrics/failures")
def admin_failures(request: Request, hours: int = 24):
    _require_admin(request)
    cutoff = _hours_ago(max(1, min(hours, 168)))
    jobs = firestore_service.list_recent_jobs(limit=500)
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
    return {"by_error_type": grouped, "failures": failures}


@router.post("/admin/metrics/refresh-social")
def refresh_social(request: Request):
    _require_admin(request)
    try:
        stats = youtube_service.get_channel_stats()
        firestore_service.save_social_metrics("youtube", stats)
        latest = firestore_service.get_social_metrics("youtube")
        return {"status": "ok", "youtube": latest}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"YouTube stats refresh failed: {e}")
