from __future__ import annotations

import math
from typing import Any

try:
    from moviepy import CompositeVideoClip, ImageClip
except Exception:
    from moviepy.editor import CompositeVideoClip, ImageClip


_GENRE_MOTION_MAP = {
    "inspiring": "ken_burns",
    "heartfelt": "parallax",
    "comedy": "pop_in",
    "crime": "push",
    "action": "whip",
    "sci-fi": "parallax",
    "mythology": "ken_burns",
    "thriller": "push",
    "mystery": "parallax",
    "adventure": "ken_burns",
    "slice-of-life": "pop_in",
    "historical": "ken_burns",
}

_TRANSITIONS = ("dissolve", "push", "flash_cut", "whip")


def _clip_duration(clip, duration: float):
    return clip.with_duration(duration) if hasattr(clip, "with_duration") else clip.set_duration(duration)


def _clip_position(clip, pos):
    if hasattr(clip, "with_position"):
        return clip.with_position(pos)
    return clip.set_position(pos) if hasattr(clip, "set_position") else clip.set_pos(pos)


def _clip_resize(clip, factor_or_fn):
    return clip.resized(factor_or_fn) if hasattr(clip, "resized") else clip.resize(factor_or_fn)


def resolve_motion_hint(
    scene: dict[str, Any],
    idx: int,
    genre: str = "",
    profile: str = "standard",
) -> dict[str, str]:
    motion_type = (scene.get("motion_type") or "").strip().lower()
    if not motion_type:
        motion_type = _GENRE_MOTION_MAP.get((genre or "").strip().lower(), "ken_burns")

    camera_path = (scene.get("camera_path") or "").strip().lower()
    if not camera_path:
        camera_path = ("left_to_right" if idx % 2 == 0 else "right_to_left")

    focus_subject = (scene.get("focus_subject") or "").strip() or "main character"
    transition = (scene.get("transition") or "").strip().lower()
    if not transition:
        transition = _TRANSITIONS[idx % len(_TRANSITIONS)]
    effect_cue = (scene.get("effect_cue") or "").strip().lower()
    if not effect_cue:
        effect_cue = "subtle glow" if profile == "standard" else "none"

    return {
        "motion_type": motion_type,
        "camera_path": camera_path,
        "focus_subject": focus_subject,
        "transition": transition,
        "effect_cue": effect_cue,
    }


def _ken_burns(clip: ImageClip, duration: float, camera_path: str, intensity: float) -> CompositeVideoClip:
    zoom_base = 1.0 + intensity

    def scale_at(t: float) -> float:
        if duration <= 0:
            return zoom_base
        return 1.0 + (zoom_base - 1.0) * min(1.0, max(0.0, t / duration))

    max_offset = int(clip.w * 0.08 * intensity / 0.08) if clip.w else 0
    if "right_to_left" in camera_path:
        x_fn = lambda t: int(max_offset * (1.0 - min(1.0, max(0.0, t / max(duration, 0.001)))))
    else:
        x_fn = lambda t: int(max_offset * min(1.0, max(0.0, t / max(duration, 0.001))))

    animated = _clip_position(_clip_resize(clip, scale_at), lambda t: (-x_fn(t), 0))
    return _clip_duration(CompositeVideoClip([animated], size=(clip.w, clip.h)), duration)


def _parallax(clip: ImageClip, duration: float, camera_path: str) -> CompositeVideoClip:
    bg = _clip_resize(clip, 1.12)
    fg = _clip_resize(clip, 1.03)

    drift = int(max(6, clip.w * 0.035))
    sign = -1 if "right_to_left" in camera_path else 1

    def bg_x(t: float) -> float:
        return sign * drift * (t / max(duration, 0.001))

    def fg_x(t: float) -> float:
        return -sign * (drift * 0.35) * (t / max(duration, 0.001))

    composed = CompositeVideoClip(
        [
            _clip_position(bg, lambda t: (bg_x(t) - (bg.w - clip.w) / 2, -(bg.h - clip.h) / 2)),
            _clip_position(fg, lambda t: (fg_x(t) - (fg.w - clip.w) / 2, -(fg.h - clip.h) / 2)),
        ],
        size=(clip.w, clip.h),
    )
    return _clip_duration(composed, duration)


def _pop_in(clip: ImageClip, duration: float) -> CompositeVideoClip:
    def scale_at(t: float) -> float:
        if duration <= 0:
            return 1.0
        normalized = min(1.0, t / max(0.5, duration * 0.35))
        # soft overshoot easing
        overshoot = 1.06 * math.sin(normalized * math.pi * 0.5)
        return 0.96 + 0.06 * normalized + (overshoot - 1.0) * 0.08

    animated = _clip_position(_clip_resize(clip, scale_at), "center")
    return _clip_duration(CompositeVideoClip([animated], size=(clip.w, clip.h)), duration)


def _dynamic_pan(clip: ImageClip, duration: float, camera_path: str) -> CompositeVideoClip:
    # Stronger “animated” feel: simultaneous drift + breathing zoom + vertical sway.
    drift_x = int(max(10, clip.w * 0.05))
    drift_y = int(max(6, clip.h * 0.02))
    sign = -1 if "right_to_left" in camera_path else 1

    def scale_at(t: float) -> float:
        phase = min(1.0, t / max(duration, 0.001))
        return 1.04 + 0.04 * math.sin(phase * math.pi)

    animated = _clip_resize(clip, scale_at)
    animated = _clip_position(
        animated,
        lambda t: (
            sign * drift_x * (t / max(duration, 0.001)) - int((animated.w - clip.w) / 2),
            int(math.sin((t / max(duration, 0.001)) * 2 * math.pi) * drift_y) - int((animated.h - clip.h) / 2),
        ),
    )
    return _clip_duration(CompositeVideoClip([animated], size=(clip.w, clip.h)), duration)


def _with_transition(clip: CompositeVideoClip, transition: str) -> CompositeVideoClip:
    # Keep transition field for future cross-scene compositing.
    # Scene-level rendering stays self-contained in V1.
    _ = transition
    return clip


def create_animated_scene_clip(
    image_path: str,
    duration: float,
    motion_hint: dict[str, Any] | None = None,
    profile: str = "standard",
):
    base = _clip_duration(ImageClip(image_path), duration)
    hint = motion_hint or {}
    motion_type = (hint.get("motion_type") or "ken_burns").strip().lower()
    camera_path = (hint.get("camera_path") or "left_to_right").strip().lower()
    transition = (hint.get("transition") or "dissolve").strip().lower()

    intensity = 0.08 if profile == "standard" else 0.05
    if motion_type == "parallax":
        animated = _parallax(base, duration, camera_path)
    elif motion_type == "pop_in":
        animated = _pop_in(base, duration)
    elif motion_type in ("dynamic_pan", "push"):
        animated = _dynamic_pan(base, duration, camera_path)
    else:
        animated = _ken_burns(base, duration, camera_path, intensity)

    return _clip_duration(_with_transition(animated, transition), duration)
