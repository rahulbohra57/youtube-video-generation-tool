# tests/test_stories_bilingual.py
from unittest.mock import MagicMock, patch


def _model_mock(text: str) -> MagicMock:
    m = MagicMock()
    m.return_value.generate_content.return_value.text = text
    return m


# ── generate_story_idea ──────────────────────────────────────────────────────

def test_generate_story_idea_hindi_prompt_contains_devanagari():
    """Hindi idea generation uses the Devanagari prompt."""
    mock = _model_mock('{"title": "मेहनत", "mood": "inspiring", "premise": "एक गरीब किसान का बेटा जब सबने उसे नकार दिया तब उसने असाधारण कदम उठाया।"}')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_story_idea
        result = generate_story_idea(preferred_mood="inspiring", language="hi")
    prompt = mock.return_value.generate_content.call_args[0][0]
    assert "Hindi" in prompt or "हिंदी" in prompt
    assert result["title"] == "मेहनत"


def test_generate_story_idea_english_prompt_is_in_english():
    """English idea generation uses an English prompt (no Devanagari)."""
    mock = _model_mock('{"title": "The Last Promise", "mood": "inspiring", "premise": "A retired teacher discovers her student became a doctor because of one encouraging word she said thirty years ago."}')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_story_idea
        result = generate_story_idea(preferred_mood="inspiring", language="en")
    prompt = mock.return_value.generate_content.call_args[0][0]
    assert "देवनागरी" not in prompt
    assert "हिंदी" not in prompt
    assert "English" in prompt
    assert result["title"] == "The Last Promise"


def test_generate_story_idea_defaults_to_hindi():
    """Omitting language defaults to Hindi (backward compat)."""
    mock = _model_mock('{"title": "मेहनत", "mood": "inspiring", "premise": "एक गरीब किसान का बेटा जब सबने उसे नकार दिया तब उसने असाधारण कदम उठाया।"}')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_story_idea
        generate_story_idea(preferred_mood="inspiring")
    prompt = mock.return_value.generate_content.call_args[0][0]
    assert "हिंदी" in prompt or "Hindi" in prompt


# ── generate_story_script ────────────────────────────────────────────────────

def test_generate_story_script_hindi_prompt_uses_devanagari():
    """Hindi script generation prompt is in Hindi with Devanagari narration rule."""
    mock = _model_mock('[{"scene":1,"narration":"एक दिन","visual":"watercolor scene"}]')
    with patch("app.services.llm_service._get_model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Vibrant storybook illustration"
            from app.services.llm_service import generate_story_script
            generate_story_script("मेहनत", "inspiring", language="hi")
    prompt = mock.return_value.generate_content.call_args[0][0]
    assert "देवनागरी" in prompt
    assert "हिंदी" in prompt


def test_generate_story_script_english_prompt_requests_english_narration():
    """English script generation prompt requests English narration."""
    mock = _model_mock('[{"scene":1,"narration":"A farmer stood alone","visual":"storybook illustration scene"}]')
    with patch("app.services.llm_service._get_model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Vibrant storybook illustration, bold outlines"
            from app.services.llm_service import generate_story_script
            generate_story_script("The Last Promise", "inspiring", language="en")
    prompt = mock.return_value.generate_content.call_args[0][0]
    assert "देवनागरी" not in prompt
    assert "English" in prompt


def test_generate_story_script_english_uses_sketch_visual_pool():
    """English stories pick from the sketch/illustration visual style pool, not the painted Hindi pool."""
    mock = _model_mock('[{"scene":1,"narration":"A farmer stood alone","visual":"storybook illustration scene"}]')
    with patch("app.services.llm_service._get_model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Vibrant storybook illustration, bold outlines"
            from app.services.llm_service import generate_story_script, _STORY_VISUAL_STYLE_POOL_EN
            generate_story_script("The Last Promise", "inspiring", language="en")
        pool_arg = mock_random.choice.call_args[0][0]
        assert pool_arg is _STORY_VISUAL_STYLE_POOL_EN


def test_generate_story_script_hindi_uses_painted_visual_pool():
    """Hindi stories pick from the painted/illustrated visual style pool."""
    mock = _model_mock('[{"scene":1,"narration":"एक दिन","visual":"watercolor"}]')
    with patch("app.services.llm_service._get_model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Vibrant storybook illustration"
            from app.services.llm_service import generate_story_script, _STORY_VISUAL_STYLE_POOL_HI
            generate_story_script("मेहनत", "inspiring", language="hi")
        pool_arg = mock_random.choice.call_args[0][0]
        assert pool_arg is _STORY_VISUAL_STYLE_POOL_HI


# ── daily cap ────────────────────────────────────────────────────────────────

def test_story_already_generated_today_returns_true_when_job_exists():
    """Daily cap fires when a non-terminal stories job was created today (IST)."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    from unittest.mock import patch

    IST = ZoneInfo("Asia/Kolkata")
    now_ist = dt.datetime(2026, 4, 27, 10, 0, 0, tzinfo=IST)
    # created_at slightly after IST midnight
    created_utc = dt.datetime(2026, 4, 26, 18, 30, 0, tzinfo=dt.timezone.utc)  # 2026-04-27 00:00 IST

    fake_job = {
        "channel_id": "stories",
        "status": "completed",
        "created_at": created_utc.isoformat(),
    }

    with patch("app.agents.story_researcher.datetime") as mock_dt, \
         patch("app.services.firestore_service.list_recent_jobs", return_value=[fake_job]):
        mock_dt.now.return_value = now_ist
        mock_dt.fromisoformat.side_effect = dt.datetime.fromisoformat
        from app.agents.story_researcher import _story_already_generated_today
        result = _story_already_generated_today()

    assert result is True


def test_story_already_generated_today_returns_false_when_no_job_today():
    """Daily cap does not fire when the only story job was created yesterday."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    from unittest.mock import patch

    IST = ZoneInfo("Asia/Kolkata")
    now_ist = dt.datetime(2026, 4, 27, 10, 0, 0, tzinfo=IST)
    # created_at before IST midnight
    created_utc = dt.datetime(2026, 4, 26, 17, 0, 0, tzinfo=dt.timezone.utc)  # 2026-04-26 22:30 IST

    fake_job = {
        "channel_id": "stories",
        "status": "completed",
        "created_at": created_utc.isoformat(),
    }

    with patch("app.agents.story_researcher.datetime") as mock_dt, \
         patch("app.services.firestore_service.list_recent_jobs", return_value=[fake_job]):
        mock_dt.now.return_value = now_ist
        mock_dt.fromisoformat.side_effect = dt.datetime.fromisoformat
        from app.agents.story_researcher import _story_already_generated_today
        result = _story_already_generated_today()

    assert result is False


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

def test_english_story_safety_fallback_uses_sketch_prompt():
    """When an English story scene is safety-filtered, the sketch/illustration fallback prompt is used."""
    from app.agents.generator_agent import _STORY_GENRE_SAFE_PROMPTS_EN
    expected_genres = {
        "inspiring", "heartfelt", "comedy", "crime", "action",
        "sci-fi", "mythology", "thriller", "mystery",
        "adventure", "slice-of-life", "historical",
    }
    assert set(_STORY_GENRE_SAFE_PROMPTS_EN.keys()) == expected_genres
    sketch_terms = {"illustration", "sketch", "graphic novel", "storybook", "watercolor", "pencil", "charcoal"}
    for genre, prompt in _STORY_GENRE_SAFE_PROMPTS_EN.items():
        has_sketch = any(term in prompt.lower() for term in sketch_terms)
        assert has_sketch, f"Genre '{genre}' fallback missing sketch/illustration descriptor: {prompt}"
