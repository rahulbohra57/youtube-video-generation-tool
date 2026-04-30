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
