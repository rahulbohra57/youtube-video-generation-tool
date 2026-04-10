# CLAUDE.md — Autoframe YouTube Video Generation Tool

Critical operational constraints, deployment rules, system gotchas, and detailed app architecture.
Read this before making any infrastructure or code change.

---

## GCP Project & Service Identifiers

| Resource | Value |
|---|---|
| GCP Project ID | `youtube-video-generator-492211` |
| Cloud Run service | `autoframe` |
| Cloud Run region | `us-central1` |
| Cloud Run URL | `https://autoframe-353645494126.us-central1.run.app` |
| GCS bucket | `yt-gen-app-bucket` |
| Cloud Tasks queue | `autoframe-generate` |
| Service account | `353645494126-compute@developer.gserviceaccount.com` |

---

## App Architecture Overview

The app is a single **FastAPI** service (`app/main.py`) running on Cloud Run that manages two
independent YouTube Shorts channels:

| Channel | Language | YouTube Name | Telegram Bot | Scheduler |
|---|---|---|---|---|
| **News** (`channel_id="news"`) | English | Kurrent Affairs | `TELEGRAM_BOT_TOKEN` | Every 2h |
| **Stories** (`channel_id="stories"`) | Hindi | Short Tales | `STORIES_BOT_TOKEN` | Every 6h |

Each channel has its own:
- Telegram bot + chat ID
- YouTube OAuth credentials + channel
- Firestore `pipeline_state` namespace
- Scheduler-triggered research pipeline

Both channels share a single Cloud Tasks queue, the same `generator_agent.run()` pipeline,
and the same Imagen / TTS / LLM services.

**FastAPI route modules:**

| Module | Prefix | Purpose |
|---|---|---|
| `app/routes/webhook.py` | `/webhook/telegram` | News bot incoming messages |
| `app/routes/stories_webhook.py` | `/webhook/telegram/stories` | Stories bot incoming messages |
| `app/routes/generate.py` | `/generate`, `/jobs` | Cloud Tasks delivery + web API |
| `app/routes/stories.py` | `/stories`, `/generate/stories-task` | Stories scheduler + Cloud Tasks delivery |
| `app/routes/research.py` | `/research` | News scheduler endpoints |
| `app/routes/auth.py` | `/auth` | YouTube OAuth flow |
| `app/routes/admin.py` | `/admin` | Admin dashboard |

---

## News Channel Pipeline (Kurrent Affairs)

### Step 1 — Research (`/research/run`, every 2h)

Triggered by Cloud Scheduler → `lead_researcher.run()`:

1. **Fetch news** via GNews for 5 primary domains: Technology, Artificial Intelligence,
   Current Affairs, Trending, Science. One API call per domain, 25 results each.
2. **Filter** by recency (last 24h) and dedup against Firestore `suggested_headlines`.
3. **Rate & select** each domain's top articles using Gemini 2.5 Flash via `rate_and_select_news()`.
4. **Score** with a composite formula: `(LLM rating × 0.60) + (recency × 2.0) + trend_bonus`.
5. **Domain selection**: prioritize domains not yet posted today (weighted by historical performance).
   Falls back to highest-scoring article if all domains covered; uses fallback domains (Health,
   Business, Sports, Entertainment, Environment) as last resort.
6. **Save batch** to Firestore `news_batches` + set `pipeline_state` to `processing`.
7. **Enqueue Cloud Task** via `whatsapp_agent._enqueue_generate()` → queue item delivered to
   `/generate/task`.
8. **Notify** the Kurrent Affairs Telegram channel with the selected headline and virality score.

### Step 2 — Video Generation (`/generate/task`)

Cloud Tasks calls this endpoint with a JSON payload. The endpoint calls `generator_agent.run()`.
Returns HTTP 200 in all cases to prevent Cloud Tasks auto-retry (which would create duplicates).

### Step 3 — News Bot Commands (via `/webhook/telegram`)

All bot commands are handled by `whatsapp_agent.handle_reply()`:

| Command | Behavior |
|---|---|
| `STATS` | Live YouTube channel stats + pipeline queue summary |
| `COMMANDS` | Show all available bot commands |
| `CREATE <topic>` | Search for recent source article (last 72h), then enqueue video |
| `CREATE <topic> \| <context>` | Same, with extra context or article URL |
| `FORCE_CREATE <topic>` | Skip pipeline busy check and dedup. No source article required. |
| `DISCARD` | Discard a pending digest awaiting reply |
| `REDO <id>` | Re-upload from GCS to YouTube, or fall back to Telegram delivery. No GCS → full regeneration. |
| `RESEND <id>` | Send GCS video + caption to Telegram (never uses YouTube API) |
| `STOP <id>` | Cancel a queued or in-progress job |
| `PRIVATE <id>` | Set a published YouTube video to private |
| `DELETE <id>` | Delete a YouTube video permanently |

**CREATE source article enforcement** (`channel_id="news"` only):
- Searches Google Custom Search first, then GNews, for a recent article (last 72h).
- If no article found and no user-provided URL, bot asks user to provide one or DISCARD.
- Source article metadata (headline, URL, published_at) is injected into the script generation
  prompt as a "strict source of truth" to prevent model hallucination.

---

## Stories Channel Pipeline (Short Tales)

### Step 1 — Story Idea Generation (`/stories/run`, every 6h)

Triggered by Cloud Scheduler → `story_researcher.run()`:

1. **Check pipeline state** — skip if already `processing`.
2. **Genre rotation**: deterministic 6-hour slot rotation across 12 genres (inspiring, comedy,
   heartfelt, crime, action, sci-fi, mythology, thriller, mystery, adventure, slice-of-life,
   historical). Formula: `slot = timestamp // (6 * 3600)`, `genre = genres[slot % 12]`.
3. **Generate story idea** via LLM: title + premise in Hindi for the selected genre.
   Recent 30-day titles are passed to the LLM to avoid repeating topics.
4. **Dedup** against Firestore `suggested_headlines` (30-day window, `channel_id="stories"`).
5. **Save batch** + set pipeline state to `processing`.
6. **Enqueue Cloud Task** to `/generate/stories-task` with `force_run=True`.
7. **Notify** the Short Tales Telegram channel.

### Step 2 — Story Video Generation (`/generate/stories-task`)

Cloud Tasks calls this endpoint. Calls `generator_agent.run()` with `script_type="story"`,
`channel_id="stories"`. `force_run=True` is always set for auto-generated stories.

### Step 3 — Stories Bot Commands (via `/webhook/telegram/stories`)

`stories_agent.handle_reply()` delegates to `whatsapp_agent.handle_reply(channel_id="stories")`.
All the same commands apply (STATS, CREATE, REDO, RESEND, STOP, PRIVATE, DELETE, FORCE_CREATE).

Stories CREATE does NOT require a source article — stories are LLM-generated fiction/fables.

---

## Video Generation Pipeline (`generator_agent.run()`)

This is the core pipeline shared by both channels. Every video goes through these stages:

### Guards (checked first, before any work)

1. **Idempotency guard**: If `job_id` already exists in Firestore with status
   `completed`, `delivered_manual`, or `cancelled` → return immediately (handles Cloud Tasks
   duplicate deliveries).
2. **Video lock**: Distributed lock in Firestore `video_lock`. `force_run=True` bypasses via
   `acquire_video_lock(force=True)`. Without force, if lock is held → `rejected_busy`.
3. **Pipeline state check**: If pipeline is `processing` for a different batch → `stale_rejected`.
   Returning 200 prevents Cloud Tasks retry.
4. **Cancellation check**: `_is_cancel_requested()` is polled before each scene and before
   video assembly. Sets status to `cancelled` and returns.

### Script Generation

- **News** (`script_type="news"`, `language="en"`): calls `generate_script_with_search()` which
  uses Gemini 2.5 Flash with Google Search grounding. Falls back to `generate_script()` on failure.
- **Stories** (`script_type="story"`, `language="hi"`): calls `generate_story_script()` which
  prompts Gemini for a Hindi moral story with narration + visuals.

Script JSON is extracted, quality-controlled via `apply_quality_controls()`, then reviewed and
finalized (title, caption, scenes) by `review_package()` in `senior_script_reviewer.py`.

The `reviewed_title` is persisted to Firestore immediately so REDO always uses the same title
that was uploaded, not the raw LLM idea.

### Scene Generation (per scene, up to `MAX_SCENES = 3`)

For each scene:
1. **TTS Audio**: `generate_audio()` → Cloud TTS Neural2 voice → local `.mp3`.
2. **Image**: `generate_image()` → Vertex AI Imagen 3 → local `.png` (9:16 portrait).
3. **Checkpoint**: scene progress saved to Firestore `scene_progress`. If a Cloud Tasks retry
   delivers the same task, completed scenes are skipped (resume from where it left off).

**Imagen retry logic (two layers)**:

*Inner layer* (`image_service.generate_image()`):
- Quota/rate-limit (429): retry 3 times with waits of 30s → 60s → 120s.
- Safety filter (empty response): raise immediately with `SAFETY_FILTER_ERROR_PREFIX` —
  retrying the same prompt is pointless.
- Other errors: raise immediately.

*Outer layer* (`generator_agent._run_with_backoff()`):
- Quota errors: wait `QUOTA_OUTER_RETRY_DELAY = 120s` between outer attempts.
- Safety filter: raise immediately (same prefix detected, skip all outer retries).
- Other errors: exponential backoff (2s / 4s).
- Max outer retries: `SCENE_MAX_RETRIES = 3`.

**Failure handling**:
- If `image_failures >= MAX_SCENES` (all 3 scenes fail): notify Telegram, set status `failed`,
  return without uploading anything.
- If fewer than `MIN_CLIPS = max(1, MAX_SCENES - 1) = 2` clips succeed: notify Telegram, set
  status `failed` with `error_type="insufficient_video_clips"`, return. Prevents partial
  (e.g. 15-second) videos from being uploaded.

### Video Assembly

`video_service.create_video()` uses MoviePy to:
1. Load each `(image_path, audio_path, narration)` clip.
2. Overlay subtitles on the image (language-aware font: DejaVuSans for English, Lohit-Devanagari
   for Hindi). Text is rendered in YouTube Shorts safe zones (bottom 30% excluded).
3. Mix background music (from `assets/music/<genre>/`) at 15% volume with voice-over at 108%.
4. Concatenate all clips → `.mp4` at 24 FPS.

### Upload and Delivery

1. **GCS upload**: video uploaded to `yt-gen-app-bucket/videos/` immediately after assembly.
   GCS URL stored as `gcs_video_url` in Firestore. This makes REDO/RESEND work even after
   YouTube upload fails.
2. **YouTube upload**: `social_media_agent.post()` → `youtube_service.upload_video()`.
   - If upload succeeds: adds to genre playlist, marks domain as posted today, sends Telegram
     notification via `whatsapp_agent.send_post_result()` or `stories_agent.send_post_result()`.
   - If upload fails (quota, auth, network): delivers video to Telegram via
     `send_video_for_manual_post()`, sets status `delivered_manual`. These jobs are excluded
     from the retry queue.
3. **Pipeline state finalized**: `set_pipeline_and_batch_state(batch_id, "completed")` releases
   the pipeline for the next scheduler run.

---

## LLM and Script Generation (`app/services/llm_service.py`)

- **Model**: Gemini 2.5 Flash (`gemini-2.5-flash`) via Vertex AI.
- **Script format**: JSON array of scene objects: `{scene, narration, visual}`.
  - 9:16 (Shorts): max 5 scenes, 20-24 words per narration, target 45-55s total.
  - Visual prompts are always in English regardless of content language.
- **Quality controls**: `apply_quality_controls()` strips profanity and copyright-risky terms.
- **Title/caption language**: `_TITLE_CAPTION_LANG_INSTRUCTIONS` provides explicit instructions
  separate from narration language instructions. For Hindi (`"hi"`), the rule is:
  "Write the title and caption in Hindi (Devanagari script). Do NOT use English for the title
  or caption body." This prevents the LLM from writing Hindi narration but English titles.
- **Music genre**: `classify_music_genre()` picks one of: Cheerful, Happy, News Bulletin,
  Party, Sad-Emotional, Suspense.

---

## TTS Service (`app/services/tts_service.py`)

- **Provider**: Google Cloud Text-to-Speech.
- **English voices**: `en-US-Neural2-*` variants.
- **Hindi voices**: `hi-IN-Neural2-*` or `hi-IN-Wavenet-*` only.
- **NEVER use `hi-IN-Chirp3-HD-*`** — $160/1M chars, no free tier. Removed April 2026.
- Voice is selected per-video via `choose_voice_for_video(language, preference, domain)`.
  `preference="shuffle"` picks a random voice from the pool.

---

## Imagen Service (`app/services/image_service.py`)

- **Model**: `imagen-3.0-generate-002` (Imagen 3, highest quality, Jan 2025).
- **Always 9:16** for the Cloud Tasks pipeline (Shorts format). Web API uses the requested ratio.
- **Style**: flat design, animated explainer, consistent color palette.
- **Negative prompt**: blocks real faces, celebrity likenesses, copyright characters, brand logos.
- **Safety filter constant**: `SAFETY_FILTER_ERROR_PREFIX = "imagen_safety_filter:"` — used to
  detect and short-circuit retries for content-policy rejections.

---

## Firestore Schema

| Collection | Key | Purpose |
|---|---|---|
| `pipeline_state` | `"news"` / `"stories"` | Active batch ID + state per channel |
| `jobs` | `job_id` | One doc per video job; all status fields |
| `news_batches` | `batch_id` | Research batch with article candidates |
| `credentials` | `"youtube"` / `"youtube_stories"` | YouTube OAuth tokens per channel |
| `quota_events` | auto | GNews + TTS + Imagen quota tracking |
| `social_metrics` | `"youtube"` / `"youtube_stories"` | Cached YouTube channel stats |
| `idempotency_keys` | `scope:key` | CREATE command dedup (TTL: 20min) |
| `video_lock` | `"lock"` | Distributed generation lock |
| `domain_posts` | `"YYYY-MM-DD"` | Domains posted today (resets daily) |
| `suggested_headlines` | `sha1(headline)` | 14-day news / 30-day story dedup |

**Critical job fields** (used by REDO, RESEND, analytics):

| Field | Set by | Used by |
|---|---|---|
| `reviewed_title` | `generator_agent` after script review | REDO (ensures same title on re-upload) |
| `final_caption` | `social_media_agent.post()` before upload | REDO, RESEND |
| `gcs_video_url` | `generator_agent` after GCS upload | REDO, RESEND |
| `youtube_url` | `social_media_agent.post()` on success | REDO, PRIVATE, DELETE, analytics |
| `scene_progress` | `generator_agent` per scene | Cloud Tasks retry resume |

---

## Idempotency and Deduplication

Three layers prevent duplicate videos:

1. **Cloud Tasks task name** (`generate-<batch_id>-<code>`): deterministic — Cloud Tasks returns
   `AlreadyExists` if enqueued twice. Handled gracefully in `_enqueue_generate()`.
2. **Job status idempotency guard** (top of `generator_agent.run()`): if job already in terminal
   state (`completed`, `delivered_manual`, `cancelled`) → skip immediately. Handles the case
   where Cloud Tasks delivers the same task twice after a long-running first attempt.
3. **CREATE topic idempotency** (`idempotency_keys` collection): SHA-1 of the normalized topic
   string, TTL 20 minutes. Prevents duplicate CREATE commands for the same topic.
4. **Webhook update dedup** (`_seen_update_ids` in `webhook.py`): in-memory set prevents the
   same Telegram update from being processed twice. Cleared after 1000 entries.
   Reason `min-instances=1` is required.

---

## Cloud Scheduler Jobs

| Job ID | Schedule (IST) | Endpoint | Notes |
|---|---|---|---|
| `autoframe-lead-researcher` | `0 */2 * * *` (every 2h) | `/research/run` | News pipeline trigger |
| `autoframe-retry-failed` | `0 */4 * * *` (every 4h) | `/research/retry-failed` | Retry last failed news job |
| `autoframe-daily-digest` | `0 8 * * *` (8am) | `/research/daily-digest` | News channel daily report |
| `autoframe-update-analytics` | `0 22 * * *` (10pm) | `/research/update-analytics` | **Paid — $0.10/month** |
| `autoframe-stories-run` | `0 */6 * * *` (every 6h) | `/stories/run` | Stories pipeline trigger |
| `autoframe-stories-digest` | `30 8 * * *` (8:30am) | `/stories/daily-digest` | Short Tales daily report |

**All scheduler endpoints require the `X-Scheduler-Secret` header.**

**Never change the news research schedule to less than 2 hours** — GNews free tier is 100 calls/day;
12 cycles/day × 5 domains = 60 calls. 1h schedule → 120 calls → quota exhaustion.

---

## CRITICAL: Cloud Run Deployment Rules

### 1. ALWAYS deploy with `--allow-unauthenticated`

```bash
gcloud run deploy autoframe \
  --source . \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --allow-unauthenticated
```

**Why:** Both Telegram webhooks are called directly by Telegram's servers. Telegram has no way
to present GCP auth credentials. Using `--no-allow-unauthenticated` blocks all Telegram traffic
→ webhook returns 403 Forbidden → all bot commands silently stop working.

### 2. Never change these Cloud Run flags without understanding the impact

| Flag | Current value | Why it must stay this way |
|---|---|---|
| `--min-instances=1` | 1 | The in-memory `_seen_update_ids` dedup sets in both webhook modules live in RAM. Cold starts lose dedup history and the bot may double-process commands. |
| `--cpu-throttling=false` | off | Video encoding (moviepy) and image generation are CPU-heavy. CPU throttling causes `ffmpeg` to time out mid-render. |
| `--startup-cpu-boost` | on | Reduces cold-start latency. |
| `--memory=4Gi` | 4 GiB | moviepy/PIL load full video frames into memory. Less than 4 GiB causes OOM kills mid-generation. |
| `--cpu=4` | 4 vCPU | Matches the memory/CPU ratio for video processing. |
| `--timeout=3600` | 3600 s | Video generation + YouTube upload can take 10–15 minutes. Default 300s timeout kills running jobs. |

### 3. Standard deploy command (copy-paste safe)

```bash
cd "/Users/chetan/Desktop/DSE_Projects/youtube-video-generation-tool"
gcloud run deploy autoframe \
  --source . \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --allow-unauthenticated
```

---

## Telegram Webhooks

Two separate webhook URLs, one per bot:

| Channel | Webhook URL | Bot Token Env Var | Chat ID Env Var |
|---|---|---|---|
| News (Kurrent Affairs) | `.../webhook/telegram` | `TELEGRAM_BOT_TOKEN` | `TELEGRAM_CHAT_ID` |
| Stories (Short Tales) | `.../webhook/telegram/stories` | `STORIES_BOT_TOKEN` | `STORIES_CHAT_ID` |

Authentication is by matching `chat.id` against the configured chat ID env var — no HMAC secret.
The Cloud Run service must be publicly accessible (see deployment rules above).

**After any deploy, verify both webhooks:**
```bash
curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
curl -s "https://api.telegram.org/bot<STORIES_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
# last_error_message must be absent or stale. pending_update_count should be ~0.
```

---

## Pipeline State & Concurrency

Two mechanisms enforce one video at a time **per channel**:

1. **Firestore `pipeline_state`** — keyed by `channel_id` (`"news"` / `"stories"`). Tracks
   active batch and state: `processing` / `completed` / `failed` / `skipped`.
2. **Firestore `video_lock`** — single global distributed lock (shared across both channels).
   Acquired in `generator_agent.run()`, released in the `finally` block.

**If the pipeline gets stuck in `processing`:**
- Set `pipeline_state` doc's `state` field to `"failed"` in Firestore console, OR
- Trigger `/research/retry-failed` — it checks for stale state automatically, OR
- Delete the `video_lock` document to release the lock.

**Never manually delete** `pipeline_state`, `news_batches`, or `jobs` documents while a video
is being generated — it orphans the Cloud Tasks task and causes infinite retries.

---

## YouTube OAuth Tokens

Two separate OAuth credential sets, one per channel:

| Channel | Firestore doc | Client ID env var | Redirect URI env var | Auth URL |
|---|---|---|---|---|
| News | `credentials/youtube` | `YOUTUBE_CLIENT_ID` | `YOUTUBE_REDIRECT_URI` | `/auth/youtube` |
| Stories | `credentials/youtube_stories` | `STORIES_YOUTUBE_CLIENT_ID` | `STORIES_YOUTUBE_REDIRECT_URI` | `/auth/youtube/stories` |

Tokens auto-refresh via `google-auth` on each API call. If "insufficient authentication scopes"
appears in STATS: re-authenticate by opening the auth URL in a browser.

**If the Cloud Run URL ever changes**, update both OAuth redirect URIs in GCP Console →
APIs & Services → Credentials, or OAuth flows will fail with `redirect_uri_mismatch`.

---

## API Quotas & Free Tier Limits

### GNews API
- **Free tier:** 100 requests/day
- **App usage (12 news/day):** ~60 calls/day (60%)
- **Circuit-breaker:** fires at 80 calls/day — returns `[]` without making HTTP call
- Quota tracked in Firestore `quota_events` (`kind == "gnews_call"`)
- **Never remove the circuit-breaker** in `gnews_service.py`

### Cloud TTS (Neural2)
- **Free tier:** 1,000,000 chars/month
- **App usage (12 videos/day):** ~864,000 chars/month (86%)
- **Hindi voices:** Must use `hi-IN-Neural2-*` or `hi-IN-Wavenet-*` only
- **Never use `hi-IN-Chirp3-HD-*` voices** — $160/1M chars, NO free tier. Removed April 2026.

### Vertex AI Imagen 3
- **Price:** $0.04/image (no free tier)
- **QPM limit:** 20 queries/minute
- **Inner retry:** 30s → 60s → 120s (3 attempts) in `image_service.generate_image()`
- **Outer retry:** 120s between outer attempts in `generator_agent._run_with_backoff()`
- **Images per video:** 3 scenes max (`MAX_SCENES = 3`) = $0.12/video
- **Monthly cost at 12/day:** ~$43.20 — the dominant cost (~90% of total spend)
- **MIN_CLIPS guard:** requires at least 2/3 scenes to succeed. If only 1 succeeds, video is
  dropped (prevents 15-second stub uploads).
- Never increase `MAX_SCENES` above 3 without reviewing Imagen quota and cost impact

### YouTube Data API v3
- **Free quota:** 10,000 units/day
- **Usage (12 videos/day):** ~2,413 units/day (24%)
- **Fallback:** YouTube upload failure → video delivered to Telegram (`delivered_manual`).
  These jobs are excluded from the auto-retry queue.

### Cloud Run Compute
- **Free tier:** 360,000 vCPU-seconds/month + 180,000 GiB-seconds/month
- **At 12 videos/day (360/month):** exceeds free tier — ~$3.44/month overage

---

## Environment Variables

All env vars are set directly on the Cloud Run service (no `.env` file in production).

```bash
gcloud run services describe autoframe \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --format="yaml(spec.template.spec.containers[0].env)"
```

| Variable | Purpose |
|---|---|
| `GNEWS_API_KEY` | GNews.io API key |
| `GOOGLE_SEARCH_API_KEY` | Google Custom Search API key (CREATE enrichment; 100 free/day) |
| `GOOGLE_SEARCH_ENGINE_ID` | Programmable Search Engine ID — must have "Search the entire web" enabled |
| `TELEGRAM_BOT_TOKEN` | News bot (Kurrent Affairs) Telegram token |
| `TELEGRAM_CHAT_ID` | News bot chat ID |
| `STORIES_BOT_TOKEN` | Stories bot (Short Tales) Telegram token |
| `STORIES_CHAT_ID` | Stories bot chat ID |
| `YOUTUBE_CLIENT_ID` | News channel YouTube OAuth 2.0 client ID |
| `YOUTUBE_CLIENT_SECRET` | News channel YouTube OAuth 2.0 client secret |
| `YOUTUBE_REDIRECT_URI` | Must match exactly what's registered in GCP OAuth client |
| `STORIES_YOUTUBE_CLIENT_ID` | Short Tales channel YouTube OAuth 2.0 client ID |
| `STORIES_YOUTUBE_CLIENT_SECRET` | Short Tales channel YouTube OAuth 2.0 client secret |
| `STORIES_YOUTUBE_REDIRECT_URI` | Must match exactly what's registered in GCP OAuth client |
| `SCHEDULER_SECRET` | Shared secret for all scheduler endpoint authentication |
| `CLOUD_RUN_URL` | Base URL of this service (used when enqueueing Cloud Tasks) |
| `TASKS_QUEUE` | Cloud Tasks queue name (`autoframe-generate`) |
| `ADMIN_DASHBOARD_SECRET` | Auth key for `/admin` dashboard |
| `TMP_RETENTION_DAYS` | Days to keep local temp files (default: 7) |
| `CREATE_TOPIC_IDEMPOTENCY_TTL_SECONDS` | CREATE dedup window (default: 1200 = 20min) |

To add a new env var:
```bash
gcloud run services update autoframe \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --set-env-vars="NEW_VAR=value"
```
Env vars set via `update` persist across `--source .` deploys.

---

## Cloud Tasks

- **Queue:** `autoframe-generate` in `us-central1`
- **Task URL routing:**
  - News: `CLOUD_RUN_URL/generate/task`
  - Stories: `CLOUD_RUN_URL/generate/stories-task`
- **Task naming:** Deterministic — `generate-<batch_id>-<code>`. Prevents duplicate tasks.
  Cloud Tasks returns `AlreadyExists` for duplicates (handled gracefully).
- **Dispatch deadline:** 1800 seconds (30 min) per task. Prevents Cloud Tasks from re-delivering
  while the original task is still running (generation typically takes 8-15 min).
- **OIDC auth:** Tasks dispatched with default compute SA OIDC token. Cloud Run must be public.
- **Retry policy:** Set at queue level. Generator always returns HTTP 200 (even on failure) to
  prevent Cloud Tasks from auto-retrying and creating duplicate videos.

---

## GCS Video Storage

- **Bucket:** `yt-gen-app-bucket`
- **Path prefix:** `videos/`
- **Retention:** 7 days (`TMP_RETENTION_DAYS` env var)
- Videos are uploaded to GCS immediately after generation, **before** YouTube upload attempt.
  REDO/RESEND work even if YouTube upload failed.
- GCS URL stored in Firestore `jobs` document as `gcs_video_url`.
- **After 7 days**, the GCS file is deleted. REDO on an old job falls back to full regeneration.

**Serving locally generated videos:**
- `GET /media/<filename>` — serves from local `Output/` if present, else 302 redirect to GCS.

---

## Firestore Collections Reference

| Collection | Purpose | Risk if corrupted |
|---|---|---|
| `pipeline_state` | Active batch + state per channel | Stuck in `processing` blocks all new videos |
| `jobs` | One doc per video generation job | Never delete — used for REDO/RESEND/analytics |
| `news_batches` | Research batches with article candidates | Deleting active batch orphans the pipeline |
| `credentials` | YouTube OAuth tokens (both channels) | Deletion requires full re-auth |
| `quota_events` | GNews + TTS + Imagen quota tracking | Used by circuit-breakers and daily digest |
| `social_metrics` | Cached YouTube channel stats (both channels) | Safe to delete — re-fetched on next STATS |
| `idempotency_keys` | Dedup for CREATE command | Safe to delete — allows re-submission |
| `video_lock` | Distributed generation lock | If stuck: delete the document to unblock |
| `domain_posts` | Tracks which domains posted today | Resets daily; safe to delete if domain coverage is wrong |
| `suggested_headlines` | 14-day news / 30-day story dedup | Safe to delete — just enables re-use of same topic |

---

## Post-Deploy Verification Checklist

Run these after every deploy:

1. **Both webhook health checks:**
   ```bash
   curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
   curl -s "https://api.telegram.org/bot<STORIES_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
   # Expect: no last_error_message (or stale), pending_update_count ~0
   ```

2. **Service reachable:**
   ```bash
   curl -o /dev/null -w "%{http_code}" -X POST \
     https://autoframe-353645494126.us-central1.run.app/webhook/telegram \
     -H "Content-Type: application/json" \
     -d '{"update_id":0,"message":{"chat":{"id":0},"text":"test"}}'
   # Expect: 200
   ```

3. **Send STATS from both Telegram bots** — confirms routing is correct for each channel.

4. **Check Cloud Run logs** for startup errors:
   ```bash
   gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=autoframe" \
     --project=youtube-video-generator-492211 \
     --limit=50 --format="table(timestamp,textPayload)"
   ```

---

## Common Failure Modes and Fixes

| Symptom | Cause | Fix |
|---|---|---|
| Same video uploaded 2–3 times | Cloud Tasks re-delivered after long run OR `force_run=True` bypassed guards | Idempotency guard at top of `generator_agent.run()` prevents this; check `dispatch_deadline=1800s` is set |
| Hindi story has English title | LLM used narration lang instruction for title too | `_TITLE_CAPTION_LANG_INSTRUCTIONS` provides explicit title language rule |
| 15-second stub video uploaded | 1/3 scenes succeeded but pipeline didn't abort | `MIN_CLIPS = 2` guard prevents upload below threshold |
| Videos keep failing with quota errors | Imagen QPM exhausted | Inner 30/60/120s retry + outer 120s delay. Check quota in Firestore `quota_events`. |
| Pipeline stuck in `processing` | Cloud Run OOM/timeout mid-generation | Set `pipeline_state.state = "failed"` in Firestore, or delete `video_lock` doc |
| REDO uploads with wrong title | Job stored raw topic, not reviewed title | `reviewed_title` persisted to Firestore immediately after script review |
| Webhook returns 403 | Service not public | Run: `gcloud run services add-iam-policy-binding autoframe --member="allUsers" --role="roles/run.invoker" ...` |
| CREATE rejects valid topic | No source article found in last 72h | Provide article URL: `CREATE <topic> \| <url>` |
