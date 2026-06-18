# tests/test_llm_long_script.py
import json
import pytest
from unittest.mock import MagicMock, patch


def _make_model_mock(return_text: str):
    mock = MagicMock()
    mock.return_value.generate_content.return_value.text = return_text
    return mock


def _make_scene(i: int, segment: str = "core") -> dict:
    return {
        "scene": i,
        "segment": segment,
        "narration": "A " * 45,  # ~45 words
        "visual_query": "ocean waves nature",
    }


def test_generate_long_facts_script_returns_json_with_scenes():
    scenes = [_make_scene(i, "hook" if i <= 2 else ("cta" if i == 24 else "core")) for i in range(1, 25)]
    mock_get_model = _make_model_mock(json.dumps(scenes))

    with patch("app.services.llm_service._get_model", mock_get_model), \
         patch("app.services.llm_service._init_search_model", side_effect=Exception("no search")):
        from app.services.llm_service import generate_long_facts_script
        result = generate_long_facts_script(
            "Why do cats purr",
            category="science & space",
            premise="Cats produce purring sounds in a surprising way",
        )

    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert len(parsed) == 24
    assert parsed[0]["segment"] == "hook"
    assert "visual_query" in parsed[0]
    assert "narration" in parsed[0]


def test_generate_long_facts_script_includes_topic_in_prompt():
    mock_get_model = _make_model_mock("[]")

    with patch("app.services.llm_service._get_model", mock_get_model), \
         patch("app.services.llm_service._init_search_model", side_effect=Exception("no search")):
        from app.services.llm_service import generate_long_facts_script
        generate_long_facts_script("Octopus intelligence facts", category="human body & biology")

    prompt = mock_get_model.return_value.generate_content.call_args[0][0]
    assert "Octopus intelligence facts" in prompt
    assert "visual_query" in prompt
    assert "hook" in prompt
    assert "24" in prompt


def test_generate_long_facts_script_falls_back_to_standard_model_on_search_failure():
    mock_get_model = _make_model_mock("[]")

    with patch("app.services.llm_service._get_model", mock_get_model), \
         patch("app.services.llm_service._init_search_model", side_effect=Exception("search unavailable")):
        from app.services.llm_service import generate_long_facts_script
        result = generate_long_facts_script("test topic")

    assert result == "[]"
    mock_get_model.return_value.generate_content.assert_called_once()
