# app/services/image_service.py

import os
import vertexai
from vertexai.preview.vision_models import ImageGenerationModel
from app.config import TEMP_DIR
from app.utils.helpers import ensure_dir
import time
import hashlib
from PIL import Image, ImageDraw

# imagen-3.0-generate-002 is the latest Imagen 3 model (Jan 2025) with the
# highest image quality and prompt adherence. 20 QPM quota is sufficient for
# 5-scene videos. Exponential backoff handles occasional rate-limit spikes.
MODEL_NAME = "imagen-3.0-generate-002"
_GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "project-2cf07291-e18f-4a4e-ad2")
_GCP_LOCATION = "us-central1"

ensure_dir(TEMP_DIR)

_model = None


def _get_model() -> ImageGenerationModel:
    global _model
    if _model is None:
        vertexai.init(project=_GCP_PROJECT, location=_GCP_LOCATION)
        _model = ImageGenerationModel.from_pretrained(MODEL_NAME)
    return _model

# Retry delays (seconds) for Imagen quota / rate-limit errors (429).
# Quota window is 1 minute, so each wait must be long enough for the bucket
# to refill before the next attempt.
_QUOTA_RETRY_DELAYS = [30, 60, 120]

# Prefix added to exceptions that the caller should NOT retry
# (same prompt → same rejection, so outer retries are wasted).
SAFETY_FILTER_ERROR_PREFIX = "imagen_safety_filter:"


def _first_generated_image(images_response):
    """Return first generated image object across SDK response shapes."""
    if images_response is None:
        return None

    nested = getattr(images_response, "images", None)
    if nested:
        try:
            return nested[0]
        except (IndexError, Exception):
            # SDK wrapper is truthy but empty — treat as filtered response.
            return None

    try:
        if len(images_response) > 0:
            return images_response[0]
    except Exception:
        pass

    return None


def generate_image(prompt: str, idx: int, aspect_ratio: str = "16:9") -> str:
    """Generate one image for a scene. Returns the local file path.

    Retry behaviour:
    - Quota / rate-limit (429): up to 3 retries with increasing waits (30s / 60s / 120s).
    - Safety-filter rejection (empty response): raises immediately with
      SAFETY_FILTER_ERROR_PREFIX so the caller can skip retrying.
    - Other errors: raises immediately (auth, bad prompt shape, etc.).
    """
    GLOBAL_STYLE = """
    animated explainer video, flat design,
    consistent color palette, modern UI style,
    clean vector illustration
    """

    if aspect_ratio == "9:16":
        style_hint = (
            "vertical short-form video, portrait orientation, "
            "YouTube Shorts style, high quality"
        )
    else:
        style_hint = (
            "youtube educational thumbnail style, "
            "high quality, cinematic lighting, 16:9"
        )

    enhanced_prompt = f"""
    {prompt}, {GLOBAL_STYLE}
    style: animated explainer video,
    flat design, consistent color palette,
    {style_hint}
    """

    last_exc: Exception | None = None

    for attempt, wait in enumerate(_QUOTA_RETRY_DELAYS, start=1):
        try:
            images = _get_model().generate_images(
                prompt=enhanced_prompt,
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                negative_prompt=(
                    "trademark logo, brand logo, embedded text, readable words, "
                    "captions, watermark, text overlay, subtitles"
                ),
                safety_filter_level="block_few",
                person_generation="allow_adult",
            )

            first_image = _first_generated_image(images)
            if first_image is None:
                # Safety / content-policy filter: Imagen accepted the request but
                # returned zero images. Retrying the same prompt will produce the
                # same result — raise with a detectable prefix so the caller skips
                # outer retries for this scene.
                raise Exception(
                    f"{SAFETY_FILTER_ERROR_PREFIX} Imagen returned 0 images for scene {idx} "
                    "(prompt blocked by safety or content policy)"
                )

            path = f"{TEMP_DIR}/scene_{idx}.png"
            first_image.save(location=path)
            return path

        except Exception as e:
            err = str(e)
            is_rate_limit = "429" in err or "quota" in err.lower() or "resource exhausted" in err.lower()
            # SDK sometimes raises IndexError instead of returning an empty list
            # when a response is filtered. Treat it the same as a safety filter.
            is_index_error = isinstance(e, IndexError) or "list index out of range" in err

            if err.startswith(SAFETY_FILTER_ERROR_PREFIX):
                # Never retry safety-filter rejections — the same prompt = same block.
                raise

            if is_index_error:
                raise Exception(
                    f"{SAFETY_FILTER_ERROR_PREFIX} Imagen SDK raised IndexError for scene {idx} "
                    "(likely empty/filtered response)"
                )

            if is_rate_limit:
                print(f"Retry {attempt} failed (rate limit – waiting {wait}s): {e}")
                last_exc = e
                time.sleep(wait)
            else:
                # Unexpected non-quota error — raise immediately.
                raise

    raise Exception(
        f"Image generation failed after {len(_QUOTA_RETRY_DELAYS)} quota retries using {MODEL_NAME}: {last_exc}"
    )


import logging as _logging
_logger = _logging.getLogger(__name__)

_THUMBNAIL_POWER_WORDS = {
    "LIE", "LIES", "LIED", "LYING", "WRONG", "DEAD", "NEVER", "ALWAYS",
    "SECRET", "SECRETS", "FAKE", "REAL", "TRUE", "TRUTH", "HIDDEN",
    "ACTUALLY", "REALLY", "SHOCKING", "IMPOSSIBLE", "FORBIDDEN", "EXPOSED",
    "BANNED", "STOLEN", "PROVEN", "MYTH", "DANGEROUS", "TERRIFYING", "HATE",
}


import re as _re

_LIST_NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
_LIST_KEYWORDS = {"ways", "habits", "laws", "rules", "tips", "secrets", "things",
                  "reasons", "steps", "facts", "signs", "lessons", "mistakes", "hacks"}


def _is_list_topic(headline: str) -> bool:
    """True when the headline is a numbered-list format ('9 Habits', 'Five Ways')."""
    lower = headline.lower().strip()
    words = lower.split()
    if not words:
        return False
    if words[0].isdigit():
        return True
    if words[0] in _LIST_NUMBER_WORDS:
        return True
    if _re.search(r'\b\d+\b', lower) and any(kw in lower for kw in _LIST_KEYWORDS):
        return True
    return False


def _image_saturation_score(path: str) -> float:
    """Return pixel std-dev as a proxy for visual variety/saturation (higher = more vivid)."""
    import numpy as np
    arr = _np_array_from_image(path)
    return float(np.std(arr))


def _np_array_from_image(path: str):
    import numpy as np
    return np.array(Image.open(path).convert("RGB"), dtype=float)


_CATEGORY_BORDER_COLORS: dict[str, tuple[int, int, int]] = {
    "science & space":              (100, 180, 255),
    "history & civilizations":      (210, 170,  50),
    "human body & biology":         (255,  80,  80),
    "technology & ai":              ( 80, 200, 255),
    "health & fitness":             ( 80, 200,  80),
    "psychology & dark psychology": (160,  80, 220),
    "relationships & dating":       (255, 120, 160),
    "self-improvement & habits":    (255, 160,  40),
    "business & finance":           ( 40, 180, 120),
    "culture & society":            (255, 200,  60),
    "philosophy & life":            (180, 130, 255),
    "mysteries & unexplained":      (150, 255, 200),
}
_DEFAULT_BORDER_COLOR: tuple[int, int, int] = (255, 200, 0)

_EMOTION_FACE_MAP: dict[str, str] = {
    "curious":    "wide-eyed wonder, slightly open mouth, leaning forward",
    "shocked":    "jaw dropped, eyes wide open, hand on cheek",
    "amused":     "big grin, raised eyebrows, eyes crinkling",
    "motivated":  "determined jaw, intense focused eyes, fist clenched",
    "unsettled":  "furrowed brow, one eyebrow raised, suspicious glance",
    "fascinated": "eyes lit up, leaning forward, lips parted in interest",
    "awed":       "looking upward with awe, mouth slightly open",
    "amazed":     "wide eyes, both hands on cheeks, stunned expression",
    "determined": "steely eyes, firm jaw, confident stance",
}
_DEFAULT_FACE_EMOTION = "wide-eyed curiosity, slight open mouth"


def _add_thumbnail_border(image_path: str, color: tuple[int, int, int] = _DEFAULT_BORDER_COLOR, thickness: int = 8) -> None:
    """Draw a thin coloured border around the entire thumbnail in-place."""
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for i in range(thickness):
        draw.rectangle([i, i, w - 1 - i, h - 1 - i], outline=color)
    img.save(image_path, "PNG")


def _pick_highlight_word(hook_text: str) -> str:
    """Pick the single most impactful word from hook_text to render in yellow.

    Priority: power word → numeric token → longest word.
    Returns the word stripped of punctuation, uppercase.
    """
    words = hook_text.upper().split()
    stripped = [w.strip(".,!?:;") for w in words]
    for w in stripped:
        if w in _THUMBNAIL_POWER_WORDS:
            return w
    for w in stripped:
        if w.isdigit():
            return w
    return max(stripped, key=len, default=stripped[0] if stripped else "")


def generate_thumbnail(
    prompt: str,
    code: str,
    hook_text: str = "",
    emotion: str = "",
    headline: str = "",
    category: str = "",
) -> str:
    """Generate a 16:9 thumbnail using Imagen 3. Returns local .png path.

    Generates 2 variants and picks the more vivid one (highest pixel std-dev).
    Applies a category-coloured border and overlays hook_text with bottom-anchored
    bold text using black stroke (no bg patch).
    """
    ensure_dir(TEMP_DIR)
    output_path = os.path.join(TEMP_DIR, f"thumbnail_{code}.png")

    face_expr = _EMOTION_FACE_MAP.get(emotion.lower(), _DEFAULT_FACE_EMOTION)
    is_list = _is_list_topic(headline) if headline else False

    hook_hint = (
        f"The overall image must visually represent this concept: '{hook_text}'. "
        if hook_text else ""
    )
    if is_list:
        composition = (
            "Split-panel composition: left panel depicts the 'before' or problem state, "
            "right panel depicts the 'after' or solution, separated by a bold vertical dividing line. "
        )
    else:
        composition = (
            f"Foreground: extreme close-up of an expressive illustrated human face showing "
            f"{face_expr}, occupying the left 55% of the frame. "
            "Background: rich thematic scene matching the topic. "
        )

    full_prompt = (
        f"{prompt} "
        f"{hook_hint}"
        f"{composition}"
        "Bold flat illustration style, vivid high-contrast colors "
        "(electric red or orange or yellow or royal blue background), "
        "single clear focal point, striking composition readable at thumbnail size. "
        "No text, no words, no letters, no logos, no watermarks, no captions."
    )
    negative = "text, words, letters, numbers, logos, captions, subtitles, watermarks, signs, typography"

    best_path = output_path
    best_score = -1.0

    for variant_idx in range(2):
        variant_path = os.path.join(TEMP_DIR, f"thumbnail_{code}_v{variant_idx}.png")
        for delay in _QUOTA_RETRY_DELAYS + [None]:
            try:
                images = _get_model().generate_images(
                    prompt=full_prompt,
                    number_of_images=1,
                    aspect_ratio="16:9",
                    safety_filter_level="block_few",
                    person_generation="allow_adult",
                    negative_prompt=negative,
                )
                img = _first_generated_image(images)
                if img is None:
                    raise RuntimeError("Imagen returned no images for thumbnail")
                img.save(variant_path)
                score = _image_saturation_score(variant_path)
                if score > best_score:
                    best_score = score
                    best_path = variant_path
                break
            except Exception as exc:
                if delay is None:
                    if variant_idx == 0:
                        raise
                    break  # second variant failed — keep first
                err = str(exc).lower()
                if any(kw in err for kw in ("quota", "429", "resource_exhausted")):
                    _logger.warning("[Thumbnail] Quota error, waiting %ds: %s", delay, exc)
                    time.sleep(delay)
                else:
                    if variant_idx == 0:
                        raise
                    break

    if best_path != output_path:
        import shutil
        shutil.copy2(best_path, output_path)

    border_color = _CATEGORY_BORDER_COLORS.get(category.lower(), _DEFAULT_BORDER_COLOR)
    try:
        _add_thumbnail_border(output_path, color=border_color)
    except Exception as border_err:
        _logger.warning("[Thumbnail] Border failed (non-fatal): %s", border_err)

    if hook_text:
        try:
            _add_thumbnail_text_overlay(output_path, hook_text.strip().upper())
        except Exception as overlay_err:
            _logger.warning("[Thumbnail] Text overlay failed (non-fatal): %s", overlay_err)

    return output_path


def _add_thumbnail_text_overlay(image_path: str, hook_text: str) -> None:
    """Overlay bold hook text at the bottom of the thumbnail.

    No background patch — uses thick black stroke only for contrast.
    The most impactful word is rendered in yellow; all others in white.
    Font size scales inversely with word count so short hooks appear massive.
    """
    from PIL import ImageFont

    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    draw = ImageDraw.Draw(img, "RGBA")

    word_count = len(hook_text.split())
    if word_count <= 3:
        font_size = max(80, min(130, width // 12))
    elif word_count == 4:
        font_size = max(68, min(110, width // 14))
    else:
        font_size = max(56, min(90, width // 17))

    _FONT_PATHS = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    font = None
    for fp in _FONT_PATHS:
        try:
            font = ImageFont.truetype(fp, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    highlight_word = _pick_highlight_word(hook_text)
    max_text_w = int(width * 0.90)
    stroke_w = max(5, font_size // 8)

    # Word-wrap
    words = hook_text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        bb = draw.textbbox((0, 0), candidate, font=font)
        if (bb[2] - bb[0]) > max_text_w and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))

    line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + int(font_size * 0.12)
    block_h = len(lines) * line_h
    bottom_anchor = int(height * 0.93)
    start_y = bottom_anchor - block_h
    cx = width // 2

    for li, line in enumerate(lines):
        ty = start_y + li * line_h
        line_words = line.split()
        line_bb = draw.textbbox((0, 0), line, font=font)
        line_w = line_bb[2] - line_bb[0]
        x = cx - line_w // 2

        for word in line_words:
            clean = word.strip(".,!?:;").upper()
            color = (255, 220, 0, 255) if clean == highlight_word else (255, 255, 255, 255)
            # Stroke pass
            draw.text(
                (x, ty), word, font=font, anchor="lt",
                fill=(0, 0, 0, 0),
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0, 255),
            )
            # Coloured fill
            draw.text((x, ty), word, font=font, anchor="lt", fill=color)
            space_bb = draw.textbbox((0, 0), word + " ", font=font)
            x += space_bb[2] - space_bb[0]

    img.save(image_path, "PNG")


def generate_fallback_image(idx: int, aspect_ratio: str = "9:16", hint: str = "", language: str = "en") -> str:
    """Generate a text-card fallback frame when Imagen is unavailable.

    Renders the narration text centred on a gradient background so the frame
    carries actual content rather than looking like a blank slide.
    """
    from PIL import ImageFont

    if aspect_ratio == "9:16":
        width, height = 1080, 1920
    else:
        width, height = 1920, 1080

    # Deterministic but visually distinct palette per scene — kept dark so
    # white text remains readable regardless of which colours are chosen.
    digest = hashlib.sha1(f"{idx}-{hint}".encode("utf-8")).hexdigest()

    def _dark(hex_pair: str) -> int:
        return max(20, min(100, int(hex_pair, 16)))

    top_color = tuple(_dark(digest[i:i + 2]) for i in (0, 2, 4))
    bot_color = tuple(_dark(digest[i:i + 2]) for i in (6, 8, 10))

    image = Image.new("RGB", (width, height), top_color)
    draw = ImageDraw.Draw(image, "RGBA")

    # Vertical gradient from top_color → bot_color
    for row in range(height):
        blend = row / max(1, height - 1)
        r = int(top_color[0] * (1 - blend) + bot_color[0] * blend)
        g = int(top_color[1] * (1 - blend) + bot_color[1] * blend)
        b = int(top_color[2] * (1 - blend) + bot_color[2] * blend)
        draw.line([(0, row), (width, row)], fill=(r, g, b, 255))

    # Semi-transparent card behind the text for legibility
    pad_x = int(width * 0.08)
    card_top = int(height * 0.25)
    card_bot = int(height * 0.75)
    draw.rounded_rectangle(
        [(pad_x, card_top), (width - pad_x, card_bot)],
        radius=40,
        fill=(0, 0, 0, 140),
    )

    # ── Font loading (mirrors video_service._load_font) ───────────────────
    font_size = max(52, min(80, width // 12))
    _FONT_EN = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    _FONT_HI = "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf"
    _FONT_FALLBACKS = [
        ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
        ("/System/Library/Fonts/Kohinoor.ttc", 3),
        ("/Library/Fonts/Arial Unicode.ttf", 0),
    ]
    font: ImageFont.FreeTypeFont | None = None
    primary_path = _FONT_HI if language == "hi" else _FONT_EN
    for path, *index in [(primary_path,)] + _FONT_FALLBACKS:
        try:
            font = ImageFont.truetype(path, font_size, index=index[0] if index else 0)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    # ── Word-wrap the hint text ───────────────────────────────────────────
    text = hint.strip() if hint else ""
    max_text_w = width - pad_x * 2 - 40  # inner card padding

    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        bb = draw.textbbox((0, 0), candidate, font=font)
        if bb[2] - bb[0] > max_text_w and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))

    # ── Render centred text block ─────────────────────────────────────────
    line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 12
    block_h = len(lines) * line_h
    card_center_y = (card_top + card_bot) // 2
    start_y = card_center_y - block_h // 2

    cx = width // 2
    for li, line in enumerate(lines):
        ty = start_y + li * line_h
        # Shadow
        draw.text((cx + 2, ty + 2), line, font=font, fill=(0, 0, 0, 180), anchor="mt")
        # White text
        draw.text((cx, ty), line, font=font, fill=(255, 255, 255, 255), anchor="mt")

    path = f"{TEMP_DIR}/scene_{idx}_fallback.png"
    image.save(path)
    return path
