# tests/test_pexels_service.py
import os
import importlib
import pytest
from unittest.mock import patch, MagicMock


def _make_video(duration: float, quality: str = "hd") -> dict:
    return {
        "duration": duration,
        "video_files": [{"quality": quality, "file_type": "video/mp4", "link": f"https://pexels.com/v/{quality}.mp4"}],
    }


def _mock_download(url, headers=None, timeout=60, stream=False):
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.iter_content = MagicMock(return_value=[b"fake_video_data"])
    return m


def test_fetch_clip_returns_path_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    videos = [_make_video(25.0), _make_video(30.0)]
    mock_search_resp = MagicMock()
    mock_search_resp.json.return_value = {"videos": videos}
    mock_search_resp.raise_for_status = MagicMock()

    mock_dl = _mock_download("url")

    def side_effect(url, **kwargs):
        if "pexels.com/videos" in url or "api.pexels.com" in url:
            return mock_search_resp
        return mock_dl

    with patch("app.services.pexels_service.requests.get", side_effect=side_effect):
        result = ps.fetch_clip("ocean waves", 20.0, scene_idx=0, temp_dir=str(tmp_path))

    assert result == str(tmp_path / "pexels_0.mp4")
    assert os.path.exists(result)


def test_fetch_clip_prefers_clip_closest_to_audio_duration(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    # Both >= audio_duration=20; 22s is closer than 60s
    videos = [_make_video(60.0), _make_video(22.0)]
    mock_search_resp = MagicMock()
    mock_search_resp.json.return_value = {"videos": videos}
    mock_search_resp.raise_for_status = MagicMock()

    selected_urls = []
    def side_effect(url, **kwargs):
        if "api.pexels.com" in url:
            return mock_search_resp
        selected_urls.append(url)
        return _mock_download(url)

    with patch("app.services.pexels_service.requests.get", side_effect=side_effect):
        ps.fetch_clip("nature", 20.0, scene_idx=1, temp_dir=str(tmp_path))

    # The 22s clip (hd.mp4) should be downloaded
    assert len(selected_urls) == 1
    assert "hd.mp4" in selected_urls[0]


def test_fetch_clip_falls_back_to_generic_on_empty_results(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    empty_resp = MagicMock()
    empty_resp.json.return_value = {"videos": []}
    empty_resp.raise_for_status = MagicMock()

    generic_resp = MagicMock()
    generic_resp.json.return_value = {"videos": [_make_video(25.0)]}
    generic_resp.raise_for_status = MagicMock()

    call_count = [0]
    def side_effect(url, **kwargs):
        if "api.pexels.com" in url:
            call_count[0] += 1
            # First 2 search queries return empty, 3rd returns result
            if call_count[0] < 3:
                return empty_resp
            return generic_resp
        return _mock_download(url)

    with patch("app.services.pexels_service.requests.get", side_effect=side_effect):
        result = ps.fetch_clip("very specific unusual query xyz", 20.0, scene_idx=2, temp_dir=str(tmp_path))

    assert result == str(tmp_path / "pexels_2.mp4")


def test_fetch_clip_returns_empty_string_when_all_fallbacks_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"videos": []}
    mock_resp.raise_for_status = MagicMock()

    with patch("app.services.pexels_service.requests.get", return_value=mock_resp):
        result = ps.fetch_clip("query", 20.0, scene_idx=3, temp_dir=str(tmp_path))

    assert result == ""


def test_fetch_clip_raises_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    import app.services.pexels_service as ps
    importlib.reload(ps)

    with patch("app.services.pexels_service.requests.get", side_effect=RuntimeError("PEXELS_API_KEY env var is not set")):
        result = ps.fetch_clip("test", 10.0, scene_idx=0, temp_dir=str(tmp_path))

    assert result == ""
