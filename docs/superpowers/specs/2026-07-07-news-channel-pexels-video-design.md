# News Channel Shorts: Imagen → Pexels Stock Video

## Background

The Vertex AI Imagen models (`imagen-3.0-generate-002` and its fallback
`imagen-3.0-generate-001`) are no longer accessible to the production GCP
project (`youtube-video-generator-492211`) — confirmed via diagnostic
GitHub Actions runs that every Imagen variant (including legacy
`imagegeneration@002/005/006` and Imagen 4) returns 403 "not visible to the
current project," while Gemini text models on the same service account work
fine. This is a project-level Model Garden entitlement issue, not fixable by
changing model names or retry logic.

A parallel investigation into the Gemini Developer API (API-key auth,
separate from Vertex AI) also hit a wall: both tested API keys return
`limit: 0` free-tier quota for Gemini image models and "Imagen 3 is only
available on paid plans" for Imagen — this account is not on a paid Gemini
API plan.

Rather than pursue GCP/Google account billing changes, the News channel
(`channel_id="news"`) will switch its Shorts scene visuals from
AI-generated Imagen stills to Pexels stock video clips — the same API
(`PEXELS_API_KEY`, already provisioned) that the separate long-format Tell
Me Why pipeline already uses successfully.

## Scope

- **In scope**: `channel_id="news"` Shorts scene generation only.
- **Out of scope**:
  - Tell Me Why Shorts (`channel_id="stories"`, `script_mode="facts"`) —
    keeps using Imagen and its illustrated storybook style, unchanged.
  - The existing Tell Me Why long-format pipeline
    (`long_generator_agent.py` / `long_video_service.py`) — entirely
    unaffected, continues as-is.
  - Thumbnails (`generate_thumbnail()`, 16:9) for either channel — still
    calls Imagen (still broken); addressed as a separate follow-up.
  - No fallback to Imagen for news scenes — Pexels fully replaces it for
    this pipeline. If Imagen access is restored later, that's a separate
    decision or revert.

## Architecture / Data Flow

```
generate_script_with_search(script_mode="news")
    -> scene["visual_query"]  (short Pexels search phrase, replaces scene["visual"])
        |
generator_agent.run()  [per scene, channel_id == "news"]
    -> pexels_service.fetch_clip(query=visual_query, audio_duration, scene_idx, category=domain)
        -> returns local .mp4 path (or "" on total exhaustion)
        |
video_service.create_video()
    -> scene asset is a video clip: load via VideoFileClip, trim/loop to audio_duration,
       center-crop/resize to 1080x1920, no Ken-Burns (real footage already has motion)
        |
      (unchanged) subtitle overlay, music mix, concatenation, GCS + YouTube upload
```

`channel_id="stories"` scenes are untouched — still `scene["visual"]` ->
`image_service.generate_image()` -> static `ImageClip` + Ken-Burns.

## Component Changes

### `app/services/llm_service.py`

- `script_mode="news"` scene schema: rename `"visual"` -> `"visual_query"`.
  Replace the Imagen-style-prompt instructions with Pexels search-phrase
  rules: 3-7 concrete, specific words, following the same pattern already
  proven in `generate_long_facts_script()`'s `VISUAL_QUERY RULES` (e.g.
  "isolated researcher microscope lab night" rather than "science
  research").
- `script_mode="facts"` (Tell Me Why Shorts) — no changes; still emits
  `"visual"` for Imagen.
- `apply_quality_controls()` (~line 548): branch on which key is present.
  `sanitize_visual_prompt_no_text()` still runs on `visual` for stories;
  for news, run `sanitize_copyright_risks()` on `visual_query` only — skip
  the Imagen-specific "no embedded text" sanitizer, which doesn't apply to
  a search phrase.
- `senior_script_reviewer.py`'s `_tighten_if_too_long()` dict-rebuild
  (~line 51): preserve whichever key (`visual` or `visual_query`) is
  present on the incoming scene instead of hardcoding `"visual"`.

### `app/agents/generator_agent.py`

- Scene loop branches on `channel_id`:
  - `"news"` -> `pexels_service.fetch_clip(scene["visual_query"],
    audio_duration, idx, category=domain)`.
  - `"stories"` -> existing `image_service.generate_image(scene["visual"],
    idx, aspect_ratio="9:16")` call, unchanged.
- `domain`/genre already available in the job payload for news (currently
  used for Imagen style selection) — reused as the Pexels `category`
  fallback key.
- Checkpointing: continues writing to the existing `image_path` checkpoint
  field name — it's a generic "local scene asset path" field with no
  schema enforcement, so no rename needed there.

### `app/services/pexels_service.py`

- `_search_pexels()`: add an `orientation` param (default remains
  `"landscape"` for the existing long-format caller); the news path calls
  with `orientation="portrait"` first.
- `fetch_clip()`: on total exhaustion of the portrait fallback chain
  (exact query -> broad query -> category fallback -> generic), retry the
  same chain once more with `orientation="landscape"` (a downstream crop
  step in `video_service.py` handles portrait conversion of that footage).
  Only if that also fully exhausts does it return `""`, same sentinel
  behavior as today for the long-format caller — `generator_agent.py`
  turns that into a scene failure (see Error Handling below).
- `_CATEGORY_FALLBACKS`: add News domains — `artificial intelligence`,
  `technology`, `current affairs`, `science`, `health`, `business`,
  `sports`, `entertainment`, `environment` — mapped to reasonable
  stock-search fallback terms.

### `app/services/video_service.py`

- `create_video()`: per-scene branch on asset file extension.
  - `.mp4` (news): load via `VideoFileClip`; trim if longer than
    `audio_duration`, loop if shorter (mirroring the existing pattern in
    `long_video_service.py`); crop-to-cover at 1080x1920 (new portrait
    variant of the existing `_fit_cover` crop helper).
  - `.png` (stories): existing static-image + Ken-Burns path, unchanged.
- Subtitle overlay, safe-zone text placement, music mixing, and
  concatenation are unchanged — they already operate on the composited
  MoviePy clip regardless of its origin.

### `app/services/image_service.py`

- Untouched. Still used for Tell Me Why Shorts scenes and both channels'
  thumbnails.

## Error Handling / Fallback

**Correction from initial design pass:** `image_service.generate_fallback_image()`
(the gradient+text-card frame) is not actually wired into any per-scene
failure path in `generator_agent.py` today — it's unused in production,
exercised only by one direct unit test. A scene that fully fails today
(all Imagen retries exhausted) simply increments `image_failures` and
counts against the existing `MIN_CLIPS` threshold; it does not get a
generated fallback frame. The News/Pexels error handling below matches
that actual behavior rather than inventing a new fallback-card path.

- **Per-scene fetch chain** (already built into `pexels_service.fetch_clip`):
  exact `visual_query` -> broad query (first two words) -> category
  fallback -> generic term, each tried at `orientation="portrait"`, then
  the same chain again at `orientation="landscape"` (cropped to portrait
  after download by `video_service`). If that is *also* fully exhausted,
  `fetch_clip` returns `""` as it does today for the long-format caller.
- **Empty-string result is treated as a scene failure**: `generator_agent.py`'s
  news branch raises an exception when `fetch_clip` returns `""`, which
  flows into the exact same existing failure handling as an Imagen
  exception today — increments `image_failures`, marks the scene
  checkpoint `"failed"`, and (if too many scenes fail) triggers the
  existing `MIN_CLIPS` / "all scenes failed" abort path. No new fallback
  frame behavior is introduced.
- **Network/HTTP errors** (timeouts, Pexels 5xx): caught per-attempt inside
  the existing fallback chain in `pexels_service.py`, logged, chain
  continues to the next fallback. No outer retry/backoff layer — Pexels
  has no documented quota-window behavior comparable to Imagen's 429s, so
  a plain per-request timeout is sufficient.
- **Rate limiting (429 from Pexels)**: treated as just another exception in
  the fallback chain (falls through to the next query/category) rather
  than a sleep-and-retry. Pexels' free-tier limit is per-hour, so
  same-run retries are unlikely to help within a single ~2-3 minute
  video-generation job; falling through to a different query lets the
  *next* run succeed instead.
- **Scene-level outcome**: unchanged — `MIN_CLIPS` (`max(1, MAX_SCENES - 1)`)
  still governs whether a video proceeds to assembly. Fewer than that many
  real clips still means `status="failed"`, Telegram notification, no
  upload.
- **No safety-filter equivalent**: `SAFETY_FILTER_ERROR_PREFIX` /
  content-policy short-circuit logic is Imagen-specific and isn't
  triggered on the news branch (dead code path for that branch; not
  removed since stories still uses it).

## Testing Plan

- `tests/test_pexels_service.py`: extend with cases for the new
  `orientation` param — portrait search success, portrait exhaustion ->
  landscape fallback triggers, and category-fallback coverage for the new
  News domain keys.
- `tests/test_llm_service.py`: update/add assertions that generated news
  scenes carry `visual_query` (not `visual`), and that
  `apply_quality_controls()` handles a news scene dict correctly (no crash
  from a missing `visual` key; `sanitize_visual_prompt_no_text` skipped
  for news scenes).
- `tests/test_pipeline.py` (or wherever `generator_agent` is covered): add
  a case exercising the news-channel branch — mock
  `pexels_service.fetch_clip` to return a clip path, assert
  `image_service.generate_image` is *not* called for `channel_id="news"`
  scenes, and that it *is* still called unchanged for
  `channel_id="stories"`.
- `tests/test_video_service.py`: add a case for the new video-clip scene
  path in `create_video()` — a `.mp4` scene asset gets loaded via
  `VideoFileClip`, trimmed/looped to audio duration, and cropped to
  1080x1920, without invoking the Ken-Burns/`create_animated_scene_clip`
  path. Existing static-image test cases remain unaffected.
- **Manual/production verification** (external API + real GCS/YouTube,
  can't be unit-tested): after merging, trigger one `CREATE <topic>` news
  job manually and visually confirm the resulting Short — portrait footage
  looks reasonable (no bad crops), subtitles still readable in the safe
  zone, audio/video sync per scene is correct, and total video length is
  still in the ~45-55s Shorts range.

No new GCP credentials or GitHub Actions secrets are needed beyond the
existing `PEXELS_API_KEY`.
