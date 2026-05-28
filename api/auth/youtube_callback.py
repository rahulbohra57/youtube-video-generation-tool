"""Vercel serverless function — OAuth callback for the News (Kurrent Affairs) channel."""
import os
import json
import urllib.parse
import logging
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_PROJECT_ID = "youtube-video-generator-492211"
_FS_BASE = f"https://firestore.googleapis.com/v1/projects/{_PROJECT_ID}/databases/(default)/documents"


def _html(title: str, body: str) -> bytes:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>body{{font-family:sans-serif;text-align:center;padding:60px;max-width:500px;margin:auto}}</style>
</head><body>{body}</body></html>""".encode()


def _get_gcp_access_token() -> str:
    """Mint a GCP access token from the service account JSON — no SDK, no gRPC."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleRequest
    key = json.loads(os.getenv("GCP_SERVICE_ACCOUNT_JSON", "{}"))
    creds = service_account.Credentials.from_service_account_info(
        key, scopes=["https://www.googleapis.com/auth/datastore"]
    )
    creds.refresh(GoogleRequest())
    return creds.token


def _save_tokens_rest(tokens: dict, doc_name: str) -> None:
    """Write tokens to Firestore via REST API (avoids heavy gRPC SDK cold-start)."""
    access_token = _get_gcp_access_token()
    fields = {k: {"stringValue": str(v)} for k, v in tokens.items() if v is not None}
    url = f"{_FS_BASE}/oauth_tokens/{doc_name}"
    resp = httpx.patch(
        url,
        json={"fields": fields},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=8,
    )
    resp.raise_for_status()
    return access_token


def _clear_auth_failure_rest(channel_id: str, access_token: str) -> None:
    """Delete the auth_failure config doc so run_refresh_auth.py stops skipping this channel."""
    url = f"{_FS_BASE}/config/auth_failure_{channel_id}"
    try:
        httpx.delete(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=5)
    except Exception:
        pass  # Non-critical — worst case the refresh workflow retries in 6h


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        error = params.get("error", [""])[0]
        code = params.get("code", [""])[0]

        if error or not code:
            body = _html("Auth Error", f"<h1>&#x274C; OAuth Error</h1><p>{error or 'No code returned.'}</p>")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        client_id = os.getenv("YOUTUBE_CLIENT_ID", "")
        client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "")
        redirect_uri = os.getenv("YOUTUBE_REDIRECT_URI", "")

        try:
            resp = httpx.post(
                _TOKEN_URI,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=8,
            )
            token = resp.json()
            if "error" in token:
                raise RuntimeError(token.get("error_description", token["error"]))

            refresh_token = token.get("refresh_token")
            expires_in = int(token.get("expires_in", 3600))
            token_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            gcp_token = _save_tokens_rest({
                "access_token": token["access_token"],
                "refresh_token": refresh_token,
                "token_expiry": token_expiry,
                "client_id": client_id,
                "client_secret": client_secret,
            }, doc_name="youtube_news")
            _clear_auth_failure_rest("news", gcp_token)

            body = _html(
                "Auth Complete",
                "<h1>&#x2705; Kurrent Affairs Connected</h1>"
                "<p>YouTube authentication is complete. Videos will now post automatically.</p>"
                "<p>You can close this tab.</p>",
            )
            self.send_response(200)
        except Exception as exc:
            logger.exception("News channel token exchange failed: %s", exc)
            body = _html("Auth Failed", f"<h1>&#x274C; Auth Failed</h1><p>{exc}</p>")
            self.send_response(500)

        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
