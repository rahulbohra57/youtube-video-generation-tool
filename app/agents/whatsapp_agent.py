# app/agents/whatsapp_agent.py

import json
import logging
import os
import re
import hashlib
from datetime import datetime, timezone
from google.cloud import tasks_v2
from google.api_core.exceptions import AlreadyExists
from app.services import firestore_service, telegram_service, youtube_service
from app.config import (
    TELEGRAM_CHAT_ID,
    PROJECT_ID,
    LOCATION,
    CLOUD_RUN_URL,
    TASKS_QUEUE,
    CREATE_TOPIC_IDEMPOTENCY_TTL_SECONDS,
)

logger = logging.getLogger(__name__)


def _download_from_gcs(url: str) -> str:
    """Download a GCS video URL to a local temp file. Returns the local path."""
    import httpx
    import tempfile
    resp = httpx.get(url, timeout=180, follow_redirects=True)
    resp.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.write(resp.content)
    tmp.close()
    return tmp.name


def _normalize_topic(topic: str) -> str:
    return " ".join((topic or "").strip().lower().split())


def _topic_key(topic: str) -> str:
    return hashlib.sha1(_normalize_topic(topic).encode("utf-8")).hexdigest()


def _task_name(batch_id: str, code: str) -> str:
    raw_name = f"generate-{batch_id}-{code}"
    return re.sub(r"[^a-zA-Z0-9_-]", "-", raw_name)


def _public_video_id(task_name: str) -> str:
    return hashlib.sha1(task_name.encode("utf-8")).hexdigest()[:8].upper()


def _command_arg(prefix: str, text: str) -> str:
    m = re.match(rf"^{prefix}\s*(.+)$", (text or "").strip(), flags=re.IGNORECASE)
    return (m.group(1).strip() if m else "")


def _resolve_job(identifier: str) -> tuple[str | None, dict | None]:
    ident = (identifier or "").strip()
    if not ident:
        return None, None
    job_id, by_public = firestore_service.get_job_by_public_id(ident)
    if job_id and by_public:
        return job_id, by_public
    by_job = firestore_service.get_job(ident)
    if by_job:
        return ident, by_job
    return None, None


def _refresh_pipeline_after_stop(job: dict):
    batch_id = (job or {}).get("batch_id")
    if not batch_id:
        return
    state = firestore_service.get_pipeline_state() or {}
    if state.get("active_batch_id") == batch_id and state.get("state") == "processing":
        firestore_service.set_pipeline_and_batch_state(batch_id, "failed")
    # Release the video lock immediately so the next scheduled run is not
    # blocked waiting for the cancelled generator to exit naturally.
    firestore_service.force_release_video_lock()


def _delete_queued_task(task_name: str):
    if not task_name:
        return
    client = tasks_v2.CloudTasksClient()
    queue_path = client.queue_path(PROJECT_ID, LOCATION, TASKS_QUEUE)
    full_name = f"{queue_path}/tasks/{task_name}"
    try:
        client.delete_task(request={"name": full_name})
    except Exception:
        return


def _send_stats(chat_id: str):
    queue = firestore_service.get_queue_snapshot()
    jobs = firestore_service.list_recent_jobs(limit=200)
    ongoing = sum(1 for j in jobs if j.get("status") == "processing")
    local_posted = sum(1 for j in jobs if j.get("status") == "completed" and j.get("youtube_url"))

    yt_error = ""
    try:
        yt = youtube_service.get_channel_stats()
        firestore_service.save_social_metrics("youtube", yt)
    except Exception as e:
        yt_error = str(e)
        yt = firestore_service.get_social_metrics("youtube") or {}

    videos_posted = int(yt.get("video_count", 0)) or local_posted
    message = (
        "📊 Pipeline Stats\n"
        f"Videos posted: {videos_posted}\n"
        f"Subscribers: {int(yt.get('subscriber_count', 0))}\n"
        f"Views: {int(yt.get('view_count', 0))}\n"
        f"Failed tasks (24h): {queue.get('failed_24h', 0)}\n"
        f"Completed tasks (24h): {queue.get('completed_24h', 0)}\n"
        f"Ongoing tasks: {ongoing}\n"
        f"Queued: {queue.get('queued', 0)}"
    )
    if yt_error:
        low = yt_error.lower()
        if ("insufficient" in low and "scope" in low) or ("invalid_scope" in low):
            message += "\n\n⚠️ YouTube stats auth token is outdated. Reconnect once: /auth/youtube"
        else:
            message += "\n\n⚠️ YouTube stats fetch failed; showing last cached values."
    telegram_service.send_message(chat_id, message)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _is_digest_expired(batch: dict | None) -> bool:
    if not isinstance(batch, dict):
        return False
    created_at = _parse_iso(batch.get("created_at"))
    if not created_at:
        return False
    expiry_hours = float(os.getenv("DIGEST_EXPIRY_HOURS", "2"))
    age_seconds = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
    return age_seconds >= expiry_hours * 3600


def send_digest(batch_id: str):
    batch = firestore_service.get_news_batch(batch_id)
    sections = []
    for code, item in batch["items"].items():
        genre = item.get("genre", "Technology")
        sections.append(
            f"*Unique Number:* {code}\n"
            f"*Genre:* {genre}\n"
            f"*Headline:* {item['headline']}\n"
            f"*Context:* {item['context']}\n"
            f"*Rating:* {item['rating']}/5"
        )
    footer = (
        "Reply with a code (e.g. TECH01 or AI01) to generate a video, "
        "or send *CREATE <topic>* for a custom topic, or reply *None* to skip."
    )
    message = "\n---\n".join(sections) + "\n\n" + footer
    telegram_service.send_message(TELEGRAM_CHAT_ID, message)


def handle_reply(chat_id: str, body: str):
    raw_text = body.strip()
    text = raw_text.upper()

    if text == "STATS":
        _send_stats(chat_id)
        return

    if text == "COMMANDS":
        telegram_service.send_message(chat_id, (
            "🤖 *AutoframeBot Commands*\n\n"
            "*STATS*\n"
            "  Channel stats, subscriber count, pipeline queue summary.\n\n"
            "*CREATE <topic>*\n"
            "  Generate a video for a custom topic. Tries YouTube upload first; sends to Telegram for manual posting on failure.\n\n"
            "*CREATE <topic> | <context>*\n"
            "  Same as CREATE with extra context to guide the script.\n\n"
            "*FORCE\\_CREATE <topic>*\n"
            "  Like CREATE but bypasses the pipeline busy check and dedup. Use when CREATE is blocked.\n\n"
            "*REDO <id>*\n"
            "  Re-upload an existing video to YouTube from GCS. Falls back to Telegram delivery on failure. If no video file exists, regenerates from scratch.\n\n"
            "*RESEND <id>*\n"
            "  Send an existing video + caption to Telegram for manual YouTube posting. Never uses the YouTube API.\n\n"
            "*STOP <id>*\n"
            "  Cancel a queued or in-progress video generation job.\n\n"
            "*PRIVATE <id>*\n"
            "  Set a published YouTube video to private.\n\n"
            "*DELETE <id>*\n"
            "  Permanently delete a video from YouTube.\n\n"
            "*COMMANDS*\n"
            "  Show this list.\n\n"
            "_<id> is the 8-character video ID shown in each notification (e.g. 2E95C55E)._"
        ))
        return

    stop_id = _command_arg("STOP", raw_text)
    if stop_id:
        job_id, job = _resolve_job(stop_id)
        if not job_id or not job:
            telegram_service.send_message(chat_id, f"No job found for ID `{stop_id}`.")
            return
        status = (job.get("status") or "").lower()
        if status in ("completed", "failed", "cancelled"):
            telegram_service.send_message(chat_id, f"Job `{stop_id}` is already `{status}`.")
            return
        firestore_service.request_job_cancel(job_id, requested_by="telegram_stop")
        if status == "queued":
            _delete_queued_task(job_id)
            firestore_service.create_or_update_job(
                job_id,
                {"status": "cancelled", "finished_at": datetime.now(timezone.utc).isoformat()},
            )
        _refresh_pipeline_after_stop(job)
        telegram_service.send_message(
            chat_id,
            f"🛑 Stop requested for `{stop_id}`.",
        )
        return

    private_id = _command_arg("PRIVATE", raw_text)
    if private_id:
        resolved_job_id, job = _resolve_job(private_id)
        if not resolved_job_id or not job:
            telegram_service.send_message(chat_id, f"No job found for ID `{private_id}`.")
            return
        video_url = job.get("youtube_url", "")
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            telegram_service.send_message(chat_id, f"Video URL not found for `{private_id}`.")
            return
        try:
            youtube_service.set_video_privacy(video_id, privacy_status="private")
            firestore_service.create_or_update_job(
                resolved_job_id,
                {"youtube_privacy": "private", "updated_at": datetime.now(timezone.utc).isoformat()},
            )
            telegram_service.send_message(chat_id, f"🔒 Video `{private_id}` is now private.")
        except Exception as e:
            telegram_service.send_message(chat_id, f"❌ Failed to set private for `{private_id}`: {e}")
        return

    delete_id = _command_arg("DELETE", raw_text)
    if delete_id:
        resolved_job_id, job = _resolve_job(delete_id)
        if not resolved_job_id or not job:
            telegram_service.send_message(chat_id, f"No job found for ID `{delete_id}`.")
            return
        video_url = job.get("youtube_url", "")
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            telegram_service.send_message(chat_id, f"Video URL not found for `{delete_id}`.")
            return
        try:
            youtube_service.delete_video(video_id)
            firestore_service.create_or_update_job(
                resolved_job_id,
                {
                    "youtube_deleted": True,
                    "youtube_url": "",
                    "status": "deleted",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            telegram_service.send_message(chat_id, f"🗑️ Video `{delete_id}` deleted from YouTube.")
        except Exception as e:
            telegram_service.send_message(chat_id, f"❌ Failed to delete `{delete_id}`: {e}")
        return

    # RESEND <id> — send existing video + caption to Telegram (no YouTube API)
    resend_id = _command_arg("RESEND", raw_text)
    if resend_id:
        job_id, job = _resolve_job(resend_id)
        if not job_id or not job:
            telegram_service.send_message(chat_id, f"❌ No job found for ID `{resend_id}`.")
            return
        gcs_url = job.get("gcs_video_url", "")
        title = job.get("topic", "")
        caption = job.get("final_caption", "")
        if gcs_url and caption:
            telegram_service.send_video_for_manual_post(
                chat_id, gcs_url, title, caption, source_label="resend"
            )
            telegram_service.send_message(chat_id, f"📤 Video sent for manual posting: `{resend_id}`")
        else:
            telegram_service.send_message(
                chat_id,
                f"❌ No video file on record for `{resend_id}` "
                f"(status: {job.get('status', 'unknown')}). Use CREATE to re-generate."
            )
        return

    # REDO <id> — try YouTube re-upload from GCS, fall back to Telegram delivery
    redo_id = _command_arg("REDO", raw_text)
    if redo_id:
        job_id, job = _resolve_job(redo_id)
        if not job_id or not job:
            telegram_service.send_message(chat_id, f"No job found for ID `{redo_id}`.")
            return
        title = job.get("topic") or job.get("headline", "")
        if not title:
            telegram_service.send_message(chat_id, f"Cannot REDO `{redo_id}` — no topic found.")
            return
        genre = job.get("genre", "")
        caption = job.get("final_caption", "")
        gcs_url = job.get("gcs_video_url", "")

        if gcs_url and caption:
            # Attempt YouTube re-upload from GCS
            telegram_service.send_message(chat_id, f"🔁 Attempting YouTube re-upload for `{redo_id}`...")
            try:
                local_path = _download_from_gcs(gcs_url)
                url = youtube_service.upload_video(local_path, title, caption, genre=genre)
                firestore_service.create_or_update_job(job_id, {"status": "completed", "youtube_url": url})
                telegram_service.send_message(chat_id, f"✅ REDO uploaded to YouTube!\n{url}")
                try:
                    os.remove(local_path)
                except Exception:
                    pass
            except Exception as e:
                if "youtube_quota_exceeded" in str(e):
                    telegram_service.send_message(chat_id, "⚠️ YouTube quota exceeded — sending for manual post.")
                else:
                    telegram_service.send_message(chat_id, f"❌ YouTube re-upload failed: {str(e)[:200]} — sending for manual post.")
                telegram_service.send_video_for_manual_post(
                    chat_id, gcs_url, title, caption, source_label="redo"
                )
        else:
            # No existing video — re-generate from scratch
            details = job.get("details", "")
            redo_batch_id = f"redo_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
            code = "DIRECT01"
            firestore_service.save_news_batch(redo_batch_id, genre or "direct", {
                code: {"code": code, "headline": title, "context": details, "rating": 5.0, "genre": genre}
            })
            firestore_service.set_pipeline_and_batch_state(redo_batch_id, "processing")
            task_name = _task_name(redo_batch_id, code)
            public_id = _public_video_id(task_name)
            enqueued = _enqueue_generate(
                title, code, redo_batch_id,
                public_id=public_id, force_run=True, genre=genre, details=details,
            )
            if enqueued:
                telegram_service.send_message(
                    chat_id,
                    f"🔄 No existing video found — regenerating *{title}*\nNew ID: `{public_id}`",
                )
            else:
                telegram_service.send_message(chat_id, "❌ Failed to re-queue. Try FORCE_CREATE instead.")
        return

    is_force_create = text.startswith("FORCE_CREATE")
    is_create = text.startswith("CREATE")

    if is_force_create or is_create:
        cmd_len = len("FORCE_CREATE") if is_force_create else len("CREATE")
        rest = raw_text[cmd_len:].strip()
        if not rest:
            usage = "FORCE_CREATE <topic>" if is_force_create else "CREATE <topic> or CREATE <topic> | <context>"
            telegram_service.send_message(chat_id, f"Please send: {usage}.")
            return
        if "|" in rest:
            topic, create_context = rest.split("|", 1)
            topic = topic.strip()
            create_context = create_context.strip()
        else:
            topic = rest
            create_context = ""

        # Enrich CREATE with recent news so Gemini has dated facts to work from.
        # Only search when the user hasn't already provided context via "topic | context".
        # Strategy: Google Custom Search first (100/day free, on GCP billing); fall back to GNews.
        if not create_context:
            try:
                from app.services import google_search_service
                articles = google_search_service.search_news(query=topic, max_results=5, date_restrict="w1")
            except Exception:
                articles = []

            if not articles:
                # GNews fallback
                try:
                    from app.services import gnews_service
                    from datetime import timedelta
                    from_date = (
                        datetime.now(timezone.utc) - timedelta(hours=72)
                    ).isoformat(timespec="seconds").replace("+00:00", "Z")
                    articles = gnews_service.search_news(
                        query=topic, max_results=5, from_date=from_date
                    )
                except Exception:
                    articles = []

            if articles:
                lines = []
                for a in articles[:3]:
                    pub = a.get("published_at", "")
                    headline = a.get("headline", "")
                    desc = a.get("description", "")
                    entry = f"- [{pub}] {headline}"
                    if desc:
                        entry += f": {desc}"
                    lines.append(entry)
                create_context = (
                    "NEWS CONTEXT (authoritative — script must cover these facts, do not use older training data):\n"
                    + "\n".join(lines)
                )

        topic_key = _topic_key(topic)
        if not is_force_create:
            acquired, existing = firestore_service.acquire_idempotency_key(
                scope="create_topic",
                key=topic_key,
                ttl_seconds=CREATE_TOPIC_IDEMPOTENCY_TTL_SECONDS,
                metadata={"topic": topic},
            )
            if not acquired:
                existing_status = existing.get("status", "queued")
                telegram_service.send_message(
                    chat_id,
                    f"This topic is already {existing_status}. Skipping duplicate request.",
                )
                return

        state = firestore_service.get_pipeline_state()
        if state.get("state") == "processing" and not is_force_create:
            telegram_service.send_message(chat_id, "A video is already being processed. Please wait for it to finish.")
            firestore_service.update_idempotency_key(
                "create_topic",
                topic_key,
                {"status": "rejected_busy"},
            )
            return
        if is_force_create:
            firestore_service.update_idempotency_key(
                "create_topic",
                topic_key,
                {"status": "force_override"},
            )

        direct_batch_id = f"direct_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
        firestore_service.save_news_batch(
            direct_batch_id,
            "direct",
            {
                "DIRECT01": {
                    "code": "DIRECT01",
                    "headline": topic,
                    "context": "Direct user topic request",
                    "rating": 5.0,
                    "genre": "Direct",
                }
            },
        )
        firestore_service.set_pipeline_and_batch_state(direct_batch_id, "processing")
        task_name = _task_name(direct_batch_id, "DIRECT01")
        public_id = _public_video_id(task_name)
        enqueued = _enqueue_generate(
            topic,
            "DIRECT01",
            direct_batch_id,
            public_id=public_id,
            force_run=is_force_create,
            details=create_context,
            idempotency_scope="create_topic",
            idempotency_key=topic_key,
        )
        if enqueued:
            firestore_service.update_idempotency_key(
                "create_topic",
                topic_key,
                {"status": "queued", "batch_id": direct_batch_id, "public_id": public_id},
            )
            telegram_service.send_message(
                chat_id,
                (
                    f"FORCE_CREATE accepted. Generating video for your custom topic: *{topic}*.\nVideo ID: `{public_id}`"
                    if is_force_create
                    else f"Got it! Generating video for your custom topic: *{topic}*.\nVideo ID: `{public_id}`"
                ),
            )
        else:
            firestore_service.update_idempotency_key(
                "create_topic",
                topic_key,
                {"status": "duplicate_task"},
            )
            telegram_service.send_message(
                chat_id,
                "A similar request is already queued. I will notify you when it is uploaded.",
            )
        return

    state = firestore_service.get_pipeline_state()
    batch_id = state.get("active_batch_id")

    if not batch_id:
        telegram_service.send_message(chat_id, "No active digest. Please wait for the next one.")
        return

    if text == "NONE":
        firestore_service.set_pipeline_and_batch_state(batch_id, "skipped")
        telegram_service.send_message(chat_id, "Got it! See you in the next digest.")
        return

    batch = firestore_service.get_news_batch(batch_id)
    if state.get("state") == "awaiting_reply" and _is_digest_expired(batch):
        firestore_service.set_pipeline_and_batch_state(batch_id, "skipped")
        telegram_service.send_message(
            chat_id,
            "This digest expired after 2 hours and has been skipped. Please wait for the next digest, or use CREATE <topic>.",
        )
        return

    item = batch["items"].get(text) if batch else None

    if item:
        current_state = state.get("state")
        if current_state in ("processing", "completed"):
            telegram_service.send_message(chat_id, "A video is already being processed. Please wait for it to finish.")
            return
        firestore_service.set_pipeline_and_batch_state(batch_id, "processing")
        task_name = _task_name(batch_id, text)
        public_id = _public_video_id(task_name)
        enqueued = _enqueue_generate(item["headline"], text, batch_id, public_id=public_id)
        if enqueued:
            telegram_service.send_message(
                chat_id,
                f"Got it! Generating video for *{text}* (ID: `{public_id}`). I'll notify you when it's uploaded.",
            )
        else:
            telegram_service.send_message(chat_id, f"Video for *{text}* is already queued. I'll notify you when it's uploaded.")
        return

    valid_codes = ", ".join(sorted(batch["items"].keys())) if batch else "TECH01–TECH05"
    telegram_service.send_message(chat_id, f"Invalid code. Please reply with {valid_codes} or None.")


def _enqueue_generate(
    headline: str,
    code: str,
    batch_id: str,
    public_id: str = "",
    force_run: bool = False,
    genre: str = "",
    details: str = "",
    virality_score: float | int | None = None,
    source: str = "telegram",
    idempotency_scope: str | None = None,
    idempotency_key: str | None = None,
) -> bool:
    """Enqueue a Cloud Task to generate a video. Returns True if newly enqueued, False if duplicate."""
    client = tasks_v2.CloudTasksClient()
    queue_path = client.queue_path(PROJECT_ID, LOCATION, TASKS_QUEUE)

    # Deterministic task name prevents Cloud Tasks from running duplicates
    raw_name = f"generate-{batch_id}-{code}"
    task_name = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_name)
    video_public_id = public_id or _public_video_id(task_name)
    job_id = task_name
    payload_dict = {
        "headline": headline,
        "code": code,
        "batch_id": batch_id,
        "job_id": job_id,
        "public_id": video_public_id,
        "force_run": bool(force_run),
        "genre": genre,
        "details": details,
        "virality_score": float(virality_score or 0),
    }
    if idempotency_scope and idempotency_key:
        payload_dict["idempotency_scope"] = idempotency_scope
        payload_dict["idempotency_key"] = idempotency_key
    payload = json.dumps(payload_dict).encode()

    try:
        client.create_task(request={
            "parent": queue_path,
            "task": {
                "name": f"{queue_path}/tasks/{task_name}",
                "http_request": {
                    "http_method": tasks_v2.HttpMethod.POST,
                    "url": f"{CLOUD_RUN_URL}/generate/task",
                    "headers": {"Content-Type": "application/json"},
                    "body": payload,
                    "oidc_token": {
                        "service_account_email": "353645494126-compute@developer.gserviceaccount.com",
                    },
                },
                # retry_config is queue-level only — not settable per-task.
                # Dedup is handled via deterministic task name + single-video guard.
            },
        })
        firestore_service.create_or_update_job(
            job_id,
            {
                "job_id": job_id,
                "batch_id": batch_id,
                "code": code,
                "topic": headline,
                "source": source,
                "status": "queued",
                "public_id": video_public_id,
                "genre": genre,
                "details": details,
                "virality_score": float(virality_score or 0),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return True
    except AlreadyExists:
        logger.warning(f"Task {task_name} already exists in queue — skipping duplicate enqueue")
        return False


def send_post_result(title: str, url: str, public_id: str = "", live_date: str = "", live_time: str = "", domain: str = ""):
    id_line = f"\nId: `{public_id}`" if public_id else ""
    domain_line = f"\nDomain: {domain.title()}" if domain else ""
    date_line = live_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    time_line = live_time or datetime.now(timezone.utc).strftime("%H:%M UTC")
    message = (
        "✅ Your video is live on Kurrent Affairs\n"
        f"Live Link: {url}\n"
        f"Date: {date_line}\n"
        f"Time: {time_line}"
        f"{id_line}"
        f"{domain_line}"
    )
    telegram_service.send_message(TELEGRAM_CHAT_ID, message)
