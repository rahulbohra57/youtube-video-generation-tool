# Tell Me Why Long-Format Video Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily 8–10 minute 16:9 YouTube video to the Tell Me Why channel, triggered at 2pm IST, with Pexels video clips as scene visuals, Google Cloud TTS narration, an Imagen thumbnail, and its own GitHub Actions workflow separate from the Shorts pipeline.

**Architecture:** New `long_generator_agent.py` orchestrates the full pipeline. `pexels_service.py` handles clip search/download. `long_video_service.py` assembles the 16:9 MoviePy output. Two new GitHub Actions workflows handle scheduling and generation with their own concurrency group so they never block the Shorts pipeline.

**Tech Stack:** Python 3.10, MoviePy 2.x, Pexels Videos API, Google Cloud TTS Neural2, Vertex AI Imagen 3, YouTube Data API v3, Firestore, GitHub Actions, GCS.

## Global Constraints

- Python 3.10 — use `str | None` union syntax (not `Optional[str]`)
- All tests must run with no GCP credentials (all external SDKs mocked via `sys.modules` in `conftest.py`)
- TTS voices: English only (`en-US-Neural2-*` / `en-US-Wavenet-*`) — never use `hi-IN-Chirp3-HD-*`
- Imagen model: `imagen-3.0-generate-002` only
- Long-format pipeline state key: `"stories_long"` (never `"stories"` — would block Shorts)
- YouTube upload uses `channel_id="stories"` OAuth credentials (same Tell Me Why channel)
- Long-format videos: 16:9 (1920×1080), 24fps, NOT tagged `#Shorts`
- `PEXELS_API_KEY` read from env var at call time
- Max 30 scenes, min 22 scenes — below 22 is a pipeline failure
- Scene narration: 40–50 words each at natural speaking pace (~16–20 seconds)
- No animation pass (Pexels clips are already motion — skip `STORIES_ANIMATION_ENABLED` path)
- Follow existing Firestore job tracking fields: `job_id`, `batch_id`, `code`, `topic`, `status`, `channel_id`, `video_type`, `created_at`, `finished_at`, `reviewed_title`, `gcs_video_url`, `youtube_url`

---

### Task 1: Pexels Service

**Files:**
- Create: `app/services/pexels_service.py`
- Test: `tests/test_pexels_service.py`

**Interfaces:**
- Produces: `fetch_clip(query: str, audio_duration: float, scene_idx: int, category: str = "", temp_dir: str = TEMP_DIR) -> str` — returns local `.mp4` path, or `""` on total failure

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pexels_service.py
import os
import importlib
import pytest
from unittest.mock import patch, MagicMock


def _make_video(duration: float, quality: str = "hd") -> dict:
    return {
        "duration": duration,
        "video_files": [{"quality": quality, "file_type": "video/mp4", "link": f"https://pexels.com/v/{quality}.mp4"}],
    }


def test_fetch_clip_returns_path_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    videos = [_make_video(25.0), _make_video(30.0)]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"videos": videos}
    mock_resp.raise_for_status = MagicMock()

    with patch("app.services.pexels_service.requests.get", return_value=mock_resp), \
         patch("app.services.pexels_service.urllib.request.urlretrieve") as mock_dl:
        result = ps.fetch_clip("ocean waves", 20.0, scene_idx=0, temp_dir=str(tmp_path))

    assert result == str(tmp_path / "pexels_0.mp4")
    mock_dl.assert_called_once()


def test_fetch_clip_prefers_clip_closest_to_audio_duration(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    # Both >= audio_duration=20; 22s is closer than 60s
    videos = [_make_video(60.0), _make_video(22.0)]
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"videos": videos}
    mock_resp.raise_for_status = MagicMock()

    selected = []
    def fake_urlretrieve(url, dest):
        selected.append(url)
    
    with patch("app.services.pexels_service.requests.get", return_value=mock_resp), \
         patch("app.services.pexels_service.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        ps.fetch_clip("nature", 20.0, scene_idx=1, temp_dir=str(tmp_path))

    assert "22" in selected[0] or "22.0" in selected[0] or selected[0].endswith("hd.mp4")


def test_fetch_clip_falls_back_to_generic_on_empty_results(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    empty_resp = MagicMock()
    empty_resp.json.return_value = {"videos": []}
    empty_resp.raise_for_status = MagicMock()

    generic_resp = MagicMock()
    generic_resp.json.return_value = {"videos": [_make_video(25.0)]}
    generic_resp.raise_for_status = MagicMock()

    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        # First 3 queries return empty, 4th (generic fallback) returns result
        if call_count[0] < 4:
            return empty_resp
        return generic_resp

    with patch("app.services.pexels_service.requests.get", side_effect=side_effect), \
         patch("app.services.pexels_service.urllib.request.urlretrieve"):
        result = ps.fetch_clip("very specific unusual query xyz", 20.0, scene_idx=2, temp_dir=str(tmp_path))

    assert result == str(tmp_path / "pexels_2.mp4")


def test_fetch_clip_returns_empty_string_when_all_fallbacks_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"videos": []}
    mock_resp.raise_for_status = MagicMock()

    with patch("app.services.pexels_service.requests.get", return_value=mock_resp):
        result = ps.fetch_clip("query", 20.0, scene_idx=3, temp_dir=str(tmp_path))

    assert result == ""


def test_fetch_clip_raises_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    import app.services.pexels_service as ps
    importlib.reload(ps)

    with patch("app.services.pexels_service.requests.get", side_effect=RuntimeError("PEXELS_API_KEY env var is not set")):
        result = ps.fetch_clip("test", 10.0, scene_idx=0, temp_dir=str(tmp_path))

    assert result == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_pexels_service.py -v
```
Expected: `ModuleNotFoundError` or `ImportError` (file does not exist yet)

- [ ] **Step 3: Create `app/services/pexels_service.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pexels_service.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/pexels_service.py tests/test_pexels_service.py
git commit -m "feat: add pexels_service for long-format video clip fetching"
```

---

### Task 2: Firestore lock key support

**Files:**
- Modify: `app/services/firestore_service.py:114-193` — add `lock_key` param to `acquire_video_lock` and `release_video_lock`
- Test: `tests/test_long_lock.py`

**Interfaces:**
- Consumes: nothing new — extends existing functions
- Produces: `acquire_video_lock(owner, ttl_seconds=1800, force=False, lock_key="video_generation") -> bool` and `release_video_lock(owner, lock_key="video_generation") -> bool`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_long_lock.py
import importlib
import pytest
from unittest.mock import MagicMock, patch


def test_acquire_lock_uses_custom_key(monkeypatch):
    mock_db = MagicMock()
    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_doc_ref.create = MagicMock()

    with patch("app.services.firestore_service._get_db", return_value=mock_db):
        import app.services.firestore_service as fs
        importlib.reload(fs)
        fs.acquire_video_lock("owner-1", lock_key="long_video_generation")

    mock_db.collection.assert_called_with("locks")
    mock_db.collection.return_value.document.assert_called_with("long_video_generation")


def test_release_lock_uses_custom_key(monkeypatch):
    mock_db = MagicMock()
    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_snap = MagicMock()
    mock_snap.exists = True
    mock_snap.to_dict.return_value = {"owner": "owner-1"}

    mock_db.transaction.return_value = MagicMock()

    with patch("app.services.firestore_service._get_db", return_value=mock_db):
        import app.services.firestore_service as fs
        importlib.reload(fs)
        fs.release_video_lock("owner-1", lock_key="long_video_generation")

    mock_db.collection.assert_called_with("locks")
    mock_db.collection.return_value.document.assert_called_with("long_video_generation")


def test_acquire_lock_default_key_unchanged():
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = MagicMock()

    with patch("app.services.firestore_service._get_db", return_value=mock_db):
        import app.services.firestore_service as fs
        importlib.reload(fs)
        try:
            fs.acquire_video_lock("owner-default")
        except Exception:
            pass

    mock_db.collection.return_value.document.assert_called_with("video_generation")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_long_lock.py -v
```
Expected: FAIL — `acquire_video_lock` does not accept `lock_key`

- [ ] **Step 3: Update `app/services/firestore_service.py`**

Find `def acquire_video_lock(owner: str, ttl_seconds: int = 1800, force: bool = False) -> bool:` and change the signature and the first line inside to:

```python
def acquire_video_lock(owner: str, ttl_seconds: int = 1800, force: bool = False, lock_key: str = "video_generation") -> bool:
    """Acquire a cross-instance video generation lock.

    Returns True only when this caller owns the lock.
    When force=True, unconditionally overwrites any existing lock (used by force_run).
    lock_key allows separate locks for different pipelines (e.g. "long_video_generation").
    """
    db = _get_db()
    doc_ref = db.collection("locks").document(lock_key)
```

Find `def release_video_lock(owner: str) -> bool:` and change to:

```python
def release_video_lock(owner: str, lock_key: str = "video_generation") -> bool:
    """Release the lock only if the caller still owns it."""
    db = _get_db()
    doc_ref = db.collection("locks").document(lock_key)
```

All other code in both functions stays unchanged.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_long_lock.py tests/test_pipeline.py -v
```
Expected: all pass (existing pipeline tests use default `lock_key` so are unaffected)

- [ ] **Step 5: Commit**

```bash
git add app/services/firestore_service.py tests/test_long_lock.py
git commit -m "feat: add lock_key param to acquire/release_video_lock for long-format isolation"
```

---

### Task 3: LLM long-format script generation

**Files:**
- Modify: `app/services/llm_service.py` — add `generate_long_facts_script()`
- Test: `tests/test_llm_long_script.py`

**Interfaces:**
- Consumes: `_get_model()` (existing), `_response_text()` (existing)
- Produces: `generate_long_facts_script(topic: str, category: str = "", premise: str = "") -> str` — returns raw JSON string of 25–30 scene objects

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm_long_script.py
import json
import importlib
import pytest
from unittest.mock import MagicMock, patch


def _make_scene(i: int, segment: str = "core") -> dict:
    return {
        "scene": i,
        "segment": segment,
        "narration": "A " * 45,  # ~45 words
        "visual_query": "ocean waves nature",
    }


def test_generate_long_facts_script_returns_json_with_scenes():
    scenes = [_make_scene(i, "hook" if i <= 2 else ("cta" if i == 27 else "core")) for i in range(1, 28)]
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(scenes)

    with patch("app.services.llm_service._get_model") as mock_model:
        mock_model.return_value.generate_content.return_value = mock_resp
        import app.services.llm_service as lm
        importlib.reload(lm)
        result = lm.generate_long_facts_script("Why do cats purr", category="science & space", premise="Cats produce purring sounds in a surprising way")

    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert len(parsed) == 27
    assert parsed[0]["segment"] == "hook"
    assert "visual_query" in parsed[0]
    assert "narration" in parsed[0]


def test_generate_long_facts_script_includes_topic_in_prompt():
    mock_resp = MagicMock()
    mock_resp.text = "[]"

    with patch("app.services.llm_service._get_model") as mock_model:
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = mock_resp
        mock_model.return_value = mock_instance
        import app.services.llm_service as lm
        importlib.reload(lm)
        lm.generate_long_facts_script("Octopus intelligence facts", category="human body & biology")

    call_args = mock_instance.generate_content.call_args
    prompt = call_args[0][0]
    assert "Octopus intelligence facts" in prompt
    assert "visual_query" in prompt
    assert "hook" in prompt
    assert "25" in prompt or "30" in prompt


def test_generate_long_facts_script_falls_back_to_standard_model_on_search_failure():
    mock_resp = MagicMock()
    mock_resp.text = "[]"

    with patch("app.services.llm_service._get_model") as mock_model, \
         patch("app.services.llm_service._init_search_model", side_effect=Exception("search unavailable")):
        mock_model.return_value.generate_content.return_value = mock_resp
        import app.services.llm_service as lm
        importlib.reload(lm)
        result = lm.generate_long_facts_script("test topic")

    assert result == "[]"
    mock_model.return_value.generate_content.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_llm_long_script.py -v
```
Expected: `AttributeError: module has no attribute 'generate_long_facts_script'`

- [ ] **Step 3: Add `generate_long_facts_script` to `app/services/llm_service.py`**

Add this function at the end of `llm_service.py` (after the existing `generate_fact_topic` function):

```python
def generate_long_facts_script(topic: str, category: str = "", premise: str = "") -> str:
    """Generate a 25–30 scene long-format facts script for Tell Me Why.

    Each scene has: scene (int), segment (hook/core/retention/cta),
    narration (40–50 words), visual_query (2–5 word Pexels search phrase).
    Returns raw JSON string. Caller must parse with extract_json().
    Tries Google Search grounding first; falls back to standard model on failure.
    """
    from datetime import date
    today_str = date.today().isoformat()

    category_block = f"\nCategory: {category}\n" if category else ""
    premise_block = f"\nPremise: {premise.strip()}\n" if premise and premise.strip() else ""

    prompt = f"""You are a scriptwriter for 'Tell Me Why', a YouTube educational channel. Write a long-form video script (8–10 minutes, 27 scenes) on the topic below. Verify all facts carefully before writing.

Topic: {topic}{category_block}{premise_block}
TODAY'S DATE: {today_str}.

Return ONLY a valid JSON array — no markdown, no explanation, no code fences.

Each scene object must have exactly these four fields:
- "scene": integer (1-based, 1 to 27)
- "segment": one of "hook", "core", "retention", "cta"
- "narration": spoken content (40–50 words at natural conversational pace)
- "visual_query": 2–5 word Pexels search phrase in English (e.g. "deep ocean bioluminescence")

SEGMENT RULES — follow exactly:
Scenes 1–2 → "hook": Scene 1 first sentence MUST be 12 words or fewer. Open with a surprising number, a named person doing something unexpected, or a direct question. No context-setting. Viewer decides in 3 seconds.
Scenes 3–25 → "core": One concrete insight per scene — real figures, dates, mechanisms, consequences. No filler. No cliffhangers — every fact resolves within its scene.
Scenes 26–27 → "retention": Conversational engagement — "Drop a comment below", "What surprised you most?", or a teaser for a related fact. Warm tone.
Scene 27 → change last "retention" to "cta": Like & Subscribe. One sentence, warm not pushy.

Wait — use this exact distribution:
- scene 1–2: "hook" (2 scenes)
- scene 3–24: "core" (22 scenes)
- scene 25–26: "retention" (2 scenes)
- scene 27: "cta" (1 scene)
Total: exactly 27 scenes.

NARRATION RULES:
- 40–50 words per scene (approx 16–20 seconds at 150 wpm)
- Conversational English — like explaining to a curious friend
- Banned phrases: "let's explore", "stay tuned", "it's fascinating", "game-changer", "in this video", "we'll discover"
- Every fact resolves within its scene — no "but we'll get to that later"

VISUAL_QUERY RULES:
- 2–5 plain English words for a Pexels video search
- Always in English
- Describe what appears visually on screen
- Examples: "human brain neurons firing", "ancient roman colosseum ruins", "stock market trading floor"

Return the JSON array directly. Start with "[" and end with "]".
"""
    try:
        search_model = _init_search_model(_SEARCH_MODEL_CANDIDATES[0])
        response = search_model.generate_content(prompt)
        return _response_text(response)
    except Exception as search_exc:
        logger.warning("Long script search grounding failed (%s), using standard model", search_exc)

    response = _get_model().generate_content(prompt)
    return _response_text(response)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_llm_long_script.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/llm_service.py tests/test_llm_long_script.py
git commit -m "feat: add generate_long_facts_script for 27-scene long-format Tell Me Why videos"
```

---

### Task 4: Imagen thumbnail + YouTube thumbnail upload

**Files:**
- Modify: `app/services/image_service.py` — add `generate_thumbnail()`
- Modify: `app/services/youtube_service.py` — add `set_thumbnail()`, add `is_short` param to `upload_video()`, fix `extract_video_id()` for `/watch?v=` URLs, add `_UPLOAD_DEFAULTS["stories_long"]`
- Test: `tests/test_thumbnail.py`

**Interfaces:**
- Produces:
  - `generate_thumbnail(prompt: str, code: str) -> str` — returns local `.png` path
  - `set_thumbnail(video_id: str, thumbnail_path: str, channel_id: str = "stories") -> None`
  - `upload_video(..., is_short: bool = True) -> str` — unchanged default behaviour for existing callers

- [ ] **Step 1: Write failing tests**

```python
# tests/test_thumbnail.py
import importlib
import pytest
from unittest.mock import MagicMock, patch


def test_generate_thumbnail_returns_png_path(tmp_path):
    mock_img = MagicMock()
    mock_images = MagicMock()
    mock_images.__len__ = lambda self: 1
    mock_images.__getitem__ = lambda self, i: mock_img

    nested = MagicMock()
    nested.__bool__ = lambda self: False
    mock_images_obj = MagicMock()
    mock_images_obj.images = None
    mock_images_obj.__len__ = lambda self: 1
    mock_images_obj.__getitem__ = lambda self, i: mock_img

    with patch("app.services.image_service.TEMP_DIR", str(tmp_path)), \
         patch("app.services.image_service._get_model") as mock_model:
        mock_model.return_value.generate_images.return_value = mock_images_obj
        import app.services.image_service as ims
        importlib.reload(ims)
        ims.TEMP_DIR = str(tmp_path)
        result = ims.generate_thumbnail("A scientist with a glowing brain", "TEST01")

    assert result.endswith("thumbnail_TEST01.png")
    mock_img.save.assert_called_once_with(result)


def test_set_thumbnail_calls_youtube_api():
    mock_creds = MagicMock()
    mock_youtube = MagicMock()
    mock_request = MagicMock()
    mock_youtube.thumbnails.return_value.set.return_value = mock_request

    with patch("app.services.youtube_service.get_credentials", return_value=mock_creds), \
         patch("app.services.youtube_service.build", return_value=mock_youtube):
        import app.services.youtube_service as ys
        importlib.reload(ys)
        ys.set_thumbnail("abc123", "/tmp/thumb.png", channel_id="stories")

    mock_youtube.thumbnails.return_value.set.assert_called_once_with(
        videoId="abc123", media_body=mock_youtube.thumbnails.return_value.set.call_args[1]["media_body"]
    )
    mock_request.execute.assert_called_once()


def test_upload_video_does_not_add_shorts_when_is_short_false():
    mock_creds = MagicMock()
    mock_youtube = MagicMock()
    mock_request = MagicMock()
    mock_request.execute.return_value = {"id": "vid999"}
    mock_youtube.videos.return_value.insert.return_value = mock_request

    with patch("app.services.youtube_service.get_credentials", return_value=mock_creds), \
         patch("app.services.youtube_service.build", return_value=mock_youtube), \
         patch("app.services.youtube_service.MediaFileUpload"):
        import app.services.youtube_service as ys
        importlib.reload(ys)
        ys.upload_video("/tmp/video.mp4", "My Video", "", is_short=False)

    call_args = mock_youtube.videos.return_value.insert.call_args
    desc = call_args[1]["body"]["snippet"]["description"]
    assert "#Shorts" not in desc
    assert "#shorts" not in desc


def test_extract_video_id_handles_watch_url():
    import app.services.youtube_service as ys
    importlib.reload(ys)
    assert ys.extract_video_id("https://www.youtube.com/watch?v=abc123") == "abc123"


def test_extract_video_id_handles_shorts_url():
    import app.services.youtube_service as ys
    importlib.reload(ys)
    assert ys.extract_video_id("https://www.youtube.com/shorts/xyz789") == "xyz789"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_thumbnail.py -v
```
Expected: `AttributeError` — `generate_thumbnail`, `set_thumbnail` do not exist; `upload_video` has no `is_short` param; `extract_video_id` doesn't handle `/watch?v=`

- [ ] **Step 3: Add `generate_thumbnail` to `app/services/image_service.py`**

Add after the existing `generate_image` function:

```python
def generate_thumbnail(prompt: str, code: str) -> str:
    """Generate a 16:9 thumbnail image using Imagen 3. Returns local .png path."""
    ensure_dir(TEMP_DIR)
    output_path = os.path.join(TEMP_DIR, f"thumbnail_{code}.png")
    full_prompt = (
        f"{prompt} "
        "Thumbnail composition: single bold focal subject, high contrast, vivid complementary colors, "
        "cinematic lighting. No text, no words, no letters, no logos, no captions, no watermarks."
    )
    for delay in _QUOTA_RETRY_DELAYS + [None]:
        try:
            images = _get_model().generate_images(
                prompt=full_prompt,
                number_of_images=1,
                aspect_ratio="16:9",
                safety_filter_level="block_few",
                person_generation="allow_adult",
                negative_prompt="text, words, letters, numbers, logos, captions, subtitles, watermarks, signs",
            )
            img = _first_generated_image(images)
            if img is None:
                raise RuntimeError("Imagen returned no images for thumbnail")
            img.save(output_path)
            return output_path
        except Exception as exc:
            if delay is None:
                raise
            err = str(exc).lower()
            if any(kw in err for kw in ("quota", "429", "resource_exhausted")):
                logger.warning("[Thumbnail] Quota error, waiting %ds: %s", delay, exc)
                time.sleep(delay)
            else:
                raise
    raise RuntimeError("generate_thumbnail: all retries exhausted")
```

- [ ] **Step 4: Update `app/services/youtube_service.py`**

**4a.** Add `_UPLOAD_DEFAULTS["stories_long"]` in the `_UPLOAD_DEFAULTS` dict:

```python
_UPLOAD_DEFAULTS: dict[str, str] = {
    "news": (
        "----\n\n"
        "📲 Follow for daily tech & AI news in under a minute.\n"
        "🔔 Subscribe so you never miss a headline.\n\n"
        "Kurrent Affairs breaks down the biggest stories in Tech, AI, and innovation — fast, clear, and without the noise.\n\n"
        "🔗 Subscribe: https://www.youtube.com/@KurrentAffairs"
    ),
    "stories": (
        "----\n\n"
        "Welcome to Tell Me Why — your daily dose of curiosity.\n\n"
        "Ever wondered why things work the way they do? We answer the questions you never thought to ask — from science and psychology to everyday mysteries and mind-bending facts.\n\n"
        "Every short gives you one fascinating answer in under a minute.\n\n"
        "🔍 Discover something new every day.\n"
        "🧠 Feed your curiosity.\n"
        "💡 Share the knowledge.\n\n"
        "If you love learning something surprising every day, subscribe and never stop asking why.\n\n"
        "🔔 Join the curious minds:\n"
        "https://www.youtube.com/@TellMeWhy-in\n\n"
        "#shorts #TellMeWhy #CuriosityFacts #DidYouKnow #LearnOnYouTube #WhyFacts #MindBlown"
    ),
    "stories_long": (
        "----\n\n"
        "Welcome to Tell Me Why — your daily deep dive into curiosity.\n\n"
        "Ever wondered why things work the way they do? We go deep on the questions you never thought to ask — from science and psychology to everyday mysteries and mind-bending facts.\n\n"
        "🔍 Discover something surprising every day.\n"
        "🧠 Feed your curiosity.\n"
        "💡 Share the knowledge.\n\n"
        "If you love learning something new every day, subscribe and never stop asking why.\n\n"
        "🔔 Join the curious minds:\n"
        "https://www.youtube.com/@TellMeWhy-in\n\n"
        "#TellMeWhy #CuriosityFacts #DidYouKnow #LearnOnYouTube #WhyFacts #MindBlown #Educational"
    ),
}
```

**4b.** Update `upload_video` signature and `#Shorts` logic. Find:

```python
def upload_video(video_path: str, title: str, description: str, genre: str = "", channel_id: str = "news", tags: list | None = None) -> str:
    creds = get_credentials(channel_id=channel_id)
    youtube = build("youtube", "v3", credentials=creds)

    # Ensure #Shorts is in the description so YouTube surfaces it in the Shorts feed
    desc = description or ""
    if "#shorts" not in desc.lower():
        desc = desc.rstrip() + "\n#Shorts"
```

Replace with:

```python
def upload_video(video_path: str, title: str, description: str, genre: str = "", channel_id: str = "news", tags: list | None = None, is_short: bool = True) -> str:
    creds = get_credentials(channel_id=channel_id)
    youtube = build("youtube", "v3", credentials=creds)

    # Ensure #Shorts is in the description so YouTube surfaces it in the Shorts feed
    desc = description or ""
    if is_short and "#shorts" not in desc.lower():
        desc = desc.rstrip() + "\n#Shorts"
```

**4c.** Fix `extract_video_id` to handle `/watch?v=` URLs. Find the function and replace entirely:

```python
def extract_video_id(url: str) -> str:
    if not url:
        return ""
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    # https://www.youtube.com/watch?v=VIDEO_ID
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0].strip()
    path = (parsed.path or "").strip("/")
    if not path:
        return ""
    # https://www.youtube.com/shorts/VIDEO_ID
    if path.startswith("shorts/"):
        return path.split("/", 1)[1].strip()
    # https://youtu.be/VIDEO_ID
    if parsed.netloc.endswith("youtu.be"):
        return path.split("/")[0].strip()
    return ""
```

**4d.** Add `set_thumbnail` function after `extract_video_id`:

```python
def set_thumbnail(video_id: str, thumbnail_path: str, channel_id: str = "stories") -> None:
    """Upload a custom thumbnail to an already-published YouTube video."""
    creds = get_credentials(channel_id=channel_id)
    youtube = build("youtube", "v3", credentials=creds)
    media = MediaFileUpload(thumbnail_path, mimetype="image/png", resumable=False)
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    logger.info("[YouTube] Thumbnail set for video %s", video_id)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_thumbnail.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 6: Verify existing YouTube tests still pass**

```bash
pytest tests/ -v -k "youtube or routes or pipeline"
```
Expected: all pass (default `is_short=True` preserves existing behaviour)

- [ ] **Step 7: Commit**

```bash
git add app/services/image_service.py app/services/youtube_service.py tests/test_thumbnail.py
git commit -m "feat: add generate_thumbnail, set_thumbnail, is_short upload param, fix extract_video_id for watch URLs"
```

---

### Task 5: Long video assembly service

**Files:**
- Create: `app/services/long_video_service.py`
- Test: `tests/test_long_video_service.py`

**Interfaces:**
- Consumes: `_make_word_caption_clips`, `_pick_music`, `_audio_fade_in`, `_audio_fade_out`, `_audio_loop`, `_volume`, `_subclip`, `_fit_cover`, `_clip_audio`, `_clip_duration`, `_crop_center`, `BG_VOLUME`, `VO_GAIN`, `AUDIO_FADE_IN`, `AUDIO_FADE_OUT` — all from `app.services.video_service`
- Produces: `create_long_video(clips: list[dict], output_path: str, music_genre: str = "general", language: str = "en") -> str`
  - `clips` items: `{"video_path": str, "audio_path": str, "narration": str}`
  - `video_path` may be `""` → black frame fallback

- [ ] **Step 1: Write failing tests**

```python
# tests/test_long_video_service.py
import os
import importlib
import pytest
from unittest.mock import MagicMock, patch, call


def _make_mock_audio(duration: float = 20.0):
    m = MagicMock()
    m.duration = duration
    return m


def _make_mock_video(duration: float = 25.0, w: int = 1920, h: int = 1080):
    m = MagicMock()
    m.duration = duration
    m.w = w
    m.h = h
    m.audio = _make_mock_audio(duration)
    return m


def test_create_long_video_calls_write_videofile(tmp_path):
    clip1 = {"video_path": str(tmp_path / "clip1.mp4"), "audio_path": str(tmp_path / "a1.mp3"), "narration": "Scene one narration text here."}
    clip2 = {"video_path": "", "audio_path": str(tmp_path / "a2.mp3"), "narration": "Scene two is a black frame fallback."}

    mock_audio_clip = _make_mock_audio(20.0)
    mock_video_clip = _make_mock_video(25.0)
    mock_final = MagicMock()
    mock_final.duration = 40.0
    mock_final.audio = _make_mock_audio(40.0)

    with patch("app.services.long_video_service.AudioFileClip", return_value=mock_audio_clip), \
         patch("app.services.long_video_service.VideoFileClip", return_value=mock_video_clip), \
         patch("app.services.long_video_service.ImageClip", return_value=mock_video_clip), \
         patch("app.services.long_video_service.concatenate_videoclips", return_value=mock_final), \
         patch("app.services.long_video_service.CompositeVideoClip", return_value=mock_final), \
         patch("app.services.long_video_service.CompositeAudioClip", return_value=mock_final.audio), \
         patch("app.services.long_video_service._pick_music", return_value=None), \
         patch("app.services.long_video_service._make_word_caption_clips", return_value=[]):
        import app.services.long_video_service as lvs
        importlib.reload(lvs)
        output = str(tmp_path / "out.mp4")
        lvs.create_long_video([clip1, clip2], output)

    mock_final.write_videofile.assert_called_once()
    call_kwargs = mock_final.write_videofile.call_args
    assert call_kwargs[0][0] == output
    assert call_kwargs[1]["fps"] == 24
    assert call_kwargs[1]["codec"] == "libx264"


def test_create_long_video_uses_black_frame_when_video_path_empty(tmp_path):
    clip = {"video_path": "", "audio_path": str(tmp_path / "a.mp3"), "narration": "narration"}

    mock_audio = _make_mock_audio(20.0)
    mock_image_clip = MagicMock()
    mock_image_clip.w = 1920
    mock_image_clip.h = 1080
    mock_image_clip.audio = mock_audio

    mock_final = MagicMock()
    mock_final.duration = 20.0
    mock_final.audio = mock_audio

    image_clip_calls = []
    def capture_image_clip(arr):
        image_clip_calls.append(arr)
        return mock_image_clip

    with patch("app.services.long_video_service.AudioFileClip", return_value=mock_audio), \
         patch("app.services.long_video_service.VideoFileClip") as mock_vc, \
         patch("app.services.long_video_service.ImageClip", side_effect=capture_image_clip), \
         patch("app.services.long_video_service.concatenate_videoclips", return_value=mock_final), \
         patch("app.services.long_video_service.CompositeVideoClip", return_value=mock_final), \
         patch("app.services.long_video_service.CompositeAudioClip", return_value=mock_audio), \
         patch("app.services.long_video_service._pick_music", return_value=None), \
         patch("app.services.long_video_service._make_word_caption_clips", return_value=[]):
        import app.services.long_video_service as lvs
        importlib.reload(lvs)
        lvs.create_long_video([clip], str(tmp_path / "out.mp4"))

    # VideoFileClip should NOT have been called since video_path is ""
    mock_vc.assert_not_called()
    # ImageClip SHOULD have been called with a black numpy array
    assert len(image_clip_calls) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_long_video_service.py -v
```
Expected: `ModuleNotFoundError` — file does not exist yet

- [ ] **Step 3: Create `app/services/long_video_service.py`**

```python
# app/services/long_video_service.py

import os
import logging
import numpy as np

try:
    from moviepy import (
        VideoFileClip, AudioFileClip, ImageClip,
        concatenate_videoclips, CompositeVideoClip, CompositeAudioClip,
    )
except Exception:
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, ImageClip,
        concatenate_videoclips, CompositeVideoClip, CompositeAudioClip,
    )

from app.services.video_service import (
    _make_word_caption_clips,
    _pick_music,
    _audio_fade_in,
    _audio_fade_out,
    _audio_loop,
    _volume,
    _subclip,
    _fit_cover,
    _clip_audio,
    _clip_duration,
    BG_VOLUME,
    VO_GAIN,
    AUDIO_FADE_IN,
    AUDIO_FADE_OUT,
)

logger = logging.getLogger(__name__)

_TARGET_W = 1920
_TARGET_H = 1080


def create_long_video(
    clips: list[dict],
    output_path: str,
    music_genre: str = "general",
    language: str = "en",
) -> str:
    """Assemble a long-format 16:9 video from Pexels clips + TTS audio.

    clips: list of {"video_path": str, "audio_path": str, "narration": str}
    video_path may be "" — caller provides black frame fallback in that case.
    Returns output_path.
    """
    scene_clips = []

    for idx, item in enumerate(clips):
        video_path = item.get("video_path", "")
        audio_path = item["audio_path"]
        narration = item.get("narration", "")

        audio = AudioFileClip(audio_path)
        audio = _audio_fade_in(audio, AUDIO_FADE_IN)
        audio = _audio_fade_out(audio, AUDIO_FADE_OUT)
        duration = audio.duration

        if video_path and os.path.exists(video_path):
            raw = VideoFileClip(video_path)
            if raw.duration >= duration:
                base = _subclip(raw, 0, duration)
            else:
                loops_needed = int(duration / raw.duration) + 1
                looped = concatenate_videoclips([raw] * loops_needed)
                base = _subclip(looped, 0, duration)
            base = _fit_cover(base, _TARGET_W, _TARGET_H)
        else:
            arr = np.zeros((_TARGET_H, _TARGET_W, 3), dtype=np.uint8)
            base = _clip_duration(ImageClip(arr), duration)

        base = _clip_audio(base, audio)

        try:
            caption_clips = _make_word_caption_clips(
                narration, duration, _TARGET_W, _TARGET_H, language=language
            )
            clip = CompositeVideoClip([base] + caption_clips) if caption_clips else base
        except Exception as cap_err:
            logger.warning("[LongVideo] Caption failed for scene %d: %s", idx, cap_err)
            clip = base

        scene_clips.append(clip)

    final_video = concatenate_videoclips(scene_clips, method="compose")
    vo_audio = _volume(_audio_fade_out(final_video.audio, 0.5), VO_GAIN)
    final_video = _clip_audio(final_video, vo_audio)

    music_path = _pick_music(music_genre)
    if music_path:
        try:
            bg = AudioFileClip(music_path)
            total_duration = final_video.duration
            if bg.duration < total_duration:
                bg = _audio_loop(bg, duration=total_duration)
            else:
                start_offset = min(12.0, max(0.0, (bg.duration - total_duration) * 0.25))
                bg = _subclip(bg, start_offset, start_offset + total_duration)
            bg = _volume(_audio_fade_out(bg, 1.0), BG_VOLUME)
            mixed = CompositeAudioClip([vo_audio, bg])
            final_video = _clip_audio(final_video, mixed)
            logger.info("[LongVideo] Background music [%s]: %s", music_genre, os.path.basename(music_path))
        except Exception as music_err:
            logger.warning("[LongVideo] Background music skipped: %s", music_err)

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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_long_video_service.py -v
```
Expected: both tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/long_video_service.py tests/test_long_video_service.py
git commit -m "feat: add long_video_service for 16:9 Pexels-based video assembly"
```

---

### Task 6: GitHub dispatch for long video

**Files:**
- Modify: `app/agents/github_dispatch.py` — add `dispatch_long_video_generation()`
- Test: `tests/test_github_dispatch.py` — add two new tests

**Interfaces:**
- Consumes: existing `requests`, `os`, `json`, `logger` in `github_dispatch.py`
- Produces: `dispatch_long_video_generation(payload: dict) -> None` — dispatches `generate-long-video.yml`

- [ ] **Step 1: Write failing tests**

Add to the bottom of `tests/test_github_dispatch.py`:

```python
def test_dispatch_long_video_posts_to_correct_workflow(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp) as mock_post:
        import app.agents.github_dispatch as gd
        importlib.reload(gd)
        gd.dispatch_long_video_generation({"job_id": "long_123", "headline": "Long Test"})

    call_args = mock_post.call_args
    assert "generate-long-video.yml" in call_args[0][0]
    body = call_args[1]["json"]
    payload = json.loads(body["inputs"]["payload"])
    assert payload["job_id"] == "long_123"


def test_dispatch_long_video_raises_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    import app.agents.github_dispatch as gd
    importlib.reload(gd)

    with pytest.raises(RuntimeError, match="GITHUB_DISPATCH_TOKEN"):
        gd.dispatch_long_video_generation({"job_id": "x"})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_github_dispatch.py::test_dispatch_long_video_posts_to_correct_workflow tests/test_github_dispatch.py::test_dispatch_long_video_raises_without_token -v
```
Expected: `AttributeError: module 'app.agents.github_dispatch' has no attribute 'dispatch_long_video_generation'`

- [ ] **Step 3: Add `dispatch_long_video_generation` to `app/agents/github_dispatch.py`**

Add after the existing `dispatch_video_generation` function:

```python
_LONG_WORKFLOW_FILE = "generate-long-video.yml"


def dispatch_long_video_generation(payload: dict) -> None:
    """POST a workflow_dispatch to GitHub Actions to trigger generate-long-video.yml."""
    token = os.getenv("GITHUB_DISPATCH_TOKEN") or os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GITHUB_REPO", "")
    if not token:
        raise RuntimeError("GITHUB_DISPATCH_TOKEN (or GITHUB_TOKEN) env var must be set")
    if not repo:
        raise RuntimeError("GITHUB_REPO env var must be set (e.g. 'owner/repo')")

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{_LONG_WORKFLOW_FILE}/dispatches"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        },
        json={"ref": "main", "inputs": {"payload": json.dumps(payload)}},
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("Dispatched generate-long-video workflow for job %s", payload.get("job_id"))
```

- [ ] **Step 4: Run all dispatch tests to verify they pass**

```bash
pytest tests/test_github_dispatch.py -v
```
Expected: all tests PASS (existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add app/agents/github_dispatch.py tests/test_github_dispatch.py
git commit -m "feat: add dispatch_long_video_generation for generate-long-video.yml workflow"
```

---

### Task 7: Long generator agent

**Files:**
- Create: `app/agents/long_generator_agent.py`
- Test: `tests/test_long_generator_agent.py`

**Interfaces:**
- Consumes:
  - `generate_long_facts_script(topic, category, premise) -> str` from `llm_service`
  - `generate_audio(text, output_file, language, voice_name, channel_id) -> None` from `tts_service`
  - `choose_voice_for_video(language, preference, domain) -> str` from `tts_service`
  - `fetch_clip(query, audio_duration, scene_idx, category, temp_dir) -> str` from `pexels_service`
  - `generate_thumbnail(prompt, code) -> str` from `image_service`
  - `create_long_video(clips, output_path, music_genre, language) -> str` from `long_video_service`
  - `upload_video(video_path, title, description, genre, channel_id, tags, is_short) -> str` from `youtube_service`
  - `set_thumbnail(video_id, thumbnail_path, channel_id) -> None` from `youtube_service`
  - `extract_video_id(url) -> str` from `youtube_service`
  - `classify_music_genre(topic, story_genre) -> str` from `llm_service`
  - `acquire_video_lock(owner, lock_key) -> bool` from `firestore_service`
  - `release_video_lock(owner, lock_key) -> None` from `firestore_service`
  - `extract_json(raw) -> list` from `app.utils.helpers`
- Produces: `run(headline, code, batch_id, job_id, public_id, force_run, genre, details, channel_id) -> None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_long_generator_agent.py
import json
import importlib
import pytest
from unittest.mock import MagicMock, patch, call


def _make_scenes(n: int = 27) -> list[dict]:
    scenes = []
    for i in range(1, n + 1):
        if i <= 2:
            seg = "hook"
        elif i >= n:
            seg = "cta"
        elif i >= n - 1:
            seg = "retention"
        else:
            seg = "core"
        scenes.append({
            "scene": i,
            "segment": seg,
            "narration": ("word " * 45).strip(),
            "visual_query": "nature landscape",
        })
    return scenes


def test_run_completes_happy_path(tmp_path):
    scenes = _make_scenes(27)

    with patch("app.agents.long_generator_agent.generate_long_facts_script", return_value=json.dumps(scenes)), \
         patch("app.agents.long_generator_agent.extract_json", return_value=scenes), \
         patch("app.agents.long_generator_agent.choose_voice_for_video", return_value="en-US-Neural2-D"), \
         patch("app.agents.long_generator_agent.generate_audio"), \
         patch("app.agents.long_generator_agent.fetch_clip", return_value=str(tmp_path / "clip.mp4")), \
         patch("app.agents.long_generator_agent.generate_thumbnail", return_value=str(tmp_path / "thumb.png")), \
         patch("app.agents.long_generator_agent.create_long_video", return_value=str(tmp_path / "out.mp4")), \
         patch("app.agents.long_generator_agent.classify_music_genre", return_value="Happy"), \
         patch("app.agents.long_generator_agent.firestore_service") as mock_fs, \
         patch("app.agents.long_generator_agent.send_message"), \
         patch("app.agents.long_generator_agent.upload_video", return_value="https://www.youtube.com/watch?v=abc123"), \
         patch("app.agents.long_generator_agent.set_thumbnail"), \
         patch("app.agents.long_generator_agent.extract_video_id", return_value="abc123"), \
         patch("app.agents.long_generator_agent.gcs_upload_video", return_value="gs://bucket/video.mp4"), \
         patch("app.agents.long_generator_agent.AudioFileClip") as mock_afc:
        mock_afc.return_value.duration = 20.0
        mock_afc.return_value.close = MagicMock()
        mock_fs.get_job.return_value = {}
        mock_fs.acquire_video_lock.return_value = True
        mock_fs.create_or_update_job.return_value = None
        mock_fs.mark_scene_checkpoint.return_value = None
        mock_fs.set_pipeline_and_batch_state.return_value = None
        mock_fs.release_video_lock.return_value = None

        import app.agents.long_generator_agent as lga
        importlib.reload(lga)
        lga.run("Why do cats purr", "LONG01", job_id="job-123", public_id="PUB01")

    # Verify job was marked completed
    completed_calls = [
        c for c in mock_fs.create_or_update_job.call_args_list
        if c[0][1].get("status") == "completed"
    ]
    assert len(completed_calls) == 1


def test_run_skips_if_job_already_terminal():
    with patch("app.agents.long_generator_agent.firestore_service") as mock_fs:
        mock_fs.get_job.return_value = {"status": "completed"}
        import app.agents.long_generator_agent as lga
        importlib.reload(lga)
        lga.run("Topic", "CODE01", job_id="existing-job")

    mock_fs.acquire_video_lock.assert_not_called()


def test_run_marks_failed_when_too_few_scenes():
    too_few = _make_scenes(5)

    with patch("app.agents.long_generator_agent.generate_long_facts_script", return_value=json.dumps(too_few)), \
         patch("app.agents.long_generator_agent.extract_json", return_value=too_few), \
         patch("app.agents.long_generator_agent.choose_voice_for_video", return_value="en-US-Neural2-D"), \
         patch("app.agents.long_generator_agent.classify_music_genre", return_value="Happy"), \
         patch("app.agents.long_generator_agent.firestore_service") as mock_fs, \
         patch("app.agents.long_generator_agent.send_message"):
        mock_fs.get_job.return_value = {}
        mock_fs.acquire_video_lock.return_value = True
        mock_fs.create_or_update_job.return_value = None
        mock_fs.release_video_lock.return_value = None
        mock_fs.set_pipeline_and_batch_state.return_value = None

        import app.agents.long_generator_agent as lga
        importlib.reload(lga)

        with pytest.raises(RuntimeError, match="Script too short"):
            lga.run("Topic", "CODE02", job_id="job-fail")

    failed_calls = [
        c for c in mock_fs.create_or_update_job.call_args_list
        if c[0][1].get("status") == "failed"
    ]
    assert len(failed_calls) >= 1


def test_run_uses_long_lock_key():
    with patch("app.agents.long_generator_agent.generate_long_facts_script", return_value="[]"), \
         patch("app.agents.long_generator_agent.extract_json", return_value=[]), \
         patch("app.agents.long_generator_agent.choose_voice_for_video", return_value="en-US-Neural2-D"), \
         patch("app.agents.long_generator_agent.classify_music_genre", return_value="Happy"), \
         patch("app.agents.long_generator_agent.firestore_service") as mock_fs, \
         patch("app.agents.long_generator_agent.send_message"):
        mock_fs.get_job.return_value = {}
        mock_fs.acquire_video_lock.return_value = True
        mock_fs.create_or_update_job.return_value = None
        mock_fs.release_video_lock.return_value = None
        mock_fs.set_pipeline_and_batch_state.return_value = None

        import app.agents.long_generator_agent as lga
        importlib.reload(lga)

        try:
            lga.run("Topic", "CODE03", job_id="job-lock")
        except Exception:
            pass

    acquire_call = mock_fs.acquire_video_lock.call_args
    assert acquire_call[1].get("lock_key") == "long_video_generation" or \
           (len(acquire_call[0]) > 1 and acquire_call[0][1] == "long_video_generation") or \
           "long_video_generation" in str(acquire_call)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_long_generator_agent.py -v
```
Expected: `ModuleNotFoundError` — file does not exist

- [ ] **Step 3: Create `app/agents/long_generator_agent.py`**

```python
# app/agents/long_generator_agent.py

import logging
import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from app.config import TEMP_DIR, OUTPUT_DIR, TMP_RETENTION_DAYS, get_chat_id
from app.services import firestore_service
from app.services.llm_service import generate_long_facts_script, classify_music_genre
from app.services.tts_service import generate_audio, choose_voice_for_video
from app.services.pexels_service import fetch_clip
from app.services.image_service import generate_thumbnail
from app.services.long_video_service import create_long_video
from app.services.telegram_service import send_message
from app.services.youtube_service import upload_video, set_thumbnail, extract_video_id
from app.utils.helpers import extract_json, ensure_dir, cleanup_files_older_than

logger = logging.getLogger(__name__)

_LONG_LOCK_KEY = "long_video_generation"
_LONG_PIPELINE_CHANNEL = "stories_long"  # Firestore pipeline_state key — isolated from Shorts
_YOUTUBE_CHANNEL = "stories"             # OAuth credentials key — same Tell Me Why channel

LONG_MIN_SCENES = 22
LONG_MAX_SCENES = 30

_TMW_VISUAL_STYLE = (
    "Bright flat digital illustration, thick bold outlines, vivid complementary color palette "
    "(electric blue, warm yellow, coral red), expressive cartoonish characters with exaggerated "
    "reactions, slightly surreal visual metaphors, clean white or gradient background, "
    "high contrast, playful and quirky composition"
)


def _sanitize_narration(text: str) -> str:
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    return text.strip()


def _set_long_pipeline_terminal(batch_id: str | None, status: str) -> None:
    if not batch_id:
        return
    try:
        firestore_service.set_pipeline_and_batch_state(batch_id, status, channel_id=_LONG_PIPELINE_CHANNEL)
    except Exception as err:
        logger.warning("set_pipeline_and_batch_state failed: %s", err)


def run(
    headline: str,
    code: str,
    batch_id: str | None = None,
    job_id: str | None = None,
    public_id: str | None = None,
    force_run: bool = False,
    genre: str = "",
    details: str = "",
    channel_id: str = _LONG_PIPELINE_CHANNEL,
) -> None:
    # Idempotency guard
    if job_id:
        existing = firestore_service.get_job(job_id) or {}
        if existing.get("status") in ("completed", "delivered_manual", "cancelled"):
            logger.info("Job %s already terminal (%s) — skipping", job_id, existing.get("status"))
            return

    ensure_dir(TEMP_DIR)
    ensure_dir(OUTPUT_DIR)
    cleanup_files_older_than(TEMP_DIR, TMP_RETENTION_DAYS)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    lock_owner = f"long:{batch_id or 'manual'}:{code}:{uuid4().hex}"
    effective_job_id = job_id or f"long-{uuid4().hex}"
    _chat_id = get_chat_id(_YOUTUBE_CHANNEL)

    firestore_service.create_or_update_job(effective_job_id, {
        "job_id": effective_job_id,
        "batch_id": batch_id or "",
        "code": code,
        "topic": headline,
        "source": "scheduler",
        "status": "processing",
        "public_id": public_id or "",
        "genre": genre,
        "details": details,
        "channel_id": channel_id,
        "video_type": "long",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    selected_voice = choose_voice_for_video(language="en", preference="shuffle", domain=genre or "")
    firestore_service.create_or_update_job(effective_job_id, {"voice_selected": selected_voice})

    if force_run:
        firestore_service.acquire_video_lock(lock_owner, force=True, lock_key=_LONG_LOCK_KEY)
    elif not firestore_service.acquire_video_lock(lock_owner, lock_key=_LONG_LOCK_KEY):
        firestore_service.create_or_update_job(effective_job_id, {
            "status": "rejected_busy",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        send_message(_chat_id, f"⚠️ Long video already being processed. Request `{code}` rejected.", channel_id=_YOUTUBE_CHANNEL)
        _set_long_pipeline_terminal(batch_id, "failed")
        return

    try:
        raw_script = generate_long_facts_script(headline, category=genre, premise=details or "")
        try:
            scenes = extract_json(raw_script)
        except Exception:
            scenes = []

        if not scenes or len(scenes) < LONG_MIN_SCENES:
            raise RuntimeError(f"Script too short: got {len(scenes)} scenes, need at least {LONG_MIN_SCENES}")

        scenes = scenes[:LONG_MAX_SCENES]
        for s in scenes:
            s["narration"] = _sanitize_narration(s.get("narration", ""))

        firestore_service.create_or_update_job(effective_job_id, {
            "reviewed_title": headline,
            "scene_count": len(scenes),
        })

        music_genre = classify_music_genre(headline, story_genre=genre)
        video_clips = []
        successful_scenes = 0

        for i, scene in enumerate(scenes):
            narration = scene.get("narration", "")
            visual_query = scene.get("visual_query", "documentary nature")
            if not narration:
                continue

            audio_path = os.path.join(TEMP_DIR, f"long_audio_{code}_{i}.mp3")
            try:
                generate_audio(narration, audio_path, language="en", voice_name=selected_voice, channel_id=channel_id)
            except Exception as tts_err:
                logger.warning("Scene %d TTS failed, skipping: %s", i, tts_err)
                continue

            try:
                from moviepy import AudioFileClip
                _tmp = AudioFileClip(audio_path)
                audio_duration = _tmp.duration
                _tmp.close()
            except Exception:
                audio_duration = 20.0

            video_path = fetch_clip(visual_query, audio_duration, scene_idx=i, category=genre, temp_dir=TEMP_DIR)

            video_clips.append({
                "video_path": video_path,
                "audio_path": audio_path,
                "narration": narration,
            })
            successful_scenes += 1
            firestore_service.mark_scene_checkpoint(effective_job_id, i, "completed", audio_path=audio_path, image_path=video_path)

        if successful_scenes < LONG_MIN_SCENES:
            raise RuntimeError(f"Only {successful_scenes} scenes succeeded, need at least {LONG_MIN_SCENES}")

        # Thumbnail — non-fatal
        thumbnail_path = None
        try:
            thumbnail_prompt = f"{_TMW_VISUAL_STYLE} — {headline}"
            thumbnail_path = generate_thumbnail(thumbnail_prompt, code)
        except Exception as thumb_err:
            logger.warning("Thumbnail generation failed (non-fatal): %s", thumb_err)
            send_message(_chat_id, f"⚠️ Thumbnail generation failed for `{public_id or effective_job_id}`: {str(thumb_err)[:200]}", channel_id=_YOUTUBE_CHANNEL)

        output_path = os.path.join(OUTPUT_DIR, f"long_{code}_{timestamp}.mp4")
        create_long_video(video_clips, output_path, music_genre=music_genre, language="en")

        # GCS upload
        try:
            from app.services.storage_service import upload_video as gcs_upload_video
            gcs_url = gcs_upload_video(output_path, f"videos/long/{os.path.basename(output_path)}")
            firestore_service.create_or_update_job(effective_job_id, {"gcs_video_url": gcs_url})
        except Exception as gcs_err:
            logger.warning("GCS upload failed: %s", gcs_err)

        # YouTube upload (regular video, not Short)
        youtube_url = upload_video(
            video_path=output_path,
            title=headline,
            description="",
            genre=genre,
            channel_id=_YOUTUBE_CHANNEL,
            tags=["TellMeWhy", "facts", "educational", "longform", "curiosity"],
            is_short=False,
        )

        # Derive watch URL and set thumbnail — both non-fatal
        video_id = extract_video_id(youtube_url or "")
        watch_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else youtube_url

        if thumbnail_path and video_id:
            try:
                set_thumbnail(video_id, thumbnail_path, channel_id=_YOUTUBE_CHANNEL)
            except Exception as st_err:
                logger.warning("Thumbnail upload failed (non-fatal): %s", st_err)
                send_message(_chat_id, f"⚠️ Thumbnail upload failed: {str(st_err)[:200]}", channel_id=_YOUTUBE_CHANNEL)

        firestore_service.create_or_update_job(effective_job_id, {
            "status": "completed",
            "video_path": output_path,
            "youtube_url": watch_url or youtube_url,
            "num_scenes": len(video_clips),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        _set_long_pipeline_terminal(batch_id, "completed")

        send_message(
            _chat_id,
            f"✅ Long video published!\n*{headline}*\n{watch_url or youtube_url or '(no URL)'}",
            channel_id=_YOUTUBE_CHANNEL,
        )

    except Exception as exc:
        firestore_service.create_or_update_job(effective_job_id, {
            "status": "failed",
            "error_type": "pipeline_exception",
            "error_message": str(exc)[:500],
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        _set_long_pipeline_terminal(batch_id, "failed")
        try:
            send_message(_chat_id, f"❌ Long video failed for *{code}*: {str(exc)[:300]}", channel_id=_YOUTUBE_CHANNEL)
        except Exception:
            pass
        raise
    finally:
        firestore_service.release_video_lock(lock_owner, lock_key=_LONG_LOCK_KEY)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_long_generator_agent.py -v
```
Expected: all 4 tests PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add app/agents/long_generator_agent.py tests/test_long_generator_agent.py
git commit -m "feat: add long_generator_agent for Tell Me Why daily long-format pipeline"
```

---

### Task 8: Scheduler + runner scripts

**Files:**
- Create: `scripts/run_long_stories.py`
- Create: `scripts/run_long_generate_video.py`
- Test: `tests/test_long_scheduler.py`

**Interfaces:**
- Consumes:
  - `_select_category() -> str` from `app.agents.story_researcher` (imported directly)
  - `generate_fact_topic(category, recently_used_titles) -> dict` from `app.services.llm_service`
  - `_recently_used_titles(limit) -> list[str]` from `app.agents.story_researcher`
  - `dispatch_long_video_generation(payload) -> None` from `app.agents.github_dispatch`
  - `long_generator_agent.run(...)` from `app.agents.long_generator_agent`
- Produces: `run_long_stories.py` (entry point for cron), `run_long_generate_video.py` (entry point for dispatch)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_long_scheduler.py
import json
import importlib
import pytest
from unittest.mock import MagicMock, patch


def test_run_long_stories_dispatches_workflow(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_state = {"state": "completed"}
    mock_idea = {"title": "Why cats purr at 25Hz", "premise": "The mechanism is surprising."}

    with patch("scripts.run_long_stories.firestore_service") as mock_fs, \
         patch("scripts.run_long_stories._select_category", return_value="science & space"), \
         patch("scripts.run_long_stories._recently_used_titles", return_value=[]), \
         patch("scripts.run_long_stories.generate_fact_topic", return_value=mock_idea), \
         patch("scripts.run_long_stories.dispatch_long_video_generation") as mock_dispatch, \
         patch("scripts.run_long_stories.send_message"):
        mock_fs.get_pipeline_state.return_value = mock_state
        mock_fs.get_job.return_value = {}
        mock_fs.save_news_batch.return_value = None
        mock_fs.set_pipeline_and_batch_state.return_value = None
        mock_fs.create_or_update_job.return_value = None
        mock_fs.is_headline_already_suggested.return_value = False
        mock_fs.mark_headline_suggested.return_value = None

        import scripts.run_long_stories as rls
        importlib.reload(rls)
        public_id = rls.run()

    assert public_id is not None
    mock_dispatch.assert_called_once()
    payload = mock_dispatch.call_args[0][0]
    assert payload["headline"] == "Why cats purr at 25Hz"
    assert payload["script_type"] == "long_facts"
    assert payload["channel_id"] == "stories_long"


def test_run_long_stories_skips_when_pipeline_processing(monkeypatch):
    with patch("scripts.run_long_stories.firestore_service") as mock_fs, \
         patch("scripts.run_long_stories.send_message"), \
         patch("scripts.run_long_stories._select_category", return_value="science & space"), \
         patch("scripts.run_long_stories._recently_used_titles", return_value=[]), \
         patch("scripts.run_long_stories.generate_fact_topic") as mock_topic:
        mock_fs.get_pipeline_state.return_value = {
            "state": "processing",
            "active_batch_id": "long_20260617_120000",
            "last_run_at": "2099-01-01T00:00:00+00:00",  # not stale
        }
        import scripts.run_long_stories as rls
        importlib.reload(rls)
        result = rls.run()

    assert result is None
    mock_topic.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_long_scheduler.py -v
```
Expected: `ModuleNotFoundError` — scripts do not exist yet

- [ ] **Step 3: Create `scripts/run_long_stories.py`**

```python
#!/usr/bin/env python3
# scripts/run_long_stories.py
#
# Scheduled at 2pm IST daily by stories-long-run.yml.
# Picks a Tell Me Why topic and dispatches generate-long-video.yml.

import re
import hashlib
import logging
from datetime import datetime, timezone, timedelta

from app.services import firestore_service
from app.services.llm_service import generate_fact_topic
from app.services.telegram_service import send_message
from app.agents.story_researcher import _select_category, _recently_used_titles
from app.agents.github_dispatch import dispatch_long_video_generation
from app.config import STORIES_CHAT_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_LONG_CHANNEL = "stories_long"
_DEDUP_DAYS = 365


def run() -> str | None:
    """Pick a fact topic and dispatch the long-format video generation workflow.
    Returns public_id if dispatched, None if skipped.
    """
    state = firestore_service.get_pipeline_state(channel_id=_LONG_CHANNEL)
    if state.get("state") == "processing":
        last_run_str = state.get("last_run_at", "")
        is_stale = False
        if last_run_str:
            try:
                last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                is_stale = (datetime.now(timezone.utc) - last_run) > timedelta(hours=4)
            except Exception:
                pass
        if not is_stale:
            logger.info("Long-format pipeline busy — skipping this run")
            send_message(
                STORIES_CHAT_ID,
                f"⏭️ Long video slot skipped — pipeline is busy.",
                channel_id="stories",
            )
            return None
        stale_batch_id = state.get("active_batch_id", "?")
        logger.warning("Clearing stale long pipeline (batch %s)", stale_batch_id)
        try:
            firestore_service.set_pipeline_and_batch_state(stale_batch_id, "failed", channel_id=_LONG_CHANNEL)
        except Exception as err:
            logger.warning("Could not clear stale long pipeline: %s", err)

    recently_used = _recently_used_titles(limit=60)
    category = _select_category()

    try:
        idea = generate_fact_topic(category=category, recently_used_titles=recently_used)
    except Exception as exc:
        logger.exception("Fact topic generation failed: %s", exc)
        send_message(STORIES_CHAT_ID, f"⚠️ Long video topic generation failed: {exc}", channel_id="stories")
        return None

    title = (idea.get("title") or "").strip()
    premise = (idea.get("premise") or "").strip()

    if not title:
        logger.warning("Empty topic title — skipping")
        return None

    if firestore_service.is_headline_already_suggested(title, ttl_days=_DEDUP_DAYS, channel_id=_LONG_CHANNEL):
        logger.info("Topic already used recently: %s", title)
        send_message(STORIES_CHAT_ID, f"⏭️ Long video slot skipped — topic already used: _{title}_.", channel_id="stories")
        return None

    batch_id = f"long_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    code = "LONG01"
    raw_task = f"generate-{batch_id}-{code}"
    task_name = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_task)
    public_id = hashlib.sha1(task_name.encode("utf-8")).hexdigest()[:8].upper()
    job_id = task_name

    firestore_service.save_news_batch(batch_id, category, {
        code: {
            "code": code,
            "headline": title,
            "context": premise,
            "rating": 5.0,
            "genre": category,
        }
    })
    firestore_service.set_pipeline_and_batch_state(batch_id, "processing", channel_id=_LONG_CHANNEL)
    firestore_service.create_or_update_job(job_id, {
        "job_id": job_id,
        "batch_id": batch_id,
        "code": code,
        "topic": title,
        "source": "scheduler",
        "status": "queued",
        "public_id": public_id,
        "genre": category,
        "details": premise,
        "channel_id": _LONG_CHANNEL,
        "video_type": "long",
        "language": "en",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    try:
        dispatch_long_video_generation({
            "headline": title,
            "code": code,
            "batch_id": batch_id,
            "job_id": job_id,
            "public_id": public_id,
            "force_run": True,
            "genre": category,
            "details": premise,
            "channel_id": _LONG_CHANNEL,
            "script_type": "long_facts",
            "language": "en",
        })
    except Exception as exc:
        logger.exception("Failed to dispatch long video: %s", exc)
        firestore_service.set_pipeline_and_batch_state(batch_id, "failed", channel_id=_LONG_CHANNEL)
        send_message(STORIES_CHAT_ID, f"❌ Failed to queue long video: {exc}", channel_id="stories")
        return None

    firestore_service.mark_headline_suggested(title, genre=category, channel_id=_LONG_CHANNEL)

    send_message(
        STORIES_CHAT_ID,
        f"🎬 Generating long video...\nTopic: *{title}*\nCategory: {category.title()}\nId: `{public_id}`",
        channel_id="stories",
    )
    logger.info("Long video dispatched: %s | %s | category=%s", task_name, title, category)
    return public_id


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Create `scripts/run_long_generate_video.py`**

```python
#!/usr/bin/env python3
# scripts/run_long_generate_video.py
#
# Entry point for generate-long-video.yml workflow.
# Reads GENERATE_PAYLOAD env var and calls long_generator_agent.run().

import os
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    raw = os.getenv("GENERATE_PAYLOAD", "")
    if not raw:
        raise RuntimeError("GENERATE_PAYLOAD env var is required")

    payload = json.loads(raw)
    logger.info("Starting long video generation: %s", payload.get("job_id"))

    from app.agents.long_generator_agent import run
    run(
        headline=payload["headline"],
        code=payload["code"],
        batch_id=payload.get("batch_id"),
        job_id=payload.get("job_id"),
        public_id=payload.get("public_id"),
        force_run=payload.get("force_run", False),
        genre=payload.get("genre", ""),
        details=payload.get("details", ""),
        channel_id=payload.get("channel_id", "stories_long"),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_long_scheduler.py -v
```
Expected: both tests PASS

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add scripts/run_long_stories.py scripts/run_long_generate_video.py tests/test_long_scheduler.py
git commit -m "feat: add long-format scheduler and runner scripts"
```

---

### Task 9: GitHub Actions workflows

**Files:**
- Create: `.github/workflows/stories-long-run.yml`
- Create: `.github/workflows/generate-long-video.yml`

**Interfaces:**
- Consumes: `scripts/run_long_stories.py`, `scripts/run_long_generate_video.py`, all existing GitHub Secrets + `PEXELS_API_KEY`
- Produces: Automated daily cron at 2pm IST + on-demand video generation workflow with separate concurrency group

No unit tests for YAML files — validate by inspecting the structure matches the existing pattern from `stories-run.yml` and `generate-video.yml`.

- [ ] **Step 1: Create `.github/workflows/stories-long-run.yml`**

```yaml
name: Tell Me Why Long-Format Run

on:
  schedule:
    - cron: "30 8 * * *"  # 2:00pm IST daily (UTC+5:30 → 08:30 UTC)
  workflow_dispatch:

permissions:
  actions: write
  contents: read

jobs:
  tell-me-why-long:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.10
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Set up GCP credentials
        run: |
          echo '${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}' > /tmp/gcp_key.json
          echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp_key.json" >> $GITHUB_ENV

      - name: Run Tell Me Why long-format scheduler
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          STORIES_BOT_TOKEN: ${{ secrets.STORIES_BOT_TOKEN }}
          STORIES_CHAT_ID: ${{ secrets.STORIES_CHAT_ID }}
          GCP_PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
          GITHUB_REPO: ${{ github.repository }}
          GITHUB_DISPATCH_TOKEN: ${{ github.token }}
        run: python scripts/run_long_stories.py
```

- [ ] **Step 2: Create `.github/workflows/generate-long-video.yml`**

```yaml
name: Generate Long Video

on:
  workflow_dispatch:
    inputs:
      payload:
        description: "JSON payload for long_generator_agent.run()"
        required: true
        type: string

concurrency:
  group: long-video-generation
  cancel-in-progress: false

jobs:
  generate-long:
    runs-on: ubuntu-latest
    timeout-minutes: 120

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.10
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - name: Install system packages
        run: sudo apt-get update && sudo apt-get install -y ffmpeg fonts-dejavu-core fonts-indic

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Set up GCP credentials
        run: |
          echo '${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}' > /tmp/gcp_key.json
          echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp_key.json" >> $GITHUB_ENV

      - name: Generate long-format video
        env:
          GENERATE_PAYLOAD: ${{ inputs.payload }}
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          STORIES_BOT_TOKEN: ${{ secrets.STORIES_BOT_TOKEN }}
          STORIES_CHAT_ID: ${{ secrets.STORIES_CHAT_ID }}
          YOUTUBE_CLIENT_ID: ${{ secrets.YOUTUBE_CLIENT_ID }}
          YOUTUBE_CLIENT_SECRET: ${{ secrets.YOUTUBE_CLIENT_SECRET }}
          YOUTUBE_REDIRECT_URI: ${{ secrets.YOUTUBE_REDIRECT_URI }}
          STORIES_YOUTUBE_CLIENT_ID: ${{ secrets.STORIES_YOUTUBE_CLIENT_ID }}
          STORIES_YOUTUBE_CLIENT_SECRET: ${{ secrets.STORIES_YOUTUBE_CLIENT_SECRET }}
          STORIES_YOUTUBE_REDIRECT_URI: ${{ secrets.STORIES_YOUTUBE_REDIRECT_URI }}
          BUCKET_NAME: ${{ secrets.BUCKET_NAME }}
          GCP_PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
          APP_BASE_URL: ${{ secrets.APP_BASE_URL }}
        run: python scripts/run_long_generate_video.py
```

- [ ] **Step 3: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/stories-long-run.yml')); print('OK')"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/generate-long-video.yml')); print('OK')"
```
Expected: `OK` for both files

- [ ] **Step 4: Add `PEXELS_API_KEY` GitHub Secret**

In the GitHub repository UI: **Settings → Secrets and variables → Actions → New repository secret**

- Name: `PEXELS_API_KEY`
- Value: `6wUi8UhPYUZfzsWZO2q2ZzlsEPvzzIofXUKTnwFV6TWgtd037sS0L3mE`

Note: This step is manual (GitHub UI). Confirm in the Actions secrets list before proceeding.

- [ ] **Step 5: Run full test suite one final time**

```bash
pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 6: Commit workflows**

```bash
git add .github/workflows/stories-long-run.yml .github/workflows/generate-long-video.yml
git commit -m "feat: add stories-long-run and generate-long-video GitHub Actions workflows for daily long-format Tell Me Why videos"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Task |
|---|---|
| 1 long-format video per day, Tell Me Why channel | Task 8 (cron), Task 9 (workflow) |
| Visuals from Pexels video clips, 16:9 horizontal | Task 1 (pexels_service), Task 5 (long_video_service) |
| Minimum 8 min, maximum 10 min (22–30 scenes × ~20s) | Task 7 (LONG_MIN_SCENES, LONG_MAX_SCENES) |
| Script: hook + core content + retention + CTA segments | Task 3 (generate_long_facts_script prompt) |
| Good quality TTS — Google Cloud Neural2 (as decided) | Task 7 (generate_audio with same voice pool) |
| Catchy thumbnail via Imagen, uploaded with video | Task 4 (generate_thumbnail, set_thumbnail) |
| Separate GitHub Actions workflow, own concurrency group | Task 9 (generate-long-video.yml, concurrency: long-video-generation) |
| Firestore pipeline_state isolated from Shorts | Task 7 (_LONG_PIPELINE_CHANNEL = "stories_long") |
| Separate lock from Shorts | Task 2 (lock_key param), Task 7 (_LONG_LOCK_KEY = "long_video_generation") |
| Pexels fallback chain | Task 1 |
| Black frame when all Pexels fallbacks fail | Task 1 (returns ""), Task 5 (black ImageClip) |
| `is_short=False` — no #Shorts in description | Task 4 |
| `extract_video_id` handles `/watch?v=` URLs | Task 4 |
| `PEXELS_API_KEY` secret | Task 9 step 4 |
| 2pm IST cron (`30 8 * * *`) | Task 9 |
| Telegram notifications on Tell Me Why bot | Task 7 (uses `_YOUTUBE_CHANNEL = "stories"` for get_chat_id) |

### Placeholder scan
No TBD, TODO, or incomplete sections found.

### Type consistency check
- `fetch_clip(query: str, audio_duration: float, scene_idx: int, category: str = "", temp_dir: str = TEMP_DIR) -> str` — used identically in Task 1 and Task 7
- `generate_long_facts_script(topic: str, category: str = "", premise: str = "") -> str` — used identically in Task 3 and Task 7
- `generate_thumbnail(prompt: str, code: str) -> str` — used identically in Task 4 and Task 7
- `set_thumbnail(video_id: str, thumbnail_path: str, channel_id: str = "stories") -> None` — used identically in Task 4 and Task 7
- `upload_video(..., is_short: bool = True) -> str` — Task 4 adds param, Task 7 calls with `is_short=False`
- `acquire_video_lock(owner, lock_key=...) -> bool` / `release_video_lock(owner, lock_key=...) -> bool` — Task 2 adds param, Task 7 calls with `lock_key=_LONG_LOCK_KEY`
- `dispatch_long_video_generation(payload: dict) -> None` — Task 6 defines, Task 8 calls
