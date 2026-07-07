from contextlib import ExitStack
from unittest.mock import MagicMock, patch


def test_generator_agent_news_branch_calls_pexels_not_imagen():
    """channel_id='news' scenes must call pexels_service.fetch_clip, not image_service.generate_image."""
    import app.agents.generator_agent as ga

    def mock_generate_script_with_search(topic, language="en", aspect_ratio="9:16", context="", visual_style_override="", script_mode="news"):
        return '[{"scene": 1, "narration": "Breaking news twenty words filler filler filler filler filler filler.", "visual_query": "city skyline sunrise"}]'

    pexels_calls = []

    def mock_fetch_pexels_clip(query, audio_duration, scene_idx, category="", orientation="landscape"):
        pexels_calls.append({"query": query, "category": category, "orientation": orientation})
        return "/tmp/pexels_0.mp4"

    imagen_calls = []

    def mock_generate_image(*args, **kwargs):
        imagen_calls.append((args, kwargs))
        return "/tmp/img.png"

    with ExitStack() as stack:
        stack.enter_context(patch.object(ga, "generate_script_with_search", mock_generate_script_with_search))
        stack.enter_context(patch.object(ga, "fetch_pexels_clip", mock_fetch_pexels_clip))
        stack.enter_context(patch.object(ga, "generate_image", mock_generate_image))
        stack.enter_context(patch.object(ga, "_audio_duration", return_value=10.0))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_job", return_value={}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.create_or_update_job"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.release_video_lock"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.set_pipeline_and_batch_state"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.mark_scene_checkpoint"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.record_quota_event"))
        stack.enter_context(patch("app.agents.generator_agent.generate_audio"))
        stack.enter_context(patch("app.agents.generator_agent.choose_voice_for_video", return_value="en-US-Neural2-C"))
        stack.enter_context(patch("app.agents.generator_agent.create_video"))
        stack.enter_context(patch("app.agents.generator_agent.send_message"))
        stack.enter_context(patch("app.agents.generator_agent.review_package", return_value={"scenes": [{"scene": 1, "narration": "test narration here", "visual_query": "city skyline sunrise"}], "title": "Test News", "caption": "cap"}))
        stack.enter_context(patch.object(ga, "apply_quality_controls", side_effect=lambda t, s, **kw: s))
        stack.enter_context(patch.object(ga, "classify_music_genre", return_value="News Bulletin"))
        stack.enter_context(patch.object(ga, "get_cta_narration", return_value="Subscribe now."))
        stack.enter_context(patch("app.services.storage_service.upload_video", return_value="gs://bucket/vid.mp4"))
        stack.enter_context(patch("app.agents.social_media_agent.post", return_value="https://youtu.be/abc"))

        ga.run(
            headline="AI update",
            code="NEWS01",
            batch_id="b1",
            job_id="job-news-001",
            public_id="ABCD1234",
            force_run=True,
            genre="Artificial Intelligence",
            details="",
            channel_id="news",
            script_type="news",
            language="en",
        )

    assert len(pexels_calls) == 1, f"Expected exactly 1 Pexels call, got {len(pexels_calls)}"
    assert pexels_calls[0]["query"] == "city skyline sunrise"
    assert pexels_calls[0]["category"] == "Artificial Intelligence"
    assert pexels_calls[0]["orientation"] == "portrait"
    assert len(imagen_calls) == 0, "generate_image must NOT be called for channel_id='news'"


def test_generator_agent_stories_branch_still_calls_imagen():
    """channel_id='stories' scenes must keep calling image_service.generate_image, unchanged."""
    import app.agents.generator_agent as ga

    def mock_generate_script_with_search(topic, language="en", aspect_ratio="9:16", context="", visual_style_override="", script_mode="news"):
        return '[{"scene": 1, "narration": "Fact narration here twenty words filler filler filler.", "visual": "storybook illustration style"}]'

    pexels_calls = []
    imagen_calls = []

    def mock_generate_image(*args, **kwargs):
        imagen_calls.append((args, kwargs))
        return "/tmp/img.png"

    def mock_fetch_pexels_clip(*args, **kwargs):
        pexels_calls.append((args, kwargs))
        return "/tmp/pexels_0.mp4"

    with ExitStack() as stack:
        stack.enter_context(patch.object(ga, "generate_script_with_search", mock_generate_script_with_search))
        stack.enter_context(patch.object(ga, "fetch_pexels_clip", mock_fetch_pexels_clip))
        stack.enter_context(patch.object(ga, "generate_image", mock_generate_image))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_job", return_value={}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.create_or_update_job"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.release_video_lock"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.set_pipeline_and_batch_state"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.mark_scene_checkpoint"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.record_quota_event"))
        stack.enter_context(patch("app.agents.generator_agent.generate_audio"))
        stack.enter_context(patch("app.agents.generator_agent.choose_voice_for_video", return_value="en-US-Neural2-C"))
        stack.enter_context(patch("app.agents.generator_agent.create_video"))
        stack.enter_context(patch("app.agents.generator_agent.send_message"))
        stack.enter_context(patch("app.agents.generator_agent.review_package", return_value={"scenes": [{"scene": 1, "narration": "test", "visual": "storybook illustration style"}], "title": "Test Fact", "caption": "cap"}))
        stack.enter_context(patch.object(ga, "apply_quality_controls", side_effect=lambda t, s, **kw: s))
        stack.enter_context(patch.object(ga, "classify_music_genre", return_value="Cheerful"))
        stack.enter_context(patch.object(ga, "get_cta_narration", return_value="Subscribe now."))
        stack.enter_context(patch("app.services.storage_service.upload_video", return_value="gs://bucket/vid.mp4"))
        stack.enter_context(patch("app.agents.social_media_agent.post", return_value="https://youtu.be/abc"))

        ga.run(
            headline="Why do cats always land on their feet?",
            code="FACT01",
            batch_id="b1",
            job_id="job-facts-002",
            public_id="ABCD5678",
            force_run=True,
            genre="science & space",
            details="",
            channel_id="stories",
            script_type="facts",
            language="en",
        )

    assert len(imagen_calls) == 1
    assert len(pexels_calls) == 0, "fetch_pexels_clip must NOT be called for channel_id='stories'"


def test_generator_agent_news_scene_fails_when_pexels_exhausted():
    """An empty string from fetch_pexels_clip must be treated as a scene failure, not a
    silent success with an empty asset path."""
    import app.agents.generator_agent as ga

    def mock_generate_script_with_search(topic, language="en", aspect_ratio="9:16", context="", visual_style_override="", script_mode="news"):
        return '[{"scene": 1, "narration": "Breaking news twenty words filler filler filler filler filler filler.", "visual_query": "extremely rare unmatched query"}]'

    with ExitStack() as stack:
        stack.enter_context(patch.object(ga, "generate_script_with_search", mock_generate_script_with_search))
        stack.enter_context(patch.object(ga, "fetch_pexels_clip", return_value=""))
        stack.enter_context(patch.object(ga, "_audio_duration", return_value=10.0))
        stack.enter_context(patch("app.agents.generator_agent.time.sleep"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_job", return_value={}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.create_or_update_job"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.release_video_lock"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.set_pipeline_and_batch_state"))
        mock_checkpoint = stack.enter_context(patch("app.agents.generator_agent.firestore_service.mark_scene_checkpoint"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.record_quota_event"))
        stack.enter_context(patch("app.agents.generator_agent.generate_audio"))
        stack.enter_context(patch("app.agents.generator_agent.choose_voice_for_video", return_value="en-US-Neural2-C"))
        stack.enter_context(patch("app.agents.generator_agent.create_video"))
        mock_send = stack.enter_context(patch("app.agents.generator_agent.send_message"))
        stack.enter_context(patch("app.agents.generator_agent.review_package", return_value={"scenes": [{"scene": 1, "narration": "test narration here", "visual_query": "extremely rare unmatched query"}], "title": "Test News", "caption": "cap"}))
        stack.enter_context(patch.object(ga, "apply_quality_controls", side_effect=lambda t, s, **kw: s))
        stack.enter_context(patch.object(ga, "classify_music_genre", return_value="News Bulletin"))
        stack.enter_context(patch.object(ga, "get_cta_narration", return_value="Subscribe now."))

        ga.run(
            headline="AI update",
            code="NEWS02",
            batch_id="b1",
            job_id="job-news-003",
            public_id="ABCD9999",
            force_run=True,
            genre="Artificial Intelligence",
            details="",
            channel_id="news",
            script_type="news",
            language="en",
        )

    # Scene must be marked "failed", never "completed" with an empty image_path
    failed_calls = [c for c in mock_checkpoint.call_args_list if c.args[2] == "failed"]
    completed_calls = [c for c in mock_checkpoint.call_args_list if c.args[2] == "completed"]
    assert len(failed_calls) >= 1
    assert len(completed_calls) == 0
    # All scenes failed -> Telegram notified, video dropped
    assert mock_send.called
