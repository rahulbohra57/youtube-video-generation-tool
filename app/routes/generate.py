# app/routes/generate.py

from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.services.llm_service import (
    generate_script,
    generate_shorts_caption,
    classify_music_genre,
    apply_quality_controls,
)
from app.agents.senior_script_reviewer import review_package
from app.services.tts_service import generate_audio, choose_voice_for_video, get_voice_options
from app.services.image_service import generate_image
from app.services.video_service import create_video
from app.services import firestore_service
from app.services.storage_service import upload_video as gcs_upload_video
from app.utils.helpers import extract_json, ensure_dir, cleanup_files_older_than
from app.config import TEMP_DIR, OUTPUT_DIR, TMP_RETENTION_DAYS

import logging
import os
import re
import time
from datetime import datetime, timezone
from uuid import uuid4
from typing import Callable, Any

logger = logging.getLogger(__name__)


def _strip_markdown(text: str) -> str:
    """Remove common markdown formatting so captions render as plain text."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'`(.+?)`', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text.strip()


router = APIRouter()
SCENE_MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2


def _run_with_backoff(fn: Callable[[], Any], max_retries: int = SCENE_MAX_RETRIES):
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn(), (attempt - 1)
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    raise last_exc


# ─── Poll endpoint ─────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    """Poll for async video generation status.

    Returns the full job record. When status == 'completed', video_url contains
    the GCS public URL (or /media/<filename> if GCS upload failed).
    """
    job = firestore_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ─── Background pipeline ───────────────────────────────────────────────────────

def _run_pipeline_background(
    job_id: str,
    topic: str,
    language: str,
    aspect_ratio: str,
    selected_voice: str,
    lock_owner: str,
):
    """Full video generation pipeline — runs in a FastAPI background thread.

    All errors are caught and written to Firestore so the client can poll.
    Never raises — returning from this function with any status is always correct.
    """
    try:
        ensure_dir(TEMP_DIR)
        ensure_dir(OUTPUT_DIR)
        cleanup_files_older_than(TEMP_DIR, TMP_RETENTION_DAYS)

        # ── 1. Generate script ────────────────────────────────────────────────
        raw_script = generate_script(topic, language=language, aspect_ratio=aspect_ratio)

        print("\n================ RAW LLM OUTPUT ================\n")
        print(raw_script)
        print("\n================================================\n")

        try:
            scenes = extract_json(raw_script)
        except Exception:
            print("⚠️ JSON parsing failed. Using fallback...")
            scenes = [
                {"scene": 1, "narration": raw_script[:150], "visual": "AI related concept illustration"},
                {"scene": 2, "narration": raw_script[150:300] if len(raw_script) > 150 else raw_script, "visual": "technology and future visuals"},
            ]

        scenes = apply_quality_controls(topic, scenes, language=language)
        reviewed = review_package(topic, scenes, language=language, min_seconds=15, max_seconds=58)
        scenes = reviewed.get("scenes") or scenes

        if not isinstance(scenes, list) or len(scenes) == 0:
            firestore_service.create_or_update_job(job_id, {
                "status": "failed",
                "error_type": "validation",
                "error_message": "No valid scenes generated",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            return

        music_genre = classify_music_genre(topic)
        print(f"🎵 Music genre selected: {music_genre}")

        video_clips = []

        # ── 2. Process scenes ─────────────────────────────────────────────────
        for i, scene in enumerate(scenes):
            narration = scene.get("narration")
            visual = scene.get("visual")

            if not narration or not visual:
                print(f"⚠️ Skipping invalid scene: {scene}")
                continue

            try:
                audio_path = os.path.join(TEMP_DIR, f"audio_{job_id}_{i}.mp3")
                # Capture loop vars explicitly to avoid late-binding in lambda
                _, audio_retries = _run_with_backoff(
                    lambda n=narration, p=audio_path: generate_audio(n, p, language=language, voice_name=selected_voice)
                )
                firestore_service.mark_scene_checkpoint(
                    job_id, i, "audio_ready",
                    audio_path=audio_path, retries_audio=audio_retries,
                )

                image_path, image_retries = _run_with_backoff(
                    lambda v=visual, idx=i: generate_image(v, idx, aspect_ratio=aspect_ratio)
                )
                firestore_service.record_quota_event("image_success")
                firestore_service.mark_scene_checkpoint(
                    job_id, i, "completed",
                    audio_path=audio_path, image_path=image_path,
                    retries_audio=audio_retries, retries_image=image_retries,
                )

                video_clips.append((image_path, audio_path, narration))
                time.sleep(2)

            except Exception as scene_error:
                firestore_service.mark_scene_checkpoint(job_id, i, "failed", error=str(scene_error))
                err_text = str(scene_error).lower()
                if "quota" in err_text or "resource_exhausted" in err_text or "429" in err_text:
                    firestore_service.record_quota_event("image_quota_error", str(scene_error))
                else:
                    firestore_service.record_quota_event("image_error", str(scene_error))
                print(f"⚠️ Scene {i} failed:", scene_error)
                continue

        if len(video_clips) == 0:
            firestore_service.create_or_update_job(job_id, {
                "status": "failed",
                "error_type": "no_video_clips",
                "error_message": "No video clips could be generated",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            return

        # ── 3. Assemble video ─────────────────────────────────────────────────
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_filename = f"final_{timestamp}.mp4"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        create_video(video_clips, output_path, music_genre=music_genre, language=language)

        firestore_service.create_or_update_job(job_id, {
            "video_path": output_path,
            "num_scenes": len(video_clips),
        })

        # ── 4. Upload to GCS for durable storage ─────────────────────────────
        video_url = f"/media/{output_filename}"  # local fallback
        try:
            gcs_url = gcs_upload_video(output_path, f"videos/{output_filename}")
            video_url = gcs_url
            print(f"☁️ Uploaded to GCS: {gcs_url}")
        except Exception as gcs_err:
            logger.warning(f"GCS upload failed, serving from local path: {gcs_err}")

        firestore_service.create_or_update_job(job_id, {
            "status": "completed",
            "video_url": video_url,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        firestore_service.create_or_update_job(job_id, {
            "status": "failed",
            "error_type": "server",
            "error_message": str(e)[:500],
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.exception(f"Pipeline background task failed for job_id={job_id}: {e}")
    finally:
        firestore_service.release_video_lock(lock_owner)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/voices")
def list_voices(language: str = "en"):
    if language not in ("en", "hi"):
        language = "en"
    return {"language": language, "voices": get_voice_options(language)}


@router.post("/generate")
def generate_video(
    background_tasks: BackgroundTasks,
    topic: str,
    language: str = "en",
    aspect_ratio: str = "16:9",
    voice_profile: str = "shuffle",
):
    """Start async video generation. Returns immediately with a job_id.

    Poll GET /jobs/{job_id} for status. When status == 'completed', the
    response contains video_url pointing to the GCS-hosted video.
    """
    if not topic or topic.strip() == "":
        raise HTTPException(status_code=400, detail="Topic is required")

    if language not in ("en", "hi"):
        language = "en"

    if aspect_ratio not in ("16:9", "9:16"):
        aspect_ratio = "16:9"

    lock_owner = f"api:{uuid4().hex}"
    job_id = f"api-{uuid4().hex}"

    if not firestore_service.acquire_video_lock(lock_owner):
        firestore_service.create_or_update_job(job_id, {
            "job_id": job_id,
            "source": "web",
            "topic": topic,
            "status": "rejected_busy",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        raise HTTPException(
            status_code=429,
            detail="Another video is currently being generated. Please retry in a few minutes.",
        )

    selected_voice = choose_voice_for_video(language=language, preference=voice_profile)
    firestore_service.create_or_update_job(job_id, {
        "job_id": job_id,
        "source": "web",
        "topic": topic,
        "status": "processing",
        "language": language,
        "aspect_ratio": aspect_ratio,
        "voice_profile": voice_profile,
        "voice_selected": selected_voice,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    background_tasks.add_task(
        _run_pipeline_background,
        job_id=job_id,
        topic=topic,
        language=language,
        aspect_ratio=aspect_ratio,
        selected_voice=selected_voice,
        lock_owner=lock_owner,
    )

    return {
        "status": "processing",
        "job_id": job_id,
        "poll_url": f"/jobs/{job_id}",
        "message": "Video generation started. Poll poll_url for status and video_url when completed.",
    }


@router.post("/generate/task")
def generate_task(payload: dict):
    """Called by Cloud Tasks to run the full pipeline for a selected news item.

    Returns 200 in all cases except invalid payload — even on pipeline errors.
    This prevents Cloud Tasks from auto-retrying, which would cause duplicate videos.
    """
    import logging
    logger = logging.getLogger(__name__)
    headline = payload.get("headline", "")
    code = payload.get("code", "")
    batch_id = payload.get("batch_id")
    job_id = payload.get("job_id", f"task-{uuid4().hex}")
    public_id = payload.get("public_id")
    force_run = bool(payload.get("force_run", False))
    genre = payload.get("genre", "")
    details = payload.get("details", "")
    virality_score = float(payload.get("virality_score", 0) or 0)
    idempotency_scope = payload.get("idempotency_scope")
    idempotency_key = payload.get("idempotency_key")
    channel_id = payload.get("channel_id", "news")
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
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
            channel_id=channel_id,
        )
    except Exception as e:
        # Log but return 200 — returning 500 would cause Cloud Tasks to retry,
        # which would trigger duplicate video generation.
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
                current = firestore_service.get_pipeline_state(channel_id=channel_id) or {}
                if current.get("active_batch_id") == batch_id:
                    firestore_service.set_pipeline_and_batch_state(batch_id, "failed", channel_id=channel_id)
            except Exception:
                pass
        logger.exception(f"generate_task failed for code={code}: {e}")
    return {"status": "ok"}


@router.post("/generate-caption")
def generate_caption(topic: str, language: str = "en"):
    if not topic or topic.strip() == "":
        raise HTTPException(status_code=400, detail="Topic is required")

    if language not in ("en", "hi"):
        language = "en"

    try:
        caption = generate_shorts_caption(topic, language=language)
        caption = _strip_markdown(caption)
        return {"caption": caption}
    except Exception as e:
        print("❌ Caption ERROR:", str(e))
        raise HTTPException(status_code=500, detail="Caption generation failed")
