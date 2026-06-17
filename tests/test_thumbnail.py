# tests/test_thumbnail.py
import pytest
from unittest.mock import MagicMock, patch


def test_generate_thumbnail_returns_png_path(tmp_path):
    mock_img = MagicMock()
    mock_images_obj = MagicMock()
    mock_images_obj.images = None
    mock_images_obj.__len__ = lambda self: 1
    mock_images_obj.__getitem__ = lambda self, i: mock_img

    with patch("app.services.image_service._get_model") as mock_model, \
         patch("app.services.image_service.TEMP_DIR", str(tmp_path)):
        mock_model.return_value.generate_images.return_value = mock_images_obj
        from app.services.image_service import generate_thumbnail
        result = generate_thumbnail("A scientist with a glowing brain", "TEST01")

    assert result.endswith("thumbnail_TEST01.png")
    mock_img.save.assert_called_once_with(result)


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
