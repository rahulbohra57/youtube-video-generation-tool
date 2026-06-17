# Tell Me Why — Long-Format Video Pipeline

**Date:** 2026-06-17  
**Channel:** Tell Me Why (`channel_id="stories_long"` for state isolation, YouTube OAuth `channel_id="stories"`)  
**Format:** 16:9 landscape, 8–10 minutes, regular YouTube video (not Short)  
**Schedule:** Daily at 2pm IST (`30 8 * * *` UTC)  

---

## Goal

Add a daily long-format video to the Tell Me Why channel. Topics use the same 12 fact categories as the existing Shorts pipeline. Visuals come from Pexels video clips (not Imagen), a single Imagen thumbnail is generated and uploaded with the video, and TTS stays on Google Cloud Neural2 voices.

---

## Architecture

### New files

| File | Purpose |
|---|---|
| `app/services/pexels_service.py` | Pexels Videos API: search, download, trim clip to TTS duration |
| `app/services/long_video_service.py` | 16:9 MoviePy assembly: Pexels clip + TTS audio + subtitles + music |
| `app/agents/long_generator_agent.py` | Long-format pipeline orchestration |
| `scripts/run_long_stories.py` | Cron entry point: topic selection → dispatch workflow |
| `scripts/run_long_generate_video.py` | Runs `long_generator_agent.run()` from workflow payload |
| `.github/workflows/stories-long-run.yml` | Cron `30 8 * * *` (2pm IST daily) |
| `.github/workflows/generate-long-video.yml` | workflow_dispatch, concurrency `long-video-generation`, timeout 120 min |

### Modified files

| File | Change |
|---|---|
| `app/services/llm_service.py` | Add `generate_long_facts_script()` |
| `app/services/image_service.py` | Add `generate_thumbnail()` — Imagen 3, `16:9` aspect ratio |
| `app/services/youtube_service.py` | Add `set_thumbnail(video_id, thumbnail_path)` |

### Data flow

```
stories-long-run.yml  (2pm IST daily)
  → scripts/run_long_stories.py
      → _select_category()           [reused from story_researcher]
      → generate_fact_topic()        [reused from llm_service]
      → Firestore: save job (channel_id="stories_long")
      → dispatch generate-long-video.yml

generate-long-video.yml  (concurrency: long-video-generation, timeout: 120 min)
  → scripts/run_long_generate_video.py
      → long_generator_agent.run()
          → generate_long_facts_script()     [LLM, 25–30 scenes, Google Search grounded]
          → per scene:
              → generate_audio()             [Google Cloud TTS Neural2]
              → pexels_service.fetch_clip()  [search + download + trim]
          → generate_thumbnail()             [Imagen 3, 16:9, 1 image]
          → long_video_service.create_long_video()
          → GCS upload
          → youtube_service.upload_video()   [channel_id="stories", regular video]
          → youtube_service.set_thumbnail()
          → Telegram notification (Tell Me Why bot)
```

---

## Script Structure

### `generate_long_facts_script(topic, category, premise)`

Returns a JSON array of 25–30 scene objects. Uses Google Search grounding (same as `script_mode="facts"`) to verify the topic before scripting.

**Scene object schema:**
```json
{
  "scene": 1,
  "segment": "hook",
  "narration": "40–50 words of spoken content",
  "visual_query": "pexels search keyword e.g. 'deep ocean bioluminescence'"
}
```

### Four segments

| Segment | Scenes | Target duration | Rules |
|---|---|---|---|
| `hook` | 1–2 | 10–20s | Scene 1 first sentence ≤12 words. Open with a number, a named person doing something surprising, or a direct question. No context-setting. |
| `core` | 20–24 | ~7.5 min | One concrete insight per scene: real figures, dates, mechanisms, consequences. No filler. No unresolved cliffhangers. |
| `retention` | 1–2 | 30–45s | Engagement prompt: "Drop a comment", "What surprised you most?", teaser for a related topic. |
| `cta` | 1 | 15–20s | Like & Subscribe — warm, not pushy. No hard sell. |

### Scene count math

27 scenes × ~22 seconds each (45 words at 150 wpm) ≈ **9 minutes 54 seconds** — lands solidly in the 8–10 min window.

**Hard limits enforced by `long_generator_agent`:**
- Maximum: 30 scenes
- Minimum: 22 scenes (below this = `failed`, `error_type="insufficient_scenes"`)

### Prompt rules (enforced in LLM prompt)

- Scene 1 first sentence: ≤12 words
- Core scenes: 40–50 words each
- `visual_query`: plain English Pexels search phrase (2–5 words), always in English
- No filler phrases ("let's explore", "stay tuned", "game-changer")
- Every fact resolves within its scene — no cliffhangers
- Google Search grounding active for accuracy

---

## Pexels Integration (`pexels_service.py`)

### API call per scene

```
GET https://api.pexels.com/videos/search
    ?query={visual_query}
    &orientation=landscape
    &size=medium
    &per_page=5
```

Auth: `Authorization: {PEXELS_API_KEY}` header.

### Clip selection logic

1. Filter results to clips where `duration >= audio_duration`
2. Among those, pick the one closest in duration to `audio_duration` (least unused footage)
3. If none are long enough, pick the longest available clip (will be looped once)
4. Prefer HD (1280×720) or Full HD (1920×1080) file links

### Trim / loop

- If `clip.duration > audio_duration`: `subclipped(0, audio_duration)` — use only what's needed
- If `clip.duration < audio_duration`: loop once using MoviePy `VideoFileClip` concatenation

### Fallback chain

```
primary query          → "deep ocean bioluminescence"
broad fallback         → first 2 words: "deep ocean"
category fallback      → e.g. "science nature"
generic fallback       → "knowledge learning education"
black clip fallback    → ImageClip black frame, audio still plays
```

### Output resolution

All clips resized/cropped to **1920×1080** using `_fit_cover()` (imported from `video_service.py`).

### Environment variable

`PEXELS_API_KEY` — read at call time. Must be added to GitHub Secrets and `generate-long-video.yml`.

---

## Video Assembly (`long_video_service.py`)

### Per scene

1. Load trimmed Pexels VideoFileClip
2. Attach TTS audio with fade-in (0.15s) and fade-out (0.35s), VO_GAIN = 1.08×
3. Generate subtitle caption clips via `_make_word_caption_clips()` (imported from `video_service.py`) — uses landscape safe-zone constants (`_NORMAL_BOTTOM = 0.09`)
4. `CompositeVideoClip([pexels_clip] + caption_clips)`

### Final assembly

- `concatenate_videoclips(all_scene_clips, method="compose")`
- Background music: `_pick_music(music_genre)` looped to full video duration at 15% volume
- Output: `libx264` / AAC, 24fps, 1920×1080

### Key differences from `video_service.create_video()`

- No animation path (Pexels clips already move)
- No 9:16 safe-zone — uses landscape constants
- Base layer is `VideoFileClip` not `ImageClip`
- No `#Shorts` in description or tags

---

## Thumbnail (`image_service.generate_thumbnail()`)

- **Model:** Imagen 3 (`imagen-3.0-generate-002`)
- **Aspect ratio:** `16:9`
- **Prompt:** Built from `reviewed_title` using `_TMW_VISUAL_STYLE` base + directive: "thumbnail-worthy: high contrast, single bold focal subject, vivid complementary colors, no text, no words, no letters"
- **Output:** Saved as `thumbnail_{code}.png` in `TEMP_DIR`
- **Cost:** $0.04 (1 Imagen call per video)
- **Failure:** Non-fatal — video uploads without thumbnail, Telegram notification sent

---

## YouTube Upload

### `upload_video()` call (existing function, no change needed to signature)

- `channel_id="stories"` — uses Tell Me Why OAuth credentials
- `#Shorts` tag NOT added (handled by checking `channel_id` vs video duration in description builder)
- Category: `27` (Education)
- Title: `reviewed_title` (max 100 chars)
- Description: `_UPLOAD_DEFAULTS["stories"]` without `#shorts` hashtag

### `set_thumbnail(video_id, thumbnail_path)` (new function)

```python
def set_thumbnail(video_id: str, thumbnail_path: str, channel_id: str = "stories"):
    creds = get_credentials(channel_id=channel_id)
    youtube = build("youtube", "v3", credentials=creds)
    media = MediaFileUpload(thumbnail_path, mimetype="image/png")
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
```

Called immediately after `upload_video()` returns. Failure is non-fatal.

---

## Error Handling

| Failure | Behaviour |
|---|---|
| Pexels all fallbacks exhausted | Black `ImageClip` for that scene — audio still plays |
| TTS fails for a scene | Skip scene entirely, log warning |
| `len(scenes) < 22` after generation | `failed`, `error_type="insufficient_scenes"`, Telegram notify |
| Thumbnail Imagen fails | Upload video anyway, Telegram warning — non-fatal |
| `set_thumbnail()` fails | Log warning, Telegram notify — non-fatal, video is live |
| `generate_long_facts_script()` fails | Abort, mark `failed`, Telegram notify |

---

## Firestore

- `pipeline_state` key: `"stories_long"` — isolated from Shorts `"stories"` state
- `jobs` documents: `channel_id="stories_long"`, `video_type="long"`
- Stale lock detection: same 4-hour threshold as Shorts pipeline
- Lock: separate Firestore key `locks/long_video_generation` (does not share `locks/video_generation` with Shorts)

---

## GitHub Actions

### `stories-long-run.yml`

- Cron: `30 8 * * *` (2pm IST)
- Timeout: 10 minutes
- Runs: `scripts/run_long_stories.py`
- Secrets: same as `stories-run.yml` plus `PEXELS_API_KEY`

### `generate-long-video.yml`

- Trigger: `workflow_dispatch` only
- Concurrency group: `long-video-generation` (separate from `video-generation`)
- `cancel-in-progress: false` — queue, never cancel
- Timeout: **120 minutes**
- Runs: `scripts/run_long_generate_video.py`
- Secrets: same as `generate-video.yml` plus `PEXELS_API_KEY`

---

## New GitHub Secret

| Secret | Value |
|---|---|
| `PEXELS_API_KEY` | `6wUi8UhPYUZfzsWZO2q2ZzlsEPvzzIofXUKTnwFV6TWgtd037sS0L3mE` |

Add to: GitHub repo secrets + Vercel env vars (if ever called from webhook path — not needed for MVP).

---

## Telegram Notifications

Uses existing `STORIES_BOT_TOKEN` / `STORIES_CHAT_ID` (Tell Me Why bot). Message prefixes to distinguish from Shorts:

- Scheduler: `🎬 Generating long video...`
- Completion: `✅ Long video published!`
- Failure: `❌ Long video failed:`

---

## Cost per video

| Item | Cost |
|---|---|
| Pexels API | Free (attribution not required for API users) |
| Google Cloud TTS Neural2 (~1,400 words ≈ 9,000 chars) | ~$0 (within 1M char/month free tier) |
| Imagen 3 thumbnail (1 image) | $0.04 |
| YouTube Data API | ~50 units (well within 10,000/day free quota) |
| **Total** | **~$0.04/video** |

---

## Out of scope (this spec)

- Hindi long-format videos (English only, same as existing Tell Me Why Shorts)
- B-roll from providers other than Pexels
- Long-format REDO / RESEND bot commands (can be added later)
- Long-format inclusion in daily digest (can filter by `video_type` later)
