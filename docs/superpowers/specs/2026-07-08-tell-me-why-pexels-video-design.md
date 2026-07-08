# Tell Me Why Shorts: Imagen → Pexels Stock Video

## Background

Vertex AI Imagen (`imagen-3.0-generate-002`/`-001`) is inaccessible to the
production GCP project — this was already confirmed and fixed for the News
channel (`docs/superpowers/specs/2026-07-07-news-channel-pexels-video-design.md`).
The same 403 "Publisher Model ... is not visible to the current project" error
is now failing every Tell Me Why (`channel_id="stories"`, `script_type="facts"`)
Shorts video, dropping all 4 scenes on every run.

Tell Me Why Shorts will switch to Pexels stock video, same as News. Unlike
News (already photorealistic), Tell Me Why's current visual style is
illustrated/storybook (cartoon depictions of facts) — this is a real style
change, not just an outage workaround. Confirmed with the user: switch to
real stock footage, and fully replace Imagen (no first-try-Imagen-then-fallback)
since the 403 is a project-entitlement issue, not transient.

## Scope

- **In scope**: `channel_id="stories"` + `script_type="facts"` Shorts scene
  generation only (the actual production Tell Me Why pipeline per
  `stories-run.yml` → `story_researcher.run()`).
- **Out of scope**:
  - Legacy Hindi `script_type="story"` pipeline (`app/routes/stories.py`
    `/generate/stories-task`, dispatched via retired Cloud Tasks) — dead code,
    untouched, still uses `generate_story_script()` + Imagen if ever invoked.
  - The separate long-format Tell Me Why pipeline
    (`long_generator_agent.py`) — already uses Pexels for scenes; unaffected.
  - Thumbnails (`generate_thumbnail()`) — already has an in-progress
    Imagen-unavailable → Pexels-photo fallback (uncommitted WIP in
    `image_service.py`/`pexels_service.py`/`tests/test_thumbnail.py`),
    handled as part of the same session but tracked separately below.
  - `app/routes/generate.py` (legacy generic `/generate` FastAPI route, not
    wired into Vercel routing, not part of any documented pipeline) — must
    keep working exactly as today.

## What Already Exists (built during the News migration, needs no changes)

- `pexels_service.py`: `_CATEGORY_FALLBACKS` already has all 12 Tell Me Why
  fact categories (science & space, history & civilizations, etc.) — added
  when this file was shared with the long-format pipeline.
- `video_service.py`: `create_video()` already branches per-scene on file
  extension (`_is_video_asset()`), not on `channel_id` — a `.mp4` scene asset
  is loaded via `VideoFileClip`, trimmed/looped to audio duration, and
  cropped to 1080x1920 regardless of channel. No changes needed.
- `apply_quality_controls()`, `fact_check_scenes()`, senior script reviewer's
  `_tighten_if_too_long()`: already key off whichever of `visual`/
  `visual_query` is present on the scene dict, not on channel or script mode.

## Component Changes

### `app/services/llm_service.py`

- `generate_script_with_search()`, `script_mode == "facts"` branch: replace
  the Imagen-style "literal/absurd illustration" `VISUAL PROMPT RULES` block
  with a `VISUAL_QUERY RULES` block (matching the News/long-form pattern) —
  3-7 concrete English words for a Pexels stock video search, describing the
  fact's real-world subject rather than an illustrated interpretation of it.
  `visual_field` changes from `"visual"` to `"visual_query"`.
- `generate_script()` (non-search fallback, used when search grounding
  fails): add the same `elif script_mode == "facts":` branch. To avoid
  changing behavior for the legacy `/generate` route (which calls this
  function with no `script_mode` arg), **change the function's default
  parameter from `script_mode="facts"` to `script_mode="legacy"`** — the old
  Imagen-prompt code becomes the `else` branch, reached by `"legacy"` and any
  other unrecognized value. `generator_agent.py`'s facts-fallback call sites
  must pass `script_mode="facts"` explicitly to opt into the new behavior.

### `app/agents/generator_agent.py`

- The two facts-fallback `generate_script(...)` calls (search-grounding
  failure paths inside the `script_type == "facts"` branch): add
  `script_mode="facts"` explicitly.
- Scene-fetch branch: broaden `if channel_id == "news":` to
  `if channel_id == "news" or script_type == "facts":` so Tell Me Why scenes
  route to `_fetch_pexels_clip_or_raise(visual, audio_duration, idx,
  category=genre)`. The legacy `script_type == "story"` path also has
  `channel_id == "stories"` but is not `script_type == "facts"`, so it stays
  on the untouched `generate_image()` path.
- `genre` for Tell Me Why is already one of the 12 fact categories, passed
  as `category` — already covered by `_CATEGORY_FALLBACKS`.

### `app/services/pexels_service.py`, `app/services/video_service.py`

- No changes — already generic per above.

## Error Handling

Unchanged from the News design: `fetch_clip` empty-string result is a scene
failure (existing `MIN_CLIPS` / all-scenes-failed abort path in
`generator_agent.py` already handles this identically for any channel).

## Testing Plan

- `tests/test_llm_service.py`:
  - Update `test_generate_script_with_search_facts_mode_still_uses_visual` →
    now asserts `visual_query` / `VISUAL_QUERY RULES` / `Pexels` for
    `script_mode="facts"` (this was true-until-now for the old Imagen
    behavior; flipping it is the point of this change).
  - `test_generate_script_default_mode_still_uses_visual` stays valid
    unchanged — default is now `"legacy"` instead of `"facts"`, but the
    assertion (no `script_mode` arg → Imagen `visual` prompt) still holds.
  - Add `test_generate_script_facts_mode_uses_visual_query` — explicit
    `script_mode="facts"` → `visual_query` / Pexels rules.
- `tests/test_generator_agent_news_pexels.py` (or a new
  `test_generator_agent_stories_pexels.py`): mock `fetch_pexels_clip` to
  return a clip path for a `channel_id="stories"`, `script_type="facts"` job;
  assert `generate_image` is *not* called for that scene and *is* still
  called unchanged for `script_type="story"` (legacy) jobs.
- Manual/production verification (can't be unit tested): trigger one Tell Me
  Why `CREATE <topic>` job and visually confirm the resulting Short — stock
  footage matches the fact reasonably, subtitles readable, scene/audio sync
  correct.

No new secrets or infra — reuses the existing `PEXELS_API_KEY`.
