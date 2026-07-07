from unittest.mock import MagicMock, patch


def test_tighten_if_too_long_preserves_visual_query_key():
    from app.agents.senior_script_reviewer import _tighten_if_too_long
    scenes = [
        {"scene": 1, "narration": " ".join(["word"] * 60), "visual_query": "city skyline sunrise"},
    ]
    out = _tighten_if_too_long(scenes, max_seconds=5)
    assert "visual_query" in out[0]
    assert out[0]["visual_query"] == "city skyline sunrise"
    assert "visual" not in out[0]


def test_tighten_if_too_long_preserves_visual_key_for_stories():
    from app.agents.senior_script_reviewer import _tighten_if_too_long
    scenes = [
        {"scene": 1, "narration": " ".join(["word"] * 60), "visual": "storybook illustration"},
    ]
    out = _tighten_if_too_long(scenes, max_seconds=5)
    assert "visual" in out[0]
    assert out[0]["visual"] == "storybook illustration"
    assert "visual_query" not in out[0]


def test_review_script_with_senior_reviewer_prompt_uses_visual_query_field():
    from app.services import llm_service

    def _make_model_mock(return_text: str):
        mock = MagicMock()
        mock.return_value.generate_content.return_value.text = return_text
        return mock

    mock = _make_model_mock('[{"scene":1,"narration":"n","visual_query":"q"}]')
    scenes = [{"scene": 1, "narration": "n", "visual_query": "city skyline"}]
    with patch("app.services.llm_service._get_model", mock):
        llm_service.review_script_with_senior_reviewer("topic", scenes, language="en")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert "scene, narration, visual_query" in prompt_used
    assert "short 3-7 word Pexels search phrases" in prompt_used
