# app/agents/lead_researcher.py

import logging
import random
from datetime import datetime, timezone, timedelta
from app.services import gnews_service, firestore_service
from app.services.llm_service import rate_and_select_news
from app.services.trends_service import get_trend_scores
from app.services.telegram_service import send_message
from app.config import TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def _within_suggestion_window() -> bool:
    # v3: no time restriction for automated generation.
    return True


def _norm_headline(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _dedupe_and_filter_unsuggested(articles: list[dict]) -> list[dict]:
    out = []
    local_seen: set[str] = set()
    for article in articles:
        headline = article.get("headline", "")
        key = _norm_headline(headline)
        if not key or key in local_seen:
            continue
        local_seen.add(key)
        if firestore_service.is_headline_already_suggested(headline):
            continue
        out.append(article)
    return out


def _is_recent_article(article: dict, lookback_hours: int) -> bool:
    published_at = _parse_iso(article.get("published_at"))
    if not published_at:
        # Keep articles with missing published time instead of dropping too aggressively.
        return True
    age_seconds = (datetime.now(timezone.utc) - published_at.astimezone(timezone.utc)).total_seconds()
    return age_seconds <= (lookback_hours * 3600)


def _recency_score(article: dict) -> float:
    published_at = _parse_iso(article.get("published_at"))
    if not published_at:
        return 0.2
    age_h = (datetime.now(timezone.utc) - published_at.astimezone(timezone.utc)).total_seconds() / 3600
    if age_h <= 3:
        return 1.0
    if age_h <= 6:
        return 0.85
    if age_h <= 12:
        return 0.65
    return 0.2


def _trend_bonus(headline: str) -> float:
    text = (headline or "").lower()
    hot_terms = [
        "breaking", "just in", "launch", "new", "update",
        "ai", "google", "openai", "nvidia", "tesla", "apple",
        "mars", "moon", "election", "war", "quantum", "chip",
    ]
    hit = sum(1 for t in hot_terms if t in text)
    return min(0.5, 0.1 * hit)


def _prefix_for_domain(domain: str) -> str:
    return {
        "technology": "TECH",
        "artificial intelligence": "AI",
        "current affairs": "CA",
        "trending": "TRND",
        "science": "SCI",
        "health": "HLTH",
        "business": "BIZ",
        "sports": "SPRT",
        "entertainment": "ENT",
        "environment": "ENV",
    }.get(domain.lower(), "NEWS")


def _ist_now_hour() -> int:
    """Return the current hour in IST (0-23). Extracted for testability."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).hour


def _ist_day_of_year() -> int:
    """Return the current day-of-year in IST. Extracted for testability."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).timetuple().tm_yday


_FIXED_SLOTS: dict[int, str] = {
    10: "Artificial Intelligence",
}
_ROTATING_SLOT_POSITIONS: dict[int, int] = {2: 0, 18: 1}


def _get_slot_domain(schedule: dict) -> str:
    """Return the domain assigned to the current IST scheduler slot.

    Fixed slots (10h IST) are hardcoded.
    Rotating slots (2h, 18h IST) cycle through schedule['rotating_domains']
    using day_of_year % 3 as the rotation index so every domain gets equal
    exposure across all time slots over a 3-day cycle.
    """
    hour = _ist_now_hour()
    if hour in _FIXED_SLOTS:
        return _FIXED_SLOTS[hour]
    rotating = schedule.get("rotating_domains") or ["Technology", "Current Affairs", "Science"]
    slot_pos = _ROTATING_SLOT_POSITIONS.get(hour, 0)
    day_offset = _ist_day_of_year() % 3
    return rotating[(day_offset + slot_pos) % len(rotating)]


def _performance_weighted_order(domains: list[str], genre_perf: dict[str, float]) -> list[str]:
    """Return domains in a performance-weighted random order (highest-weight tends first).

    Uses weighted-sampling-without-replacement so high-performing domains are more
    likely to appear early in the fallback sequence.
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


def _primary_domain_query_map() -> dict[str, dict]:
    return {
        "Technology": {
            "category": "technology",
            "query": "technology OR smartphone OR software OR internet OR startup",
        },
        "Artificial Intelligence": {
            "category": "technology",
            "query": "artificial intelligence OR AI OR machine learning OR llm OR generative ai",
        },
        "Current Affairs": {
            "category": "general",
            "query": "current affairs OR global events OR geopolitics OR policy OR economy",
        },
        "Trending": {
            "category": "general",
            "query": "trending OR viral OR breaking OR must watch",
        },
        "Science": {
            "category": "science",
            "query": "science OR space OR research OR discovery OR nasa",
        },
    }


def _fallback_domain_query_map() -> dict[str, dict]:
    return {
        "Health": {
            "category": "health",
            "query": "health OR medicine OR disease OR wellness OR research",
        },
        "Business": {
            "category": "business",
            "query": "business OR economy OR market OR finance OR startup",
        },
        "Sports": {
            "category": "sports",
            "query": "sports OR cricket OR football OR tennis OR olympics",
        },
        "Entertainment": {
            "category": "entertainment",
            "query": "entertainment OR movies OR celebrity OR music OR award",
        },
        "Environment": {
            "category": "science",
            "query": "environment OR climate OR pollution OR nature OR sustainability",
        },
    }


def _assign_codes(items: list[dict], prefix: str) -> dict:
    result = {}
    for i, item in enumerate(items[:5], start=1):
        code = f"{prefix}{i:02d}"
        enriched = {**item, "code": code}
        result[code] = enriched
    return result


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _expire_stale_digest_if_needed():
    state = firestore_service.get_pipeline_state() or {}
    if not isinstance(state, dict):
        return

    if state.get("state") != "awaiting_reply":
        return

    batch_id = state.get("active_batch_id")
    if not batch_id:
        return

    batch = firestore_service.get_news_batch(batch_id) or {}
    if not isinstance(batch, dict):
        return

    created_at = _parse_iso(batch.get("created_at"))
    if not created_at:
        return

    expiry_hours = 2.0
    age_seconds = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
    if age_seconds < expiry_hours * 3600:
        return

    firestore_service.set_pipeline_and_batch_state(batch_id, "skipped")



def send_daily_digest():
    """Send a daily summary report to Telegram."""
    from zoneinfo import ZoneInfo
    from datetime import date as _date
    from app.services import youtube_service as yt_svc
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)

    try:
        yt = yt_svc.get_channel_stats()
        firestore_service.save_social_metrics("youtube", yt)
    except Exception:
        yt = firestore_service.get_social_metrics("youtube") or {}

    # The digest fires at 8am IST, exactly when the window resets to the new day.
    # Use the PREVIOUS window (yesterday 8am → today 8am) to capture the day's activity.
    current_window_start = firestore_service._ist_window_start()
    prev_window_start = current_window_start - timedelta(hours=24)
    prev_day_key = prev_window_start.astimezone(ist).strftime("%Y-%m-%d")

    queue = firestore_service.get_queue_snapshot(window_start=prev_window_start)
    quota = firestore_service.get_quota_usage_snapshot()

    # TTS usage: daily (previous window) + actual month-to-date cumulative total
    tts_chars_today = firestore_service.get_tts_chars_today(window_start=prev_window_start, channel_id="news")
    tts_chars_month = firestore_service.get_tts_chars_this_month(channel_id="news")
    tts_pct = round((tts_chars_month / 1_000_000) * 100, 1)

    # GNews calls in previous window
    gnews_today = firestore_service.get_gnews_calls_today(window_start=prev_window_start)

    # Domain slot coverage: show each slot's assigned domain and whether it was posted
    schedule = firestore_service.get_domain_schedule()
    rotating = schedule.get("rotating_domains") or ["Technology", "Current Affairs", "Science"]
    last_updated = schedule.get("last_updated", "never")
    prev_day_of_year = _date.fromisoformat(prev_day_key).timetuple().tm_yday
    domains_today = firestore_service.get_domains_posted_today(day_key=prev_day_key)
    domain_lines = []
    for hour in [2, 10, 18]:
        if hour in _FIXED_SLOTS:
            domain = _FIXED_SLOTS[hour]
        else:
            slot_pos = _ROTATING_SLOT_POSITIONS.get(hour, 0)
            domain = rotating[(prev_day_of_year % 3 + slot_pos) % len(rotating)]
        mark = "✅" if domain.lower() in domains_today else "⬜"
        domain_lines.append(f"  {mark} {hour:02d}h → {domain}")

    top = firestore_service.get_top_performers(n=1, days=7, channel_id="news")
    top_line = ""
    if top:
        t = top[0]
        top_line = f"\n\n🏆 Weekly Top Video: _{t['topic']}_ ({t['view_count']:,} views)"

    # Failed jobs awaiting manual re-send — scoped to news channel only
    failed_jobs = firestore_service.get_failed_auto_jobs(max_age_hours=24, channel_id="news")
    delivered_manual = [
        j for j in firestore_service.list_recent_jobs(limit=50, channel_id="news")
        if j.get("status") == "delivered_manual"
        and _parse_iso(j.get("updated_at")) and
        (datetime.now(timezone.utc) - _parse_iso(j.get("updated_at")).astimezone(timezone.utc)).total_seconds() < 86400
    ]
    failed_lines = ""
    if failed_jobs or delivered_manual:
        failed_lines = "\n\n⚠️ Jobs Needing Attention\n"
        for j in failed_jobs[:5]:
            pid = j.get("public_id", j.get("job_id", "?"))
            failed_lines += f"  ❌ Failed: `{pid}` — {j.get('topic', '')[:40]}\n"
        for j in delivered_manual[:5]:
            pid = j.get("public_id", j.get("job_id", "?"))
            failed_lines += f"  📤 Manual: `{pid}` — {j.get('topic', '')[:40]}\n"
        failed_lines = failed_lines.rstrip()
        failed_lines += "\n  _(Use RESEND <id> to re-send to Telegram)_"

    message = (
        f"📅 Daily Report — {now_ist.strftime('%d %b %Y, %I:%M %p IST')}\n\n"
        f"📺 Channel\n"
        f"  Subscribers: {int(yt.get('subscriber_count', 0)):,}\n"
        f"  Total Views: {int(yt.get('view_count', 0)):,}\n"
        f"  Videos: {int(yt.get('video_count', 0))}\n\n"
        f"⚙️ Pipeline (24h)\n"
        f"  Completed: {queue.get('completed_24h', 0)}\n"
        f"  Failed: {queue.get('failed_24h', 0)}\n"
        f"  Quota errors: {quota.get('quota_errors_24h', 0)}\n"
        f"  Quota pressure: {quota.get('pressure', 'unknown')}\n\n"
        f"📊 API Usage Today\n"
        f"  TTS chars: {tts_chars_today:,} today | {tts_chars_month:,} this month ({tts_pct}% of 1M free tier)\n"
        f"  GNews calls: {gnews_today}/100\n\n"
        f"🗂️ Slot Coverage Yesterday\n"
        + "\n".join(domain_lines)
        + f"\n  🔄 Rotating: {', '.join(rotating)} (updated: {last_updated})"
        + top_line
        + failed_lines
    )
    send_message(TELEGRAM_CHAT_ID, message, channel_id="news")



_FIXED_DOMAIN_NAMES = {"trending", "artificial intelligence"}


def update_domain_schedule() -> bool:
    """Fortnightly: re-rank rotating domains by 14-day avg views and update Firestore.

    Returns True if the schedule was updated, False if skipped (updated < 14 days ago).
    Trending and Artificial Intelligence are never candidates — only the 3 rotating
    slots are updated.
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

    # Eligible: primaries excluding fixed domains, plus all fallback domains
    candidates = [
        d for d in _primary_domain_query_map()
        if d.lower() not in _FIXED_DOMAIN_NAMES
    ] + list(_fallback_domain_query_map().keys())

    # Sort by avg views descending; domains with no data score 0 (rank last, still eligible)
    ranked = sorted(candidates, key=lambda d: genre_perf.get(d.lower(), 0.0), reverse=True)
    new_rotating = ranked[:3]

    firestore_service.save_domain_schedule(new_rotating)
    logger.info(f"update_domain_schedule: updated rotating domains to {new_rotating}")
    send_message(
        TELEGRAM_CHAT_ID,
        f"📅 Domain schedule updated: {', '.join(new_rotating)}",
        channel_id="news",
    )
    return True


def run() -> str | None:
    _expire_stale_digest_if_needed()

    if not _within_suggestion_window():
        return None

    state = firestore_service.get_pipeline_state() or {}
    if state.get("state") == "processing":
        stale_batch_id = state.get("active_batch_id", "?")
        last_run_str = state.get("last_run_at", "")
        is_stale = False
        if last_run_str:
            try:
                last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                is_stale = (datetime.now(timezone.utc) - last_run) > timedelta(hours=4)
            except Exception:
                pass

        if is_stale:
            logger.warning("Clearing stale news pipeline (batch %s, last_run_at %s)", stale_batch_id, last_run_str)
            try:
                firestore_service.set_pipeline_and_batch_state(stale_batch_id, "failed")
            except Exception as _stale_err:
                logger.warning("set_pipeline_and_batch_state failed during stale clear (%s) — clearing pipeline_state directly", _stale_err)
                firestore_service.set_pipeline_state(stale_batch_id, "failed")
            send_message(
                TELEGRAM_CHAT_ID,
                f"⚠️ Stale pipeline cleared — batch `{stale_batch_id}` was stuck for 4+ hours. Retrying now...",
                channel_id="news",
            )
            # Fall through and run this slot normally
        else:
            slot_domain = _get_slot_domain(firestore_service.get_domain_schedule())
            send_message(
                TELEGRAM_CHAT_ID,
                f"⏭️ Scheduler slot skipped — pipeline is busy processing batch "
                f"`{stale_batch_id}`. "
                f"Assigned domain for this slot: *{slot_domain}*.",
                channel_id="news",
            )
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
    all_query_configs = {**all_primary, **_fallback_domain_query_map()}
    # Try assigned domain first, then remaining primaries in performance-weighted order.
    # assigned_domain may be a promoted fallback domain — all_query_configs covers both maps.
    domain_order = [assigned_domain] + _performance_weighted_order(
        [d for d in all_primary if d != assigned_domain],
        genre_perf,
    )

    selected_domain = ""
    selected_item = None
    domain_articles: list[dict] = []

    for domain in domain_order:
        cfg = all_query_configs.get(domain)
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
        # Re-attach published_at / url that rate_and_select_news drops
        _orig_lookup = {_norm_headline(c.get("headline", "")): c for c in candidates}
        rated = rate_and_select_news(
            candidates, top_performers=top_performers, recently_covered=recently_covered
        )[:5]
        for item in rated:
            orig = _orig_lookup.get(_norm_headline(item.get("headline", ""))) or {}
            item.setdefault("published_at", orig.get("published_at", ""))
            item.setdefault("url", orig.get("url", ""))
            item.setdefault("source", orig.get("source", ""))
        headlines = [item.get("headline", "") for item in rated]
        trend_scores = get_trend_scores(headlines)
        enriched = []
        for item in rated:
            score = float(item.get("rating", 0))
            if score < 3.8:
                continue
            trend_score = trend_scores.get(item.get("headline", ""), 0.2)
            rigorous = (
                (score * 0.55)
                + (_recency_score(item) * 1.8)
                + (trend_score * 0.8)
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

    # Fallback: try fallback domains if all primaries returned nothing usable
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
        send_message(
            TELEGRAM_CHAT_ID,
            f"⚠️ Slot skipped — no usable articles found for assigned domain *{assigned_domain}* "
            f"or any fallback domain. All sources exhausted for this scheduler run.",
            channel_id="news",
        )
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
