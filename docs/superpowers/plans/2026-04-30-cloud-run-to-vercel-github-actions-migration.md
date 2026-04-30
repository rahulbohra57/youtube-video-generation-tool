# Cloud Run → Vercel + GitHub Actions Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Cloud Run FastAPI server with Vercel (admin dashboard + Telegram webhooks) and GitHub Actions (all scheduled tasks + video generation), eliminating Cloud Run, Cloud Tasks, and Cloud Scheduler entirely.

**Architecture:** Two call-sites in the Python codebase (`_enqueue_generate` in `whatsapp_agent.py` and the enqueue block in `story_researcher.py`) switch from Cloud Tasks to a `dispatch_video_generation()` call that POSTs to GitHub's `workflow_dispatch` API. Vercel serves static `admin.html` and six Python serverless functions that reuse existing `app/` modules directly. GitHub Actions cron workflows invoke entry-point scripts in `scripts/` that call agent functions directly without HTTP.

**Tech Stack:** Python 3.10, Vercel Python serverless (BaseHTTPRequestHandler), GitHub Actions, `requests` for GitHub API dispatch

**Spec:** `docs/superpowers/specs/2026-04-30-cloud-run-to-vercel-github-actions-migration-design.md`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| **Create** | `app/agents/github_dispatch.py` | `dispatch_video_generation()` — POSTs workflow_dispatch to GitHub API |
| **Modify** | `app/config.py` | Add `GITHUB_DISPATCH_TOKEN`, `GITHUB_REPO`; remove `CLOUD_RUN_URL`, `TASKS_QUEUE` |
| **Modify** | `app/agents/whatsapp_agent.py` | Replace `_enqueue_generate()` — remove Cloud Tasks, call `dispatch_video_generation()` |
| **Modify** | `app/agents/story_researcher.py` | Replace inline Cloud Tasks block; remove unused config imports |
| **Create** | `scripts/run_research.py` | GitHub Actions entry: calls `lead_researcher.run()` |
| **Create** | `scripts/run_stories.py` | GitHub Actions entry: calls `story_researcher.run()` |
| **Create** | `scripts/run_daily_digest.py` | GitHub Actions entry: calls `lead_researcher.send_daily_digest()` |
| **Create** | `scripts/run_stories_digest.py` | GitHub Actions entry: calls `_send_stories_daily_digest()` |
| **Create** | `scripts/run_update_analytics.py` | GitHub Actions entry: runs domain schedule update + YouTube analytics refresh |
| **Create** | `scripts/run_refresh_auth.py` | GitHub Actions entry: calls `youtube_service.refresh_all_tokens()` |
| **Create** | `scripts/run_generate_video.py` | GitHub Actions entry: reads `GENERATE_PAYLOAD` env var, calls `generator_agent.run()` |
| **Create** | `.github/workflows/generate-video.yml` | workflow_dispatch trigger; concurrency group; runs `run_generate_video.py` |
| **Create** | `.github/workflows/research-run.yml` | Cron 12am/8am/4pm IST; runs `run_research.py` |
| **Create** | `.github/workflows/stories-run.yml` | Cron 7am/11am/2pm/6pm IST; runs `run_stories.py` |
| **Create** | `.github/workflows/daily-digest.yml` | Cron 8am IST; runs `run_daily_digest.py` |
| **Create** | `.github/workflows/stories-daily-digest.yml` | Cron 8:30am IST; runs `run_stories_digest.py` |
| **Create** | `.github/workflows/update-analytics.yml` | Cron 10pm IST; runs `run_update_analytics.py` |
| **Create** | `.github/workflows/refresh-youtube-auth.yml` | Cron every 6h; runs `run_refresh_auth.py` |
| **Create** | `api/_shared.py` | `setup_credentials()` — writes GCP key to `/tmp`; `require_admin()` — auth check |
| **Create** | `api/admin/metrics/summary.py` | Vercel function: GET `/admin/metrics/summary` |
| **Create** | `api/admin/metrics/jobs.py` | Vercel function: GET `/admin/metrics/jobs` |
| **Create** | `api/admin/metrics/failures.py` | Vercel function: GET `/admin/metrics/failures` |
| **Create** | `api/admin/metrics/refresh-social.py` | Vercel function: POST `/admin/metrics/refresh-social` |
| **Create** | `api/webhook/telegram.py` | Vercel function: POST `/webhook/telegram` — news bot |
| **Create** | `api/webhook/stories.py` | Vercel function: POST `/webhook/stories` — stories bot |
| **Create** | `public/admin.html` | Copy of `app/static/admin.html` (unchanged — rewrites preserve existing fetch paths) |
| **Create** | `vercel.json` | Rewrites: `/admin` → static; `/admin/metrics/*` → api functions; webhook paths |
| **Create** | `requirements-vercel.txt` | Lighter subset: no moviepy, Pillow, vertexai, google-cloud-tasks, uvicorn |
| **Delete** | `Dockerfile` | No longer used |

---

## Task 1: Create `app/agents/github_dispatch.py`

**Files:**
- Create: `app/agents/github_dispatch.py`
- Create: `tests/test_github_dispatch.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_github_dispatch.py
import json
import os
import pytest
from unittest.mock import MagicMock, patch


def test_dispatch_posts_to_github_api(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp) as mock_post:
        from app.agents import github_dispatch
        github_dispatch.dispatch_video_generation({"job_id": "job_123", "headline": "Test"})

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

    import importlib
    import app.agents.github_dispatch as gd
    importlib.reload(gd)

    with pytest.raises(RuntimeError, match="GITHUB_DISPATCH_TOKEN"):
        gd.dispatch_video_generation({"job_id": "x"})


def test_dispatch_raises_without_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_REPO", raising=False)

    import importlib
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
        from app.agents import github_dispatch
        with pytest.raises(Exception, match="422"):
            github_dispatch.dispatch_video_generation({"job_id": "x"})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_github_dispatch.py -v
```
Expected: `ModuleNotFoundError` or `ImportError` — module doesn't exist yet.

- [ ] **Step 3: Implement `app/agents/github_dispatch.py`**

```python
# app/agents/github_dispatch.py
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

_WORKFLOW_FILE = "generate-video.yml"


def dispatch_video_generation(payload: dict) -> None:
    """POST a workflow_dispatch to GitHub Actions to trigger generate-video.yml.

    Uses GITHUB_DISPATCH_TOKEN if set (required for Vercel), otherwise falls back
    to GITHUB_TOKEN (automatically available in GitHub Actions runners).
    Raises RuntimeError if config is missing, propagates HTTPError on API failure.
    """
    token = os.getenv("GITHUB_DISPATCH_TOKEN") or os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GITHUB_REPO", "")
    if not token:
        raise RuntimeError("GITHUB_DISPATCH_TOKEN (or GITHUB_TOKEN) env var must be set")
    if not repo:
        raise RuntimeError("GITHUB_REPO env var must be set (e.g. 'owner/repo')")

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{_WORKFLOW_FILE}/dispatches"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        },
        json={"ref": "main", "inputs": {"payload": json.dumps(payload)}},
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("Dispatched generate-video workflow for job %s", payload.get("job_id"))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_github_dispatch.py -v
```
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/agents/github_dispatch.py tests/test_github_dispatch.py
git commit -m "feat: add github_dispatch module replacing Cloud Tasks trigger"
```

---

## Task 2: Update `app/config.py`

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: Add new constants and remove Cloud Run/Tasks constants**

In `app/config.py`, replace lines 54-56:
```python
# Cloud Tasks
CLOUD_RUN_URL = os.getenv("CLOUD_RUN_URL", "https://autoframe-353645494126.us-central1.run.app")
TASKS_QUEUE = os.getenv("TASKS_QUEUE", "autoframe-generate")
```

With:
```python
# GitHub Actions dispatch
GITHUB_DISPATCH_TOKEN = os.getenv("GITHUB_DISPATCH_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
```

Also remove the `PROJECT_ID` and `LOCATION` constants if they are no longer imported anywhere else after Tasks 3 and 4. For now, leave them.

- [ ] **Step 2: Run existing tests to confirm nothing breaks**

```bash
pytest tests/ -v
```
Expected: all tests that passed before still pass. Tests that imported `CLOUD_RUN_URL` or `TASKS_QUEUE` will fail — fix those imports in the next step.

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "chore: replace CLOUD_RUN_URL/TASKS_QUEUE config with GITHUB_DISPATCH_TOKEN/GITHUB_REPO"
```

---

## Task 3: Replace `_enqueue_generate()` in `whatsapp_agent.py`

**Files:**
- Modify: `app/agents/whatsapp_agent.py`
- Create: `tests/test_enqueue_generate.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_enqueue_generate.py
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
    assert "generate-batch_20240101_120000-CODE01" in job["job_id"]


def test_enqueue_generate_stories_channel(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    dispatched_payloads = []

    import app.agents.whatsapp_agent as wa
    import app.agents.github_dispatch as gd

    original_dispatch = gd.dispatch_video_generation

    def capture_dispatch(payload):
        dispatched_payloads.append(payload)
        return original_dispatch.__wrapped__(payload) if hasattr(original_dispatch, '__wrapped__') else None

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp):
        with patch.object(wa.firestore_service, "create_or_update_job"):
            wa._enqueue_generate(
                headline="Hindi Story",
                code="STORY01",
                batch_id="stories_20240101_070000",
                channel_id="stories",
            )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_enqueue_generate.py::test_enqueue_generate_creates_job_and_dispatches_workflow -v
```
Expected: FAIL — `_enqueue_generate` still uses Cloud Tasks.

- [ ] **Step 3: Replace `_enqueue_generate()` in `whatsapp_agent.py`**

Replace the entire function at lines 684-770 with:

```python
def _enqueue_generate(
    headline: str,
    code: str,
    batch_id: str,
    public_id: str = "",
    force_run: bool = False,
    genre: str = "",
    details: str = "",
    virality_score: float | int | None = None,
    source: str = "telegram",
    idempotency_scope: str | None = None,
    idempotency_key: str | None = None,
    channel_id: str = "news",
) -> bool:
    """Dispatch a GitHub Actions workflow to generate a video. Returns True if dispatched."""
    from app.agents.github_dispatch import dispatch_video_generation

    raw_name = f"generate-{batch_id}-{code}"
    task_name = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_name)
    video_public_id = public_id or _public_video_id(task_name)
    job_id = task_name

    payload_dict = {
        "headline": headline,
        "code": code,
        "batch_id": batch_id,
        "job_id": job_id,
        "public_id": video_public_id,
        "force_run": bool(force_run),
        "genre": genre,
        "details": details,
        "virality_score": float(virality_score or 0),
        "channel_id": channel_id,
    }
    if idempotency_scope and idempotency_key:
        payload_dict["idempotency_scope"] = idempotency_scope
        payload_dict["idempotency_key"] = idempotency_key

    firestore_service.create_or_update_job(
        job_id,
        {
            "job_id": job_id,
            "batch_id": batch_id,
            "code": code,
            "topic": headline,
            "source": source,
            "status": "queued",
            "public_id": video_public_id,
            "genre": genre,
            "details": details,
            "virality_score": float(virality_score or 0),
            "channel_id": channel_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    dispatch_video_generation(payload_dict)
    return True
```

- [ ] **Step 4: Remove Cloud Tasks imports and unused config imports from `whatsapp_agent.py`**

Replace lines 9-26 (the Cloud Tasks try/except and config import):

```python
# Remove entirely:
try:
    from google.cloud import tasks_v2
    from google.api_core.exceptions import AlreadyExists
except Exception:
    tasks_v2 = None

    class AlreadyExists(Exception):
        pass

# Replace config import (lines 18-26):
from app.config import (
    TELEGRAM_CHAT_ID,
    get_chat_id,
    PROJECT_ID,
    LOCATION,
    CLOUD_RUN_URL,
    TASKS_QUEUE,
    CREATE_TOPIC_IDEMPOTENCY_TTL_SECONDS,
)
```

With:

```python
from app.config import (
    TELEGRAM_CHAT_ID,
    get_chat_id,
    CREATE_TOPIC_IDEMPOTENCY_TTL_SECONDS,
)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_enqueue_generate.py tests/test_github_dispatch.py -v
```
Expected: all PASSED.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all passing tests continue to pass. Fix any remaining `CLOUD_RUN_URL`/`TASKS_QUEUE` references if found.

- [ ] **Step 7: Commit**

```bash
git add app/agents/whatsapp_agent.py tests/test_enqueue_generate.py
git commit -m "feat: replace Cloud Tasks enqueue with GitHub Actions dispatch in whatsapp_agent"
```

---

## Task 4: Replace story_researcher Cloud Tasks enqueue

**Files:**
- Modify: `app/agents/story_researcher.py`
- Create: `tests/test_story_researcher_dispatch.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_story_researcher_dispatch.py
import pytest
from unittest.mock import MagicMock, patch


def test_story_researcher_run_dispatches_github_workflow(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    import app.agents.story_researcher as sr

    with patch("app.agents.github_dispatch.requests.post", return_value=mock_resp) as mock_post:
        with patch.object(sr.firestore_service, "get_pipeline_state", return_value={"state": "completed"}):
            with patch.object(sr, "_story_already_generated_today", return_value=False):
                with patch.object(sr, "_recently_used_titles", return_value=[]):
                    with patch.object(sr, "_select_story_genre", return_value="inspiring"):
                        with patch.object(sr, "generate_story_idea", return_value={"title": "Test Story", "premise": "A test premise"}):
                            with patch.object(sr, "_is_story_already_used", return_value=False):
                                with patch.object(sr.firestore_service, "save_news_batch"):
                                    with patch.object(sr.firestore_service, "set_pipeline_and_batch_state"):
                                        with patch.object(sr.firestore_service, "create_or_update_job"):
                                            with patch.object(sr, "_mark_story_used"):
                                                with patch.object(sr, "send_message"):
                                                    result = sr.run()

    assert result is not None
    mock_post.assert_called_once()
    call_body = mock_post.call_args[1]["json"]
    payload = __import__("json").loads(call_body["inputs"]["payload"])
    assert payload["channel_id"] == "stories"
    assert payload["script_type"] == "story"
    assert payload["language"] == "hi"
    assert payload["headline"] == "Test Story"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_story_researcher_dispatch.py -v
```
Expected: FAIL — story_researcher still uses Cloud Tasks.

- [ ] **Step 3: Replace the Cloud Tasks enqueue block in `story_researcher.py`**

Replace lines 225-264 (from `# Enqueue Cloud Task` comment through the `except Exception` block):

```python
    # Dispatch GitHub Actions workflow for video generation
    from app.agents.github_dispatch import dispatch_video_generation
    try:
        dispatch_video_generation({
            "headline": title,
            "code": code,
            "batch_id": batch_id,
            "job_id": job_id,
            "public_id": public_id,
            "force_run": True,
            "genre": mood,
            "details": premise,
            "virality_score": 0.0,
            "channel_id": "stories",
            "script_type": "story",
            "language": language,
        })
    except Exception as e:
        logger.exception(f"Failed to dispatch story generation workflow: {e}")
        firestore_service.set_pipeline_and_batch_state(batch_id, "failed", channel_id="stories")
        if STORIES_CHAT_ID:
            send_message(STORIES_CHAT_ID, f"❌ Failed to queue story: {e}", channel_id="stories")
        return None
```

- [ ] **Step 4: Remove unused imports from `story_researcher.py` (line 16)**

Replace:
```python
from app.config import STORIES_CHAT_ID, CLOUD_RUN_URL, PROJECT_ID, LOCATION, TASKS_QUEUE
```

With:
```python
from app.config import STORIES_CHAT_ID
```

Also remove the `from google.cloud import tasks_v2` and `from google.api_core.exceptions import AlreadyExists` lines at the top of `run()` (lines 131-132 in the original).

- [ ] **Step 5: Remove now-unused constants from `app/config.py`**

Remove these lines (they are no longer imported anywhere):
```python
PROJECT_ID = os.getenv("PROJECT_ID", "youtube-video-generator-492211")
LOCATION = os.getenv("LOCATION", "us-central1")
```

Verify first: `grep -r "PROJECT_ID\|LOCATION" app/ --include="*.py"`. Remove only if the grep shows no other usages.

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_story_researcher_dispatch.py tests/ -v
```
Expected: all passing.

- [ ] **Step 7: Remove `google-cloud-tasks` from `requirements.txt`**

Delete the line `google-cloud-tasks` from `requirements.txt`.

- [ ] **Step 8: Commit**

```bash
git add app/agents/story_researcher.py app/config.py requirements.txt tests/test_story_researcher_dispatch.py
git commit -m "feat: replace Cloud Tasks enqueue with GitHub Actions dispatch in story_researcher"
```

---

## Task 5: Create `scripts/` entry point scripts

**Files:** Create 7 files in `scripts/`

- [ ] **Step 1: Create `scripts/run_research.py`**

```python
# scripts/run_research.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import lead_researcher

lead_researcher.run()
```

- [ ] **Step 2: Create `scripts/run_stories.py`**

```python
# scripts/run_stories.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import story_researcher

story_researcher.run()
```

- [ ] **Step 3: Create `scripts/run_daily_digest.py`**

```python
# scripts/run_daily_digest.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import lead_researcher

lead_researcher.send_daily_digest()
```

- [ ] **Step 4: Create `scripts/run_stories_digest.py`**

```python
# scripts/run_stories_digest.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routes.stories import _send_stories_daily_digest

_send_stories_daily_digest()
```

- [ ] **Step 5: Create `scripts/run_update_analytics.py`**

```python
# scripts/run_update_analytics.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import lead_researcher
from app.services import firestore_service, youtube_service

lead_researcher.update_domain_schedule()

jobs = firestore_service.list_recent_jobs(limit=200)
updated = 0
for job in jobs:
    if job.get("status") != "completed":
        continue
    video_id = youtube_service.extract_video_id(job.get("youtube_url", ""))
    if not video_id:
        continue
    analytics = youtube_service.fetch_video_analytics(video_id)
    if analytics:
        firestore_service.update_job_analytics(job["job_id"], analytics)
        updated += 1

print(f"Updated analytics for {updated} jobs")
```

- [ ] **Step 6: Create `scripts/run_refresh_auth.py`**

```python
# scripts/run_refresh_auth.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import youtube_service, telegram_service
from app.config import get_chat_id

results = youtube_service.refresh_all_tokens()
failed_channels = [ch for ch, status in results.items() if status != "ok"]

if failed_channels:
    for ch in failed_channels:
        channel_label = "Short Tales" if ch == "stories" else "Kurrent Affairs"
        reauth_url = youtube_service._auth_url(ch)
        alert = (
            f"🔴 *YouTube OAuth token refresh failed!*\n\n"
            f"*{channel_label}* (`{ch}`) — {results[ch]}\n"
            f"Re-authenticate: {reauth_url}\n\n"
            "_Open the link above in a browser to reconnect._"
        )
        try:
            telegram_service.send_message(get_chat_id(ch), alert, channel_id=ch)
        except Exception:
            pass

print(f"Token refresh results: {results}")
```

- [ ] **Step 7: Create `scripts/run_generate_video.py`**

```python
# scripts/run_generate_video.py
"""Entry point for the generate-video GitHub Actions workflow.

Reads the full job payload from the GENERATE_PAYLOAD env var (JSON string set
by the workflow from the workflow_dispatch input) and calls generator_agent.run().
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import generator_agent

raw = os.environ["GENERATE_PAYLOAD"]
p = json.loads(raw)

generator_agent.run(
    p["headline"],
    p["code"],
    batch_id=p.get("batch_id"),
    job_id=p.get("job_id"),
    public_id=p.get("public_id"),
    force_run=bool(p.get("force_run", False)),
    genre=p.get("genre", ""),
    details=p.get("details", ""),
    virality_score=float(p.get("virality_score", 0) or 0),
    channel_id=p.get("channel_id", "news"),
    script_type=p.get("script_type", "news"),
    language=p.get("language", "en"),
)
```

- [ ] **Step 8: Commit**

```bash
git add scripts/
git commit -m "feat: add scripts/ entry points for GitHub Actions workflows"
```

---

## Task 6: Create `generate-video.yml` GitHub Actions workflow

**Files:**
- Create: `.github/workflows/generate-video.yml`

- [ ] **Step 1: Create the directory**

```bash
mkdir -p .github/workflows
```

- [ ] **Step 2: Create `.github/workflows/generate-video.yml`**

```yaml
name: Generate Video

on:
  workflow_dispatch:
    inputs:
      payload:
        description: "JSON payload for generator_agent.run()"
        required: true
        type: string

concurrency:
  group: video-generation
  cancel-in-progress: false

jobs:
  generate:
    runs-on: ubuntu-latest
    timeout-minutes: 60

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.10
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - name: Install system packages
        run: sudo apt-get update && sudo apt-get install -y ffmpeg fonts-dejavu-core fonts-indic

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Set up GCP credentials
        run: |
          echo '${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}' > /tmp/gcp_key.json
          echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp_key.json" >> $GITHUB_ENV

      - name: Generate video
        env:
          GENERATE_PAYLOAD: ${{ inputs.payload }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          STORIES_BOT_TOKEN: ${{ secrets.STORIES_BOT_TOKEN }}
          STORIES_CHAT_ID: ${{ secrets.STORIES_CHAT_ID }}
          YOUTUBE_CLIENT_ID: ${{ secrets.YOUTUBE_CLIENT_ID }}
          YOUTUBE_CLIENT_SECRET: ${{ secrets.YOUTUBE_CLIENT_SECRET }}
          YOUTUBE_REDIRECT_URI: ${{ secrets.YOUTUBE_REDIRECT_URI }}
          STORIES_YOUTUBE_CLIENT_ID: ${{ secrets.STORIES_YOUTUBE_CLIENT_ID }}
          STORIES_YOUTUBE_CLIENT_SECRET: ${{ secrets.STORIES_YOUTUBE_CLIENT_SECRET }}
          STORIES_YOUTUBE_REDIRECT_URI: ${{ secrets.STORIES_YOUTUBE_REDIRECT_URI }}
          BUCKET_NAME: ${{ secrets.BUCKET_NAME }}
        run: python scripts/run_generate_video.py
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/generate-video.yml
git commit -m "feat: add generate-video GitHub Actions workflow"
```

---

## Task 7: Create 6 scheduled GitHub Actions workflows

**Files:** Create 6 files in `.github/workflows/`

All 6 workflows share a common structure: checkout, Python 3.10, pip install (no apt-get — no ffmpeg/fonts needed), GCP credentials, run script with secrets as env vars.

**Note on GitHub Actions dispatch from scheduled workflows:** Scheduled workflows use `permissions: actions: write` so that `GITHUB_TOKEN` (auto-available) can trigger `workflow_dispatch` on `generate-video.yml`. The Python code reads `GITHUB_TOKEN` as a fallback when `GITHUB_DISPATCH_TOKEN` is not set.

- [ ] **Step 1: Create `.github/workflows/research-run.yml`**

```yaml
name: Research Run

on:
  schedule:
    - cron: "30 18,2,10 * * *"  # 12:00am, 8:00am, 4:00pm IST
  workflow_dispatch:

permissions:
  actions: write
  contents: read

jobs:
  research:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.10
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Set up GCP credentials
        run: |
          echo '${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}' > /tmp/gcp_key.json
          echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp_key.json" >> $GITHUB_ENV

      - name: Run research
        env:
          GNEWS_API_KEY: ${{ secrets.GNEWS_API_KEY }}
          GOOGLE_SEARCH_API_KEY: ${{ secrets.GOOGLE_SEARCH_API_KEY }}
          GOOGLE_SEARCH_ENGINE_ID: ${{ secrets.GOOGLE_SEARCH_ENGINE_ID }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          GITHUB_REPO: ${{ github.repository }}
        run: python scripts/run_research.py
```

- [ ] **Step 2: Create `.github/workflows/stories-run.yml`**

```yaml
name: Stories Run

on:
  schedule:
    - cron: "30 1,5,8,12 * * *"  # 7:00am, 11:00am, 2:00pm, 6:00pm IST
  workflow_dispatch:

permissions:
  actions: write
  contents: read

jobs:
  stories:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.10
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Set up GCP credentials
        run: |
          echo '${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}' > /tmp/gcp_key.json
          echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp_key.json" >> $GITHUB_ENV

      - name: Run stories scheduler
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          STORIES_BOT_TOKEN: ${{ secrets.STORIES_BOT_TOKEN }}
          STORIES_CHAT_ID: ${{ secrets.STORIES_CHAT_ID }}
          GITHUB_REPO: ${{ github.repository }}
        run: python scripts/run_stories.py
```

- [ ] **Step 3: Create `.github/workflows/daily-digest.yml`**

```yaml
name: Daily Digest

on:
  schedule:
    - cron: "30 2 * * *"  # 8:00am IST
  workflow_dispatch:

jobs:
  digest:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.10
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Set up GCP credentials
        run: |
          echo '${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}' > /tmp/gcp_key.json
          echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp_key.json" >> $GITHUB_ENV

      - name: Send daily digest
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          YOUTUBE_CLIENT_ID: ${{ secrets.YOUTUBE_CLIENT_ID }}
          YOUTUBE_CLIENT_SECRET: ${{ secrets.YOUTUBE_CLIENT_SECRET }}
          YOUTUBE_REDIRECT_URI: ${{ secrets.YOUTUBE_REDIRECT_URI }}
        run: python scripts/run_daily_digest.py
```

- [ ] **Step 4: Create `.github/workflows/stories-daily-digest.yml`**

```yaml
name: Stories Daily Digest

on:
  schedule:
    - cron: "0 3 * * *"  # 8:30am IST
  workflow_dispatch:

jobs:
  digest:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.10
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Set up GCP credentials
        run: |
          echo '${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}' > /tmp/gcp_key.json
          echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp_key.json" >> $GITHUB_ENV

      - name: Send stories digest
        env:
          STORIES_BOT_TOKEN: ${{ secrets.STORIES_BOT_TOKEN }}
          STORIES_CHAT_ID: ${{ secrets.STORIES_CHAT_ID }}
          STORIES_YOUTUBE_CLIENT_ID: ${{ secrets.STORIES_YOUTUBE_CLIENT_ID }}
          STORIES_YOUTUBE_CLIENT_SECRET: ${{ secrets.STORIES_YOUTUBE_CLIENT_SECRET }}
          STORIES_YOUTUBE_REDIRECT_URI: ${{ secrets.STORIES_YOUTUBE_REDIRECT_URI }}
        run: python scripts/run_stories_digest.py
```

- [ ] **Step 5: Create `.github/workflows/update-analytics.yml`**

```yaml
name: Update Analytics

on:
  schedule:
    - cron: "30 16 * * *"  # 10:00pm IST
  workflow_dispatch:

jobs:
  analytics:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.10
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Set up GCP credentials
        run: |
          echo '${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}' > /tmp/gcp_key.json
          echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp_key.json" >> $GITHUB_ENV

      - name: Update analytics
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          YOUTUBE_CLIENT_ID: ${{ secrets.YOUTUBE_CLIENT_ID }}
          YOUTUBE_CLIENT_SECRET: ${{ secrets.YOUTUBE_CLIENT_SECRET }}
          YOUTUBE_REDIRECT_URI: ${{ secrets.YOUTUBE_REDIRECT_URI }}
        run: python scripts/run_update_analytics.py
```

- [ ] **Step 6: Create `.github/workflows/refresh-youtube-auth.yml`**

```yaml
name: Refresh YouTube Auth

on:
  schedule:
    - cron: "0 0,6,12,18 * * *"  # every 6 hours
  workflow_dispatch:

jobs:
  refresh:
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.10
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Set up GCP credentials
        run: |
          echo '${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}' > /tmp/gcp_key.json
          echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp_key.json" >> $GITHUB_ENV

      - name: Refresh tokens
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          STORIES_BOT_TOKEN: ${{ secrets.STORIES_BOT_TOKEN }}
          STORIES_CHAT_ID: ${{ secrets.STORIES_CHAT_ID }}
          YOUTUBE_CLIENT_ID: ${{ secrets.YOUTUBE_CLIENT_ID }}
          YOUTUBE_CLIENT_SECRET: ${{ secrets.YOUTUBE_CLIENT_SECRET }}
          YOUTUBE_REDIRECT_URI: ${{ secrets.YOUTUBE_REDIRECT_URI }}
          STORIES_YOUTUBE_CLIENT_ID: ${{ secrets.STORIES_YOUTUBE_CLIENT_ID }}
          STORIES_YOUTUBE_CLIENT_SECRET: ${{ secrets.STORIES_YOUTUBE_CLIENT_SECRET }}
          STORIES_YOUTUBE_REDIRECT_URI: ${{ secrets.STORIES_YOUTUBE_REDIRECT_URI }}
        run: python scripts/run_refresh_auth.py
```

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/
git commit -m "feat: add 6 scheduled GitHub Actions workflows replacing Cloud Scheduler"
```

---

## Task 8: Create `api/_shared.py`

**Files:**
- Create: `api/_shared.py`

The underscore prefix prevents Vercel from treating this as a serverless function endpoint.

- [ ] **Step 1: Create `api/_shared.py`**

```python
# api/_shared.py
"""Shared helpers for Vercel serverless functions."""
import json
import os


def setup_credentials() -> None:
    """Write GCP_SERVICE_ACCOUNT_JSON to /tmp and set GOOGLE_APPLICATION_CREDENTIALS.

    Must be called before importing any google.cloud modules. Safe to call
    multiple times — skips if credentials are already configured.
    """
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return
    key = os.getenv("GCP_SERVICE_ACCOUNT_JSON", "")
    if not key:
        return
    key_path = "/tmp/gcp_key.json"
    if not os.path.exists(key_path):
        with open(key_path, "w") as f:
            f.write(key)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_path


def require_admin(headers: dict, query_params: dict, secret: str) -> bool:
    """Return True if the request passes admin auth, False if it should be rejected."""
    if not secret:
        return True
    provided = headers.get("x-admin-secret", "") or query_params.get("secret", [""])[0]
    return provided == secret


def json_response(handler, code: int, data: dict) -> None:
    """Write a JSON HTTP response via a BaseHTTPRequestHandler."""
    body = json.dumps(data).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
```

- [ ] **Step 2: Commit**

```bash
git add api/_shared.py
git commit -m "feat: add api/_shared.py with GCP credential setup and Vercel helpers"
```

---

## Task 9: Create 4 Vercel admin metric functions

**Files:** Create 4 files in `api/admin/metrics/`

Each function: call `setup_credentials()` first, then import app modules, parse query params, check admin auth, run the existing route logic, return JSON.

- [ ] **Step 1: Create `api/admin/metrics/summary.py`**

```python
# api/admin/metrics/summary.py
import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from api._shared import setup_credentials, require_admin, json_response
setup_credentials()

from app.config import ADMIN_DASHBOARD_SECRET
from app.routes.admin import (
    _channel_id, _social_key, _hours_ago, _genre_performance, _parse_iso
)
from app.services import firestore_service, youtube_service
from datetime import datetime, timezone


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not require_admin(dict(self.headers), params, ADMIN_DASHBOARD_SECRET):
            json_response(self, 403, {"error": "Forbidden"})
            return

        channel = _channel_id(params.get("channel_id", ["news"])[0])
        pipeline = firestore_service.get_pipeline_state(channel_id=channel)
        queue = firestore_service.get_queue_snapshot(channel_id=channel)
        lock = firestore_service.get_current_lock()
        quota = firestore_service.get_quota_usage_snapshot()
        social = firestore_service.get_social_metrics(_social_key(channel))
        jobs = [
            j for j in firestore_service.list_recent_jobs(limit=200)
            if j.get("channel_id", "news") == channel
        ]

        cutoff = _hours_ago(24)
        total_24h = completed_24h = failed_24h = 0
        durations = []
        last_run_at = None
        for j in jobs:
            updated = _parse_iso(j.get("updated_at"))
            if updated and (last_run_at is None or updated > last_run_at):
                last_run_at = updated
            if not updated or updated.timestamp() < cutoff:
                continue
            status = j.get("status")
            if status in ("completed", "failed"):
                total_24h += 1
            if status == "completed":
                completed_24h += 1
            if status == "failed":
                failed_24h += 1
            start = _parse_iso(j.get("started_at"))
            end = _parse_iso(j.get("finished_at"))
            if start and end and end >= start:
                durations.append((end - start).total_seconds())

        success_rate = round((completed_24h / total_24h) * 100, 1) if total_24h else 0.0
        avg_duration = round(sum(durations) / len(durations), 1) if durations else 0.0

        json_response(self, 200, {
            "channel_id": channel,
            "queue": queue,
            "pipeline": pipeline,
            "lock": lock,
            "quota": quota,
            "jobs_24h": {
                "total": total_24h,
                "completed": completed_24h,
                "failed": failed_24h,
                "success_rate_pct": success_rate,
                "avg_duration_seconds": avg_duration,
            },
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "youtube": {
                "subscriber_count": int(social.get("subscriber_count", 0)),
                "view_count": int(social.get("view_count", 0)),
                "video_count": int(social.get("video_count", 0)),
                "updated_at": social.get("updated_at"),
            },
            "genre_performance_14d": _genre_performance(jobs, hours=24 * 14),
        })

    def log_message(self, *args):
        pass
```

- [ ] **Step 2: Create `api/admin/metrics/jobs.py`**

```python
# api/admin/metrics/jobs.py
import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from api._shared import setup_credentials, require_admin, json_response
setup_credentials()

from app.config import ADMIN_DASHBOARD_SECRET
from app.routes.admin import _channel_id
from app.services import firestore_service


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not require_admin(dict(self.headers), params, ADMIN_DASHBOARD_SECRET):
            json_response(self, 403, {"error": "Forbidden"})
            return

        channel = _channel_id(params.get("channel_id", ["news"])[0])
        raw_limit = params.get("limit", ["50"])[0]
        safe_limit = max(1, min(int(raw_limit), 200))

        jobs = [
            j for j in firestore_service.list_recent_jobs(limit=500)
            if j.get("channel_id", "news") == channel
        ][:safe_limit]

        json_response(self, 200, {"channel_id": channel, "jobs": jobs})

    def log_message(self, *args):
        pass
```

- [ ] **Step 3: Create `api/admin/metrics/failures.py`**

```python
# api/admin/metrics/failures.py
import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from api._shared import setup_credentials, require_admin, json_response
setup_credentials()

from app.config import ADMIN_DASHBOARD_SECRET
from app.routes.admin import _channel_id, _hours_ago, _parse_iso
from app.services import firestore_service


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not require_admin(dict(self.headers), params, ADMIN_DASHBOARD_SECRET):
            json_response(self, 403, {"error": "Forbidden"})
            return

        channel = _channel_id(params.get("channel_id", ["news"])[0])
        raw_hours = params.get("hours", ["24"])[0]
        cutoff = _hours_ago(max(1, min(int(raw_hours), 168)))

        jobs = [
            j for j in firestore_service.list_recent_jobs(limit=500)
            if j.get("channel_id", "news") == channel
        ]
        grouped = {}
        failures = []
        for j in jobs:
            if j.get("status") != "failed":
                continue
            updated = _parse_iso(j.get("updated_at"))
            if not updated or updated.timestamp() < cutoff:
                continue
            err = j.get("error_type", "unknown")
            grouped[err] = grouped.get(err, 0) + 1
            failures.append(j)

        json_response(self, 200, {
            "channel_id": channel,
            "by_error_type": grouped,
            "failures": failures,
        })

    def log_message(self, *args):
        pass
```

- [ ] **Step 4: Create `api/admin/metrics/refresh-social.py`**

```python
# api/admin/metrics/refresh-social.py
import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from api._shared import setup_credentials, require_admin, json_response
setup_credentials()

from app.config import ADMIN_DASHBOARD_SECRET
from app.routes.admin import _channel_id, _social_key
from app.services import firestore_service, youtube_service


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if not require_admin(dict(self.headers), params, ADMIN_DASHBOARD_SECRET):
            json_response(self, 403, {"error": "Forbidden"})
            return

        channel = _channel_id(params.get("channel_id", ["news"])[0])
        try:
            stats = youtube_service.get_channel_stats(channel_id=channel)
            key = _social_key(channel)
            firestore_service.save_social_metrics(key, stats)
            latest = firestore_service.get_social_metrics(key)
            json_response(self, 200, {"status": "ok", "channel_id": channel, "youtube": latest})
        except Exception as e:
            json_response(self, 500, {"error": f"YouTube stats refresh failed: {e}"})

    def log_message(self, *args):
        pass
```

- [ ] **Step 5: Commit**

```bash
git add api/admin/
git commit -m "feat: add Vercel admin metrics serverless functions"
```

---

## Task 10: Create 2 Vercel Telegram webhook functions

**Files:** Create 2 files in `api/webhook/`

These functions receive Telegram updates, call the existing agent `handle_reply()`, and return 200. The agent's `_enqueue_generate()` now dispatches to GitHub Actions via `dispatch_video_generation()`.

- [ ] **Step 1: Create `api/webhook/telegram.py`**

```python
# api/webhook/telegram.py
import sys
import os
import json
import logging
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from api._shared import setup_credentials, json_response
setup_credentials()

from app.config import TELEGRAM_CHAT_ID
from app.agents import whatsapp_agent
from app.services.firestore_service import is_duplicate_telegram_update

logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw)
        except Exception:
            json_response(self, 400, {"error": "invalid json"})
            return

        update_id = data.get("update_id")
        message = data.get("message", {})
        text = (message.get("text") or "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        if not text or chat_id != str(TELEGRAM_CHAT_ID):
            json_response(self, 200, {"ok": True})
            return

        if update_id is not None and is_duplicate_telegram_update(update_id, "news"):
            json_response(self, 200, {"ok": True})
            return

        try:
            whatsapp_agent.handle_reply(chat_id, text)
        except Exception as e:
            logger.exception(f"handle_reply failed: {e}")

        json_response(self, 200, {"ok": True})

    def log_message(self, *args):
        pass
```

- [ ] **Step 2: Create `api/webhook/stories.py`**

```python
# api/webhook/stories.py
import sys
import os
import json
import logging
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from api._shared import setup_credentials, json_response
setup_credentials()

from app.config import STORIES_CHAT_ID
from app.agents import stories_agent
from app.services.firestore_service import is_duplicate_telegram_update

logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw)
        except Exception:
            json_response(self, 400, {"error": "invalid json"})
            return

        update_id = data.get("update_id")
        message = data.get("message", {})
        text = (message.get("text") or "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        if not text or chat_id != str(STORIES_CHAT_ID):
            json_response(self, 200, {"ok": True})
            return

        if update_id is not None and is_duplicate_telegram_update(update_id, "stories"):
            json_response(self, 200, {"ok": True})
            return

        try:
            stories_agent.handle_reply(chat_id, text)
        except Exception as e:
            logger.exception(f"stories handle_reply failed: {e}")

        json_response(self, 200, {"ok": True})

    def log_message(self, *args):
        pass
```

- [ ] **Step 3: Verify that `whatsapp_agent.handle_reply` and `stories_agent.handle_reply` exist**

```bash
grep -n "def handle_reply" app/agents/whatsapp_agent.py app/agents/stories_agent.py
```
Expected: at least one match per file.

- [ ] **Step 4: Commit**

```bash
git add api/webhook/
git commit -m "feat: add Vercel Telegram webhook serverless functions"
```

---

## Task 11: Create `public/admin.html`, `vercel.json`, `requirements-vercel.txt`

**Files:**
- Create: `public/admin.html` (copy of `app/static/admin.html`, no changes)
- Create: `vercel.json`
- Create: `requirements-vercel.txt`

- [ ] **Step 1: Create `public/` directory and copy admin.html**

```bash
mkdir -p public
cp app/static/admin.html public/admin.html
```

The fetch paths in admin.html (`/admin/metrics/summary` etc.) work as-is because `vercel.json` rewrites `/admin/metrics/:path*` to `/api/admin/metrics/:path*`.

- [ ] **Step 2: Create `vercel.json`**

```json
{
  "rewrites": [
    { "source": "/admin", "destination": "/admin.html" },
    {
      "source": "/admin/metrics/:path*",
      "destination": "/api/admin/metrics/:path*"
    },
    {
      "source": "/webhook/telegram",
      "destination": "/api/webhook/telegram"
    },
    {
      "source": "/webhook/telegram/stories",
      "destination": "/api/webhook/stories"
    }
  ]
}
```

- [ ] **Step 3: Create `requirements-vercel.txt`**

```
google-cloud-firestore
google-cloud-storage
google-api-python-client
google-auth-oauthlib
requests
httpx
```

This excludes: `moviepy`, `Pillow`, `vertexai`, `google-cloud-texttospeech`, `google-cloud-tasks`, `uvicorn[standard]`, `python-multipart`, `fastapi` — none are needed in the Vercel serverless context.

- [ ] **Step 4: Delete `Dockerfile`**

```bash
git rm Dockerfile
```

- [ ] **Step 5: Commit**

```bash
git add public/admin.html vercel.json requirements-vercel.txt
git commit -m "feat: add Vercel static files, routing config, and light requirements"
```

---

## Task 12: Deploy to Vercel and register Telegram webhooks

**Prerequisites:** Vercel account, `vercel` CLI installed (`npm i -g vercel`), or deploy via Vercel dashboard.

- [ ] **Step 1: Link the repo to Vercel**

In Vercel dashboard: New Project → Import from GitHub → select this repo.

Under **Build & Development Settings**:
- Framework Preset: **Other**
- Build Command: *(leave blank)*
- Output Directory: `public`
- Install Command: `pip install -r requirements-vercel.txt`

- [ ] **Step 2: Set all Vercel environment variables**

In Vercel dashboard → Settings → Environment Variables, add:

```
GCP_SERVICE_ACCOUNT_JSON    = <full JSON content of the GCP service account key>
TELEGRAM_BOT_TOKEN          = <from Cloud Run env>
TELEGRAM_CHAT_ID            = <from Cloud Run env>
STORIES_BOT_TOKEN           = <from Cloud Run env>
STORIES_CHAT_ID             = <from Cloud Run env>
GNEWS_API_KEY               = <from Cloud Run env>
GOOGLE_SEARCH_API_KEY       = <from Cloud Run env>
GOOGLE_SEARCH_ENGINE_ID     = <from Cloud Run env>
YOUTUBE_CLIENT_ID           = <from Cloud Run env>
YOUTUBE_CLIENT_SECRET       = <from Cloud Run env>
YOUTUBE_REDIRECT_URI        = <update to new Vercel domain if needed>
STORIES_YOUTUBE_CLIENT_ID   = <from Cloud Run env>
STORIES_YOUTUBE_CLIENT_SECRET = <from Cloud Run env>
STORIES_YOUTUBE_REDIRECT_URI  = <update to new Vercel domain if needed>
ADMIN_DASHBOARD_SECRET      = <from Cloud Run env>
GITHUB_DISPATCH_TOKEN       = <new PAT — see Step 3>
GITHUB_REPO                 = owner/repo-name
```

To copy current Cloud Run env vars:
```bash
gcloud run services describe autoframe \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --format="yaml(spec.template.spec.containers[0].env)"
```

- [ ] **Step 3: Create GitHub PAT for Vercel → GitHub Actions dispatch**

Go to: github.com/settings/tokens → Generate new token (classic)
- Scopes: ✅ `workflow`
- Expiration: No expiration (or 1 year)

Copy the token. Set it as `GITHUB_DISPATCH_TOKEN` in Vercel env vars (Step 2).

- [ ] **Step 4: Deploy to Vercel**

```bash
vercel --prod
```

Note the deployed URL (e.g., `https://autoframe-dashboard.vercel.app`).

- [ ] **Step 5: Register Telegram webhooks**

Replace `<VERCEL_URL>` with your actual Vercel domain:

```bash
# News bot
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook?url=https://<VERCEL_URL>/webhook/telegram" | python3 -m json.tool

# Stories bot
curl -s "https://api.telegram.org/bot${STORIES_BOT_TOKEN}/setWebhook?url=https://<VERCEL_URL>/webhook/telegram/stories" | python3 -m json.tool
```

Expected response: `{"ok": true, "result": true, "description": "Webhook was set"}`

- [ ] **Step 6: Verify admin dashboard**

Open: `https://<VERCEL_URL>/admin?secret=<ADMIN_DASHBOARD_SECRET>`

Expected: dashboard loads, cards show data, channel tabs switch correctly.

- [ ] **Step 7: Verify Telegram bots**

Send `STATS` from both bots. Expected: response within ~5 seconds with channel stats.

---

## Task 13: Add GitHub Secrets, test workflows, and teardown GCP

- [ ] **Step 1: Add GitHub Secrets**

In the repo → Settings → Secrets and Variables → Actions → New repository secret, add all of the following:

```
GCP_SERVICE_ACCOUNT_JSON         (same as Vercel)
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
STORIES_BOT_TOKEN
STORIES_CHAT_ID
GNEWS_API_KEY
GOOGLE_SEARCH_API_KEY
GOOGLE_SEARCH_ENGINE_ID
YOUTUBE_CLIENT_ID
YOUTUBE_CLIENT_SECRET
YOUTUBE_REDIRECT_URI
STORIES_YOUTUBE_CLIENT_ID
STORIES_YOUTUBE_CLIENT_SECRET
STORIES_YOUTUBE_REDIRECT_URI
BUCKET_NAME                      (yt-gen-app-bucket)
```

Note: `GITHUB_DISPATCH_TOKEN` is NOT needed as a GitHub Secret — scheduled workflows use `GITHUB_TOKEN` automatically (set via the `permissions: actions: write` block).

- [ ] **Step 2: Manually trigger `research-run.yml` to test**

In GitHub: Actions tab → Research Run → Run workflow → Run workflow.

Watch the run. Expected: completes in < 5 minutes, new batch appears in Firestore `pipeline_state`.

- [ ] **Step 3: Manually trigger `generate-video.yml` to test end-to-end**

Get a recent `batch_id` from Firestore (or use the one from Step 2).

In GitHub: Actions tab → Generate Video → Run workflow → paste a test payload:

```json
{"headline":"Test Video","code":"TEST01","batch_id":"batch_test","job_id":"generate-batch-test-TEST01","public_id":"TESTPUB1","force_run":true,"genre":"inspiring","details":"Test run","virality_score":5.0,"channel_id":"news","script_type":"news","language":"en"}
```

Watch the run. Expected: Imagen + TTS calls, video uploaded to GCS, Telegram notification arrives.

- [ ] **Step 4: Teardown Cloud Scheduler jobs**

```bash
gcloud scheduler jobs delete autoframe-lead-researcher --project=youtube-video-generator-492211 --location=us-central1 --quiet
gcloud scheduler jobs delete autoframe-daily-digest --project=youtube-video-generator-492211 --location=us-central1 --quiet
gcloud scheduler jobs delete autoframe-update-analytics --project=youtube-video-generator-492211 --location=us-central1 --quiet
gcloud scheduler jobs delete autoframe-stories-run --project=youtube-video-generator-492211 --location=us-central1 --quiet
gcloud scheduler jobs delete autoframe-stories-digest --project=youtube-video-generator-492211 --location=us-central1 --quiet
gcloud scheduler jobs delete autoframe-refresh-youtube-auth --project=youtube-video-generator-492211 --location=us-central1 --quiet 2>/dev/null || true
```

- [ ] **Step 5: Teardown Cloud Tasks queue**

```bash
gcloud tasks queues delete autoframe-generate \
  --project=youtube-video-generator-492211 \
  --location=us-central1 \
  --quiet
```

- [ ] **Step 6: Teardown Cloud Run service**

```bash
gcloud run services delete autoframe \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --quiet
```

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "chore: complete Cloud Run → Vercel + GitHub Actions migration"
```

---

## Verification Checklist

| Check | Command / Action | Expected |
|---|---|---|
| Admin dashboard loads | Open `https://<VERCEL_URL>/admin?secret=...` | Cards render, jobs table populates |
| Channel tabs work | Click "Stories" tab | Metrics update for stories channel |
| News bot responds | Send `STATS` to Kurrent Affairs bot | Response in < 5s |
| Stories bot responds | Send `STATS` to Short Tales bot | Response in < 5s |
| CREATE triggers video gen | Send `CREATE AI news` | `generate-video` workflow appears in Actions tab |
| Scheduled research runs | Trigger `research-run.yml` manually | Firestore batch created, workflow dispatched |
| Video generation completes | Trigger `generate-video.yml` manually | GCS upload + Telegram notification |
| No Cloud Run traffic | Check Cloud Run logs after cutover | No new invocations |
