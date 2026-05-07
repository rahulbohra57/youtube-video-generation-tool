# app/services/video_service.py

try:
    from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, CompositeAudioClip
except Exception:
    from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, CompositeAudioClip
try:
    from moviepy.audio.fx import AudioFadeIn, AudioFadeOut, AudioLoop, MultiplyVolume
    _AUDIO_FX_MODE = "v2"
except Exception:
    from moviepy.audio.fx import all as afx
    _AUDIO_FX_MODE = "legacy"
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import os
import random

from app.config import (
    STORIES_ANIMATION_ENABLED,
    STORIES_ANIMATION_PROFILE,
    STORIES_MAX_SCENES_ANIMATED,
    STORIES_BROLL_ENABLED,
    STORIES_BROLL_MIN_VIRALITY,
)
from app.services.animation_service import create_animated_scene_clip, resolve_motion_hint

MUSIC_DIR  = "assets/music"
BG_VOLUME  = 0.15   # Keep ambience subtle so VO stays dominant.
VO_GAIN    = 1.08   # Slight boost to narration clarity.

AUDIO_FADE_IN  = 0.15
AUDIO_FADE_OUT = 0.35

# ─── Fonts ────────────────────────────────────────────────────────────────────
# Primary: platform-specific best fonts.
# DejaVu is installed via Dockerfile (fonts-dejavu-core) and is the production font.
# macOS fonts are listed as secondary so local dev still gets good rendering.
_FONT_EN  = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0)  # Linux / Cloud Run
_FONT_HI  = ("/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf", 0)  # Linux / Cloud Run (fonts-indic)
_FONT_HI_FALLBACKS = [
    ("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc", 0),
    ("/System/Library/Fonts/Supplemental/DevanagariMT.ttc", 0),
    ("/System/Library/Fonts/Supplemental/ITFDevanagari.ttc", 0),
    ("/System/Library/Fonts/Kohinoor.ttc", 3),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
]
_FONT_EN_FALLBACKS = [
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
]


_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}

def _load_font(size: int, language: str = "en") -> ImageFont.FreeTypeFont:
    key = (size, language)
    if key in _font_cache:
        return _font_cache[key]
    if language == "hi":
        candidates = [_FONT_HI] + _FONT_HI_FALLBACKS + _FONT_EN_FALLBACKS
    else:
        candidates = [_FONT_EN] + _FONT_EN_FALLBACKS + _FONT_HI_FALLBACKS
    for path, index in candidates:
        try:
            font = ImageFont.truetype(path, size, index=index)
            _font_cache[key] = font
            return font
        except Exception:
            continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# ─── Safe-zone constants ───────────────────────────────────────────────────────
# YouTube Shorts UI (username, likes, share) covers bottom ~26% + right ~14%.
# Landscape covers only ~9% at the bottom.
# Horizontal safe margin keeps text clear of the right-side button column.
_SHORTS_BOTTOM  = 0.30    # 30 % from bottom — portrait / Shorts
_NORMAL_BOTTOM  = 0.09    # 9 % from bottom  — landscape
_HORIZ_MARGIN   = 0.08    # 8 % from each side — both orientations


def _is_portrait(width: int, height: int) -> bool:
    return height > width


def _font_size(width: int, height: int) -> int:
    """Font size tuned for each orientation."""
    if _is_portrait(width, height):
        # 9:16 — readable on a phone, but not overwhelming
        return max(42, min(64, width // 13))
    else:
        # 16:9 — smaller; text competes with the wide image canvas
        return max(28, min(40, width // 38))


def _bottom_margin(width: int, height: int) -> int:
    ratio = _SHORTS_BOTTOM if _is_portrait(width, height) else _NORMAL_BOTTOM
    return int(height * ratio)


def _max_text_width(width: int, height: int) -> int:
    """Usable horizontal text width respecting both side margins."""
    return int(width * (1 - 2 * _HORIZ_MARGIN))


# ─── Line splitting ────────────────────────────────────────────────────────────

def _split_to_lines(words: list[str], font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """
    Split a list of words into at most 2 lines, each no wider than max_w.
    Tries every split point and picks the one that balances both lines best.
    """
    probe = Image.new("RGBA", (1, 1))
    draw  = ImageDraw.Draw(probe)

    def measure(wlist: list[str]) -> int:
        if not wlist:
            return 0
        bb = draw.textbbox((0, 0), " ".join(wlist), font=font)
        return bb[2] - bb[0]

    # Fits on one line?
    if measure(words) <= max_w:
        return [" ".join(words)]

    # Find the most balanced split where both halves fit
    best_split, best_imbalance = None, float("inf")
    for i in range(1, len(words)):
        w1 = measure(words[:i])
        w2 = measure(words[i:])
        if w1 <= max_w and w2 <= max_w:
            imbalance = abs(w1 - w2)
            if imbalance < best_imbalance:
                best_split, best_imbalance = i, imbalance

    if best_split is None:
        # Even a single word is too wide — fall back to simple half-split
        mid = max(1, len(words) // 2)
        best_split = mid

    return [" ".join(words[:best_split]), " ".join(words[best_split:])]


# ─── Guaranteed-fit chunker ───────────────────────────────────────────────────

def _chunk_to_fit(
    words: list[str], font: ImageFont.FreeTypeFont, max_w: int
) -> list[list[str]]:
    """
    Greedily split `words` into sub-lists where each sub-list, when passed to
    _split_to_lines, produces lines that all measure ≤ max_w pixels wide.
    Uses binary search per chunk for efficiency.
    Returns at least one chunk even if a single word exceeds max_w.
    """
    if not words:
        return []

    probe = Image.new("RGBA", (1, 1))
    draw  = ImageDraw.Draw(probe)

    def fits(wlist: list[str]) -> bool:
        lines = _split_to_lines(wlist, font, max_w)
        return all((draw.textbbox((0, 0), ln, font=font)[2]
                    - draw.textbbox((0, 0), ln, font=font)[0]) <= max_w
                   for ln in lines)

    result = []
    start  = 0
    n      = len(words)
    while start < n:
        lo, hi = 1, n - start
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fits(words[start : start + mid]):
                lo = mid
            else:
                hi = mid - 1
        result.append(words[start : start + lo])
        start += lo
    return result


# ─── Caption renderer ─────────────────────────────────────────────────────────

def _make_word_caption_clips(
    narration: str,
    total_duration: float,
    width: int,
    height: int,
    language: str = "en",
    animated_entry: bool = False,
) -> list:
    """
    Split narration into sentence chunks (at . ! ?), render each as a timed ImageClip.
    Bold text, dark stroke outline, no background pill.
    Respects YouTube Shorts safe zone (bottom + horizontal margins).
    Long sentences are wrapped to 2 lines automatically.
    Time is distributed proportionally by word count across sentences.
    """
    import re

    # Strip markdown formatting characters the LLM may include in narration
    # before rendering as plain subtitles.
    # Remove *bold* and _italic_ markers, keeping the inner text.
    clean = re.sub(r'\*([^*]+)\*', r'\1', narration)
    clean = re.sub(r'_([^_]+)_', r'\1', clean)
    # Remove single-quotes used as emphasis markers (at word boundaries) but
    # preserve apostrophes inside words like "Microsoft's" or "don't".
    # A quote is an emphasis marker if it is NOT between two word characters.
    clean = re.sub(r"(?<!\w)'|'(?!\w)", '', clean)

    # Split at sentence-ending punctuation, preserving the punctuation on the left side
    raw = re.split(r'(?<=[.!?])\s+', clean.strip())
    sentences = [s.strip() for s in raw if s.strip()]

    if not sentences:
        return []

    font_size  = _font_size(width, height)
    font       = _load_font(font_size, language)
    stroke_w   = max(2, font_size // 20)
    max_txt_w  = _max_text_width(width, height)
    margin_bot = _bottom_margin(width, height)
    cx         = width // 2

    # Measure line height from font
    probe      = Image.new("RGBA", (1, 1))
    probe_draw = ImageDraw.Draw(probe)
    bbox       = probe_draw.textbbox((0, 0), "Ag", font=font)
    line_h     = bbox[3] - bbox[1]
    line_gap   = int(line_h * 0.18)

    # Break each sentence into sub-chunks that are guaranteed to fit within max_txt_w
    all_chunks: list[list[str]] = []
    for sentence in sentences:
        all_chunks.extend(_chunk_to_fit(sentence.split(), font, max_txt_w))

    total_words = sum(len(c) for c in all_chunks)
    if total_words == 0:
        return []

    time_per_word = total_duration / total_words

    clips        = []
    current_time = 0.0

    for words in all_chunks:
        chunk_duration = len(words) * time_per_word
        lines          = _split_to_lines(words, font, max_txt_w)
        n_lines        = len(lines)
        block_h        = n_lines * line_h + (n_lines - 1) * line_gap

        # Anchor: bottom of text block sits at (height - margin_bot)
        ty_start = height - margin_bot - block_h

        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw   = ImageDraw.Draw(canvas)

        for i, line in enumerate(lines):
            ty = ty_start + i * (line_h + line_gap)
            # Dark stroke outline for contrast on any background
            draw.text(
                (cx, ty), line, font=font, anchor="mt",
                fill=(0, 0, 0, 0),
                stroke_width=stroke_w,
                stroke_fill=(20, 20, 20, 230),
            )
            # Bright white fill
            draw.text(
                (cx, ty), line, font=font, anchor="mt",
                fill=(255, 255, 255, 255),
            )

        arr = np.array(canvas)
        rgb_clip = ImageClip(arr[:, :, :3])
        alpha_clip = _clip_is_mask(ImageClip(arr[:, :, 3] / 255.0), True)
        caption_clip = _clip_duration(_clip_start(_clip_mask(rgb_clip, alpha_clip), current_time), chunk_duration)
        if animated_entry:
            try:
                caption_clip = _clip_position(
                    caption_clip,
                    lambda t, start=current_time: (
                        0,
                        int(-12 * max(0.0, 1.0 - min(1.0, (t - start) / 0.22))),
                    ),
                )
            except Exception:
                pass
        clips.append(caption_clip)
        current_time += chunk_duration

    return clips


# ─── Music picker ─────────────────────────────────────────────────────────────

_music_cache: dict[str, list[str]] = {}


def _clip_duration(clip, duration: float):
    return clip.with_duration(duration) if hasattr(clip, "with_duration") else clip.set_duration(duration)


def _clip_start(clip, start: float):
    return clip.with_start(start) if hasattr(clip, "with_start") else clip.set_start(start)


def _clip_audio(clip, audio):
    return clip.with_audio(audio) if hasattr(clip, "with_audio") else clip.set_audio(audio)


def _clip_mask(clip, mask):
    return clip.with_mask(mask) if hasattr(clip, "with_mask") else clip.set_mask(mask)


def _clip_is_mask(clip, is_mask: bool):
    return clip.with_is_mask(is_mask) if hasattr(clip, "with_is_mask") else clip.set_ismask(is_mask)


def _clip_position(clip, pos):
    if hasattr(clip, "with_position"):
        return clip.with_position(pos)
    return clip.set_position(pos) if hasattr(clip, "set_position") else clip.set_pos(pos)


def _crop_center(clip, x1: int, y1: int, x2: int, y2: int):
    if hasattr(clip, "cropped"):
        return clip.cropped(x1=x1, y1=y1, x2=x2, y2=y2)
    return clip.crop(x1=x1, y1=y1, x2=x2, y2=y2)


def _audio_fade_in(clip, duration: float):
    if _AUDIO_FX_MODE == "v2":
        return clip.with_effects([AudioFadeIn(duration)])
    return clip.fx(afx.audio_fadein, duration)


def _audio_fade_out(clip, duration: float):
    if _AUDIO_FX_MODE == "v2":
        return clip.with_effects([AudioFadeOut(duration)])
    return clip.fx(afx.audio_fadeout, duration)


def _audio_loop(clip, duration: float):
    if _AUDIO_FX_MODE == "v2":
        return clip.with_effects([AudioLoop(duration=duration)])
    return clip.fx(afx.audio_loop, duration=duration)


def _volume(clip, factor: float):
    if hasattr(clip, "with_volume_scaled"):
        return clip.with_volume_scaled(factor)
    if _AUDIO_FX_MODE == "v2":
        return clip.with_effects([MultiplyVolume(factor)])
    return clip.fx(afx.volumex, factor)


def _subclip(clip, start: float, end: float):
    return clip.subclipped(start, end) if hasattr(clip, "subclipped") else clip.subclip(start, end)


def _fit_cover(clip, target_w: int, target_h: int):
    """Scale and center-crop clip so it fully covers target frame (no letter/pillarboxing)."""
    if clip.w <= 0 or clip.h <= 0 or target_w <= 0 or target_h <= 0:
        return clip
    scale = max(target_w / clip.w, target_h / clip.h)
    resized = _clip_resize(clip, scale) if "_clip_resize" in globals() else (clip.resized(scale) if hasattr(clip, "resized") else clip.resize(scale))
    x1 = int(max(0, (resized.w - target_w) // 2))
    y1 = int(max(0, (resized.h - target_h) // 2))
    return _crop_center(resized, x1=x1, y1=y1, x2=x1 + target_w, y2=y1 + target_h)

def _tracks_in(directory: str) -> list[str]:
    if directory not in _music_cache:
        _music_cache[directory] = [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if f.lower().endswith(('.mp3', '.wav', '.ogg', '.m4a'))
        ] if os.path.isdir(directory) else []
    return _music_cache[directory]


def _pick_music(genre: str = "general") -> str | None:
    if not os.path.isdir(MUSIC_DIR):
        return None

    if genre and genre.lower() != "general":
        genre_dir = os.path.join(MUSIC_DIR, genre)
        genre_tracks = _tracks_in(genre_dir)
        if genre_tracks:
            return random.choice(genre_tracks)

    root_tracks = _tracks_in(MUSIC_DIR)
    return random.choice(root_tracks) if root_tracks else None


# ─── Main entry point ─────────────────────────────────────────────────────────

def _normalize_clip_item(item):
    if isinstance(item, dict):
        return {
            "image_path": item.get("image_path", ""),
            "audio_path": item.get("audio_path", ""),
            "narration": item.get("narration", ""),
            "motion_type": item.get("motion_type", ""),
            "camera_path": item.get("camera_path", ""),
            "focus_subject": item.get("focus_subject", ""),
            "transition": item.get("transition", ""),
            "effect_cue": item.get("effect_cue", ""),
        }
    image_path, audio_path, narration = item
    return {
        "image_path": image_path,
        "audio_path": audio_path,
        "narration": narration,
        "motion_type": "",
        "camera_path": "",
        "focus_subject": "",
        "transition": "",
        "effect_cue": "",
    }


def create_video(
    clips,
    output_path,
    music_genre: str = "general",
    language: str = "en",
    channel_id: str = "news",
    story_genre: str = "",
    virality_score: float = 0.0,
):
    """
    clips: list of (image_path, audio_path, narration_text) tuples
    """
    video_clips = []

    normalized = [_normalize_clip_item(c) for c in clips]
    target_w = 1080 if channel_id == "stories" else 0
    target_h = 1920 if channel_id == "stories" else 0

    # Phase-2 scaffold: when enabled and virality is high enough, this gate can
    # route selected scenes to an AI video/B-roll provider. V1 keeps 2.5D motion.
    use_broll_path = (
        channel_id == "stories"
        and STORIES_BROLL_ENABLED
        and float(virality_score or 0.0) >= STORIES_BROLL_MIN_VIRALITY
    )
    if use_broll_path:
        print("ℹ️ B-roll gate active; no provider configured yet, using 2.5D animation fallback.")

    for idx, item in enumerate(normalized):
        image_path = item["image_path"]
        audio_path = item["audio_path"]
        narration = item["narration"]
        audio = AudioFileClip(audio_path)
        audio = _audio_fade_in(audio, AUDIO_FADE_IN)
        audio = _audio_fade_out(audio, AUDIO_FADE_OUT)

        use_animation = (
            channel_id == "stories"
            and STORIES_ANIMATION_ENABLED
            and idx < max(0, STORIES_MAX_SCENES_ANIMATED)
        )
        if use_animation:
            try:
                hint = resolve_motion_hint(item, idx, genre=story_genre, profile=STORIES_ANIMATION_PROFILE)
                base = create_animated_scene_clip(
                    image_path=image_path,
                    duration=audio.duration,
                    motion_hint=hint,
                    profile=STORIES_ANIMATION_PROFILE,
                )
                base = _clip_audio(base, audio)
            except Exception as anim_err:
                print(f"⚠️ Animation failed for scene {idx}, falling back to static: {anim_err}")
                base = _clip_audio(_clip_duration(ImageClip(image_path), audio.duration), audio)
        else:
            base = _clip_audio(_clip_duration(ImageClip(image_path), audio.duration), audio)

        # Enforce full-frame output so every scene fills Shorts frame edge-to-edge.
        if target_w <= 0 or target_h <= 0:
            target_w, target_h = int(base.w), int(base.h)
        base = _fit_cover(base, target_w, target_h)

        try:
            caption_clips = _make_word_caption_clips(
                narration,
                audio.duration,
                base.w,
                base.h,
                language=language,
                animated_entry=use_animation,
            )
            clip = CompositeVideoClip([base] + caption_clips) if caption_clips else base
        except Exception as e:
            print(f"⚠️ Caption overlay failed, skipping: {e}")
            clip = base

        video_clips.append(clip)

    final_video = concatenate_videoclips(video_clips, method="compose")
    vo_audio = _volume(_audio_fade_out(final_video.audio, 0.5), VO_GAIN)
    final_video = _clip_audio(final_video, vo_audio)

    music_path = _pick_music(music_genre)
    if music_path:
        try:
            bg       = AudioFileClip(music_path)
            duration = final_video.duration

            # Skip silent intros by starting from a later point when possible.
            start_offset = min(12.0, max(0.0, (bg.duration - duration) * 0.25))
            if bg.duration < duration:
                bg = _audio_loop(bg, duration=duration)
            else:
                bg = _subclip(bg, start_offset, start_offset + duration)

            bg       = _volume(_audio_fade_out(bg, 1.0), BG_VOLUME)
            mixed    = CompositeAudioClip([vo_audio, bg])
            final_video = _clip_audio(final_video, mixed)
            print(f"🎵 Background music [{music_genre}]: {os.path.basename(music_path)}")
        except Exception as e:
            print(f"⚠️ Background music skipped: {e}")

    # Derive a unique temp audio path from the output path so concurrent calls
    # (or parallel test runs) never collide on the same temp file.
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
