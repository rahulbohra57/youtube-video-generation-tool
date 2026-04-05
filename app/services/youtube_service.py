# app/services/youtube_service.py

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from urllib.parse import urlparse

from app.services import firestore_service  # noqa: E402 (used by playlist helpers)
from app.config import YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET

_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
_TOKEN_URI = "https://oauth2.googleapis.com/token"

# YouTube category IDs: https://developers.google.com/youtube/v3/docs/videoCategories
_GENRE_TO_CATEGORY: dict[str, str] = {
    "technology": "28",           # Science & Technology
    "artificial intelligence": "28",
    "science": "28",
    "current affairs": "25",      # News & Politics
    "trending": "22",             # People & Blogs
    "direct": "22",
    "news": "25",
}
_DEFAULT_CATEGORY = "28"  # Science & Technology — better default than News & Politics


def genre_to_category_id(genre: str) -> str:
    return _GENRE_TO_CATEGORY.get((genre or "").strip().lower(), _DEFAULT_CATEGORY)


def normalize_title(title: str, limit: int = 100) -> str:
    """Trim title safely to `limit` chars while avoiding mid-word cuts."""
    clean = " ".join((title or "").strip().split())
    if len(clean) <= limit:
        return clean or "Untitled Video"
    truncated = clean[:limit].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0].rstrip()
    return (truncated or clean[:limit]).strip()


def get_credentials() -> Credentials:
    tokens = firestore_service.get_youtube_tokens()
    if not tokens:
        raise RuntimeError("YouTube OAuth tokens not found. Run /auth/youtube first.")

    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri=_TOKEN_URI,
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        scopes=_SCOPES,
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        firestore_service.save_youtube_tokens({
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_expiry": creds.expiry.isoformat() if creds.expiry else None,
            "client_id": YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
        })

    return creds


def upload_video(video_path: str, title: str, description: str, genre: str = "") -> str:
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    # Ensure #Shorts is in the description so YouTube surfaces it in the Shorts feed
    desc = description or ""
    if "#shorts" not in desc.lower():
        desc = desc.rstrip() + "\n#Shorts"

    body = {
        "snippet": {
            "title": normalize_title(title, limit=100),
            "description": desc,
            "categoryId": genre_to_category_id(genre),
            "tags": ["Shorts", "shorts"],
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    try:
        response = request.execute()
    except HttpError as e:
        content_str = str(e.content).lower()
        if e.resp.status in (403, 429) and any(
            kw in content_str for kw in ("quotaexceeded", "dailylimitexceeded", "userrequestedtoofast", "forbidden")
        ):
            raise RuntimeError("youtube_quota_exceeded") from e
        raise

    video_id = response["id"]
    return f"https://www.youtube.com/shorts/{video_id}"



def extract_video_id(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    path = (parsed.path or "").strip("/")
    if not path:
        return ""
    if path.startswith("shorts/"):
        return path.split("/", 1)[1].strip()
    if parsed.netloc.endswith("youtu.be"):
        return path.split("/")[0].strip()
    return ""


_GENRE_PLAYLIST_NAMES: dict[str, str] = {
    "technology": "Technology",
    "artificial intelligence": "Artificial Intelligence",
    "current affairs": "Current Affairs",
    "trending": "Trending",
    "science": "Science",
    "direct": "General",
}


def get_or_create_playlist(genre: str) -> str | None:
    """Return playlist_id for the genre, creating it on YouTube if needed. Cached in Firestore."""
    playlist_name = _GENRE_PLAYLIST_NAMES.get((genre or "").strip().lower(), "General")

    cached = firestore_service.get_playlist_id(playlist_name)
    if cached:
        return cached

    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    # Check if playlist already exists on channel
    resp = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
    for pl in resp.get("items", []):
        if pl["snippet"]["title"] == playlist_name:
            pl_id = pl["id"]
            firestore_service.save_playlist_id(playlist_name, pl_id)
            return pl_id

    # Create it
    result = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": playlist_name,
                "description": f"Auto-generated {playlist_name} Shorts",
            },
            "status": {"privacyStatus": "public"},
        },
    ).execute()
    pl_id = result["id"]
    firestore_service.save_playlist_id(playlist_name, pl_id)
    return pl_id


def add_video_to_playlist(video_id: str, playlist_id: str) -> bool:
    if not video_id or not playlist_id:
        return False
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()
    return True


def set_video_privacy(video_id: str, privacy_status: str = "private") -> bool:
    if not video_id:
        return False
    if privacy_status not in ("private", "public", "unlisted"):
        privacy_status = "private"
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    youtube.videos().update(
        part="status",
        body={"id": video_id, "status": {"privacyStatus": privacy_status}},
    ).execute()
    return True


def delete_video(video_id: str) -> bool:
    if not video_id:
        return False
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    youtube.videos().delete(id=video_id).execute()
    return True


def fetch_video_analytics(video_id: str) -> dict:
    if not video_id:
        return {}
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.videos().list(part="statistics", id=video_id, maxResults=1).execute()
    items = resp.get("items", [])
    if not items:
        return {}
    stats = items[0].get("statistics", {})
    return {
        "view_count": int(stats.get("viewCount", 0)),
        "like_count": int(stats.get("likeCount", 0)),
        "comment_count": int(stats.get("commentCount", 0)),
    }


def get_channel_stats() -> dict:
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    response = youtube.channels().list(part="statistics,contentDetails", mine=True).execute()
    items = response.get("items", [])
    if not items:
        return {
            "subscriber_count": 0,
            "view_count": 0,
            "video_count": 0,
        }
    item = items[0]
    stats = item.get("statistics", {})
    view_count = int(stats.get("viewCount", 0))

    # Some new channels may report channel-level viewCount as 0 for a while.
    # Fallback: sum views from uploaded videos to show practical total.
    if view_count == 0:
        uploads_playlist = (
            (item.get("contentDetails") or {})
            .get("relatedPlaylists", {})
            .get("uploads", "")
        )
        if uploads_playlist:
            view_count = _sum_upload_views(youtube, uploads_playlist)

    return {
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "view_count": view_count,
        "video_count": int(stats.get("videoCount", 0)),
    }


def _sum_upload_views(youtube, uploads_playlist_id: str, max_items: int = 100) -> int:
    total = 0
    video_ids: list[str] = []
    next_page = None

    while len(video_ids) < max_items:
        req = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=min(50, max_items - len(video_ids)),
            pageToken=next_page,
        )
        resp = req.execute()
        for it in resp.get("items", []):
            vid = (it.get("contentDetails") or {}).get("videoId")
            if vid:
                video_ids.append(vid)
        next_page = resp.get("nextPageToken")
        if not next_page:
            break

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        if not batch:
            continue
        vresp = youtube.videos().list(part="statistics", id=",".join(batch), maxResults=50).execute()
        for v in vresp.get("items", []):
            vstats = v.get("statistics", {})
            total += int(vstats.get("viewCount", 0))
    return total
