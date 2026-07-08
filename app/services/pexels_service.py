# app/services/pexels_service.py

import os
import logging
import random
import requests

from app.config import TEMP_DIR
from app.utils.helpers import ensure_dir

logger = logging.getLogger(__name__)

_VIDEOS_SEARCH_URL = "https://api.pexels.com/videos/search"
_PHOTOS_SEARCH_URL = "https://api.pexels.com/v1/search"

# Clip duration targets for long-format B-roll assembly
_SHORT_CLIP_RANGE = (5.0, 10.0)   # most clips
_LONG_CLIP_RANGE = (15.0, 20.0)   # occasional longer clips
_LONG_CLIP_PROBABILITY = 0.25     # 25% chance of a long clip

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
    "artificial intelligence": "artificial intelligence technology",
    "technology": "technology digital devices",
    "current affairs": "news world affairs",
    "science": "science research laboratory",
    "health": "health medical",
    "business": "business finance office",
    "sports": "sports athletes stadium",
    "entertainment": "entertainment celebrity event",
    "environment": "environment nature climate",
}


def _search_pexels(query: str, per_page: int = 5, orientation: str = "landscape") -> list[dict]:
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY env var is not set")
    resp = requests.get(
        _VIDEOS_SEARCH_URL,
        headers={"Authorization": api_key},
        params={"query": query, "orientation": orientation, "size": "medium", "per_page": per_page},
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
    orientation: str = "landscape",
) -> str:
    """Search Pexels for a video clip, download it, return local path.

    Falls back through: broad query -> category keyword -> generic -> empty string.
    When orientation="portrait", the whole fallback chain is retried once more with
    orientation="landscape" before giving up (a downstream crop step in video_service
    converts landscape footage to portrait). Empty string means the caller should
    treat this scene as failed.
    """
    ensure_dir(temp_dir)
    dest = os.path.join(temp_dir, f"pexels_{scene_idx}.mp4")

    words = query.split()
    broad = " ".join(words[:2]) if len(words) > 2 else None
    category_fallback = _CATEGORY_FALLBACKS.get((category or "").lower().strip())
    fallback_chain = [q for q in [query, broad, category_fallback, "knowledge learning education"] if q]

    orientations = [orientation]
    if orientation == "portrait":
        orientations.append("landscape")

    for attempt_orientation in orientations:
        for attempt_query in fallback_chain:
            try:
                videos = _search_pexels(attempt_query, orientation=attempt_orientation)
                if not videos:
                    continue
                clip = _select_clip(videos, audio_duration)
                if not clip:
                    continue
                url = _best_video_file(clip)
                if not url:
                    continue
                api_key = os.getenv("PEXELS_API_KEY", "")
                resp = requests.get(url, headers={"Authorization": api_key}, timeout=60, stream=True)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                logger.info(
                    "[Pexels] scene=%d query=%r orientation=%s duration=%s",
                    scene_idx, attempt_query, attempt_orientation, clip.get("duration"),
                )
                return dest
            except Exception as exc:
                logger.warning(
                    "[Pexels] scene=%d query=%r orientation=%s failed: %s",
                    scene_idx, attempt_query, attempt_orientation, exc,
                )

    logger.warning(
        "[Pexels] scene=%d all fallbacks exhausted (orientations=%s) — signalling failure",
        scene_idx, orientations,
    )
    return ""


def _pick_clip_target_duration() -> float:
    """Return a random target clip duration: mostly 5-10s, occasionally 15-20s."""
    if random.random() < _LONG_CLIP_PROBABILITY:
        return random.uniform(*_LONG_CLIP_RANGE)
    return random.uniform(*_SHORT_CLIP_RANGE)


def fetch_clips_for_scene(
    query: str,
    audio_duration: float,
    scene_idx: int,
    category: str = "",
    temp_dir: str = TEMP_DIR,
) -> list[dict]:
    """Fetch multiple Pexels clips that together cover audio_duration with varied lengths.

    Returns list of {"video_path": str, "clip_duration": float}.
    Most clips are 5-10s; ~25% are 15-20s to add visual variety.
    The Pexels search API has no duration filter, so clips are trimmed in post.
    """
    ensure_dir(temp_dir)

    words = query.split()
    broad = " ".join(words[:2]) if len(words) > 2 else None
    category_fallback = _CATEGORY_FALLBACKS.get((category or "").lower().strip())
    fallback_chain = [q for q in [query, broad, category_fallback, "knowledge learning education"] if q]

    video_pool = []
    used_query = query
    for attempt_query in fallback_chain:
        try:
            videos = _search_pexels(attempt_query, per_page=15)
            if videos:
                video_pool = videos
                used_query = attempt_query
                break
        except Exception as exc:
            logger.warning("[Pexels] scene=%d pool query=%r failed: %s", scene_idx, attempt_query, exc)

    if not video_pool:
        logger.warning("[Pexels] scene=%d no pool found — black frame for full scene", scene_idx)
        return [{"video_path": "", "clip_duration": audio_duration}]

    pool = list(video_pool)
    random.shuffle(pool)

    result: list[dict] = []
    covered = 0.0
    clip_num = 0
    downloaded_urls: set[str] = set()

    while covered < audio_duration - 0.5:
        remaining = audio_duration - covered
        target_dur = min(_pick_clip_target_duration(), remaining)

        long_enough = [v for v in pool if v.get("duration", 0) >= target_dur]
        candidates = long_enough if long_enough else pool

        fresh = [v for v in candidates if _best_video_file(v) not in downloaded_urls]
        pick = random.choice(fresh) if fresh else random.choice(candidates)

        url = _best_video_file(pick)
        if not url:
            result.append({"video_path": "", "clip_duration": target_dur})
            covered += target_dur
            clip_num += 1
            continue

        dest = os.path.join(temp_dir, f"pexels_{scene_idx}_{clip_num}.mp4")
        try:
            api_key = os.getenv("PEXELS_API_KEY", "")
            resp = requests.get(url, headers={"Authorization": api_key}, timeout=60, stream=True)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            downloaded_urls.add(url)
            result.append({"video_path": dest, "clip_duration": target_dur})
            logger.info(
                "[Pexels] scene=%d clip=%d query=%r target=%.1fs pexels_dur=%ds",
                scene_idx, clip_num, used_query, target_dur, pick.get("duration", 0),
            )
        except Exception as exc:
            logger.warning("[Pexels] scene=%d clip=%d download failed: %s", scene_idx, clip_num, exc)
            result.append({"video_path": "", "clip_duration": target_dur})

        covered += target_dur
        clip_num += 1

    return result or [{"video_path": "", "clip_duration": audio_duration}]


def fetch_photo(query: str, category: str = "", dest_path: str = "") -> str:
    """Search Pexels for a landscape photo, download it, return local path.

    Free (no per-call cost) alternative background source for thumbnails when
    Imagen is unavailable. Falls back through: query -> broad query ->
    category keyword -> generic. Returns "" if every attempt fails.
    """
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY env var is not set")

    words = query.split()
    broad = " ".join(words[:2]) if len(words) > 2 else None
    category_fallback = _CATEGORY_FALLBACKS.get((category or "").lower().strip())
    fallback_chain = [q for q in [query, broad, category_fallback, "knowledge learning education"] if q]

    ensure_dir(TEMP_DIR)
    out = dest_path or os.path.join(TEMP_DIR, "pexels_thumbnail.jpg")

    for attempt_query in fallback_chain:
        try:
            resp = requests.get(
                _PHOTOS_SEARCH_URL,
                headers={"Authorization": api_key},
                params={"query": attempt_query, "orientation": "landscape", "size": "large", "per_page": 8},
                timeout=15,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if not photos:
                continue
            photo = random.choice(photos)
            src = photo.get("src", {})
            url = src.get("large2x") or src.get("original") or src.get("large")
            if not url:
                continue
            img_resp = requests.get(url, timeout=30)
            img_resp.raise_for_status()
            with open(out, "wb") as f:
                f.write(img_resp.content)
            logger.info("[Pexels] thumbnail photo query=%r", attempt_query)
            return out
        except Exception as exc:
            logger.warning("[Pexels] thumbnail photo query=%r failed: %s", attempt_query, exc)

    logger.warning("[Pexels] thumbnail photo: all fallbacks exhausted for query=%r", query)
    return ""
