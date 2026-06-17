# app/services/pexels_service.py

import os
import logging
import urllib.request
import requests

from app.config import TEMP_DIR
from app.utils.helpers import ensure_dir

logger = logging.getLogger(__name__)

_VIDEOS_SEARCH_URL = "https://api.pexels.com/videos/search"

_CATEGORY_FALLBACKS = {
    "science & space": "science cosmos",
    "history & civilizations": "ancient history",
    "human body & biology": "human body medical",
    "technology & ai": "technology digital",
    "health & fitness": "health fitness",
    "psychology & dark psychology": "psychology mind",
    "relationships & dating": "people relationship",
    "self-improvement & habits": "motivation success",
    "business & finance": "business finance",
    "culture & society": "culture people",
    "philosophy & life": "philosophy nature",
    "mysteries & unexplained": "mystery dark",
}


def _search_pexels(query: str, per_page: int = 5) -> list[dict]:
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY env var is not set")
    resp = requests.get(
        _VIDEOS_SEARCH_URL,
        headers={"Authorization": api_key},
        params={"query": query, "orientation": "landscape", "size": "medium", "per_page": per_page},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("videos", [])


def _best_video_file(video: dict) -> str | None:
    files = video.get("video_files", [])
    for quality in ("hd", "full_hd", "sd"):
        for f in files:
            if f.get("quality") == quality and f.get("file_type") == "video/mp4":
                return f.get("link")
    for f in files:
        if f.get("file_type") == "video/mp4":
            return f.get("link")
    return None


def _select_clip(videos: list[dict], audio_duration: float) -> dict | None:
    if not videos:
        return None
    long_enough = [v for v in videos if v.get("duration", 0) >= audio_duration]
    if long_enough:
        return min(long_enough, key=lambda v: v.get("duration", 0) - audio_duration)
    return max(videos, key=lambda v: v.get("duration", 0))


def fetch_clip(
    query: str,
    audio_duration: float,
    scene_idx: int,
    category: str = "",
    temp_dir: str = TEMP_DIR,
) -> str:
    """Search Pexels for a landscape video clip, download it, return local path.

    Falls back through: broad query → category keyword → generic → empty string.
    Empty string means the caller should render a black frame for this scene.
    """
    ensure_dir(temp_dir)
    dest = os.path.join(temp_dir, f"pexels_{scene_idx}.mp4")

    words = query.split()
    broad = " ".join(words[:2]) if len(words) > 2 else None
    category_fallback = _CATEGORY_FALLBACKS.get((category or "").lower().strip())
    fallback_chain = [q for q in [query, broad, category_fallback, "knowledge learning education"] if q]

    for attempt_query in fallback_chain:
        try:
            videos = _search_pexels(attempt_query)
            if not videos:
                continue
            clip = _select_clip(videos, audio_duration)
            if not clip:
                continue
            url = _best_video_file(clip)
            if not url:
                continue
            urllib.request.urlretrieve(url, dest)  # noqa: S310
            logger.info("[Pexels] scene=%d query=%r duration=%s", scene_idx, attempt_query, clip.get("duration"))
            return dest
        except Exception as exc:
            logger.warning("[Pexels] scene=%d query=%r failed: %s", scene_idx, attempt_query, exc)

    logger.warning("[Pexels] scene=%d all fallbacks exhausted — black frame", scene_idx)
    return ""
