import json
import os
import importlib
import pytest
from unittest.mock import MagicMock, patch


def test_dispatch_posts_to_github_api(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp) as mock_post:
        import app.agents.github_dispatch as gd
        importlib.reload(gd)
        gd.dispatch_video_generation({"job_id": "job_123", "headline": "Test"})

    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert "api.github.com" in call_args[0][0]
    assert "owner/repo" in call_args[0][0]
    assert "generate-video.yml" in call_args[0][0]
    body = call_args[1]["json"]
    assert body["ref"] == "main"
    payload = json.loads(body["inputs"]["payload"])
    assert payload["job_id"] == "job_123"
    assert call_args[1]["headers"]["Authorization"] == "token test-token"


def test_dispatch_raises_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    import app.agents.github_dispatch as gd
    importlib.reload(gd)

    with pytest.raises(RuntimeError, match="GITHUB_DISPATCH_TOKEN"):
        gd.dispatch_video_generation({"job_id": "x"})


def test_dispatch_raises_without_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_REPO", raising=False)

    import app.agents.github_dispatch as gd
    importlib.reload(gd)

    with pytest.raises(RuntimeError, match="GITHUB_REPO"):
        gd.dispatch_video_generation({"job_id": "x"})


def test_dispatch_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("422 Unprocessable")

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp):
        import app.agents.github_dispatch as gd
        importlib.reload(gd)
        with pytest.raises(Exception, match="422"):
            gd.dispatch_video_generation({"job_id": "x"})


def test_dispatch_falls_back_to_github_token(monkeypatch):
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "gha-auto-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp) as mock_post:
        import app.agents.github_dispatch as gd
        importlib.reload(gd)
        gd.dispatch_video_generation({"job_id": "job_456"})

    assert mock_post.call_args[1]["headers"]["Authorization"] == "token gha-auto-token"
