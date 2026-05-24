# Tell Me Why Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Hindi Stories pipeline with an English facts channel (@TellMeWhy-in) that posts 4 search-grounded "interesting/unbelievable facts" YouTube Shorts per day across 12 rotating topic categories.

**Architecture:** `channel_id="stories"` is retained unchanged throughout Firestore, Telegram bot, and Vercel routing. A new `script_type="facts"` is added to route fact video generation through `generate_script_with_search()` with a facts-oriented prompt and category-appropriate visual style. `story_researcher.py` is fully rewritten to generate fact topics via a new `generate_fact_topic()` LLM function instead of story ideas. The News channel (`channel_id="news"`, `script_type="news"`) is untouched.

**Tech Stack:** Python, Gemini 2.5 Flash (Vertex AI), Google Search grounding, Firestore, GitHub Actions, Telegram Bot API

---

## File Map

| File | Change |
|---|---|
| `app/services/llm_service.py` | Add `generate_fact_topic()`, `_FACT_VISUAL_STYLE_POOL_CINEMATIC`, `_FACT_VISUAL_STYLE_POOL_ILLUSTRATED`, `_CINEMATIC_CATEGORIES`, `_CTA_FACTS_EN`; update `get_cta_narration()` routing; add `visual_style_override` + `script_mode` params to `generate_script_with_search()` |
| `app/agents/story_researcher.py` | Full rewrite: `_FACT_CATEGORIES` replaces `_STORY_GENRES`, `generate_fact_topic()` replaces `generate_story_idea()`, dispatch `script_type="facts"` + `language="en"`, remove daily cap |
| `app/agents/generator_agent.py` | Add `elif script_type == "facts":` branch with cinematic/illustrated style selection; enable fact-check pass |
| `app/agents/stories_agent.py` | Update `send_post_result()` display strings: "Short Tales" → "Tell Me Why" |
| `tests/test_story_researcher_dispatch.py` | Update assertions: `script_type` → `"facts"`, `language` → `"en"` |
| `.github/workflows/stories-run.yml` | Update comment from "Hindi story" to "Tell Me Why facts" |

---

## Task 1: llm_service.py — Add fact generation functions and update CTA routing

**Files:**
- Modify: `app/services/llm_service.py`

- [ ] **Step 1.1: Write the failing test for `generate_fact_topic()`**

Create `tests/test_llm_fact_topic.py`:

```python
import pytest
from unittest.mock import patch, MagicMock


def _mock_model(json_text: str):
    m = MagicMock()
    m.generate_content.return_value.text = json_text
    return m


def test_generate_fact_topic_returns_title_and_premise(monkeypatch):
    from app.services import llm_service
    json_resp = '{"title": "Why do humans yawn when others yawn?", "premise": "Mirror neurons fire in response to observed behaviour — yawning spreads through social contagion."}'
    with patch.object(llm_service, "_get_model", return_value=_mock_model(json_resp)):
        result = llm_service.generate_fact_topic("psychology & dark psychology", recently_used_titles=[])
    assert result["title"] == "Why do humans yawn when others yawn?"
    assert "Mirror neurons" in result["premise"]


def test_generate_fact_topic_avoids_recently_used(monkeypatch):
    from app.services import llm_service
    json_resp = '{"title": "Fresh topic", "premise": "A brand new fact about the brain that nobody has covered before in this series."}'
    used = ["Why do humans yawn when others yawn?"]
    with patch.object(llm_service, "_get_model", return_value=_mock_model(json_resp)):
        result = llm_service.generate_fact_topic("psychology & dark psychology", recently_used_titles=used)
    assert result["title"] == "Fresh topic"


def test_get_cta_narration_facts_en(monkeypatch):
    from app.services.llm_service import get_cta_narration, _CTA_FACTS_EN
    cta = get_cta_narration(channel_id="stories", language="en")
    assert cta in _CTA_FACTS_EN
```

- [ ] **Step 1.2: Run the test to verify it fails**

```bash
cd /Users/chetan/Desktop/DSE_Projects/youtube-video-generation-tool
pytest tests/test_llm_fact_topic.py -v
```

Expected: FAIL — `generate_fact_topic` not defined, `_CTA_FACTS_EN` not defined.

- [ ] **Step 1.3: Add fact visual style pools and categories to `llm_service.py`**

After the `_VISUAL_STYLE_POOL` list (around line 70), add:

```python
_FACT_VISUAL_STYLE_POOL_CINEMATIC = [
    "Cinematic 4K, dramatic side lighting, deep shadows, photorealistic",
    "Wide-angle cinematic shot, warm golden-hour lighting, photorealistic",
    "Overhead aerial perspective, cool blue tones, ultra-sharp 4K detail, photorealistic",
    "Documentary-style, natural soft lighting, gritty texture, ultra-realistic",
    "Futuristic neon-lit environment, deep blue and purple hues, cinematic 4K, photorealistic",
    "Epic wide establishing shot, overcast moody sky, high dynamic range, photorealistic",
    "Close-up macro cinematic, shallow depth of field, soft bokeh background, photorealistic",
    "Dramatic low-angle shot, vibrant saturated colours, high contrast, cinematic 4K, photorealistic",
]

_FACT_VISUAL_STYLE_POOL_ILLUSTRATED = [
    "Bold infographic illustration style, clean lines, vibrant accent colours, flat design",
    "Bold illustrated style, strong geometric shapes, vivid palette, high contrast",
    "Modern flat design illustration, bold typography-free layout, clean colour blocks",
    "Isometric illustrated scene, bright primary colours, clean flat design",
    "Minimalist editorial illustration, bold ink outlines, limited colour palette",
    "Dynamic graphic novel style, expressive characters, vivid saturated palette",
    "Bright poster-style illustration, flat colour fills, strong silhouettes",
]

_CINEMATIC_CATEGORIES = {
    "science & space",
    "history & civilizations",
    "human body & biology",
    "technology & ai",
    "health & fitness",
    "mysteries & unexplained",
}


def _fact_visual_style(category: str) -> str:
    if category.lower() in _CINEMATIC_CATEGORIES:
        return random.choice(_FACT_VISUAL_STYLE_POOL_CINEMATIC)
    return random.choice(_FACT_VISUAL_STYLE_POOL_ILLUSTRATED)
```

- [ ] **Step 1.4: Add `_CTA_FACTS_EN` pool and update `get_cta_narration()` routing**

After `_CTA_STORIES_HI` (around line 627), add:

```python
_CTA_FACTS_EN = [
    "Follow for daily mind-blowing facts.",
    "Subscribe — your daily dose of 'Did you know?'",
    "More unbelievable facts every day — Subscribe now.",
    "Like if this surprised you, Subscribe for more.",
    "Turn on notifications — tomorrow's fact will shock you.",
    "Subscribe and discover something incredible every day.",
    "Facts that change how you see the world — Subscribe.",
    "One surprising fact a day — hit Subscribe.",
    "Your brain just learned something new — Subscribe for more.",
    "Share this with someone who needs to know — and Subscribe.",
]
```

Then update `get_cta_narration()`:

```python
def get_cta_narration(channel_id: str = "news", language: str = "en") -> str:
    """Return a randomly chosen CTA narration string. No visual — caller reuses last frame."""
    if channel_id == "stories":
        if language == "hi":
            pool = _CTA_STORIES_HI
        else:
            pool = _CTA_FACTS_EN
    else:
        pool = _CTA_NEWS
    return random.choice(pool)
```

- [ ] **Step 1.5: Add `generate_fact_topic()` function**

After `generate_story_idea()` (after line ~730), add:

```python
def generate_fact_topic(category: str, recently_used_titles: list[str] | None = None) -> dict:
    """Generate a specific, punchy fact topic for the given category.

    Returns {"title": str, "premise": str}.
    title — a hook question or punchy claim (e.g. "Why do humans feel heartbreak as physical pain?")
    premise — 1-2 sentence context the script generator can expand on.
    """
    avoid_block = ""
    if recently_used_titles:
        lines = "\n".join(f"  - {t}" for t in recently_used_titles[:20])
        avoid_block = f"\nDo NOT reuse these recently covered topics:\n{lines}\n"

    prompt = f"""You are a researcher for a YouTube Shorts channel called "Tell Me Why" that posts surprising, factual, and educational content.

Generate a specific, punchy fact topic for the category: {category.title()}{avoid_block}

Rules:
- title: 6-12 words, must be a hook question OR a shocking fact statement. Examples:
  - "Why do humans feel heartbreak as physical pain?"
  - "Your body replaces itself completely every 7 years — sort of"
  - "Ancient Romans used crushed mouse brains as toothpaste"
  - "The human eye can detect a single photon of light"
- premise: 1-2 sentences of factual context that the script writer can expand. Must include the core mechanism, number, or surprising detail. Minimum 15 words.
- Topic must be genuinely surprising or counterintuitive — avoid obvious or well-worn facts.
- Topic must be verifiable via Google Search.

Return only a valid JSON object, no markdown:
{{"title": "...", "premise": "..."}}"""

    for attempt in range(2):
        try:
            response = _get_model().generate_content(prompt)
            result = _extract_json_object(_response_text(response))
            if result.get("title") and result.get("premise"):
                if len((result["premise"] or "").strip().split()) >= 15:
                    return result
                logger.warning("Fact topic premise quality gate failed (attempt %d): %s", attempt + 1, result.get("premise"))
        except Exception:
            pass
    return {
        "title": f"The most surprising fact about {category}",
        "premise": f"Scientists and researchers have uncovered a fact about {category} that challenges common assumptions and reveals something deeply counterintuitive about how the world works.",
    }
```

- [ ] **Step 1.6: Update `generate_script_with_search()` to accept `visual_style_override` and `script_mode`**

Change the function signature from:
```python
def generate_script_with_search(topic: str, language: str = "en", aspect_ratio: str = "16:9", context: str = "") -> str:
```
to:
```python
def generate_script_with_search(topic: str, language: str = "en", aspect_ratio: str = "16:9", context: str = "", visual_style_override: str = "", script_mode: str = "news") -> str:
```

Then inside the function, replace:
```python
    video_style = random.choice(_VISUAL_STYLE_POOL)
```
with:
```python
    video_style = visual_style_override if visual_style_override else random.choice(_VISUAL_STYLE_POOL)
```

And replace the prompt string starting with `"You are an expert scriptwriter for educational YouTube videos. Use your Google Search tool..."` with a conditional:

```python
    if script_mode == "facts":
        system_instruction = (
            "You are a scriptwriter for 'Tell Me Why', a YouTube Shorts channel about surprising, "
            "mind-blowing facts. Use Google Search to verify the fact and find supporting details. "
            "Structure every script as: Scene 1 — Hook (the most surprising/counterintuitive angle, "
            "lead with the shocking number or claim); Scene 2 — Elaboration (the science, history, "
            "or mechanism behind it — the 'why'); Scene 3 — Payoff (a related mind-blowing extension "
            "or real-world implication the viewer can share). "
            "Narration: conversational English, 20-24 words per scene, no jargon. "
            "Visual prompts: always English, safe for Imagen."
        )
    else:
        system_instruction = (
            "You are an expert scriptwriter for educational YouTube videos. Use your Google Search "
            "tool to look up the latest information about this headline, then write a factually "
            "accurate video script. The script must faithfully represent ALL angles in the headline "
            "and news context. Do NOT substitute outdated training-data knowledge when current "
            "search results are available."
        )
```

Then replace the start of `prompt = f"""` with:
```python
    prompt = f"""
{system_instruction}
```

(All remaining prompt content — rules, format, etc. — stays identical. Only the opening paragraph changes.)

- [ ] **Step 1.7: Run tests to verify they pass**

```bash
pytest tests/test_llm_fact_topic.py -v
```

Expected: 3 PASSED.

- [ ] **Step 1.8: Commit**

```bash
git add app/services/llm_service.py tests/test_llm_fact_topic.py
git commit -m "feat: add generate_fact_topic, fact visual pools, CTA_FACTS_EN, script_mode param to generate_script_with_search"
```

---

## Task 2: story_researcher.py — Full rewrite for facts

**Files:**
- Modify: `app/agents/story_researcher.py`

- [ ] **Step 2.1: Verify existing test passes before changes**

```bash
pytest tests/test_story_researcher_dispatch.py -v
```

Expected: 1 PASSED (will break after Task 2 changes, fixed in Task 5).

- [ ] **Step 2.2: Rewrite `story_researcher.py`**

Replace the entire content of `app/agents/story_researcher.py` with:

```python
# app/agents/story_researcher.py
#
# Tell Me Why facts channel — posts 4 English-language facts videos per day.
# GitHub Actions cron (2am, 8am, 2pm, 8pm IST) → scripts/run_stories.py → this module
# → dispatch generate-video.yml (script_type="facts", language="en")

import re
import random
import hashlib
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services import firestore_service
from app.services.llm_service import generate_fact_topic
from app.services.telegram_service import send_message
from app.config import STORIES_CHAT_ID

logger = logging.getLogger(__name__)

_FACT_DEDUP_DAYS = 30

_FACT_CATEGORIES = [
    "science & space",
    "history & civilizations",
    "human body & biology",
    "technology & ai",
    "health & fitness",
    "psychology & dark psychology",
    "relationships & dating",
    "self-improvement & habits",
    "business & finance",
    "culture & society",
    "philosophy & life",
    "mysteries & unexplained",
]

# Slot hours matching stories-run.yml cron: 2am, 8am, 2pm, 8pm IST
_SLOT_HOURS = [2, 8, 14, 20]


def _is_topic_already_used(title: str) -> bool:
    return firestore_service.is_headline_already_suggested(
        title, ttl_days=_FACT_DEDUP_DAYS, channel_id="stories"
    )


def _mark_topic_used(title: str, category: str = ""):
    firestore_service.mark_headline_suggested(title, genre=category, channel_id="stories")


def _recently_used_titles(limit: int = 20) -> list[str]:
    try:
        return firestore_service.get_recently_suggested_headlines(
            days=_FACT_DEDUP_DAYS, limit=limit, channel_id="stories"
        )
    except Exception:
        return []


def _select_category() -> str:
    """Select fact category using performance-weighted randomization with deterministic fallback."""
    from app.services.firestore_service import get_genre_performance_fortnightly

    try:
        perf = get_genre_performance_fortnightly(channel_id="stories")
    except Exception:
        perf = {}

    if perf:
        scores = [perf.get(g, 0.0) for g in _FACT_CATEGORIES]
        known = sorted(s for s in scores if s > 0)
        baseline = known[len(known) // 2] if known else 100.0
        weights = [s if s > 0 else baseline for s in scores]
        return random.choices(_FACT_CATEGORIES, weights=weights, k=1)[0]

    # Deterministic IST schedule-slot rotation
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    slot_index = None
    for idx, hour in enumerate(_SLOT_HOURS):
        if now_ist.hour > hour or (now_ist.hour == hour and now_ist.minute >= 0):
            slot_index = idx
    if slot_index is None:
        slot_index = len(_SLOT_HOURS) - 1
        day_ordinal = now_ist.date().toordinal() - 1
    else:
        day_ordinal = now_ist.date().toordinal()
    schedule_slot = (day_ordinal * len(_SLOT_HOURS)) + slot_index
    return _FACT_CATEGORIES[schedule_slot % len(_FACT_CATEGORIES)]


def run() -> str | None:
    """
    Main entry point called by scripts/run_stories.py or GitHub Actions scheduled workflow.
    1. Check pipeline state — skip if already processing.
    2. Generate a fresh English fact topic via LLM.
    3. Deduplicate against recent 30-day window.
    4. Dispatch GitHub Actions workflow to generate the full video.
    5. Notify the Tell Me Why Telegram channel.
    Returns the public_id string if enqueued, None otherwise.
    """

    state = firestore_service.get_pipeline_state(channel_id="stories")
    if state.get("state") == "processing":
        logger.info("Tell Me Why pipeline busy — skipping this run")
        send_message(
            STORIES_CHAT_ID,
            f"⏭️ Tell Me Why scheduler slot skipped — pipeline is busy processing batch "
            f"`{state.get('active_batch_id', '?')}`.",
            channel_id="stories",
        )
        return None

    language = "en"
    recently_used = _recently_used_titles()
    target_category = _select_category()

    try:
        idea = generate_fact_topic(
            category=target_category,
            recently_used_titles=recently_used,
        )
    except Exception as e:
        logger.exception(f"Fact topic generation failed: {e}")
        if STORIES_CHAT_ID:
            send_message(STORIES_CHAT_ID, f"⚠️ Fact topic generation failed: {e}", channel_id="stories")
        return None

    title = (idea.get("title") or "").strip()
    premise = (idea.get("premise") or "").strip()

    if not title:
        logger.warning("Fact topic returned empty title — skipping")
        send_message(
            STORIES_CHAT_ID,
            f"⚠️ Fact slot skipped — LLM returned an empty title for category *{target_category}*. Will retry next slot.",
            channel_id="stories",
        )
        return None

    if _is_topic_already_used(title):
        logger.info(f"Fact topic already used recently: {title}")
        send_message(
            STORIES_CHAT_ID,
            f"⏭️ Fact slot skipped — recently used title detected: _{title}_. A new topic will be generated next slot.",
            channel_id="stories",
        )
        return None

    batch_id = f"stories_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    code = "FACT01"
    raw_task = f"generate-{batch_id}-{code}"
    task_name = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_task)
    public_id = hashlib.sha1(task_name.encode("utf-8")).hexdigest()[:8].upper()
    job_id = task_name

    firestore_service.save_news_batch(batch_id, target_category, {
        code: {
            "code": code,
            "headline": title,
            "context": premise,
            "rating": 5.0,
            "genre": target_category,
        }
    })
    firestore_service.set_pipeline_and_batch_state(batch_id, "processing", channel_id="stories")

    firestore_service.create_or_update_job(job_id, {
        "job_id": job_id,
        "batch_id": batch_id,
        "code": code,
        "topic": title,
        "source": "scheduler",
        "status": "queued",
        "public_id": public_id,
        "genre": target_category,
        "details": premise,
        "channel_id": "stories",
        "language": language,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    from app.agents.github_dispatch import dispatch_video_generation
    try:
        dispatch_video_generation({
            "headline": title,
            "code": code,
            "batch_id": batch_id,
            "job_id": job_id,
            "public_id": public_id,
            "force_run": True,
            "genre": target_category,
            "details": premise,
            "virality_score": 0.0,
            "channel_id": "stories",
            "script_type": "facts",
            "language": language,
        })
    except Exception as e:
        logger.exception(f"Failed to dispatch fact generation workflow: {e}")
        firestore_service.set_pipeline_and_batch_state(batch_id, "failed", channel_id="stories")
        if STORIES_CHAT_ID:
            send_message(STORIES_CHAT_ID, f"❌ Failed to queue fact video: {e}", channel_id="stories")
        return None

    _mark_topic_used(title, category=target_category)

    if STORIES_CHAT_ID:
        send_message(
            STORIES_CHAT_ID,
            f"💡 Generating facts video...\n"
            f"Topic: *{title}*\n"
            f"Category: {target_category.title()}\n"
            f"Id: `{public_id}`",
            channel_id="stories",
        )

    logger.info(f"Facts task enqueued: {task_name} | {title} | category={target_category}")
    return public_id
```

- [ ] **Step 2.3: Run the full test suite to check for import errors**

```bash
pytest tests/ -v --tb=short 2>&1 | head -60
```

Expected: Most tests pass. `test_story_researcher_dispatch.py` will FAIL with assertion errors on `script_type` and `language` — that is expected and fixed in Task 5.

- [ ] **Step 2.4: Commit**

```bash
git add app/agents/story_researcher.py
git commit -m "feat: rewrite story_researcher.py as Tell Me Why facts pipeline"
```

---

## Task 3: generator_agent.py — Add `facts` script_type branch

**Files:**
- Modify: `app/agents/generator_agent.py`

- [ ] **Step 3.1: Write a failing test for the facts branch**

Create `tests/test_generator_agent_facts.py`:

```python
import pytest
from unittest.mock import MagicMock, patch


def test_generator_agent_facts_branch_uses_search(monkeypatch):
    """When script_type='facts', generator must call generate_script_with_search with script_mode='facts'."""
    import app.agents.generator_agent as ga
    import app.services.llm_service as llm

    calls = {}

    def mock_generate_script_with_search(topic, language="en", aspect_ratio="9:16", context="", visual_style_override="", script_mode="news"):
        calls["script_mode"] = script_mode
        calls["language"] = language
        calls["visual_style_override"] = visual_style_override
        return '[{"scene": 1, "narration": "Fact narration here test.", "visual": "cinematic style — mountains"}]'

    monkeypatch.setattr(llm, "generate_script_with_search", mock_generate_script_with_search)
    monkeypatch.setattr(ga, "generate_script_with_search", mock_generate_script_with_search)

    # Patch all side-effectful services
    with patch("app.services.firestore_service.get_job", return_value={}), \
         patch("app.services.firestore_service.create_or_update_job"), \
         patch("app.services.firestore_service.acquire_video_lock", return_value=True), \
         patch("app.services.firestore_service.release_video_lock"), \
         patch("app.services.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}), \
         patch("app.services.firestore_service.set_pipeline_and_batch_state"), \
         patch("app.services.firestore_service.mark_scene_checkpoint"), \
         patch("app.services.firestore_service.record_quota_event"), \
         patch("app.services.tts_service.generate_audio"), \
         patch("app.services.tts_service.choose_voice_for_video", return_value="en-US-Neural2-C"), \
         patch("app.services.image_service.generate_image", return_value=("/tmp/img.png", 0)), \
         patch("app.services.video_service.create_video"), \
         patch("app.services.telegram_service.send_message"), \
         patch("app.agents.senior_script_reviewer.review_package", return_value={"scenes": [{"scene": 1, "narration": "test", "visual": "test visual"}], "title": "Test Fact", "caption": "cap"}), \
         patch("app.services.llm_service.apply_quality_controls", side_effect=lambda t, s, **kw: s), \
         patch("app.services.llm_service.classify_music_genre", return_value="Cheerful"), \
         patch("app.services.llm_service.get_cta_narration", return_value="Subscribe now."), \
         patch("app.services.storage_service.upload_video", return_value="gs://bucket/vid.mp4"), \
         patch("app.agents.social_media_agent.post", return_value="https://youtu.be/abc"):
        ga.run(
            headline="Why do cats always land on their feet?",
            code="FACT01",
            batch_id="b1",
            job_id="job-facts-001",
            public_id="ABCD1234",
            force_run=True,
            genre="science & space",
            details="Cats use a righting reflex to twist mid-air.",
            channel_id="stories",
            script_type="facts",
            language="en",
        )

    assert calls.get("script_mode") == "facts", f"Expected script_mode='facts', got {calls.get('script_mode')}"
    assert calls.get("language") == "en"
    assert calls.get("visual_style_override") != "", "Expected a non-empty visual_style_override for facts"
```

- [ ] **Step 3.2: Run to verify it fails**

```bash
pytest tests/test_generator_agent_facts.py -v
```

Expected: FAIL — facts branch not yet implemented.

- [ ] **Step 3.3: Add the `facts` branch to `generator_agent.py`**

In `generator_agent.py`, import `_fact_visual_style` at the top alongside existing llm_service imports:

```python
from app.services.llm_service import (
    generate_script,
    generate_script_with_search,
    SearchGroundingUnavailable,
    generate_story_script,
    classify_music_genre,
    apply_quality_controls,
    get_cta_narration,
    _fact_visual_style,
)
```

Then find the `if script_type == "story":` block (around line 288) and add the `facts` branch so the full block reads:

```python
        if script_type == "story":
            # Stories: pure LLM generation, language from payload (default "hi" for backward compat)
            language = language or "hi"
            mood = genre or "inspiring"
            raw_script = generate_story_script(headline, mood=mood, premise=details or "", language=language)
        elif script_type == "facts":
            # Facts: search-grounded English script with category-appropriate visual style
            language = "en"
            fact_visual_style = _fact_visual_style(genre or "")
            try:
                raw_script = generate_script_with_search(
                    headline,
                    language="en",
                    aspect_ratio="9:16",
                    context=details or "",
                    visual_style_override=fact_visual_style,
                    script_mode="facts",
                )
            except SearchGroundingUnavailable:
                logger.info("Search grounding unavailable for %s, using standard generation", public_id or effective_job_id)
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
            except Exception as _search_exc:
                logger.warning("Search-grounded facts script generation failed (%s), falling back to standard", _search_exc)
                send_message(
                    _chat_id,
                    f"⚠️ Search-grounded script failed for `{public_id or effective_job_id}` — "
                    f"falling back to standard generation.\nReason: {str(_search_exc)[:200]}",
                    channel_id=channel_id,
                )
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
        else:
            # News: search-grounded script generation in English
            language = "en"
            try:
                raw_script = generate_script_with_search(headline, language="en", aspect_ratio="9:16", context=details or "")
            except SearchGroundingUnavailable:
                logger.info("Search grounding unavailable for %s, using standard generation", public_id or effective_job_id)
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
            except Exception as _search_exc:
                logger.warning("Search-grounded script generation failed (%s), falling back to standard", _search_exc)
                send_message(
                    _chat_id,
                    f"⚠️ Search-grounded script failed for `{public_id or effective_job_id}` — "
                    f"falling back to standard generation (content may be less accurate).\n"
                    f"Reason: {str(_search_exc)[:200]}",
                    channel_id=channel_id,
                )
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
```

Also update the `skip_fact_check` parameter on the `apply_quality_controls` call (currently `skip_fact_check=(script_type == "story")`). Facts should get the full fact-check pass:

```python
        scenes = apply_quality_controls(headline, scenes, language=language, context=details or "", skip_fact_check=(script_type == "story"))
```

This line already works correctly for facts — `(script_type == "story")` evaluates to `False` for `"facts"`, so fact-check is enabled. No change needed.

Also update the `_voice_lang` line (around line 199) so `facts` uses English voice:

Find:
```python
    _voice_lang = (language or "hi") if script_type == "story" else "en"
```

Replace with:
```python
    _voice_lang = (language or "hi") if script_type == "story" else "en"
```

This already correctly resolves to `"en"` for `script_type="facts"`. No change needed.

- [ ] **Step 3.4: Run the facts generator test**

```bash
pytest tests/test_generator_agent_facts.py -v
```

Expected: PASSED.

- [ ] **Step 3.5: Run full test suite to check no regressions**

```bash
pytest tests/ -v --tb=short 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected: Only `test_story_researcher_dispatch.py` fails (assertions on `script_type` and `language` — fixed next task). All others PASSED.

- [ ] **Step 3.6: Commit**

```bash
git add app/agents/generator_agent.py tests/test_generator_agent_facts.py
git commit -m "feat: add script_type='facts' branch to generator_agent — search-grounded English facts with category visual style"
```

---

## Task 4: stories_agent.py — Update display strings

**Files:**
- Modify: `app/agents/stories_agent.py`

- [ ] **Step 4.1: Update `send_post_result()` in `stories_agent.py`**

Find:
```python
    message = (
        "✅ Your story is live on Short Tales\n"
        f"Live Link: {url}\n"
        f"Date: {date_line}\n"
        f"Time: {time_line}"
        f"{id_line}"
        f"{mood_line}"
    )
```

Replace with:
```python
    message = (
        "✅ Your fact video is live on Tell Me Why\n"
        f"Live Link: {url}\n"
        f"Date: {date_line}\n"
        f"Time: {time_line}"
        f"{id_line}"
        f"{mood_line}"
    )
```

Also rename the parameter `mood` → `category` in the function signature and docstring to reflect the new domain:

```python
def send_post_result(title: str, url: str, public_id: str = "", live_date: str = "", live_time: str = "", mood: str = ""):
    """Notify the Tell Me Why Telegram channel when a fact video goes live."""
    id_line = f"\nId: `{public_id}`" if public_id else ""
    mood_line = f"\nCategory: {mood.title()}" if mood else ""
```

(Keep the parameter named `mood` for call-site compatibility — `social_media_agent` passes `genre` as `mood=`.)

- [ ] **Step 4.2: Run full test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected: Same as before — only `test_story_researcher_dispatch.py` still failing.

- [ ] **Step 4.3: Commit**

```bash
git add app/agents/stories_agent.py
git commit -m "feat: update stories_agent display strings for Tell Me Why channel"
```

---

## Task 5: Update test + workflow comment

**Files:**
- Modify: `tests/test_story_researcher_dispatch.py`
- Modify: `.github/workflows/stories-run.yml`

- [ ] **Step 5.1: Update `test_story_researcher_dispatch.py`**

Replace the entire file content with:

```python
import pytest
from unittest.mock import MagicMock, patch


def test_story_researcher_run_dispatches_github_workflow(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    import app.agents.story_researcher as sr

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp) as mock_post:
        with patch.object(sr.firestore_service, "get_pipeline_state", return_value={"state": "completed"}):
            with patch.object(sr, "_recently_used_titles", return_value=[]):
                with patch.object(sr, "_select_category", return_value="science & space"):
                    with patch.object(sr, "generate_fact_topic", return_value={"title": "Why do black holes evaporate?", "premise": "Stephen Hawking showed that quantum effects cause black holes to slowly emit radiation and shrink over trillions of years."}):
                        with patch.object(sr, "_is_topic_already_used", return_value=False):
                            with patch.object(sr.firestore_service, "save_news_batch"):
                                with patch.object(sr.firestore_service, "set_pipeline_and_batch_state"):
                                    with patch.object(sr.firestore_service, "create_or_update_job"):
                                        with patch.object(sr, "_mark_topic_used"):
                                            with patch.object(sr, "send_message"):
                                                result = sr.run()

    assert result is not None
    mock_post.assert_called_once()
    call_body = mock_post.call_args[1]["json"]
    payload = __import__("json").loads(call_body["inputs"]["payload"])
    assert payload["channel_id"] == "stories"
    assert payload["script_type"] == "facts"
    assert payload["language"] == "en"
    assert payload["headline"] == "Why do black holes evaporate?"
```

- [ ] **Step 5.2: Run the updated test**

```bash
pytest tests/test_story_researcher_dispatch.py -v
```

Expected: PASSED.

- [ ] **Step 5.3: Update workflow comment in `stories-run.yml`**

Find the comment line in `.github/workflows/stories-run.yml`:

```yaml
# Runs the Hindi story research and dispatch pipeline
```

(or similar wording) and replace with:

```yaml
# Runs the Tell Me Why facts research and dispatch pipeline
```

If no such comment exists, add one at the top of the `jobs:` block or leave unchanged.

- [ ] **Step 5.4: Run full test suite — all must pass**

```bash
pytest tests/ -v --tb=short
```

Expected: ALL PASSED. Zero failures.

- [ ] **Step 5.5: Commit**

```bash
git add tests/test_story_researcher_dispatch.py .github/workflows/stories-run.yml
git commit -m "test: update story_researcher dispatch test for facts pipeline (script_type=facts, language=en)"
```

---

## Task 6: Final verification and push

- [ ] **Step 6.1: Run full test suite one final time**

```bash
pytest tests/ -v
```

Expected: ALL PASSED.

- [ ] **Step 6.2: Verify News channel is untouched**

```bash
pytest tests/test_pipeline.py tests/test_news_domain_scheduler.py -v 2>/dev/null || pytest tests/ -k "news or pipeline or research" -v
```

Expected: All news-related tests PASSED. The News channel code path (`script_type="news"`) is unchanged.

- [ ] **Step 6.3: Push to trigger CI**

```bash
git push origin main
```

Check CI passes at `https://github.com/rahulbohra57/youtube-video-generation-tool/actions`.

- [ ] **Step 6.4: Manually trigger a Tell Me Why pipeline run**

```bash
gh workflow run stories-run.yml
```

Watch the Telegram bot for a "💡 Generating facts video..." notification confirming the new pipeline fired correctly.
