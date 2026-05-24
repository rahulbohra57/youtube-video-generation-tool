# app/routes/stories.py
#
# Cloud Scheduler endpoints for the Tell Me Why stories channel.
# /stories/run        → Triggered at 7am, 11am, 2pm, 6pm IST — generates a new Hindi story
# /stories/daily-digest → Triggered at 8:30am IST — sends stats to stories Telegram
# /generate/stories-task → Cloud Tasks delivery endpoint for story video generation

import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from app.config import SCHEDULER_SECRET, STORIES_CHAT_ID
from app.services import firestore_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/stories/run")
def stories_run(request: Request):
    """Called by Cloud Scheduler at 7am, 11am, 2pm, 6pm IST. Generates a new Hindi moral story."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        from app.agents import story_researcher
        public_id = story_researcher.run()
    except Exception as e:
        logger.exception(f"story_researcher.run() failed: {e}")
        raise HTTPException(status_code=500, detail="story_researcher_failed")
    if not public_id:
        return {"status": "skipped", "reason": "pipeline_busy_or_story_already_used"}
    return {"status": "ok", "public_id": public_id}


@router.post("/stories/daily-digest")
def stories_daily_digest(request: Request):
    """Called by Cloud Scheduler at 8:30am IST. Sends Tell Me Why stats to stories Telegram."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not STORIES_CHAT_ID:
        return {"status": "skipped", "reason": "STORIES_CHAT_ID not configured"}
    try:
        _send_stories_daily_digest()
    except Exception as e:
        logger.exception(f"stories daily digest failed: {e}")
        raise HTTPException(status_code=500, detail="stories_daily_digest_failed")
    return {"status": "ok"}


def _send_stories_daily_digest():
    from app.services import youtube_service, telegram_service
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)

    try:
        yt = youtube_service.get_channel_stats(channel_id="stories")
        firestore_service.save_social_metrics("youtube_stories", yt)
    except Exception:
        yt = firestore_service.get_social_metrics("youtube_stories") or {}

    # Previous 24h window
    current_window_start = firestore_service._ist_window_start()
    prev_window_start = current_window_start - timedelta(hours=24)

    queue = firestore_service.get_queue_snapshot(window_start=prev_window_start, channel_id="stories")
    tts_chars_today = firestore_service.get_tts_chars_today(window_start=prev_window_start, channel_id="stories")
    tts_chars_month = firestore_service.get_tts_chars_this_month(channel_id="stories")
    tts_pct = round((tts_chars_month / 1_000_000) * 100, 1)

    message = (
        f"📅 Tell Me Why Daily Report — {now_ist.strftime('%d %b %Y, %I:%M %p IST')}\n\n"
        f"📺 Channel\n"
        f"  Subscribers: {int(yt.get('subscriber_count', 0)):,}\n"
        f"  Total Views: {int(yt.get('view_count', 0)):,}\n"
        f"  Videos: {int(yt.get('video_count', 0))}\n\n"
        f"⚙️ Pipeline (24h)\n"
        f"  Completed: {queue.get('completed_24h', 0)}\n"
        f"  Failed: {queue.get('failed_24h', 0)}\n\n"
        f"📊 TTS Usage Today\n"
        f"  {tts_chars_today:,} today | {tts_chars_month:,} this month ({tts_pct}% of 1M free tier)"
    )
    telegram_service.send_message(STORIES_CHAT_ID, message, channel_id="stories")


@router.post("/generate/stories-task")
def generate_stories_task(payload: dict):
    """Called by Cloud Tasks to run the full Hindi story video pipeline.

    Returns 200 in all cases except invalid payload — even on pipeline errors.
    This prevents Cloud Tasks from auto-retrying and creating duplicate videos.
    """
    headline = payload.get("headline", "")
    code = payload.get("code", "")
    batch_id = payload.get("batch_id")
    job_id = payload.get("job_id", f"stories-task-{uuid4().hex}")
    public_id = payload.get("public_id")
    force_run = bool(payload.get("force_run", True))
    genre = payload.get("genre", "inspiring")
    details = payload.get("details", "")
    virality_score = float(payload.get("virality_score", 0) or 0)
    language = payload.get("language", "hi")

    if not headline or not code:
        raise HTTPException(status_code=400, detail="headline and code required")

    try:
        from app.agents import generator_agent
        generator_agent.run(
            headline,
            code,
            batch_id=batch_id,
            job_id=job_id,
            public_id=public_id,
            force_run=force_run,
            genre=genre,
            details=details,
            virality_score=virality_score,
            channel_id="stories",
            script_type="story",
            language=language,
        )
    except Exception as e:
        firestore_service.create_or_update_job(
            job_id,
            {
                "status": "failed",
                "error_type": "task_exception",
                "error_message": str(e)[:500],
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if batch_id:
            try:
                current = firestore_service.get_pipeline_state(channel_id="stories") or {}
                if current.get("active_batch_id") == batch_id:
                    firestore_service.set_pipeline_and_batch_state(batch_id, "failed", channel_id="stories")
            except Exception:
                pass
        logger.exception(f"generate_stories_task failed for code={code}: {e}")
    return {"status": "ok"}
