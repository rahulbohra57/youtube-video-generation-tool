# tests/test_long_video_service.py
import os
import pytest
from unittest.mock import MagicMock, patch


def _make_mock_audio(duration: float = 20.0):
    m = MagicMock()
    m.duration = duration
    return m


def _make_mock_video(duration: float = 25.0, w: int = 1920, h: int = 1080):
    m = MagicMock()
    m.duration = duration
    m.w = w
    m.h = h
    m.audio = _make_mock_audio(duration)
    return m


def test_create_long_video_calls_write_videofile(tmp_path):
    clip1 = {"video_path": str(tmp_path / "clip1.mp4"), "audio_path": str(tmp_path / "a1.mp3"), "narration": "Scene one narration text here."}
    clip2 = {"video_path": "", "audio_path": str(tmp_path / "a2.mp3"), "narration": "Scene two is a black frame fallback."}

    mock_audio_clip = _make_mock_audio(20.0)
    mock_video_clip = _make_mock_video(25.0)
    mock_final = MagicMock()
    mock_final.duration = 40.0
    mock_final.audio = _make_mock_audio(40.0)

    passthrough = lambda clip, *a, **kw: clip

    with patch("app.services.long_video_service.AudioFileClip", return_value=mock_audio_clip), \
         patch("app.services.long_video_service.VideoFileClip", return_value=mock_video_clip), \
         patch("app.services.long_video_service.ImageClip", return_value=mock_video_clip), \
         patch("app.services.long_video_service.concatenate_videoclips", return_value=mock_final), \
         patch("app.services.long_video_service.CompositeVideoClip", return_value=mock_final), \
         patch("app.services.long_video_service.CompositeAudioClip", return_value=mock_final.audio), \
         patch("app.services.long_video_service._pick_music", return_value=None), \
         patch("app.services.long_video_service._make_word_caption_clips", return_value=[]), \
         patch("app.services.long_video_service._clip_audio", side_effect=passthrough), \
         patch("app.services.long_video_service._audio_fade_in", side_effect=passthrough), \
         patch("app.services.long_video_service._audio_fade_out", side_effect=passthrough), \
         patch("app.services.long_video_service._volume", side_effect=passthrough), \
         patch("app.services.long_video_service._fit_cover", side_effect=passthrough), \
         patch("app.services.long_video_service._subclip", side_effect=passthrough):
        from app.services.long_video_service import create_long_video
        output = str(tmp_path / "out.mp4")
        create_long_video([clip1, clip2], output)

    mock_final.write_videofile.assert_called_once()
    call_kwargs = mock_final.write_videofile.call_args
    assert call_kwargs[0][0] == output
    assert call_kwargs[1]["fps"] == 24
    assert call_kwargs[1]["codec"] == "libx264"


def test_create_long_video_uses_black_frame_when_video_path_empty(tmp_path):
    clip = {"video_path": "", "audio_path": str(tmp_path / "a.mp3"), "narration": "narration"}

    mock_audio = _make_mock_audio(20.0)
    mock_image_clip = MagicMock()
    mock_image_clip.w = 1920
    mock_image_clip.h = 1080
    mock_image_clip.audio = mock_audio

    mock_final = MagicMock()
    mock_final.duration = 20.0
    mock_final.audio = mock_audio

    image_clip_calls = []
    def capture_image_clip(arr):
        image_clip_calls.append(arr)
        return mock_image_clip

    passthrough = lambda clip, *a, **kw: clip

    with patch("app.services.long_video_service.AudioFileClip", return_value=mock_audio), \
         patch("app.services.long_video_service.VideoFileClip") as mock_vc, \
         patch("app.services.long_video_service.ImageClip", side_effect=capture_image_clip), \
         patch("app.services.long_video_service.concatenate_videoclips", return_value=mock_final), \
         patch("app.services.long_video_service.CompositeVideoClip", return_value=mock_final), \
         patch("app.services.long_video_service.CompositeAudioClip", return_value=mock_audio), \
         patch("app.services.long_video_service._pick_music", return_value=None), \
         patch("app.services.long_video_service._make_word_caption_clips", return_value=[]), \
         patch("app.services.long_video_service._clip_audio", side_effect=passthrough), \
         patch("app.services.long_video_service._clip_duration", side_effect=passthrough), \
         patch("app.services.long_video_service._audio_fade_in", side_effect=passthrough), \
         patch("app.services.long_video_service._audio_fade_out", side_effect=passthrough), \
         patch("app.services.long_video_service._volume", side_effect=passthrough):
        from app.services.long_video_service import create_long_video
        create_long_video([clip], str(tmp_path / "out.mp4"))

    # VideoFileClip should NOT have been called since video_path is ""
    mock_vc.assert_not_called()
    # ImageClip SHOULD have been called with a black numpy array
    assert len(image_clip_calls) > 0
