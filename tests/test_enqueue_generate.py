import pytest
from unittest.mock import MagicMock, patch


def test_enqueue_generate_creates_job_and_dispatches_workflow(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    created_jobs = {}

    import app.agents.whatsapp_agent as wa

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp):
        with patch.object(
            wa.firestore_service,
            "create_or_update_job",
            side_effect=lambda jid, data: created_jobs.update({jid: data}),
        ):
            result = wa._enqueue_generate(
                headline="Test Headline",
                code="CODE01",
                batch_id="batch_20240101_120000",
                channel_id="news",
                source="telegram",
            )

    assert result is True
    assert len(created_jobs) == 1
    job = list(created_jobs.values())[0]
    assert job["status"] == "queued"
    assert job["topic"] == "Test Headline"
    assert job["channel_id"] == "news"
    assert "generate-batch" in job["job_id"]


def test_enqueue_generate_dispatches_correct_payload(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    import json
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    captured = {}

    import app.agents.whatsapp_agent as wa

    def capture_post(url, **kwargs):
        captured["inputs"] = kwargs.get("json", {}).get("inputs", {})
        return mock_resp

    with patch("app.agents.github_dispatch.requests.post", side_effect=capture_post):
        with patch.object(wa.firestore_service, "create_or_update_job"):
            wa._enqueue_generate(
                headline="AI Story",
                code="AI01",
                batch_id="batch_ai",
                genre="inspiring",
                channel_id="stories",
                force_run=True,
            )

    payload = json.loads(captured["inputs"]["payload"])
    assert payload["headline"] == "AI Story"
    assert payload["channel_id"] == "stories"
    assert payload["force_run"] is True
    assert payload["genre"] == "inspiring"


def test_enqueue_generate_includes_idempotency_fields(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    import json
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    captured = {}

    import app.agents.whatsapp_agent as wa

    def capture_post(url, **kwargs):
        captured["inputs"] = kwargs.get("json", {}).get("inputs", {})
        return mock_resp

    with patch("app.agents.github_dispatch.requests.post", side_effect=capture_post):
        with patch.object(wa.firestore_service, "create_or_update_job"):
            wa._enqueue_generate(
                headline="Test",
                code="T01",
                batch_id="batch_xyz",
                idempotency_scope="create_news",
                idempotency_key="abc123",
            )

    payload = json.loads(captured["inputs"]["payload"])
    assert payload["idempotency_scope"] == "create_news"
    assert payload["idempotency_key"] == "abc123"
