# app/agents/generator_agent.py

import logging
import os
import time
from datetime import datetime, timezone
from uuid import uuid4
from typing import Callable, Any

from app.config import TEMP_DIR, OUTPUT_DIR, TMP_RETENTION_DAYS, TELEGRAM_CHAT_ID
from app.services import firestore_service
from app.services.llm_service import (
    generate_script,
    classify_music_genre,
    apply_quality_controls,
)
from app.services.tts_service import generate_audio, choose_voice_for_video
from app.services.image_service import generate_image
from app.services.video_service import create_video
from app.services.telegram_service import send_message
from app.utils.helpers import extract_json, ensure_dir, cleanup_files_older_than
from app.agents.senior_script_reviewer import review_package

logger = logging.getLogger(__name__)

# Maximum scenes to generate — keeps us under free-tier Imagen quota (5 images/min)
MAX_SCENES = 3
SCENE_MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("quota" in text) or ("resource_exhausted" in text) or ("429" in text)


def _run_with_backoff(fn: Callable[[], Any], max_retries: int = SCENE_MAX_RETRIES):
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn(), (attempt - 1)
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            time.sleep(delay)
    raise last_exc


def _set_batch_terminal_state(batch_id: str | None, status: str):
    """Keep pipeline state consistent when a batch reaches a terminal state."""
    if not batch_id:
        return
    try:
        current = firestore_service.get_pipeline_state() or {}
        if current.get("active_batch_id") == batch_id:
            firestore_service.update_batch_status(batch_id, status)
            firestore_service.set_pipeline_state(batch_id, status)
    except Exception:
        return


def _is_cancel_requested(job_id: str) -> bool:
    try:
        job = firestore_service.get_job(job_id) or {}
        return bool(job.get("cancel_requested"))
    except Exception:
        return False


def run(
    headline: str,
    code: str,
    batch_id: str = None,
    job_id: str | None = None,
    public_id: str | None = None,
    force_run: bool = False,
    genre: str = "",
    details: str = "",
    virality_score: float = 0.0,
    idempotency_scope: str | None = None,
    idempotency_key: str | None = None,
):
    ensure_dir(TEMP_DIR)
    ensure_dir(OUTPUT_DIR)
    cleanup_files_older_than(TEMP_DIR, TMP_RETENTION_DAYS)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    lock_owner = f"task:{batch_id or 'manual'}:{code}:{uuid4().hex}" if not force_run else ""
    effective_job_id = job_id or f"task-{uuid4().hex}"

    firestore_service.create_or_update_job(
        effective_job_id,
        {
            "job_id": effective_job_id,
            "batch_id": batch_id or "",
            "code": code,
            "topic": headline,
            "source": "telegram",
            "status": "processing",
            "public_id": public_id or "",
            "genre": genre,
            "details": details,
            "virality_score": float(virality_score or 0),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    selected_voice = choose_voice_for_video(language="en", preference="shuffle", domain=genre or "")
    firestore_service.create_or_update_job(
        effective_job_id,
        {
            "voice_profile": "shuffle",
            "voice_selected": selected_voice,
        },
    )

    if (not force_run) and (not firestore_service.acquire_video_lock(lock_owner)):
        logger.warning("Rejected generation because video lock is held by another run")
        firestore_service.create_or_update_job(
            effective_job_id,
            {
                "status": "rejected_busy",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        send_message(
            TELEGRAM_CHAT_ID,
            f"⚠️ Another video is already being processed. "
            f"Request for *{code}* has been rejected. Please wait for the current video to finish."
        )
        if idempotency_scope and idempotency_key:
            firestore_service.update_idempotency_key(
                idempotency_scope,
                idempotency_key,
                {"status": "rejected_busy"},
            )
        _set_batch_terminal_state(batch_id, "failed")
        return

    try:
        # ── Single-video guard ─────────────────────────────────────────────
        # If another pipeline is already running for a DIFFERENT batch, reject
        # this task immediately so Cloud Tasks doesn't retry it.
        current = firestore_service.get_pipeline_state()
        current_state = current.get("state")
        current_batch = current.get("active_batch_id")

        if (not force_run) and batch_id and (current_batch != batch_id or current_state != "processing"):
            logger.warning(
                f"Rejected stale task: batch_id={batch_id}, "
                f"current={current_batch}/{current_state}"
            )
            firestore_service.create_or_update_job(
                effective_job_id,
                {
                    "status": "stale_rejected",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            if idempotency_scope and idempotency_key:
                firestore_service.update_idempotency_key(
                    idempotency_scope,
                    idempotency_key,
                    {"status": "stale_rejected"},
                )
            _set_batch_terminal_state(batch_id, "failed")
            return  # return silently — Cloud Tasks will see 200 OK and not retry

        if (not force_run) and current_state == "processing" and current_batch != batch_id:
            firestore_service.create_or_update_job(
                effective_job_id,
                {
                    "status": "rejected_busy",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            send_message(
                TELEGRAM_CHAT_ID,
                f"⚠️ Another video is already being processed. "
                f"Request for *{code}* has been rejected. Please wait for the current video to finish."
            )
            if idempotency_scope and idempotency_key:
                firestore_service.update_idempotency_key(
                    idempotency_scope,
                    idempotency_key,
                    {"status": "rejected_busy"},
                )
            _set_batch_terminal_state(batch_id, "failed")
            return
        # ──────────────────────────────────────────────────────────────────

        send_message(
            TELEGRAM_CHAT_ID,
            f"🎬 Starting video generation for *{code}* (ID: `{public_id or effective_job_id}`)...\n_{headline}_",
        )

        raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
        try:
            scenes = extract_json(raw_script)
        except Exception:
            scenes = [{"scene": 1, "narration": headline, "visual": "news concept illustration"}]
        scenes = apply_quality_controls(headline, scenes, language="en")
        reviewed = review_package(headline, scenes, language="en", min_seconds=15, max_seconds=58, genre=genre or "")
        scenes = reviewed.get("scenes") or scenes
        reviewed_title = reviewed.get("title") or headline
        reviewed_caption = reviewed.get("caption") or ""

        # Cap at MAX_SCENES to stay within free-tier Imagen quota
        scenes = scenes[:MAX_SCENES]

        music_genre = classify_music_genre(headline)
        video_clips = []
        image_failures = 0

        for i, scene in enumerate(scenes):
            if _is_cancel_requested(effective_job_id):
                send_message(TELEGRAM_CHAT_ID, f"🛑 Generation stopped for ID `{public_id or effective_job_id}`.")
                firestore_service.create_or_update_job(
                    effective_job_id,
                    {
                        "status": "cancelled",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                _set_batch_terminal_state(batch_id, "failed")
                if idempotency_scope and idempotency_key:
                    firestore_service.update_idempotency_key(
                        idempotency_scope,
                        idempotency_key,
                        {"status": "cancelled", "job_id": effective_job_id},
                    )
                return
            narration = scene.get("narration")
            visual = scene.get("visual")
            if not narration or not visual:
                continue

            checkpoint = (firestore_service.get_job(effective_job_id) or {}).get("scene_progress", {}).get(str(i), {})
            checkpoint_status = checkpoint.get("status")
            checkpoint_audio = checkpoint.get("audio_path", "")
            checkpoint_image = checkpoint.get("image_path", "")
            if (
                checkpoint_status == "completed"
                and checkpoint_audio
                and checkpoint_image
                and os.path.exists(checkpoint_audio)
                and os.path.exists(checkpoint_image)
            ):
                video_clips.append((checkpoint_image, checkpoint_audio, narration))
                continue

            firestore_service.mark_scene_checkpoint(effective_job_id, i, "started")
            try:
                audio_path = os.path.join(TEMP_DIR, f"audio_{code}_{i}.mp3")
                _, audio_retries = _run_with_backoff(
                    lambda: generate_audio(narration, audio_path, language="en", voice_name=selected_voice)
                )
                firestore_service.mark_scene_checkpoint(
                    effective_job_id,
                    i,
                    "audio_ready",
                    audio_path=audio_path,
                    retries_audio=audio_retries,
                )

                image_path, image_retries = _run_with_backoff(
                    lambda: generate_image(visual, i, aspect_ratio="9:16")
                )
                firestore_service.record_quota_event("image_success")
                firestore_service.mark_scene_checkpoint(
                    effective_job_id,
                    i,
                    "completed",
                    audio_path=audio_path,
                    image_path=image_path,
                    retries_audio=audio_retries,
                    retries_image=image_retries,
                )
                video_clips.append((image_path, audio_path, narration))
                time.sleep(2)
            except Exception as e:
                image_failures += 1
                logger.error(f"Scene {i} failed: {e}")
                firestore_service.mark_scene_checkpoint(
                    effective_job_id,
                    i,
                    "failed",
                    error=str(e),
                )
                if _is_quota_error(e):
                    firestore_service.record_quota_event("image_quota_error", str(e))
                else:
                    firestore_service.record_quota_event("image_error", str(e))
                # If ALL scenes have failed due to quota/image errors, notify and abort early
                if image_failures >= MAX_SCENES:
                    send_message(
                        TELEGRAM_CHAT_ID,
                        f"❌ Image generation failed for *{code}* after 3 attempts "
                        f"(Imagen quota may be exhausted). Please try again later."
                    )
                    # Reset pipeline state so user can retry
                    if batch_id:
                        firestore_service.update_batch_status(batch_id, "failed")
                        firestore_service.set_pipeline_state(batch_id, "failed")
                    firestore_service.create_or_update_job(
                        effective_job_id,
                        {
                            "status": "failed",
                            "error_type": "image_generation",
                            "error_message": str(e)[:500],
                            "finished_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    if idempotency_scope and idempotency_key:
                        firestore_service.update_idempotency_key(
                            idempotency_scope,
                            idempotency_key,
                            {"status": "failed", "job_id": effective_job_id},
                        )
                    _set_batch_terminal_state(batch_id, "failed")
                    return

        if not video_clips:
            send_message(
                TELEGRAM_CHAT_ID,
                f"❌ Video generation failed for *{code}* — no scenes could be generated. "
                f"Please try again later."
            )
            if batch_id:
                firestore_service.update_batch_status(batch_id, "failed")
                firestore_service.set_pipeline_state(batch_id, "failed")
            firestore_service.create_or_update_job(
                effective_job_id,
                {
                    "status": "failed",
                    "error_type": "no_video_clips",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            if idempotency_scope and idempotency_key:
                firestore_service.update_idempotency_key(
                    idempotency_scope,
                    idempotency_key,
                    {"status": "failed", "job_id": effective_job_id},
                )
            _set_batch_terminal_state(batch_id, "failed")
            return

        if _is_cancel_requested(effective_job_id):
            send_message(TELEGRAM_CHAT_ID, f"🛑 Generation stopped for ID `{public_id or effective_job_id}`.")
            firestore_service.create_or_update_job(
                effective_job_id,
                {
                    "status": "cancelled",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            _set_batch_terminal_state(batch_id, "failed")
            if idempotency_scope and idempotency_key:
                firestore_service.update_idempotency_key(
                    idempotency_scope,
                    idempotency_key,
                    {"status": "cancelled", "job_id": effective_job_id},
                )
            return

        send_message(TELEGRAM_CHAT_ID, f"✅ Video generated! Now uploading to YouTube...")

        output_path = os.path.join(OUTPUT_DIR, f"final_{code}_{timestamp}.mp4")
        create_video(video_clips, output_path, music_genre=music_genre, language="en")

        # Upload to GCS so the video survives instance restarts
        try:
            from app.services.storage_service import upload_video as gcs_upload_video
            gcs_url = gcs_upload_video(output_path, f"videos/{os.path.basename(output_path)}")
            logger.info(f"☁️ Uploaded to GCS: {gcs_url}")
            firestore_service.create_or_update_job(effective_job_id, {"gcs_video_url": gcs_url})
        except Exception as gcs_err:
            logger.warning(f"GCS upload failed, video at local path only: {gcs_err}")

        caption = reviewed_caption

        # Lazy import to avoid circular dependency with social_media_agent → whatsapp_agent
        from app.agents import social_media_agent
        source = (firestore_service.get_job(effective_job_id) or {}).get("source", "")
        youtube_url = social_media_agent.post(
            video_path=output_path,
            caption=caption,
            title=reviewed_title,
            job_id=effective_job_id,
            public_id=public_id or "",
            genre=genre,
            source=source,
        )
        # If post() returned None the video was delivered via Telegram (delivered_manual).
        # Do not overwrite that status — just record the local path and scene count.
        if youtube_url is None:
            firestore_service.create_or_update_job(
                effective_job_id,
                {
                    "video_path": output_path,
                    "num_scenes": len(video_clips),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        else:
            firestore_service.create_or_update_job(
                effective_job_id,
                {
                    "status": "completed",
                    "video_path": output_path,
                    "youtube_url": youtube_url,
                    "num_scenes": len(video_clips),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        if idempotency_scope and idempotency_key:
            firestore_service.update_idempotency_key(
                idempotency_scope,
                idempotency_key,
                {"status": "completed", "job_id": effective_job_id},
            )
        _set_batch_terminal_state(batch_id, "completed")
    except Exception as e:
        firestore_service.create_or_update_job(
            effective_job_id,
            {
                "status": "failed",
                "error_type": "pipeline_exception",
                "error_message": str(e)[:500],
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if idempotency_scope and idempotency_key:
            firestore_service.update_idempotency_key(
                idempotency_scope,
                idempotency_key,
                {"status": "failed", "job_id": effective_job_id},
            )
        _set_batch_terminal_state(batch_id, "failed")
        raise
    finally:
        if lock_owner:
            firestore_service.release_video_lock(lock_owner)
