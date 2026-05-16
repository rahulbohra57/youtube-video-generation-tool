# app/services/trends_service.py

import time
import logging

logger = logging.getLogger(__name__)

_NEUTRAL_DEFAULT = 0.2
_SLEEP_BETWEEN_QUERIES = 1.0


def get_trend_scores(topics: list[str]) -> dict[str, float]:
    """Return a 0.0–1.0 Google Trends interest score for each topic.

    Scores are normalised against the batch maximum so the hottest topic
    always scores 1.0. Topics with no Trends data score _NEUTRAL_DEFAULT.
    A 1-second sleep between queries avoids pytrends rate-limiting.
    Any exception returns _NEUTRAL_DEFAULT for all topics — the research
    pipeline continues using LLM rating + recency only.
    """
    if not topics:
        return {}
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=330)  # tz=330 is IST offset
        raw: dict[str, float] = {}
        for topic in topics:
            try:
                pytrends.build_payload([topic], timeframe="now 7-d")
                df = pytrends.interest_over_time()
                try:
                    raw[topic] = float(df[topic].mean())
                except Exception:
                    raw[topic] = 0.0
                time.sleep(_SLEEP_BETWEEN_QUERIES)
            except Exception:
                raw[topic] = 0.0
        max_val = max(raw.values()) if raw else 0.0
        if max_val <= 0:
            return {t: _NEUTRAL_DEFAULT for t in topics}
        return {
            t: round(raw.get(t, 0.0) / max_val, 3) or _NEUTRAL_DEFAULT
            for t in topics
        }
    except Exception as exc:
        logger.warning(f"trends_service: pytrends unavailable, using neutral defaults: {exc}")
        return {t: _NEUTRAL_DEFAULT for t in topics}
