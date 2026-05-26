"""Vercel serverless function — OAuth callback for the Stories (Tell Me Why) channel."""
import sys
import os
import urllib.parse
import logging
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from api._shared import setup_credentials
setup_credentials()

import httpx
from app.services import firestore_service

logger = logging.getLogger(__name__)

_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _html(title: str, body: str) -> bytes:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>body{{font-family:sans-serif;text-align:center;padding:60px;max-width:500px;margin:auto}}</style>
</head><body>{body}</body></html>""".encode()


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
                timeout=15,
            )
            token = resp.json()
            if "error" in token:
                raise RuntimeError(token.get("error_description", token["error"]))

            refresh_token = token.get("refresh_token")
            expires_in = int(token.get("expires_in", 3600))
            token_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            firestore_service.save_youtube_tokens({
                "access_token": token["access_token"],
                "refresh_token": refresh_token,
                "token_expiry": token_expiry,
                "client_id": client_id,
                "client_secret": client_secret,
            }, channel_id="stories")

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
