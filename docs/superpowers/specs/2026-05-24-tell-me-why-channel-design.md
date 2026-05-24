# Tell Me Why Channel — Design Spec

**Date:** 2026-05-24  
**Channel:** @TellMeWhy-in  
**Replaces:** Short Tales (Hindi stories channel)  
**Approach:** Option B — new `script_type="facts"` branch, `channel_id="stories"` retained internally

---

## Overview

Repurpose the Stories channel pipeline to post 4 English-language "interesting/unbelievable facts" YouTube Shorts per day. Content covers a wide topic universe (science, psychology, history, relationships, AI, culture, etc.). Scripts are grounded via Google Search to avoid hallucination. Visuals are topic-dependent: cinematic for hard facts, illustrated/infographic for soft topics.

All Telegram bot, Vercel webhook, Firestore, and GitHub Actions infra remains unchanged — only content logic is replaced.

---

## Fact Categories (12 rotating buckets)

The scheduler rotates through these deterministically by slot, with performance-weighted randomization once analytics data exists:

| # | Category | Script style | Visual style |
|---|---|---|---|
| 1 | Science & Space | Search-grounded | Cinematic/photorealistic |
| 2 | History & Civilizations | Search-grounded | Cinematic/photorealistic |
| 3 | Human Body & Biology | Search-grounded | Cinematic/photorealistic |
| 4 | Technology & AI | Search-grounded | Cinematic/photorealistic |
| 5 | Health & Fitness | Search-grounded | Cinematic/photorealistic |
| 6 | Psychology & Dark Psychology | Search-grounded | Bold illustrated/infographic |
| 7 | Relationships & Dating | Search-grounded | Bold illustrated/infographic |
| 8 | Self-Improvement & Habits | Search-grounded | Bold illustrated/infographic |
| 9 | Business & Finance | Search-grounded | Bold illustrated/infographic |
| 10 | Culture & Society | Search-grounded | Bold illustrated/infographic |
| 11 | Philosophy & Life | Search-grounded | Bold illustrated/infographic |
| 12 | Mysteries & Unexplained | Search-grounded | Cinematic/photorealistic |

---

## Architecture

### Pipeline flow (unchanged from stories)

```
GitHub Actions cron (4x/day IST)
  → scripts/run_stories.py
  → story_researcher.run()           ← rewritten for facts
  → dispatch generate-video.yml
  → generator_agent.run(script_type="facts", language="en")
  → generate_script_with_search()    ← reused from news pipeline
  → TTS (en-US-Neural2-*)
  → Imagen (mixed visual style)
  → MoviePy assembly
  → GCS upload → YouTube upload
  → Telegram notification (@TellMeWhy-in bot)
```

### Internal identifiers (unchanged)

| Key | Value | Reason |
|---|---|---|
| `channel_id` | `"stories"` | Keeps all Firestore queries, Vercel routes, and bot token lookups working |
| `script_type` | `"facts"` (new) | Routes to search-grounded fact script generation |
| `language` | `"en"` | English narration and titles |
| Telegram bot | `STORIES_BOT_TOKEN` | Same bot as before |
| Workflow file | `stories-run.yml` | Unchanged |

---

## Files Changed

### 1. `app/agents/story_researcher.py` — full rewrite

- Replace `_STORY_GENRES` list with `_FACT_CATEGORIES` (12 buckets above)
- Replace `generate_story_idea()` call with new `generate_fact_topic(category)` call
- Change `script_type` dispatched payload from `"story"` → `"facts"`
- Change `language` from `"hi"` → `"en"`
- Remove `_story_already_generated_today()` daily cap (facts can post 4x/day freely)
- Update Telegram notification text from "📖 Generating Hindi story..." to "💡 Generating facts video..."
- Keep all Firestore/dedup/dispatch/lock logic identical

### 2. `app/services/llm_service.py` — additions

**New function: `generate_fact_topic(category, recently_used_titles)`**
- Prompts Gemini to produce a specific, punchy fact topic for the given category
- Returns `{"title": str, "premise": str}` — same shape as `generate_story_idea()`
- Title example: *"Why do humans feel heartbreak as physical pain?"*
- Premise: 1–2 sentence context the script generator can expand on
- Passes recently used titles to avoid repeats

**New visual style pools:**
```python
_FACT_VISUAL_STYLE_POOL_CINEMATIC = [
    # photorealistic, documentary, cinematic styles
    # used for: Science, History, Body, Tech, Health, Mysteries
]
_FACT_VISUAL_STYLE_POOL_ILLUSTRATED = [
    # bold illustrated, infographic, flat design styles
    # used for: Psychology, Relationships, Self-improvement, Business, Culture, Philosophy
]
_CINEMATIC_CATEGORIES = {
    "science & space", "history & civilizations", "human body & biology",
    "technology & ai", "health & fitness", "mysteries & unexplained"
}
```

**New CTA pool: `_CTA_FACTS_EN`**
- ~10 CTAs themed around facts/curiosity: *"Follow for daily mind-blowing facts."*, *"Subscribe — your daily dose of 'Did you know?'"*, etc.
- `get_cta_narration(channel_id="stories", language="en")` returns from this pool

### 3. `app/agents/generator_agent.py` — new branch

Add `elif script_type == "facts":` block:
```python
elif script_type == "facts":
    language = "en"
    raw_script = generate_script_with_search(headline, language="en", aspect_ratio="9:16", context=details or "")
    # falls back to generate_script() on search failure (same pattern as news)
```

Visual style selection — pass `genre` to image service so it picks the right pool:
- Generator passes `genre` (the fact category) to `image_service.generate_image()`
- Image service checks if category is in `_CINEMATIC_CATEGORIES` → picks cinematic pool; else illustrated pool

Fact-check pass: **enabled** (unlike stories where it was skipped), since facts must be accurate.

### 4. `app/agents/stories_agent.py`

- Update any hardcoded "Short Tales" display strings → "Tell Me Why"
- Update Telegram notification format to reflect facts content

### 5. `.github/workflows/stories-run.yml`

- Update comment from "Hindi story" to "Tell Me Why facts"
- Schedule unchanged: `2am, 8am, 2pm, 8pm IST`

---

## Visual Style Selection Logic

```python
def _fact_visual_style(category: str) -> str:
    if category.lower() in _CINEMATIC_CATEGORIES:
        return random.choice(_FACT_VISUAL_STYLE_POOL_CINEMATIC)
    return random.choice(_FACT_VISUAL_STYLE_POOL_ILLUSTRATED)
```

This is called in `generate_script_with_search()` when `script_type="facts"`, replacing the single static style used for news.

---

## Script Prompt Design

Facts scripts use `generate_script_with_search()` with a modified system prompt for facts:

- **Hook** (scene 1): Lead with the most surprising/counterintuitive angle of the fact
- **Elaboration** (scene 2): The science/history/mechanism behind it — the "why"
- **Payoff** (scene 3): A related mind-blowing extension or real-world implication
- Narration: conversational English, 20–24 words/scene, no jargon
- Visual prompts: always English, safe for Imagen

The existing `review_package()` and `apply_quality_controls()` run unchanged.

---

## Deduplication

- Same mechanism as stories: `suggested_headlines` Firestore collection, `channel_id="stories"`, 30-day TTL
- `generate_fact_topic()` receives recently used titles and avoids repeating them
- No daily cap (stories had a 1/day cap; facts posts 4x/day freely)

---

## Error Handling

- Same retry/fallback chain as stories: Imagen quota → retry with backoff → safety filter → pre-approved safe prompt fallback
- Search grounding failure → falls back to `generate_script()` (pure LLM), same as news channel
- Invalid JSON from LLM → retried once, then raises (existing behavior)

---

## Tests to Update

- `tests/test_story_researcher_dispatch.py` → update `script_type` assertion from `"story"` → `"facts"`, `language` from `"hi"` → `"en"`
- No new test files needed — existing dispatch test structure covers the changed payload

---

## Out of Scope

- No change to `channel_id` value (stays `"stories"`)
- No change to Vercel webhook routes
- No change to Telegram bot tokens or chat IDs
- No change to YouTube channel credentials
- No new Firestore collections
- No change to `stories-daily-digest.yml` (digest logic is channel-agnostic)
