from unittest.mock import MagicMock, patch


def test_generate_passes_aspect_ratio_to_services():
    """aspect_ratio query param is forwarded to generate_script and generate_image."""
    mock_script = MagicMock(return_value='[{"scene":1,"narration":"hi","visual":"img"}]')
    mock_audio = MagicMock()
    mock_image = MagicMock(return_value="tmp/scene_0.png")
    mock_video = MagicMock(return_value="tmp/final.mp4")

    with patch("app.routes.generate.generate_script", mock_script), \
         patch("app.routes.generate.generate_audio", mock_audio), \
         patch("app.routes.generate.generate_image", mock_image), \
         patch("app.routes.generate.create_video", mock_video), \
         patch("app.routes.generate.classify_music_genre", return_value="general"), \
         patch("app.routes.generate.firestore_service.acquire_video_lock", return_value=True), \
         patch("app.routes.generate.firestore_service.release_video_lock", return_value=True), \
         patch("app.routes.generate.ensure_dir"):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.post("/generate?topic=black+holes&aspect_ratio=9:16")

    assert resp.status_code == 200
    mock_script.assert_called_once_with("black holes", language="en", aspect_ratio="9:16")
    mock_image.assert_called_once()
    args = mock_image.call_args[0]
    kwargs = mock_image.call_args[1]
    assert args[1] == 0
    assert args[0].startswith("img")
    assert "no text" in args[0].lower()
    assert kwargs["aspect_ratio"] == "9:16"


def test_generate_defaults_to_16x9():
    """When aspect_ratio is omitted, 16:9 is used."""
    mock_script = MagicMock(return_value='[{"scene":1,"narration":"hi","visual":"img"}]')
    mock_audio = MagicMock()
    mock_image = MagicMock(return_value="tmp/scene_0.png")
    mock_video = MagicMock(return_value="tmp/final.mp4")

    with patch("app.routes.generate.generate_script", mock_script), \
         patch("app.routes.generate.generate_audio", mock_audio), \
         patch("app.routes.generate.generate_image", mock_image), \
         patch("app.routes.generate.create_video", mock_video), \
         patch("app.routes.generate.classify_music_genre", return_value="general"), \
         patch("app.routes.generate.firestore_service.acquire_video_lock", return_value=True), \
         patch("app.routes.generate.firestore_service.release_video_lock", return_value=True), \
         patch("app.routes.generate.ensure_dir"):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        client.post("/generate?topic=black+holes")

    mock_script.assert_called_once_with("black holes", language="en", aspect_ratio="16:9")


def test_generate_invalid_aspect_ratio_falls_back_to_16x9():
    """An unrecognised aspect_ratio value is silently corrected to 16:9."""
    mock_script = MagicMock(return_value='[{"scene":1,"narration":"hi","visual":"img"}]')
    mock_audio = MagicMock()
    mock_image = MagicMock(return_value="tmp/scene_0.png")
    mock_video = MagicMock(return_value="tmp/final.mp4")

    with patch("app.routes.generate.generate_script", mock_script), \
         patch("app.routes.generate.generate_audio", mock_audio), \
         patch("app.routes.generate.generate_image", mock_image), \
         patch("app.routes.generate.create_video", mock_video), \
         patch("app.routes.generate.classify_music_genre", return_value="general"), \
         patch("app.routes.generate.firestore_service.acquire_video_lock", return_value=True), \
         patch("app.routes.generate.firestore_service.release_video_lock", return_value=True), \
         patch("app.routes.generate.ensure_dir"):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        client.post("/generate?topic=black+holes&aspect_ratio=4:3")

    mock_script.assert_called_once_with("black holes", language="en", aspect_ratio="16:9")


# ── /generate-caption tests ──────────────────────────────────────────────────

def test_generate_caption_returns_caption():
    """/generate-caption returns { caption: '...' } for a valid topic."""
    mock_caption = MagicMock(return_value="Black holes are wild! 🌌\n#space #physics")
    with patch("app.routes.generate.generate_shorts_caption", mock_caption):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.post("/generate-caption?topic=black+holes&language=en")
    assert resp.status_code == 200
    data = resp.json()
    assert "caption" in data
    assert data["caption"] == "Black holes are wild! 🌌\n#space #physics"
    mock_caption.assert_called_once_with("black holes", language="en")


def test_generate_caption_empty_topic_returns_400():
    """/generate-caption returns 400 when topic is empty."""
    with patch("app.routes.generate.generate_shorts_caption"):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.post("/generate-caption?topic=")
    assert resp.status_code == 400


def test_generate_caption_defaults_language_to_en():
    """When language is omitted, defaults to 'en'."""
    mock_caption = MagicMock(return_value="Some caption #tag")
    with patch("app.routes.generate.generate_shorts_caption", mock_caption):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        client.post("/generate-caption?topic=jazz")
    mock_caption.assert_called_once_with("jazz", language="en")


def test_generate_returns_429_when_lock_is_held():
    with patch("app.routes.generate.firestore_service.acquire_video_lock", return_value=False):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.post("/generate?topic=black+holes")

    assert resp.status_code == 429
