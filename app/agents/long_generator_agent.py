# app/agents/long_generator_agent.py

import logging
import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from app.config import TEMP_DIR, OUTPUT_DIR, TMP_RETENTION_DAYS, get_chat_id
from app.services import firestore_service
from app.services.llm_service import generate_long_facts_script, classify_music_genre, generate_long_video_description, generate_thumbnail_hook, generate_viral_title
from app.services.tts_service import generate_audio, choose_voice_for_video, choose_two_voices
from app.services.pexels_service import fetch_clips_for_scene
from app.services.image_service import generate_thumbnail
from app.services.long_video_service import create_long_video
from app.services.telegram_service import send_message
from app.services.youtube_service import upload_video, set_thumbnail, extract_video_id
from app.utils.helpers import extract_json, ensure_dir, cleanup_files_older_than

logger = logging.getLogger(__name__)

_LONG_LOCK_KEY = "long_video_generation"
_LONG_PIPELINE_CHANNEL = "stories_long"
_YOUTUBE_CHANNEL = "stories"

LONG_MIN_SCENES = 18
LONG_MAX_SCENES = 24

_TMW_VISUAL_STYLE = (
    "Bright flat digital illustration, thick bold outlines, vivid complementary color palette "
    "(electric blue, warm yellow, coral red), expressive cartoonish characters with exaggerated "
    "reactions, slightly surreal visual metaphors, clean white or gradient background, "
    "high contrast, playful and quirky composition"
)


def _sanitize_narration(text: str) -> str:
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    return text.strip()


def _set_long_pipeline_terminal(batch_id: str | None, status: str) -> None:
    if not batch_id:
        return
    try:
        firestore_service.set_pipeline_and_batch_state(batch_id, status, channel_id=_LONG_PIPELINE_CHANNEL)
    except Exception as err:
        logger.warning("set_pipeline_and_batch_state failed: %s", err)


def run(
    headline: str,
    code: str,
    batch_id: str | None = None,
    job_id: str | None = None,
    public_id: str | None = None,
    force_run: bool = False,
    genre: str = "",
    details: str = "",
    channel_id: str = _LONG_PIPELINE_CHANNEL,
) -> None:
    # Idempotency guard
    if job_id:
        existing = firestore_service.get_job(job_id) or {}
        if existing.get("status") in ("completed", "delivered_manual", "cancelled"):
            logger.info("Job %s already terminal (%s) — skipping", job_id, existing.get("status"))
            return

    ensure_dir(TEMP_DIR)
    ensure_dir(OUTPUT_DIR)
    cleanup_files_older_than(TEMP_DIR, TMP_RETENTION_DAYS)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    lock_owner = f"long:{batch_id or 'manual'}:{code}:{uuid4().hex}"
    effective_job_id = job_id or f"long-{uuid4().hex}"
    _chat_id = get_chat_id(_YOUTUBE_CHANNEL)

    firestore_service.create_or_update_job(effective_job_id, {
        "job_id": effective_job_id,
        "batch_id": batch_id or "",
        "code": code,
        "topic": headline,
        "source": "scheduler",
        "status": "processing",
        "public_id": public_id or "",
        "genre": genre,
        "details": details,
        "channel_id": channel_id,
        "video_type": "long",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    hook_voice, core_voice = choose_two_voices(language="en")
    # hook_voice for hook/retention/cta; core_voice for core facts
    selected_voice = core_voice  # default for backward compat / firestore logging
    firestore_service.create_or_update_job(effective_job_id, {
        "voice_selected": selected_voice,
        "hook_voice": hook_voice,
        "core_voice": core_voice,
    })

    if force_run:
        firestore_service.acquire_video_lock(lock_owner, force=True, lock_key=_LONG_LOCK_KEY)
    elif not firestore_service.acquire_video_lock(lock_owner, lock_key=_LONG_LOCK_KEY):
        firestore_service.create_or_update_job(effective_job_id, {
            "status": "rejected_busy",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        send_message(_chat_id, f"⚠️ Long video already being processed. Request `{code}` rejected.", channel_id=_YOUTUBE_CHANNEL)
        _set_long_pipeline_terminal(batch_id, "failed")
        return

    try:
        raw_script = generate_long_facts_script(headline, category=genre, premise=details or "")
        try:
            scenes = extract_json(raw_script)
        except Exception:
            scenes = []

        if not scenes or len(scenes) < LONG_MIN_SCENES:
            raise RuntimeError(f"Script too short: got {len(scenes)} scenes, need at least {LONG_MIN_SCENES}")

        scenes = scenes[:LONG_MAX_SCENES]
        for s in scenes:
            s["narration"] = _sanitize_narration(s.get("narration", ""))

        firestore_service.create_or_update_job(effective_job_id, {
            "reviewed_title": headline,
            "scene_count": len(scenes),
        })

        music_genre = classify_music_genre(headline, story_genre=genre)
        video_clips = []
        successful_scenes = 0

        for i, scene in enumerate(scenes):
            narration = scene.get("narration", "")
            visual_query = scene.get("visual_query", "documentary nature")
            if not narration:
                continue

            audio_path = os.path.join(TEMP_DIR, f"long_audio_{code}_{i}.mp3")
            try:
                segment_type = scene.get("segment", "core")
                scene_voice = hook_voice if segment_type in ("hook", "retention", "cta") else core_voice
                generate_audio(narration, audio_path, language="en", voice_name=scene_voice, channel_id=channel_id)
            except Exception as tts_err:
                logger.warning("Scene %d TTS failed, skipping: %s", i, tts_err)
                continue

            try:
                from moviepy import AudioFileClip
                _tmp = AudioFileClip(audio_path)
                audio_duration = _tmp.duration
                _tmp.close()
            except Exception:
                audio_duration = 20.0

            scene_clips = fetch_clips_for_scene(visual_query, audio_duration, scene_idx=i, category=genre, temp_dir=TEMP_DIR)

            video_clips.append({
                "clips_list": scene_clips,
                "audio_path": audio_path,
                "narration": narration,
            })
            successful_scenes += 1
            first_clip_path = scene_clips[0]["video_path"] if scene_clips else ""
            firestore_service.mark_scene_checkpoint(effective_job_id, i, "completed", audio_path=audio_path, image_path=first_clip_path)

        if successful_scenes < LONG_MIN_SCENES:
            raise RuntimeError(f"Only {successful_scenes} scenes succeeded, need at least {LONG_MIN_SCENES}")

        # Thumbnail — non-fatal
        thumbnail_path = None
        thumbnail_hook = ""
        try:
            thumbnail_hook = generate_thumbnail_hook(headline, category=genre)
        except Exception:
            pass
        try:
            thumbnail_prompt = f"{_TMW_VISUAL_STYLE} — {headline}"
            thumbnail_path = generate_thumbnail(thumbnail_prompt, code, hook_text=thumbnail_hook)
        except Exception as thumb_err:
            logger.warning("Thumbnail generation failed (non-fatal): %s", thumb_err)
            send_message(_chat_id, f"⚠️ Thumbnail generation failed for `{public_id or effective_job_id}`: {str(thumb_err)[:200]}", channel_id=_YOUTUBE_CHANNEL)

        # Viral title — non-fatal, falls back to original headline
        try:
            viral_title = generate_viral_title(headline, category=genre)
        except Exception:
            viral_title = headline

        firestore_service.create_or_update_job(effective_job_id, {
            "reviewed_title": viral_title,
            "scene_count": len(scenes),
        })

        # SEO description — non-fatal, falls back to simple line
        sample_narrations = [item.get("narration", "") for item in video_clips[:6]]
        try:
            video_description = generate_long_video_description(headline, category=genre, narrations=sample_narrations)
        except Exception as desc_err:
            logger.warning("Description generation failed (non-fatal): %s", desc_err)
            video_description = f"Discover the fascinating truth about: {headline}"

        output_path = os.path.join(OUTPUT_DIR, f"long_{code}_{timestamp}.mp4")
        create_long_video(video_clips, output_path, music_genre=music_genre, language="en", title=headline)

        # GCS upload — non-fatal
        try:
            from app.services.storage_service import upload_video as gcs_upload_video
            gcs_url = gcs_upload_video(output_path, f"videos/long/{os.path.basename(output_path)}")
            firestore_service.create_or_update_job(effective_job_id, {"gcs_video_url": gcs_url})
        except Exception as gcs_err:
            logger.warning("GCS upload failed: %s", gcs_err)

        # YouTube upload (regular video, not Short)
        youtube_url = upload_video(
            video_path=output_path,
            title=viral_title,
            description=video_description,
            genre=genre,
            channel_id=_YOUTUBE_CHANNEL,
            tags=["TellMeWhy", "facts", "educational", "longform", "curiosity"],
            is_short=False,
        )

        video_id = extract_video_id(youtube_url or "")
        watch_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else youtube_url

        if thumbnail_path and video_id:
            try:
                set_thumbnail(video_id, thumbnail_path, channel_id=_YOUTUBE_CHANNEL)
            except Exception as st_err:
                logger.warning("Thumbnail upload failed (non-fatal): %s", st_err)
                send_message(_chat_id, f"⚠️ Thumbnail upload failed: {str(st_err)[:200]}", channel_id=_YOUTUBE_CHANNEL)

        firestore_service.create_or_update_job(effective_job_id, {
            "status": "completed",
            "video_path": output_path,
            "youtube_url": watch_url or youtube_url,
            "num_scenes": len(video_clips),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        _set_long_pipeline_terminal(batch_id, "completed")

        send_message(
            _chat_id,
            f"✅ Long video published!\n*{headline}*\n{watch_url or youtube_url or '(no URL)'}",
            channel_id=_YOUTUBE_CHANNEL,
        )

    except Exception as exc:
        firestore_service.create_or_update_job(effective_job_id, {
            "status": "failed",
            "error_type": "pipeline_exception",
            "error_message": str(exc)[:500],
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        _set_long_pipeline_terminal(batch_id, "failed")
        try:
            send_message(_chat_id, f"❌ Long video failed for *{code}*: {str(exc)[:300]}", channel_id=_YOUTUBE_CHANNEL)
        except Exception:
            pass
        raise
    finally:
        firestore_service.release_video_lock(lock_owner, lock_key=_LONG_LOCK_KEY)
