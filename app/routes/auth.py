# app/routes/auth.py
#
# Manual OAuth 2.0 flow — bypasses google_auth_oauthlib entirely to avoid
# PKCE (code_verifier) issues on stateless Cloud Run instances.
# We have a client_secret so PKCE is unnecessary.

import secrets
import urllib.parse
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse

from app.config import (
    YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI,
    STORIES_YOUTUBE_CLIENT_ID, STORIES_YOUTUBE_CLIENT_SECRET, STORIES_YOUTUBE_REDIRECT_URI,
)
from app.services import firestore_service

router = APIRouter()

_AUTH_URI  = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_SCOPES    = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


@router.get("/auth/youtube")
def youtube_auth():
    """Redirect the browser to Google's OAuth consent screen (no PKCE)."""
    params = {
        "client_id":     YOUTUBE_CLIENT_ID,
        "redirect_uri":  YOUTUBE_REDIRECT_URI,
        "response_type": "code",
        "scope":         " ".join(_SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         secrets.token_urlsafe(32),
    }
    url = _AUTH_URI + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@router.get("/auth/youtube/callback")
def youtube_callback(code: str, state: str = "", error: str = ""):
    """Exchange the authorisation code for tokens and persist them."""
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    resp = httpx.post(
        _TOKEN_URI,
        data={
            "code":          code,
            "client_id":     YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "redirect_uri":  YOUTUBE_REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    token = resp.json()
    if "error" in token:
        raise HTTPException(
            status_code=500,
            detail=f"Token exchange failed: {token.get('error_description', token['error'])}",
        )

    existing = firestore_service.get_youtube_tokens(channel_id="news") or {}
    refresh_token = token.get("refresh_token") or existing.get("refresh_token")
    expires_in = int(token.get("expires_in", 3600))
    token_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    firestore_service.save_youtube_tokens({
        "access_token":  token["access_token"],
        "refresh_token": refresh_token,
        "token_expiry":  token_expiry,
        "client_id":     YOUTUBE_CLIENT_ID,
        "client_secret": YOUTUBE_CLIENT_SECRET,
    })
    firestore_service.clear_auth_failure("news")

    return HTMLResponse("""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px">
        <h1>&#x2705; YouTube Auth Complete</h1>
        <p>Your YouTube account is now connected. You can close this tab.</p>
        </body></html>
    """)


# ─── Stories channel (Tell Me Why) OAuth flow ─────────────────────────────────

@router.get("/auth/youtube/stories")
def youtube_stories_auth():
    """Redirect the browser to Google OAuth for the Tell Me Why channel."""
    params = {
        "client_id":     STORIES_YOUTUBE_CLIENT_ID,
        "redirect_uri":  STORIES_YOUTUBE_REDIRECT_URI,
        "response_type": "code",
        "scope":         " ".join(_SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         secrets.token_urlsafe(32),
    }
    url = _AUTH_URI + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@router.get("/auth/youtube/stories/callback")
def youtube_stories_callback(code: str, state: str = "", error: str = ""):
    """Exchange the authorisation code for Tell Me Why tokens and persist them."""
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    resp = httpx.post(
        _TOKEN_URI,
        data={
            "code":          code,
            "client_id":     STORIES_YOUTUBE_CLIENT_ID,
            "client_secret": STORIES_YOUTUBE_CLIENT_SECRET,
            "redirect_uri":  STORIES_YOUTUBE_REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    token = resp.json()
    if "error" in token:
        raise HTTPException(
            status_code=500,
            detail=f"Token exchange failed: {token.get('error_description', token['error'])}",
        )

    existing = firestore_service.get_youtube_tokens(channel_id="stories") or {}
    refresh_token = token.get("refresh_token") or existing.get("refresh_token")
    expires_in = int(token.get("expires_in", 3600))
    token_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    firestore_service.save_youtube_tokens({
        "access_token":  token["access_token"],
        "refresh_token": refresh_token,
        "token_expiry":  token_expiry,
        "client_id":     STORIES_YOUTUBE_CLIENT_ID,
        "client_secret": STORIES_YOUTUBE_CLIENT_SECRET,
    }, channel_id="stories")
    firestore_service.clear_auth_failure("stories")

    return HTMLResponse("""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px">
        <h1>&#x2705; Tell Me Why YouTube Auth Complete</h1>
        <p>The Tell Me Why channel is now connected. You can close this tab.</p>
        </body></html>
    """)
