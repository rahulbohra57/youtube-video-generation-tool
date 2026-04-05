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

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# YouTube OAuth
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REDIRECT_URI = os.getenv("YOUTUBE_REDIRECT_URI", "")

# Cloud Scheduler auth
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")

# Cloud Tasks
CLOUD_RUN_URL = os.getenv("CLOUD_RUN_URL", "https://autoframe-353645494126.us-central1.run.app")
TASKS_QUEUE = os.getenv("TASKS_QUEUE", "autoframe-generate")
