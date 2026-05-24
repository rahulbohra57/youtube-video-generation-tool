import pytest
from unittest.mock import MagicMock, patch


def test_story_researcher_run_dispatches_github_workflow(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    import app.agents.story_researcher as sr

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp) as mock_post:
        with patch.object(sr.firestore_service, "get_pipeline_state", return_value={"state": "completed"}):
            with patch.object(sr, "_recently_used_titles", return_value=[]):
                with patch.object(sr, "_select_category", return_value="science & space"):
                    with patch.object(sr, "generate_fact_topic", return_value={"title": "Why do black holes evaporate?", "premise": "Stephen Hawking showed that quantum effects cause black holes to slowly emit radiation and shrink over trillions of years."}):
                        with patch.object(sr, "_is_topic_already_used", return_value=False):
                            with patch.object(sr.firestore_service, "save_news_batch"):
                                with patch.object(sr.firestore_service, "set_pipeline_and_batch_state"):
                                    with patch.object(sr.firestore_service, "create_or_update_job"):
                                        with patch.object(sr, "_mark_topic_used"):
                                            with patch.object(sr, "send_message"):
                                                result = sr.run()

    assert result is not None
    mock_post.assert_called_once()
    call_body = mock_post.call_args[1]["json"]
    payload = __import__("json").loads(call_body["inputs"]["payload"])
    assert payload["channel_id"] == "stories"
    assert payload["script_type"] == "facts"
    assert payload["language"] == "en"
    assert payload["headline"] == "Why do black holes evaporate?"
