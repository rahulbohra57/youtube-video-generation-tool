from unittest.mock import MagicMock, patch


def _make_model_mock(return_text: str):
    mock = MagicMock()
    mock.return_value.generate_content.return_value.text = return_text
    return mock


# ── generate_script tests ────────────────────────────────────────────────────

def test_generate_script_default_is_16x9():
    """With no aspect_ratio arg, prompt targets 16:9 (landscape) format hints."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"img"}]')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_script
        generate_script("black holes", language="en")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert "MAXIMUM 5 scenes" in prompt_used
    assert "12 words or fewer" in prompt_used   # Scene 1 hook rule applies to all formats


def test_generate_script_shorts_mode():
    """With aspect_ratio='9:16', prompt includes Shorts-specific structure hints."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"img"}]')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_script
        generate_script("black holes", language="en", aspect_ratio="9:16")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert "MAXIMUM 5 scenes" in prompt_used
    assert "12 words or fewer" in prompt_used      # Shorts hook instruction present
    assert "9–11 seconds" in prompt_used


def test_generate_script_invalid_ratio_falls_back():
    """An unknown aspect_ratio value is treated as 16:9."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"img"}]')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_script
        generate_script("black holes", language="en", aspect_ratio="4:3")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert "MAXIMUM 5 scenes" in prompt_used
    assert "12 words or fewer" in prompt_used  # Scene 1 hook rule applies to all formats


# ── image_service tests ──────────────────────────────────────────────────────

def test_generate_image_uses_16x9_by_default():
    """Default call uses 16:9 aspect ratio and landscape style hint."""
    mock_model = MagicMock()
    mock_img = MagicMock()
    mock_model.generate_images.return_value = [mock_img]
    mock_img.save = MagicMock()

    with patch("app.services.image_service._get_model", return_value=mock_model), \
         patch("app.services.image_service.ensure_dir"):
        from app.services.image_service import generate_image
        generate_image("a robot", 0)

    call_kwargs = mock_model.generate_images.call_args[1]
    assert call_kwargs["aspect_ratio"] == "16:9"
    assert "cinematic" in call_kwargs["prompt"] or "16:9" in call_kwargs["prompt"]


def test_generate_image_uses_9x16_ratio():
    """9:16 aspect_ratio is passed to Imagen and portrait hint appears in prompt."""
    mock_model = MagicMock()
    mock_img = MagicMock()
    mock_model.generate_images.return_value = [mock_img]
    mock_img.save = MagicMock()

    with patch("app.services.image_service._get_model", return_value=mock_model), \
         patch("app.services.image_service.ensure_dir"):
        from app.services.image_service import generate_image
        generate_image("a robot", 0, aspect_ratio="9:16")

    call_kwargs = mock_model.generate_images.call_args[1]
    assert call_kwargs["aspect_ratio"] == "9:16"
    assert "portrait" in call_kwargs["prompt"] or "Shorts" in call_kwargs["prompt"]


# ── generate_shorts_caption tests ────────────────────────────────────────────

def test_generate_shorts_caption_returns_string():
    """Function returns the model's response text directly."""
    mock = _make_model_mock("Black holes bend spacetime! 🌌\n#space #blackholes #science")
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_shorts_caption
        result = generate_shorts_caption("black holes")
    assert isinstance(result, str)
    assert len(result) > 0


def test_generate_shorts_caption_prompt_contains_topic():
    """The topic appears in the prompt sent to the model."""
    mock = _make_model_mock("Some caption #tag")
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_shorts_caption
        generate_shorts_caption("quantum computing")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert "quantum computing" in prompt_used


def test_generate_shorts_caption_hindi_uses_hindi_instruction():
    """When language='hi', the Hindi lang instruction appears in the prompt."""
    mock = _make_model_mock("कुछ कैप्शन #टैग")
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_shorts_caption
        generate_shorts_caption("black holes", language="hi")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert "Hindi" in prompt_used or "Devanagari" in prompt_used


def test_apply_quality_controls_sanitizes_profanity_and_copyright():
    from app.services import llm_service
    scenes = [
        {"scene": 1, "narration": "This is fucking wild", "visual": "Use Disney style castle with Google logo"}
    ]
    with patch("app.services.llm_service.fact_check_scenes", return_value=scenes):
        cleaned = llm_service.apply_quality_controls("topic", scenes, language="en")
    assert "[censored]" in cleaned[0]["narration"]
    assert "generic public-domain style" in cleaned[0]["visual"]


# ── generate_script_with_search visual_query tests ───────────────────────────

def test_generate_script_with_search_news_mode_uses_visual_query():
    """Default (news) script_mode prompt asks for visual_query, not visual."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual_query":"city skyline sunrise"}]')
    with patch("app.services.llm_service._get_search_model", return_value=mock.return_value), \
         patch("app.services.llm_service._SEARCH_MODEL_CANDIDATES", ("gemini-2.5-flash",)):
        from app.services.llm_service import generate_script_with_search
        generate_script_with_search("AI update", language="en", aspect_ratio="9:16")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual_query"' in prompt_used
    assert "VISUAL_QUERY RULES" in prompt_used
    assert "Pexels" in prompt_used


def test_generate_script_with_search_facts_mode_still_uses_visual():
    """script_mode='facts' (Tell Me Why) keeps the Imagen-style visual field unchanged."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"storybook illustration"}]')
    with patch("app.services.llm_service._get_search_model", return_value=mock.return_value), \
         patch("app.services.llm_service._SEARCH_MODEL_CANDIDATES", ("gemini-2.5-flash",)):
        from app.services.llm_service import generate_script_with_search
        generate_script_with_search("bananas radioactive", script_mode="facts")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual"' in prompt_used
    assert "VISUAL PROMPT RULES" in prompt_used
    assert "Imagen" in prompt_used


# ── generate_script (fallback) script_mode tests ─────────────────────────────

def test_generate_script_default_mode_still_uses_visual():
    """Default script_mode (no arg passed) preserves existing Imagen-style behavior for
    every caller that doesn't know about the new mode (routes/generate.py, facts fallback)."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"img"}]')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_script
        generate_script("black holes", language="en")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual"' in prompt_used
    assert "VISUAL PROMPT RULES" in prompt_used


def test_generate_script_news_mode_uses_visual_query():
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual_query":"city skyline"}]')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_script
        generate_script("AI update", language="en", aspect_ratio="9:16", script_mode="news")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual_query"' in prompt_used
    assert "VISUAL_QUERY RULES" in prompt_used
