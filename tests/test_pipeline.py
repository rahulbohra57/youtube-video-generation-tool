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


def test_format_caption_for_youtube_normalizes_spacing_and_hashtags():
    from app.services.llm_service import format_caption_for_youtube
    raw = "  Hook line.  \n\nBody line here.  \n#AI\n#Tech\n#AI  "
    out = format_caption_for_youtube(raw)
    assert "  " not in out
    assert "\n\n#AI #Tech" in out


# ---------------------------------------------------------------------------
# Task 5: telegram_service
# ---------------------------------------------------------------------------

@patch("app.services.telegram_service.httpx.post")
def test_send_message_calls_telegram_with_correct_params(mock_post):
    mock_post.return_value = MagicMock(raise_for_status=MagicMock())

    from app.services import telegram_service
    telegram_service.send_message("123456789", "Hello test")

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args[1]
    assert call_kwargs["json"]["chat_id"] == "123456789"
    assert call_kwargs["json"]["text"] == "Hello test"
    assert call_kwargs["json"]["parse_mode"] == "Markdown"


@patch("app.services.telegram_service.httpx.post")
def test_send_message_falls_back_to_plain_text(mock_post):
    markdown_resp = MagicMock()
    markdown_resp.raise_for_status.side_effect = Exception("can't parse entities")
    plain_resp = MagicMock()
    plain_resp.raise_for_status.return_value = None
    mock_post.side_effect = [markdown_resp, plain_resp]

    from app.services import telegram_service
    ok = telegram_service.send_message("123456789", "Hello [test]")

    assert ok is True
    assert mock_post.call_count == 2
    first_payload = mock_post.call_args_list[0][1]["json"]
    second_payload = mock_post.call_args_list[1][1]["json"]
    assert "parse_mode" in first_payload
    assert "parse_mode" not in second_payload


@patch.dict(
    "os.environ",
    {
        "TELEGRAM_BOT_TOKEN": "news-token",
        "STORIES_BOT_TOKEN": "stories-token",
        "STORIES_CHAT_ID": "123456789",
    },
    clear=False,
)
@patch("app.services.telegram_service.httpx.post")
def test_send_message_uses_explicit_stories_channel_token(mock_post):
    mock_post.return_value = MagicMock(raise_for_status=MagicMock())

    from app.services import telegram_service
    telegram_service.send_message("123456789", "Hello stories", channel_id="stories")

    called_url = mock_post.call_args[0][0]
    assert "botstories-token" in called_url


@patch.dict(
    "os.environ",
    {
        "TELEGRAM_BOT_TOKEN": "news-token",
        "STORIES_BOT_TOKEN": "stories-token",
        "STORIES_CHAT_ID": "123456789",
    },
    clear=False,
)
@patch("app.services.telegram_service.httpx.post")
def test_send_message_uses_explicit_news_channel_token(mock_post):
    mock_post.return_value = MagicMock(raise_for_status=MagicMock())

    from app.services import telegram_service
    telegram_service.send_message("123456789", "Hello news", channel_id="news")

    called_url = mock_post.call_args[0][0]
    assert "botnews-token" in called_url


def test_choose_voice_for_video_returns_requested_gender():
    from app.services import tts_service
    voice = tts_service.choose_voice_for_video(language="en", preference="female")
    options = {v["name"]: v["gender"] for v in tts_service.get_voice_options("en")}
    assert options[voice] == "female"


# ---------------------------------------------------------------------------
# Task 6: lead_researcher agent
# ---------------------------------------------------------------------------

@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch("app.agents.lead_researcher.send_message")
@patch("app.agents.lead_researcher.firestore_service")
@patch("app.agents.lead_researcher.rate_and_select_news")
@patch("app.agents.lead_researcher.gnews_service")
@patch("app.agents.lead_researcher._within_suggestion_window", return_value=True)
def test_lead_researcher_run_creates_batch_and_enqueues_video(
    mock_window, mock_gnews, mock_rate, mock_fs, mock_send_message, mock_enqueue
):
    mock_fs.get_pipeline_state.return_value = {}
    mock_fs.get_domains_posted_today.return_value = {}
    mock_fs.get_top_performers.return_value = []
    mock_fs.get_genre_performance_weekly.return_value = {}
    mock_fs.get_recently_suggested_headlines.return_value = []
    mock_gnews.fetch_top_headlines.return_value = [
        {"headline": f"News {i}", "url": "https://x", "description": "desc"} for i in range(10)
    ]
    mock_gnews.search_news.return_value = [
        {"headline": f"Search News {i}", "url": "https://x", "description": "desc"} for i in range(10)
    ]
    mock_rate.return_value = [
        {"headline": f"Top Pick {i}", "context": "ctx", "rating": 4.6, "description": "brief"} for i in range(1, 6)
    ]
    mock_fs.is_headline_already_suggested.return_value = False

    from app.agents import lead_researcher
    batch_id = lead_researcher.run()

    assert batch_id.startswith("auto_")
    mock_fs.save_news_batch.assert_called_once()
    mock_fs.set_pipeline_and_batch_state.assert_called_once()
    mock_send_message.assert_called_once()
    mock_enqueue.assert_called_once()


def test_fallback_domain_query_map_has_five_entries():
    from app.agents.lead_researcher import _fallback_domain_query_map
    m = _fallback_domain_query_map()
    assert len(m) == 5
    for name, cfg in m.items():
        assert "query" in cfg, f"{name} missing 'query'"
        assert "category" in cfg, f"{name} missing 'category'"


def test_prefix_for_domain_covers_all_fallback_domains():
    from app.agents.lead_researcher import _prefix_for_domain
    assert _prefix_for_domain("Health") == "HLTH"
    assert _prefix_for_domain("Business") == "BIZ"
    assert _prefix_for_domain("Sports") == "SPRT"
    assert _prefix_for_domain("Entertainment") == "ENT"
    assert _prefix_for_domain("Environment") == "ENV"


def test_primary_domain_query_map_has_five_entries():
    from app.agents.lead_researcher import _primary_domain_query_map
    m = _primary_domain_query_map()
    assert set(m.keys()) == {
        "Technology", "Artificial Intelligence", "Current Affairs", "Trending", "Science"
    }


@patch("app.agents.lead_researcher.firestore_service")
def test_lead_researcher_expires_stale_awaiting_reply_digest(mock_fs):
    from app.agents import lead_researcher

    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_old",
        "state": "awaiting_reply",
    }
    mock_fs.get_news_batch.return_value = {
        "created_at": "2020-01-01T00:00:00+00:00",
        "status": "awaiting_reply",
        "items": {},
    }

    lead_researcher._expire_stale_digest_if_needed()

    mock_fs.set_pipeline_and_batch_state.assert_called_once_with("batch_old", "skipped")


@patch("app.agents.lead_researcher._within_suggestion_window", return_value=False)
def test_lead_researcher_skips_outside_allowed_hours(mock_window):
    from app.agents import lead_researcher
    assert lead_researcher.run() is None


@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch("app.agents.lead_researcher.send_message")
@patch("app.agents.lead_researcher.firestore_service")
@patch("app.agents.lead_researcher.rate_and_select_news")
@patch("app.agents.lead_researcher.gnews_service")
@patch("app.agents.lead_researcher._within_suggestion_window", return_value=True)
def test_lead_researcher_falls_back_to_new_domain_when_primary_exhausted(
    mock_window, mock_gnews, mock_rate, mock_fs, mock_send, mock_enqueue
):
    """When all 5 primary domains have no quality articles, Phase 3 fetches a fallback domain."""
    mock_fs.get_pipeline_state.return_value = {}
    mock_fs.get_domains_posted_today.return_value = {}
    mock_fs.get_top_performers.return_value = []
    mock_fs.get_genre_performance_weekly.return_value = {}
    mock_fs.get_recently_suggested_headlines.return_value = []
    mock_fs.is_headline_already_suggested.return_value = False

    fallback_article = {
        "headline": "Health breakthrough announced",
        "url": "https://example.com/health",
        "description": "Scientists discover cure",
        "published_at": "2026-04-09T10:00:00Z",
        "source": "HealthNews",
    }

    call_count = {"n": 0}

    def search_side_effect(**kwargs):
        call_count["n"] += 1
        # First 5 calls are primary domains → empty; subsequent calls are Phase 3 → article
        if call_count["n"] <= 5:
            return []
        return [fallback_article]

    mock_gnews.search_news.side_effect = search_side_effect
    mock_rate.return_value = []

    from app.agents import lead_researcher
    batch_id = lead_researcher.run()

    assert batch_id is not None
    assert batch_id.startswith("auto_")
    mock_fs.save_news_batch.assert_called_once()
    mock_enqueue.assert_called_once()


@patch("app.agents.lead_researcher.firestore_service")
@patch("app.agents.lead_researcher.gnews_service")
@patch("app.agents.lead_researcher._within_suggestion_window", return_value=True)
def test_lead_researcher_uses_news_only_recently_covered(mock_window, mock_gnews, mock_fs):
    mock_fs.get_pipeline_state.return_value = {}
    mock_fs.get_domains_posted_today.return_value = {}
    mock_fs.get_top_performers.return_value = []
    mock_fs.get_genre_performance_weekly.return_value = {}
    mock_fs.get_recently_suggested_headlines.return_value = []
    mock_fs.is_headline_already_suggested.return_value = False
    mock_gnews.search_news.return_value = []

    from app.agents import lead_researcher
    lead_researcher.run()

    mock_fs.get_recently_suggested_headlines.assert_called_once_with(
        days=14, limit=20, channel_id="news"
    )


@patch("app.agents.lead_researcher.send_message")
@patch("app.agents.lead_researcher.firestore_service")
@patch("app.agents.lead_researcher.rate_and_select_news")
@patch("app.agents.lead_researcher.gnews_service")
@patch("app.agents.lead_researcher._within_suggestion_window", return_value=True)
def test_lead_researcher_returns_none_when_all_domains_truly_empty(
    mock_window, mock_gnews, mock_rate, mock_fs, mock_send
):
    """Returns None only when primary AND all fallback domains yield nothing."""
    mock_fs.get_pipeline_state.return_value = {}
    mock_fs.get_domains_posted_today.return_value = {}
    mock_fs.get_top_performers.return_value = []
    mock_fs.get_genre_performance_weekly.return_value = {}
    mock_fs.get_recently_suggested_headlines.return_value = []
    mock_fs.is_headline_already_suggested.return_value = False

    mock_gnews.search_news.return_value = []
    mock_rate.return_value = []

    from app.agents import lead_researcher
    result = lead_researcher.run()

    assert result is None
    mock_fs.save_news_batch.assert_not_called()


def test_lead_researcher_uses_24h_lookback():
    """Verify lookback_hours is 24 in the run() function source."""
    import inspect
    from app.agents import lead_researcher
    src = inspect.getsource(lead_researcher.run)
    assert "lookback_hours = 24" in src


@patch("app.agents.story_researcher.firestore_service")
def test_story_researcher_recent_titles_are_stories_only(mock_fs):
    mock_fs.get_recently_suggested_headlines.return_value = []

    from app.agents import story_researcher
    story_researcher._recently_used_titles(limit=5)

    mock_fs.get_recently_suggested_headlines.assert_called_once_with(
        days=30, limit=5, channel_id="stories"
    )


# ---------------------------------------------------------------------------
# Task 7: whatsapp_agent
# ---------------------------------------------------------------------------

@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_send_digest_formats_and_sends_message(mock_fs, mock_telegram):
    mock_fs.get_news_batch.return_value = {
        "genre": "technology",
        "items": {
            "TECH01": {"headline": "AI news", "context": "Big AI development.", "rating": 4.5},
            "TECH02": {"headline": "Chip news", "context": "New chip released.", "rating": 4.0},
        }
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.send_digest("batch_001")

    mock_fs.get_news_batch.assert_called_with("batch_001")
    mock_telegram.send_message.assert_called_once()
    sent_body = mock_telegram.send_message.call_args[0][1]
    assert "TECH01" in sent_body
    assert "AI news" in sent_body
    assert "TECH02" in sent_body


@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_none_skips_pipeline(mock_fs, mock_telegram):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001", "state": "awaiting_reply"
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "none")

    mock_fs.set_pipeline_and_batch_state.assert_called_with("batch_001", "skipped")
    mock_telegram.send_message.assert_called_once()
    assert "See you" in mock_telegram.send_message.call_args[0][1]


@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_discard_skips_pending_request(mock_fs, mock_telegram):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001", "state": "awaiting_reply"
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "DISCARD")

    mock_fs.set_pipeline_and_batch_state.assert_called_with("batch_001", "skipped")
    sent = mock_telegram.send_message.call_args[0][1]
    assert "Discarded" in sent


@patch("app.agents.whatsapp_agent._enqueue_generate")
@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_valid_code_triggers_generator(mock_fs, mock_telegram, mock_enqueue):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001", "state": "awaiting_reply"
    }
    mock_fs.get_news_batch.return_value = {
        "items": {"TECH01": {"headline": "Big AI news", "context": "ctx", "rating": 4.8}}
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "tech01")

    mock_enqueue.assert_called_once()
    args = mock_enqueue.call_args[0]
    kwargs = mock_enqueue.call_args[1]
    assert args == ("Big AI news", "TECH01", "batch_001")
    assert "public_id" in kwargs
    mock_fs.set_pipeline_and_batch_state.assert_called_with("batch_001", "processing")


@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_invalid_code_sends_error(mock_fs, mock_telegram):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001", "state": "awaiting_reply"
    }
    mock_fs.get_news_batch.return_value = {
        "items": {"TECH01": {"headline": "AI news", "context": "ctx", "rating": 4.5}}
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "TECH99")

    mock_telegram.send_message.assert_called_once()
    assert "Invalid" in mock_telegram.send_message.call_args[0][1]


@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_blocks_concurrent_processing(mock_fs, mock_telegram):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001", "state": "processing"
    }
    mock_fs.get_news_batch.return_value = {
        "items": {"TECH01": {"headline": "AI news", "context": "ctx", "rating": 4.5}}
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "TECH01")

    mock_telegram.send_message.assert_called_once()
    assert "already being processed" in mock_telegram.send_message.call_args[0][1]


@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_marks_expired_digest_as_skipped(mock_fs, mock_telegram):
    mock_fs.get_pipeline_state.return_value = {
        "active_batch_id": "batch_001",
        "state": "awaiting_reply",
    }
    mock_fs.get_news_batch.return_value = {
        "created_at": "2020-01-01T00:00:00+00:00",
        "items": {"TECH01": {"headline": "AI news", "context": "ctx", "rating": 4.5}},
    }

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "TECH01")

    mock_fs.set_pipeline_and_batch_state.assert_called_with("batch_001", "skipped")
    assert "expired after 2 hours" in mock_telegram.send_message.call_args[0][1]


@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch(
    "app.agents.whatsapp_agent._best_recent_article",
    return_value={
        "headline": "Artemis mission update",
        "url": "https://example.com/artemis",
        "published_at": "2026-04-09T10:00:00Z",
        "description": "Latest Artemis mission update.",
        "source": "Example News",
    },
)
@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_create_topic_triggers_direct_generation(mock_fs, mock_telegram, _mock_source, mock_enqueue):
    mock_fs.get_pipeline_state.return_value = {"state": "awaiting_reply", "active_batch_id": "batch_001"}
    mock_fs.acquire_idempotency_key.return_value = (True, {"status": "new"})

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "CREATE Why is Artemis going to moon?")

    mock_fs.save_news_batch.assert_called_once()
    mock_fs.set_pipeline_and_batch_state.assert_called_once()
    mock_enqueue.assert_called_once()


@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
@patch(
    "app.agents.whatsapp_agent._best_recent_article",
    return_value={
        "headline": "Artemis mission update",
        "url": "https://example.com/artemis",
        "published_at": "2026-04-09T10:00:00Z",
    },
)
def test_handle_reply_create_topic_duplicate_is_ignored(mock_source, mock_fs, mock_telegram):
    mock_fs.acquire_idempotency_key.return_value = (False, {"status": "queued"})
    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "CREATE Why is Artemis going to moon?")
    mock_telegram.send_message.assert_called_once()
    assert "already" in mock_telegram.send_message.call_args[0][1]


@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch("app.agents.whatsapp_agent._best_recent_article", return_value=None)
@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_create_topic_requires_recent_source(mock_fs, mock_telegram, _mock_source, mock_enqueue):
    mock_fs.get_pipeline_state.return_value = {"state": "awaiting_reply", "active_batch_id": "batch_001"}
    mock_fs.acquire_idempotency_key.return_value = (True, {"status": "new"})

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "CREATE Why is Artemis going to moon?")

    mock_enqueue.assert_not_called()
    sent = mock_telegram.send_message.call_args[0][1]
    assert "could not find a recent" in sent


@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch("app.agents.whatsapp_agent._best_recent_article", return_value=None)
@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_create_accepts_user_article_link_when_recent_source_missing(
    mock_fs, mock_telegram, _mock_source, mock_enqueue
):
    mock_fs.get_pipeline_state.return_value = {"state": "awaiting_reply", "active_batch_id": "batch_001"}
    mock_fs.acquire_idempotency_key.return_value = (True, {"status": "new"})

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply(
        "123456789",
        "CREATE Why is Artemis going to moon? | https://example.com/source-article",
    )

    mock_enqueue.assert_called_once()
    details = mock_enqueue.call_args[1]["details"]
    assert "Source URL: https://example.com/source-article" in details


@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch(
    "app.agents.whatsapp_agent._best_recent_article",
    return_value={
        "headline": "Artemis mission update",
        "url": "https://example.com/artemis",
        "published_at": "2026-04-09T10:00:00Z",
    },
)
@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_force_create_allows_rejected_busy_override(mock_fs, mock_telegram, _mock_source, mock_enqueue):
    mock_fs.acquire_idempotency_key.return_value = (False, {"status": "rejected_busy"})
    mock_fs.get_pipeline_state.return_value = {"state": "processing", "active_batch_id": "batch_001"}

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "FORCE_CREATE Why is Artemis going to moon?")

    mock_enqueue.assert_called_once()
    sent = mock_telegram.send_message.call_args[0][1]
    assert "FORCE_CREATE accepted" in sent


@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch(
    "app.agents.whatsapp_agent._best_recent_article",
    return_value={
        "headline": "Artemis mission update",
        "url": "https://example.com/artemis",
        "published_at": "2026-04-09T10:00:00Z",
    },
)
@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_force_create_allows_completed_override(mock_fs, mock_telegram, _mock_source, mock_enqueue):
    mock_fs.acquire_idempotency_key.return_value = (False, {"status": "completed"})
    mock_fs.get_pipeline_state.return_value = {"state": "completed", "active_batch_id": "batch_001"}

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "FORCE_CREATE Why is Artemis going to moon?")

    mock_enqueue.assert_called_once()
    sent = mock_telegram.send_message.call_args[0][1]
    assert "FORCE_CREATE accepted" in sent


@patch("app.agents.whatsapp_agent._enqueue_generate", return_value=True)
@patch(
    "app.agents.whatsapp_agent._best_recent_article",
    return_value={
        "headline": "Artemis mission update",
        "url": "https://example.com/artemis",
        "published_at": "2026-04-09T10:00:00Z",
    },
)
@patch("app.agents.whatsapp_agent.telegram_service")
@patch("app.agents.whatsapp_agent.firestore_service")
def test_handle_reply_force_create_bypasses_duplicate_status(mock_fs, mock_telegram, _mock_source, mock_enqueue):
    mock_fs.acquire_idempotency_key.return_value = (False, {"status": "queued"})
    mock_fs.get_pipeline_state.return_value = {"state": "processing", "active_batch_id": "batch_001"}

    from app.agents import whatsapp_agent
    whatsapp_agent.handle_reply("123456789", "FORCE_CREATE Why is Artemis going to moon?")

    mock_enqueue.assert_called_once()
    assert "FORCE_CREATE accepted" in mock_telegram.send_message.call_args[0][1]


# ---------------------------------------------------------------------------
# Task 8: generator_agent
# ---------------------------------------------------------------------------

@patch("app.agents.social_media_agent.post")
@patch(
    "app.agents.generator_agent.review_package",
    return_value={
        "scenes": [
            {"scene": 1, "narration": "Big AI news story.", "visual": "AI illustration"},
            {"scene": 2, "narration": "Second scene narration.", "visual": "Tech illustration"},
        ],
        "title": "Big AI news story",
        "caption": "Great caption #ai",
        "estimated_seconds": 22.0,
    },
)
@patch("app.agents.generator_agent.create_video")
@patch("app.agents.generator_agent.generate_image", return_value="/tmp/scene_TECH01_0.png")
@patch("app.agents.generator_agent.generate_audio")
@patch("app.agents.generator_agent.classify_music_genre", return_value="News Bulletin")
@patch("app.agents.generator_agent.generate_script")
@patch("app.agents.generator_agent.extract_json")
@patch("app.agents.generator_agent.send_message")
@patch("app.agents.generator_agent.firestore_service.release_video_lock", return_value=True)
@patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True)
def test_generator_agent_run_calls_social_media_agent(
    mock_acquire_lock, mock_release_lock, mock_send_message,
    mock_extract, mock_script, mock_music, mock_audio,
    mock_image, mock_video, mock_review, mock_social_post
):
    mock_script.return_value = "[]"
    mock_extract.return_value = [
        {"scene": 1, "narration": "draft", "visual": "draft"},
        {"scene": 2, "narration": "draft2", "visual": "draft2"},
    ]

    from app.agents import generator_agent
    generator_agent.run("Big AI news story", "TECH01")

    mock_social_post.assert_called_once()
    mock_acquire_lock.assert_called_once()
    mock_release_lock.assert_called_once()
    call_kwargs = mock_social_post.call_args[1]
    assert call_kwargs["caption"] == "Great caption #ai"
    assert call_kwargs["title"] == "Big AI news story"


@patch("app.agents.generator_agent.send_message")
@patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=False)
def test_generator_agent_run_skips_when_lock_held(mock_acquire_lock, mock_send_message):
    from app.agents import generator_agent
    generator_agent.run("Big AI news story", "TECH01")
    mock_acquire_lock.assert_called_once()
    mock_send_message.assert_called_once()


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


def test_normalize_title_caps_to_100_chars():
    from app.services.youtube_service import normalize_title
    long_title = "A" * 140
    out = normalize_title(long_title, limit=100)
    assert len(out) <= 100


@patch("app.services.youtube_service.firestore_service")
def test_get_credentials_raises_when_no_tokens(mock_fs):
    mock_fs.get_youtube_tokens.return_value = None

    from app.services import youtube_service
    with pytest.raises(RuntimeError, match="YouTube OAuth tokens not found"):
        youtube_service.get_credentials()


# ---------------------------------------------------------------------------
# Task 10: social_media_agent
# ---------------------------------------------------------------------------

@patch("app.agents.whatsapp_agent.send_post_result")
@patch("app.agents.social_media_agent.firestore_service")
@patch("app.agents.social_media_agent.youtube_service")
@patch("app.agents.social_media_agent.enhance_caption", return_value="Enhanced caption #ai")
@patch("app.agents.social_media_agent.send_message")
def test_social_media_agent_post_uploads_and_notifies(
    mock_send_message, mock_enhance, mock_yt, mock_fs, mock_send_post
):
    mock_yt.upload_video.return_value = "https://www.youtube.com/shorts/xyz"
    mock_fs.get_pipeline_state.return_value = {"active_batch_id": "batch_001"}

    from app.agents import social_media_agent
    social_media_agent.post("/tmp/final.mp4", "Original caption #ai", "Big AI News")

    mock_enhance.assert_called_once_with("Original caption #ai")
    mock_yt.upload_video.assert_called_once()
    args = mock_yt.upload_video.call_args[0]
    assert args[0] == "/tmp/final.mp4"
    assert args[1] == "Big AI News"
    assert "Enhanced caption" in args[2]
    mock_fs.set_pipeline_and_batch_state.assert_called_with("batch_001", "completed")
    mock_send_post.assert_called_once()
    kwargs = mock_send_post.call_args[1]
    assert kwargs["title"] == "Big AI News"
    assert kwargs["url"] == "https://www.youtube.com/shorts/xyz"


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


@patch("app.routes.research.SCHEDULER_SECRET", "mysecret")
@patch("app.routes.research.lead_researcher")
def test_research_run_requires_secret(mock_lr):
    client = TestClient(_make_app())
    resp = client.post("/research/run", headers={"X-Scheduler-Secret": "wrong"})
    assert resp.status_code == 403


@patch("app.routes.research.SCHEDULER_SECRET", "mysecret")
@patch("app.routes.research.lead_researcher")
def test_research_run_triggers_lead_researcher(mock_lr):
    mock_lr.run.return_value = "batch_001"

    client = TestClient(_make_app())
    resp = client.post("/research/run", headers={"X-Scheduler-Secret": "mysecret"})
    assert resp.status_code == 200
    assert resp.json()["batch_id"] == "batch_001"


@patch("app.routes.research.SCHEDULER_SECRET", "mysecret")
@patch("app.routes.research.lead_researcher")
def test_research_run_returns_skipped_when_no_batch(mock_lr):
    mock_lr.run.return_value = None

    client = TestClient(_make_app())
    resp = client.post("/research/run", headers={"X-Scheduler-Secret": "mysecret"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


@patch("app.services.telegram_service.send_message")
@patch("app.services.youtube_service.get_channel_stats")
@patch("app.routes.stories.firestore_service")
def test_stories_daily_digest_uses_stories_queue_only(mock_fs, mock_get_channel_stats, _mock_send_message):
    mock_get_channel_stats.return_value = {"subscriber_count": 1, "view_count": 10, "video_count": 2}
    mock_fs._ist_window_start.return_value = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    mock_fs.get_queue_snapshot.return_value = {"completed_24h": 1, "failed_24h": 0}
    mock_fs.get_tts_chars_this_month.return_value = 100

    from app.routes import stories
    stories._send_stories_daily_digest()

    assert mock_fs.get_queue_snapshot.call_args[1]["channel_id"] == "stories"


@patch("app.routes.webhook.whatsapp_agent")
def test_webhook_returns_ok_immediately(mock_wa):
    client = TestClient(_make_app())
    resp = client.post(
        "/webhook/telegram",
        json={"message": {"text": "TECH01", "chat": {"id": 123456789}}}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
