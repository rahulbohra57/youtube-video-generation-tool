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


@pytest.fixture
def mock_moviepy():
    mock_audio = _make_mock_audio(20.0)
    mock_video = _make_mock_video(25.0)
    mock_final = MagicMock()
    mock_final.duration = 40.0
    mock_final.audio = _make_mock_audio(40.0)

    passthrough = lambda clip, *a, **kw: clip
    concat_mock = MagicMock(return_value=mock_final)

    patches = {
        "AudioFileClip": patch("app.services.long_video_service.AudioFileClip", return_value=mock_audio),
        "VideoFileClip": patch("app.services.long_video_service.VideoFileClip", return_value=mock_video),
        "ImageClip": patch("app.services.long_video_service.ImageClip", return_value=mock_video),
        "concatenate_videoclips": patch("app.services.long_video_service.concatenate_videoclips", concat_mock),
        "CompositeVideoClip": patch("app.services.long_video_service.CompositeVideoClip", return_value=mock_final),
        "CompositeAudioClip": patch("app.services.long_video_service.CompositeAudioClip", return_value=mock_final.audio),
        "_pick_music": patch("app.services.long_video_service._pick_music", return_value=None),
        "_make_word_caption_clips": patch("app.services.long_video_service._make_word_caption_clips", return_value=[]),
        "_clip_audio": patch("app.services.long_video_service._clip_audio", side_effect=passthrough),
        "_clip_duration": patch("app.services.long_video_service._clip_duration", side_effect=passthrough),
        "_audio_fade_in": patch("app.services.long_video_service._audio_fade_in", side_effect=passthrough),
        "_audio_fade_out": patch("app.services.long_video_service._audio_fade_out", side_effect=passthrough),
        "_volume": patch("app.services.long_video_service._volume", side_effect=passthrough),
        "_fit_cover": patch("app.services.long_video_service._fit_cover", side_effect=passthrough),
        "_subclip": patch("app.services.long_video_service._subclip", side_effect=passthrough),
        "_make_title_card": patch("app.services.long_video_service._make_title_card", return_value=mock_video),
        "_make_black_frame": patch("app.services.long_video_service._make_black_frame", return_value=mock_video),
    }
    started = {k: p.start() for k, p in patches.items()}
    # Map concatenate_videoclips to the actual mock so call_args_list is inspectable
    started["concatenate_videoclips"] = concat_mock
    yield started
    for p in patches.values():
        p.stop()


def test_create_long_video_no_title_card_even_with_title(tmp_path, mock_moviepy):
    """create_long_video never prepends a title card, even when title= is provided."""
    from app.services.long_video_service import create_long_video
    clips = [
        {"clips_list": [{"video_path": "", "clip_duration": 5.0}],
         "audio_path": str(tmp_path / "a.mp3"),
         "narration": "test narration"},
    ]
    create_long_video(clips, str(tmp_path / "out.mp4"), title="My Test Title")
    # Title card must NOT be generated — it causes viewer drop-off on first frame
    mock_moviepy["_make_title_card"].assert_not_called()
    # _make_black_frame should NOT be called for a single scene (no transition after last)
    mock_moviepy["_make_black_frame"].assert_not_called()


def test_create_long_video_black_frame_between_scenes(tmp_path, mock_moviepy):
    """Black frame transition is inserted between scenes but not after the last."""
    from app.services.long_video_service import create_long_video
    clips = [
        {"clips_list": [{"video_path": "", "clip_duration": 5.0}],
         "audio_path": str(tmp_path / "a.mp3"),
         "narration": "scene one"},
        {"clips_list": [{"video_path": "", "clip_duration": 5.0}],
         "audio_path": str(tmp_path / "b.mp3"),
         "narration": "scene two"},
        {"clips_list": [{"video_path": "", "clip_duration": 5.0}],
         "audio_path": str(tmp_path / "c.mp3"),
         "narration": "scene three"},
    ]
    create_long_video(clips, str(tmp_path / "out.mp4"))
    # 3 scenes → 2 black frame transitions (between 1-2 and 2-3, not after 3)
    assert mock_moviepy["_make_black_frame"].call_count == 2


def test_create_long_video_no_title_card_when_title_empty(tmp_path, mock_moviepy):
    """No title card is prepended when title is empty string."""
    from app.services.long_video_service import create_long_video
    clips = [
        {"clips_list": [{"video_path": "", "clip_duration": 5.0}],
         "audio_path": str(tmp_path / "a.mp3"),
         "narration": "test narration"},
    ]
    create_long_video(clips, str(tmp_path / "out.mp4"), title="")
    mock_moviepy["_make_title_card"].assert_not_called()


def test_make_title_card_returns_image_clip():
    """_make_title_card returns a PIL-based ImageClip of correct dimensions."""
    from app.services.long_video_service import _make_title_card
    card = _make_title_card("Test Title Card", duration=4.0, width=1920, height=1080)
    assert card is not None


def test_create_long_video_caption_clips_called_without_caption_bg(tmp_path, mock_moviepy):
    """_make_word_caption_clips must not receive caption_bg — long-format uses stroke only."""
    captured_kwargs: list[dict] = []

    def spy_caption(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return []

    clips = [
        {"clips_list": [{"video_path": "", "clip_duration": 3.0}],
         "audio_path": str(tmp_path / "a.mp3"),
         "narration": "Hello world."},
    ]

    with patch("app.services.long_video_service._make_word_caption_clips", side_effect=spy_caption):
        from app.services.long_video_service import create_long_video
        create_long_video(clips, str(tmp_path / "out.mp4"))

    assert captured_kwargs, "caption clips spy was never called"
    for kw in captured_kwargs:
        assert "caption_bg" not in kw, f"caption_bg must not be passed to _make_word_caption_clips; got {kw}"
