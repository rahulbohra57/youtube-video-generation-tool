# 4-Agent Autonomous News-to-YouTube Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend AUTOFRAME with a fully automated pipeline that fetches trending tech news every 4 hours, delivers a WhatsApp digest, and — when the user selects a headline — generates a YouTube Shorts video and posts it automatically.

**Architecture:** A single FastAPI Cloud Run service gains four agent modules (`app/agents/`) plus four new service wrappers (`app/services/`), three new route files, and a GCP Cloud Scheduler job. Agents call each other directly (in-process); the WhatsApp webhook returns immediately and processes the pipeline in FastAPI `BackgroundTasks`. State persists in Firestore.

**Tech Stack:** Python 3.10, FastAPI, GNews API, Twilio WhatsApp, GCP Firestore, GCP Cloud Scheduler, YouTube Data API v3, google-auth-oauthlib, httpx, existing Gemini 2.5 Flash + Imagen 3 + Cloud TTS + MoviePy stack.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `requirements.txt` | Modify | Add 5 new deps |
| `app/config.py` | Modify | Add env var constants |
| `app/main.py` | Modify | Register 3 new routers |
| `app/services/firestore_service.py` | Create | Read/write Firestore collections |
| `app/services/gnews_service.py` | Create | GNews API HTTP client |
| `app/services/twilio_service.py` | Create | Twilio WhatsApp send wrapper |
| `app/services/youtube_service.py` | Create | YouTube Data API v3 upload + OAuth token refresh |
| `app/services/llm_service.py` | Modify | Add `rate_and_select_news()` and `enhance_caption()` |
| `app/agents/__init__.py` | Create | Empty package marker |
| `app/agents/lead_researcher.py` | Create | GNews fetch + Gemini rating → Firestore → WhatsApp digest |
| `app/agents/whatsapp_agent.py` | Create | Twilio send/format + reply handler |
| `app/agents/generator_agent.py` | Create | Orchestrate existing video+caption services |
| `app/agents/social_media_agent.py` | Create | Caption enhancement + YouTube upload |
| `app/routes/research.py` | Create | `POST /research/run` (Cloud Scheduler target) |
| `app/routes/webhook.py` | Create | `POST /webhook/whatsapp` (Twilio webhook) |
| `app/routes/auth.py` | Create | `GET /auth/youtube` + `/auth/youtube/callback` |
| `tests/test_pipeline.py` | Create | Unit tests for all agents and services |

---

## Task 1: Add Dependencies and Config Constants

**Files:**
- Modify: `requirements.txt`
- Modify: `app/config.py`

- [ ] **Step 1: Add new dependencies to requirements.txt**

Open `requirements.txt` and append:
```
twilio
google-cloud-firestore
google-api-python-client
google-auth-oauthlib
httpx
```

Final `requirements.txt`:
```
fastapi
uvicorn[standard]
vertexai
google-cloud-storage
google-cloud-texttospeech
moviepy
Pillow
requests
python-multipart
twilio
google-cloud-firestore
google-api-python-client
google-auth-oauthlib
httpx
```

- [ ] **Step 2: Add env var constants to app/config.py**

Open `app/config.py` and append after the existing constants:
```python
# GNews
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY", "")

# Twilio WhatsApp
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
OWNER_WHATSAPP_TO = os.getenv("OWNER_WHATSAPP_TO", "")

# YouTube OAuth
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REDIRECT_URI = os.getenv("YOUTUBE_REDIRECT_URI", "")

# Cloud Scheduler auth
SCHEDULER_SECRET = os.getenv("SCHEDULER_SECRET", "")
```

- [ ] **Step 3: Install dependencies**

```bash
pip install twilio google-cloud-firestore google-api-python-client google-auth-oauthlib httpx
```

Expected: all packages install without error.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt app/config.py
git commit -m "feat: add pipeline deps and env var constants"
```

---

## Task 2: Firestore Service

**Files:**
- Create: `app/services/firestore_service.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:
```python
# tests/test_pipeline.py

from unittest.mock import MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Task 2: firestore_service
# ---------------------------------------------------------------------------

def _mock_doc(data: dict):
    """Returns a Firestore DocumentSnapshot mock with exists=True."""
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = data
    return doc


def _missing_doc():
    doc = MagicMock()
    doc.exists = False
    return doc


@patch("app.services.firestore_service.firestore")
def test_save_news_batch_writes_correct_structure(mock_fs):
    mock_db = MagicMock()
    mock_fs.Client.return_value = mock_db

    from app.services import firestore_service
    firestore_service._db = mock_db  # inject mock

    firestore_service.save_news_batch("batch_001", "technology", {"TECH01": {"headline": "x"}})

    mock_db.collection.assert_called_with("news_batches")
    mock_db.collection().document.assert_called_with("batch_001")
    set_call = mock_db.collection().document().set.call_args[0][0]
    assert set_call["genre"] == "technology"
    assert set_call["status"] == "awaiting_reply"
    assert set_call["items"] == {"TECH01": {"headline": "x"}}


@patch("app.services.firestore_service.firestore")
def test_get_news_batch_returns_none_when_missing(mock_fs):
    mock_db = MagicMock()
    mock_fs.Client.return_value = mock_db
    mock_db.collection().document().get.return_value = _missing_doc()

    from app.services import firestore_service
    firestore_service._db = mock_db

    result = firestore_service.get_news_batch("nonexistent")
    assert result is None


@patch("app.services.firestore_service.firestore")
def test_update_batch_status(mock_fs):
    mock_db = MagicMock()
    mock_fs.Client.return_value = mock_db

    from app.services import firestore_service
    firestore_service._db = mock_db

    firestore_service.update_batch_status("batch_001", "completed")

    mock_db.collection().document().update.assert_called_with({"status": "completed"})


@patch("app.services.firestore_service.firestore")
def test_get_pipeline_state_returns_empty_when_missing(mock_fs):
    mock_db = MagicMock()
    mock_fs.Client.return_value = mock_db
    mock_db.collection().document().get.return_value = _missing_doc()

    from app.services import firestore_service
    firestore_service._db = mock_db

    result = firestore_service.get_pipeline_state()
    assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/chetan/Desktop/Data Science/youtube-video-generation-tool"
pytest tests/test_pipeline.py -v -k "firestore"
```

Expected: `ModuleNotFoundError` or `ImportError` — `firestore_service` does not exist yet.

- [ ] **Step 3: Create app/services/firestore_service.py**

```python
# app/services/firestore_service.py

from google.cloud import firestore
from datetime import datetime, timezone

_db = None


def _get_db():
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def save_news_batch(batch_id: str, genre: str, items: dict):
    _get_db().collection("news_batches").document(batch_id).set({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "genre": genre,
        "status": "awaiting_reply",
        "items": items,
    })


def get_news_batch(batch_id: str) -> dict | None:
    doc = _get_db().collection("news_batches").document(batch_id).get()
    return doc.to_dict() if doc.exists else None


def update_batch_status(batch_id: str, status: str):
    _get_db().collection("news_batches").document(batch_id).update({"status": status})


def set_pipeline_state(batch_id: str, state: str):
    _get_db().collection("pipeline_state").document("current").set({
        "active_batch_id": batch_id,
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
    })


def get_pipeline_state() -> dict:
    doc = _get_db().collection("pipeline_state").document("current").get()
    return doc.to_dict() if doc.exists else {}


def save_youtube_tokens(tokens: dict):
    _get_db().collection("oauth_tokens").document("youtube").set(tokens)


def get_youtube_tokens() -> dict | None:
    doc = _get_db().collection("oauth_tokens").document("youtube").get()
    return doc.to_dict() if doc.exists else None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pipeline.py -v -k "firestore"
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/firestore_service.py tests/test_pipeline.py
git commit -m "feat: add firestore_service with pipeline state management"
```

---

## Task 3: GNews Service

**Files:**
- Create: `app/services/gnews_service.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:
```python
# ---------------------------------------------------------------------------
# Task 3: gnews_service
# ---------------------------------------------------------------------------

@patch("app.services.gnews_service.httpx.get")
def test_fetch_top_headlines_returns_list(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "articles": [
                {"title": "AI breakthrough", "url": "https://example.com/1", "description": "Details here"},
                {"title": "Quantum chip", "url": "https://example.com/2", "description": ""},
            ]
        }
    )

    from app.services import gnews_service
    results = gnews_service.fetch_top_headlines(category="technology", max_results=2)

    assert len(results) == 2
    assert results[0]["headline"] == "AI breakthrough"
    assert results[0]["url"] == "https://example.com/1"
    assert results[1]["description"] == ""


@patch("app.services.gnews_service.httpx.get")
def test_fetch_top_headlines_raises_on_http_error(mock_get):
    mock_get.return_value = MagicMock(
        raise_for_status=MagicMock(side_effect=Exception("HTTP 429"))
    )

    from app.services import gnews_service
    with pytest.raises(Exception, match="HTTP 429"):
        gnews_service.fetch_top_headlines()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_pipeline.py -v -k "gnews"
```

Expected: `ImportError` — `gnews_service` does not exist yet.

- [ ] **Step 3: Create app/services/gnews_service.py**

```python
# app/services/gnews_service.py

import httpx
from app.config import GNEWS_API_KEY

_GNEWS_URL = "https://gnews.io/api/v4/top-headlines"


def fetch_top_headlines(category: str = "technology", max_results: int = 10) -> list[dict]:
    params = {
        "category": category,
        "lang": "en",
        "max": max_results,
        "token": GNEWS_API_KEY,
    }
    response = httpx.get(_GNEWS_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    return [
        {
            "headline": a["title"],
            "url": a["url"],
            "description": a.get("description", ""),
        }
        for a in data.get("articles", [])
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pipeline.py -v -k "gnews"
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/gnews_service.py tests/test_pipeline.py
git commit -m "feat: add gnews_service for tech headline fetching"
```

---

## Task 4: LLM Service Additions

**Files:**
- Modify: `app/services/llm_service.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:
```python
# ---------------------------------------------------------------------------
# Task 4: llm_service additions
# ---------------------------------------------------------------------------

@patch("app.services.llm_service.model")
def test_rate_and_select_news_returns_five_items(mock_model):
    mock_model.generate_content.return_value = MagicMock(text="""
[
  {"code": "TECH01", "headline": "AI does stuff", "context": "Context here.", "rating": 4.8},
  {"code": "TECH02", "headline": "Quantum leap", "context": "Another context.", "rating": 4.5},
  {"code": "TECH03", "headline": "Robot uprising", "context": "Robots everywhere.", "rating": 4.2},
  {"code": "TECH04", "headline": "Chip shortage ends", "context": "Supply fixed.", "rating": 4.0},
  {"code": "TECH05", "headline": "Battery lasts forever", "context": "New tech.", "rating": 3.9}
]""")

    from app.services.llm_service import rate_and_select_news
    articles = [{"headline": f"Article {i}", "url": "", "description": ""} for i in range(10)]
    results = rate_and_select_news(articles)

    assert len(results) == 5
    assert results[0]["code"] == "TECH01"
    assert results[0]["rating"] == 4.8


@patch("app.services.llm_service.model")
def test_enhance_caption_returns_non_empty_string(mock_model):
    mock_model.generate_content.return_value = MagicMock(
        text="Wow, this will blow your mind!\nHere is the body.\nLike and subscribe!\n#tech #ai"
    )

    from app.services.llm_service import enhance_caption
    result = enhance_caption("Original caption\n#tech")

    assert isinstance(result, str)
    assert len(result) > 0
    assert "Like and subscribe" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_pipeline.py -v -k "rate_and_select or enhance_caption"
```

Expected: `ImportError` — `rate_and_select_news` and `enhance_caption` not defined yet.

- [ ] **Step 3: Add rate_and_select_news and enhance_caption to app/services/llm_service.py**

Append to the end of `app/services/llm_service.py`:
```python

def rate_and_select_news(articles: list[dict]) -> list[dict]:
    headlines_text = "\n".join(
        f"{i + 1}. {a['headline']}" for i, a in enumerate(articles)
    )
    prompt = f"""You are a news editor. Rate each headline 1–5 for virality and public interest.
Select the top 5. Return ONLY a valid JSON array, no markdown, no explanation:
[{{"code": "TECH01", "headline": "...", "context": "2-sentence summary.", "rating": 4.5}}, ...]

Headlines:
{headlines_text}"""
    response = model.generate_content(prompt)
    from app.utils.helpers import extract_json
    return extract_json(response.text)


def enhance_caption(caption: str) -> str:
    prompt = f"""Improve this YouTube Shorts caption.
Add a strong hook as the very first line.
Add an engaging closing line asking viewers to like and subscribe.
Keep all existing hashtags. Return plain text only, no markdown.

Caption:
{caption}"""
    response = model.generate_content(prompt)
    return response.text.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pipeline.py -v -k "rate_and_select or enhance_caption"
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/llm_service.py tests/test_pipeline.py
git commit -m "feat: add rate_and_select_news and enhance_caption to llm_service"
```

---

## Task 5: Twilio Service

**Files:**
- Create: `app/services/twilio_service.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:
```python
# ---------------------------------------------------------------------------
# Task 5: twilio_service
# ---------------------------------------------------------------------------

@patch("app.services.twilio_service.Client")
def test_send_whatsapp_calls_twilio_with_correct_params(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    from app.services import twilio_service
    twilio_service.send_whatsapp("whatsapp:+91999", "Hello test")

    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["to"] == "whatsapp:+91999"
    assert call_kwargs["body"] == "Hello test"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_pipeline.py -v -k "twilio"
```

Expected: `ImportError` — `twilio_service` does not exist yet.

- [ ] **Step 3: Create app/services/twilio_service.py**

```python
# app/services/twilio_service.py

from twilio.rest import Client
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM


def send_whatsapp(to: str, body: str):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=to, body=body)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_pipeline.py -v -k "twilio"
```

Expected: 1 test PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/twilio_service.py tests/test_pipeline.py
git commit -m "feat: add twilio_service WhatsApp wrapper"
```

---

## Task 6: Lead Researcher Agent

**Files:**
- Create: `app/agents/__init__.py`
- Create: `app/agents/lead_researcher.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:
```python
# ---------------------------------------------------------------------------
# Task 6: lead_researcher agent
# ---------------------------------------------------------------------------

@patch("app.agents.lead_researcher.whatsapp_agent")
@patch("app.agents.lead_researcher.firestore_service")
@patch("app.agents.lead_researcher.rate_and_select_news")
@patch("app.agents.lead_researcher.gnews_service")
def test_lead_researcher_run_creates_batch_and_sends_digest(
    mock_gnews, mock_rate, mock_fs, mock_wa
):
    mock_gnews.fetch_top_headlines.return_value = [
        {"headline": f"News {i}", "url": "", "description": ""} for i in range(10)
    ]
    mock_rate.return_value = [
        {"code": f"TECH0{i}", "headline": f"News {i}", "context": "ctx", "rating": 4.0}
        for i in range(1, 6)
    ]

    from app.agents import lead_researcher
    batch_id = lead_researcher.run()

    assert batch_id.startswith("batch_")
    mock_fs.save_news_batch.assert_called_once()
    mock_fs.set_pipeline_state.assert_called_once()
    mock_wa.send_digest.assert_called_once_with(batch_id)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_pipeline.py -v -k "lead_researcher"
```

Expected: `ImportError` — `lead_researcher` does not exist yet.

- [ ] **Step 3: Create app/agents/__init__.py**

Create an empty file:
```python
# app/agents/__init__.py
```

- [ ] **Step 4: Create app/agents/lead_researcher.py**

```python
# app/agents/lead_researcher.py

from datetime import datetime, timezone
from app.services import gnews_service, firestore_service
from app.services.llm_service import rate_and_select_news
from app.agents import whatsapp_agent


def run() -> str:
    articles = gnews_service.fetch_top_headlines(category="technology", max_results=10)
    rated_items = rate_and_select_news(articles)

    batch_id = f"batch_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    items = {item["code"]: item for item in rated_items}

    firestore_service.save_news_batch(batch_id, "technology", items)
    firestore_service.set_pipeline_state(batch_id, "awaiting_reply")

    whatsapp_agent.send_digest(batch_id)
    return batch_id
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/test_pipeline.py -v -k "lead_researcher"
```

Expected: 1 test PASS.

- [ ] **Step 6: Commit**

```bash
git add app/agents/__init__.py app/agents/lead_researcher.py tests/test_pipeline.py
git commit -m "feat: add lead_researcher agent"
```

---

## Task 7: WhatsApp Agent

**Files:**
- Create: `app/agents/whatsapp_agent.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:
```python
# ---------------------------------------------------------------------------
# Task 7: whatsapp_agent
# ---------------------------------------------------------------------------

@patch("app.agents.whatsapp_agent.twilio_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_send_digest_formats_and_sends_message(mock_fs, mock_twilio):
    mock_fs.get_news_batch.return_value = {
        "genre": "technology",
        "items": {
            "TECH01": {"headline": "AI news", "context": "Big AI development.", "rating": 4.5},
            "TECH02": {"headline": "Chip news", "context": "New chip released.", "rating": 4.0},
        }
    }

    from app.agents import whatsapp_agent
    import app.config as config
    config.OWNER_WHATSAPP_TO = "whatsapp:+91999"

    whatsapp_agent.send_digest("batch_001")

    mock_fs.get_news_batch.assert_called_with("batch_001")
    mock_twilio.send_whatsapp.assert_called_once()
    sent_body = mock_twilio.send_whatsapp.call_args[0][1]
    assert "TECH01" in sent_body
    assert "AI news" in sent_body
    assert "TECH02" in sent_body


@patch("app.agents.whatsapp_agent.twilio_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_none_skips_pipeline(mock_fs, mock_twilio):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001", "state": "awaiting_reply"
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("whatsapp:+91999", "none")

    mock_fs.update_batch_status.assert_called_with("batch_001", "skipped")
    mock_twilio.send_whatsapp.assert_called_once()
    assert "See you" in mock_twilio.send_whatsapp.call_args[0][1]


@patch("app.agents.generator_agent.run")
@patch("app.agents.whatsapp_agent.twilio_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_valid_code_triggers_generator(mock_fs, mock_twilio, mock_gen_run):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001", "state": "awaiting_reply"
    }
    mock_fs.get_news_batch.return_value = {
        "items": {"TECH01": {"headline": "Big AI news", "context": "ctx", "rating": 4.8}}
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("whatsapp:+91999", "tech01")

    mock_gen_run.assert_called_once_with("Big AI news", "TECH01")
    mock_fs.update_batch_status.assert_called_with("batch_001", "processing")


@patch("app.agents.whatsapp_agent.twilio_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_invalid_code_sends_error(mock_fs, mock_twilio):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001", "state": "awaiting_reply"
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("whatsapp:+91999", "TECH99")

    mock_twilio.send_whatsapp.assert_called_once()
    assert "Invalid" in mock_twilio.send_whatsapp.call_args[0][1]


@patch("app.agents.whatsapp_agent.twilio_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_blocks_concurrent_processing(mock_fs, mock_twilio):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001", "state": "processing"
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("whatsapp:+91999", "TECH01")

    mock_twilio.send_whatsapp.assert_called_once()
    assert "Processing" in mock_twilio.send_whatsapp.call_args[0][1]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_pipeline.py -v -k "whatsapp_agent"
```

Expected: `ImportError` — `whatsapp_agent` does not exist yet.

- [ ] **Step 3: Create app/agents/whatsapp_agent.py**

```python
# app/agents/whatsapp_agent.py

import re
from app.services import firestore_service, twilio_service
from app.config import OWNER_WHATSAPP_TO


def send_digest(batch_id: str):
    batch = firestore_service.get_news_batch(batch_id)
    sections = []
    for code, item in batch["items"].items():
        sections.append(
            f"*Unique Number:* {code}\n"
            f"*Genre:* Technology\n"
            f"*Headline:* {item['headline']}\n"
            f"*Context:* {item['context']}\n"
            f"*Rating:* {item['rating']}/5"
        )
    footer = "Reply with a code (e.g. TECH01) to generate a video, or reply *None* to skip."
    message = "\n---\n".join(sections) + "\n\n" + footer
    twilio_service.send_whatsapp(OWNER_WHATSAPP_TO, message)


def handle_reply(from_number: str, body: str):
    state = firestore_service.get_pipeline_state()
    batch_id = state.get("active_batch_id")

    if not batch_id:
        twilio_service.send_whatsapp(from_number, "No active digest. Please wait for the next one.")
        return

    text = body.strip().upper()

    if text == "NONE":
        firestore_service.update_batch_status(batch_id, "skipped")
        firestore_service.set_pipeline_state(batch_id, "skipped")
        twilio_service.send_whatsapp(from_number, "Got it! See you in the next digest.")
        return

    if re.match(r"^TECH0[1-5]$", text):
        if state.get("state") == "processing":
            twilio_service.send_whatsapp(from_number, "Processing in progress. Please wait.")
            return
        batch = firestore_service.get_news_batch(batch_id)
        item = batch["items"].get(text)
        if not item:
            twilio_service.send_whatsapp(from_number, f"Code {text} not found. Please try again.")
            return
        firestore_service.update_batch_status(batch_id, "processing")
        firestore_service.set_pipeline_state(batch_id, "processing")
        from app.agents import generator_agent
        generator_agent.run(item["headline"], text)
        return

    twilio_service.send_whatsapp(from_number, "Invalid code. Please reply with TECH01–TECH05 or None.")


def send_post_result(title: str, url: str):
    message = f"\u2705 Posted to YouTube!\n*Post Title:* {title}\n*Post Link:* {url}"
    twilio_service.send_whatsapp(OWNER_WHATSAPP_TO, message)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pipeline.py -v -k "whatsapp_agent"
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agents/whatsapp_agent.py tests/test_pipeline.py
git commit -m "feat: add whatsapp_agent with send_digest and handle_reply"
```

---

## Task 8: Generator Agent

**Files:**
- Create: `app/agents/generator_agent.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:
```python
# ---------------------------------------------------------------------------
# Task 8: generator_agent
# ---------------------------------------------------------------------------

@patch("app.agents.social_media_agent.post")
@patch("app.agents.generator_agent.generate_shorts_caption", return_value="Great caption #ai")
@patch("app.agents.generator_agent.create_video")
@patch("app.agents.generator_agent.generate_image", return_value="/tmp/scene_TECH01_0.png")
@patch("app.agents.generator_agent.generate_audio")
@patch("app.agents.generator_agent.classify_music_genre", return_value="News Bulletin")
@patch("app.agents.generator_agent.generate_script")
@patch("app.agents.generator_agent.extract_json")
def test_generator_agent_run_calls_social_media_agent(
    mock_extract, mock_script, mock_music, mock_audio,
    mock_image, mock_video, mock_caption, mock_social_post
):
    mock_script.return_value = "[]"
    mock_extract.return_value = [
        {"scene": 1, "narration": "Big AI news story.", "visual": "AI illustration"}
    ]

    from app.agents import generator_agent
    generator_agent.run("Big AI news story", "TECH01")

    mock_social_post.assert_called_once()
    call_kwargs = mock_social_post.call_args[1]
    assert call_kwargs["caption"] == "Great caption #ai"
    assert call_kwargs["title"] == "Big AI news story"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_pipeline.py -v -k "generator_agent"
```

Expected: `ImportError` — `generator_agent` does not exist yet.

- [ ] **Step 3: Create app/agents/generator_agent.py**

Note: `social_media_agent` is imported lazily inside `run()` to avoid a circular import chain (`social_media_agent` → `whatsapp_agent` → `generator_agent` → `social_media_agent`).

```python
# app/agents/generator_agent.py

import os
import time
from datetime import datetime, timezone

from app.config import TEMP_DIR
from app.services.llm_service import generate_script, generate_shorts_caption, classify_music_genre
from app.services.tts_service import generate_audio
from app.services.image_service import generate_image
from app.services.video_service import create_video
from app.utils.helpers import extract_json, ensure_dir


def run(headline: str, code: str):
    ensure_dir(TEMP_DIR)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    raw_script = generate_script(headline, language="en", aspect_ratio="9:16")
    try:
        scenes = extract_json(raw_script)
    except Exception:
        scenes = [{"scene": 1, "narration": headline, "visual": "news concept illustration"}]

    music_genre = classify_music_genre(headline)
    video_clips = []

    for i, scene in enumerate(scenes):
        narration = scene.get("narration")
        visual = scene.get("visual")
        if not narration or not visual:
            continue
        try:
            audio_path = os.path.join(TEMP_DIR, f"audio_{code}_{i}.mp3")
            generate_audio(narration, audio_path, language="en")
            image_path = generate_image(visual, i, aspect_ratio="9:16")
            video_clips.append((image_path, audio_path, narration))
            time.sleep(2)
        except Exception as e:
            print(f"Scene {i} failed: {e}")

    if not video_clips:
        raise ValueError("No video clips generated")

    output_path = os.path.join(TEMP_DIR, f"final_{code}_{timestamp}.mp4")
    create_video(video_clips, output_path, music_genre=music_genre, language="en")

    caption = generate_shorts_caption(headline, language="en")

    # Lazy import to avoid circular dependency with social_media_agent → whatsapp_agent
    from app.agents import social_media_agent
    social_media_agent.post(video_path=output_path, caption=caption, title=headline)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_pipeline.py -v -k "generator_agent"
```

Expected: 1 test PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agents/generator_agent.py tests/test_pipeline.py
git commit -m "feat: add generator_agent orchestrating existing video services"
```

---

## Task 9: YouTube Service

**Files:**
- Create: `app/services/youtube_service.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:
```python
# ---------------------------------------------------------------------------
# Task 9: youtube_service
# ---------------------------------------------------------------------------

@patch("app.services.youtube_service.firestore_service")
@patch("app.services.youtube_service.build")
@patch("app.services.youtube_service.Credentials")
def test_upload_video_returns_shorts_url(mock_creds_cls, mock_build, mock_fs):
    mock_fs.get_youtube_tokens.return_value = {
        "access_token": "tok", "refresh_token": "ref",
        "token_expiry": None, "client_id": "cid", "client_secret": "csec"
    }
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_cls.return_value = mock_creds

    mock_yt = MagicMock()
    mock_build.return_value = mock_yt
    mock_request = MagicMock()
    mock_yt.videos().insert.return_value = mock_request
    mock_request.execute.return_value = {"id": "abc123XYZ"}

    from app.services import youtube_service
    with patch("app.services.youtube_service.MediaFileUpload"):
        url = youtube_service.upload_video("/tmp/test.mp4", "Big AI News", "Caption here")

    assert url == "https://www.youtube.com/shorts/abc123XYZ"


@patch("app.services.youtube_service.firestore_service")
def test_get_credentials_raises_when_no_tokens(mock_fs):
    mock_fs.get_youtube_tokens.return_value = None

    from app.services import youtube_service
    with pytest.raises(RuntimeError, match="YouTube OAuth tokens not found"):
        youtube_service.get_credentials()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_pipeline.py -v -k "youtube_service"
```

Expected: `ImportError` — `youtube_service` does not exist yet.

- [ ] **Step 3: Create app/services/youtube_service.py**

```python
# app/services/youtube_service.py

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.services import firestore_service
from app.config import YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET

_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_TOKEN_URI = "https://oauth2.googleapis.com/token"


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


def upload_video(video_path: str, title: str, description: str) -> str:
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "25",   # News & Politics
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()

    video_id = response["id"]
    return f"https://www.youtube.com/shorts/{video_id}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pipeline.py -v -k "youtube_service"
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/youtube_service.py tests/test_pipeline.py
git commit -m "feat: add youtube_service for video upload via Data API v3"
```

---

## Task 10: Social Media Agent

**Files:**
- Create: `app/agents/social_media_agent.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:
```python
# ---------------------------------------------------------------------------
# Task 10: social_media_agent
# ---------------------------------------------------------------------------

@patch("app.agents.whatsapp_agent.send_post_result")
@patch("app.agents.social_media_agent.firestore_service")
@patch("app.agents.social_media_agent.youtube_service")
@patch("app.agents.social_media_agent.enhance_caption", return_value="Enhanced caption #ai")
def test_social_media_agent_post_uploads_and_notifies(
    mock_enhance, mock_yt, mock_fs, mock_send_post
):
    mock_yt.upload_video.return_value = "https://www.youtube.com/shorts/xyz"
    mock_fs.get_pipeline_state.return_value = {"active_batch_id": "batch_001"}

    from app.agents import social_media_agent
    social_media_agent.post("/tmp/final.mp4", "Original caption #ai", "Big AI News")

    mock_enhance.assert_called_once_with("Original caption #ai")
    mock_yt.upload_video.assert_called_once_with("/tmp/final.mp4", "Big AI News", "Enhanced caption #ai")
    mock_fs.update_batch_status.assert_called_with("batch_001", "completed")
    mock_send_post.assert_called_once_with(
        "Big AI News", "https://www.youtube.com/shorts/xyz"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_pipeline.py -v -k "social_media_agent"
```

Expected: `ImportError` — `social_media_agent` does not exist yet.

- [ ] **Step 3: Create app/agents/social_media_agent.py**

Note: `whatsapp_agent` is imported lazily inside `post()` to break the circular chain.

```python
# app/agents/social_media_agent.py

from app.services import youtube_service, firestore_service
from app.services.llm_service import enhance_caption


def post(video_path: str, caption: str, title: str):
    enhanced = enhance_caption(caption)
    url = youtube_service.upload_video(video_path, title, enhanced)

    state = firestore_service.get_pipeline_state()
    batch_id = state.get("active_batch_id")
    if batch_id:
        firestore_service.update_batch_status(batch_id, "completed")
        firestore_service.set_pipeline_state(batch_id, "completed")

    # Lazy import to avoid circular dependency with whatsapp_agent → generator_agent → social_media_agent
    from app.agents import whatsapp_agent
    whatsapp_agent.send_post_result(title, url)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_pipeline.py -v -k "social_media_agent"
```

Expected: 1 test PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agents/social_media_agent.py tests/test_pipeline.py
git commit -m "feat: add social_media_agent for caption enhancement and YouTube posting"
```

---

## Task 11: Routes (Research, Webhook, Auth)

**Files:**
- Create: `app/routes/research.py`
- Create: `app/routes/webhook.py`
- Create: `app/routes/auth.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:
```python
# ---------------------------------------------------------------------------
# Task 11: routes
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient


def _make_app():
    from fastapi import FastAPI
    from app.routes.research import router as research_router
    from app.routes.webhook import router as webhook_router
    app = FastAPI()
    app.include_router(research_router)
    app.include_router(webhook_router)
    return app


@patch("app.routes.research.lead_researcher")
def test_research_run_requires_secret(mock_lr):
    import app.config as config
    config.SCHEDULER_SECRET = "mysecret"

    client = TestClient(_make_app())
    resp = client.post("/research/run", headers={"X-Scheduler-Secret": "wrong"})
    assert resp.status_code == 403


@patch("app.routes.research.lead_researcher")
def test_research_run_triggers_lead_researcher(mock_lr):
    import app.config as config
    config.SCHEDULER_SECRET = "mysecret"
    mock_lr.run.return_value = "batch_001"

    client = TestClient(_make_app())
    resp = client.post("/research/run", headers={"X-Scheduler-Secret": "mysecret"})
    assert resp.status_code == 200
    assert resp.json()["batch_id"] == "batch_001"


@patch("app.routes.webhook.whatsapp_agent")
def test_webhook_returns_twiml_immediately(mock_wa):
    client = TestClient(_make_app())
    resp = client.post(
        "/webhook/whatsapp",
        data={"Body": "TECH01", "From": "whatsapp:+91999"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert resp.status_code == 200
    assert "<Response/>" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_pipeline.py -v -k "research_run or webhook"
```

Expected: `ImportError` for missing route modules.

- [ ] **Step 3: Create app/routes/research.py**

```python
# app/routes/research.py

from fastapi import APIRouter, HTTPException, Request
from app.agents import lead_researcher
from app.config import SCHEDULER_SECRET

router = APIRouter()


@router.post("/research/run")
async def run_research(request: Request):
    secret = request.headers.get("X-Scheduler-Secret", "")
    if secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    batch_id = lead_researcher.run()
    return {"batch_id": batch_id}
```

- [ ] **Step 4: Create app/routes/webhook.py**

```python
# app/routes/webhook.py

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import Response
from app.agents import whatsapp_agent

router = APIRouter()


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    body = form.get("Body", "")
    from_number = form.get("From", "")
    background_tasks.add_task(whatsapp_agent.handle_reply, from_number, body)
    return Response(content="<Response/>", media_type="application/xml")
```

- [ ] **Step 5: Create app/routes/auth.py**

```python
# app/routes/auth.py

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, HTMLResponse
from google_auth_oauthlib.flow import Flow

from app.config import YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI
from app.services import firestore_service

router = APIRouter()

_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _make_flow() -> Flow:
    return Flow.from_client_config(
        {
            "web": {
                "client_id": YOUTUBE_CLIENT_ID,
                "client_secret": YOUTUBE_CLIENT_SECRET,
                "redirect_uris": [YOUTUBE_REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=_SCOPES,
        redirect_uri=YOUTUBE_REDIRECT_URI,
    )


@router.get("/auth/youtube")
def youtube_auth():
    flow = _make_flow()
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return RedirectResponse(auth_url)


@router.get("/auth/youtube/callback")
def youtube_callback(code: str):
    flow = _make_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    firestore_service.save_youtube_tokens({
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_expiry": creds.expiry.isoformat() if creds.expiry else None,
        "client_id": YOUTUBE_CLIENT_ID,
        "client_secret": YOUTUBE_CLIENT_SECRET,
    })
    return HTMLResponse("<h1>YouTube auth complete. You can close this tab.</h1>")
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_pipeline.py -v -k "research_run or webhook"
```

Expected: 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routes/research.py app/routes/webhook.py app/routes/auth.py tests/test_pipeline.py
git commit -m "feat: add research, webhook, and auth routes"
```

---

## Task 12: Wire Routes into main.py

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Register the three new routers in app/main.py**

Open `app/main.py` and add imports and `include_router` calls. The final file:
```python
# app/main.py

import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.routes.generate import router as generate_router
from app.routes.research import router as research_router
from app.routes.webhook import router as webhook_router
from app.routes.auth import router as auth_router
from app.utils.helpers import ensure_dir

STATIC_DIR = "app/static"
MEDIA_DIR = "tmp"

ensure_dir(STATIC_DIR)
ensure_dir(MEDIA_DIR)

app = FastAPI()

app.include_router(generate_router)
app.include_router(research_router)
app.include_router(webhook_router)
app.include_router(auth_router)

# Serve generated video/image files at /media/<filename>
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

# Serve frontend static assets
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
```

- [ ] **Step 2: Verify the app starts without errors**

```bash
cd "/Users/chetan/Desktop/Data Science/youtube-video-generation-tool"
python -c "from app.main import app; print('App loaded OK, routes:', [r.path for r in app.routes])"
```

Expected output includes: `/generate`, `/research/run`, `/webhook/whatsapp`, `/auth/youtube`, `/auth/youtube/callback`

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/test_pipeline.py -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "feat: register pipeline routes in FastAPI app"
```

---

## Task 13: Environment Setup and .env Update

**Files:**
- Modify: `.env`

- [ ] **Step 1: Add new variables to .env**

Open `.env` and append:
```
# GNews
GNEWS_API_KEY=<your-gnews-api-key>

# Twilio WhatsApp
TWILIO_ACCOUNT_SID=<your-twilio-account-sid>
TWILIO_AUTH_TOKEN=<your-twilio-auth-token>
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
OWNER_WHATSAPP_TO=whatsapp:+91<your-number>

# YouTube OAuth (client credentials from Google Cloud Console)
YOUTUBE_CLIENT_ID=<your-client-id>
YOUTUBE_CLIENT_SECRET=<your-client-secret>
YOUTUBE_REDIRECT_URI=https://<your-cloud-run-url>/auth/youtube/callback

# Cloud Scheduler auth
SCHEDULER_SECRET=<generate-a-random-string>
```

- [ ] **Step 2: Get GNews API key**

Visit `https://gnews.io` → Sign up for free tier → Copy API key to `.env`.

- [ ] **Step 3: Get Twilio credentials**

- Log in to `https://console.twilio.com`
- Copy Account SID and Auth Token to `.env`
- Go to Messaging → Try it out → Send a WhatsApp message → Note the sandbox number (already in `.env` as default)
- Your WhatsApp number must send `join <sandbox-word>` to the sandbox number to opt in (one-time)

- [ ] **Step 4: Create YouTube OAuth credentials in Google Cloud Console**

- Open `https://console.cloud.google.com` → project `youtube-video-generator-492211`
- APIs & Services → Enable "YouTube Data API v3"
- Credentials → Create Credentials → OAuth 2.0 Client ID → Web application
- Add `https://<cloud-run-url>/auth/youtube/callback` as an Authorized redirect URI
- Copy Client ID and Client Secret to `.env`

---

## Task 14: Deploy to Cloud Run and Configure Cloud Scheduler

- [ ] **Step 1: Build and deploy to Cloud Run**

```bash
cd "/Users/chetan/Desktop/Data Science/youtube-video-generation-tool"
gcloud run deploy autoframe \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --min-instances 1 \
  --set-env-vars "$(cat .env | grep -v '^#' | grep '=' | tr '\n' ',')"
```

Note the deployed service URL (e.g. `https://autoframe-xxxx-uc.a.run.app`).

- [ ] **Step 2: Update YOUTUBE_REDIRECT_URI in .env and redeploy**

Replace `<your-cloud-run-url>` in `.env` with the actual Cloud Run URL, then redeploy with the updated env vars.

- [ ] **Step 3: Run the YouTube OAuth flow (one-time)**

Open in browser:
```
https://<cloud-run-url>/auth/youtube
```

Complete the Google consent screen. You will be redirected to `/auth/youtube/callback` which stores tokens in Firestore. The page should show "YouTube auth complete."

- [ ] **Step 4: Create Cloud Scheduler job**

```bash
gcloud scheduler jobs create http autoframe-lead-researcher \
  --location us-central1 \
  --schedule "0 */4 * * *" \
  --time-zone "Asia/Kolkata" \
  --uri "https://<cloud-run-url>/research/run" \
  --http-method POST \
  --headers "X-Scheduler-Secret=<SCHEDULER_SECRET>,Content-Type=application/json" \
  --message-body "{}"
```

- [ ] **Step 5: Register Twilio webhook URL**

- Go to Twilio Console → Messaging → Settings → WhatsApp Sandbox Settings
- Set "When a message comes in" to:  
  `https://<cloud-run-url>/webhook/whatsapp` (HTTP POST)
- Save

---

## Task 15: End-to-End Smoke Test

- [ ] **Step 1: Trigger Lead Researcher manually**

```bash
curl -X POST https://<cloud-run-url>/research/run \
  -H "X-Scheduler-Secret: <SCHEDULER_SECRET>"
```

Expected response:
```json
{"batch_id": "batch_20260404_100000"}
```

- [ ] **Step 2: Verify WhatsApp message received**

Check your WhatsApp — you should receive a formatted digest with TECH01–TECH05 headlines within ~30 seconds.

- [ ] **Step 3: Reply with a news code**

Reply `TECH01` (or whichever code you prefer) to the WhatsApp message.

- [ ] **Step 4: Watch Cloud Run logs**

```bash
gcloud run services logs tail autoframe --region us-central1
```

Expected log sequence:
```
WhatsApp reply received: TECH01
Generating video for: <headline>
Music genre selected: News Bulletin
Creating video...
Enhancing caption...
Uploading to YouTube...
Posted: https://www.youtube.com/shorts/<id>
```

- [ ] **Step 5: Verify final WhatsApp confirmation**

You should receive a WhatsApp message with the YouTube Shorts URL within 2–4 minutes (video generation + upload time).

- [ ] **Step 6: Verify Cloud Scheduler fires automatically**

Wait for the next 4-hour interval or force-run from GCP Console:
- Cloud Scheduler → `autoframe-lead-researcher` → Force run

Confirm another WhatsApp digest arrives.

---

## Summary: New Env Vars Required

| Variable | Where to get it |
|---|---|
| `GNEWS_API_KEY` | gnews.io free tier |
| `TWILIO_ACCOUNT_SID` | Twilio console |
| `TWILIO_AUTH_TOKEN` | Twilio console |
| `TWILIO_WHATSAPP_FROM` | Twilio sandbox: `whatsapp:+14155238886` |
| `OWNER_WHATSAPP_TO` | Your own WhatsApp: `whatsapp:+91XXXXXXXXXX` |
| `YOUTUBE_CLIENT_ID` | GCP → APIs & Services → Credentials |
| `YOUTUBE_CLIENT_SECRET` | GCP → APIs & Services → Credentials |
| `YOUTUBE_REDIRECT_URI` | `https://<cloud-run-url>/auth/youtube/callback` |
| `SCHEDULER_SECRET` | Any random string you choose |
