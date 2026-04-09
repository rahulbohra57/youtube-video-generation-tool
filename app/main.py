# app/main.py

import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from app.routes.generate import router as generate_router
from app.routes.research import router as research_router
from app.routes.webhook import router as webhook_router
from app.routes.auth import router as auth_router
from app.routes.admin import router as admin_router
from app.routes.stories import router as stories_router
from app.routes.stories_webhook import router as stories_webhook_router
from app.utils.helpers import ensure_dir, cleanup_files_older_than
from app.config import TEMP_DIR, OUTPUT_DIR, TMP_RETENTION_DAYS, BUCKET_NAME

STATIC_DIR = "app/static"
MEDIA_DIR = OUTPUT_DIR

ensure_dir(STATIC_DIR)
ensure_dir(TEMP_DIR)
ensure_dir(MEDIA_DIR)
cleanup_files_older_than(TEMP_DIR, TMP_RETENTION_DAYS)

app = FastAPI()

app.include_router(generate_router)
app.include_router(research_router)
app.include_router(webhook_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(stories_router)
app.include_router(stories_webhook_router)

# Serve frontend static assets
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/media/{filename}")
def serve_media(filename: str):
    """Serve a generated video.

    Tries local Output/ first (fast, works during the generating session).
    Falls back to GCS public URL redirect if the local file no longer exists
    (e.g. after a Cloud Run instance restart).
    """
    local_path = os.path.join(MEDIA_DIR, filename)
    if os.path.isfile(local_path):
        return FileResponse(local_path, media_type="video/mp4")
    # Redirect to GCS — the video was uploaded there after generation
    gcs_url = f"https://storage.googleapis.com/{BUCKET_NAME}/videos/{filename}"
    return RedirectResponse(url=gcs_url, status_code=302)


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
