#!/usr/bin/env python3
# scripts/run_long_stories.py
#
# Scheduled at 2pm IST daily by stories-long-run.yml.
# Picks a Tell Me Why topic and dispatches generate-long-video.yml.

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import hashlib
import logging
from datetime import datetime, timezone, timedelta

from app.services import firestore_service
from app.services.llm_service import generate_fact_topic
from app.services.telegram_service import send_message
from app.agents.story_researcher import _select_category, _recently_used_titles
from app.agents.github_dispatch import dispatch_long_video_generation
from app.config import STORIES_CHAT_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_LONG_CHANNEL = "stories_long"
_DEDUP_DAYS = 365


def run() -> str | None:
    """Pick a fact topic and dispatch the long-format video generation workflow.
    Returns public_id if dispatched, None if skipped.
    """
    state = firestore_service.get_pipeline_state(channel_id=_LONG_CHANNEL)
    if state.get("state") == "processing":
        last_run_str = state.get("last_run_at", "")
        is_stale = False
        if last_run_str:
            try:
                last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                is_stale = (datetime.now(timezone.utc) - last_run) > timedelta(hours=4)
            except Exception:
                pass
        if not is_stale:
            logger.info("Long-format pipeline busy — skipping this run")
            send_message(
                STORIES_CHAT_ID,
                "⏭️ Long video slot skipped — pipeline is busy.",
                channel_id="stories",
            )
            return None
        stale_batch_id = state.get("active_batch_id", "?")
        logger.warning("Clearing stale long pipeline (batch %s)", stale_batch_id)
        try:
            firestore_service.set_pipeline_and_batch_state(stale_batch_id, "failed", channel_id=_LONG_CHANNEL)
        except Exception as err:
            logger.warning("Could not clear stale long pipeline: %s", err)

    recently_used = _recently_used_titles(limit=60)
    category = _select_category()

    try:
        idea = generate_fact_topic(category=category, recently_used_titles=recently_used)
    except Exception as exc:
        logger.exception("Fact topic generation failed: %s", exc)
        send_message(STORIES_CHAT_ID, f"⚠️ Long video topic generation failed: {exc}", channel_id="stories")
        return None

    title = (idea.get("title") or "").strip()
    premise = (idea.get("premise") or "").strip()

    if not title:
        logger.warning("Empty topic title — skipping")
        return None

    if firestore_service.is_headline_already_suggested(title, ttl_days=_DEDUP_DAYS, channel_id=_LONG_CHANNEL):
        logger.info("Topic already used recently: %s", title)
        send_message(STORIES_CHAT_ID, f"⏭️ Long video slot skipped — topic already used: _{title}_.", channel_id="stories")
        return None

    batch_id = f"long_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    code = "LONG01"
    raw_task = f"generate-{batch_id}-{code}"
    task_name = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_task)
    public_id = hashlib.sha1(task_name.encode("utf-8")).hexdigest()[:8].upper()
    job_id = task_name

    firestore_service.save_news_batch(batch_id, category, {
        code: {
            "code": code,
            "headline": title,
            "context": premise,
            "rating": 5.0,
            "genre": category,
        }
    })
    firestore_service.set_pipeline_and_batch_state(batch_id, "processing", channel_id=_LONG_CHANNEL)
    firestore_service.create_or_update_job(job_id, {
        "job_id": job_id,
        "batch_id": batch_id,
        "code": code,
        "topic": title,
        "source": "scheduler",
        "status": "queued",
        "public_id": public_id,
        "genre": category,
        "details": premise,
        "channel_id": _LONG_CHANNEL,
        "video_type": "long",
        "language": "en",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    try:
        dispatch_long_video_generation({
            "headline": title,
            "code": code,
            "batch_id": batch_id,
            "job_id": job_id,
            "public_id": public_id,
            "force_run": True,
            "genre": category,
            "details": premise,
            "channel_id": _LONG_CHANNEL,
            "script_type": "long_facts",
            "language": "en",
        })
    except Exception as exc:
        logger.exception("Failed to dispatch long video: %s", exc)
        firestore_service.set_pipeline_and_batch_state(batch_id, "failed", channel_id=_LONG_CHANNEL)
        send_message(STORIES_CHAT_ID, f"❌ Failed to queue long video: {exc}", channel_id="stories")
        return None

    firestore_service.mark_headline_suggested(title, genre=category, channel_id=_LONG_CHANNEL)

    send_message(
        STORIES_CHAT_ID,
        f"🎬 Generating long video...\nTopic: *{title}*\nCategory: {category.title()}\nId: `{public_id}`",
        channel_id="stories",
    )
    logger.info("Long video dispatched: %s | %s | category=%s", task_name, title, category)
    return public_id


if __name__ == "__main__":
    run()
