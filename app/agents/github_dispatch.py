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
    to GITHUB_TOKEN (automatically available in GitHub Actions runners with
    permissions: actions: write).
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
