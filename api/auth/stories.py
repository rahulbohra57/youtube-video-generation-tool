"""Vercel serverless function — initiates YouTube OAuth for the Stories (Tell Me Why) channel."""
import sys
import os
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from api._shared import setup_credentials
setup_credentials()

_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        client_id = os.getenv("STORIES_YOUTUBE_CLIENT_ID", "")
        redirect_uri = os.getenv("STORIES_YOUTUBE_REDIRECT_URI", "")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": secrets.token_urlsafe(32),
        }
        url = _AUTH_URI + "?" + urllib.parse.urlencode(params)
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()
