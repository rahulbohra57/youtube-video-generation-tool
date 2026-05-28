"""Vercel serverless function — OAuth callback for the Stories (Tell Me Why) channel."""
import os
import json
import base64
import time
import urllib.parse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _get_gcp_access_token() -> str:
    """Mint a GCP service-account access token using a hand-rolled RS256 JWT.

    Avoids importing google.oauth2.service_account and google.auth.transport
    (both are heavy cold-start importers). Uses only cryptography + httpx,
    which are already present in the process.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

    key = json.loads(os.getenv("GCP_SERVICE_ACCOUNT_JSON", "{}"))
    now = int(time.time())

    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "iss": key["client_email"],
        "scope": "https://www.googleapis.com/auth/datastore",
        "aud": _TOKEN_URI,
        "iat": now,
        "exp": now + 3600,
    }).encode())

    signing_input = f"{header}.{payload}".encode()
    private_key = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    sig = _b64url(private_key.sign(signing_input, asym_padding.PKCS1v15(), hashes.SHA256()))

    resp = httpx.post(
        _TOKEN_URI,
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": f"{header}.{payload}.{sig}"},
        timeout=8,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _exchange_youtube_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
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
    return token


def _save_tokens_rest(tokens: dict, doc_name: str, access_token: str) -> None:
    fields = {k: {"stringValue": str(v)} for k, v in tokens.items() if v is not None}
    resp = httpx.patch(
        f"{_FS_BASE}/oauth_tokens/{doc_name}",
        json={"fields": fields},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=8,
    )
    resp.raise_for_status()


def _clear_auth_failure_rest(channel_id: str, access_token: str) -> None:
    try:
        httpx.delete(
            f"{_FS_BASE}/config/auth_failure_{channel_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=5,
        )
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

        client_id = os.getenv("STORIES_YOUTUBE_CLIENT_ID", "")
        client_secret = os.getenv("STORIES_YOUTUBE_CLIENT_SECRET", "")
        redirect_uri = os.getenv("STORIES_YOUTUBE_REDIRECT_URI", "")

        try:
            # Run YouTube code exchange and GCP token mint in parallel — both are
            # independent HTTP calls and were previously sequential (~1s wasted).
            with ThreadPoolExecutor(max_workers=2) as pool:
                yt_future = pool.submit(_exchange_youtube_code, code, client_id, client_secret, redirect_uri)
                gcp_future = pool.submit(_get_gcp_access_token)
                token = yt_future.result()
                gcp_token = gcp_future.result()

            refresh_token = token.get("refresh_token")
            expires_in = int(token.get("expires_in", 3600))
            token_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            _save_tokens_rest({
                "access_token": token["access_token"],
                "refresh_token": refresh_token,
                "token_expiry": token_expiry,
                "client_id": client_id,
                "client_secret": client_secret,
            }, doc_name="youtube_stories", access_token=gcp_token)
            _clear_auth_failure_rest("stories", gcp_token)

            body = _html(
                "Auth Complete",
                "<h1>&#x2705; Tell Me Why Connected</h1>"
                "<p>YouTube authentication is complete. Videos will now post automatically.</p>"
                "<p>You can close this tab.</p>",
            )
            self.send_response(200)
        except Exception as exc:
            logger.exception("Stories channel token exchange failed: %s", exc)
            body = _html("Auth Failed", f"<h1>&#x274C; Auth Failed</h1><p>{exc}</p>")
            self.send_response(500)

        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
