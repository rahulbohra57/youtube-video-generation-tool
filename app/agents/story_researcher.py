# app/agents/story_researcher.py
#
# Tell Me Why facts channel — posts 4 English-language facts videos per day.
# GitHub Actions cron (2am, 8am, 2pm, 8pm IST) → scripts/run_stories.py → this module
# → dispatch generate-video.yml (script_type="facts", language="en")

import re
import random
import hashlib
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services import firestore_service
from app.services.llm_service import generate_fact_topic
from app.services.telegram_service import send_message
from app.config import STORIES_CHAT_ID

logger = logging.getLogger(__name__)

_FACT_DEDUP_DAYS = 30

_FACT_CATEGORIES = [
    "science & space",
    "history & civilizations",
    "human body & biology",
    "technology & ai",
    "health & fitness",
    "psychology & dark psychology",
    "relationships & dating",
    "self-improvement & habits",
    "business & finance",
    "culture & society",
    "philosophy & life",
    "mysteries & unexplained",
]

# Slot hours matching stories-run.yml cron: 2am, 8am, 2pm, 8pm IST
_SLOT_HOURS = [2, 8, 14, 20]


def _is_topic_already_used(title: str) -> bool:
    return firestore_service.is_headline_already_suggested(
        title, ttl_days=_FACT_DEDUP_DAYS, channel_id="stories"
    )


def _mark_topic_used(title: str, category: str = ""):
    firestore_service.mark_headline_suggested(title, genre=category, channel_id="stories")


def _recently_used_titles(limit: int = 20) -> list[str]:
    try:
        return firestore_service.get_recently_suggested_headlines(
            days=_FACT_DEDUP_DAYS, limit=limit, channel_id="stories"
        )
    except Exception:
        return []


def _select_category() -> str:
    """Select fact category using performance-weighted randomization with deterministic fallback."""
    from app.services.firestore_service import get_genre_performance_fortnightly

    try:
        perf = get_genre_performance_fortnightly(channel_id="stories")
    except Exception:
        perf = {}

    if perf:
        scores = [perf.get(g, 0.0) for g in _FACT_CATEGORIES]
        known = sorted(s for s in scores if s > 0)
        baseline = known[len(known) // 2] if known else 100.0
        weights = [s if s > 0 else baseline for s in scores]
        return random.choices(_FACT_CATEGORIES, weights=weights, k=1)[0]

    # Deterministic IST schedule-slot rotation
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    slot_index = None
    for idx, hour in enumerate(_SLOT_HOURS):
        if now_ist.hour > hour or (now_ist.hour == hour and now_ist.minute >= 0):
            slot_index = idx
    if slot_index is None:
        slot_index = len(_SLOT_HOURS) - 1
        day_ordinal = now_ist.date().toordinal() - 1
    else:
        day_ordinal = now_ist.date().toordinal()
    schedule_slot = (day_ordinal * len(_SLOT_HOURS)) + slot_index
    return _FACT_CATEGORIES[schedule_slot % len(_FACT_CATEGORIES)]


def run() -> str | None:
    """
    Main entry point called by scripts/run_stories.py or GitHub Actions scheduled workflow.
    1. Check pipeline state — skip if already processing.
    2. Generate a fresh English fact topic via LLM.
    3. Deduplicate against recent 30-day window.
    4. Dispatch GitHub Actions workflow to generate the full video.
    5. Notify the Tell Me Why Telegram channel.
    Returns the public_id string if enqueued, None otherwise.
    """

    state = firestore_service.get_pipeline_state(channel_id="stories")
    if state.get("state") == "processing":
        logger.info("Tell Me Why pipeline busy — skipping this run")
        send_message(
            STORIES_CHAT_ID,
            f"⏭️ Tell Me Why scheduler slot skipped — pipeline is busy processing batch "
            f"`{state.get('active_batch_id', '?')}`.",
            channel_id="stories",
        )
        return None

    language = "en"
    recently_used = _recently_used_titles()
    target_category = _select_category()

    try:
        idea = generate_fact_topic(
            category=target_category,
            recently_used_titles=recently_used,
        )
    except Exception as e:
        logger.exception(f"Fact topic generation failed: {e}")
        if STORIES_CHAT_ID:
            send_message(STORIES_CHAT_ID, f"⚠️ Fact topic generation failed: {e}", channel_id="stories")
        return None

    title = (idea.get("title") or "").strip()
    premise = (idea.get("premise") or "").strip()

    if not title:
        logger.warning("Fact topic returned empty title — skipping")
        send_message(
            STORIES_CHAT_ID,
            f"⚠️ Fact slot skipped — LLM returned an empty title for category *{target_category}*. Will retry next slot.",
            channel_id="stories",
        )
        return None

    if _is_topic_already_used(title):
        logger.info(f"Fact topic already used recently: {title}")
        send_message(
            STORIES_CHAT_ID,
            f"⏭️ Fact slot skipped — recently used title detected: _{title}_. A new topic will be generated next slot.",
            channel_id="stories",
        )
        return None

    batch_id = f"stories_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    code = "FACT01"
    raw_task = f"generate-{batch_id}-{code}"
    task_name = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_task)
    public_id = hashlib.sha1(task_name.encode("utf-8")).hexdigest()[:8].upper()
    job_id = task_name

    firestore_service.save_news_batch(batch_id, target_category, {
        code: {
            "code": code,
            "headline": title,
            "context": premise,
            "rating": 5.0,
            "genre": target_category,
        }
    })
    firestore_service.set_pipeline_and_batch_state(batch_id, "processing", channel_id="stories")

    firestore_service.create_or_update_job(job_id, {
        "job_id": job_id,
        "batch_id": batch_id,
        "code": code,
        "topic": title,
        "source": "scheduler",
        "status": "queued",
        "public_id": public_id,
        "genre": target_category,
        "details": premise,
        "channel_id": "stories",
        "language": language,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    from app.agents.github_dispatch import dispatch_video_generation
    try:
        dispatch_video_generation({
            "headline": title,
            "code": code,
            "batch_id": batch_id,
            "job_id": job_id,
            "public_id": public_id,
            "force_run": True,
            "genre": target_category,
            "details": premise,
            "virality_score": 0.0,
            "channel_id": "stories",
            "script_type": "facts",
            "language": language,
        })
    except Exception as e:
        logger.exception(f"Failed to dispatch fact generation workflow: {e}")
        firestore_service.set_pipeline_and_batch_state(batch_id, "failed", channel_id="stories")
        if STORIES_CHAT_ID:
            send_message(STORIES_CHAT_ID, f"❌ Failed to queue fact video: {e}", channel_id="stories")
        return None

    _mark_topic_used(title, category=target_category)

    if STORIES_CHAT_ID:
        send_message(
            STORIES_CHAT_ID,
            f"💡 Generating facts video...\n"
            f"Topic: *{title}*\n"
            f"Category: {target_category.title()}\n"
            f"Id: `{public_id}`",
            channel_id="stories",
        )

    logger.info(f"Facts task enqueued: {task_name} | {title} | category={target_category}")
    return public_id
