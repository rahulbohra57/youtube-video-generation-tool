# app/services/storage_service.py

import logging
try:
    from google.cloud import storage
except Exception:
    storage = None
from app.config import BUCKET_NAME

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        if storage is None:
            raise RuntimeError(
                "google-cloud-storage is not installed or could not be imported."
            )
        _client = storage.Client()
    return _client


def upload_video(local_path: str, blob_name: str) -> str:
    """Upload a video file to GCS and return its public URL.

    Requires either:
      - Bucket-level IAM: allUsers → Storage Object Viewer, OR
      - Per-object ACLs enabled (Uniform bucket-level access must be OFF).

    Falls back gracefully — callers should catch exceptions and serve locally
    if GCS is unavailable.
    """
    bucket = _get_client().bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path, content_type="video/mp4")
    try:
        blob.make_public()
    except Exception as e:
        # Uniform bucket-level access may be enabled — log and return the URL anyway.
        # The video can still be served if the bucket IAM grants allUsers read.
        logger.warning(f"blob.make_public() failed (check bucket IAM): {e}")
    return blob.public_url


def upload_file(local_path: str, dest_path: str) -> str:
    """Upload any file to GCS and return its public URL."""
    bucket = _get_client().bucket(BUCKET_NAME)
    blob = bucket.blob(dest_path)
    blob.upload_from_filename(local_path)
    try:
        blob.make_public()
    except Exception as e:
        logger.warning(f"blob.make_public() failed: {e}")
    return blob.public_url


def get_public_url(blob_name: str) -> str:
    return f"https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}"
