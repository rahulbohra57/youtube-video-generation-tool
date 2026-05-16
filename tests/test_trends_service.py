import sys
import time
from unittest.mock import MagicMock, patch


def _make_mock_df(values_by_topic: dict[str, float]) -> MagicMock:
    """Build a mock DataFrame where df[topic].mean() returns the given value."""
    mock_df = MagicMock()

    def getitem(key):
        series = MagicMock()
        series.mean.return_value = values_by_topic.get(key, 0.0)
        return series

    mock_df.__getitem__ = MagicMock(side_effect=getitem)
    return mock_df


def _install_mock_trend_req(df_by_topic: dict[str, float]):
    """Patch sys.modules["pytrends.request"].TrendReq to return a mock that
    yields df_by_topic values from interest_over_time()."""
    mock_instance = MagicMock()
    mock_instance.interest_over_time.return_value = _make_mock_df(df_by_topic)
    sys.modules["pytrends.request"].TrendReq = MagicMock(return_value=mock_instance)
    return mock_instance


@patch("time.sleep")
def test_get_trend_scores_normalises_to_one(mock_sleep):
    """Highest-interest topic scores 1.0; others are proportionally lower."""
    _install_mock_trend_req({"AI news": 100.0, "Tech update": 50.0})

    from app.services import trends_service
    scores = trends_service.get_trend_scores(["AI news", "Tech update"])

    assert scores["AI news"] == 1.0
    assert 0.0 < scores["Tech update"] < 1.0
    assert scores["Tech update"] == 0.5


@patch("time.sleep")
def test_get_trend_scores_returns_neutral_for_zero_interest(mock_sleep):
    """Topics with 0 mean interest get the neutral default 0.2, not 0.0."""
    _install_mock_trend_req({"Popular topic": 80.0, "No data topic": 0.0})

    from app.services import trends_service
    scores = trends_service.get_trend_scores(["Popular topic", "No data topic"])

    assert scores["No data topic"] == 0.2


@patch("time.sleep")
def test_get_trend_scores_all_zero_returns_neutral_default(mock_sleep):
    """When all topics have 0 interest, every score is the neutral default."""
    _install_mock_trend_req({"Topic A": 0.0, "Topic B": 0.0})

    from app.services import trends_service
    scores = trends_service.get_trend_scores(["Topic A", "Topic B"])

    assert scores["Topic A"] == 0.2
    assert scores["Topic B"] == 0.2


@patch("time.sleep")
def test_get_trend_scores_falls_back_on_pytrends_exception(mock_sleep):
    """Any pytrends exception returns neutral default for all topics."""
    sys.modules["pytrends.request"].TrendReq = MagicMock(
        side_effect=Exception("network error")
    )

    from app.services import trends_service
    scores = trends_service.get_trend_scores(["Some topic"])

    assert scores["Some topic"] == 0.2


def test_get_trend_scores_empty_input():
    """Empty input returns empty dict without calling pytrends."""
    from app.services import trends_service
    assert trends_service.get_trend_scores([]) == {}


@patch("time.sleep")
def test_get_trend_scores_sleeps_between_queries(mock_sleep):
    """Verifies sleep is called once per topic to avoid rate-limiting."""
    _install_mock_trend_req({"Topic A": 50.0, "Topic B": 30.0})

    from app.services import trends_service
    trends_service.get_trend_scores(["Topic A", "Topic B"])

    assert mock_sleep.call_count == 2
