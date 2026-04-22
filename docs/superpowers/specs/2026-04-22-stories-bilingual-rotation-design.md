# Design: Stories Channel Bilingual Rotation (3 English + 1 Hindi/day)

**Date:** 2026-04-22  
**Status:** Approved

---

## Context

The Stories channel (Short Tales) currently generates 4 Hindi stories per day at 7am, 11am, 2pm, and 6pm IST. All language values are hardcoded to `"hi"` in the pipeline. The goal is to shift to 3 English stories and 1 Hindi story per day, with the Hindi slot rotating across the 4 daily time slots on a 4-day cycle. English stories use the same 12 genres and fable style but with a realistic/photorealistic visual aesthetic instead of the current painted/illustrated style. Both languages continue to upload to the same Short Tales YouTube channel.

---

## Approach: Language as Payload Field

Add a `language` field (`"en"` or `"hi"`) to the Cloud Task payload. `story_researcher.run()` determines the language per run using a deterministic formula, then threads it through the entire pipeline. All downstream components already accept a `language` parameter — the main work is replacing the two hardcoded `"hi"` values in `generator_agent.py` and making `llm_service.py` language-aware for story generation.

---

## Section 1: Language Rotation

**Where:** `app/agents/story_researcher.py`

The 4 daily scheduler slots map to indices by IST hour:
- 7am → slot 0
- 11am → slot 1
- 2pm → slot 2
- 6pm → slot 3

At runtime, `run()` computes:
```python
hindi_slot = datetime.now(IST).timetuple().tm_yday % 4
slot_index = {7: 0, 11: 1, 14: 2, 18: 3}[current_hour]
language = "hi" if slot_index == hindi_slot else "en"
```

This gives a clean 4-day cycle where each slot gets exactly one Hindi run every 4 days. The `language` value is then passed to `generate_story_idea()` and included in the Cloud Task payload.

---

## Section 2: Payload & Generator Agent

**Where:** `app/agents/generator_agent.py`, `app/routes/stories.py`

The Cloud Task payload gains a `language` field alongside the existing `headline`, `genre`, `details` fields. The `/generate/stories-task` route passes it through to `generator_agent.run()`.

In `generator_agent.run()`, two hardcoded `"hi"` references are replaced:

| Line | Current | New |
|------|---------|-----|
| 178 | `_voice_lang = "hi" if script_type == "story" else "en"` | `_voice_lang = payload.get("language", "hi") if script_type == "story" else "en"` |
| 269 | `language = "hi"` | `language = payload.get("language", "hi")` |

The default of `"hi"` preserves backward compatibility for any in-flight tasks or manual FORCE_CREATE calls that don't include a `language` field.

---

## Section 3: LLM Service — Story Idea & Script Generation

**Where:** `app/services/llm_service.py`

### `generate_story_idea(preferred_mood, recently_used_titles, language="hi")`

Add `language` parameter. When `"en"`, prompt and output (title, premise) are in English. When `"hi"`, uses existing Devanagari prompt (unchanged).

### `generate_story_script(headline, mood, premise, language="hi")`

Add `language` parameter. The function branches on language:
- `"hi"` → existing Hindi Devanagari prompt (unchanged)
- `"en"` → new English prompt: same structure (4 scenes, 15–18 words/scene narration, moral story format), English narration, English visual prompts

### Visual Style Pools

The single `_STORY_VISUAL_STYLE_POOL` is split into two:

**`_STORY_VISUAL_STYLE_POOL_HI`** (existing, unchanged):
- Studio Ghibli-inspired animation
- Indian folk art (Madhubani, Warli)
- Watercolor illustration
- Vibrant hand-painted

**`_STORY_VISUAL_STYLE_POOL_EN`** (new, realistic):
- Cinematic photorealistic
- Documentary-style photography
- Dramatic natural lighting portrait
- High-resolution realistic render
- Moody cinematic scene

`generate_story_script()` selects from the appropriate pool based on `language`.

---

## Section 4: Safety-Filter Fallback Visuals

**Where:** `app/agents/generator_agent.py` (lines 386–417)

The existing safety-filter fallback prompts (watercolor illustrations by mood) are Hindi-story-appropriate. A parallel set of realistic fallback prompts is added for English stories, keyed by genre:

```python
_STORY_SAFE_PROMPTS_EN = {
    "inspiring": "cinematic scene of a person standing on a hilltop at sunrise, photorealistic",
    "comedy": "candid documentary photo of friends laughing at a cafe, natural light",
    ...
}
```

The fallback selection branches on `language`.

---

## Files Modified

| File | Change |
|------|--------|
| `app/agents/story_researcher.py` | Add language rotation logic; pass `language` to `generate_story_idea()` and Cloud Task payload |
| `app/services/llm_service.py` | Add `language` param to `generate_story_idea()` and `generate_story_script()`; split visual style pool |
| `app/agents/generator_agent.py` | Replace 2 hardcoded `"hi"` values; add English safety-filter fallback prompts |
| `app/routes/stories.py` | Pass `language` from Cloud Task payload to `generator_agent.run()` |

---

## What Does NOT Change

- TTS voice pool — already selected by `language` param (`"en"` → English Neural2 voices, `"hi"` → Hindi Neural2 voices)
- Video service font selection — already branches on `language` for DejaVuSans vs Lohit-Devanagari
- Script reviewer title/caption language — already uses `_TITLE_CAPTION_LANG_INSTRUCTIONS[language]`
- YouTube channel, Telegram bot, Cloud Scheduler jobs — all unchanged
- Genre rotation — same 12 genres, same performance-weighted selection, for both languages
- `MAX_SCENES = 4` for stories — unchanged

---

## Verification

1. **Manual test (English story):** Send `FORCE_CREATE <topic>` to Stories bot — but language assignment is automatic. Instead, temporarily set `language = "en"` in `story_researcher.run()` and trigger `/stories/run` manually via curl with scheduler secret. Verify: English narration, English title/caption, realistic visual prompts in Firestore job doc, English TTS voice in Cloud Run logs.
2. **Manual test (Hindi story):** Same but `language = "hi"`. Verify: Devanagari narration, Hindi title/caption, painted visual style prompt.
3. **Rotation formula:** Log `hindi_slot` and `slot_index` on each scheduler run. Verify the Hindi slot advances by 1 each day across all 4 time slots.
4. **Backward compat:** Trigger a Cloud Task payload without `language` field — verify it defaults to `"hi"` without errors.
