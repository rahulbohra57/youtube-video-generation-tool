# Design: 4-Agent Autonomous News-to-YouTube Pipeline

**Date:** 2026-04-04
**Status:** Approved

---

## Overview

Extend AUTOFRAME with a fully automated 4-agent pipeline that:
1. Fetches trending tech news every 4 hours (Lead Researcher)
2. Sends a digest to the owner via WhatsApp and waits for selection (WhatsApp Agent)
3. Generates a YouTube Shorts video for the selected headline using the existing AUTOFRAME engine (Generator Agent)
4. Optimises the caption and posts the video to YouTube, then confirms via WhatsApp (Social Media Agent)

**Decisions made:**
- WhatsApp API: Twilio
- News source: GNews API (free tier, 100 req/day)
- Deployment: GCP Cloud Run (existing project)
- Genres: Technology only for MVP — extensible later
- Scheduler: GCP Cloud Scheduler (4-hour cron)
- State storage: GCP Firestore

---

## Architecture & Data Flow

```
GCP Cloud Scheduler (every 4 hours)
    └── POST /research/run  →  Cloud Run (FastAPI)
                                   │
                              Lead Researcher Agent
                              ├── GNews API → top 10 tech headlines
                              ├── Gemini: rate & pick top 5
                              ├── Store batch → Firestore news_batches/{batch_id}
                              └── WhatsApp Agent: send_digest(batch_id)
                                        │
                                   Twilio → User's WhatsApp

User replies "TECH01"
                                   │
                         POST /webhook/whatsapp  (Twilio webhook)
                                   │
                              WhatsApp Agent: handle_reply()
                              ├── "NONE" → mark batch skipped, end pipeline
                              ├── Valid code → lookup headline in Firestore
                              └── Generator Agent: run(headline)
                                        │
                              Generator Agent
                              ├── generate_script(headline, "en", "9:16")   [existing]
                              ├── generate_audio() per scene                [existing]
                              ├── generate_image() per scene                [existing]
                              ├── create_video()                            [existing]
                              ├── generate_shorts_caption(headline, "en")   [existing]
                              └── Social Media Agent: post(video_path, caption, title)
                                        │
                              Social Media Agent
                              ├── llm_service.enhance_caption(caption)      [new prompt]
                              ├── YouTube Data API v3: videos.insert()
                              └── WhatsApp Agent: send_post_result(title, url)
                                        │
                                   Twilio → User's WhatsApp
```

---

## Firestore Schema

### Collection: `news_batches`

Document ID: `batch_YYYYMMDD_HHMMSS`

```json
{
  "created_at": "2026-04-04T10:00:00Z",
  "genre": "technology",
  "status": "awaiting_reply",
  "items": {
    "TECH01": {
      "headline": "OpenAI releases GPT-5 with real-time vision",
      "context": "OpenAI launched GPT-5 today with live camera input support. Early benchmarks show 40% improvement over GPT-4o on reasoning tasks.",
      "rating": 4.8,
      "url": "https://..."
    },
    "TECH02": { ... },
    "TECH03": { ... },
    "TECH04": { ... },
    "TECH05": { ... }
  }
}
```

`status` values: `awaiting_reply` | `processing` | `completed` | `skipped`

### Collection: `pipeline_state`

Document ID: `current` (singleton)

```json
{
  "active_batch_id": "batch_20260404_100000",
  "last_run_at": "2026-04-04T10:00:00Z",
  "state": "awaiting_reply"
}
```

### Collection: `oauth_tokens`

Document ID: `youtube` (singleton — written once during OAuth setup)

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_expiry": "2026-04-04T11:00:00Z",
  "client_id": "...",
  "client_secret": "..."
}
```

---

## New Files

```
app/
├── agents/
│   ├── __init__.py
│   ├── lead_researcher.py      # GNews fetch + Gemini rating/selection
│   ├── whatsapp_agent.py       # Twilio send + reply handler + formatter
│   ├── generator_agent.py      # Orchestrates existing video+caption services
│   └── social_media_agent.py  # Caption enhancement + YouTube upload
├── routes/
│   ├── generate.py             # EXISTING — unchanged
│   ├── research.py             # NEW: POST /research/run
│   ├── webhook.py              # NEW: POST /webhook/whatsapp
│   └── auth.py                 # NEW: GET /auth/youtube + /auth/youtube/callback
├── services/
│   ├── llm_service.py          # EXISTING — add enhance_caption() function
│   ├── firestore_service.py    # NEW: read/write Firestore collections
│   ├── gnews_service.py        # NEW: GNews API HTTP client
│   ├── youtube_service.py      # NEW: YouTube Data API v3 upload
│   └── twilio_service.py       # NEW: Twilio WhatsApp send wrapper
```

---

## New Dependencies

Add to `requirements.txt`:
```
twilio
google-cloud-firestore
google-api-python-client
google-auth-oauthlib
httpx
```

---

## New Environment Variables

Add to `.env`:
```
# GNews
GNEWS_API_KEY=

# Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # Twilio sandbox number
OWNER_WHATSAPP_TO=whatsapp:+91XXXXXXXXXX     # Your WhatsApp number

# YouTube OAuth (populated by /auth/youtube flow)
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=https://<cloud-run-url>/auth/youtube/callback

# Cloud Scheduler auth
SCHEDULER_SECRET=<random-string>             # Simple shared secret to protect /research/run
```

---

## Agent Specifications

### Lead Researcher Agent (`app/agents/lead_researcher.py`)

**Entry point:** `run() -> str` — returns `batch_id`

1. Call `gnews_service.fetch_top_headlines(category="technology", max=10)`
2. Build Gemini prompt:
   ```
   You are a news editor. Rate each headline 1–5 for virality and public interest.
   Select the top 5. Return ONLY a JSON array:
   [{"code": "TECH01", "headline": "...", "context": "2-sentence summary", "rating": 4.5}, ...]
   ```
3. Parse response, generate `batch_id = f"batch_{datetime.utcnow():%Y%m%d_%H%M%S}"`
4. Write to Firestore `news_batches/{batch_id}`
5. Update `pipeline_state/current` → `{ active_batch_id, state: "awaiting_reply" }`
6. Call `whatsapp_agent.send_digest(batch_id)`
7. Return `batch_id`

---

### WhatsApp Agent (`app/agents/whatsapp_agent.py`)

**`send_digest(batch_id: str)`**
- Reads batch from Firestore
- Formats one message per item using spec format (Unique Number / Genre / Headline / Context / Rating)
- Appends footer: *"Reply with a code (e.g. TECH01) to generate a video, or reply None to skip."*
- Sends via `twilio_service.send_whatsapp(OWNER_WHATSAPP_TO, message)`

**`handle_reply(from_number: str, body: str)`**
- Strips and uppercases `body`
- `"NONE"` → `firestore_service.update_batch_status(active_batch_id, "skipped")`, send "Got it! See you in the next digest." 
- Matches `r'^TECH0[1-5]$'` → lookup item in Firestore, call `generator_agent.run(headline, code)` in background thread
- Else → send "Invalid code. Please reply with TECH01–TECH05 or None."

**`send_post_result(title: str, url: str)`**
- Sends formatted WhatsApp message:
  ```
  ✅ Posted to YouTube!
  *Post Title:* <title>
  *Post Link:* <url>
  ```

---

### Generator Agent (`app/agents/generator_agent.py`)

**Entry point:** `run(headline: str, code: str)`

1. Update batch status → `"processing"`
2. Call `generate_script(headline, language="en", aspect_ratio="9:16")` [existing `llm_service`]
3. Per scene: `generate_audio()` + `generate_image()` [existing services]
4. Call `create_video(clips, output_path)` [existing `video_service`]
5. Call `generate_shorts_caption(headline, language="en")` [existing `llm_service`]
6. Call `social_media_agent.post(video_path, caption, title=headline)`

Note: uses a timestamped output path `tmp/final_{code}_{timestamp}.mp4` to avoid collision.

---

### Social Media Agent (`app/agents/social_media_agent.py`)

**Entry point:** `post(video_path: str, caption: str, title: str)`

1. Call `llm_service.enhance_caption(caption)` — new prompt:
   ```
   Improve this YouTube Shorts caption. Add a strong hook as the first line.
   Add an engaging closing line asking viewers to like and subscribe.
   Keep hashtags. Return plain text only.
   Caption: {caption}
   ```
2. Load OAuth tokens from Firestore `oauth_tokens/youtube` via `youtube_service.get_credentials()`
3. Call `youtube_service.upload_video(video_path, title, enhanced_caption)`
4. Returns YouTube video URL
5. Update batch status → `"completed"`
6. Call `whatsapp_agent.send_post_result(title, url)`

---

### YouTube OAuth Setup (`app/routes/auth.py`)

One-time manual flow:

- `GET /auth/youtube` — generates Google OAuth URL, redirects user's browser
- `GET /auth/youtube/callback?code=...` — exchanges code for tokens, stores in Firestore `oauth_tokens/youtube`, returns "Auth complete."

After this one-time step, all uploads auto-refresh via `google-auth-oauthlib`.

---

### Webhook Handler (`app/routes/webhook.py`)

`POST /webhook/whatsapp` — receives Twilio webhook

- Parses `request.form` for `Body` and `From` fields (Twilio sends `application/x-www-form-urlencoded`)
- Immediately returns Twilio-compatible empty TwiML response (`<Response/>`) — must be within 15 seconds
- Dispatches `whatsapp_agent.handle_reply()` in a `BackgroundTasks` thread

---

### Research Trigger (`app/routes/research.py`)

`POST /research/run` — Cloud Scheduler target

- Validates `X-Scheduler-Secret` header against `SCHEDULER_SECRET` env var
- Calls `lead_researcher.run()`
- Returns `{ "batch_id": "..." }`

---

## GCP Cloud Scheduler Configuration

```
Job name:       autoframe-lead-researcher
Target type:    HTTP
URL:            https://<cloud-run-url>/research/run
HTTP method:    POST
Headers:        X-Scheduler-Secret: <SCHEDULER_SECRET>
Schedule:       0 */4 * * *   (every 4 hours)
Time zone:      Asia/Kolkata
```

---

## Cloud Run Configuration Changes

- Set `--min-instances=1` to keep one instance warm for Twilio webhook responsiveness
- Add all new env vars to Cloud Run service (or use Secret Manager)

---

## Key Challenges & Mitigations

| Challenge | Detail | Mitigation |
|---|---|---|
| Twilio 15s webhook timeout | Video generation takes 60–120s | Respond immediately with empty TwiML, run pipeline in `BackgroundTasks` |
| YouTube OAuth on Cloud Run | Browser redirect needs a public HTTPS URL | Use Cloud Run URL as redirect URI; run auth flow once after deploy |
| Cloud Run spin-down | Twilio webhook may hit a cold instance | `min-instances=1` keeps one warm |
| GNews free tier 100 req/day | 4h interval = 6 req/day | Well within limit; add rate-limit guard just in case |
| Concurrent pipeline runs | User replies while previous run is processing | Check `pipeline_state.state == "processing"` before starting new run; send "Processing in progress" |
| YouTube upload quota | 10,000 units/day (upload = 1,600 units) | ~6 uploads/day max — sufficient for this use case |
| Video filename collision | Multiple runs overwrite `tmp/final.mp4` | Use `tmp/final_{code}_{timestamp}.mp4` |

---

## Files Modified (Existing)

| File | Change |
|---|---|
| `app/main.py` | Register 3 new routers: `research`, `webhook`, `auth` |
| `app/services/llm_service.py` | Add `enhance_caption(caption: str) -> str` function |
| `app/config.py` | Add new env var constants |
| `requirements.txt` | Add 5 new dependencies |

---

## Verification Steps

1. **Unit test Lead Researcher** — mock GNews + Gemini, verify Firestore write and WhatsApp call
2. **Unit test WhatsApp reply handler** — send "TECH01", "none", "invalid", verify correct branch each time
3. **Integration test webhook** — POST to `/webhook/whatsapp` with Twilio form data, verify TwiML `<Response/>` returned in < 1s
4. **End-to-end dry run** — POST to `/research/run` manually, watch Firestore, reply via WhatsApp, confirm video generates and posts
5. **YouTube OAuth** — run `/auth/youtube` flow once, verify tokens stored in Firestore, run a test upload
6. **Cloud Scheduler** — trigger manually from GCP console, verify full pipeline runs
