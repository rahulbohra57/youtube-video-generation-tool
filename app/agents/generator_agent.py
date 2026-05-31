# app/agents/generator_agent.py

import logging
import os
import time
from datetime import datetime, timezone
from uuid import uuid4
from typing import Callable, Any

from app.config import TEMP_DIR, OUTPUT_DIR, TMP_RETENTION_DAYS, get_chat_id
from app.services import firestore_service
from app.services.llm_service import (
    generate_script,
    generate_script_with_search,
    SearchGroundingUnavailable,
    generate_story_script,
    classify_music_genre,
    apply_quality_controls,
    get_cta_narration,
    _fact_visual_style,
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

# How many times the outer wrapper retries a fully-exhausted scene.
# image_service already does 3 internal quota retries (30s/60s/120s).
# Each outer retry gives Imagen another full backoff cycle after the inner
# one is exhausted, so 2 outer retries = up to ~9 total quota attempts.
SCENE_MAX_RETRIES = 3

# Delay between outer retries when Imagen quota is exhausted.
# Must be long enough for the per-minute quota window to refill.
QUOTA_OUTER_RETRY_DELAY = 120  # seconds

BACKOFF_BASE_SECONDS = 2  # for non-quota errors only

# Pre-approved safe fallback visual prompts per story genre.
# Used when the LLM-generated prompt is rejected by Imagen's safety filter.
# Each prompt uses the same watercolor style and is guaranteed content-policy safe.
_STORY_GENRE_SAFE_PROMPTS = {
    "inspiring": "Soft watercolor illustration, warm earthy palette — a young person planting a seedling in golden morning light, hopeful expression, no text, no words",
    "heartfelt": "Soft watercolor illustration, warm earthy palette — two people sharing tea at a candlelit window, gentle smiles, no text, no words",
    "comedy": "Soft watercolor illustration, bright cheerful palette — a person tripping over a garden hose while a puppy watches, playful and funny, no text, no words",
    "crime": "Soft watercolor illustration, warm earthy palette — a determined figure walking a winding road toward a glowing village at dusk, no text, no words",
    "action": "Soft watercolor illustration, vibrant palette — a figure leaping across stepping stones in a sunlit river, dynamic energy, no text, no words",
    "sci-fi": "Soft watercolor illustration, cool blue-purple palette — a scientist holding a glowing orb in a misty forest, wonder on their face, no text, no words",
    "mythology": "Soft watercolor illustration, golden earthy palette — a sage meditating beneath an ancient tree with glowing roots, sacred forest clearing, no text, no words",
    "thriller": "Soft watercolor illustration, warm amber palette — a curious person holding a lantern on a forest path, sense of wonder, no text, no words",
    "mystery": "Soft watercolor illustration, warm earthy palette — a child with a curious expression holding a lantern over an old wooden chest, garden at dusk, no text, no words",
    "adventure": "Soft watercolor illustration, vibrant greens and blues — a traveller studying a map on a sunlit hilltop, distant mountains behind them, no text, no words",
    "slice-of-life": "Soft watercolor illustration, warm earthy palette — a family sharing a meal at a wooden table in a cosy kitchen, warm morning light, no text, no words",
    "historical": "Soft watercolor illustration, muted sepia palette — a scholar in traditional robes in an ancient palace courtyard with blossoming trees, no text, no words",
}


# Safe fallback prompts for Tell Me Why (English facts) channel.
# Keyed by the 12 fact categories used in story_researcher._FACT_CATEGORIES.
# Used when an image prompt is rejected by Imagen's safety filter.
_STORY_GENRE_SAFE_PROMPTS_EN = {
    "science & space": "Vibrant flat-design illustration — a telescope pointed at a starry night sky, glowing planets in the background, warm cosmic colors, no text, no words",
    "history & civilizations": "Soft pencil sketch illustration, muted sepia tones — an ancient stone archway leading to a sunlit marketplace, people in traditional robes, serene atmosphere, no text, no words",
    "human body & biology": "Clean flat-design infographic style — a glowing human silhouette with colorful abstract shapes representing cells and organs, bright blue-green palette, no text, no words",
    "technology & ai": "Bold graphic novel style, cool blue palette — a scientist holding a glowing holographic sphere in a bright modern laboratory, wonder on their face, no text, no words",
    "health & fitness": "Vibrant storybook illustration — a person stretching at sunrise on a hilltop overlooking a green valley, energetic and joyful, warm colors, no text, no words",
    "psychology & dark psychology": "Soft pencil sketch, cool blue-grey tones — a lone figure looking at their own reflection in a still lake, abstract swirling shapes behind them, no text, no words",
    "relationships & dating": "Soft watercolor illustration, warm golden tones — two friends sharing tea at a sunlit window, gentle expressions, no text, no words",
    "self-improvement & habits": "Vibrant storybook illustration, bold outlines — a young person planting a seedling in golden morning light, hopeful smile, warm colors, no text, no words",
    "business & finance": "Clean flat-design illustration — stacked coins and a rising graph on a wooden desk beside a coffee cup, warm morning light, no text, no words",
    "culture & society": "Vibrant flat-design illustration — a diverse group of illustrated figures standing together under a colorful mosaic arch, joyful atmosphere, no text, no words",
    "philosophy & life": "Soft pencil sketch illustration, warm golden tones — a figure sitting on a cliff edge at sunset gazing at the horizon, contemplative mood, no text, no words",
    "mysteries & unexplained": "Bold graphic novel style, deep navy and amber palette — a lone explorer with a lantern inside a vast ancient cave with glowing rock formations, no text, no words",
}


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("quota" in text) or ("resource_exhausted" in text) or ("429" in text)


def _is_safety_filter_error(exc: Exception) -> bool:
    from app.services.image_service import SAFETY_FILTER_ERROR_PREFIX
    return str(exc).startswith(SAFETY_FILTER_ERROR_PREFIX)


def _run_with_backoff(fn: Callable[[], Any], max_retries: int = SCENE_MAX_RETRIES):
    """Call fn() up to max_retries times with smart backoff.

    - Quota errors: wait QUOTA_OUTER_RETRY_DELAY seconds before the next attempt
      so the Imagen per-minute bucket has time to refill.
    - Safety-filter errors: raise immediately — retrying the same prompt is pointless.
    - Other errors: short exponential backoff (2s / 4s / ...).
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn(), (attempt - 1)
        except Exception as exc:
            last_exc = exc
            if _is_safety_filter_error(exc):
                # Same prompt will keep being rejected — don't waste retries.
                raise
            if attempt >= max_retries:
                break
            if _is_quota_error(exc):
                logger.warning(
                    f"Quota error on attempt {attempt}/{max_retries} — "
                    f"waiting {QUOTA_OUTER_RETRY_DELAY}s before retry"
                )
                time.sleep(QUOTA_OUTER_RETRY_DELAY)
            else:
                delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                time.sleep(delay)
    raise last_exc


def _set_batch_terminal_state(batch_id: str | None, status: str, channel_id: str = "news"):
    """Keep pipeline state consistent when a batch reaches a terminal state."""
    if not batch_id:
        return
    try:
        current = firestore_service.get_pipeline_state(channel_id=channel_id) or {}
        if current.get("active_batch_id") == batch_id:
            firestore_service.set_pipeline_and_batch_state(batch_id, status, channel_id=channel_id)
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
    channel_id: str = "news",
    script_type: str = "news",
    language: str | None = None,
):
    # ── Idempotency guard ─────────────────────────────────────────────────
    # Prevents Cloud Tasks duplicate/retry deliveries from uploading twice.
    # A job in a terminal state means this task already completed successfully.
    if job_id:
        _existing = firestore_service.get_job(job_id) or {}
        if _existing.get("status") in ("completed", "delivered_manual", "cancelled"):
            logger.info(
                f"Job {job_id} already terminal ({_existing.get('status')}) — "
                "skipping duplicate task delivery"
            )
            return
    # ──────────────────────────────────────────────────────────────────────

    ensure_dir(TEMP_DIR)
    ensure_dir(OUTPUT_DIR)
    cleanup_files_older_than(TEMP_DIR, TMP_RETENTION_DAYS)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    lock_owner = f"task:{batch_id or 'manual'}:{code}:{uuid4().hex}"
    effective_job_id = job_id or f"task-{uuid4().hex}"

    _chat_id = get_chat_id(channel_id)
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
            "channel_id": channel_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    # language defaults to "hi" for stories (backward compat with in-flight tasks)
    _voice_lang = (language or "hi") if script_type == "story" else "en"
    selected_voice = choose_voice_for_video(language=_voice_lang, preference="shuffle", domain=genre or "")
    firestore_service.create_or_update_job(
        effective_job_id,
        {
            "voice_profile": "shuffle",
            "voice_selected": selected_voice,
        },
    )

    if force_run:
        firestore_service.acquire_video_lock(lock_owner, force=True)
    elif not firestore_service.acquire_video_lock(lock_owner):
        logger.warning("Rejected generation because video lock is held by another run")
        firestore_service.create_or_update_job(
            effective_job_id,
            {
                "status": "rejected_busy",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        send_message(
            _chat_id,
            f"⚠️ Another video is already being processed. "
            f"Request for *{code}* has been rejected. Please wait for the current video to finish.",
            channel_id=channel_id,
        )
        if idempotency_scope and idempotency_key:
            firestore_service.update_idempotency_key(
                idempotency_scope,
                idempotency_key,
                {"status": "rejected_busy"},
            )
        _set_batch_terminal_state(batch_id, "failed", channel_id=channel_id)
        return

    try:
        # ── Single-video guard ─────────────────────────────────────────────
        # If another pipeline is already running for a DIFFERENT batch, reject
        # this task immediately so Cloud Tasks doesn't retry it.
        current = firestore_service.get_pipeline_state(channel_id=channel_id)
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
            _set_batch_terminal_state(batch_id, "failed", channel_id=channel_id)
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
                _chat_id,
                f"⚠️ Another video is already being processed. "
                f"Request for *{code}* has been rejected. Please wait for the current video to finish.",
                channel_id=channel_id,
            )
            if idempotency_scope and idempotency_key:
                firestore_service.update_idempotency_key(
                    idempotency_scope,
                    idempotency_key,
                    {"status": "rejected_busy"},
                )
            _set_batch_terminal_state(batch_id, "failed", channel_id=channel_id)
            return
        # ──────────────────────────────────────────────────────────────────

        if script_type == "story":
            # Stories: pure LLM generation, language from payload (default "hi" for backward compat)
            language = language or "hi"
            mood = genre or "inspiring"
            raw_script = generate_story_script(headline, mood=mood, premise=details or "", language=language)
        elif script_type == "facts":
            # Facts: search-grounded English script with category-appropriate visual style
            language = "en"
            fact_visual_style = _fact_visual_style(genre or "")
            try:
                raw_script = generate_script_with_search(
                    headline,
                    language="en",
                    aspect_ratio="9:16",
                    context=details or "",
                    visual_style_override=fact_visual_style,
                    script_mode="facts",
                )
            except SearchGroundingUnavailable:
                logger.info("Search grounding unavailable for %s, using standard generation", public_id or effective_job_id)
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
            except Exception as _search_exc:
                logger.warning("Search-grounded facts script generation failed (%s), falling back to standard", _search_exc)
                send_message(
                    _chat_id,
                    f"⚠️ Search-grounded script failed for `{public_id or effective_job_id}` — "
                    f"falling back to standard generation.\nReason: {str(_search_exc)[:200]}",
                    channel_id=channel_id,
                )
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
        else:
            # News: search-grounded script generation in English
            language = "en"
            try:
                raw_script = generate_script_with_search(headline, language="en", aspect_ratio="9:16", context=details or "")
            except SearchGroundingUnavailable:
                logger.info("Search grounding unavailable for %s, using standard generation", public_id or effective_job_id)
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
            except Exception as _search_exc:
                logger.warning("Search-grounded script generation failed (%s), falling back to standard", _search_exc)
                send_message(
                    _chat_id,
                    f"⚠️ Search-grounded script failed for `{public_id or effective_job_id}` — "
                    f"falling back to standard generation (content may be less accurate).\n"
                    f"Reason: {str(_search_exc)[:200]}",
                    channel_id=channel_id,
                )
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
        try:
            scenes = extract_json(raw_script)
        except Exception:
            scenes = [{"scene": 1, "narration": headline, "visual": "news concept illustration"}]
        scenes = apply_quality_controls(headline, scenes, language=language, context=details or "", skip_fact_check=(script_type == "story"))
        reviewed = review_package(headline, scenes, language=language, min_seconds=15, max_seconds=58, genre=genre or "")
        scenes = reviewed.get("scenes") or scenes
        reviewed_title = reviewed.get("title") or headline
        reviewed_caption = reviewed.get("caption") or ""

        # Persist the reviewed title so REDO uses the same title as the original upload
        firestore_service.create_or_update_job(effective_job_id, {"reviewed_title": reviewed_title})

        scenes = scenes[:MAX_SCENES]

        music_genre = classify_music_genre(headline, story_genre=genre)
        video_clips = []
        image_failures = 0
        _last_scene_err_reason = "unknown"

        for i, scene in enumerate(scenes):
            if _is_cancel_requested(effective_job_id):
                send_message(_chat_id, f"🛑 Generation stopped successfully for ID `{public_id or effective_job_id}`.", channel_id=channel_id)
                firestore_service.create_or_update_job(
                    effective_job_id,
                    {
                        "status": "cancelled",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                _set_batch_terminal_state(batch_id, "failed", channel_id=channel_id)
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
                video_clips.append({
                    "image_path": checkpoint_image,
                    "audio_path": checkpoint_audio,
                    "narration": narration,
                    "motion_type": scene.get("motion_type", ""),
                    "camera_path": scene.get("camera_path", ""),
                    "focus_subject": scene.get("focus_subject", ""),
                    "transition": scene.get("transition", ""),
                    "effect_cue": scene.get("effect_cue", ""),
                })
                continue

            firestore_service.mark_scene_checkpoint(effective_job_id, i, "started")
            try:
                audio_path = os.path.join(TEMP_DIR, f"audio_{code}_{i}.mp3")
                _, audio_retries = _run_with_backoff(
                    lambda n=narration, p=audio_path: generate_audio(n, p, language=language, voice_name=selected_voice, channel_id=channel_id)
                )
                firestore_service.mark_scene_checkpoint(
                    effective_job_id,
                    i,
                    "audio_ready",
                    audio_path=audio_path,
                    retries_audio=audio_retries,
                )

                image_path, image_retries = _run_with_backoff(
                    lambda v=visual, idx=i: generate_image(v, idx, aspect_ratio="9:16")
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
                video_clips.append({
                    "image_path": image_path,
                    "audio_path": audio_path,
                    "narration": narration,
                    "motion_type": scene.get("motion_type", ""),
                    "camera_path": scene.get("camera_path", ""),
                    "focus_subject": scene.get("focus_subject", ""),
                    "transition": scene.get("transition", ""),
                    "effect_cue": scene.get("effect_cue", ""),
                })
            except Exception as e:
                # For story scenes blocked by safety filter, try a pre-approved
                # genre-safe fallback prompt once before counting the scene as failed.
                if _is_safety_filter_error(e) and script_type == "story":
                    logger.warning(
                        f"Scene {i} safety-filtered (genre={genre!r}). "
                        f"Rejected prompt: {visual!r}. Retrying with genre fallback."
                    )
                    firestore_service.record_quota_event(
                        "image_safety_filter",
                        f"scene={i} genre={genre} rejected_prompt={visual[:300]}",
                    )
                    _safe_prompts = (
                        _STORY_GENRE_SAFE_PROMPTS_EN
                        if language == "en"
                        else _STORY_GENRE_SAFE_PROMPTS
                    )
                    _default_safe = next(iter(_safe_prompts.values()))
                    fallback_visual = _safe_prompts.get(
                        (genre or "").lower(),
                        _default_safe,
                    )
                    try:
                        image_path, image_retries = _run_with_backoff(
                            lambda fp=fallback_visual, idx=i: generate_image(fp, idx, aspect_ratio="9:16")
                        )
                        firestore_service.record_quota_event("image_success")
                        firestore_service.mark_scene_checkpoint(
                            effective_job_id,
                            i,
                            "completed",
                            audio_path=audio_path,
                            image_path=image_path,
                            retries_audio=0,
                            retries_image=image_retries,
                        )
                        video_clips.append({
                            "image_path": image_path,
                            "audio_path": audio_path,
                            "narration": narration,
                            "motion_type": scene.get("motion_type", ""),
                            "camera_path": scene.get("camera_path", ""),
                            "focus_subject": scene.get("focus_subject", ""),
                            "transition": scene.get("transition", ""),
                            "effect_cue": scene.get("effect_cue", ""),
                        })
                        continue  # scene recovered via fallback — skip failure handling
                    except Exception as fallback_exc:
                        e = fallback_exc  # fall through to normal failure handling below

                image_failures += 1
                logger.error(f"Scene {i} failed: {e}")
                if _is_safety_filter_error(e):
                    logger.warning(f"Scene {i} safety-filtered. Rejected prompt: {visual!r}")
                    firestore_service.record_quota_event(
                        "image_safety_filter",
                        f"scene={i} rejected_prompt={visual[:300]}",
                    )
                    _scene_err_reason = "prompt blocked by safety filter"
                elif _is_quota_error(e):
                    firestore_service.record_quota_event("image_quota_error", str(e))
                    _scene_err_reason = "Imagen quota exhausted"
                else:
                    firestore_service.record_quota_event("image_error", str(e))
                    _scene_err_reason = str(e)[:120]
                _last_scene_err_reason = _scene_err_reason
                firestore_service.mark_scene_checkpoint(
                    effective_job_id,
                    i,
                    "failed",
                    error=str(e),
                )
                # If ALL scenes have failed, notify and abort early
                if image_failures >= MAX_SCENES:
                    send_message(
                        _chat_id,
                        f"❌ Image generation failed for *{code}* — all {MAX_SCENES} scenes failed.\n"
                        f"Reason: {_scene_err_reason}\n"
                        f"Video has been dropped. Please try again later.",
                        channel_id=channel_id,
                    )
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
                    _set_batch_terminal_state(batch_id, "failed", channel_id=channel_id)
                    return

        # Require at least MAX_SCENES-1 clips. A single successful scene
        # produces a ~15s stub that looks broken on YouTube. Treat it as a failure
        # so Cloud Tasks does NOT retry a partial video upload.
        min_clips = max(1, MAX_SCENES - 1)
        if len(video_clips) < min_clips:
            clip_count = len(video_clips)
            send_message(
                _chat_id,
                f"❌ Video generation failed for *{code}* — only {clip_count}/{MAX_SCENES} scenes "
                f"could be generated. Reason: {_last_scene_err_reason}. Please try again later.",
                channel_id=channel_id,
            )
            firestore_service.create_or_update_job(
                effective_job_id,
                {
                    "status": "failed",
                    "error_type": "insufficient_video_clips",
                    "error_message": f"{clip_count}/{MAX_SCENES} scenes generated",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            if idempotency_scope and idempotency_key:
                firestore_service.update_idempotency_key(
                    idempotency_scope,
                    idempotency_key,
                    {"status": "failed", "job_id": effective_job_id},
                )
            _set_batch_terminal_state(batch_id, "failed", channel_id=channel_id)
            return

        if _is_cancel_requested(effective_job_id):
            send_message(_chat_id, f"🛑 Generation stopped successfully for ID `{public_id or effective_job_id}`.", channel_id=channel_id)
            firestore_service.create_or_update_job(
                effective_job_id,
                {
                    "status": "cancelled",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            _set_batch_terminal_state(batch_id, "failed", channel_id=channel_id)
            if idempotency_scope and idempotency_key:
                firestore_service.update_idempotency_key(
                    idempotency_scope,
                    idempotency_key,
                    {"status": "cancelled", "job_id": effective_job_id},
                )
            return

        # Append CTA voiceover over the last frame — no new Imagen call needed
        try:
            cta_narration = get_cta_narration(channel_id=channel_id, language=language)
            cta_audio_path = os.path.join(TEMP_DIR, f"audio_{code}_cta.mp3")
            generate_audio(cta_narration, cta_audio_path, language=language, voice_name=selected_voice, channel_id=channel_id)
            last_image_path = video_clips[-1]["image_path"] if isinstance(video_clips[-1], dict) else video_clips[-1][0]
            video_clips.append({
                "image_path": last_image_path,
                "audio_path": cta_audio_path,
                "narration": cta_narration,
                "motion_type": "ken_burns",
                "camera_path": "center_hold",
                "focus_subject": "cta",
                "transition": "dissolve",
                "effect_cue": "subtle glow",
            })
        except Exception as _cta_err:
            logger.warning("CTA audio generation failed, skipping CTA: %s", _cta_err)

        output_path = os.path.join(OUTPUT_DIR, f"final_{code}_{timestamp}.mp4")
        create_video(
            video_clips,
            output_path,
            music_genre=music_genre,
            language=language,
            channel_id=channel_id,
            story_genre=genre,
            virality_score=virality_score,
        )

        # Upload to GCS so the video survives instance restarts
        try:
            from app.services.storage_service import upload_video as gcs_upload_video
            gcs_url = gcs_upload_video(output_path, f"videos/{os.path.basename(output_path)}")
            logger.info(f"☁️ Uploaded to GCS: {gcs_url}")
            firestore_service.create_or_update_job(effective_job_id, {"gcs_video_url": gcs_url})
        except Exception as gcs_err:
            logger.warning(f"GCS upload failed, video at local path only: {gcs_err}")
            send_message(
                _chat_id,
                f"⚠️ GCS upload failed for `{public_id or effective_job_id}` — "
                f"REDO and RESEND will *not* work for this video.\n"
                f"Error: {str(gcs_err)[:200]}",
                channel_id=channel_id,
            )

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
            channel_id=channel_id,
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
        _set_batch_terminal_state(batch_id, "completed", channel_id=channel_id)
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
        _set_batch_terminal_state(batch_id, "failed", channel_id=channel_id)
        try:
            send_message(
                _chat_id,
                f"❌ Pipeline failed for *{code}* (ID: `{public_id or effective_job_id}`) — "
                f"{type(e).__name__}: {str(e)[:300]}",
                channel_id=channel_id,
            )
        except Exception:
            pass
        raise
    finally:
        firestore_service.release_video_lock(lock_owner)
