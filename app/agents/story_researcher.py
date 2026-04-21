# app/agents/story_researcher.py
#
# Fully-automated Hindi moral story pipeline — no human digest approval step.
# Cloud Scheduler → /stories/run → this module → Cloud Task → /generate/stories-task

import re
import random
import hashlib
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services import firestore_service
from app.services.llm_service import generate_story_idea
from app.services.telegram_service import send_message
from app.config import STORIES_CHAT_ID, CLOUD_RUN_URL, PROJECT_ID, LOCATION, TASKS_QUEUE

logger = logging.getLogger(__name__)

_STORY_DEDUP_DAYS = 30
_STORY_GENRES = [
    "inspiring",
    "comedy",
    "heartfelt",
    "crime",
    "action",
    "sci-fi",
    "mythology",
    "thriller",
    "mystery",
    "adventure",
    "slice-of-life",
    "historical",
]


def _story_key(title: str) -> str:
    norm = " ".join((title or "").strip().lower().split())
    return "stories_" + hashlib.sha1(norm.encode("utf-8")).hexdigest()


def _is_story_already_used(title: str) -> bool:
    return firestore_service.is_headline_already_suggested(
        title, ttl_days=_STORY_DEDUP_DAYS, channel_id="stories"
    )


def _mark_story_used(title: str, mood: str = ""):
    firestore_service.mark_headline_suggested(title, genre=mood, channel_id="stories")


def _recently_used_titles(limit: int = 20) -> list[str]:
    """Return recent story titles from suggested_headlines to pass to the LLM to avoid repeats."""
    try:
        return firestore_service.get_recently_suggested_headlines(
            days=_STORY_DEDUP_DAYS, limit=limit, channel_id="stories"
        )
    except Exception:
        return []


def _select_story_genre() -> str:
    """Select genre using performance-weighted randomization with deterministic fallback.

    When 14-day view data exists for this channel, genres that performed better get
    proportionally higher selection probability. Genres with no data receive the median
    score so new genres still get a fair share of slots. Falls back to deterministic
    slot rotation when no performance data is available (e.g. early in channel life).
    """
    from app.services.firestore_service import get_genre_performance_fortnightly

    try:
        perf = get_genre_performance_fortnightly(channel_id="stories")
    except Exception:
        perf = {}

    if perf:
        scores = [perf.get(g, 0.0) for g in _STORY_GENRES]
        known = sorted(s for s in scores if s > 0)
        baseline = known[len(known) // 2] if known else 100.0
        weights = [s if s > 0 else baseline for s in scores]
        return random.choices(_STORY_GENRES, weights=weights, k=1)[0]

    # Fallback: deterministic IST schedule-slot rotation
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    slot_hours = [7, 11, 14, 18]
    slot_index = None
    for idx, hour in enumerate(slot_hours):
        if now_ist.hour > hour or (now_ist.hour == hour and now_ist.minute >= 0):
            slot_index = idx
    if slot_index is None:
        day_ordinal = now_ist.date().toordinal() - 1
        slot_index = len(slot_hours) - 1
    else:
        day_ordinal = now_ist.date().toordinal()
    schedule_slot = (day_ordinal * len(slot_hours)) + slot_index
    return _STORY_GENRES[schedule_slot % len(_STORY_GENRES)]


def run() -> str | None:
    """
    Main entry point called by POST /stories/run (Cloud Scheduler).
    1. Check pipeline state — skip if already processing.
    2. Generate a fresh Hindi story idea via LLM.
    3. Deduplicate against recent 30-day window.
    4. Enqueue a Cloud Task to generate the full video.
    5. Notify the stories Telegram channel.
    Returns the public_id string if enqueued, None otherwise.
    """
    from google.cloud import tasks_v2
    from google.api_core.exceptions import AlreadyExists

    state = firestore_service.get_pipeline_state(channel_id="stories")
    if state.get("state") == "processing":
        logger.info("Stories pipeline busy — skipping this run")
        send_message(
            STORIES_CHAT_ID,
            f"⏭️ Stories scheduler slot skipped — pipeline is busy processing batch "
            f"`{state.get('active_batch_id', '?')}`.",
            channel_id="stories",
        )
        return None

    # Generate a new story idea (title + mood + premise, all in Hindi)
    recently_used = _recently_used_titles()
    target_genre = _select_story_genre()
    try:
        idea = generate_story_idea(
            recently_used_titles=recently_used,
            preferred_mood=target_genre,
        )
    except Exception as e:
        logger.exception(f"Story idea generation failed: {e}")
        if STORIES_CHAT_ID:
            send_message(STORIES_CHAT_ID, f"⚠️ Story idea generation failed: {e}", channel_id="stories")
        return None

    title = (idea.get("title") or "").strip()
    # Enforce scheduled genre rotation to diversify experiments across scheduler slots.
    mood = target_genre
    premise = (idea.get("premise") or "").strip()

    if not title:
        logger.warning("Story idea returned empty title — skipping")
        send_message(
            STORIES_CHAT_ID,
            f"⚠️ Story slot skipped — LLM returned an empty title for genre *{target_genre}*. Will retry next slot.",
            channel_id="stories",
        )
        return None

    if _is_story_already_used(title):
        logger.info(f"Story already used recently: {title}")
        send_message(
            STORIES_CHAT_ID,
            f"⏭️ Story slot skipped — recently used title detected: _{title}_. A new idea will be generated next slot.",
            channel_id="stories",
        )
        return None

    # Build a deterministic batch + task name
    batch_id = f"stories_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    code = "STORY01"
    raw_task = f"generate-{batch_id}-{code}"
    task_name = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_task)
    public_id = hashlib.sha1(task_name.encode("utf-8")).hexdigest()[:8].upper()
    job_id = task_name

    # Save batch + pipeline state
    firestore_service.save_news_batch(batch_id, mood, {
        code: {
            "code": code,
            "headline": title,
            "context": premise,
            "rating": 5.0,
            "genre": mood,
        }
    })
    firestore_service.set_pipeline_and_batch_state(batch_id, "processing", channel_id="stories")

    # Create job document immediately so STOP/REDO commands work
    firestore_service.create_or_update_job(job_id, {
        "job_id": job_id,
        "batch_id": batch_id,
        "code": code,
        "topic": title,
        "source": "scheduler",
        "status": "queued",
        "public_id": public_id,
        "genre": mood,
        "details": premise,
        "channel_id": "stories",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Enqueue Cloud Task
    import json
    client = tasks_v2.CloudTasksClient()
    queue_path = client.queue_path(PROJECT_ID, LOCATION, TASKS_QUEUE)
    payload = json.dumps({
        "headline": title,
        "code": code,
        "batch_id": batch_id,
        "job_id": job_id,
        "public_id": public_id,
        "force_run": True,
        "genre": mood,
        "details": premise,
        "virality_score": 0.0,
        "channel_id": "stories",
        "script_type": "story",
    }).encode()

    try:
        client.create_task(request={
            "parent": queue_path,
            "task": {
                "name": f"{queue_path}/tasks/{task_name}",
                "dispatch_deadline": {"seconds": 1800},  # 30 min — prevents retry before task completes
                "http_request": {
                    "http_method": tasks_v2.HttpMethod.POST,
                    "url": f"{CLOUD_RUN_URL}/generate/stories-task",
                    "headers": {"Content-Type": "application/json"},
                    "body": payload,
                    "oidc_token": {
                        "service_account_email": "353645494126-compute@developer.gserviceaccount.com",
                    },
                },
            },
        })
    except AlreadyExists:
        logger.warning(f"Stories task {task_name} already exists — skipping duplicate")
        firestore_service.set_pipeline_and_batch_state(batch_id, "skipped", channel_id="stories")
        return None
    except Exception as e:
        logger.exception(f"Failed to enqueue stories task: {e}")
        firestore_service.set_pipeline_and_batch_state(batch_id, "failed", channel_id="stories")
        if STORIES_CHAT_ID:
            send_message(STORIES_CHAT_ID, f"❌ Failed to queue story: {e}", channel_id="stories")
        return None

    _mark_story_used(title, mood=mood)

    if STORIES_CHAT_ID:
        send_message(
            STORIES_CHAT_ID,
            f"📖 Generating story...\n"
            f"Title: *{title}*\n"
            f"Genre: {mood.title()}\n"
            f"Id: `{public_id}`",
            channel_id="stories",
        )

    logger.info(f"Stories task enqueued: {task_name} | {title} | genre={mood}")
    return public_id
