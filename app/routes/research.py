# app/routes/research.py

import logging
from fastapi import APIRouter, HTTPException, Request
from app.agents import lead_researcher
from app.config import SCHEDULER_SECRET
from app.services import firestore_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/research/run")
def run_research(request: Request):
    """Called by Cloud Scheduler every 2h. Always returns 200 to prevent retries."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        batch_id = lead_researcher.run()
    except Exception as e:
        logger.exception(f"lead_researcher.run() failed: {e}")
        return {"status": "error", "reason": str(e)[:500]}
    if not batch_id:
        return {"status": "skipped", "reason": "outside_suggestion_window_or_no_fresh_news"}
    return {"status": "ok", "batch_id": batch_id}


@router.post("/research/retry-failed")
def retry_failed(request: Request):
    """Called by Cloud Scheduler every 4h to retry the latest failed auto-pipeline."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        batch_id = lead_researcher.retry_failed_pipeline()
    except Exception as e:
        logger.exception(f"retry_failed_pipeline() failed: {e}")
        return {"status": "error", "reason": str(e)[:500]}
    if not batch_id:
        return {"status": "skipped", "reason": "no_failed_jobs_or_pipeline_busy"}
    return {"status": "ok", "batch_id": batch_id}


@router.post("/research/update-analytics")
def update_analytics(request: Request):
    """Called by Cloud Scheduler daily. Fetches YouTube analytics for all completed jobs."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        from app.services import youtube_service
        jobs = firestore_service.list_recent_jobs(limit=200)
        updated = 0
        for job in jobs:
            if job.get("status") != "completed":
                continue
            video_id = youtube_service.extract_video_id(job.get("youtube_url", ""))
            if not video_id:
                continue
            analytics = youtube_service.fetch_video_analytics(video_id)
            if analytics:
                firestore_service.update_job_analytics(job["job_id"], analytics)
                updated += 1
        return {"status": "ok", "updated": updated}
    except Exception as e:
        logger.exception(f"update_analytics failed: {e}")
        return {"status": "error", "reason": str(e)[:500]}


@router.post("/research/daily-digest")
def daily_digest(request: Request):
    """Called by Cloud Scheduler daily at 8am IST."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        lead_researcher.send_daily_digest()
    except Exception as e:
        logger.exception(f"send_daily_digest() failed: {e}")
        return {"status": "error", "reason": str(e)[:500]}
    return {"status": "ok"}
