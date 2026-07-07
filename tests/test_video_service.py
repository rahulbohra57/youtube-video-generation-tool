# tests/test_video_service.py
import os
import pytest
from unittest.mock import MagicMock, patch


def _make_mock_audio(duration: float = 10.0):
    m = MagicMock()
    m.duration = duration
    return m


def _make_mock_clip(w: int = 1080, h: int = 1920, duration: float = 10.0):
    m = MagicMock()
    m.w = w
    m.h = h
    m.duration = duration
    return m


@pytest.fixture
def mock_moviepy():
    mock_audio = _make_mock_audio(10.0)
    mock_clip = _make_mock_clip()
    mock_final = MagicMock()
    mock_final.duration = 10.0
    mock_final.audio = _make_mock_audio(10.0)

    passthrough = lambda clip, *a, **kw: clip
    concat_mock = MagicMock(return_value=mock_clip)

    patches = {
        "AudioFileClip": patch("app.services.video_service.AudioFileClip", return_value=mock_audio),
        "VideoFileClip": patch("app.services.video_service.VideoFileClip", return_value=mock_clip),
        "ImageClip": patch("app.services.video_service.ImageClip", return_value=mock_clip),
        "concatenate_videoclips": patch("app.services.video_service.concatenate_videoclips", concat_mock),
        "CompositeVideoClip": patch("app.services.video_service.CompositeVideoClip", return_value=mock_final),
        "CompositeAudioClip": patch("app.services.video_service.CompositeAudioClip", return_value=mock_final.audio),
        "_pick_music": patch("app.services.video_service._pick_music", return_value=None),
        "_make_word_caption_clips": patch("app.services.video_service._make_word_caption_clips", return_value=[]),
        "_clip_audio": patch("app.services.video_service._clip_audio", side_effect=passthrough),
        "_clip_duration": patch("app.services.video_service._clip_duration", side_effect=passthrough),
        "_audio_fade_in": patch("app.services.video_service._audio_fade_in", side_effect=passthrough),
        "_audio_fade_out": patch("app.services.video_service._audio_fade_out", side_effect=passthrough),
        "_volume": patch("app.services.video_service._volume", side_effect=passthrough),
        "_fit_cover": patch("app.services.video_service._fit_cover", side_effect=passthrough),
        "_subclip": patch("app.services.video_service._subclip", side_effect=passthrough),
        "create_animated_scene_clip": patch("app.services.video_service.create_animated_scene_clip", return_value=mock_clip),
        "resolve_motion_hint": patch("app.services.video_service.resolve_motion_hint", return_value={}),
    }
    started = {k: p.start() for k, p in patches.items()}
    started["concatenate_videoclips"] = concat_mock
    yield started
    for p in patches.values():
        p.stop()


def test_create_video_uses_video_file_clip_for_mp4_scene(tmp_path, mock_moviepy):
    """A .mp4 scene asset must load via VideoFileClip, not ImageClip, and must not
    invoke the Ken-Burns animation path."""
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/pexels_0.mp4", "audio_path": str(tmp_path / "a.mp3"), "narration": "Breaking news today."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="news")

    mock_moviepy["VideoFileClip"].assert_called_once_with("/tmp/pexels_0.mp4")
    mock_moviepy["ImageClip"].assert_not_called()
    mock_moviepy["create_animated_scene_clip"].assert_not_called()


def test_create_video_static_image_scene_still_uses_imageclip_for_stories(tmp_path, mock_moviepy):
    """A .png scene asset for channel_id='stories' keeps using ImageClip, unchanged."""
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/scene_0.png", "audio_path": str(tmp_path / "a.mp3"), "narration": "Once upon a time."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="stories")

    mock_moviepy["ImageClip"].assert_called_once_with("/tmp/scene_0.png")
    mock_moviepy["VideoFileClip"].assert_not_called()


def test_create_video_trims_video_clip_longer_than_audio(tmp_path, mock_moviepy):
    """A Pexels clip longer than the narration audio is trimmed via _subclip, not looped."""
    mock_moviepy["VideoFileClip"].return_value.duration = 25.0
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/pexels_0.mp4", "audio_path": str(tmp_path / "a.mp3"), "narration": "Breaking news today."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="news")

    mock_moviepy["_subclip"].assert_any_call(mock_moviepy["VideoFileClip"].return_value, 0, 10.0)
    # concatenate_videoclips is only called once here — for final scene
    # assembly. No per-scene loop concatenation since the clip is already
    # long enough (no shorter-than-audio branch taken).
    assert mock_moviepy["concatenate_videoclips"].call_count == 1


def test_create_video_loops_video_clip_shorter_than_audio(tmp_path, mock_moviepy):
    """A Pexels clip shorter than the narration audio is looped via concatenate_videoclips
    before being trimmed to the exact audio duration."""
    mock_moviepy["VideoFileClip"].return_value.duration = 4.0
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/pexels_0.mp4", "audio_path": str(tmp_path / "a.mp3"), "narration": "Breaking news today."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="news")

    # concatenate_videoclips is called twice: once to loop the short source
    # clip (first call), once for final scene assembly (second call).
    assert mock_moviepy["concatenate_videoclips"].call_count == 2
    loop_call = mock_moviepy["concatenate_videoclips"].call_args_list[0]
    looped_list = loop_call[0][0]
    assert len(looped_list) == 3  # int(10.0 / 4.0) + 1 == 3


def test_create_video_forces_portrait_target_for_news(tmp_path, mock_moviepy):
    """News video-clip scenes must be cropped to 1080x1920, same as stories."""
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/pexels_0.mp4", "audio_path": str(tmp_path / "a.mp3"), "narration": "Breaking news today."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="news")

    mock_moviepy["_fit_cover"].assert_any_call(mock_moviepy["VideoFileClip"].return_value, 1080, 1920)
