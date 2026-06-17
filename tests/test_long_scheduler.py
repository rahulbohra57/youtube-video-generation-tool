# tests/test_long_scheduler.py
import json
import pytest
from unittest.mock import MagicMock, patch


def test_run_long_stories_dispatches_workflow(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_state = {"state": "completed"}
    mock_idea = {"title": "Why cats purr at 25Hz", "premise": "The mechanism is surprising."}

    with patch("scripts.run_long_stories.firestore_service") as mock_fs, \
         patch("scripts.run_long_stories._select_category", return_value="science & space"), \
         patch("scripts.run_long_stories._recently_used_titles", return_value=[]), \
         patch("scripts.run_long_stories.generate_fact_topic", return_value=mock_idea), \
         patch("scripts.run_long_stories.dispatch_long_video_generation") as mock_dispatch, \
         patch("scripts.run_long_stories.send_message"):
        mock_fs.get_pipeline_state.return_value = mock_state
        mock_fs.get_job.return_value = {}
        mock_fs.save_news_batch.return_value = None
        mock_fs.set_pipeline_and_batch_state.return_value = None
        mock_fs.create_or_update_job.return_value = None
        mock_fs.is_headline_already_suggested.return_value = False
        mock_fs.mark_headline_suggested.return_value = None

        from scripts.run_long_stories import run
        public_id = run()

    assert public_id is not None
    mock_dispatch.assert_called_once()
    payload = mock_dispatch.call_args[0][0]
    assert payload["headline"] == "Why cats purr at 25Hz"
    assert payload["script_type"] == "long_facts"
    assert payload["channel_id"] == "stories_long"


def test_run_long_stories_skips_when_pipeline_processing(monkeypatch):
    with patch("scripts.run_long_stories.firestore_service") as mock_fs, \
         patch("scripts.run_long_stories.send_message"), \
         patch("scripts.run_long_stories._select_category", return_value="science & space"), \
         patch("scripts.run_long_stories._recently_used_titles", return_value=[]), \
         patch("scripts.run_long_stories.generate_fact_topic") as mock_topic:
        mock_fs.get_pipeline_state.return_value = {
            "state": "processing",
            "active_batch_id": "long_20260617_120000",
            "last_run_at": "2099-01-01T00:00:00+00:00",
        }
        from scripts.run_long_stories import run
        result = run()

    assert result is None
    mock_topic.assert_not_called()
