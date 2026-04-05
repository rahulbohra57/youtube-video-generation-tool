# app/workers/video_worker.py

from app.services.video_service import create_video

def process_video_job(clips, output_path):
    """
    Worker function for async execution
    """
    create_video(clips, output_path)
    return output_path