# tests/test_stories_bilingual.py
from unittest.mock import MagicMock, patch


def _model_mock(text: str) -> MagicMock:
    m = MagicMock()
    m.generate_content.return_value.text = text
    return m


# ── generate_story_idea ──────────────────────────────────────────────────────

def test_generate_story_idea_hindi_prompt_contains_devanagari():
    """Hindi idea generation uses the Devanagari prompt."""
    mock = _model_mock('{"title": "मेहनत", "mood": "inspiring", "premise": "एक गरीब किसान का बेटा जब सबने उसे नकार दिया तब उसने असाधारण कदम उठाया।"}')
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_story_idea
        result = generate_story_idea(preferred_mood="inspiring", language="hi")
    prompt = mock.generate_content.call_args[0][0]
    assert "Hindi" in prompt or "हिंदी" in prompt
    assert result["title"] == "मेहनत"


def test_generate_story_idea_english_prompt_is_in_english():
    """English idea generation uses an English prompt (no Devanagari)."""
    mock = _model_mock('{"title": "The Last Promise", "mood": "inspiring", "premise": "A retired teacher discovers her student became a doctor because of one encouraging word she said thirty years ago."}')
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_story_idea
        result = generate_story_idea(preferred_mood="inspiring", language="en")
    prompt = mock.generate_content.call_args[0][0]
    assert "देवनागरी" not in prompt
    assert "हिंदी" not in prompt
    assert "English" in prompt
    assert result["title"] == "The Last Promise"


def test_generate_story_idea_defaults_to_hindi():
    """Omitting language defaults to Hindi (backward compat)."""
    mock = _model_mock('{"title": "मेहनत", "mood": "inspiring", "premise": "एक गरीब किसान का बेटा जब सबने उसे नकार दिया तब उसने असाधारण कदम उठाया।"}')
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_story_idea
        generate_story_idea(preferred_mood="inspiring")
    prompt = mock.generate_content.call_args[0][0]
    assert "हिंदी" in prompt or "Hindi" in prompt


# ── generate_story_script ────────────────────────────────────────────────────

def test_generate_story_script_hindi_prompt_uses_devanagari():
    """Hindi script generation prompt is in Hindi with Devanagari narration rule."""
    mock = _model_mock('[{"scene":1,"narration":"एक दिन","visual":"watercolor scene"}]')
    with patch("app.services.llm_service.model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Vibrant storybook illustration"
            from app.services.llm_service import generate_story_script
            generate_story_script("मेहनत", "inspiring", language="hi")
    prompt = mock.generate_content.call_args[0][0]
    assert "देवनागरी" in prompt
    assert "हिंदी" in prompt


def test_generate_story_script_english_prompt_requests_english_narration():
    """English script generation prompt requests English narration."""
    mock = _model_mock('[{"scene":1,"narration":"A farmer stood alone","visual":"cinematic scene"}]')
    with patch("app.services.llm_service.model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Cinematic photorealistic"
            from app.services.llm_service import generate_story_script
            generate_story_script("The Last Promise", "inspiring", language="en")
    prompt = mock.generate_content.call_args[0][0]
    assert "देवनागरी" not in prompt
    assert "English" in prompt


def test_generate_story_script_english_uses_realistic_visual_pool():
    """English stories pick from the realistic visual style pool, not the painted pool."""
    mock = _model_mock('[{"scene":1,"narration":"A farmer stood alone","visual":"cinematic scene"}]')
    with patch("app.services.llm_service.model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Cinematic photorealistic, dramatic natural lighting"
            from app.services.llm_service import generate_story_script, _STORY_VISUAL_STYLE_POOL_EN
            generate_story_script("The Last Promise", "inspiring", language="en")
        pool_arg = mock_random.choice.call_args[0][0]
        assert pool_arg is _STORY_VISUAL_STYLE_POOL_EN


def test_generate_story_script_hindi_uses_painted_visual_pool():
    """Hindi stories pick from the painted/illustrated visual style pool."""
    mock = _model_mock('[{"scene":1,"narration":"एक दिन","visual":"watercolor"}]')
    with patch("app.services.llm_service.model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Vibrant storybook illustration"
            from app.services.llm_service import generate_story_script, _STORY_VISUAL_STYLE_POOL_HI
            generate_story_script("मेहनत", "inspiring", language="hi")
        pool_arg = mock_random.choice.call_args[0][0]
        assert pool_arg is _STORY_VISUAL_STYLE_POOL_HI


# ── _story_language rotation ─────────────────────────────────────────────────

def test_story_language_hindi_slot_rotates_by_day():
    """Hindi slot advances by 1 each day across 4 slots on a 4-day cycle."""
    from zoneinfo import ZoneInfo
    from unittest.mock import patch
    import datetime as dt

    IST = ZoneInfo("Asia/Kolkata")
    # slot_hours = [7, 11, 14, 18] → indices 0, 1, 2, 3
    test_cases = [
        # (DOY, hour, expected_language)
        (1, 7,  "en"),   # DOY=1 → hindi_slot=1, running at slot0 → English
        (1, 11, "hi"),   # DOY=1 → hindi_slot=1, running at slot1 → Hindi
        (1, 14, "en"),   # DOY=1 → hindi_slot=1, running at slot2 → English
        (1, 18, "en"),   # DOY=1 → hindi_slot=1, running at slot3 → English
        (2, 7,  "en"),   # DOY=2 → hindi_slot=2, slot0 → English
        (2, 14, "hi"),   # DOY=2 → hindi_slot=2, slot2 → Hindi
        (4, 7,  "hi"),   # DOY=4 → hindi_slot=0, slot0 → Hindi
        (4, 11, "en"),   # DOY=4 → hindi_slot=0, slot1 → English
    ]

    for doy, hour, expected in test_cases:
        fake_now = dt.datetime(2026, 1, doy, hour, 5, 0, tzinfo=IST)
        with patch("app.agents.story_researcher.datetime") as mock_dt:
            # Return a real datetime so .hour and .timetuple() work naturally
            mock_dt.now.return_value = fake_now
            from app.agents.story_researcher import _story_language
            result = _story_language()
        assert result == expected, f"DOY={doy}, hour={hour}: expected {expected}, got {result}"


def test_story_language_returns_valid_string_for_off_hours():
    """If current hour is before all slots, function still returns a valid language string."""
    from zoneinfo import ZoneInfo
    import datetime as dt
    from unittest.mock import patch

    IST = ZoneInfo("Asia/Kolkata")
    fake_now = dt.datetime(2026, 1, 1, 3, 0, 0, tzinfo=IST)  # 3am — before all slots
    with patch("app.agents.story_researcher.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        from app.agents.story_researcher import _story_language
        result = _story_language()
    assert result in ("en", "hi")


# ── generator_agent language threading ──────────────────────────────────────

def test_generate_stories_task_route_passes_language_to_generator():
    """The /generate/stories-task route extracts language from payload and passes it to generator_agent."""
    from unittest.mock import patch
    from fastapi.testclient import TestClient

    with patch("app.agents.generator_agent.run") as mock_run, \
         patch("app.services.firestore_service.get_job", return_value=None), \
         patch("app.services.firestore_service.create_or_update_job"), \
         patch("app.services.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}):
        from app.main import app
        client = TestClient(app)
        resp = client.post("/generate/stories-task", json={
            "headline": "The Last Promise",
            "code": "STORY01",
            "batch_id": "b1",
            "job_id": "j1",
            "genre": "inspiring",
            "language": "en",
        })
    assert resp.status_code == 200
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs.get("language") == "en"


# ── English safety-filter fallback ──────────────────────────────────────────

def test_english_story_safety_fallback_uses_realistic_prompt():
    """When an English story scene is safety-filtered, the realistic fallback prompt is used (not watercolor)."""
    from app.agents.generator_agent import _STORY_GENRE_SAFE_PROMPTS_EN
    expected_genres = {
        "inspiring", "heartfelt", "comedy", "crime", "action",
        "sci-fi", "mythology", "thriller", "mystery",
        "adventure", "slice-of-life", "historical",
    }
    assert set(_STORY_GENRE_SAFE_PROMPTS_EN.keys()) == expected_genres
    for genre, prompt in _STORY_GENRE_SAFE_PROMPTS_EN.items():
        assert "watercolor" not in prompt.lower(), f"Genre '{genre}' fallback still uses watercolor style"
    realistic_terms = {"cinematic", "photorealistic", "documentary", "realistic", "photograph"}
    for genre, prompt in _STORY_GENRE_SAFE_PROMPTS_EN.items():
        has_realistic = any(term in prompt.lower() for term in realistic_terms)
        assert has_realistic, f"Genre '{genre}' fallback missing realistic descriptor: {prompt}"
