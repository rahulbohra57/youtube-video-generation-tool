# app/config.py

import os

BUCKET_NAME = os.getenv("BUCKET_NAME", "yt-gen-app-bucket")

TEMP_DIR = "tmp/"
OUTPUT_DIR = "Output/"
TMP_RETENTION_DAYS = int(os.getenv("TMP_RETENTION_DAYS", "7"))
ADMIN_DASHBOARD_SECRET = os.getenv("ADMIN_DASHBOARD_SECRET", "")
CREATE_TOPIC_IDEMPOTENCY_TTL_SECONDS = int(os.getenv("CREATE_TOPIC_IDEMPOTENCY_TTL_SECONDS", "1200"))

# Stories animation settings (V1 2.5D motion)
STORIES_ANIMATION_ENABLED = os.getenv("STORIES_ANIMATION_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
STORIES_ANIMATION_PROFILE = os.getenv("STORIES_ANIMATION_PROFILE", "standard").strip().lower()
if STORIES_ANIMATION_PROFILE not in ("lite", "standard"):
    STORIES_ANIMATION_PROFILE = "standard"
STORIES_MAX_SCENES_ANIMATED = int(os.getenv("STORIES_MAX_SCENES_ANIMATED", "3"))
STORIES_BROLL_ENABLED = os.getenv("STORIES_BROLL_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
STORIES_BROLL_MIN_VIRALITY = float(os.getenv("STORIES_BROLL_MIN_VIRALITY", "4.5"))

# Video settings
VIDEO_FPS = 24
SCENE_DURATION = 5  # seconds per scene

# GNews
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY", "")

# Google Custom Search (used for CREATE/FORCE_CREATE topic enrichment)
# Get API key: console.cloud.google.com/apis/library/customsearch.googleapis.com
# Get Search Engine ID: programmablesearchengine.google.com (enable "Search the entire web")
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_SEARCH_ENGINE_ID = os.getenv("GOOGLE_SEARCH_ENGINE_ID", "")

# Telegram — News channel (Kurrent Affairs)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Telegram — Stories channel (Tell Me Why)
STORIES_BOT_TOKEN = os.getenv("STORIES_BOT_TOKEN", "")
STORIES_CHAT_ID = os.getenv("STORIES_CHAT_ID", "")

# YouTube OAuth — News channel
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REDIRECT_URI = os.getenv("YOUTUBE_REDIRECT_URI", "")

# YouTube OAuth — Stories channel (Tell Me Why)
STORIES_YOUTUBE_CLIENT_ID = os.getenv("STORIES_YOUTUBE_CLIENT_ID", "")
STORIES_YOUTUBE_CLIENT_SECRET = os.getenv("STORIES_YOUTUBE_CLIENT_SECRET", "")
STORIES_YOUTUBE_REDIRECT_URI = os.getenv("STORIES_YOUTUBE_REDIRECT_URI", "")


def get_chat_id(channel_id: str) -> str:
    """Return the Telegram chat ID for the given channel. Read env vars at call time."""
    if channel_id == "stories":
        return os.getenv("STORIES_CHAT_ID", "") or STORIES_CHAT_ID
    return os.getenv("TELEGRAM_CHAT_ID", "") or TELEGRAM_CHAT_ID

# Cloud Scheduler auth
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")

# Base URL of the deployed app (used for OAuth re-auth links in Telegram notifications).
# Auth flows run locally via FastAPI — set APP_BASE_URL if hosting elsewhere.
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8080")

# GitHub Actions dispatch
GITHUB_DISPATCH_TOKEN = os.getenv("GITHUB_DISPATCH_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
