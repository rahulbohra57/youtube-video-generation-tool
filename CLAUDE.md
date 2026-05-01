# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Critical operational constraints, deployment rules, system gotchas, and detailed app architecture.
Read this before making any infrastructure or code change.

---

## Development Commands

### Local Development

```bash
# Activate virtualenv
source venv/bin/activate

# Run the FastAPI server locally (optional — only needed for OAuth flows)
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

External SDKs (Vertex AI, Firestore, GCS, TTS) require GCP credentials to be set up locally:
```bash
gcloud auth application-default login
```

### Tests

External SDKs are fully mocked in `tests/conftest.py` via `sys.modules` patching — no GCP credentials needed to run tests.

```bash
# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_pipeline.py

# Run a single test by name
pytest tests/test_pipeline.py::test_video_lock_rejected_when_held
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

---

## GCP Project & Service Identifiers

| Resource | Value |
|---|---|
| GCP Project ID | `youtube-video-generator-492211` |
| GCS bucket | `yt-gen-app-bucket` |
| Service account | `353645494126-compute@developer.gserviceaccount.com` |

> Cloud Run and Cloud Tasks are no longer used. The app runs on Vercel (webhooks + admin) and GitHub Actions (all scheduled tasks and video generation).

---

## App Architecture Overview

The app manages two independent YouTube Shorts channels:

| Channel | Language | YouTube Name | Telegram Bot | Scheduler |
|---|---|---|---|---|
| **News** (`channel_id="news"`) | English | Kurrent Affairs | `TELEGRAM_BOT_TOKEN` | GitHub Actions cron (`12am, 8am, 4pm IST`) |
| **Stories** (`channel_id="stories"`) | Hindi | Short Tales | `STORIES_BOT_TOKEN` | GitHub Actions cron (`7am, 11am, 2pm, 6pm IST`) |

**Deployment split:**

| Layer | Platform | Handles |
|---|---|---|
| **Webhooks + Admin API** | Vercel serverless (`api/`) | Telegram bot messages, admin dashboard metrics |
| **Scheduled pipelines** | GitHub Actions (`.github/workflows/`) | Research, story generation, video generation, digests, auth refresh |
| **Video generation runner** | GitHub Actions (`generate-video.yml`) | Full `generator_agent.run()` invoked as a workflow dispatch |
| **Core app logic** | Python (`app/`) | Shared by both Vercel functions and GitHub Actions runners |

**Vercel serverless functions (`api/`):**

| File | Route | Purpose |
|---|---|---|
| `api/webhook/telegram.py` | `/webhook/telegram` | News bot incoming messages |
| `api/webhook/stories.py` | `/webhook/telegram/stories` | Stories bot incoming messages |
| `api/admin/metrics/*.py` | `/admin/metrics/*` | Admin dashboard API endpoints |

**FastAPI app (`app/`) is still present** for the OAuth auth flows (`/auth/youtube`, `/auth/youtube/stories`) which require a persistent server. Run locally when re-authenticating YouTube tokens.

---

## News Channel Pipeline (Kurrent Affairs)

### Step 1 — Research (GitHub Actions `research-run.yml`, cron: `12am, 8am, 4pm IST`)

Triggered by GitHub Actions schedule → `scripts/run_research.py` → `lead_researcher.run()`:

1. **Slot-domain resolution**: Each scheduler slot has a pre-assigned domain (fetches one domain
   per run, not all five):
   - **Fixed slots**: `0h → Trending`, `8h → Artificial Intelligence`, `12h → Trending`
   - **Rotating slots** (`4h`, `16h`, `20h`): cycle through `schedule['rotating_domains']`
     (default: Technology, Current Affairs, Science). Rotation uses `day_of_year % 3` so every
     domain gets equal exposure across all time slots over a 3-day cycle.
   - Domain schedule stored in Firestore `config/domain_schedule`; updated fortnightly.
2. **Fetch news** via GNews for the assigned domain only (25 results, one API call per run).
   Falls back through remaining primary domains (performance-weighted), then fallback domains
   (Health, Business, Sports, Entertainment, Environment) if assigned domain yields nothing.
3. **Filter** by recency (last 24h) and dedup against Firestore `suggested_headlines`.
4. **Rate & select** top articles using Gemini 2.5 Flash via `rate_and_select_news()`.
5. **Score** with a composite formula: `(LLM rating × 0.60) + (recency × 2.0) + trend_bonus`.
6. **Save batch** to Firestore `news_batches` + set `pipeline_state` to `processing`.
7. **Dispatch GitHub Actions workflow** via `github_dispatch.dispatch_video_generation()` →
   triggers `generate-video.yml` with the job payload.
8. **Notify** the Kurrent Affairs Telegram channel with the selected headline and virality score.

**GNews quota impact**: Single-domain fetch at 3 runs/day = **3 calls/day** (97% reduction from the original 30/day). Well within the 100/day free tier.

**Fortnightly domain schedule auto-update** (`update_domain_schedule()`):
- Triggered by `update-analytics.yml` GitHub Actions workflow (10pm IST daily).
- Skips update if last update was < 14 days ago.
- Ranks eligible domains (all primaries except Trending/AI + all fallback domains) by 14-day
  avg views from `get_genre_performance_fortnightly()`.
- Top 3 by performance become the new rotating domains; persisted to `config/domain_schedule`.
- Sends a Telegram notification with the updated domain list.

### Step 2 — Video Generation (`generate-video.yml` workflow dispatch)

`lead_researcher.run()` dispatches `generate-video.yml` with a JSON payload via the GitHub
Actions API. The workflow runs `scripts/run_generate_video.py` → `generator_agent.run()`.

### Step 3 — News Bot Commands (via `/webhook/telegram` on Vercel)

All bot commands are handled by `whatsapp_agent.handle_reply()`:

| Command | Behavior |
|---|---|
| `STATS` | Live YouTube channel stats + pipeline queue summary |
| `COMMANDS` | Show all available bot commands |
| `CREATE <topic>` | Search for recent source article (last 72h), then dispatch video generation |
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

### Step 1 — Story Idea Generation (`stories-run.yml`, 7am, 11am, 2pm, 6pm IST)

Triggered by GitHub Actions schedule → `scripts/run_stories.py` → `story_researcher.run()`:

1. **Check pipeline state** — skip if already `processing`.
2. **Genre rotation**: deterministic scheduler-slot rotation across 12 genres (inspiring, comedy,
   heartfelt, crime, action, sci-fi, mythology, thriller, mystery, adventure, slice-of-life,
   historical), aligned to IST run slots (`7am, 11am, 2pm, 6pm`).
3. **Generate story idea** via LLM: title + premise in Hindi for the selected genre.
   Recent 30-day titles are passed to the LLM to avoid repeating topics.
4. **Dedup** against Firestore `suggested_headlines` (30-day window, `channel_id="stories"`).
5. **Save batch** + set pipeline state to `processing`.
6. **Dispatch `generate-video.yml`** with `force_run=True`.
7. **Notify** the Short Tales Telegram channel.

### Step 2 — Story Video Generation (`generate-video.yml` workflow dispatch)

`story_researcher.run()` dispatches `generate-video.yml` with `script_type="story"`,
`channel_id="stories"`, `force_run=True`.

### Step 3 — Stories Bot Commands (via `/webhook/telegram/stories` on Vercel)

`stories_agent.handle_reply()` delegates to `whatsapp_agent.handle_reply(channel_id="stories")`.
All the same commands apply (STATS, CREATE, REDO, RESEND, STOP, PRIVATE, DELETE, FORCE_CREATE).

Stories CREATE does NOT require a source article — stories are LLM-generated fiction/fables.

---

## Video Generation Pipeline (`generator_agent.run()`)

This is the core pipeline shared by both channels. Invoked from `scripts/run_generate_video.py`
inside the `generate-video.yml` GitHub Actions workflow. Every video goes through these stages:

### Guards (checked first, before any work)

1. **Idempotency guard**: If `job_id` already exists in Firestore with status
   `completed`, `delivered_manual`, or `cancelled` → return immediately.
2. **Video lock**: Distributed lock in Firestore `locks/video_generation`. `force_run=True` bypasses via
   `acquire_video_lock(force=True)`. Without force, if lock is held → `rejected_busy`.
3. **Pipeline state check**: If pipeline is `processing` for a different batch → `stale_rejected`.
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
3. **Checkpoint**: scene progress saved to Firestore `scene_progress`. If the workflow is
   re-run for the same job, completed scenes are skipped (resume from where it left off).

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

**Imagen config (as of April 2026):**
- `safety_filter_level="block_few"` and `person_generation="allow_adult"` are set explicitly to
  reduce false-positive safety rejections.

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

## GitHub Actions Workflows

All scheduled tasks and video generation run as GitHub Actions workflows. Secrets are stored
in the repository's GitHub Secrets (Settings → Secrets and variables → Actions).

| Workflow | File | Schedule (IST) | Script | Purpose |
|---|---|---|---|---|
| CI - Tests | `ci.yml` | On push/PR | `pytest -q` | Runs all tests |
| Research Run | `research-run.yml` | `12am, 8am, 4pm` | `run_research.py` | News pipeline trigger |
| Stories Run | `stories-run.yml` | `7am, 11am, 2pm, 6pm` | `run_stories.py` | Stories pipeline trigger |
| Generate Video | `generate-video.yml` | workflow_dispatch only | `run_generate_video.py` | Full video generation |
| Daily Digest | `daily-digest.yml` | `8am` | `run_daily_digest.py` | News channel daily report |
| Stories Daily Digest | `stories-daily-digest.yml` | `8:30am` | `run_stories_digest.py` | Short Tales daily report |
| Update Analytics | `update-analytics.yml` | `10pm` | `run_update_analytics.py` | Fortnightly domain schedule update |
| Refresh YouTube Auth | `refresh-youtube-auth.yml` | Every 6h | `run_refresh_auth.py` | Proactive OAuth token refresh |

**Video generation dispatch**: `github_dispatch.dispatch_video_generation(payload)` calls the
GitHub Actions API to trigger `generate-video.yml` with a JSON payload. Uses
`GITHUB_DISPATCH_TOKEN` env var (or falls back to `GITHUB_TOKEN` inside GitHub Actions runners).

**`generate-video.yml` concurrency**: `concurrency: group: video-generation` — only one video
generation runs at a time. New dispatches queue (not cancelled) while one is running.

**Required GitHub Secrets:**

| Secret | Purpose |
|---|---|
| `GCP_SERVICE_ACCOUNT_JSON` | Full JSON of the GCP service account key |
| `GNEWS_API_KEY` | GNews.io API key |
| `GOOGLE_SEARCH_API_KEY` | Google Custom Search API key |
| `GOOGLE_SEARCH_ENGINE_ID` | Programmable Search Engine ID |
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
| `BUCKET_NAME` | GCS bucket name (`yt-gen-app-bucket`) |
| `GITHUB_REPO` | Repository slug (`owner/repo`) — used by dispatcher |
| `GITHUB_DISPATCH_TOKEN` | PAT or token with `actions: write` scope — used by Vercel to dispatch workflows |

---

## Vercel Deployment

Vercel hosts the webhook receivers and admin dashboard. Serverless functions live in `api/`.

**Routing** (defined in `vercel.json`):

| Source | Destination | Purpose |
|---|---|---|
| `/webhook/telegram` | `/api/webhook/telegram` | News bot messages |
| `/webhook/telegram/stories` | `/api/webhook/stories` | Stories bot messages |
| `/admin/metrics/*` | `/api/admin/metrics/*` | Admin dashboard API |
| `/admin` | `/admin.html` | Static admin dashboard |

**Credentials in Vercel**: Vercel functions read `GCP_SERVICE_ACCOUNT_JSON` env var and write it
to `/tmp/gcp_key.json` via `api/_shared.setup_credentials()`. Set this in Vercel project settings.

**`GITHUB_DISPATCH_TOKEN`** must also be set in Vercel env vars so webhook handlers can dispatch
video generation workflows when CREATE commands arrive.

**After any Vercel redeploy, verify both webhooks:**
```bash
curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
curl -s "https://api.telegram.org/bot<STORIES_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
# last_error_message must be absent or stale. pending_update_count should be ~0.
```

---

## Telegram Webhooks

Two separate webhook URLs, one per bot:

| Channel | Webhook URL | Bot Token Env Var | Chat ID Env Var |
|---|---|---|---|
| News (Kurrent Affairs) | `.../webhook/telegram` | `TELEGRAM_BOT_TOKEN` | `TELEGRAM_CHAT_ID` |
| Stories (Short Tales) | `.../webhook/telegram/stories` | `STORIES_BOT_TOKEN` | `STORIES_CHAT_ID` |

Authentication is by matching `chat.id` against the configured chat ID env var — no HMAC secret.
Webhooks are served by Vercel serverless functions.

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
- **Always 9:16** for the pipeline (Shorts format).
- **Style**: flat design, animated explainer, consistent color palette.
- **Negative prompt**: blocks real faces, celebrity likenesses, copyright characters, brand logos.
- **Safety settings**: `safety_filter_level="block_few"`, `person_generation="allow_adult"` —
  set explicitly to reduce false-positive content rejections.
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
| `locks` | `"video_generation"` | Distributed generation lock |
| `domain_posts` | `"YYYY-MM-DD"` | Domains posted today (resets daily) |
| `suggested_headlines` | `sha1(headline)` | 14-day news / 30-day story dedup |
| `config` | `"domain_schedule"` | Rotating domain list + `last_updated` date (fortnightly) |

**Critical job fields** (used by REDO, RESEND, analytics):

| Field | Set by | Used by |
|---|---|---|
| `reviewed_title` | `generator_agent` after script review | REDO (ensures same title on re-upload) |
| `final_caption` | `social_media_agent.post()` before upload | REDO, RESEND |
| `gcs_video_url` | `generator_agent` after GCS upload | REDO, RESEND |
| `youtube_url` | `social_media_agent.post()` on success | REDO, PRIVATE, DELETE, analytics |
| `scene_progress` | `generator_agent` per scene | Workflow re-run resume |

---

## Idempotency and Deduplication

Three layers prevent duplicate videos:

1. **GitHub Actions concurrency group** (`video-generation`): only one `generate-video.yml` run
   at a time. New dispatches queue behind the active run.
2. **Job status idempotency guard** (top of `generator_agent.run()`): if job already in terminal
   state (`completed`, `delivered_manual`, `cancelled`) → skip immediately.
3. **CREATE topic idempotency** (`idempotency_keys` collection): SHA-1 of the normalized topic
   string, TTL 20 minutes. Prevents duplicate CREATE commands for the same topic.
4. **Webhook update dedup** (`idempotency_keys` collection, scope `tg_update_news` /
   `tg_update_stories`): Firestore-backed, 5-minute TTL. Prevents duplicate Telegram updates.

---

## YouTube OAuth Tokens

Two separate OAuth credential sets, one per channel:

| Channel | Firestore doc | Client ID env var | Redirect URI env var | Auth URL |
|---|---|---|---|---|
| News | `credentials/youtube` | `YOUTUBE_CLIENT_ID` | `YOUTUBE_REDIRECT_URI` | `/auth/youtube` |
| Stories | `credentials/youtube_stories` | `STORIES_YOUTUBE_CLIENT_ID` | `STORIES_YOUTUBE_REDIRECT_URI` | `/auth/youtube/stories` |

Tokens auto-refresh via `google-auth` on each API call. The `refresh-youtube-auth.yml` GitHub
Actions workflow also runs every 6 hours to proactively refresh tokens before they expire.

If "insufficient authentication scopes" appears in STATS: run the FastAPI app locally and
open the auth URL in a browser to re-authenticate.

---

## API Quotas & Free Tier Limits

### GNews API
- **Free tier:** 100 requests/day
- **App usage:** 1 domain/run × 3 runs/day = **3 calls/day (3%)** — single-domain scheduling.
  Fallback domains add up to ~5 extra calls/day worst case.
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

### GitHub Actions
- **Free tier (public repo):** unlimited minutes
- **Private repo:** 2,000 minutes/month free; video generation workflow uses ~15 min/run.
  At 12 videos/day: ~5,400 min/month — watch for overages on private repos.

---

## GCS Video Storage

- **Bucket:** `yt-gen-app-bucket`
- **Path prefix:** `videos/`
- **Retention:** 7 days (`TMP_RETENTION_DAYS` env var)
- Videos are uploaded to GCS immediately after generation, **before** YouTube upload attempt.
  REDO/RESEND work even if YouTube upload failed.
- GCS URL stored in Firestore `jobs` document as `gcs_video_url`.
- **After 7 days**, the GCS file is deleted. REDO on an old job falls back to full regeneration.

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
| `locks` | Distributed generation lock (`video_generation` doc) | If stuck: delete `locks/video_generation` to unblock |
| `domain_posts` | Tracks which domains posted today | Resets daily; safe to delete if domain coverage is wrong |
| `suggested_headlines` | 14-day news / 30-day story dedup | Safe to delete — just enables re-use of same topic |
| `config` | `domain_schedule` doc: rotating domains + last_updated | Safe to delete — defaults to Technology/Current Affairs/Science |

---

## Pipeline State & Concurrency

Two mechanisms enforce one video at a time **per channel**:

1. **Firestore `pipeline_state`** — keyed by `channel_id` (`"news"` / `"stories"`). Tracks
   active batch and state: `processing` / `completed` / `failed` / `skipped`.
2. **Firestore `locks/video_generation`** — single global distributed lock (shared across both channels).
   Acquired in `generator_agent.run()`, released in the `finally` block.
3. **GitHub Actions concurrency group** — `generate-video.yml` uses `concurrency: group: video-generation`
   so only one generation runs at a time; queued dispatches wait.

**If the pipeline gets stuck in `processing`:**
- Set `pipeline_state` doc's `state` field to `"failed"` in Firestore console, OR
- Delete the `locks/video_generation` document to release the lock.

---

## Post-Deploy Verification Checklist

Run these after any Vercel or GitHub Actions change:

1. **Both webhook health checks:**
   ```bash
   curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
   curl -s "https://api.telegram.org/bot<STORIES_BOT_TOKEN>/getWebhookInfo" | python3 -m json.tool
   # Expect: no last_error_message (or stale), pending_update_count ~0
   ```

2. **Send STATS from both Telegram bots** — confirms Vercel webhook routing is correct.

3. **Check GitHub Actions** for recent workflow failures at
   `https://github.com/<owner>/youtube-video-generation-tool/actions`.

4. **Manually trigger a workflow** via `workflow_dispatch` to verify end-to-end:
   ```bash
   gh workflow run research-run.yml
   gh workflow run stories-run.yml
   ```

---

## Common Failure Modes and Fixes

| Symptom | Cause | Fix |
|---|---|---|
| Same video uploaded 2–3 times | `generate-video.yml` dispatched twice for same job | Idempotency guard at top of `generator_agent.run()` catches this; check `concurrency: group` in workflow |
| Hindi story has English title | LLM used narration lang instruction for title too | `_TITLE_CAPTION_LANG_INSTRUCTIONS` provides explicit title language rule |
| 15-second stub video uploaded | 1/3 scenes succeeded but pipeline didn't abort | `MIN_CLIPS = 2` guard prevents upload below threshold |
| Videos keep failing with quota errors | Imagen QPM exhausted | Inner 30/60/120s retry + outer 120s delay. Check quota in Firestore `quota_events`. |
| Pipeline stuck in `processing` | GitHub Actions runner OOM or timeout mid-generation | Set `pipeline_state.state = "failed"` in Firestore, or delete `locks/video_generation` doc |
| REDO uploads with wrong title | Job stored raw topic, not reviewed title | `reviewed_title` persisted to Firestore immediately after script review |
| Webhook returns 4xx | Vercel function error | Check Vercel function logs in the Vercel dashboard |
| CREATE rejects valid topic | No source article found in last 72h | Provide article URL: `CREATE <topic> \| <url>` |
| Video dispatch silently fails | `GITHUB_DISPATCH_TOKEN` missing or expired | Set/refresh token in GitHub Secrets and Vercel env vars |
| YouTube token expired | Token refresh failed | Run `refresh-youtube-auth.yml` manually; if still failing, run FastAPI locally and re-auth via browser |

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current
