# app/services/image_service.py

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

ensure_dir(TEMP_DIR)

model = ImageGenerationModel.from_pretrained(MODEL_NAME)

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
            images = model.generate_images(
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
