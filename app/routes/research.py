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
    """Called by Cloud Scheduler every 4h (12am, 4am, 8am, 12pm, 4pm, 8pm IST)."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        batch_id = lead_researcher.run()
    except Exception as e:
        logger.exception(f"lead_researcher.run() failed: {e}")
        raise HTTPException(status_code=500, detail="lead_researcher_failed")
    if not batch_id:
        return {"status": "skipped", "reason": "outside_suggestion_window_or_no_fresh_news"}
    return {"status": "ok", "batch_id": batch_id}


@router.post("/research/retry-failed")
def retry_failed(request: Request):
    """Called by Cloud Scheduler every 4h (IST) to retry the latest failed auto-pipeline."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        batch_id = lead_researcher.retry_failed_pipeline()
    except Exception as e:
        logger.exception(f"retry_failed_pipeline() failed: {e}")
        raise HTTPException(status_code=500, detail="retry_failed_pipeline_error")
    if not batch_id:
        return {"status": "skipped", "reason": "no_failed_jobs_or_pipeline_busy"}
    return {"status": "ok", "batch_id": batch_id}


@router.post("/research/update-analytics")
def update_analytics(request: Request):
    """Called by Cloud Scheduler daily. Fetches YouTube analytics and runs fortnightly schedule update."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        # Fortnightly domain schedule update (no-ops if < 14 days since last update)
        schedule_updated = lead_researcher.update_domain_schedule()

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
        return {"status": "ok", "updated": updated, "schedule_updated": schedule_updated}
    except Exception as e:
        logger.exception(f"update_analytics failed: {e}")
        raise HTTPException(status_code=500, detail="update_analytics_failed")


@router.post("/research/refresh-youtube-auth")
def refresh_youtube_auth(request: Request):
    """Called by Cloud Scheduler every 6h to proactively refresh YouTube OAuth tokens.

    Refreshes access tokens for both channels before they expire. On failure sends
    an urgent Telegram alert with the reauth URL so the team can re-authenticate
    before the next video generation run is affected. Always returns 200 so
    Cloud Scheduler does not retry on failure (Telegram alert is the signal).
    """
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    from app.services import youtube_service
    from app.services.telegram_service import send_message
    from app.config import get_chat_id

    results = youtube_service.refresh_all_tokens()
    failed_channels = [ch for ch, status in results.items() if status != "ok"]

    if failed_channels:
        lines = []
        for ch in failed_channels:
            channel_label = "Short Tales" if ch == "stories" else "Kurrent Affairs"
            reauth_url = youtube_service._auth_url(ch)
            lines.append(
                f"*{channel_label}* (`{ch}`) — {results[ch]}\n"
                f"Re-authenticate: {reauth_url}"
            )
        alert = (
            "🔴 *YouTube OAuth token refresh failed!*\n\n"
            + "\n\n".join(lines)
            + "\n\n_Open the link above in a browser to reconnect._"
        )
        logger.error("YouTube token refresh failed: %s", results)
        for ch in ["news", "stories"]:
            try:
                send_message(get_chat_id(ch), alert, channel_id=ch)
            except Exception:
                pass

    return {"status": "ok", "results": results}


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
        raise HTTPException(status_code=500, detail="daily_digest_failed")
    return {"status": "ok"}
