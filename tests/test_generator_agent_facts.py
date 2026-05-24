import pytest
from unittest.mock import MagicMock, patch


def test_generator_agent_facts_branch_uses_search_with_facts_mode():
    """When script_type='facts', generator calls generate_script_with_search with script_mode='facts'."""
    import app.agents.generator_agent as ga
    import app.services.llm_service as llm

    calls = {}

    def mock_generate_script_with_search(topic, language="en", aspect_ratio="9:16", context="", visual_style_override="", script_mode="news"):
        calls["script_mode"] = script_mode
        calls["language"] = language
        calls["visual_style_override"] = visual_style_override
        return '[{"scene": 1, "narration": "Fact narration here twenty words test.", "visual": "cinematic style mountains photorealistic"}]'

    with patch.object(ga, "generate_script_with_search", mock_generate_script_with_search), \
         patch("app.agents.generator_agent.firestore_service.get_job", return_value={}), \
         patch("app.agents.generator_agent.firestore_service.create_or_update_job"), \
         patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True), \
         patch("app.agents.generator_agent.firestore_service.release_video_lock"), \
         patch("app.agents.generator_agent.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}), \
         patch("app.agents.generator_agent.firestore_service.set_pipeline_and_batch_state"), \
         patch("app.agents.generator_agent.firestore_service.mark_scene_checkpoint"), \
         patch("app.agents.generator_agent.firestore_service.record_quota_event"), \
         patch("app.agents.generator_agent.generate_audio"), \
         patch("app.agents.generator_agent.choose_voice_for_video", return_value="en-US-Neural2-C"), \
         patch("app.agents.generator_agent.generate_image", return_value=("/tmp/img.png", 0)), \
         patch("app.agents.generator_agent.create_video"), \
         patch("app.agents.generator_agent.send_message"), \
         patch("app.agents.generator_agent.review_package", return_value={"scenes": [{"scene": 1, "narration": "test", "visual": "test visual"}], "title": "Test Fact", "caption": "cap"}), \
         patch.object(ga, "apply_quality_controls", side_effect=lambda t, s, **kw: s), \
         patch.object(ga, "classify_music_genre", return_value="Cheerful"), \
         patch.object(ga, "get_cta_narration", return_value="Subscribe now."), \
         patch("app.services.storage_service.upload_video", return_value="gs://bucket/vid.mp4"), \
         patch("app.agents.social_media_agent.post", return_value="https://youtu.be/abc"):
        ga.run(
            headline="Why do cats always land on their feet?",
            code="FACT01",
            batch_id="b1",
            job_id="job-facts-001",
            public_id="ABCD1234",
            force_run=True,
            genre="science & space",
            details="Cats use a righting reflex to twist mid-air.",
            channel_id="stories",
            script_type="facts",
            language="en",
        )

    assert calls.get("script_mode") == "facts", f"Expected script_mode='facts', got {calls.get('script_mode')}"
    assert calls.get("language") == "en"
    assert calls.get("visual_style_override") != "", "Expected a non-empty visual_style_override for cinematic category"
