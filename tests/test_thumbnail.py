# tests/test_thumbnail.py
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image


def test_generate_thumbnail_falls_back_to_pexels_when_imagen_unavailable(tmp_path):
    """Both imagen-3.0-generate-002 and -001 return 403 (no Vertex Imagen entitlement) —
    thumbnail generation should fall back to a free Pexels photo instead of failing."""
    photo_path = tmp_path / "photo.jpg"
    Image.new("RGB", (1920, 1080), color="blue").save(photo_path)

    mock_model = MagicMock()
    mock_model.generate_images.side_effect = Exception(
        "403 Publisher Model publishers/google/models/imagen-3.0-generate-001 "
        "is not visible to the current project 0."
    )

    with patch("app.services.image_service._get_model", return_value=mock_model), \
         patch("app.services.image_service._use_fallback", True), \
         patch("app.services.image_service.TEMP_DIR", str(tmp_path)), \
         patch("app.services.pexels_service.fetch_photo", return_value=str(photo_path)) as mock_fetch:
        from app.services.image_service import generate_thumbnail
        result = generate_thumbnail("A scientist with a glowing brain", "TEST02", category="science & space")

    assert result.endswith("thumbnail_TEST02.png")
    mock_fetch.assert_called_once()
    with Image.open(result) as img:
        assert img.size[0] > 0 and img.size[1] > 0


def test_generate_thumbnail_returns_png_path(tmp_path):
    mock_img = MagicMock()
    mock_images_obj = MagicMock()
    mock_images_obj.images = None
    mock_images_obj.__len__ = lambda self: 1
    mock_images_obj.__getitem__ = lambda self, i: mock_img

    with patch("app.services.image_service._get_model") as mock_model, \
         patch("app.services.image_service.TEMP_DIR", str(tmp_path)), \
         patch("app.services.image_service._image_saturation_score", return_value=50.0), \
         patch("shutil.copy2"):
        mock_model.return_value.generate_images.return_value = mock_images_obj
        from app.services.image_service import generate_thumbnail
        result = generate_thumbnail("A scientist with a glowing brain", "TEST01")

    assert result.endswith("thumbnail_TEST01.png")
    assert mock_img.save.call_count >= 1


def test_set_thumbnail_calls_youtube_api():
    mock_creds = MagicMock()
    mock_youtube = MagicMock()
    mock_request = MagicMock()
    mock_youtube.thumbnails.return_value.set.return_value = mock_request

    with patch("app.services.youtube_service.get_credentials", return_value=mock_creds), \
         patch("app.services.youtube_service.build", return_value=mock_youtube), \
         patch("app.services.youtube_service.MediaFileUpload"):
        from app.services.youtube_service import set_thumbnail
        set_thumbnail("abc123", "/tmp/thumb.png", channel_id="stories")

    mock_youtube.thumbnails.return_value.set.assert_called_once()
    mock_request.execute.assert_called_once()


def test_upload_video_does_not_add_shorts_when_is_short_false():
    mock_creds = MagicMock()
    mock_youtube = MagicMock()
    mock_request = MagicMock()
    mock_request.execute.return_value = {"id": "vid999"}
    mock_youtube.videos.return_value.insert.return_value = mock_request

    with patch("app.services.youtube_service.get_credentials", return_value=mock_creds), \
         patch("app.services.youtube_service.build", return_value=mock_youtube), \
         patch("app.services.youtube_service.MediaFileUpload"):
        from app.services.youtube_service import upload_video
        upload_video("/tmp/video.mp4", "My Video", "", is_short=False)

    call_args = mock_youtube.videos.return_value.insert.call_args
    desc = call_args[1]["body"]["snippet"]["description"]
    assert "#Shorts" not in desc
    assert "#shorts" not in desc


def test_extract_video_id_handles_watch_url():
    from app.services.youtube_service import extract_video_id
    assert extract_video_id("https://www.youtube.com/watch?v=abc123") == "abc123"


def test_extract_video_id_handles_shorts_url():
    from app.services.youtube_service import extract_video_id
    assert extract_video_id("https://www.youtube.com/shorts/xyz789") == "xyz789"
