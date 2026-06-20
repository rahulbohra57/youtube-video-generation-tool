# app/services/long_video_service.py

import os
import logging
import numpy as np
from PIL import Image, ImageDraw, ImageFont

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
    AUDIO_FADE_IN,
    AUDIO_FADE_OUT,
)

LONG_VO_GAIN = 1.5  # higher than Shorts (1.08) — long-format needs clearer narration over B-roll

logger = logging.getLogger(__name__)

_TARGET_W = 1920
_TARGET_H = 1080

_TITLE_CARD_DURATION = 4.0
_TRANSITION_DURATION = 0.1

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
]


def _make_title_card(title: str, duration: float = _TITLE_CARD_DURATION, width: int = _TARGET_W, height: int = _TARGET_H) -> "ImageClip":
    """Create a black title card with centered white bold text."""
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    font_size = max(48, min(96, width // 18))
    font = None
    for fp in _FONT_PATHS:
        try:
            font = ImageFont.truetype(fp, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    max_w = int(width * 0.80)
    words = title.split()
    lines: list = []
    current: list = []
    for word in words:
        candidate = " ".join(current + [word])
        bb = draw.textbbox((0, 0), candidate, font=font)
        if (bb[2] - bb[0]) > max_w and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))

    line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + int(font_size * 0.2)
    block_h = len(lines) * line_h
    start_y = (height - block_h) // 2
    cx = width // 2

    for li, line in enumerate(lines):
        ty = start_y + li * line_h
        draw.text((cx + 2, ty + 2), line, font=font, fill=(80, 80, 80, 255), anchor="mt")
        draw.text((cx, ty), line, font=font, fill=(255, 255, 255, 255), anchor="mt")

    arr = np.array(img)
    return _clip_duration(ImageClip(arr), duration)


def _make_black_frame(duration: float = _TRANSITION_DURATION) -> "ImageClip":
    """Create a brief black frame for use as a transition between scenes."""
    arr = np.zeros((_TARGET_H, _TARGET_W, 3), dtype=np.uint8)
    return _clip_duration(ImageClip(arr), duration)


def create_long_video(
    clips: list[dict],
    output_path: str,
    music_genre: str = "general",
    language: str = "en",
    title: str = "",
) -> str:
    """Assemble a long-format 16:9 video from Pexels clips + TTS audio.

    clips: list of {"video_path": str, "audio_path": str, "narration": str}
    video_path may be "" — black frame used as fallback for that scene.
    title: optional headline — if non-empty, a title card is prepended.
    Returns output_path.
    """
    scene_clips = []

    for idx, item in enumerate(clips):
        audio_path = item["audio_path"]
        narration = item.get("narration", "")

        audio = AudioFileClip(audio_path)
        audio = _audio_fade_in(audio, AUDIO_FADE_IN)
        audio = _audio_fade_out(audio, AUDIO_FADE_OUT)
        total_duration = audio.duration

        # Support new clips_list format and old video_path format
        clips_list = item.get("clips_list")
        if clips_list is None:
            clips_list = [{"video_path": item.get("video_path", ""), "clip_duration": None}]

        sub_clips = []
        running = 0.0

        for clip_info in clips_list:
            video_path = clip_info.get("video_path", "")
            target_dur = clip_info.get("clip_duration") or (total_duration - running)
            remaining = total_duration - running
            if remaining <= 0.05:
                break
            actual_dur = min(target_dur, remaining)

            if video_path and os.path.exists(video_path):
                raw = VideoFileClip(video_path)
                if raw.duration >= actual_dur:
                    seg = _subclip(raw, 0, actual_dur)
                else:
                    loops_needed = int(actual_dur / raw.duration) + 1
                    looped = concatenate_videoclips([raw] * loops_needed)
                    seg = _subclip(looped, 0, actual_dur)
                seg = _fit_cover(seg, _TARGET_W, _TARGET_H)
            else:
                arr = np.zeros((_TARGET_H, _TARGET_W, 3), dtype=np.uint8)
                seg = _clip_duration(ImageClip(arr), actual_dur)

            sub_clips.append(seg)
            running += actual_dur

        # Pad any remaining duration with black if clips fall short
        if running < total_duration - 0.05 and sub_clips:
            arr = np.zeros((_TARGET_H, _TARGET_W, 3), dtype=np.uint8)
            sub_clips.append(_clip_duration(ImageClip(arr), total_duration - running))

        if not sub_clips:
            arr = np.zeros((_TARGET_H, _TARGET_W, 3), dtype=np.uint8)
            base = _clip_duration(ImageClip(arr), total_duration)
        elif len(sub_clips) == 1:
            base = sub_clips[0]
        else:
            base = concatenate_videoclips(sub_clips, method="compose")

        base = _clip_audio(base, audio)

        try:
            caption_clips = _make_word_caption_clips(
                narration, total_duration, _TARGET_W, _TARGET_H, language=language,
            )
            clip = CompositeVideoClip([base] + caption_clips) if caption_clips else base
        except Exception as cap_err:
            logger.warning("[LongVideo] Caption failed for scene %d: %s", idx, cap_err)
            clip = base

        scene_clips.append(clip)

    final_video = concatenate_videoclips(scene_clips, method="compose")
    vo_audio = _volume(_audio_fade_out(final_video.audio, 0.5), LONG_VO_GAIN)
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
