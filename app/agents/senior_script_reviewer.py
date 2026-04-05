# app/agents/senior_script_reviewer.py

from app.services.llm_service import (
    review_script_with_senior_reviewer,
    review_title_and_caption_with_senior_reviewer,
)


def _word_count(scenes: list[dict]) -> int:
    return sum(len(str(s.get("narration", "")).split()) for s in (scenes or []))


def _estimate_seconds(scenes: list[dict], wpm: int = 145) -> float:
    words = _word_count(scenes)
    return (words / max(wpm, 1)) * 60.0


def _truncate_at_sentence(text: str, max_words: int) -> str:
    """Truncate `text` to at most `max_words` words, ending on a sentence boundary.

    Falls back to the full target word count if no sentence boundary exists
    within the limit (prevents dropping too much content).
    """
    import re
    words = text.split()
    if len(words) <= max_words:
        return text
    candidate = " ".join(words[:max_words])
    # Find the last sentence-ending punctuation within the candidate
    m = re.search(r'^(.*[.!?])\s', candidate + " ", re.DOTALL)
    if m:
        return m.group(1).strip()
    # No sentence boundary found — keep the full max_words slice
    return candidate.strip()


def _tighten_if_too_long(scenes: list[dict], max_seconds: int) -> list[dict]:
    cur = _estimate_seconds(scenes)
    if cur <= max_seconds or not scenes:
        return scenes
    scale = max_seconds / max(cur, 1)
    out = []
    for s in scenes:
        narration = str(s.get("narration", ""))
        word_count = len(narration.split())
        keep = max(8, int(word_count * scale))
        out.append(
            {
                "scene": s.get("scene"),
                "narration": _truncate_at_sentence(narration, keep),
                "visual": s.get("visual", ""),
            }
        )
    return out


def _expand_if_too_short(scenes: list[dict], min_seconds: int) -> list[dict]:
    cur = _estimate_seconds(scenes)
    if cur >= min_seconds or not scenes:
        return scenes
    out = list(scenes)
    out[-1] = {
        **out[-1],
        "narration": (
            str(out[-1].get("narration", "")).strip()
            + " This matters because it affects real decisions people make every day."
        ).strip(),
    }
    return out


def review_package(
    topic: str,
    scenes: list[dict],
    language: str = "en",
    min_seconds: int = 15,
    max_seconds: int = 58,
    genre: str = "",
) -> dict:
    reviewed_scenes = review_script_with_senior_reviewer(
        topic=topic,
        scenes=scenes,
        language=language,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
    )
    reviewed_scenes = _tighten_if_too_long(reviewed_scenes, max_seconds=max_seconds)
    reviewed_scenes = _expand_if_too_short(reviewed_scenes, min_seconds=min_seconds)

    title_caption = review_title_and_caption_with_senior_reviewer(
        topic=topic,
        scenes=reviewed_scenes,
        language=language,
        genre=genre,
    )
    title = (title_caption.get("title") or topic).strip()[:100]
    caption = (title_caption.get("caption") or "").strip()

    return {
        "scenes": reviewed_scenes,
        "title": title or topic.strip(),
        "caption": caption,
        "estimated_seconds": round(_estimate_seconds(reviewed_scenes), 1),
    }

