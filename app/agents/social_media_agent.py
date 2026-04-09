# app/agents/social_media_agent.py

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from app.services import youtube_service, firestore_service
from app.services.llm_service import enhance_caption, format_caption_for_youtube
from app.services.telegram_service import send_message, send_video_for_manual_post
from app.config import get_chat_id

logger = logging.getLogger(__name__)


def _deliver_video_to_telegram(
    job_id: str,
    video_path: str,
    title: str,
    caption: str,
    source_label: str = "",
    channel_id: str = "news",
):
    """Send video + caption to Telegram for manual posting and mark job as delivered_manual."""
    chat_id = get_chat_id(channel_id)
    send_video_for_manual_post(chat_id, video_path, title, caption, source_label=source_label)
    if job_id:
        firestore_service.create_or_update_job(job_id, {
            "status": "delivered_manual",
            "manual_delivery_at": datetime.now(timezone.utc).isoformat(),
        })


def post(video_path: str, caption: str, title: str, job_id: str = "", public_id: str = "", genre: str = "", source: str = "", channel_id: str = "news"):
    chat_id = get_chat_id(channel_id)
    enhanced = enhance_caption(caption)
    enhanced = format_caption_for_youtube(enhanced)

    # Persist final caption for REDO/RESEND before any upload attempt
    if job_id:
        firestore_service.create_or_update_job(job_id, {"final_caption": enhanced})

    send_message(chat_id, "📤 Posting to YouTube Shorts...")
    try:
        url = youtube_service.upload_video(video_path, title, enhanced, genre=genre, channel_id=channel_id)
    except Exception as e:
        err = str(e)
        if "youtube_quota_exceeded" in err:
            logger.warning(f"YouTube quota exceeded for job {job_id}")
            send_message(chat_id, "⚠️ YouTube daily quota exceeded — sending video for manual posting.")
            label = f"{source}_quota" if source else "quota"
        else:
            logger.exception(f"YouTube upload failed: {e}")
            send_message(chat_id, f"❌ YouTube upload failed: {e}")
            label = f"{source}_upload_error" if source else "upload_error"
        _deliver_video_to_telegram(job_id, video_path, title, enhanced, source_label=label, channel_id=channel_id)
        # Mark pipeline state as completed so the next scheduler run isn't blocked
        _finalize_pipeline_state(job_id, channel_id=channel_id)
        return None

    video_id = youtube_service.extract_video_id(url)
    if video_id:
        try:
            playlist_id = youtube_service.get_or_create_playlist(genre, channel_id=channel_id)
            if playlist_id:
                youtube_service.add_video_to_playlist(video_id, playlist_id, channel_id=channel_id)
                logger.info(f"📋 Added to playlist: {playlist_id}")
        except Exception as e:
            logger.warning(f"Playlist assignment failed (non-fatal): {e}")

    _finalize_pipeline_state(job_id, channel_id=channel_id)

    # Lazy import to avoid circular dependency
    from app.agents import whatsapp_agent, stories_agent
    try:
        if job_id and channel_id != "stories":
            firestore_service.mark_domain_posted_today(
                domain=(firestore_service.get_job(job_id) or {}).get("genre", ""),
                job_id=job_id,
                headline=title,
            )
        ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
        if channel_id == "stories":
            stories_agent.send_post_result(
                title=title,
                url=url,
                public_id=public_id,
                live_date=ist_now.strftime("%Y-%m-%d"),
                live_time=ist_now.strftime("%I:%M %p IST"),
                mood=genre,
            )
        else:
            whatsapp_agent.send_post_result(
                title=title,
                url=url,
                public_id=public_id,
                live_date=ist_now.strftime("%Y-%m-%d"),
                live_time=ist_now.strftime("%I:%M %p IST"),
                domain=(firestore_service.get_job(job_id) or {}).get("genre", "") if job_id else "",
            )
    except Exception as notify_err:
        logger.exception(f"Post-result notification failed: {notify_err}")
        send_message(chat_id, f"✅ Posted to YouTube!\nPost Title: {title}\nPost Link: {url}")
    return url


def _finalize_pipeline_state(job_id: str, channel_id: str = "news"):
    """Mark pipeline state completed so the next scheduler run isn't blocked."""
    try:
        state = firestore_service.get_pipeline_state(channel_id=channel_id)
        batch_id = state.get("active_batch_id")
        if batch_id:
            firestore_service.set_pipeline_and_batch_state(batch_id, "completed", channel_id=channel_id)
    except Exception as e:
        logger.warning(f"Failed to finalize pipeline state: {e}")
