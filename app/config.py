# app/config.py

import os

PROJECT_ID = os.getenv("PROJECT_ID", "youtube-video-generator-492211")
LOCATION = os.getenv("LOCATION", "us-central1")
BUCKET_NAME = os.getenv("BUCKET_NAME", "yt-gen-app-bucket")

TEMP_DIR = "tmp/"
OUTPUT_DIR = "Output/"
TMP_RETENTION_DAYS = int(os.getenv("TMP_RETENTION_DAYS", "7"))
ADMIN_DASHBOARD_SECRET = os.getenv("ADMIN_DASHBOARD_SECRET", "")
CREATE_TOPIC_IDEMPOTENCY_TTL_SECONDS = int(os.getenv("CREATE_TOPIC_IDEMPOTENCY_TTL_SECONDS", "1200"))

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

# Telegram — Stories channel (Short Tales)
STORIES_BOT_TOKEN = os.getenv("STORIES_BOT_TOKEN", "")
STORIES_CHAT_ID = os.getenv("STORIES_CHAT_ID", "")

# YouTube OAuth — News channel
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REDIRECT_URI = os.getenv("YOUTUBE_REDIRECT_URI", "")

# YouTube OAuth — Stories channel (Short Tales)
STORIES_YOUTUBE_CLIENT_ID = os.getenv("STORIES_YOUTUBE_CLIENT_ID", "")
STORIES_YOUTUBE_CLIENT_SECRET = os.getenv("STORIES_YOUTUBE_CLIENT_SECRET", "")
STORIES_YOUTUBE_REDIRECT_URI = os.getenv("STORIES_YOUTUBE_REDIRECT_URI", "")


def get_chat_id(channel_id: str) -> str:
    """Return the Telegram chat ID for the given channel."""
    return STORIES_CHAT_ID if channel_id == "stories" else TELEGRAM_CHAT_ID

# Cloud Scheduler auth
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")

# Cloud Tasks
CLOUD_RUN_URL = os.getenv("CLOUD_RUN_URL", "https://autoframe-353645494126.us-central1.run.app")
TASKS_QUEUE = os.getenv("TASKS_QUEUE", "autoframe-generate")
