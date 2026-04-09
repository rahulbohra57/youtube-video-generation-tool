# app/agents/lead_researcher.py

from datetime import datetime, timezone, timedelta
from app.services import gnews_service, firestore_service
from app.services.llm_service import rate_and_select_news
from app.services.telegram_service import send_message
from app.config import TELEGRAM_CHAT_ID


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
    tts_chars_today = firestore_service.get_tts_chars_today(window_start=prev_window_start)
    tts_chars_month = firestore_service.get_tts_chars_this_month()
    tts_pct = round((tts_chars_month / 1_000_000) * 100, 1)

    # GNews calls in previous window
    gnews_today = firestore_service.get_gnews_calls_today(window_start=prev_window_start)

    all_domains = ["technology", "artificial intelligence", "current affairs", "trending", "science"]
    domains_today = firestore_service.get_domains_posted_today(day_key=prev_day_key)
    domain_lines = []
    for d in all_domains:
        mark = "✅" if d in domains_today else "⬜"
        domain_lines.append(f"  {mark} {d.title()}")

    top = firestore_service.get_top_performers(n=1)
    top_line = ""
    if top:
        t = top[0]
        top_line = f"\n\n🏆 Top video: _{t['topic']}_ ({t['view_count']:,} views)"

    # Failed jobs awaiting manual re-send
    failed_jobs = firestore_service.get_failed_auto_jobs(max_age_hours=24)
    delivered_manual = [
        j for j in firestore_service.list_recent_jobs(limit=50)
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
        f"🗂️ Domain Coverage Today\n"
        + "\n".join(domain_lines)
        + top_line
        + failed_lines
    )
    send_message(TELEGRAM_CHAT_ID, message, channel_id="news")


def retry_failed_pipeline() -> str | None:
    """Find the most recent failed auto-generated job and re-enqueue it."""
    state = firestore_service.get_pipeline_state() or {}
    if state.get("state") == "processing":
        return None

    failed_jobs = firestore_service.get_failed_auto_jobs(max_age_hours=12)
    if not failed_jobs:
        return None

    job = failed_jobs[0]
    topic = job.get("topic", "")
    genre = job.get("genre", "")
    details = job.get("details", "")
    original_job_id = job.get("job_id", "")

    if not topic:
        return None

    firestore_service.create_or_update_job(original_job_id, {"retry_attempted": True})

    batch_id = f"retry_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    prefix = _prefix_for_domain(genre)
    code = f"{prefix}01"

    firestore_service.save_news_batch(batch_id, (genre or "general").lower(), {
        code: {"code": code, "headline": topic, "context": details, "rating": 4.5, "genre": genre}
    })
    firestore_service.set_pipeline_and_batch_state(batch_id, "processing")

    from app.agents import whatsapp_agent
    task_name = whatsapp_agent._task_name(batch_id, code)
    public_id = whatsapp_agent._public_video_id(task_name)

    send_message(TELEGRAM_CHAT_ID, f"🔁 Auto-retrying failed pipeline\nTopic: _{topic}_", channel_id="news")

    enqueued = whatsapp_agent._enqueue_generate(
        topic, code, batch_id,
        public_id=public_id, genre=genre, details=details, source="retry",
    )
    if not enqueued:
        firestore_service.set_pipeline_and_batch_state(batch_id, "failed")
        return None

    return batch_id


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
    domains = _primary_domain_query_map()
    domain_results: dict[str, list[dict]] = {}

    top_performers = firestore_service.get_top_performers(n=3)
    recently_covered = firestore_service.get_recently_suggested_headlines(days=14, limit=20)

    for domain, cfg in domains.items():
        # One search call per domain (sorted by publishedAt already covers top headlines).
        # Avoids the previous 10-call pattern (fetch_top + search × 5 domains) that
        # could exhaust the GNews free-tier quota (~100 req/day) within a few hours.
        search = gnews_service.search_news(
            query=cfg["query"],
            max_results=25,
            from_date=from_date,
            category=cfg["category"],
        )
        raw = [a for a in search if _is_recent_article(a, lookback_hours)]
        candidates = _dedupe_and_filter_unsuggested(raw)
        if not candidates:
            domain_results[domain] = []
            continue
        # Build lookup so we can re-attach published_at / url that rate_and_select_news drops
        _orig_lookup = {_norm_headline(c.get("headline", "")): c for c in candidates}
        rated = rate_and_select_news(candidates, top_performers=top_performers, recently_covered=recently_covered)[:5]
        for item in rated:
            orig = _orig_lookup.get(_norm_headline(item.get("headline", ""))) or {}
            item.setdefault("published_at", orig.get("published_at", ""))
            item.setdefault("url", orig.get("url", ""))
            item.setdefault("source", orig.get("source", ""))
        enriched = []
        for item in rated:
            h = item.get("headline", "")
            score = float(item.get("rating", 0))
            rigorous = (score * 0.60) + (_recency_score(item) * 2.0) + _trend_bonus(h)
            if score < 3.8:
                continue
            enriched.append(
                {
                    **item,
                    "genre": domain,
                    "rigorous_score": round(min(5.0, rigorous), 2),
                }
            )
        domain_results[domain] = sorted(enriched, key=lambda x: x.get("rigorous_score", 0), reverse=True)[:5]

    # Cross-domain deduplication: same story (e.g. "OpenAI launches X") can surface
    # in both Technology and Artificial Intelligence. Keep the highest-scored copy only.
    seen_cross: set[str] = set()
    for d in list(domain_results.keys()):
        unique = []
        for item in domain_results[d]:
            key = _norm_headline(item.get("headline", ""))
            if key and key not in seen_cross:
                seen_cross.add(key)
                unique.append(item)
        domain_results[d] = unique

    today_posted = firestore_service.get_domains_posted_today()
    missing_domains = [d for d in domains.keys() if d.lower() not in today_posted]

    # Weekly genre performance — avg views per genre over last 7 days.
    # Used to weight domain selection probability; every missing domain stays eligible.
    genre_perf = firestore_service.get_genre_performance_weekly()
    # Baseline weight 1.0 ensures domains with no history are always selectable.
    # Add 1.0 to the avg-view score so even a 0-view genre gets weight 1.0, not 0.
    _BASE = 1.0

    selected_domain = ""
    selected_item = None
    import random

    # Phase 1: weighted selection from domains not yet posted today (mandatory coverage).
    # Build a weighted-shuffle by repeatedly picking with weights, without replacement.
    pool = [(d, _BASE + genre_perf.get(d.lower(), 0.0)) for d in missing_domains]
    while pool:
        domains_list, w_list = zip(*pool)
        pick = random.choices(domains_list, weights=w_list, k=1)[0]
        pool = [(d, w) for d, w in pool if d != pick]
        if domain_results.get(pick):
            selected_domain = pick
            selected_item = domain_results[pick][0]
            break

    # Phase 2: all domains covered today — pick extra video weighted by
    # blended score (rigorous_score * genre performance multiplier).
    if not selected_item:
        all_candidates = []
        for d, rows in domain_results.items():
            if rows:
                all_candidates.append((d, rows[0]))
        if not all_candidates:
            # Phase 3: all primary domains exhausted — try a fallback domain.
            # Fallback domains are fetched live here (not during the primary loop)
            # to avoid wasting GNews quota on every cycle.
            fallback_domains = _fallback_domain_query_map()
            fallback_names = list(fallback_domains.keys())
            random.shuffle(fallback_names)
            for fallback_name in fallback_names:
                cfg = fallback_domains[fallback_name]
                fallback_search = gnews_service.search_news(
                    query=cfg["query"],
                    max_results=25,
                    from_date=from_date,
                    category=cfg["category"],
                )
                fallback_raw = [a for a in fallback_search if _is_recent_article(a, lookback_hours)]
                fallback_candidates = _dedupe_and_filter_unsuggested(fallback_raw)
                if fallback_candidates:
                    selected_domain = fallback_name
                    # No quality floor — any article is acceptable in last-resort fallback.
                    selected_item = {
                        **fallback_candidates[0],
                        "genre": fallback_name,
                        "rigorous_score": 3.5,
                    }
                    break
            if not selected_item:
                return None
        else:
            perf_max = max(genre_perf.values(), default=1.0) or 1.0
            selected_domain, selected_item = sorted(
                all_candidates,
                key=lambda pair: pair[1].get("rigorous_score", 0)
                * (1 + genre_perf.get(pair[0].lower(), 0.0) / perf_max),
                reverse=True,
            )[0]

    batch_id = f"auto_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    prefix = _prefix_for_domain(selected_domain)
    code = f"{prefix}01"
    selected_item["code"] = code
    items = _assign_codes(domain_results.get(selected_domain, [selected_item]), prefix)

    firestore_service.save_news_batch(batch_id, selected_domain.lower(), items)
    firestore_service.set_pipeline_and_batch_state(batch_id, "processing")

    from app.agents import whatsapp_agent
    task_name = whatsapp_agent._task_name(batch_id, code)  # shared deterministic id
    public_id = whatsapp_agent._public_video_id(task_name)
    context_summary = selected_item.get("context") or selected_item.get("description") or "Top trending story selected."
    pub_date = selected_item.get("published_at", "")
    source_url = selected_item.get("url", "")
    # Prepend the GNews publication date + URL so the script generator knows the exact event date
    # and can instruct Google Search grounding to find the right (recent) version of the story.
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
