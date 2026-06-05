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
