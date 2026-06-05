import pytest
from unittest.mock import patch, MagicMock


# ── Task 1 ──────────────────────────────────────────────────────────────────

def test_fact_visual_style_is_single_brand_string():
    """_fact_visual_style always returns the single brand constant."""
    from app.services import llm_service
    style_space = llm_service._fact_visual_style("science & space")
    style_psych = llm_service._fact_visual_style("psychology & dark psychology")
    assert style_space == style_psych, "All categories must share the same brand aesthetic"
    assert "flat" in style_space.lower(), "Brand style must be flat illustration"
    assert "electric blue" in style_space.lower(), "Brand palette must include electric blue"


# ── Task 2 ──────────────────────────────────────────────────────────────────

def test_facts_script_instruction_has_conversational_voice():
    """generate_script_with_search with script_mode='facts' sends conversational-friend rules."""
    from app.services import llm_service

    captured = {}

    mock_model = MagicMock()
    def _capture(prompt, **kw):
        captured["prompt"] = prompt
        resp = MagicMock()
        resp.text = '[{"scene":1,"narration":"Tell me why bananas are radioactive.","visual":"flat"}]'
        return resp
    mock_model.generate_content.side_effect = _capture

    with patch.object(llm_service, "_get_search_model", return_value=mock_model),\
         patch.object(llm_service, "_search_grounding_disabled", False):
        try:
            llm_service.generate_script_with_search("bananas radioactive", script_mode="facts")
        except Exception:
            pass  # we only care about the captured prompt

    assert "prompt" in captured, "generate_content was not called"
    p = captured["prompt"]

    # New voice rules must be present
    assert "conversational friend" in p.lower(), "Must instruct conversational-friend voice"
    assert "wait, it gets weirder" in p.lower(), "Must describe scene 3 as escalation beat"
    assert "shareable" in p.lower(), "Must instruct shareable payoff for scene 4"

    # Old journalistic opener must be removed from the channel description
    assert "mind-blowing facts" not in p, "Old journalistic opener must be removed"
    # Banned phrases must appear in a BANNED context, not as encouraged patterns
    banned_section = p.lower().find("banned:")
    assert banned_section != -1, "Prompt must include a BANNED phrases list"
    assert "scientists have discovered" in p.lower()[banned_section:], (
        "Banned phrase must appear in the banned list, not as an encouraged pattern"
    )


# ── Task 3 ──────────────────────────────────────────────────────────────────

def test_fact_topic_prompt_has_timeless_guardrail_and_casual_titles():
    """generate_fact_topic sends a prompt with the timeless guardrail and casual example titles."""
    from app.services import llm_service

    captured = {}

    mock_model = MagicMock()
    def _capture(prompt, **kw):
        captured["prompt"] = prompt
        resp = MagicMock()
        resp.text = '{"title": "Your brain is hiding your nose from you right now", "premise": "The brain actively suppresses the image of your nose through a process called neural adaptation, filtering out constant stimuli to focus on novelty."}'
        return resp
    mock_model.generate_content.side_effect = _capture

    with patch.object(llm_service, "_get_model", return_value=mock_model):
        llm_service.generate_fact_topic("human body & biology")

    assert "prompt" in captured
    p = captured["prompt"]

    # Timeless guardrail must be present
    assert "timeless" in p.lower(), "Timeless guardrail must be in prompt"
    assert "recent news" in p.lower() or "trending" in p.lower(), (
        "Guardrail must explicitly ban news/trending topics"
    )

    # Casual title examples must be present; academic ones must be gone
    assert "radioactive" in p.lower() or "hiding your nose" in p.lower(), (
        "Casual example titles must appear in the prompt"
    )
    assert "ancient romans" not in p.lower(), "Old academic example must be removed"


# ── Task 4 ──────────────────────────────────────────────────────────────────

def test_safe_prompts_match_brand_aesthetic():
    """All _STORY_GENRE_SAFE_PROMPTS_EN values must use the brand flat-illustration aesthetic."""
    from app.agents.generator_agent import _STORY_GENRE_SAFE_PROMPTS_EN

    assert len(_STORY_GENRE_SAFE_PROMPTS_EN) == 12, "Must still have exactly 12 fallback prompts"

    for category, prompt in _STORY_GENRE_SAFE_PROMPTS_EN.items():
        p = prompt.lower()
        assert "flat" in p, f"Category '{category}' safe prompt must use flat illustration style"
        assert any(color in p for color in ["electric blue", "blue", "yellow", "coral"]), (
            f"Category '{category}' safe prompt must reference brand palette"
        )
        assert "no text" in p, f"Category '{category}' safe prompt must include 'no text, no words'"
