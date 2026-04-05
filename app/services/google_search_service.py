# app/services/google_search_service.py

import logging
import httpx
from app.config import GOOGLE_SEARCH_API_KEY, GOOGLE_SEARCH_ENGINE_ID

logger = logging.getLogger(__name__)

_SEARCH_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
_TIMEOUT_SECONDS = 10


def search_news(query: str, max_results: int = 5, date_restrict: str = "w1") -> list[dict]:
    """Search for recent web results via Google Custom Search JSON API.

    Args:
        query:        Search query string.
        max_results:  Number of results (max 10 per API call).
        date_restrict: Restrict to recent results — "d3" (3 days), "w1" (1 week, default),
                       "m1" (1 month).

    Returns:
        List of dicts with keys: headline, description, url, published_at.
        Returns [] on any error so callers can fall back gracefully.
    """
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        logger.warning("GOOGLE_SEARCH_API_KEY or GOOGLE_SEARCH_ENGINE_ID not set — skipping Google search")
        return []

    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": GOOGLE_SEARCH_ENGINE_ID,
        "q": query,
        "num": min(max_results, 10),
        "dateRestrict": date_restrict,
    }

    try:
        response = httpx.get(
            _SEARCH_ENDPOINT,
            params=params,
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.warning("Google Custom Search API error: %s", exc)
        return []

    items = data.get("items") or []
    articles = []
    for item in items:
        # Extract published date from page metadata if available
        metatags = (item.get("pagemap") or {}).get("metatags") or [{}]
        published_at = (
            metatags[0].get("article:published_time")
            or metatags[0].get("og:updated_time")
            or metatags[0].get("date")
            or ""
        )
        articles.append({
            "headline": item.get("title", ""),
            "description": item.get("snippet", "").replace("\n", " ").strip(),
            "url": item.get("link", ""),
            "published_at": published_at,
        })
    return articles
