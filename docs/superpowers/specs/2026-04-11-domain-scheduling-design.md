# Domain Scheduling: Pre-assigned Slots with Fortnightly Auto-Update

**Date:** 2026-04-11
**Status:** Approved

## Problem

The current `lead_researcher.run()` fetches all 5 primary domains from GNews on every cycle (30 calls/day), even though only 1 domain is ever used per video. This wastes quota and caused a 429 on 2026-04-11 when GNews rate-limited the 3rd domain call in a cycle.

## Goal

- Reduce GNews calls from 30/day to ~6/day (one per cycle)
- Guarantee a predictable content mix: 2 Trending, 1 AI, 3 rotating
- Auto-tune the 3 rotating domains fortnightly based on performance

---

## Slot Assignment

6 scheduler slots per day at 12am, 4am, 8am, 12pm, 4pm, 8pm IST.

| IST Hour | Type | Domain |
|---|---|---|
| 0 (12am) | Fixed | Trending |
| 4 (4am) | Rotating | see below |
| 8 (8am) | Fixed | Artificial Intelligence |
| 12 (12pm) | Fixed | Trending |
| 16 (4pm) | Rotating | see below |
| 20 (8pm) | Rotating | see below |

**Fixed slots** are hardcoded. **Rotating slots** are served one domain each, cycling daily.

### Rotating Slot Rotation

The 3 rotating domains (default: Technology, Current Affairs, Science) are assigned to the 3 rotating slots using `day_of_year % 3` as a rotation index:

| day % 3 | 4am | 4pm | 8pm |
|---|---|---|---|
| 0 | domains[0] | domains[1] | domains[2] |
| 1 | domains[1] | domains[2] | domains[0] |
| 2 | domains[2] | domains[0] | domains[1] |

Each domain gets exactly one slot per day, and rotates through all 3 time slots over 3 days.

---

## Firestore Config

Document: `config/domain_schedule`

```json
{
  "rotating_domains": ["Technology", "Current Affairs", "Science"],
  "last_updated": "2026-04-11"
}
```

- `rotating_domains`: ordered list of 3 domain names used in rotating slots
- `last_updated`: ISO date string; used to gate the fortnightly update

Fixed slots are hardcoded in `lead_researcher.py` and are never stored in Firestore.

The doc is editable manually in Firestore console without a deploy.

---

## Refactored `run()` Flow

```
1. Determine current IST hour → look up assigned domain (fixed or rotating)
2. Fetch GNews for that domain only (1 call)
3. Score & filter articles (same logic as today)
4. If <2 quality articles pass threshold:
     Fallback: try remaining primary domains in performance-weighted order
     (up to 4 more calls — worst case same as today's 5)
   If all primaries fail:
     Fallback Phase 3: try a random fallback domain (Health, Business, etc.)
   If still nothing: return None (skip cycle)
5. Select best article → save batch → enqueue Cloud Task (unchanged)
```

### New helper: `_get_slot_domain()`

```python
def _get_slot_domain(schedule: dict) -> str:
    """Return the domain assigned to the current IST scheduler slot."""
```

- Reads current IST hour
- Checks fixed slot map first (hours 0, 8, 12)
- For rotating slots (hours 4, 16, 20): uses `day_of_year % 3` against `rotating_domains`

---

## Fortnightly Auto-Update

### Trigger

The existing `/research/update-analytics` endpoint (nightly at 10pm IST). At the start of that handler, call `lead_researcher.update_domain_schedule()`. The function reads `last_updated` from `config/domain_schedule` and no-ops if fewer than 14 days have passed.

### Algorithm

1. Read all `jobs` documents with `status == "completed"` from the last 14 days
2. Group by `genre`, compute average `view_count` per domain
3. Eligible domains: all 5 primary domains **except** Trending and Artificial Intelligence, plus all 5 fallback domains (Health, Business, Sports, Entertainment, Environment) — 8 candidates total
4. Take top 3 by avg views
5. Write `{"rotating_domains": [...], "last_updated": today}` to `config/domain_schedule`
6. Send Telegram notification to the news channel: `"📅 Domain schedule updated: Domain1, Domain2, Domain3"`

Domains with no jobs in the window get avg_views = 0 and rank last (but remain eligible).

---

## Code Changes

| File | Change |
|---|---|
| `app/services/firestore_service.py` | Add `get_domain_schedule()` and `save_domain_schedule()` |
| `app/agents/lead_researcher.py` | Add `_get_slot_domain()`, `update_domain_schedule()`, refactor `run()` |
| `app/routes/research.py` | Call `update_domain_schedule()` inside the analytics update endpoint |

No schema changes to `jobs`, `news_batches`, or `pipeline_state`. No new Cloud Scheduler jobs. No deploy flags changed.

---

## GNews Call Budget

| Scenario | Calls/cycle | Calls/day |
|---|---|---|
| Current (all domains) | 5 | 30 |
| Proposed (happy path) | 1 | 6 |
| Proposed (1 fallback needed) | 2 | up to 12 |
| Proposed (worst case) | 5 | up to 30 |

Expected steady-state: 6 calls/day, leaving 94 in reserve.

---

## What Does Not Change

- All article scoring logic (`rate_and_select_news`, `rigorous_score`, `_recency_score`, `_trend_bonus`)
- 24h lookback window — assigned domain still catches the full day's best articles
- `domain_posts` tracking — continues to record which domain was posted
- Daily digest — still shows domain coverage correctly
- Fallback domain list (Health, Business, Sports, Entertainment, Environment) — unchanged
- All bot commands (CREATE, REDO, RESEND, etc.) — unaffected
