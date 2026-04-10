# Finance — YouTube Video Generation Tool

> **Assumptions**
> - Exchange rate: **$1 USD ≈ ₹85**
> - Throughput: **12 videos/day** (combined automated + manual runs)
> - News scheduler runs every 4 hours → 6 research cycles/day
> - Each video: 3 scenes, 1 thumbnail → 4 Imagen calls; 6–7 Gemini calls; ~2,400 TTS characters
> - GCP free trial credits: **₹27,287**, expires **June 18 2026** (~74 days remaining)
> - All prices are **public list prices** as of April 2026; GCP credits apply to all GCP services

---

## 1. Models & APIs Configured

| # | Service / Model | Version / Endpoint |
|---|---|---|
| 1 | Vertex AI — Gemini | `gemini-2.5-flash` |
| 2 | Vertex AI — Imagen | `imagen-3.0-generate-002` |
| 3 | Google Cloud TTS | Neural2-D/F/H/J, Wavenet-D (EN); Neural2-A/B/C, Wavenet-A/D (HI) |
| 4 | Google Cloud Firestore | Native mode |
| 5 | Google Cloud Storage | `yt-gen-app-bucket` |
| 6 | Google Cloud Tasks | Queue: `autoframe-generate` |
| 7 | Google Cloud Run | Revision-based, us-central1 |
| 8 | Google Cloud Scheduler | 4 cron jobs |
| 9 | YouTube Data API v3 | OAuth 2.0 |
| 10 | GNews API | `gnews.io/api/v4` |
| 11 | Telegram Bot API | `api.telegram.org` |

---

## 2. Cost of Generating One Video

Every video runs through this call chain:

| Step | Service | Calls | Unit Cost | Cost per Video |
|---|---|---|---|---|
| Script generation | Gemini 2.5 Flash | 1 (~1,200 in / 2,000 out tokens) | $0.15/1M in · $0.60/1M out | ~$0.0014 |
| Fact-check pass | Gemini 2.5 Flash | 1 (~1,500 in / 500 out) | same | ~$0.0005 |
| Music genre classifier | Gemini 2.5 Flash | 1 (~400 in / 100 out) | same | ~$0.0001 |
| Script quality review | Gemini 2.5 Flash | 1 (~2,000 in / 500 out) | same | ~$0.0004 |
| Title + caption review | Gemini 2.5 Flash | 1 (~1,000 in / 300 out) | same | ~$0.0003 |
| Shorts caption | Gemini 2.5 Flash | 1 (~500 in / 200 out) | same | ~$0.0001 |
| **Gemini subtotal** | | **~6 calls / ~8,600 in / ~3,600 out tokens** | | **~$0.003** |
| Scene images (×3) | Imagen 3 | 3 | $0.04/image | $0.12 |
| **Imagen subtotal** | | **3 images** | | **$0.12** |
| Narration audio (×3 scenes) | Cloud TTS Neural2 | ~2,400 chars | $16/1M chars | ~$0.00004 |
| Video upload | YouTube API | 1 (~100 units) | Free (10K units/day quota) | $0 |
| Playlist assign | YouTube API | 1–2 (51–101 units) | Free | $0 |
| Task enqueue | Cloud Tasks | 1 | Free (1M/month free) | $0 |
| Firestore reads/writes | Firestore | ~50 ops | Free (50K reads/day free) | $0 |
| Video file storage (7 days) | Cloud Storage | ~80 MB | $0.02/GB/month | ~$0.0001 |
| Compute (10 min avg) | Cloud Run | ~1,200 vCPU-s | Free (360K vCPU-s/month free) | ~$0* |
| Telegram notifications | Telegram Bot API | 3–5 messages | Free | $0 |

**Total per video ≈ $0.123 (≈ ₹10)**

> *Cloud Run stays within free tier up to 300 videos/month (10/day). At 12 videos/day (360/month), ~$3.44/month overage applies.

---

## 3. Daily Running Cost

### At 12 videos/day

| Item | Daily volume | Daily cost |
|---|---|---|
| Imagen 3 | 36 images | $1.44 |
| Gemini 2.5 Flash (video) | 72–84 calls | $0.036 |
| Gemini 2.5 Flash (research) | 12 calls | $0.012 |
| GNews (research) | 60 calls | $0 (free tier) |
| Cloud TTS | ~28,800 chars | $0 (free tier) |
| Cloud Run | ~14,400 vCPU-s | ~$0.016* |
| YouTube API | ~2,413 units | $0 (free tier) |
| Firestore | ~600 ops | $0 (free tier) |
| Cloud Storage | +960 MB | ~$0.000 |
| Misc (Scheduler, Tasks) | — | ~$0.001 |
| **Daily total** | | **~$1.51 (≈ ₹128)** |

> *Cloud Run daily overage: (14,400 − 12,000 free/day) vCPU-s × $0.000024 ≈ $0.058/day averaged monthly.

---

## 4. Monthly Running Cost

### At 12 videos/day → 360 videos/month

| Service | Monthly usage | Free tier | Billed | Cost (USD) | Cost (INR) |
|---|---|---|---|---|---|
| Vertex AI Imagen 3 | 1,080 images | None | 1,080 images | **$43.20** | ₹3,672 |
| Vertex AI Gemini 2.5 Flash | ~2,520 video calls + 360 research calls / ~3.4M in / ~1.4M out tokens | None | all | **$1.44** | ₹122 |
| Cloud TTS (Neural2) | ~864K chars | 1M chars/month | 0 | $0 | ₹0 |
| Cloud Run | ~432K vCPU-s, ~864K GiB-s | 360K vCPU-s, 180K GiB-s | 72K vCPU-s + 684K GiB-s | **$3.44** | ₹292 |
| Cloud Storage | ~6.7 GB peak (84-video 7-day window) | 5 GB/month | ~1.7 GB | **$0.03** | ₹3 |
| Firestore | ~18K reads, 10.8K writes | 50K reads/day, 20K writes/day | 0 | $0 | ₹0 |
| Cloud Tasks | 360 tasks | 1M/month | 0 | $0 | ₹0 |
| Cloud Scheduler | 4 jobs | 3 jobs free | 1 job | **$0.10** | ₹9 |
| YouTube Data API | ~72,400 units | 10K units/day (~300K/month) | 0 | $0 | ₹0 |
| GNews API | ~1,800 calls | 100 calls/day (~3,000/month) | 0 | $0 | ₹0 |
| Telegram Bot API | ~1,800 messages | Free | 0 | $0 | ₹0 |
| **Monthly total** | | | | **~$48.21** | **≈ ₹4,098** |

---

## 5. Individual Service Cost Breakdown & Limits

### 5.1 Vertex AI — Gemini 2.5 Flash

| Metric | Value |
|---|---|
| Input price | $0.15 per 1M tokens |
| Output price | $0.60 per 1M tokens |
| Free tier | None (pay-as-you-go on Vertex AI) |
| Calls per video | 6–7 |
| Calls per research cycle | 1 (rate_and_select_news) |
| Cost per video | ~$0.003 |
| Cost per research cycle | ~$0.001 |
| Rate limits | Shared project quota; no hard cap in code |
| Monthly cost at 12 vid/day | **~$1.44** |

### 5.2 Vertex AI — Imagen 3 (`imagen-3.0-generate-002`)

> **This is the dominant cost driver — 92% of total monthly spend.**

| Metric | Value |
|---|---|
| Price | $0.04 per image |
| Free tier | None |
| Images per video | 3 (scene images only — no thumbnail) |
| Free-tier QPM limit | 20 queries/minute |
| Retry delays on 429 | 30s → 60s → 120s (3 attempts max) |
| Cost per video | **$0.12** |
| Cost per day (12 videos) | **$1.44** |
| Cost per month (12 vid/day) | **$43.20** |

> **To reduce cost:** Cap scenes at 2 instead of 3 → saves $0.04/video → **~$14.40/month**.

### 5.3 Google Cloud TTS

| Voice type | Price | Free tier |
|---|---|---|
| Standard | $4/1M chars | 4M chars/month |
| WaveNet | $16/1M chars | 1M chars/month |
| Neural2 | $16/1M chars | 1M chars/month |
| ~~Studio / Chirp3-HD (Hindi)~~ | ~~$160/1M chars~~ | ~~None~~ |

> **Chirp3-HD removed** (April 2026): Replaced with Neural2 Hindi voices (hi-IN-Neural2-A/B/C + Wavenet-A/D). All voices now within the 1M chars/month Neural2 free tier.

| Metric | Value |
|---|---|
| Chars per video | ~2,400 (3 × 800 char scenes) |
| Monthly chars at 12 vid/day | ~864,000 chars |
| Free monthly allowance (Neural2) | 1,000,000 chars |
| **Effective cost** | **$0 — within free tier (86% used)** |
| Hindi voices | Neural2-A/B/C, Wavenet-A/D — same $16/1M tier, same free allowance |

> **Warning:** At 14 videos/day (1,008,000 chars/month), the free tier would be exceeded. Current buffer: ~136K chars/month (~4 videos/day headroom).

### 5.4 YouTube Data API v3

> **Quota update (April 2026):** Google reduced `videos.insert` cost from ~1,600 units to **~100 units**.
> Source: [YouTube Data API Revision History](https://developers.google.com/youtube/v3/revision_history).

| Operation | Units | Calls per video |
|---|---|---|
| `videos.insert` (upload) | **~100** *(was ~1,600)* | 1 |
| `playlists.list` | 1 | 1 (first time only, cached) |
| `playlists.insert` | 50 | 1 (first time only) |
| `playlistItems.insert` | 50 | 1 |
| `videos.list` (analytics) | 1 | 1/day per completed video |
| `channels.list` | 1 | 1/day |
| **Per new video** | **~151 units** | |

| Metric | Value |
|---|---|
| Free daily quota | **10,000 units/day** |
| Usage at 12 videos/day | ~1,813 units/day (18% of quota) |
| Headroom | ~7,587 units/day |
| Quota exhaustion risk | None — ample headroom even if doubled |
| Cost beyond quota | $0.00015/unit |
| **Effective monthly cost** | **$0** |

> Telegram fallback is in place: if YouTube upload fails for any reason (quota or otherwise), the video + caption are sent to Telegram for manual posting and the job is marked `delivered_manual` (excluded from retry queue).

### 5.5 GNews API

| Metric | Value |
|---|---|
| Free tier | **100 requests/day** |
| App usage | 5 calls/research cycle × 6 cycles/day = **30 calls/day** |
| Free tier headroom | 70 calls/day (70% buffer) |
| Circuit-breaker | Fires at 80 calls/day (code-enforced guard) |
| Paid Basic plan | $9.99/month → 1,000 req/day |
| Paid Advanced plan | $99/month → unlimited |
| **Effective monthly cost** | **$0 (within free tier)** |

### 5.6 Google Cloud Run

| Metric | Value |
|---|---|
| Free vCPU-seconds/month | **360,000** |
| Free GiB-seconds/month | **180,000** |
| Free requests/month | **2,000,000** |
| Per-video compute | ~600 sec × 2 vCPU = 1,200 vCPU-s; × 4 GiB = 2,400 GiB-s |
| Monthly usage at 12/day | 432,000 vCPU-s; 864,000 GiB-s |
| vCPU overage | 72,000 × $0.000024 = **$1.73** |
| GiB-s overage | 684,000 × $0.0000025 = **$1.71** |
| **Monthly cost** | **~$3.44** |

### 5.7 Google Cloud Firestore

| Metric | Value |
|---|---|
| Free reads/day | **50,000** |
| Free writes/day | **20,000** |
| Free deletes/day | **20,000** |
| Free storage | **1 GiB** |
| App reads per video | ~50 |
| App writes per video | ~30 |
| Daily ops at 12 videos/day | ~600 reads, ~360 writes |
| Free tier utilization | 1.2% of daily reads quota |
| **Effective monthly cost** | **$0** |

### 5.8 Google Cloud Storage

| Metric | Value |
|---|---|
| Free storage | **5 GB/month** |
| Free egress | 1 GB/month |
| Paid storage | $0.020/GB/month |
| Per-video storage | ~80 MB |
| Retention | 7 days (TMP_RETENTION_DAYS) |
| Peak storage (7-day rolling window) | 84 videos × 80 MB = **~6.7 GB** |
| Overage | ~1.7 GB × $0.02 = **~$0.03/month** |
| **Effective monthly cost** | **~$0.03** |

### 5.9 Google Cloud Tasks

| Metric | Value |
|---|---|
| Free tasks/month | **1,000,000** |
| App tasks per video | 1 |
| Monthly tasks at 12/day | 360 |
| **Effective monthly cost** | **$0** |

### 5.10 Google Cloud Scheduler

| Metric | Value |
|---|---|
| Free jobs | **3 jobs** |
| App has | multiple jobs including news/research at `12am, 4am, 8am, 12pm, 4pm, 8pm IST` and stories at `7am, 11am, 2pm, 6pm IST` |
| Paid jobs | $0.10/job/month |
| Paid jobs in use | 1 |
| **Monthly cost** | **$0.10 (≈ ₹9)** |

### 5.11 Telegram Bot API

| Metric | Value |
|---|---|
| Cost | **Free** |
| Rate limit | 30 messages/second globally, 1 message/second to same chat |
| App usage | ~5 messages per video + daily digest + retry alerts |
| Video delivery fallback | Supports files up to 50 MB (Shorts well within limit) |
| **Monthly cost** | **$0** |

---

## 6. GCP Free Trial Summary

| Metric | Value |
|---|---|
| Total credits | **₹27,287** |
| Trial expiry | **June 18, 2026** (~74 days from today) |
| Estimated burn at 12 videos/day | ~₹128/day → **₹9,472 total over remaining trial** |
| Credits remaining after trial period | ~₹17,815 (but these expire with the trial) |
| Post-trial cost at 12 videos/day | **~₹4,098/month** |

> All costs during the free trial period are covered by GCP credits. After June 18, 2026, you will need a billing account. Imagen 3 ($43.20/month) and Cloud Run overage ($3.44/month) are the only services that cannot be covered by permanent free tiers.

---

## 7. Cost Optimization Options

| Action | Monthly Savings | Tradeoff |
|---|---|---|
| Reduce scenes from 3 → 2 | **~$14.40/month** (~$0.04/video × 360) | Shorter videos |
| Cache thumbnails by genre (reuse across same domain) | **~$7.20/month** (~$0.02/video × 360) | Less unique thumbnails |
| Drop to 9 videos/day (stay in Cloud Run free tier) | **~$3.44/month** (Cloud Run overage eliminated) | 25% fewer videos |
| Use Gemini 2.0 Flash instead of 2.5 Flash | **~$0.43/month** (~30% cheaper tokens) | Slightly lower quality |
| Upgrade GNews to Basic ($9.99/mo) if hitting 80-call circuit-breaker | Avoids missed research cycles | +$9.99/month |
| Request YouTube API quota increase (free) | Supports >49 videos/day if ever needed | 5 min of Google form |
