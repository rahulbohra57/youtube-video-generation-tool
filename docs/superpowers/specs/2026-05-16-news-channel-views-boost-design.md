# News Channel Views Boost — Design Spec
**Date:** 2026-05-16
**Channel:** Kurrent Affairs (News)
**Goal:** Increase YouTube views through better topic selection and higher-CTR output — using only free APIs and existing LLM calls.

---

## Overview

Two independent improvements are bundled here:

- **A — pytrends trend scoring**: Replace the keyword-based `_trend_bonus()` with real Google Trends interest data, making topic selection respond to actual search demand.
- **C — Prompt-level improvements**: Sharpen the existing LLM calls for title CTR, scene-1 hook strength, and article virality scoring — no new API calls, no added cost.

---

## A — pytrends Trend Scoring

### Problem
`_trend_bonus()` in `app/agents/lead_researcher.py` is a keyword scan. Headlines containing words like "breaking" or "Google" add 0.1–0.5 to the composite score. This is a proxy, not a real signal — a stale "breaking" story scores the same as a genuinely trending one.

### Solution

**New file: `app/services/trends_service.py`**

Wraps `pytrends.request.TrendReq` and exposes one public function:

```python
def get_trend_scores(topics: list[str]) -> dict[str, float]:
    ...
```

- Queries Google Trends for each topic individually (7-day interest, `gprop=""` for web search).
- Normalises all scores to 0.0–1.0 against the batch max.
- Topics with no data get a neutral default of `0.2`.
- Sleeps 1 second between queries to avoid rate-limiting.
- Entire function wrapped in `try/except` — any pytrends outage returns `{topic: 0.2}` for all, so the research pipeline never breaks.

**Changes to `app/agents/lead_researcher.py`**

- After dedup filtering, batch-call `get_trend_scores()` with all surviving article headlines.
- Remove `_trend_bonus()` call from composite formula.
- New composite formula:
  ```
  score = (llm_rating × 0.55) + (recency_score × 1.8) + (trend_score × 0.8)
  ```
- `trend_score` is the value returned by `get_trend_scores()` for that article's headline.

**New dependency**

Add `pytrends` to `requirements.txt`.

### Error handling
- pytrends outage → all trend scores default to `0.2` → pipeline continues using LLM rating + recency only (same quality as today).
- Rate-limit response from pytrends → caught by `try/except`, same fallback.

---

## C — Prompt-level Improvements

All changes are within existing LLM calls. No new Gemini invocations.

### C1 — Title CTR rubric (`review_title_and_caption_with_senior_reviewer`)

**File:** `app/services/llm_service.py` — `review_title_and_caption_with_senior_reviewer()` (~line 1044)

**Current instruction:**
> "A catchy but non-clickbait YouTube Shorts title"

**Replace with an explicit pattern rubric:**

The LLM must produce a title using one of these four proven YouTube CTR patterns:
1. **Number/stat** — *"X Countries Just Banned This AI Tool"*
2. **Curiosity gap** — *"The Real Reason NASA Delayed This Launch"*
3. **Specificity** — *"OpenAI's $6.6B Deal — What It Actually Means"*
4. **Stakes** — *"This Ruling Could Change How You Use the Internet"*

Constraints:
- Max 70 characters.
- Must use only facts present in the script (no fabrication).
- No generic openers like "Breaking:", "This is", "Here's why".

### C2 — Scene 1 hook enforcement (`generate_script` / `generate_script_with_search`)

**File:** `app/services/llm_service.py` — `format_hint` block for `aspect_ratio == "9:16"` (~line 81)

**Current instruction:**
> "Scene 1: open with the single most compelling fact or question — hook the viewer immediately"

**Replace with:**
> "Scene 1: first sentence must be under 12 words — a specific number, a named person doing something surprising, or a direct question. No scene-setting, no context-building. The viewer decides to stay or swipe in the first 2 seconds."

### C3 — Virality rubric in `rate_and_select_news()`

**File:** `app/services/llm_service.py` — `rate_and_select_news()` prompt

Add an explicit YouTube virality scoring section to the existing prompt:

```
When rating articles, score HIGHER for:
- Headlines with a specific number, name, or organisation (concrete > vague)
- A clear "why this affects you" angle (practical relevance)
- Novelty: something genuinely new, not a recap of ongoing events
Score LOWER for:
- Vague headlines with no named entity ("Scientists discover new thing")
- Pure recap or follow-up stories with no new development
- Topics already covered in the last 7 days (check suggested_headlines)
```

---

## Files Changed

| File | Change |
|---|---|
| `requirements.txt` | Add `pytrends` |
| `app/services/trends_service.py` | New file — trend score fetcher |
| `app/agents/lead_researcher.py` | Replace `_trend_bonus()` with `get_trend_scores()`, update formula |
| `app/services/llm_service.py` | Update title prompt (C1), scene-1 hint (C2), rate_and_select_news prompt (C3) |

---

## What Is Not Changed

- GNews fetch logic, quota circuit-breaker, domain scheduling — untouched.
- Stories channel — untouched.
- Video generation, image, TTS, upload — untouched.
- No new GitHub Actions workflows or Vercel functions.

---

## Testing

- Unit test `get_trend_scores()` with a mocked `TrendReq` — verify normalisation, neutral default, and fallback on exception.
- Update `tests/test_pipeline.py` to mock `trends_service.get_trend_scores` (same pattern as other external SDK mocks in `conftest.py`).
- Manually inspect first 3 research runs after deploy: confirm trend scores appear in Telegram notification and that high-scoring articles are being selected.
