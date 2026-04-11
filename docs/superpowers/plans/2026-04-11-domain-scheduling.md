# Domain Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current all-5-domains GNews fetch with a pre-assigned single-domain fetch per scheduler slot, reducing calls from 30/day to ~6/day, with fortnightly auto-update of the 3 rotating domains.

**Architecture:** A Firestore doc (`config/domain_schedule`) stores the 3 rotating domains. `lead_researcher.run()` reads the current IST hour, resolves the assigned domain for this slot, fetches only that domain, and falls back to other primaries (performance-weighted) only if needed. A fortnightly function re-ranks the rotating domains by avg views and updates the doc.

**Tech Stack:** Python, FastAPI, Firestore, GNews API (existing stack — no new dependencies)

---

### Task 1: Add firestore helpers — `get_domain_schedule`, `save_domain_schedule`, `get_genre_performance_fortnightly`

**Files:**
- Modify: `app/services/firestore_service.py` (append after `get_genre_performance_weekly`)
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_pipeline.py`:

```python
# ---------------------------------------------------------------------------
# Task 1: domain schedule firestore helpers
# ---------------------------------------------------------------------------

@patch("app.services.firestore_service.firestore")
def test_get_domain_schedule_returns_default_when_missing(mock_fs):
    mock_db = MagicMock()
    mock_fs.Client.return_value = mock_db
    missing = MagicMock(); missing.exists = False
    mock_db.collection().document().get.return_value = missing

    from app.services import firestore_service
    firestore_service._db = mock_db
    result = firestore_service.get_domain_schedule()
    assert result["rotating_domains"] == ["Technology", "Current Affairs", "Science"]


@patch("app.services.firestore_service.firestore")
def test_get_domain_schedule_returns_stored_value(mock_fs):
    mock_db = MagicMock()
    mock_fs.Client.return_value = mock_db
    doc = MagicMock(); doc.exists = True
    doc.to_dict.return_value = {
        "rotating_domains": ["Health", "Business", "Sports"],
        "last_updated": "2026-04-01",
    }
    mock_db.collection().document().get.return_value = doc

    from app.services import firestore_service
    firestore_service._db = mock_db
    result = firestore_service.get_domain_schedule()
    assert result["rotating_domains"] == ["Health", "Business", "Sports"]


@patch("app.services.firestore_service.firestore")
def test_save_domain_schedule_writes_correct_fields(mock_fs):
    mock_db = MagicMock()
    mock_fs.Client.return_value = mock_db

    from app.services import firestore_service
    firestore_service._db = mock_db
    firestore_service.save_domain_schedule(["Technology", "Science", "Health"])

    mock_db.collection.assert_called_with("config")
    mock_db.collection().document.assert_called_with("domain_schedule")
    written = mock_db.collection().document().set.call_args[0][0]
    assert written["rotating_domains"] == ["Technology", "Science", "Health"]
    assert "last_updated" in written
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/chetan/Desktop/DSE_Projects/youtube-video-generation-tool
source venv/bin/activate
pytest tests/test_pipeline.py::test_get_domain_schedule_returns_default_when_missing tests/test_pipeline.py::test_get_domain_schedule_returns_stored_value tests/test_pipeline.py::test_save_domain_schedule_writes_correct_fields -v 2>&1 | tail -20
```

Expected: FAILED (functions not defined)

- [ ] **Step 3: Implement the three functions**

Append to `app/services/firestore_service.py` after `get_genre_performance_weekly`:

```python
_DEFAULT_DOMAIN_SCHEDULE = {
    "rotating_domains": ["Technology", "Current Affairs", "Science"],
    "last_updated": "2000-01-01",
}


def get_domain_schedule() -> dict:
    """Return the current domain schedule config, or defaults if not set."""
    try:
        doc = _get_db().collection("config").document("domain_schedule").get()
        if doc.exists:
            return doc.to_dict() or _DEFAULT_DOMAIN_SCHEDULE
        return _DEFAULT_DOMAIN_SCHEDULE
    except Exception:
        return _DEFAULT_DOMAIN_SCHEDULE


def save_domain_schedule(rotating_domains: list[str]) -> None:
    """Persist the rotating domain list and today's date to Firestore."""
    from datetime import date
    _get_db().collection("config").document("domain_schedule").set({
        "rotating_domains": rotating_domains,
        "last_updated": date.today().isoformat(),
    })


def get_genre_performance_fortnightly() -> dict[str, float]:
    """Return average view_count per genre over the last 14 days.

    Only considers completed jobs that have analytics data.
    Returns {genre_lower: avg_views}. Genres with no data are absent.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        docs = (
            _get_db()
            .collection("jobs")
            .where("status", "==", "completed")
            .where("updated_at", ">=", cutoff)
            .stream()
        )
        totals: dict[str, list[int]] = {}
        for d in docs:
            data = d.to_dict() or {}
            genre = (data.get("genre") or "").strip().lower()
            analytics = data.get("analytics") or {}
            views = int(analytics.get("view_count", 0))
            if genre:
                totals.setdefault(genre, []).append(views)
        return {g: sum(v) / len(v) for g, v in totals.items()}
    except Exception:
        return {}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_pipeline.py::test_get_domain_schedule_returns_default_when_missing tests/test_pipeline.py::test_get_domain_schedule_returns_stored_value tests/test_pipeline.py::test_save_domain_schedule_writes_correct_fields -v 2>&1 | tail -10
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/firestore_service.py tests/test_pipeline.py
git commit -m "feat: add domain schedule firestore helpers"
```

---

### Task 2: Add `_get_slot_domain()` and `_performance_weighted_order()` helpers + fix missing logger

**Files:**
- Modify: `app/agents/lead_researcher.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_pipeline.py`:

```python
# ---------------------------------------------------------------------------
# Task 2: _get_slot_domain helper
# ---------------------------------------------------------------------------

from unittest.mock import patch
from datetime import datetime, timezone


def _make_schedule(domains=None):
    return {
        "rotating_domains": domains or ["Technology", "Current Affairs", "Science"],
        "last_updated": "2026-04-11",
    }


def test_get_slot_domain_fixed_midnight():
    """Hour 0 (12am IST) must always return Trending."""
    fake_now = datetime(2026, 4, 11, 0, 0, tzinfo=timezone.utc)  # midnight UTC ~ 5:30am IST; override below
    with patch("app.agents.lead_researcher._ist_now_hour", return_value=0):
        from app.agents import lead_researcher
        assert lead_researcher._get_slot_domain(_make_schedule()) == "Trending"


def test_get_slot_domain_fixed_8am():
    """Hour 8 (8am IST) must always return Artificial Intelligence."""
    with patch("app.agents.lead_researcher._ist_now_hour", return_value=8):
        from app.agents import lead_researcher
        assert lead_researcher._get_slot_domain(_make_schedule()) == "Artificial Intelligence"


def test_get_slot_domain_fixed_noon():
    """Hour 12 (12pm IST) must always return Trending."""
    with patch("app.agents.lead_researcher._ist_now_hour", return_value=12):
        from app.agents import lead_researcher
        assert lead_researcher._get_slot_domain(_make_schedule()) == "Trending"


def test_get_slot_domain_rotating_4am_day0():
    """Hour 4, day_of_year % 3 == 0 → first rotating domain."""
    with patch("app.agents.lead_researcher._ist_now_hour", return_value=4), \
         patch("app.agents.lead_researcher._ist_day_of_year", return_value=3):  # 3 % 3 == 0
        from app.agents import lead_researcher
        result = lead_researcher._get_slot_domain(_make_schedule(["Technology", "Current Affairs", "Science"]))
        assert result == "Technology"


def test_get_slot_domain_rotating_4pm_day0():
    """Hour 16, day_of_year % 3 == 0 → second rotating domain."""
    with patch("app.agents.lead_researcher._ist_now_hour", return_value=16), \
         patch("app.agents.lead_researcher._ist_day_of_year", return_value=3):
        from app.agents import lead_researcher
        result = lead_researcher._get_slot_domain(_make_schedule(["Technology", "Current Affairs", "Science"]))
        assert result == "Current Affairs"


def test_get_slot_domain_rotating_8pm_day0():
    """Hour 20, day_of_year % 3 == 0 → third rotating domain."""
    with patch("app.agents.lead_researcher._ist_now_hour", return_value=20), \
         patch("app.agents.lead_researcher._ist_day_of_year", return_value=3):
        from app.agents import lead_researcher
        result = lead_researcher._get_slot_domain(_make_schedule(["Technology", "Current Affairs", "Science"]))
        assert result == "Science"


def test_get_slot_domain_rotating_shifts_next_day():
    """Day+1 should shift all rotating slots by one domain."""
    with patch("app.agents.lead_researcher._ist_now_hour", return_value=4), \
         patch("app.agents.lead_researcher._ist_day_of_year", return_value=4):  # 4 % 3 == 1
        from app.agents import lead_researcher
        result = lead_researcher._get_slot_domain(_make_schedule(["Technology", "Current Affairs", "Science"]))
        assert result == "Current Affairs"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_pipeline.py::test_get_slot_domain_fixed_midnight tests/test_pipeline.py::test_get_slot_domain_fixed_8am tests/test_pipeline.py::test_get_slot_domain_fixed_noon tests/test_pipeline.py::test_get_slot_domain_rotating_4am_day0 tests/test_pipeline.py::test_get_slot_domain_rotating_4pm_day0 tests/test_pipeline.py::test_get_slot_domain_rotating_8pm_day0 tests/test_pipeline.py::test_get_slot_domain_rotating_shifts_next_day -v 2>&1 | tail -15
```

Expected: FAILED

- [ ] **Step 3: Add logger, `_ist_now_hour`, `_ist_day_of_year`, `_get_slot_domain`, `_performance_weighted_order` to lead_researcher**

Replace the top of `app/agents/lead_researcher.py` imports section (add `import logging`, `import random`, and `logger`):

```python
# app/agents/lead_researcher.py

import logging
import random
from datetime import datetime, timezone, timedelta
from app.services import gnews_service, firestore_service
from app.services.llm_service import rate_and_select_news
from app.services.telegram_service import send_message
from app.config import TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)
```

Then add these four functions after `_prefix_for_domain` and before `_primary_domain_query_map`:

```python
def _ist_now_hour() -> int:
    """Return the current hour in IST (0-23). Extracted for testability."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).hour


def _ist_day_of_year() -> int:
    """Return the current day-of-year in IST. Extracted for testability."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).timetuple().tm_yday


_FIXED_SLOTS: dict[int, str] = {
    0: "Trending",
    8: "Artificial Intelligence",
    12: "Trending",
}
_ROTATING_SLOT_POSITIONS: dict[int, int] = {4: 0, 16: 1, 20: 2}


def _get_slot_domain(schedule: dict) -> str:
    """Return the domain assigned to the current IST scheduler slot.

    Fixed slots (0h, 8h, 12h) are hardcoded.
    Rotating slots (4h, 16h, 20h) cycle through schedule['rotating_domains']
    using day_of_year % 3 as the rotation index.
    """
    hour = _ist_now_hour()
    if hour in _FIXED_SLOTS:
        return _FIXED_SLOTS[hour]
    rotating = schedule.get("rotating_domains", ["Technology", "Current Affairs", "Science"])
    slot_pos = _ROTATING_SLOT_POSITIONS.get(hour, 0)
    day_offset = _ist_day_of_year() % 3
    return rotating[(day_offset + slot_pos) % len(rotating)]


def _performance_weighted_order(domains: list[str], genre_perf: dict[str, float]) -> list[str]:
    """Return domains in a performance-weighted random order (highest-weight first, on average).

    Uses weighted-sampling-without-replacement so high-performers are more likely
    to appear early in the fallback list.
    """
    _BASE = 1.0
    pool = [(d, _BASE + genre_perf.get(d.lower(), 0.0)) for d in domains]
    ordered: list[str] = []
    while pool:
        names, weights = zip(*pool)
        pick = random.choices(names, weights=weights, k=1)[0]
        pool = [(d, w) for d, w in pool if d != pick]
        ordered.append(pick)
    return ordered
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_pipeline.py::test_get_slot_domain_fixed_midnight tests/test_pipeline.py::test_get_slot_domain_fixed_8am tests/test_pipeline.py::test_get_slot_domain_fixed_noon tests/test_pipeline.py::test_get_slot_domain_rotating_4am_day0 tests/test_pipeline.py::test_get_slot_domain_rotating_4pm_day0 tests/test_pipeline.py::test_get_slot_domain_rotating_8pm_day0 tests/test_pipeline.py::test_get_slot_domain_rotating_shifts_next_day -v 2>&1 | tail -15
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add app/agents/lead_researcher.py tests/test_pipeline.py
git commit -m "feat: add slot domain resolution and weighted fallback order helpers"
```

---

### Task 3: Refactor `run()` — replace multi-domain loop with single-domain fetch + fallback

**Files:**
- Modify: `app/agents/lead_researcher.py` — replace entire `run()` body

The old `run()` had: loop over all 5 domains, cross-domain dedup, Phase 1 missing-domain weighted selection, Phase 2 global-best selection. All of that is removed. The new `run()` resolves one domain from the schedule, fetches it, falls back through other primaries if needed, then Phase 2 tries fallback domains.

- [ ] **Step 1: Replace the full `run()` function body**

In `app/agents/lead_researcher.py`, replace the entire `run()` function (lines 313–512 in the original) with:

```python
def run() -> str | None:
    _expire_stale_digest_if_needed()

    if not _within_suggestion_window():
        return None

    state = firestore_service.get_pipeline_state() or {}
    if state.get("state") == "processing":
        return None

    lookback_hours = 24
    from_date = (
        datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")

    schedule = firestore_service.get_domain_schedule()
    assigned_domain = _get_slot_domain(schedule)

    top_performers = firestore_service.get_top_performers(n=3)
    recently_covered = firestore_service.get_recently_suggested_headlines(
        days=14, limit=20, channel_id="news"
    )
    genre_perf = firestore_service.get_genre_performance_weekly()

    all_primary = _primary_domain_query_map()
    # Try assigned domain first, then remaining primaries in perf-weighted order
    domain_order = [assigned_domain] + _performance_weighted_order(
        [d for d in all_primary if d != assigned_domain],
        genre_perf,
    )

    selected_domain = ""
    selected_item = None
    domain_articles: list[dict] = []

    for domain in domain_order:
        cfg = all_primary.get(domain)
        if not cfg:
            continue
        try:
            search = gnews_service.search_news(
                query=cfg["query"],
                max_results=25,
                from_date=from_date,
                category=cfg["category"],
            )
        except Exception as e:
            logger.warning(f"GNews search failed for domain '{domain}', skipping: {e}")
            continue
        raw = [a for a in search if _is_recent_article(a, lookback_hours)]
        candidates = _dedupe_and_filter_unsuggested(raw)
        if not candidates:
            continue
        _orig_lookup = {_norm_headline(c.get("headline", "")): c for c in candidates}
        rated = rate_and_select_news(
            candidates, top_performers=top_performers, recently_covered=recently_covered
        )[:5]
        for item in rated:
            orig = _orig_lookup.get(_norm_headline(item.get("headline", ""))) or {}
            item.setdefault("published_at", orig.get("published_at", ""))
            item.setdefault("url", orig.get("url", ""))
            item.setdefault("source", orig.get("source", ""))
        enriched = []
        for item in rated:
            score = float(item.get("rating", 0))
            if score < 3.8:
                continue
            rigorous = (
                (score * 0.60)
                + (_recency_score(item) * 2.0)
                + _trend_bonus(item.get("headline", ""))
            )
            enriched.append({
                **item,
                "genre": domain,
                "rigorous_score": round(min(5.0, rigorous), 2),
            })
        if enriched:
            enriched = sorted(enriched, key=lambda x: x.get("rigorous_score", 0), reverse=True)
            selected_domain = domain
            selected_item = enriched[0]
            domain_articles = enriched
            break

    # Fallback: try fallback domains if all primaries returned nothing
    if not selected_item:
        fallback_domains = _fallback_domain_query_map()
        fallback_names = list(fallback_domains.keys())
        random.shuffle(fallback_names)
        for fallback_name in fallback_names:
            cfg = fallback_domains[fallback_name]
            try:
                search = gnews_service.search_news(
                    query=cfg["query"],
                    max_results=25,
                    from_date=from_date,
                    category=cfg["category"],
                )
            except Exception as e:
                logger.warning(f"GNews search failed for fallback '{fallback_name}', skipping: {e}")
                continue
            raw = [a for a in search if _is_recent_article(a, lookback_hours)]
            candidates = _dedupe_and_filter_unsuggested(raw)
            if candidates:
                selected_domain = fallback_name
                selected_item = {
                    **candidates[0],
                    "genre": fallback_name,
                    "rigorous_score": 3.5,
                }
                domain_articles = [selected_item]
                break

    if not selected_item:
        return None

    batch_id = f"auto_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    prefix = _prefix_for_domain(selected_domain)
    code = f"{prefix}01"
    selected_item["code"] = code
    items = _assign_codes(domain_articles[:5], prefix)
    items[code] = selected_item  # ensure the chosen item is keyed by its primary code

    firestore_service.save_news_batch(batch_id, selected_domain.lower(), items)
    firestore_service.set_pipeline_and_batch_state(batch_id, "processing")

    from app.agents import whatsapp_agent
    task_name = whatsapp_agent._task_name(batch_id, code)
    public_id = whatsapp_agent._public_video_id(task_name)
    context_summary = (
        selected_item.get("context")
        or selected_item.get("description")
        or "Top trending story selected."
    )
    pub_date = selected_item.get("published_at", "")
    source_url = selected_item.get("url", "")
    date_prefix = f"[Article published: {pub_date}]" if pub_date else ""
    url_suffix = f" Source: {source_url}" if source_url else ""
    details = f"{date_prefix} {context_summary}{url_suffix}".strip()
    virality = float(selected_item.get("rigorous_score", selected_item.get("rating", 4.0)))

    send_message(
        TELEGRAM_CHAT_ID,
        (
            "🎬 A video is being generated...\n"
            f"Id: `{public_id}`\n"
            f"Domain: {selected_domain.title()}\n"
            f"Headline: {selected_item.get('headline', '')}\n"
            f"Details: {details}\n"
            f"Virality Score: {virality}/5"
        ),
        channel_id="news",
    )

    enqueued = whatsapp_agent._enqueue_generate(
        selected_item.get("headline", ""),
        code,
        batch_id,
        public_id=public_id,
        genre=selected_domain,
        details=details,
        virality_score=virality,
        source="researcher",
    )
    if not enqueued:
        firestore_service.set_pipeline_and_batch_state(batch_id, "failed")
        return None

    firestore_service.mark_headline_suggested(
        headline=selected_item.get("headline", ""),
        genre=selected_domain,
    )
    return batch_id
```

- [ ] **Step 2: Run the full test suite to confirm nothing is broken**

```bash
pytest tests/ -v 2>&1 | tail -30
```

Expected: all previously passing tests still pass

- [ ] **Step 3: Commit**

```bash
git add app/agents/lead_researcher.py
git commit -m "refactor: replace multi-domain GNews loop with single-domain slot fetch"
```

---

### Task 4: Add `update_domain_schedule()` to lead_researcher

**Files:**
- Modify: `app/agents/lead_researcher.py` (add after `retry_failed_pipeline`)
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_pipeline.py`:

```python
# ---------------------------------------------------------------------------
# Task 4: update_domain_schedule
# ---------------------------------------------------------------------------

def test_update_domain_schedule_skips_if_updated_recently():
    """Should no-op when last_updated is less than 14 days ago."""
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=5)).isoformat()

    with patch("app.services.firestore_service.get_domain_schedule",
               return_value={"rotating_domains": ["Technology", "Current Affairs", "Science"],
                             "last_updated": recent}), \
         patch("app.services.firestore_service.save_domain_schedule") as mock_save:
        from app.agents import lead_researcher
        result = lead_researcher.update_domain_schedule()
        assert result is False
        mock_save.assert_not_called()


def test_update_domain_schedule_updates_when_overdue():
    """Should update rotating_domains when last_updated >= 14 days ago."""
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=15)).isoformat()

    perf = {
        "health": 5000.0,
        "business": 3000.0,
        "sports": 2000.0,
        "science": 500.0,
        "current affairs": 400.0,
        "technology": 300.0,
    }

    with patch("app.services.firestore_service.get_domain_schedule",
               return_value={"rotating_domains": ["Technology", "Current Affairs", "Science"],
                             "last_updated": old}), \
         patch("app.services.firestore_service.get_genre_performance_fortnightly",
               return_value=perf), \
         patch("app.services.firestore_service.save_domain_schedule") as mock_save, \
         patch("app.agents.lead_researcher.send_message"):
        from app.agents import lead_researcher
        result = lead_researcher.update_domain_schedule()
        assert result is True
        saved = mock_save.call_args[0][0]
        # Top 3 eligible (excluding Trending, AI): Health, Business, Sports
        assert saved == ["Health", "Business", "Sports"]


def test_update_domain_schedule_excludes_fixed_domains():
    """Trending and Artificial Intelligence must never appear in rotating_domains."""
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=20)).isoformat()

    # Give fixed domains high scores to verify they are excluded
    perf = {
        "trending": 99999.0,
        "artificial intelligence": 99999.0,
        "technology": 1000.0,
        "science": 800.0,
        "health": 600.0,
    }

    with patch("app.services.firestore_service.get_domain_schedule",
               return_value={"rotating_domains": ["Technology", "Current Affairs", "Science"],
                             "last_updated": old}), \
         patch("app.services.firestore_service.get_genre_performance_fortnightly",
               return_value=perf), \
         patch("app.services.firestore_service.save_domain_schedule") as mock_save, \
         patch("app.agents.lead_researcher.send_message"):
        from app.agents import lead_researcher
        lead_researcher.update_domain_schedule()
        saved = mock_save.call_args[0][0]
        assert "Trending" not in saved
        assert "Artificial Intelligence" not in saved
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_pipeline.py::test_update_domain_schedule_skips_if_updated_recently tests/test_pipeline.py::test_update_domain_schedule_updates_when_overdue tests/test_pipeline.py::test_update_domain_schedule_excludes_fixed_domains -v 2>&1 | tail -15
```

Expected: FAILED

- [ ] **Step 3: Add `update_domain_schedule()` to lead_researcher**

Add this function after `retry_failed_pipeline` and before `run()` in `app/agents/lead_researcher.py`:

```python
_FIXED_DOMAIN_NAMES = {"trending", "artificial intelligence"}


def update_domain_schedule() -> bool:
    """Fortnightly: re-rank rotating domains by 14-day avg views and update Firestore.

    Returns True if the schedule was updated, False if it was skipped (updated < 14 days ago).
    Only the 3 rotating slots are updated. Trending and Artificial Intelligence are never candidates.
    """
    from datetime import date
    schedule = firestore_service.get_domain_schedule()
    try:
        last_updated = date.fromisoformat(schedule.get("last_updated", "2000-01-01"))
    except Exception:
        last_updated = date(2000, 1, 1)

    if (date.today() - last_updated).days < 14:
        logger.info("update_domain_schedule: last update was < 14 days ago, skipping.")
        return False

    genre_perf = firestore_service.get_genre_performance_fortnightly()

    # Eligible: all primaries except fixed ones, plus all fallback domains
    candidates = [
        d for d in _primary_domain_query_map()
        if d.lower() not in _FIXED_DOMAIN_NAMES
    ] + list(_fallback_domain_query_map().keys())

    # Sort by avg views descending; domains with no data score 0 (rank last but remain eligible)
    ranked = sorted(candidates, key=lambda d: genre_perf.get(d.lower(), 0.0), reverse=True)
    new_rotating = ranked[:3]

    firestore_service.save_domain_schedule(new_rotating)
    logger.info(f"update_domain_schedule: updated to {new_rotating}")
    send_message(
        TELEGRAM_CHAT_ID,
        f"📅 Domain schedule updated: {', '.join(new_rotating)}",
        channel_id="news",
    )
    return True
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_pipeline.py::test_update_domain_schedule_skips_if_updated_recently tests/test_pipeline.py::test_update_domain_schedule_updates_when_overdue tests/test_pipeline.py::test_update_domain_schedule_excludes_fixed_domains -v 2>&1 | tail -15
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/agents/lead_researcher.py tests/test_pipeline.py
git commit -m "feat: add fortnightly domain schedule auto-update"
```

---

### Task 5: Wire `update_domain_schedule()` into `/research/update-analytics`

**Files:**
- Modify: `app/routes/research.py`

- [ ] **Step 1: Add the call inside `update_analytics`**

In `app/routes/research.py`, replace the `update_analytics` function with:

```python
@router.post("/research/update-analytics")
def update_analytics(request: Request):
    """Called by Cloud Scheduler daily. Fetches YouTube analytics and runs fortnightly schedule update."""
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        # Fortnightly domain schedule update (no-ops if < 14 days since last update)
        schedule_updated = lead_researcher.update_domain_schedule()

        from app.services import youtube_service
        jobs = firestore_service.list_recent_jobs(limit=200)
        updated = 0
        for job in jobs:
            if job.get("status") != "completed":
                continue
            video_id = youtube_service.extract_video_id(job.get("youtube_url", ""))
            if not video_id:
                continue
            analytics = youtube_service.fetch_video_analytics(video_id)
            if analytics:
                firestore_service.update_job_analytics(job["job_id"], analytics)
                updated += 1
        return {"status": "ok", "updated": updated, "schedule_updated": schedule_updated}
    except Exception as e:
        logger.exception(f"update_analytics failed: {e}")
        raise HTTPException(status_code=500, detail="update_analytics_failed")
```

- [ ] **Step 2: Run the full test suite**

```bash
pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add app/routes/research.py
git commit -m "feat: wire fortnightly domain schedule update into analytics endpoint"
```

---

### Task 6: Initialise the Firestore config doc and verify end-to-end

**Files:**
- No code changes — seed Firestore and verify

- [ ] **Step 1: Seed the `config/domain_schedule` document in Firestore**

```bash
cd /Users/chetan/Desktop/DSE_Projects/youtube-video-generation-tool
source venv/bin/activate
python3 - <<'EOF'
from app.services import firestore_service
# Write default schedule. last_updated set to old date so fortnightly update
# runs on the next analytics invocation if desired, or set to today to skip.
import datetime
firestore_service._get_db().collection("config").document("domain_schedule").set({
    "rotating_domains": ["Technology", "Current Affairs", "Science"],
    "last_updated": "2000-01-01",   # force update on next analytics run
})
print("config/domain_schedule written.")
doc = firestore_service.get_domain_schedule()
print("Read back:", doc)
EOF
```

Expected output:
```
config/domain_schedule written.
Read back: {'rotating_domains': ['Technology', 'Current Affairs', 'Science'], 'last_updated': '2000-01-01'}
```

- [ ] **Step 2: Smoke-test `_get_slot_domain` locally**

```bash
python3 - <<'EOF'
from app.services import firestore_service
from app.agents.lead_researcher import _get_slot_domain, _ist_now_hour, _ist_day_of_year

schedule = firestore_service.get_domain_schedule()
print(f"Schedule: {schedule}")
print(f"Current IST hour: {_ist_now_hour()}")
print(f"Day of year: {_ist_day_of_year()} (mod 3 = {_ist_day_of_year() % 3})")
print(f"Assigned domain for this slot: {_get_slot_domain(schedule)}")
EOF
```

Expected: prints the domain assigned to whichever IST hour it currently is.

- [ ] **Step 3: Run full test suite one final time**

```bash
pytest tests/ -v 2>&1 | tail -30
```

Expected: all tests pass

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: domain scheduling complete — single-domain GNews fetch per slot"
```
