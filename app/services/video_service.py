# app/services/video_service.py

from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, CompositeAudioClip
from moviepy.audio.fx import AudioFadeIn, AudioFadeOut, AudioLoop, MultiplyVolume
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import os
import random

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
_FONT_HI  = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0)  # Hindi fallback (no Devanagari in DejaVu, but readable)
_FONT_FALLBACKS = [
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),  # macOS dev
    ("/System/Library/Fonts/Kohinoor.ttc",      3),  # macOS Hindi dev
    ("/Library/Fonts/Arial Unicode.ttf",        0),  # macOS Unicode
]


def _load_font(size: int, language: str = "en") -> ImageFont.FreeTypeFont:
    primary = _FONT_HI if language == "hi" else _FONT_EN
    for path, index in [primary] + _FONT_FALLBACKS:
        try:
            return ImageFont.truetype(path, size, index=index)
        except Exception:
            continue
    return ImageFont.load_default()


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
) -> list:
    """
    Split narration into sentence chunks (at . ! ?), render each as a timed ImageClip.
    Bold text, dark stroke outline, no background pill.
    Respects YouTube Shorts safe zone (bottom + horizontal margins).
    Long sentences are wrapped to 2 lines automatically.
    Time is distributed proportionally by word count across sentences.
    """
    import re

    # Split at sentence-ending punctuation, preserving the punctuation on the left side
    raw = re.split(r'(?<=[.!?])\s+', narration.strip())
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
        caption_clip = (
            ImageClip(arr[:, :, :3])
            .with_mask(ImageClip(arr[:, :, 3] / 255.0).with_is_mask(True))
            .with_start(current_time)
            .with_duration(chunk_duration)
        )
        clips.append(caption_clip)
        current_time += chunk_duration

    return clips


# ─── Music picker ─────────────────────────────────────────────────────────────

def _pick_music(genre: str = "general") -> str | None:
    if not os.path.isdir(MUSIC_DIR):
        return None

    def tracks_in(directory: str) -> list[str]:
        return [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if f.lower().endswith(('.mp3', '.wav', '.ogg', '.m4a'))
        ]

    if genre and genre.lower() != "general":
        genre_dir    = os.path.join(MUSIC_DIR, genre)
        genre_tracks = tracks_in(genre_dir) if os.path.isdir(genre_dir) else []
        if genre_tracks:
            return random.choice(genre_tracks)

    root_tracks = tracks_in(MUSIC_DIR)
    return random.choice(root_tracks) if root_tracks else None


# ─── Main entry point ─────────────────────────────────────────────────────────

def create_video(clips, output_path, music_genre: str = "general", language: str = "en"):
    """
    clips: list of (image_path, audio_path, narration_text) tuples
    """
    video_clips = []

    for image_path, audio_path, narration in clips:
        audio = AudioFileClip(audio_path)
        audio = audio.with_effects([AudioFadeIn(AUDIO_FADE_IN)])
        audio = audio.with_effects([AudioFadeOut(AUDIO_FADE_OUT)])

        base = (
            ImageClip(image_path)
            .with_duration(audio.duration)
            .with_audio(audio)
        )

        try:
            caption_clips = _make_word_caption_clips(
                narration, audio.duration, base.w, base.h, language=language
            )
            clip = CompositeVideoClip([base] + caption_clips) if caption_clips else base
        except Exception as e:
            print(f"⚠️ Caption overlay failed, skipping: {e}")
            clip = base

        video_clips.append(clip)

    final_video = concatenate_videoclips(video_clips, method="compose")
    vo_audio = final_video.audio.with_effects([AudioFadeOut(0.5)]).with_volume_scaled(VO_GAIN)
    final_video = final_video.with_audio(vo_audio)

    music_path = _pick_music(music_genre)
    if music_path:
        try:
            bg       = AudioFileClip(music_path)
            duration = final_video.duration

            # Skip silent intros by starting from a later point when possible.
            start_offset = min(12.0, max(0.0, (bg.duration - duration) * 0.25))
            if bg.duration < duration:
                bg = bg.with_effects([AudioLoop(duration=duration)])
            else:
                bg = bg.subclipped(start_offset, start_offset + duration)

            bg       = bg.with_effects([AudioFadeOut(1.0)]).with_volume_scaled(BG_VOLUME)
            mixed    = CompositeAudioClip([vo_audio, bg])
            final_video = final_video.with_audio(mixed)
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
