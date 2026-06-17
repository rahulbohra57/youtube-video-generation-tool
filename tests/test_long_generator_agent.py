# tests/test_long_generator_agent.py
import json
import pytest
from unittest.mock import MagicMock, patch


def _make_scenes(n: int = 27) -> list[dict]:
    scenes = []
    for i in range(1, n + 1):
        if i <= 2:
            seg = "hook"
        elif i >= n:
            seg = "cta"
        elif i >= n - 1:
            seg = "retention"
        else:
            seg = "core"
        scenes.append({
            "scene": i,
            "segment": seg,
            "narration": ("word " * 45).strip(),
            "visual_query": "nature landscape",
        })
    return scenes


def test_run_completes_happy_path(tmp_path):
    scenes = _make_scenes(27)

    with patch("app.agents.long_generator_agent.generate_long_facts_script", return_value=json.dumps(scenes)), \
         patch("app.agents.long_generator_agent.extract_json", return_value=scenes), \
         patch("app.agents.long_generator_agent.choose_voice_for_video", return_value="en-US-Neural2-D"), \
         patch("app.agents.long_generator_agent.generate_audio"), \
         patch("app.agents.long_generator_agent.fetch_clip", return_value=str(tmp_path / "clip.mp4")), \
         patch("app.agents.long_generator_agent.generate_thumbnail", return_value=str(tmp_path / "thumb.png")), \
         patch("app.agents.long_generator_agent.create_long_video", return_value=str(tmp_path / "out.mp4")), \
         patch("app.agents.long_generator_agent.classify_music_genre", return_value="Happy"), \
         patch("app.agents.long_generator_agent.firestore_service") as mock_fs, \
         patch("app.agents.long_generator_agent.send_message"), \
         patch("app.agents.long_generator_agent.upload_video", return_value="https://www.youtube.com/watch?v=abc123"), \
         patch("app.agents.long_generator_agent.set_thumbnail"), \
         patch("app.agents.long_generator_agent.extract_video_id", return_value="abc123"):
        mock_fs.get_job.return_value = {}
        mock_fs.acquire_video_lock.return_value = True
        mock_fs.create_or_update_job.return_value = None
        mock_fs.mark_scene_checkpoint.return_value = None
        mock_fs.set_pipeline_and_batch_state.return_value = None
        mock_fs.release_video_lock.return_value = None

        from app.agents.long_generator_agent import run
        run("Why do cats purr", "LONG01", job_id="job-123", public_id="PUB01")

    completed_calls = [
        c for c in mock_fs.create_or_update_job.call_args_list
        if c[0][1].get("status") == "completed"
    ]
    assert len(completed_calls) == 1


def test_run_skips_if_job_already_terminal():
    with patch("app.agents.long_generator_agent.firestore_service") as mock_fs:
        mock_fs.get_job.return_value = {"status": "completed"}

        from app.agents.long_generator_agent import run
        run("Topic", "CODE01", job_id="existing-job")

    mock_fs.acquire_video_lock.assert_not_called()


def test_run_marks_failed_when_too_few_scenes():
    too_few = _make_scenes(5)

    with patch("app.agents.long_generator_agent.generate_long_facts_script", return_value=json.dumps(too_few)), \
         patch("app.agents.long_generator_agent.extract_json", return_value=too_few), \
         patch("app.agents.long_generator_agent.choose_voice_for_video", return_value="en-US-Neural2-D"), \
         patch("app.agents.long_generator_agent.classify_music_genre", return_value="Happy"), \
         patch("app.agents.long_generator_agent.firestore_service") as mock_fs, \
         patch("app.agents.long_generator_agent.send_message"):
        mock_fs.get_job.return_value = {}
        mock_fs.acquire_video_lock.return_value = True
        mock_fs.create_or_update_job.return_value = None
        mock_fs.release_video_lock.return_value = None
        mock_fs.set_pipeline_and_batch_state.return_value = None

        from app.agents.long_generator_agent import run
        with pytest.raises(RuntimeError, match="Script too short"):
            run("Topic", "CODE02", job_id="job-fail")

    failed_calls = [
        c for c in mock_fs.create_or_update_job.call_args_list
        if c[0][1].get("status") == "failed"
    ]
    assert len(failed_calls) >= 1


def test_run_uses_long_lock_key():
    with patch("app.agents.long_generator_agent.generate_long_facts_script", return_value="[]"), \
         patch("app.agents.long_generator_agent.extract_json", return_value=[]), \
         patch("app.agents.long_generator_agent.choose_voice_for_video", return_value="en-US-Neural2-D"), \
         patch("app.agents.long_generator_agent.classify_music_genre", return_value="Happy"), \
         patch("app.agents.long_generator_agent.firestore_service") as mock_fs, \
         patch("app.agents.long_generator_agent.send_message"):
        mock_fs.get_job.return_value = {}
        mock_fs.acquire_video_lock.return_value = True
        mock_fs.create_or_update_job.return_value = None
        mock_fs.release_video_lock.return_value = None
        mock_fs.set_pipeline_and_batch_state.return_value = None

        from app.agents.long_generator_agent import run
        try:
            run("Topic", "CODE03", job_id="job-lock")
        except Exception:
            pass

    acquire_call = mock_fs.acquire_video_lock.call_args
    assert acquire_call[1].get("lock_key") == "long_video_generation" or \
           (len(acquire_call[0]) > 1 and acquire_call[0][1] == "long_video_generation") or \
           "long_video_generation" in str(acquire_call)
