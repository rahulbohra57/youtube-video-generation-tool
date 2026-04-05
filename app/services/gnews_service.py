# app/services/gnews_service.py

import logging
import httpx
from app.config import GNEWS_API_KEY

logger = logging.getLogger(__name__)

_GNEWS_URL = "https://gnews.io/api/v4/top-headlines"
_GNEWS_SEARCH_URL = "https://gnews.io/api/v4/search"
_DAILY_CALL_LIMIT = 80  # circuit-breaker at 80% of 100 free-tier limit


def _map_articles(data: dict) -> list[dict]:
    return [
        {
            "headline": a.get("title", ""),
            "url": a.get("url", ""),
            "description": a.get("description", ""),
            "published_at": a.get("publishedAt", ""),
            "source": (a.get("source") or {}).get("name", ""),
        }
        for a in data.get("articles", [])
        if a.get("title") and a.get("url")
    ]


def fetch_top_headlines(
    category: str = "technology",
    max_results: int = 10,
    query: str | None = None,
) -> list[dict]:
    from app.services import firestore_service
    if firestore_service.get_gnews_calls_today() >= _DAILY_CALL_LIMIT:
        logger.warning(f"GNews circuit-breaker: {_DAILY_CALL_LIMIT} calls reached today, skipping fetch_top_headlines.")
        return []
    params = {
        "category": category,
        "lang": "en",
        "max": max_results,
        "token": GNEWS_API_KEY,
    }
    if query:
        params["q"] = query
    response = httpx.get(_GNEWS_URL, params=params, timeout=10)
    response.raise_for_status()
    firestore_service.record_quota_event("gnews_call")
    return _map_articles(response.json())


def search_news(
    query: str,
    max_results: int = 10,
    from_date: str | None = None,
    category: str | None = None,
) -> list[dict]:
    from app.services import firestore_service
    calls_today = firestore_service.get_gnews_calls_today()
    if calls_today >= _DAILY_CALL_LIMIT:
        logger.warning(f"GNews circuit-breaker: {calls_today} calls today (limit {_DAILY_CALL_LIMIT}), skipping search.")
        return []
    params = {
        "q": query,
        "lang": "en",
        "max": max_results,
        "token": GNEWS_API_KEY,
        "sortby": "publishedAt",
    }
    if from_date:
        params["from"] = from_date
    if category:
        params["topic"] = category
    response = httpx.get(_GNEWS_SEARCH_URL, params=params, timeout=10)
    response.raise_for_status()
    firestore_service.record_quota_event("gnews_call")
    return _map_articles(response.json())
