"""
One-shot local auth script for the Kurrent Affairs (news) channel.

Usage:
    export $(grep -v '^#' .env | xargs)
    python scripts/auth_news_local.py

Opens your browser → you click Allow → tokens are saved to Firestore.
No FastAPI server needed. Runs on localhost:8080 for a few seconds.
"""

import os
import sys
import json
import webbrowser
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
_REDIRECT_URI = "http://localhost:8080/auth/youtube/callback"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_SCOPES = " ".join([
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
])

_received_code = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _received_code
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/auth/youtube/callback"):
            self.send_response(204)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        error = params.get("error", [""])[0]
        code = params.get("code", [""])[0]

        if error or not code:
            body = f"<h1>Error: {error or 'no code'}</h1>".encode()
            self.send_response(400)
        else:
            _received_code = code
            body = b"<h1>Auth complete! You can close this tab.</h1>"
            self.send_response(200)

        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence request logs


def _exchange_code(code: str) -> dict:
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "redirect_uri": _REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(_TOKEN_URI, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _save_to_firestore(tokens: dict) -> None:
    from app.services import firestore_service
    firestore_service.save_youtube_tokens(tokens, channel_id="news")
    firestore_service.clear_auth_failure("news")


def main():
    if not _CLIENT_ID or not _CLIENT_SECRET:
        print("ERROR: YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET not set.")
        print("Run:  export $(grep -v '^#' .env | xargs)")
        sys.exit(1)

    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode({
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "response_type": "code",
            "scope": _SCOPES,
            "access_type": "offline",
            "prompt": "consent",
        })
    )

    print("Opening browser for Kurrent Affairs YouTube auth...")
    print(f"If it doesn't open, go to:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    server.timeout = 120
    print("Waiting for Google to redirect back (you have 2 minutes)...")
    server.handle_request()

    if not _received_code:
        print("ERROR: No auth code received.")
        sys.exit(1)

    print("Exchanging code for tokens...")
    token = _exchange_code(_received_code)
    if "error" in token:
        print(f"ERROR: {token.get('error_description', token['error'])}")
        sys.exit(1)

    expires_in = int(token.get("expires_in", 3600))
    token_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    print("Saving tokens to Firestore...")
    _save_to_firestore({
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token"),
        "token_expiry": token_expiry,
        "client_id": _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
    })

    print("\nDone! Kurrent Affairs is authenticated. Auto-posting will resume on the next scheduler run.")


if __name__ == "__main__":
    main()
