import pytest
from unittest.mock import patch, MagicMock


def _mock_model(json_text: str):
    m = MagicMock()
    m.generate_content.return_value.text = json_text
    return m


def test_generate_fact_topic_returns_title_and_premise(monkeypatch):
    from app.services import llm_service
    json_resp = '{"title": "Why do humans yawn when others yawn?", "premise": "Mirror neurons fire in response to observed behaviour — yawning spreads through social contagion in mammals."}'
    with patch.object(llm_service, "_get_model", return_value=_mock_model(json_resp)):
        result = llm_service.generate_fact_topic("psychology & dark psychology", recently_used_titles=[])
    assert result["title"] == "Why do humans yawn when others yawn?"
    assert "Mirror neurons" in result["premise"]


def test_generate_fact_topic_avoids_recently_used(monkeypatch):
    from app.services import llm_service
    json_resp = '{"title": "Fresh topic", "premise": "A brand new fact about the brain that nobody has covered before in this series of videos."}'
    used = ["Why do humans yawn when others yawn?"]
    with patch.object(llm_service, "_get_model", return_value=_mock_model(json_resp)):
        result = llm_service.generate_fact_topic("psychology & dark psychology", recently_used_titles=used)
    assert result["title"] == "Fresh topic"


def test_get_cta_narration_facts_en():
    from app.services.llm_service import get_cta_narration, _CTA_FACTS_EN
    cta = get_cta_narration(channel_id="stories", language="en")
    assert cta in _CTA_FACTS_EN
