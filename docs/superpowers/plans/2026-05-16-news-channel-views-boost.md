# News Channel Views Boost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increase YouTube views by replacing the keyword-based trend bonus with real Google Trends scores, and sharpening LLM prompts for higher CTR titles and stronger hooks.

**Architecture:** New `trends_service.py` fetches per-topic Google Trends scores (0.0–1.0) and the composite scoring formula in `lead_researcher.py` uses those instead of `_trend_bonus()`. Three prompt edits in `llm_service.py` improve title CTR, scene-1 hook strength, and article virality scoring — all within existing LLM calls.

**Tech Stack:** Python, pytrends (unofficial Google Trends client), Gemini 2.5 Flash (existing), pytest

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `requirements.txt` | Modify | Add `pytrends` dependency |
| `tests/conftest.py` | Modify | Add `pytrends` + `pytrends.request` to `_EXTERNAL_MOCKS` so tests don't hit real network |
| `app/services/trends_service.py` | Create | `get_trend_scores(topics) -> dict[str, float]` — queries Google Trends, normalises, falls back safely |
| `tests/test_trends_service.py` | Create | Unit tests for normalisation, neutral default, and exception fallback |
| `app/agents/lead_researcher.py` | Modify | Import `get_trend_scores`; remove `_trend_bonus`; call `get_trend_scores` after rating; update composite formula |
| `tests/test_pipeline.py` | Modify | Add `@patch("app.agents.lead_researcher.get_trend_scores")` to the existing `test_lead_researcher_run_creates_batch_and_enqueues_video` test |
| `app/services/llm_service.py` | Modify | (C3) Sharpen `rate_and_select_news` virality rubric; (C1) add title CTR pattern rubric; (C2) tighten scene-1 hook instruction |

---

## Task 1: Add pytrends to requirements and mock it in conftest

**Files:**
- Modify: `requirements.txt`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add pytrends to requirements.txt**

Open `requirements.txt` and append one line at the end:

```
pytrends
```

Full file after change:
```
fastapi
uvicorn[standard]
vertexai
google-cloud-storage
google-cloud-texttospeech
moviepy
Pillow
requests
python-multipart
google-cloud-firestore
google-api-python-client
google-auth-oauthlib
httpx
pytrends
```

- [ ] **Step 2: Mock pytrends in conftest.py**

In `tests/conftest.py`, add two entries to `_EXTERNAL_MOCKS` so pytrends never makes real HTTP calls in tests:

Find the block:
```python
_EXTERNAL_MOCKS = {
    "vertexai": MagicMock(),
    "vertexai.generative_models": MagicMock(),
    "vertexai.preview": MagicMock(),
    "vertexai.preview.vision_models": MagicMock(),
    "google": MagicMock(),
    "google.cloud": MagicMock(),
    "google.cloud.texttospeech": MagicMock(),
    "google.cloud.storage": MagicMock(),
    "moviepy": MagicMock(),
    "moviepy.editor": MagicMock(),
    "moviepy.audio": MagicMock(),
    "moviepy.audio.fx": MagicMock(),
    "moviepy.audio.fx.all": MagicMock(),
}
```

Replace with:
```python
_EXTERNAL_MOCKS = {
    "vertexai": MagicMock(),
    "vertexai.generative_models": MagicMock(),
    "vertexai.preview": MagicMock(),
    "vertexai.preview.vision_models": MagicMock(),
    "google": MagicMock(),
    "google.cloud": MagicMock(),
    "google.cloud.texttospeech": MagicMock(),
    "google.cloud.storage": MagicMock(),
    "moviepy": MagicMock(),
    "moviepy.editor": MagicMock(),
    "moviepy.audio": MagicMock(),
    "moviepy.audio.fx": MagicMock(),
    "moviepy.audio.fx.all": MagicMock(),
    "pytrends": MagicMock(),
    "pytrends.request": MagicMock(),
}
```

- [ ] **Step 3: Install pytrends locally**

```bash
pip install pytrends
```

Expected: installs without error.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt tests/conftest.py
git commit -m "chore: add pytrends dependency and mock it in test conftest"
```

---

## Task 2: Create trends_service.py (TDD)

**Files:**
- Create: `tests/test_trends_service.py`
- Create: `app/services/trends_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trends_service.py`:

```python
import sys
import time
from unittest.mock import MagicMock, patch


def _make_mock_df(values_by_topic: dict[str, float]) -> MagicMock:
    """Build a mock DataFrame where df[topic].mean() returns the given value."""
    mock_df = MagicMock()

    def getitem(key):
        series = MagicMock()
        series.mean.return_value = values_by_topic.get(key, 0.0)
        return series

    mock_df.__getitem__ = MagicMock(side_effect=getitem)
    return mock_df


def _install_mock_trend_req(df_by_topic: dict[str, float]):
    """Patch sys.modules["pytrends.request"].TrendReq to return a mock that
    yields df_by_topic values from interest_over_time()."""
    mock_instance = MagicMock()
    mock_instance.interest_over_time.return_value = _make_mock_df(df_by_topic)
    sys.modules["pytrends.request"].TrendReq = MagicMock(return_value=mock_instance)
    return mock_instance


@patch("time.sleep")
def test_get_trend_scores_normalises_to_one(mock_sleep):
    """Highest-interest topic scores 1.0; others are proportionally lower."""
    _install_mock_trend_req({"AI news": 100.0, "Tech update": 50.0})

    from app.services import trends_service
    scores = trends_service.get_trend_scores(["AI news", "Tech update"])

    assert scores["AI news"] == 1.0
    assert 0.0 < scores["Tech update"] < 1.0
    assert scores["Tech update"] == 0.5


@patch("time.sleep")
def test_get_trend_scores_returns_neutral_for_zero_interest(mock_sleep):
    """Topics with 0 mean interest get the neutral default 0.2, not 0.0."""
    _install_mock_trend_req({"Popular topic": 80.0, "No data topic": 0.0})

    from app.services import trends_service
    scores = trends_service.get_trend_scores(["Popular topic", "No data topic"])

    assert scores["No data topic"] == 0.2


@patch("time.sleep")
def test_get_trend_scores_all_zero_returns_neutral_default(mock_sleep):
    """When all topics have 0 interest, every score is the neutral default."""
    _install_mock_trend_req({"Topic A": 0.0, "Topic B": 0.0})

    from app.services import trends_service
    scores = trends_service.get_trend_scores(["Topic A", "Topic B"])

    assert scores["Topic A"] == 0.2
    assert scores["Topic B"] == 0.2


@patch("time.sleep")
def test_get_trend_scores_falls_back_on_pytrends_exception(mock_sleep):
    """Any pytrends exception returns neutral default for all topics."""
    sys.modules["pytrends.request"].TrendReq = MagicMock(
        side_effect=Exception("network error")
    )

    from app.services import trends_service
    scores = trends_service.get_trend_scores(["Some topic"])

    assert scores["Some topic"] == 0.2


def test_get_trend_scores_empty_input():
    """Empty input returns empty dict without calling pytrends."""
    from app.services import trends_service
    assert trends_service.get_trend_scores([]) == {}


@patch("time.sleep")
def test_get_trend_scores_sleeps_between_queries(mock_sleep):
    """Verifies sleep is called once per topic to avoid rate-limiting."""
    _install_mock_trend_req({"Topic A": 50.0, "Topic B": 30.0})

    from app.services import trends_service
    trends_service.get_trend_scores(["Topic A", "Topic B"])

    assert mock_sleep.call_count == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_trends_service.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` for `app.services.trends_service` (file doesn't exist yet).

- [ ] **Step 3: Implement trends_service.py**

Create `app/services/trends_service.py`:

```python
# app/services/trends_service.py

import time
import logging

logger = logging.getLogger(__name__)

_NEUTRAL_DEFAULT = 0.2
_SLEEP_BETWEEN_QUERIES = 1.0


def get_trend_scores(topics: list[str]) -> dict[str, float]:
    """Return a 0.0–1.0 Google Trends interest score for each topic.

    Scores are normalised against the batch maximum so the hottest topic
    always scores 1.0. Topics with no Trends data score _NEUTRAL_DEFAULT.
    A 1-second sleep between queries avoids pytrends rate-limiting.
    Any exception returns _NEUTRAL_DEFAULT for all topics — the research
    pipeline continues using LLM rating + recency only.
    """
    if not topics:
        return {}
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=330)  # tz=330 is IST offset
        raw: dict[str, float] = {}
        for topic in topics:
            try:
                pytrends.build_payload([topic], timeframe="now 7-d")
                df = pytrends.interest_over_time()
                try:
                    raw[topic] = float(df[topic].mean())
                except Exception:
                    raw[topic] = 0.0
                time.sleep(_SLEEP_BETWEEN_QUERIES)
            except Exception:
                raw[topic] = 0.0
        max_val = max(raw.values()) if raw else 0.0
        if max_val <= 0:
            return {t: _NEUTRAL_DEFAULT for t in topics}
        return {
            t: round(raw.get(t, 0.0) / max_val, 3) or _NEUTRAL_DEFAULT
            for t in topics
        }
    except Exception as exc:
        logger.warning(f"trends_service: pytrends unavailable, using neutral defaults: {exc}")
        return {t: _NEUTRAL_DEFAULT for t in topics}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_trends_service.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/trends_service.py tests/test_trends_service.py
git commit -m "feat: add trends_service with Google Trends scoring and fallback"
```

---

## Task 3: Wire get_trend_scores into lead_researcher.py

**Files:**
- Modify: `app/agents/lead_researcher.py:7` (imports)
- Modify: `app/agents/lead_researcher.py:458-461` (scoring loop)
- Modify: `tests/test_pipeline.py:319-351` (patch get_trend_scores)

- [ ] **Step 1: Update the existing lead_researcher test to patch get_trend_scores**

In `tests/test_pipeline.py`, find the block starting at line 319:

```python
@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch("app.agents.lead_researcher.send_message")
@patch("app.agents.lead_researcher.firestore_service")
@patch("app.agents.lead_researcher.rate_and_select_news")
@patch("app.agents.lead_researcher.gnews_service")
@patch("app.agents.lead_researcher._within_suggestion_window", return_value=True)
def test_lead_researcher_run_creates_batch_and_enqueues_video(
    mock_window, mock_gnews, mock_rate, mock_fs, mock_send_message, mock_enqueue
):
```

Replace with (adds one more `@patch` decorator and one parameter):

```python
@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch("app.agents.lead_researcher.send_message")
@patch("app.agents.lead_researcher.firestore_service")
@patch("app.agents.lead_researcher.rate_and_select_news")
@patch("app.agents.lead_researcher.gnews_service")
@patch("app.agents.lead_researcher._within_suggestion_window", return_value=True)
@patch("app.agents.lead_researcher.get_trend_scores", return_value={f"Top Pick {i}": 0.5 for i in range(1, 6)})
def test_lead_researcher_run_creates_batch_and_enqueues_video(
    mock_trends, mock_window, mock_gnews, mock_rate, mock_fs, mock_send_message, mock_enqueue
):
```

Note: `@patch` decorators apply bottom-up, so `mock_trends` is the first parameter after `self` (or the first parameter in a function).

- [ ] **Step 2: Run the existing test suite to confirm nothing is broken yet**

```bash
pytest tests/test_pipeline.py::test_lead_researcher_run_creates_batch_and_enqueues_video -v
```

Expected: PASS (the test patches `get_trend_scores` which doesn't exist in `lead_researcher` yet, but the import hasn't changed yet so the patch target won't be found — it will error with `AttributeError`).

This is the expected failure — it confirms the test is wired correctly for the next step.

- [ ] **Step 3: Update lead_researcher.py — import and replace _trend_bonus**

In `app/agents/lead_researcher.py`, update the import block at the top (lines 1–10):

```python
# app/agents/lead_researcher.py

import logging
import random
from datetime import datetime, timezone, timedelta
from app.services import gnews_service, firestore_service
from app.services.llm_service import rate_and_select_news
from app.services.trends_service import get_trend_scores
from app.services.telegram_service import send_message
from app.config import TELEGRAM_CHAT_ID
```

Then find the scoring loop in `run()` (around line 444–467):

```python
        rated = rate_and_select_news(
            candidates, top_performers=top_performers, recently_covered=recently_covered
        )[:5]
        for item in rated:
            orig = _orig_lookup.get(_norm_headline(item.get("headline", ""))) or {}
            item.setdefault("published_at", orig.get("published_at", ""))
            item.setdefault("url", orig.get("url", ""))
            item.setdefault("source", orig.get("source", ""))
        enriched = []
        for item in rated:
            score = float(item.get("rating", 0))
            if score < 3.8:
                continue
            rigorous = (
                (score * 0.60)
                + (_recency_score(item) * 2.0)
                + _trend_bonus(item.get("headline", ""))
            )
            enriched.append({
                **item,
                "genre": domain,
                "rigorous_score": round(min(5.0, rigorous), 2),
            })
```

Replace with:

```python
        rated = rate_and_select_news(
            candidates, top_performers=top_performers, recently_covered=recently_covered
        )[:5]
        for item in rated:
            orig = _orig_lookup.get(_norm_headline(item.get("headline", ""))) or {}
            item.setdefault("published_at", orig.get("published_at", ""))
            item.setdefault("url", orig.get("url", ""))
            item.setdefault("source", orig.get("source", ""))
        headlines = [item.get("headline", "") for item in rated]
        trend_scores = get_trend_scores(headlines)
        enriched = []
        for item in rated:
            score = float(item.get("rating", 0))
            if score < 3.8:
                continue
            trend_score = trend_scores.get(item.get("headline", ""), 0.2)
            rigorous = (
                (score * 0.55)
                + (_recency_score(item) * 1.8)
                + (trend_score * 0.8)
            )
            enriched.append({
                **item,
                "genre": domain,
                "rigorous_score": round(min(5.0, rigorous), 2),
            })
```

- [ ] **Step 4: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS. The `_trend_bonus` function is still defined in the file — that's fine, it's just unused now. It can stay (removing it would be a separate cleanup commit).

- [ ] **Step 5: Commit**

```bash
git add app/agents/lead_researcher.py tests/test_pipeline.py
git commit -m "feat: replace _trend_bonus with real Google Trends scoring in lead_researcher"
```

---

## Task 4: C3 — Sharpen rate_and_select_news virality rubric

**Files:**
- Modify: `app/services/llm_service.py:886-892`

- [ ] **Step 1: Update the prompt in rate_and_select_news**

In `app/services/llm_service.py`, find the prompt block inside `rate_and_select_news()` (around line 883):

```python
    prompt = f"""You are a senior news editor selecting stories for short educational videos.
TODAY'S DATE: {today_str}. Only select stories that are genuinely recent as of today. Penalise articles about events that have fully concluded weeks ago with no new angle.

Rate each article on a combined 1–5 scale using these criteria:
- Virality & public interest (will people share this?)
- Educational value (does it teach something specific and useful?)
- Freshness (is this breaking or very recent news as of {today_str}?)
- Story depth (is there a concrete fact, number, or consequence — not just a vague headline?)
- Content fatigue: heavily penalise stories already covered recently (see list below if present)
{performers_block}{fatigue_block}
```

Replace with:

```python
    prompt = f"""You are a senior news editor selecting stories for short educational videos.
TODAY'S DATE: {today_str}. Only select stories that are genuinely recent as of today. Penalise articles about events that have fully concluded weeks ago with no new angle.

Rate each article on a combined 1–5 scale using these criteria:
- Virality & public interest: score HIGHER for headlines with a specific number, named person, or named organisation (concrete beats vague); a clear "why this affects you" angle; genuine novelty not just recency. Score LOWER for vague headlines with no named entity (e.g. "Scientists discover new thing") and pure recaps with no new development.
- Educational value (does it teach something specific and useful?)
- Freshness (is this breaking or very recent news as of {today_str}?)
- Story depth (is there a concrete fact, number, or consequence — not just a vague headline?)
- Content fatigue: heavily penalise stories already covered recently (see list below if present)
{performers_block}{fatigue_block}
```

- [ ] **Step 2: Run the existing rate_and_select_news test**

```bash
pytest tests/test_pipeline.py::test_rate_and_select_news_returns_five_items -v
```

Expected: PASS (the test mocks `_get_model()` so the prompt text is not validated — confirms no import errors were introduced).

- [ ] **Step 3: Commit**

```bash
git add app/services/llm_service.py
git commit -m "feat: sharpen rate_and_select_news with explicit YouTube virality rubric"
```

---

## Task 5: C1 — Title CTR pattern rubric

**Files:**
- Modify: `app/services/llm_service.py:1044`

- [ ] **Step 1: Update the title instruction in review_title_and_caption_with_senior_reviewer**

In `app/services/llm_service.py`, find the prompt inside `review_title_and_caption_with_senior_reviewer()` (around line 1041):

```python
    prompt = f"""
You are a senior script reviewer.
Create:
1) A catchy but non-clickbait YouTube Shorts title — include the subject's name or key identifier if present in the script.
2) A reader-friendly caption aligned with the voiceover script (same core points), preserving all specific names, numbers, and facts.
3) 10-15 relevant hashtags derived from the script content and topic — mix broad popular tags with niche-specific ones.{genre_hint}
```

Replace with:

```python
    prompt = f"""
You are a senior script reviewer.
Create:
1) A YouTube Shorts title that maximises click-through rate. Use one of these proven patterns — pick whichever fits the script facts best:
   - Number/stat: "X Countries Just Banned This AI Tool"
   - Curiosity gap: "The Real Reason NASA Delayed This Launch"
   - Specificity: "OpenAI's $6.6B Deal — What It Actually Means"
   - Stakes: "This Ruling Could Change How You Use the Internet"
   Constraints: max 70 characters; use only facts present in the script; no fabrication; no generic openers like "Breaking:", "This Is", or "Here's Why".
2) A reader-friendly caption aligned with the voiceover script (same core points), preserving all specific names, numbers, and facts.
3) 10-15 relevant hashtags derived from the script content and topic — mix broad popular tags with niche-specific ones.{genre_hint}
```

- [ ] **Step 2: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add app/services/llm_service.py
git commit -m "feat: add CTR-pattern rubric to title generation prompt"
```

---

## Task 6: C2 — Scene-1 hook enforcement

**Files:**
- Modify: `app/services/llm_service.py:81` (`generate_script` format_hint)
- Modify: `app/services/llm_service.py:226` (`generate_script_with_search` format_hint)

Both functions have an identical `format_hint` block for `aspect_ratio == "9:16"`. Both must be updated.

- [ ] **Step 1: Update generate_script format_hint (first occurrence, ~line 78)**

Find in `generate_script()`:

```python
    if aspect_ratio == "9:16":
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Scene 1: open with the single most compelling fact or question — hook the viewer immediately\n"
            "- Scenes 2–4: each must reveal a specific, concrete insight, fact, number, or implication — no filler\n"
            "- Final scene: strong closing insight or call-to-reflection — not a generic sign-off\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"
    else:
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"
```

Replace with:

```python
    if aspect_ratio == "9:16":
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Scene 1: first sentence must be 12 words or fewer — a specific number, a named person doing something surprising, or a direct question. No scene-setting, no context-building. The viewer decides to stay or swipe in the first 2 seconds.\n"
            "- Scenes 2–4: each must reveal a specific, concrete insight, fact, number, or implication — no filler\n"
            "- Final scene: strong closing insight or call-to-reflection — not a generic sign-off\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"
    else:
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"
```

- [ ] **Step 2: Update generate_script_with_search format_hint (second occurrence, ~line 223)**

Find in `generate_script_with_search()`:

```python
    if aspect_ratio == "9:16":
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Scene 1: open with the single most compelling fact or question — hook the viewer immediately\n"
            "- Scenes 2–4: each must reveal a specific, concrete insight, fact, number, or implication — no filler\n"
            "- Final scene: strong closing insight or call-to-reflection — not a generic sign-off\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"
    else:
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"
```

Replace with (identical change — same new scene-1 line):

```python
    if aspect_ratio == "9:16":
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Scene 1: first sentence must be 12 words or fewer — a specific number, a named person doing something surprising, or a direct question. No scene-setting, no context-building. The viewer decides to stay or swipe in the first 2 seconds.\n"
            "- Scenes 2–4: each must reveal a specific, concrete insight, fact, number, or implication — no filler\n"
            "- Final scene: strong closing insight or call-to-reflection — not a generic sign-off\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"
    else:
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add app/services/llm_service.py
git commit -m "feat: enforce 12-word scene-1 hook in generate_script and generate_script_with_search"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ A — pytrends scoring: Task 1 (dependency), Task 2 (service + tests), Task 3 (wired into lead_researcher)
- ✅ C1 — Title CTR rubric: Task 5
- ✅ C2 — Scene-1 hook: Task 6
- ✅ C3 — Virality rubric: Task 4
- ✅ Error handling (pytrends outage → neutral default): covered in Task 2 implementation + test
- ✅ Composite formula updated: Task 3 (0.55 / 1.8 / 0.8 weights)
- ✅ Stories channel untouched: no changes to stories_agent, story_researcher, or stories paths
- ✅ Tests for trends_service: 6 tests in Task 2

**No placeholders** — all steps contain exact code.

**Type consistency** — `get_trend_scores(topics: list[str]) -> dict[str, float]` is defined in Task 2 and used as `get_trend_scores(headlines)` in Task 3. `headlines` is `list[str]`. Return is used as `trend_scores.get(item.get("headline", ""), 0.2)` — correct.
