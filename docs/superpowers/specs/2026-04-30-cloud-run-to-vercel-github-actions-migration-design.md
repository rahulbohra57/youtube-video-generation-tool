# Cloud Run → Vercel + GitHub Actions Migration

**Date:** 2026-04-30
**Status:** Approved

---

## Context

The app currently runs as a single FastAPI service on Google Cloud Run. Cloud Run hosts everything: the admin dashboard, Telegram webhook handlers, scheduler-triggered research endpoints, and heavy video generation handlers called by Cloud Tasks. The goal is to eliminate the Cloud Run service entirely by splitting responsibilities across two purpose-built platforms:

- **Vercel** — serves the admin dashboard and acts as the always-on HTTP layer for Telegram webhooks and admin API calls
- **GitHub Actions** — runs all scheduled tasks and video generation jobs (replacing both Cloud Scheduler and Cloud Tasks)

The repo is public, so GitHub Actions minutes are unlimited and free.

---

## Architecture

### Before

```
Cloud Scheduler → Cloud Run (/research/run, /stories/run, etc.)
                         ↓ enqueues
                   Cloud Tasks → Cloud Run (/generate/task, /generate/stories-task)
Telegram → Cloud Run (/webhook/telegram, /webhook/telegram/stories)
Browser → Cloud Run (/admin, /admin/metrics/*)
```

### After

```
GitHub Actions cron → scripts/run_research.py, scripts/run_stories.py, etc.
                              ↓ workflow_dispatch
                       GitHub Actions generate-video.yml → scripts/run_generate_video.py

Telegram → Vercel /api/webhook/telegram, /api/webhook/stories
                   ↓ workflow_dispatch (for CREATE/FORCE_CREATE/REDO)
            GitHub Actions generate-video.yml

Browser → Vercel /admin (static HTML) + /api/admin/metrics/* (serverless functions)
```

---

## Vercel Layer

### Static Files (`public/`)

`app/static/admin.html` moves to `public/admin.html`. One change: the JavaScript API base path changes from `/admin/metrics/` to `/api/admin/metrics/`.

### Serverless Functions (`api/`)

Six Python serverless functions, each a thin wrapper around existing service modules:

```
api/
  admin/metrics/summary.py         → GET  /api/admin/metrics/summary
  admin/metrics/jobs.py            → GET  /api/admin/metrics/jobs
  admin/metrics/failures.py        → GET  /api/admin/metrics/failures
  admin/metrics/refresh-social.py  → POST /api/admin/metrics/refresh-social
  webhook/telegram.py              → POST /api/webhook/telegram
  webhook/stories.py               → POST /api/webhook/stories
```

Each function imports existing modules from `app/` (e.g. `firestore_service`, `youtube_service`, `whatsapp_agent`) without modification. The Vercel Python runtime discovers these via the `api/` convention.

A separate `requirements-vercel.txt` lists the lightweight subset of dependencies needed on Vercel — excluding `moviepy`, `Pillow`, `vertexai`, and other video-generation-only packages.

### Telegram Webhook Flow (Vercel)

1. Telegram POSTs update to Vercel function
2. Function calls `whatsapp_agent.handle_reply()` / `stories_agent.handle_reply()`
3. For commands that generate video (CREATE, FORCE_CREATE, REDO): `_enqueue_generate()` dispatches a GitHub Actions `workflow_dispatch` via GitHub REST API — replaces the Cloud Tasks `create_task()` call
4. Function returns 200 to Telegram (within timeout)
5. Video generation runs asynchronously in GitHub Actions

### Vercel Environment Variables

| Variable | Used by |
|---|---|
| `GCP_SERVICE_ACCOUNT_JSON` | Firestore, GCS, YouTube API |
| `TELEGRAM_BOT_TOKEN` | News webhook |
| `TELEGRAM_CHAT_ID` | News webhook |
| `STORIES_BOT_TOKEN` | Stories webhook |
| `STORIES_CHAT_ID` | Stories webhook |
| `GNEWS_API_KEY` | CREATE article search |
| `GOOGLE_SEARCH_API_KEY` | CREATE article search |
| `GOOGLE_SEARCH_ENGINE_ID` | CREATE article search |
| `YOUTUBE_CLIENT_ID` + `SECRET` + `REDIRECT_URI` | News YouTube OAuth |
| `STORIES_YOUTUBE_CLIENT_ID` + `SECRET` + `REDIRECT_URI` | Stories YouTube OAuth |
| `ADMIN_DASHBOARD_SECRET` | Admin auth |
| `GITHUB_DISPATCH_TOKEN` | Trigger generate-video workflow |
| `GITHUB_REPO` | e.g. `owner/repo` |

---

## GitHub Actions Layer

### Scheduled Workflows

Six cron workflows replace the six Cloud Scheduler jobs. Each runs a Python entry-point script directly — no HTTP involved.

| Workflow file | Cron (UTC) | IST equivalent | Script |
|---|---|---|---|
| `research-run.yml` | `30 18,2,10 * * *` | 12am, 8am, 4pm | `scripts/run_research.py` |
| `stories-run.yml` | `30 1,5,8,12 * * *` | 7am, 11am, 2pm, 6pm | `scripts/run_stories.py` |
| `daily-digest.yml` | `30 2 * * *` | 8am | `scripts/run_daily_digest.py` |
| `stories-daily-digest.yml` | `0 3 * * *` | 8:30am | `scripts/run_stories_digest.py` |
| `update-analytics.yml` | `30 16 * * *` | 10pm | `scripts/run_update_analytics.py` |
| `refresh-youtube-auth.yml` | `0 0,6,12,18 * * *` | every 6h | `scripts/run_refresh_auth.py` |

Each workflow:
1. Checks out repo
2. Sets up Python 3.10
3. Installs `requirements.txt`
4. Writes `GCP_SERVICE_ACCOUNT_JSON` secret to a temp file, sets `GOOGLE_APPLICATION_CREDENTIALS`
5. Runs the entry-point script with all required env vars from GitHub Secrets

### Video Generation Workflow (`generate-video.yml`)

Triggered only via `workflow_dispatch`. Inputs: `batch_id`, `job_id`, `channel_id`, `payload` (JSON string with full job parameters).

```yaml
concurrency:
  group: video-generation
  cancel-in-progress: false
```

This queues concurrent dispatches and runs them sequentially. The existing Firestore lock (`locks/video_generation`) remains as a safety net.

Runner: `ubuntu-latest` (7GB RAM, sufficient for moviepy + Imagen responses).

Steps:
1. Checkout repo
2. Install system packages: `ffmpeg`, `fonts-dejavu-core`, `fonts-indic`
3. Install `requirements.txt`
4. Set GCP credentials from secret
5. Run `scripts/run_generate_video.py` — calls `generator_agent.run()` with deserialized inputs

### GitHub Actions Secrets

Same set as Vercel env vars, plus all YouTube OAuth credentials for both channels.

---

## Code Changes

### Modified Files (2 files)

**`app/agents/whatsapp_agent.py`** — `_enqueue_generate()` method:
- Remove: `google.cloud.tasks_v2.CloudTasksClient` import and call
- Add: POST to `https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/generate-video.yml/dispatches` with `Authorization: token {GITHUB_DISPATCH_TOKEN}` header and inputs payload

**`app/agents/story_researcher.py`** — inline Cloud Tasks enqueue block:
- Same replacement as above

### New Files

```
api/admin/metrics/summary.py          # Vercel function — wraps admin route logic
api/admin/metrics/jobs.py             # Vercel function
api/admin/metrics/failures.py         # Vercel function
api/admin/metrics/refresh-social.py   # Vercel function
api/webhook/telegram.py               # Vercel function — calls whatsapp_agent.handle_reply()
api/webhook/stories.py                # Vercel function — calls stories_agent.handle_reply()
public/admin.html                     # Copied from app/static/admin.html, API path updated
scripts/run_research.py               # Calls lead_researcher.run()
scripts/run_stories.py                # Calls story_researcher.run()
scripts/run_daily_digest.py           # Calls lead_researcher.send_daily_digest()
scripts/run_stories_digest.py         # Calls stories equivalent digest
scripts/run_update_analytics.py       # Calls analytics update logic
scripts/run_refresh_auth.py           # Calls YouTube auth refresh
scripts/run_generate_video.py         # Calls generator_agent.run() with workflow inputs
.github/workflows/research-run.yml
.github/workflows/stories-run.yml
.github/workflows/daily-digest.yml
.github/workflows/stories-daily-digest.yml
.github/workflows/update-analytics.yml
.github/workflows/refresh-youtube-auth.yml
.github/workflows/generate-video.yml
vercel.json                           # Routing config
requirements-vercel.txt               # Lighter dependency set for Vercel
```

### Deleted Files

- `Dockerfile` — no longer deployed to Cloud Run

### Unchanged

All agent logic, service modules, Firestore schema, job lifecycle, idempotency guards, retry logic, Telegram notification flows — everything in `app/` other than the two `_enqueue_generate` call sites.

---

## Telegram Webhook Registration

After Vercel deploy, re-register both webhooks pointing to the new Vercel URLs:

```bash
curl "https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url=https://{vercel-domain}/api/webhook/telegram"
curl "https://api.telegram.org/bot{STORIES_BOT_TOKEN}/setWebhook?url=https://{vercel-domain}/api/webhook/stories"
```

---

## Verification

1. **Admin dashboard**: Open `https://{vercel-domain}/admin?secret=...` — cards load, jobs table populates, channel tab switch works
2. **Telegram bots**: Send `STATS` from both bots — confirm response arrives within a few seconds
3. **CREATE command**: Send `CREATE <topic>` — confirm GitHub Actions `generate-video` workflow appears in Actions tab and runs to completion
4. **Scheduled run**: Manually trigger `research-run.yml` from GitHub Actions UI — confirm it runs without error and a new batch appears in Firestore
5. **Video generation end-to-end**: Confirm the workflow completes, video uploads to GCS + YouTube, and Telegram notification arrives

---

## What Is Eliminated

| Resource | Status after migration |
|---|---|
| Cloud Run service `autoframe` | Deleted |
| Cloud Tasks queue `autoframe-generate` | Deleted |
| All 6 Cloud Scheduler jobs | Deleted |
| `Dockerfile` | Deleted |
| `google-cloud-tasks` dependency | Removed from `requirements.txt` |
