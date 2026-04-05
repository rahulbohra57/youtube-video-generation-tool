# CLAUDE.md — Autoframe YouTube Video Generation Tool

Critical operational constraints, deployment rules, and system gotchas. Read this before
making any infrastructure or code change.

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

## CRITICAL: Cloud Run Deployment Rules

### 1. ALWAYS deploy with `--allow-unauthenticated`

```bash
gcloud run deploy autoframe \
  --source . \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --allow-unauthenticated
```

**Why:** The Telegram webhook (`/webhook/telegram`) is called directly by Telegram's servers.
Telegram has no way to present GCP auth credentials. Using `--no-allow-unauthenticated` blocks
all Telegram traffic → webhook returns 403 Forbidden → STATS, CREATE, REDO, RESEND, and all
bot commands silently stop working.

**How to verify the webhook is healthy after any deploy:**
```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo" | python3 -m json.tool
# last_error_message must be absent or stale. pending_update_count should be 0.
```

### 2. Never change these Cloud Run flags without understanding the impact

| Flag | Current value | Why it must stay this way |
|---|---|---|
| `--min-instances=1` | 1 | The in-memory Telegram update dedup set (`_seen_update_ids` in `webhook.py`) lives in RAM. If min=0, cold starts lose dedup history and the bot may double-process commands. |
| `--cpu-throttling=false` | off | Video encoding (moviepy) and image generation are CPU-heavy. CPU throttling causes `ffmpeg` to time out mid-render. |
| `--startup-cpu-boost` | on | Reduces cold-start latency. |
| `--memory=4Gi` | 4 GiB | moviepy/PIL load full video frames into memory. Less than 4 GiB causes OOM kills mid-generation. |
| `--cpu=4` | 4 vCPU | Matches the memory/CPU ratio for video processing. |
| `--timeout=3600` | 3600 s | Video generation + YouTube upload can take 10–15 minutes. Default 300s timeout kills running jobs. |

### 3. Standard deploy command (copy-paste safe)

```bash
cd "/Users/chetan/Desktop/Data Science/youtube-video-generation-tool"
gcloud run deploy autoframe \
  --source . \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --allow-unauthenticated
```

`--source .` uses the Dockerfile in the repo root. It builds via Cloud Build, pushes to
`gcr.io/youtube-video-generator-492211/autoframe`, then deploys.

---

## Cloud Scheduler Jobs

Four jobs exist. Only 3 are on the free tier; **1 is a paid job ($0.10/month).**

| Job ID | Schedule (Asia/Kolkata) | Endpoint | Notes |
|---|---|---|---|
| `autoframe-lead-researcher` | `0 */2 * * *` (every 2h) | `/research/run` | Free tier job |
| `autoframe-retry-failed` | `0 */4 * * *` (every 4h) | `/research/retry-failed` | Free tier job |
| `autoframe-daily-digest` | `0 8 * * *` (8am IST) | `/research/daily-digest` | Free tier job |
| `autoframe-update-analytics` | `0 22 * * *` (10pm IST) | `/research/update-analytics` | **Paid job** |

**All scheduler endpoints require the `X-Scheduler-Secret` header.** The secret is stored as a
Cloud Run env var (`SCHEDULER_SECRET`) and in the scheduler job's HTTP header config. If you
recreate a scheduler job, you must re-add this header.

**Never change the research schedule to less than 2 hours.** It is deliberately set to every 2h to
avoid content spam and GNews quota exhaustion (free tier: 100 calls/day; 12 cycles/day × 5 calls
= 60 calls). A 1-hour schedule would double calls to 120/day, exceeding the free tier.

To update a scheduler job:
```bash
gcloud scheduler jobs update http autoframe-update-analytics \
  --project=youtube-video-generator-492211 \
  --location=us-central1 \
  --schedule="0 22 * * *" \
  --time-zone="Asia/Kolkata"
```

---

## Telegram Webhook

- **Webhook URL:** `https://autoframe-353645494126.us-central1.run.app/webhook/telegram`
- **Registered with:** `api.telegram.org/bot<TOKEN>/setWebhook`
- **Authentication:** The webhook authenticates by matching `chat.id` against `TELEGRAM_CHAT_ID`
  env var. There is no HMAC secret — **the Cloud Run service must be publicly accessible.**
- **Pending updates:** If the webhook errors for a period, Telegram queues up to 100 updates.
  They auto-deliver once the 403/500 is resolved. No manual action needed.

After any deploy, always check:
```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```
`last_error_message` should be absent. If it shows `403 Forbidden`, the service is not public —
run the IAM fix:
```bash
gcloud run services add-iam-policy-binding autoframe \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --member="allUsers" \
  --role="roles/run.invoker"
```

---

## Pipeline State & Concurrency

The app enforces **one video at a time** using two mechanisms:

1. **Firestore `pipeline_state` document** — tracks the active batch and its state
   (`processing` / `completed` / `failed` / `skipped`). If this is stuck in `processing`,
   all new auto-generated and user-triggered videos will be rejected.

2. **Firestore `video_lock` document** — a distributed lock acquired by `generator_agent.run()`
   and released in the `finally` block.

**If the pipeline gets stuck in `processing`:**
- This happens when a Cloud Run instance crashes mid-generation (OOM, timeout, etc.)
- The lock is released on next GCS/Firestore TTL or manual reset
- Fix: set `pipeline_state.state = "failed"` in Firestore console, or trigger
  `/research/retry-failed` which handles stale state automatically

**Never manually delete Firestore documents** in `pipeline_state`, `news_batches`, or `jobs`
collections while a video is being generated — it will orphan the Cloud Tasks task and cause
infinite retries.

---

## YouTube OAuth Tokens

- Stored in Firestore (collection: `credentials`, document: `youtube`)
- Tokens auto-refresh via `google-auth` library on each API call
- If you see "insufficient authentication scopes" in STATS output: the token was issued before
  the current scope list was set. Re-authenticate:
  ```
  Open in browser: https://autoframe-353645494126.us-central1.run.app/auth/youtube
  ```
- The OAuth redirect URI is hardcoded in the GCP OAuth client as:
  `https://autoframe-353645494126.us-central1.run.app/auth/youtube/callback`
  **If the Cloud Run URL ever changes, you must update this in GCP Console → APIs & Services →
  Credentials, or the OAuth flow will fail with `redirect_uri_mismatch`.**

---

## API Quotas & Free Tier Limits

### GNews API
- **Free tier:** 100 requests/day
- **App usage at 12 videos/day:** 60 calls/day (60%)
- **Circuit-breaker:** fires at 80 calls/day (returns `[]` without making HTTP call)
- Quota tracked in Firestore `quota_events` collection (`kind == "gnews_call"`)
- **Never remove the circuit-breaker** in `gnews_service.py` — without it, 13+ scheduler cycles
  will exhaust the quota and break news research for the rest of the day

### Cloud TTS (Neural2)
- **Free tier:** 1,000,000 chars/month
- **App usage at 12 videos/day:** ~864,000 chars/month (86%)
- **Buffer:** ~136,000 chars/month (~4 videos/day headroom)
- **Hindi voices:** Must use `hi-IN-Neural2-*` or `hi-IN-Wavenet-*` only
- **Never use `hi-IN-Chirp3-HD-*` voices** — they cost $160/1M chars with NO free tier.
  At 12 videos/day, a single day with Hindi Chirp3-HD could cost ~$4.60. They were removed
  in April 2026 and must not be reintroduced.

### Vertex AI Imagen 3
- **Price:** $0.04/image (no free tier)
- **QPM limit:** 20 queries/minute
- **Retry logic:** 30s → 60s → 120s backoff (3 attempts max) in `generator_agent.py`
- **Images per video:** 3 scene images only (no thumbnail) = $0.12/video
- **Monthly cost at 12/day:** $43.20 — the dominant cost (~90% of total spend)
- Never increase `MAX_SCENES` above 3 without reviewing Imagen quota and cost impact

### YouTube Data API v3
- **Free quota:** 10,000 units/day
- **Usage at 12 videos/day:** ~2,413 units/day (24%) — well within limits
- **Upload cost (April 2026 revision):** ~100 units per `videos.insert` (was ~1,600)
- **Fallback:** If YouTube upload fails for any reason, the video + caption are sent to
  Telegram (`delivered_manual` status). These jobs are **excluded from the retry queue**
  to avoid burning Imagen/Gemini credits on videos that can't upload.

### Cloud Run Compute
- **Free tier:** 360,000 vCPU-seconds/month + 180,000 GiB-seconds/month
- **At 12 videos/day (360/month):** ~432K vCPU-s + ~864K GiB-s → exceeds free tier
- **Monthly overage:** ~$3.44
- Free tier is exhausted above 300 videos/month (~10/day)

---

## Environment Variables

All env vars are set directly on the Cloud Run service (not via `.env` file — there is none in
production). To view or update:
```bash
gcloud run services describe autoframe \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --format="yaml(spec.template.spec.containers[0].env)"
```

| Variable | Purpose |
|---|---|
| `GNEWS_API_KEY` | GNews.io API key |
| `GOOGLE_SEARCH_API_KEY` | Google Custom Search API key (CREATE/FORCE_CREATE enrichment; 100 free/day on GCP billing) |
| `GOOGLE_SEARCH_ENGINE_ID` | Programmable Search Engine ID — must have "Search the entire web" enabled |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID that the bot responds to |
| `YOUTUBE_CLIENT_ID` | YouTube OAuth 2.0 client ID |
| `YOUTUBE_CLIENT_SECRET` | YouTube OAuth 2.0 client secret |
| `YOUTUBE_REDIRECT_URI` | Must match exactly what's registered in GCP OAuth client |
| `SCHEDULER_SECRET` | Shared secret for scheduler endpoint authentication |
| `CLOUD_RUN_URL` | Base URL of this service (used when enqueueing Cloud Tasks) |
| `TASKS_QUEUE` | Cloud Tasks queue name (`autoframe-generate`) |

**If you add a new env var**, update it on the Cloud Run service via:
```bash
gcloud run services update autoframe \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --set-env-vars="NEW_VAR=value"
```
Then redeploy. Env vars set via `update` persist across `--source .` deploys.

---

## Firestore Collections

| Collection | Purpose | Risk if corrupted |
|---|---|---|
| `pipeline_state` | Single document tracking active batch + state | Stuck in `processing` blocks all new videos |
| `jobs` | One doc per video generation job | Never delete — used for REDO/RESEND/analytics |
| `news_batches` | Research batches with article candidates | Deleting active batch orphans the pipeline |
| `credentials` | YouTube OAuth tokens | Deletion requires full re-auth via `/auth/youtube` |
| `quota_events` | GNews + TTS + image quota tracking | Used by circuit-breakers and daily digest |
| `social_metrics` | Cached YouTube channel stats | Safe to delete — re-fetched on next STATS |
| `idempotency_keys` | Dedup for CREATE command | Safe to delete — just allows re-submission |
| `video_lock` | Distributed generation lock | If stuck: delete the document to unblock |
| `domain_posts` | Tracks which domains posted today | Resets daily; safe to delete if domain coverage is wrong |

---

## Cloud Tasks

- **Queue:** `autoframe-generate` in `us-central1`
- **Task naming:** Deterministic — `generate-<batch_id>-<code>`. Prevents duplicate tasks for
  the same video. Cloud Tasks returns `AlreadyExists` for duplicates (handled gracefully).
- **OIDC auth:** Tasks are dispatched with the default compute service account OIDC token.
  The Cloud Run service must have `roles/run.invoker` for `allUsers` (public) — same IAM
  setting required for Telegram webhook.
- **Retry policy:** Set at queue level, not per-task. The generator returns HTTP 200 even on
  soft failures to prevent Cloud Tasks from retrying a video that legitimately failed.

---

## Telegram Bot Commands Reference

| Command | Behavior |
|---|---|
| `COMMANDS` | List all available bot commands with descriptions |
| `STATS` | Channel stats + pipeline queue summary |
| `CREATE <topic>` | Generate video for custom topic → try YouTube upload → Telegram fallback on failure |
| `CREATE <topic> \| <context>` | Same, with extra context for script generation |
| `FORCE_CREATE <topic>` | Same as CREATE but bypasses pipeline busy check and dedup |
| `REDO <id>` | If GCS video exists: try YouTube re-upload → Telegram fallback. If not: full regeneration |
| `RESEND <id>` | Send existing GCS video + caption to Telegram for manual posting. Never uses YouTube API |
| `STOP <id>` | Request cancellation of a queued/processing job |
| `PRIVATE <id>` | Set a YouTube video to private |
| `DELETE <id>` | Delete a video from YouTube |

`<id>` is the 8-character public video ID shown in Telegram notifications (e.g. `2E95C55E`).

---

## GCS Video Storage

- **Bucket:** `yt-gen-app-bucket`
- **Path prefix:** `videos/`
- **Retention:** 7 days (`TMP_RETENTION_DAYS` env var)
- **Used by:** REDO (re-upload) and RESEND (manual delivery)
- Videos are uploaded to GCS immediately after generation, **before** YouTube upload attempt.
  This means REDO/RESEND work even if the YouTube upload failed.
- GCS URL is stored in Firestore `jobs` document as `gcs_video_url`.
- **After 7 days**, the GCS file is deleted. REDO on an old job falls back to full regeneration.

---

## Post-Deploy Verification Checklist

Run these after every deploy:

1. **Webhook health:**
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo" | python3 -m json.tool
   # Expect: no last_error_message (or an old stale one), pending_update_count ~0
   ```

2. **Service reachable:**
   ```bash
   curl -o /dev/null -w "%{http_code}" -X POST \
     https://autoframe-353645494126.us-central1.run.app/webhook/telegram \
     -H "Content-Type: application/json" \
     -d '{"update_id":0,"message":{"chat":{"id":0},"text":"test"}}'
   # Expect: 200
   ```

3. **Send STATS from Telegram** — confirms bot commands are routing correctly.

4. **Check Cloud Run logs** for startup errors:
   ```bash
   gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=autoframe" \
     --project=youtube-video-generator-492211 \
     --limit=50 --format="table(timestamp,textPayload)"
   ```
