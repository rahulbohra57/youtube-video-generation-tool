# app/services/long_video_service.py

import os
import logging
import numpy as np

try:
    from moviepy import (
        VideoFileClip, AudioFileClip, ImageClip,
        concatenate_videoclips, CompositeVideoClip, CompositeAudioClip,
    )
except Exception:
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, ImageClip,
        concatenate_videoclips, CompositeVideoClip, CompositeAudioClip,
    )

from app.services.video_service import (
    _make_word_caption_clips,
    _pick_music,
    _audio_fade_in,
    _audio_fade_out,
    _audio_loop,
    _volume,
    _subclip,
    _fit_cover,
    _clip_audio,
    _clip_duration,
    BG_VOLUME,
    VO_GAIN,
    AUDIO_FADE_IN,
    AUDIO_FADE_OUT,
)

logger = logging.getLogger(__name__)

_TARGET_W = 1920
_TARGET_H = 1080


def create_long_video(
    clips: list[dict],
    output_path: str,
    music_genre: str = "general",
    language: str = "en",
) -> str:
    """Assemble a long-format 16:9 video from Pexels clips + TTS audio.

    clips: list of {"video_path": str, "audio_path": str, "narration": str}
    video_path may be "" — black frame used as fallback for that scene.
    Returns output_path.
    """
    scene_clips = []

    for idx, item in enumerate(clips):
        video_path = item.get("video_path", "")
        audio_path = item["audio_path"]
        narration = item.get("narration", "")

        audio = AudioFileClip(audio_path)
        audio = _audio_fade_in(audio, AUDIO_FADE_IN)
        audio = _audio_fade_out(audio, AUDIO_FADE_OUT)
        duration = audio.duration

        if video_path and os.path.exists(video_path):
            raw = VideoFileClip(video_path)
            if raw.duration >= duration:
                base = _subclip(raw, 0, duration)
            else:
                loops_needed = int(duration / raw.duration) + 1
                looped = concatenate_videoclips([raw] * loops_needed)
                base = _subclip(looped, 0, duration)
            base = _fit_cover(base, _TARGET_W, _TARGET_H)
        else:
            arr = np.zeros((_TARGET_H, _TARGET_W, 3), dtype=np.uint8)
            base = _clip_duration(ImageClip(arr), duration)

        base = _clip_audio(base, audio)

        try:
            caption_clips = _make_word_caption_clips(
                narration, duration, _TARGET_W, _TARGET_H, language=language
            )
            clip = CompositeVideoClip([base] + caption_clips) if caption_clips else base
        except Exception as cap_err:
            logger.warning("[LongVideo] Caption failed for scene %d: %s", idx, cap_err)
            clip = base

        scene_clips.append(clip)

    final_video = concatenate_videoclips(scene_clips, method="compose")
    vo_audio = _volume(_audio_fade_out(final_video.audio, 0.5), VO_GAIN)
    final_video = _clip_audio(final_video, vo_audio)

    music_path = _pick_music(music_genre)
    if music_path:
        try:
            bg = AudioFileClip(music_path)
            total_duration = final_video.duration
            if bg.duration < total_duration:
                bg = _audio_loop(bg, duration=total_duration)
            else:
                start_offset = min(12.0, max(0.0, (bg.duration - total_duration) * 0.25))
                bg = _subclip(bg, start_offset, start_offset + total_duration)
            bg = _volume(_audio_fade_out(bg, 1.0), BG_VOLUME)
            mixed = CompositeAudioClip([vo_audio, bg])
            final_video = _clip_audio(final_video, mixed)
            logger.info("[LongVideo] Background music [%s]: %s", music_genre, os.path.basename(music_path))
        except Exception as music_err:
            logger.warning("[LongVideo] Background music skipped: %s", music_err)

    temp_audio = os.path.splitext(output_path)[0] + "_temp_audio.m4a"
    final_video.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=temp_audio,
        remove_temp=True,
    )
    return output_path
