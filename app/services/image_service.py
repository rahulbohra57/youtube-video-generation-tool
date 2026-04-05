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

# Exponential backoff delays (seconds) for 429 / quota-exhausted errors.
# Short retries are useless against per-minute quotas; wait long enough for
# the rate-limit window to reset.
_RETRY_DELAYS = [30, 60, 120]


def generate_image(prompt: str, idx: int, aspect_ratio: str = "16:9"):

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

    for attempt, wait in enumerate(_RETRY_DELAYS, start=1):
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

            path = f"{TEMP_DIR}/scene_{idx}.png"
            # Use the public save() API instead of the private _image_bytes attr.
            images[0].save(location=path)
            return path

        except Exception as e:
            err = str(e)
            is_rate_limit = "429" in err or "quota" in err.lower() or "resource exhausted" in err.lower()

            if is_rate_limit:
                print(f"Retry {attempt} failed (rate limit – waiting {wait}s): {e}")
                time.sleep(wait)
            else:
                # Non-rate-limit errors (bad prompt, auth, etc.) fail fast
                # after a short pause – no point in a long wait.
                print(f"Retry {attempt} failed: {e}")
                time.sleep(5)

    raise Exception(f"Image generation failed after {len(_RETRY_DELAYS)} retries using {MODEL_NAME}")
