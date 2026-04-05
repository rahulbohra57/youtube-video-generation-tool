# Design: Aspect Ratio Toggle + YouTube Shorts Caption Agent

**Date:** 2026-04-04  
**Status:** Approved

---

## Overview

Two additive features for the AUTOFRAME video generation tool:

1. **Aspect ratio selector** — lets users choose between 16:9 (landscape/YouTube) and 9:16 (portrait/YouTube Shorts) before generating a video.
2. **Shorts caption agent** — a separate, on-demand button in the results section that generates a YouTube Shorts caption with hashtags for the current topic.

---

## Feature 1: Aspect Ratio Toggle

### Frontend (`app/static/index.html`)

- Add a ratio selector row in the hero section, between the language selector and the topic input.
- Two pill buttons using the existing `.lang-btn` style: `▬ 16:9  LANDSCAPE` and `▯ 9:16  SHORTS`.
- Default selection: `16:9`.
- JS state variable `selectedRatio` tracks the current selection.
- `generateVideo()` appends `&aspect_ratio=<value>` to the `/generate` fetch call.

### Backend (`app/routes/generate.py`)

- `/generate` gains a new query param: `aspect_ratio: str = "16:9"`.
- Validated to one of `["16:9", "9:16"]`; defaults to `"16:9"` for any invalid value.
- Passed down to both `generate_script()` and `generate_image()`.

### Image Service (`app/services/image_service.py`)

- `generate_image(prompt, idx, aspect_ratio="16:9")`
- Passes `aspect_ratio` directly to Imagen 3's `aspect_ratio` field.
- Style hint in `enhanced_prompt` changes based on ratio:
  - `16:9` → existing `"youtube educational thumbnail style, high quality, cinematic lighting, 16:9"`
  - `9:16` → `"vertical short-form video, portrait orientation, YouTube Shorts style, high quality"`

### LLM Service (`app/services/llm_service.py`)

- `generate_script(topic, language, aspect_ratio="16:9")`
- When `aspect_ratio == "9:16"`, prompt instructs Gemini:
  - Max 3 scenes
  - Narrations ≤ 15 seconds (short, punchy, hook-first)
- When `aspect_ratio == "16:9"`, existing prompt behaviour (max 5 scenes) is unchanged.

---

## Feature 2: YouTube Shorts Caption Agent

### LLM Service (`app/services/llm_service.py`)

New function: `generate_shorts_caption(topic: str, language: str = "en") -> str`

- Prompts Gemini 2.5 Flash to produce:
  - 2–3 punchy lines describing/teasing the topic
  - 10–15 hashtags (mix of broad and niche)
- Returns the raw caption string.
- Language is passed via the existing `_LANG_INSTRUCTIONS` map so captions can be in Hindi too.

### Route (`app/routes/generate.py`)

New endpoint: `POST /generate-caption?topic=&language=`

- Calls `generate_shorts_caption(topic, language)`.
- Returns `{ "caption": "..." }`.
- Raises `HTTP 400` if topic is empty or blank.

### Frontend (`app/static/index.html`)

In the results section, below `.video-controls-footer`:

1. A `GENERATE CAPTION` button styled with `--teal` color scheme (similar to `.download-btn` but teal-bordered).
2. On click:
   - Button enters loading state.
   - Calls `POST /generate-caption?topic=<currentTopic>&language=<selectedLanguage>`.
3. On success: an inline caption card appears below the button row containing:
   - Header label: `YOUTUBE SHORTS CAPTION`
   - Caption text in a styled block
   - A `COPY` button that writes to clipboard and briefly shows `COPIED ✓`
4. On error: existing `showError()` toast is reused.

The JS reuses the already-available `currentTopic` and `selectedLanguage` state variables — no extra user input required.

---

## Data Flow

```
User selects ratio → JS selectedRatio
User clicks GENERATE
  → POST /generate?topic=&language=&aspect_ratio=
      → generate_script(topic, language, aspect_ratio)   [LLM — adjusts scene count/length]
      → generate_image(visual, idx, aspect_ratio)         [Imagen 3 — correct aspect_ratio]
      → generate_audio(narration, path, language)         [unchanged]
      → create_video(clips, output_path)                  [unchanged]
  ← { status, video_url, num_scenes }

User clicks GENERATE CAPTION (results section)
  → POST /generate-caption?topic=&language=
      → generate_shorts_caption(topic, language)          [LLM]
  ← { caption: "..." }
  → Inline card with Copy button
```

---

## Files Changed

| File | Change |
|------|--------|
| `app/static/index.html` | Ratio toggle UI, caption button + card UI, JS wiring |
| `app/routes/generate.py` | `aspect_ratio` param on `/generate`; new `/generate-caption` endpoint |
| `app/services/llm_service.py` | `aspect_ratio` param on `generate_script()`; new `generate_shorts_caption()` |
| `app/services/image_service.py` | `aspect_ratio` param on `generate_image()`; updated prompt hint |

No new files. No schema migrations. No dependency changes.
