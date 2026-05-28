# app/services/firestore_service.py

try:
    from google.cloud import firestore
    from google.api_core.exceptions import AlreadyExists
except Exception:
    class AlreadyExists(Exception):
        pass

    class _FirestoreUnavailable:
        class Query:
            DESCENDING = "DESCENDING"

        @staticmethod
        def transactional(fn):
            return fn

        class Client:
            def __init__(self, *args, **kwargs):
                raise RuntimeError(
                    "google-cloud-firestore is not installed or could not be imported."
                )

    firestore = _FirestoreUnavailable()
from datetime import datetime, timezone, timedelta
import hashlib

_db = None


def _get_db():
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def save_news_batch(batch_id: str, genre: str, items: dict):
    _get_db().collection("news_batches").document(batch_id).set({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "genre": genre,
        "status": "awaiting_reply",
        "items": items,
    })


def get_news_batch(batch_id: str) -> dict | None:
    doc = _get_db().collection("news_batches").document(batch_id).get()
    return doc.to_dict() if doc.exists else None


def update_batch_status(batch_id: str, status: str):
    _get_db().collection("news_batches").document(batch_id).update({"status": status})


def set_pipeline_state(batch_id: str, state: str, channel_id: str = "news"):
    _get_db().collection("pipeline_state").document(channel_id).set({
        "active_batch_id": batch_id,
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
    })


def set_pipeline_and_batch_state(batch_id: str, state: str, channel_id: str = "news"):
    """Update pipeline_state and news_batches status.

    Uses set+merge for news_batches so the write never fails on a missing document —
    pipeline_state is always cleared even if the batch doc was already cleaned up.
    """
    db = _get_db()
    wb = db.batch()
    wb.set(
        db.collection("news_batches").document(batch_id),
        {"status": state},
        merge=True,
    )
    wb.set(
        db.collection("pipeline_state").document(channel_id),
        {
            "active_batch_id": batch_id,
            "last_run_at": datetime.now(timezone.utc).isoformat(),
            "state": state,
        },
    )
    wb.commit()


def get_pipeline_state(channel_id: str = "news") -> dict:
    doc = _get_db().collection("pipeline_state").document(channel_id).get()
    if doc.exists:
        return doc.to_dict()
    # Migration: fall back to legacy "current" doc for news channel
    if channel_id == "news":
        legacy = _get_db().collection("pipeline_state").document("current").get()
        return legacy.to_dict() if legacy.exists else {}
    return {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def acquire_video_lock(owner: str, ttl_seconds: int = 1800, force: bool = False) -> bool:
    """Acquire a cross-instance video generation lock.

    Returns True only when this caller owns the lock.
    When force=True, unconditionally overwrites any existing lock (used by force_run).
    """
    db = _get_db()
    doc_ref = db.collection("locks").document("video_generation")
    now = _utc_now()
    expires_at = now.timestamp() + ttl_seconds
    payload = {
        "owner": owner,
        "acquired_at": now.isoformat(),
        "expires_at": expires_at,
    }

    if force:
        doc_ref.set(payload)
        return True

    try:
        doc_ref.create(payload)
        return True
    except AlreadyExists:
        pass

    transaction = db.transaction()

    @firestore.transactional
    def _steal_if_expired(tx):
        snap = doc_ref.get(transaction=tx)
        if snap.exists:
            current = snap.to_dict() or {}
            current_expires = current.get("expires_at")

            # Backward compatibility for any pre-existing lock doc that stored ISO timestamps.
            if isinstance(current_expires, str):
                parsed = _parse_iso(current_expires)
                current_expires = parsed.timestamp() if parsed else None

            if current_expires is None or current_expires > now.timestamp():
                return False

        tx.set(doc_ref, payload)
        return True

    return bool(_steal_if_expired(transaction))


def release_video_lock(owner: str) -> bool:
    """Release the lock only if the caller still owns it."""
    db = _get_db()
    doc_ref = db.collection("locks").document("video_generation")
    transaction = db.transaction()

    @firestore.transactional
    def _release_if_owner(tx):
        snap = doc_ref.get(transaction=tx)
        if not snap.exists:
            return False
        current = snap.to_dict() or {}
        if current.get("owner") != owner:
            return False
        tx.delete(doc_ref)
        return True

    return bool(_release_if_owner(transaction))


def save_youtube_tokens(tokens: dict, channel_id: str = "news"):
    _get_db().collection("oauth_tokens").document(f"youtube_{channel_id}").set(tokens)


def mark_auth_failure(channel_id: str) -> None:
    from datetime import datetime, timezone
    _get_db().collection("config").document(f"auth_failure_{channel_id}").set({
        "failed_at": datetime.now(timezone.utc).isoformat()
    })


def is_auth_recently_failed(channel_id: str, hours: int = 23) -> bool:
    from datetime import datetime, timezone, timedelta
    doc = _get_db().collection("config").document(f"auth_failure_{channel_id}").get()
    if not doc.exists:
        return False
    failed_at_str = (doc.to_dict() or {}).get("failed_at")
    if not failed_at_str:
        return False
    try:
        failed_at = datetime.fromisoformat(failed_at_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.now(timezone.utc) - failed_at < timedelta(hours=hours)
    except Exception:
        return False


def clear_auth_failure(channel_id: str) -> None:
    _get_db().collection("config").document(f"auth_failure_{channel_id}").delete()


def get_youtube_tokens(channel_id: str = "news") -> dict | None:
    doc = _get_db().collection("oauth_tokens").document(f"youtube_{channel_id}").get()
    if doc.exists:
        return doc.to_dict()
    # Migration: fall back to legacy "youtube" doc for news channel
    if channel_id == "news":
        legacy = _get_db().collection("oauth_tokens").document("youtube").get()
        return legacy.to_dict() if legacy.exists else None
    return None


def _headline_key(headline: str) -> str:
    normalized = " ".join((headline or "").strip().lower().split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def is_headline_already_suggested(headline: str, ttl_days: int = 14, channel_id: str = "news") -> bool:
    """Return True only if this headline was suggested within the last ttl_days.

    Headlines older than ttl_days are treated as fresh so the topic can be
    revisited if there's a new development.
    Stories use a prefixed doc key ("stories_<hash>") to avoid colliding with news.
    """
    prefix = "stories_" if channel_id == "stories" else ""
    key = f"{prefix}{_headline_key(headline)}"
    doc = _get_db().collection("suggested_headlines").document(key).get()
    if not doc.exists:
        return False
    data = doc.to_dict() or {}
    suggested_at = _parse_iso(data.get("suggested_at"))
    if suggested_at is None:
        return True  # legacy doc without timestamp — treat as still blocked
    age_days = (_utc_now() - suggested_at).total_seconds() / 86400
    return age_days < ttl_days


def mark_headline_suggested(headline: str, genre: str = "", channel_id: str = "news"):
    prefix = "stories_" if channel_id == "stories" else ""
    key = f"{prefix}{_headline_key(headline)}"
    _get_db().collection("suggested_headlines").document(key).set({
        "headline": headline,
        "genre": genre,
        "channel_id": channel_id,
        "suggested_at": datetime.now(timezone.utc).isoformat(),
    }, merge=True)


def get_recently_suggested_headlines(
    days: int = 14,
    limit: int = 20,
    channel_id: str | None = None,
) -> list[str]:
    """Return headlines suggested within the last `days` days, newest first.

    Used to detect content fatigue — passed to the LLM so it can penalize
    articles that cover the same story as something recently produced.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query = (
            _get_db()
            .collection("suggested_headlines")
            .where("suggested_at", ">=", cutoff)
        )
        if channel_id:
            query = query.where("channel_id", "==", channel_id)
        docs = (
            query
            .order_by("suggested_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [
            (d.to_dict() or {}).get("headline", "")
            for d in docs
            if (d.to_dict() or {}).get("headline")
        ]
    except Exception:
        return []


def create_or_update_job(job_id: str, payload: dict):
    try:
        doc_ref = _get_db().collection("jobs").document(job_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        doc_ref.set({
            **payload,
            "updated_at": now_iso,
        }, merge=True)
    except Exception:
        return


def get_job(job_id: str) -> dict | None:
    try:
        doc = _get_db().collection("jobs").document(job_id).get()
        return doc.to_dict() if doc.exists else None
    except Exception:
        return None


def get_job_by_public_id(public_id: str) -> tuple[str, dict] | tuple[None, None]:
    try:
        docs = (
            _get_db()
            .collection("jobs")
            .where("public_id", "==", public_id)
            .limit(1)
            .stream()
        )
        for d in docs:
            data = d.to_dict() or {}
            data["job_id"] = d.id
            return d.id, data
        return None, None
    except Exception:
        return None, None


def request_job_cancel(job_id: str, requested_by: str = "telegram") -> bool:
    try:
        doc_ref = _get_db().collection("jobs").document(job_id)
        snap = doc_ref.get()
        if not snap.exists:
            return False
        payload = snap.to_dict() or {}
        doc_ref.set(
            {
                "cancel_requested": True,
                "cancel_requested_by": requested_by,
                "cancel_requested_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "status": payload.get("status", "processing"),
            },
            merge=True,
        )
        return True
    except Exception:
        return False


def list_recent_jobs(limit: int = 50) -> list[dict]:
    docs = (
        _get_db()
        .collection("jobs")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    rows = []
    for d in docs:
        data = d.to_dict() or {}
        data["job_id"] = d.id
        rows.append(data)
    return rows


def get_queue_snapshot(window_start=None, channel_id: str | None = None) -> dict:
    rows = list_recent_jobs(limit=200)
    if channel_id:
        rows = [r for r in rows if r.get("channel_id", "news") == channel_id]
    queued = sum(1 for r in rows if r.get("status") == "queued")
    processing = sum(1 for r in rows if r.get("status") == "processing")
    failed_24h = 0
    completed_24h = 0
    window_start_ts = (window_start or _ist_window_start()).timestamp()
    for r in rows:
        updated = _parse_iso(r.get("updated_at"))
        if not updated:
            continue
        if updated.timestamp() < window_start_ts:
            continue
        if r.get("status") == "failed":
            failed_24h += 1
        if r.get("status") == "completed":
            completed_24h += 1
    return {
        "queued": queued,
        "processing": processing,
        "failed_24h": failed_24h,
        "completed_24h": completed_24h,
    }


def record_quota_event(kind: str, details: str = "", channel_id: str = ""):
    try:
        doc = {
            "kind": kind,
            "details": details[:500],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if channel_id:
            doc["channel_id"] = channel_id
        _get_db().collection("quota_events").add(doc)
    except Exception:
        return


def get_quota_usage_snapshot() -> dict:
    docs = (
        _get_db()
        .collection("quota_events")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(500)
        .stream()
    )
    now_ts = datetime.now(timezone.utc).timestamp()
    events = [d.to_dict() or {} for d in docs]

    def _count(kind: str, window_s: int) -> int:
        n = 0
        for e in events:
            if e.get("kind") != kind:
                continue
            ts = _parse_iso(e.get("created_at"))
            if not ts:
                continue
            if now_ts - ts.timestamp() <= window_s:
                n += 1
        return n

    images_1m = _count("image_success", 60)
    images_5m = _count("image_success", 5 * 60)
    quota_err_24h = _count("image_quota_error", 24 * 3600)
    if quota_err_24h == 0 and images_5m <= 15:
        pressure = "low"
    elif quota_err_24h <= 3:
        pressure = "medium"
    else:
        pressure = "high"

    return {
        "images_generated_1m": images_1m,
        "images_generated_5m": images_5m,
        "quota_errors_24h": quota_err_24h,
        "pressure": pressure,
    }


def get_current_lock() -> dict:
    doc = _get_db().collection("locks").document("video_generation").get()
    return doc.to_dict() if doc.exists else {}


def force_release_video_lock():
    """Forcibly delete the video generation lock regardless of owner.

    Used by the STOP command so the next scheduled video is not blocked by
    a lock still held by the cancelled generator.
    """
    try:
        _get_db().collection("locks").document("video_generation").delete()
    except Exception:
        return


def save_social_metrics(platform: str, payload: dict):
    _get_db().collection("social_metrics").document(platform).set({
        **payload,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, merge=True)


def get_social_metrics(platform: str) -> dict:
    doc = _get_db().collection("social_metrics").document(platform).get()
    return doc.to_dict() if doc.exists else {}


def _idempotency_doc_id(scope: str, key: str) -> str:
    raw = f"{scope}:{key}".strip().lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def acquire_idempotency_key(scope: str, key: str, ttl_seconds: int = 900, metadata: dict | None = None) -> tuple[bool, dict]:
    """Acquire an idempotency key atomically.

    Returns (acquired, current_record).
    """
    db = _get_db()
    doc_ref = db.collection("idempotency_keys").document(_idempotency_doc_id(scope, key))
    now = _utc_now()
    payload = {
        "scope": scope,
        "key": key,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "expires_at": (now.timestamp() + ttl_seconds),
    }
    if metadata:
        payload.update(metadata)

    try:
        doc_ref.create(payload)
        return True, payload
    except AlreadyExists:
        pass

    transaction = db.transaction()

    @firestore.transactional
    def _acquire_if_expired(tx):
        snap = doc_ref.get(transaction=tx)
        current = snap.to_dict() or {}
        current_expires = current.get("expires_at")
        if isinstance(current_expires, str):
            parsed = _parse_iso(current_expires)
            current_expires = parsed.timestamp() if parsed else None

        if current_expires and current_expires > now.timestamp():
            return False, current

        tx.set(doc_ref, payload)
        return True, payload

    acquired, record = _acquire_if_expired(transaction)
    return bool(acquired), record or {}


def update_idempotency_key(scope: str, key: str, fields: dict):
    doc_ref = _get_db().collection("idempotency_keys").document(_idempotency_doc_id(scope, key))
    doc_ref.set(
        {
            **fields,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        merge=True,
    )


def is_duplicate_telegram_update(update_id: int, channel: str) -> bool:
    """Return True if this Telegram update_id was already processed (within 5 minutes).

    Uses idempotency_keys collection so dedup survives cold starts — allows min-instances=0.
    """
    scope = f"tg_update_{channel}"
    acquired, _ = acquire_idempotency_key(scope, str(update_id), ttl_seconds=300)
    return not acquired


def mark_scene_checkpoint(
    job_id: str,
    scene_idx: int,
    status: str,
    audio_path: str = "",
    image_path: str = "",
    retries_audio: int = 0,
    retries_image: int = 0,
    error: str = "",
):
    try:
        job = get_job(job_id) or {}
        scene_progress = job.get("scene_progress", {})
        scene_progress[str(scene_idx)] = {
            "status": status,
            "audio_path": audio_path,
            "image_path": image_path,
            "retries_audio": retries_audio,
            "retries_image": retries_image,
            "error": error[:500],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        create_or_update_job(
            job_id,
            {
                "scene_progress": scene_progress,
                "last_scene_idx": scene_idx,
            },
        )
    except Exception:
        return


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ist_window_start() -> datetime:
    """Return the start of the current 8am-to-8am IST report window as a UTC datetime."""
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    today_8am_ist = now_ist.replace(hour=8, minute=0, second=0, microsecond=0)
    if now_ist >= today_8am_ist:
        return today_8am_ist.astimezone(timezone.utc)
    return (today_8am_ist - timedelta(days=1)).astimezone(timezone.utc)


def _ist_report_day_key() -> str:
    """Date key that resets at 8am IST — aligns with the daily digest window."""
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    if now_ist >= now_ist.replace(hour=8, minute=0, second=0, microsecond=0):
        return now_ist.strftime("%Y-%m-%d")
    return (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")


def get_domains_posted_today(day_key=None) -> dict:
    try:
        doc = _get_db().collection("daily_domain_posts").document(day_key or _ist_report_day_key()).get()
        if not doc.exists:
            return {}
        data = doc.to_dict() or {}
        return data.get("domains", {}) or {}
    except Exception:
        return {}


def save_playlist_id(playlist_name: str, playlist_id: str):
    _get_db().collection("playlists").document(playlist_name).set({
        "playlist_id": playlist_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_playlist_id(playlist_name: str) -> str | None:
    doc = _get_db().collection("playlists").document(playlist_name).get()
    return (doc.to_dict() or {}).get("playlist_id") if doc.exists else None


def update_job_analytics(job_id: str, analytics: dict):
    create_or_update_job(job_id, {
        "analytics": analytics,
        "analytics_updated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_top_performers(n: int = 3, days: int | None = None, channel_id: str = "news") -> list[dict]:
    """Return top n completed jobs by view count that have analytics data.

    If days is provided, only jobs created within the last `days` days are considered
    (uses created_at so analytics refreshes on old videos don't skew results).
    """
    try:
        all_jobs = list_recent_jobs(limit=200)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)) if days else None
        performers = []
        for data in all_jobs:
            if data.get("status") != "completed":
                continue
            if data.get("channel_id", "news") != channel_id:
                continue
            if cutoff:
                created = _parse_iso(data.get("created_at"))
                if not created or created < cutoff:
                    continue
            analytics = data.get("analytics") or {}
            views = int(analytics.get("view_count", 0))
            if views > 0:
                performers.append({
                    "topic": data.get("topic", ""),
                    "genre": data.get("genre", ""),
                    "view_count": views,
                    "like_count": int(analytics.get("like_count", 0)),
                })
        return sorted(performers, key=lambda x: x["view_count"], reverse=True)[:n]
    except Exception:
        return []


def get_failed_auto_jobs(max_age_hours: int = 12) -> list[dict]:
    """Return recent auto-generated failed jobs that haven't been retried yet."""
    try:
        all_jobs = list_recent_jobs(limit=50)
        now_ts = datetime.now(timezone.utc).timestamp()
        result = []
        for data in all_jobs:
            if data.get("status") not in ("failed",):
                continue
            # Exclude jobs already handled via Telegram delivery
            if data.get("status") == "delivered_manual":
                continue
            if data.get("error") == "youtube_quota_exceeded":
                continue
            if data.get("source") not in ("researcher",):
                continue
            if data.get("retry_attempted"):
                continue
            updated = _parse_iso(data.get("updated_at"))
            if not updated:
                continue
            if (now_ts - updated.timestamp()) / 3600 > max_age_hours:
                continue
            result.append(data)
        return result
    except Exception:
        return []


def get_gnews_calls_today(window_start=None) -> int:
    """Return GNews API calls since 8am IST today (avoids composite index by filtering in Python)."""
    try:
        window_start_ts = (window_start or _ist_window_start()).timestamp()
        docs = (
            _get_db()
            .collection("quota_events")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(500)
            .stream()
        )
        count = 0
        for d in docs:
            data = d.to_dict() or {}
            if data.get("kind") != "gnews_call":
                continue
            ts = _parse_iso(data.get("created_at"))
            if ts and ts.timestamp() >= window_start_ts:
                count += 1
        return count
    except Exception:
        return 0


def get_tts_chars_today(window_start=None, channel_id: str = "") -> int:
    """Return total TTS chars synthesized since 8am IST today (avoids composite index by filtering in Python)."""
    try:
        window_start_ts = (window_start or _ist_window_start()).timestamp()
        docs = (
            _get_db()
            .collection("quota_events")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(500)
            .stream()
        )
        total = 0
        for d in docs:
            data = d.to_dict() or {}
            if data.get("kind") != "tts_chars":
                continue
            if channel_id:
                event_channel = data.get("channel_id", "")
                # Events recorded before channel_id was added have no field — treat
                # them as "news" (the only channel that existed at that time).
                effective_channel = event_channel if event_channel else "news"
                if effective_channel != channel_id:
                    continue
            ts = _parse_iso(data.get("created_at"))
            if not ts or ts.timestamp() < window_start_ts:
                continue
            try:
                total += int(data.get("details", "0") or "0")
            except (ValueError, TypeError):
                pass
        return total
    except Exception:
        return 0


def get_tts_chars_this_month(channel_id: str = "") -> int:
    """Return total TTS chars synthesized since the 1st of the current UTC month.

    Uses the same quota_events collection as get_tts_chars_today but with a
    month-level window so the daily digest shows actual cumulative usage, not
    a single-day extrapolation.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        month_start_ts = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        docs = (
            _get_db()
            .collection("quota_events")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(2000)
            .stream()
        )
        total = 0
        for d in docs:
            data = d.to_dict() or {}
            ts = _parse_iso(data.get("created_at"))
            if not ts:
                continue
            if ts.timestamp() < month_start_ts:
                break  # descending order — everything remaining is older
            if data.get("kind") != "tts_chars":
                continue
            if channel_id:
                event_channel = data.get("channel_id", "")
                effective_channel = event_channel if event_channel else "news"
                if effective_channel != channel_id:
                    continue
            try:
                total += int(data.get("details", "0") or "0")
            except (ValueError, TypeError):
                pass
        return total
    except Exception:
        return 0


def mark_domain_posted_today(domain: str, job_id: str = "", headline: str = ""):
    try:
        key = (domain or "").strip().lower()
        if not key:
            return
        doc_ref = _get_db().collection("daily_domain_posts").document(_ist_report_day_key())
        current = get_domains_posted_today()
        current[key] = {
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "headline": headline,
        }
        doc_ref.set(
            {
                "date": _ist_report_day_key(),
                "domains": current,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            merge=True,
        )
    except Exception:
        return


def get_genre_performance_weekly() -> dict[str, float]:
    """Return average view_count per genre over the last 7 days.

    Only considers completed jobs that have analytics data.
    Returns {genre_lower: avg_views}. Genres with no data are absent from the dict.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
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


def get_genre_performance_fortnightly(channel_id: str | None = None) -> dict[str, float]:
    """Return average view_count per genre over the last 14 days.

    Only considers completed jobs that have analytics data.
    Returns {genre_lower: avg_views}. Genres with no data are absent from the dict.
    Pass channel_id to scope results to a single channel ("news" or "stories").
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        query = (
            _get_db()
            .collection("jobs")
            .where("status", "==", "completed")
            .where("updated_at", ">=", cutoff)
        )
        if channel_id:
            query = query.where("channel_id", "==", channel_id)
        totals: dict[str, list[int]] = {}
        for d in query.stream():
            data = d.to_dict() or {}
            genre = (data.get("genre") or "").strip().lower()
            analytics = data.get("analytics") or {}
            views = int(analytics.get("view_count", 0))
            if genre:
                totals.setdefault(genre, []).append(views)
        return {g: sum(v) / len(v) for g, v in totals.items()}
    except Exception:
        return {}


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
