import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import lead_researcher
from app.services import firestore_service, youtube_service

lead_researcher.update_domain_schedule()

jobs = firestore_service.list_recent_jobs(limit=200)
updated = 0
for job in jobs:
    if job.get("status") != "completed":
        continue
    video_id = youtube_service.extract_video_id(job.get("youtube_url", ""))
    if not video_id:
        continue
    analytics = youtube_service.fetch_video_analytics(video_id)
    if analytics:
        firestore_service.update_job_analytics(job["job_id"], analytics)
        updated += 1

print(f"Updated analytics for {updated} jobs")
