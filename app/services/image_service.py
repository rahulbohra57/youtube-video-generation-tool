# app/services/image_service.py

import vertexai
from vertexai.preview.vision_models import ImageGenerationModel
from app.config import TEMP_DIR
from app.utils.helpers import ensure_dir
import time

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
                    "real person face, celebrity portrait, politician likeness, "
                    "specific named individual, realistic human portrait, photorealistic face"
                    ", copyrighted character, trademark logo, brand logo, movie character, cartoon mascot"
                ),
            )

            if not images:
                # Safety / content-policy filter: Imagen accepted the request but
                # returned zero images. Retrying the same prompt will produce the
                # same result — raise with a detectable prefix so the caller skips
                # outer retries for this scene.
                raise Exception(
                    f"{SAFETY_FILTER_ERROR_PREFIX} Imagen returned 0 images for scene {idx} "
                    "(prompt blocked by safety or content policy)"
                )

            path = f"{TEMP_DIR}/scene_{idx}.png"
            images[0].save(location=path)
            return path

        except Exception as e:
            err = str(e)
            is_rate_limit = "429" in err or "quota" in err.lower() or "resource exhausted" in err.lower()

            if err.startswith(SAFETY_FILTER_ERROR_PREFIX):
                # Never retry safety-filter rejections — the same prompt = same block.
                raise

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
