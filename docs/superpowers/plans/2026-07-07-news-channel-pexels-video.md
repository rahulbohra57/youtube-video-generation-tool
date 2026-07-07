# News Channel Shorts: Imagen → Pexels Stock Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Imagen-generated stills with Pexels stock video clips for `channel_id="news"` Shorts scenes only, since Imagen is confirmed inaccessible (403) on the production GCP project and no Gemini-API paid-plan path is currently available.

**Architecture:** News scripts emit a `visual_query` (short Pexels search phrase) instead of `visual` (Imagen prompt); `generator_agent.py` branches on `channel_id` to call `pexels_service.fetch_clip()` instead of `image_service.generate_image()`; `video_service.create_video()` gets a new per-scene branch that loads a `.mp4` via `VideoFileClip`, trims/loops it to the narration's audio duration, and crops to 1080×1920 — skipping the Ken-Burns path used for static Imagen stills. Tell Me Why (`channel_id="stories"`, both Shorts and its separate long-format pipeline) is untouched.

**Tech Stack:** Python 3.10, MoviePy (video assembly), Vertex AI Gemini (script generation, unchanged), Pexels Videos API (`requests`), pytest + `unittest.mock`.

## Global Constraints

- Scope is `channel_id="news"` only — `channel_id="stories"` (Shorts and long-format) must be byte-for-byte behaviorally unchanged.
- No Imagen fallback for news scenes — Pexels fully replaces it for this pipeline (per approved spec).
- Target frame for news scenes: 1080×1920 (portrait), matching the existing `stories` target.
- Pexels search orientation for news: `"portrait"` first, falling back to `"landscape"` (cropped) only if portrait is fully exhausted across the whole query fallback chain.
- `visual_query` field: 3-7 word Pexels search phrase, always in English, describing a concrete real-world scene (not an AI-image-generation style prompt).
- A scene where `pexels_service.fetch_clip()` returns `""` (all fallbacks exhausted) must be treated as a scene failure identical to today's Imagen-exception path — it must **not** silently succeed with an empty asset path.
- Run `pytest tests/` after every task; all tests (existing + new) must pass before moving to the next task.
- Follow existing code patterns exactly (moviepy `try/except` dual-import style, `_clip_duration`/`_clip_audio`/`_subclip` helper functions, `_run_with_backoff` retry wrapper) — do not introduce new abstractions where an existing helper already does the job.

---

### Task 1: Pexels service — portrait orientation + News category fallbacks

**Files:**
- Modify: `app/services/pexels_service.py`
- Test: `tests/test_pexels_service.py`

**Interfaces:**
- Consumes: nothing new — `requests`, `TEMP_DIR`, `ensure_dir` (already imported).
- Produces: `fetch_clip(query: str, audio_duration: float, scene_idx: int, category: str = "", temp_dir: str = TEMP_DIR, orientation: str = "landscape") -> str` — new `orientation` parameter, default preserves existing behavior for the long-format caller (which never passes it). When `orientation="portrait"`, on total exhaustion of the query fallback chain, retries the same chain once more with `orientation="landscape"` before giving up. Returns `""` only if both orientations are fully exhausted (unchanged sentinel meaning). `_CATEGORY_FALLBACKS` gains News domain keys used by later tasks: `"artificial intelligence"`, `"technology"`, `"current affairs"`, `"science"`, `"health"`, `"business"`, `"sports"`, `"entertainment"`, `"environment"`.

- [ ] **Step 1: Write failing test for orientation being passed through to the Pexels search request**

Add to `tests/test_pexels_service.py`:

```python
def test_fetch_clip_portrait_orientation_searched_first(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    videos = [_make_video(25.0)]
    mock_search_resp = MagicMock()
    mock_search_resp.json.return_value = {"videos": videos}
    mock_search_resp.raise_for_status = MagicMock()

    seen_orientations = []

    def side_effect(url, **kwargs):
        if "api.pexels.com" in url:
            seen_orientations.append(kwargs["params"]["orientation"])
            return mock_search_resp
        return _mock_download(url)

    with patch("app.services.pexels_service.requests.get", side_effect=side_effect):
        result = ps.fetch_clip("skyline sunrise city", 20.0, scene_idx=0, temp_dir=str(tmp_path), orientation="portrait")

    assert result == str(tmp_path / "pexels_0.mp4")
    assert seen_orientations[0] == "portrait"


def test_fetch_clip_falls_back_to_landscape_when_portrait_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    empty_resp = MagicMock()
    empty_resp.json.return_value = {"videos": []}
    empty_resp.raise_for_status = MagicMock()

    landscape_resp = MagicMock()
    landscape_resp.json.return_value = {"videos": [_make_video(25.0)]}
    landscape_resp.raise_for_status = MagicMock()

    seen_orientations = []

    def side_effect(url, **kwargs):
        if "api.pexels.com" in url:
            orientation = kwargs["params"]["orientation"]
            seen_orientations.append(orientation)
            return empty_resp if orientation == "portrait" else landscape_resp
        return _mock_download(url)

    with patch("app.services.pexels_service.requests.get", side_effect=side_effect):
        result = ps.fetch_clip("rare unusual query", 20.0, scene_idx=1, temp_dir=str(tmp_path), orientation="portrait")

    assert result == str(tmp_path / "pexels_1.mp4")
    # No category passed -> 3-item fallback chain (query, broad, generic).
    # All 3 portrait attempts exhausted first, then the landscape pass
    # succeeds on its first (exact-query) attempt.
    assert seen_orientations.count("portrait") == 3
    assert "landscape" in seen_orientations


def test_fetch_clip_default_orientation_is_landscape_only(tmp_path, monkeypatch):
    """Existing long-format caller never passes orientation — must stay single-pass landscape."""
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    empty_resp = MagicMock()
    empty_resp.json.return_value = {"videos": []}
    empty_resp.raise_for_status = MagicMock()

    seen_orientations = []

    def side_effect(url, **kwargs):
        if "api.pexels.com" in url:
            seen_orientations.append(kwargs["params"]["orientation"])
            return empty_resp
        return _mock_download(url)

    with patch("app.services.pexels_service.requests.get", side_effect=side_effect):
        result = ps.fetch_clip("very specific unusual query xyz", 20.0, scene_idx=2, temp_dir=str(tmp_path))

    assert result == ""
    assert set(seen_orientations) == {"landscape"}
    # No category passed -> 3-item fallback chain (query, broad, generic),
    # single orientation pass (default orientation="landscape", no portrait retry).
    assert len(seen_orientations) == 3


def test_fetch_clip_news_category_fallback_terms(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    import app.services.pexels_service as ps
    importlib.reload(ps)

    empty_resp = MagicMock()
    empty_resp.json.return_value = {"videos": []}
    empty_resp.raise_for_status = MagicMock()

    match_resp = MagicMock()
    match_resp.json.return_value = {"videos": [_make_video(25.0)]}
    match_resp.raise_for_status = MagicMock()

    seen_queries = []

    def side_effect(url, **kwargs):
        if "api.pexels.com" in url:
            query = kwargs["params"]["query"]
            seen_queries.append(query)
            return match_resp if query == ps._CATEGORY_FALLBACKS["artificial intelligence"] else empty_resp
        return _mock_download(url)

    with patch("app.services.pexels_service.requests.get", side_effect=side_effect):
        result = ps.fetch_clip(
            "one two three four five specific words",
            20.0,
            scene_idx=3,
            category="Artificial Intelligence",
            temp_dir=str(tmp_path),
        )

    assert result == str(tmp_path / "pexels_3.mp4")
    assert ps._CATEGORY_FALLBACKS["artificial intelligence"] in seen_queries
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pexels_service.py -v -k "orientation or news_category"`
Expected: FAIL — `_search_pexels()` has no `orientation` param yet, `fetch_clip()` doesn't accept `orientation=`, and `_CATEGORY_FALLBACKS` has no `"artificial intelligence"` key.

- [ ] **Step 3: Implement `_search_pexels()` orientation parameter**

In `app/services/pexels_service.py`, replace:

```python
def _search_pexels(query: str, per_page: int = 5) -> list[dict]:
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY env var is not set")
    resp = requests.get(
        _VIDEOS_SEARCH_URL,
        headers={"Authorization": api_key},
        params={"query": query, "orientation": "landscape", "size": "medium", "per_page": per_page},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("videos", [])
```

with:

```python
def _search_pexels(query: str, per_page: int = 5, orientation: str = "landscape") -> list[dict]:
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY env var is not set")
    resp = requests.get(
        _VIDEOS_SEARCH_URL,
        headers={"Authorization": api_key},
        params={"query": query, "orientation": orientation, "size": "medium", "per_page": per_page},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("videos", [])
```

- [ ] **Step 4: Implement `fetch_clip()` orientation fallback and add News category terms**

Replace the `fetch_clip()` function body with:

```python
def fetch_clip(
    query: str,
    audio_duration: float,
    scene_idx: int,
    category: str = "",
    temp_dir: str = TEMP_DIR,
    orientation: str = "landscape",
) -> str:
    """Search Pexels for a video clip, download it, return local path.

    Falls back through: broad query -> category keyword -> generic -> empty string.
    When orientation="portrait", the whole fallback chain is retried once more with
    orientation="landscape" before giving up (a downstream crop step in video_service
    converts landscape footage to portrait). Empty string means the caller should
    treat this scene as failed.
    """
    ensure_dir(temp_dir)
    dest = os.path.join(temp_dir, f"pexels_{scene_idx}.mp4")

    words = query.split()
    broad = " ".join(words[:2]) if len(words) > 2 else None
    category_fallback = _CATEGORY_FALLBACKS.get((category or "").lower().strip())
    fallback_chain = [q for q in [query, broad, category_fallback, "knowledge learning education"] if q]

    orientations = [orientation]
    if orientation == "portrait":
        orientations.append("landscape")

    for attempt_orientation in orientations:
        for attempt_query in fallback_chain:
            try:
                videos = _search_pexels(attempt_query, orientation=attempt_orientation)
                if not videos:
                    continue
                clip = _select_clip(videos, audio_duration)
                if not clip:
                    continue
                url = _best_video_file(clip)
                if not url:
                    continue
                api_key = os.getenv("PEXELS_API_KEY", "")
                resp = requests.get(url, headers={"Authorization": api_key}, timeout=60, stream=True)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                logger.info(
                    "[Pexels] scene=%d query=%r orientation=%s duration=%s",
                    scene_idx, attempt_query, attempt_orientation, clip.get("duration"),
                )
                return dest
            except Exception as exc:
                logger.warning(
                    "[Pexels] scene=%d query=%r orientation=%s failed: %s",
                    scene_idx, attempt_query, attempt_orientation, exc,
                )

    logger.warning(
        "[Pexels] scene=%d all fallbacks exhausted (orientations=%s) — signalling failure",
        scene_idx, orientations,
    )
    return ""
```

Update `_CATEGORY_FALLBACKS` (near the top of the file) by adding the News domain keys:

```python
_CATEGORY_FALLBACKS = {
    "science & space": "science cosmos",
    "history & civilizations": "ancient history",
    "human body & biology": "human body medical",
    "technology & ai": "technology digital",
    "health & fitness": "health fitness",
    "psychology & dark psychology": "psychology mind",
    "relationships & dating": "people relationship",
    "self-improvement & habits": "motivation success",
    "business & finance": "business finance",
    "culture & society": "culture people",
    "philosophy & life": "philosophy nature",
    "mysteries & unexplained": "mystery dark",
    "artificial intelligence": "artificial intelligence technology",
    "technology": "technology digital devices",
    "current affairs": "news world affairs",
    "science": "science research laboratory",
    "health": "health medical",
    "business": "business finance office",
    "sports": "sports athletes stadium",
    "entertainment": "entertainment celebrity event",
    "environment": "environment nature climate",
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_pexels_service.py -v`
Expected: PASS (all existing + 4 new tests)

- [ ] **Step 6: Commit**

```bash
git add app/services/pexels_service.py tests/test_pexels_service.py
git commit -m "feat: add portrait-orientation fallback and News category terms to pexels_service"
```

---

### Task 2: llm_service — `generate_script_with_search()` news branch emits `visual_query`

**Files:**
- Modify: `app/services/llm_service.py:226-423` (`generate_script_with_search`)
- Test: `tests/test_llm_service.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: when `script_mode != "facts"` (the news/default branch), scenes now use `"visual_query"` instead of `"visual"` in the prompt schema. `script_mode == "facts"` output is byte-for-byte unchanged.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_llm_service.py`:

```python
def test_generate_script_with_search_news_mode_uses_visual_query():
    """Default (news) script_mode prompt asks for visual_query, not visual."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual_query":"city skyline sunrise"}]')
    with patch("app.services.llm_service._get_search_model", return_value=mock.return_value), \
         patch("app.services.llm_service._SEARCH_MODEL_CANDIDATES", ("gemini-2.5-flash",)):
        from app.services.llm_service import generate_script_with_search
        generate_script_with_search("AI update", language="en", aspect_ratio="9:16")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual_query"' in prompt_used
    assert "VISUAL_QUERY RULES" in prompt_used
    assert "Pexels" in prompt_used


def test_generate_script_with_search_facts_mode_still_uses_visual():
    """script_mode='facts' (Tell Me Why) keeps the Imagen-style visual field unchanged."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"storybook illustration"}]')
    with patch("app.services.llm_service._get_search_model", return_value=mock.return_value), \
         patch("app.services.llm_service._SEARCH_MODEL_CANDIDATES", ("gemini-2.5-flash",)):
        from app.services.llm_service import generate_script_with_search
        generate_script_with_search("bananas radioactive", script_mode="facts")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual"' in prompt_used
    assert "VISUAL PROMPT RULES" in prompt_used
    assert "Imagen" in prompt_used
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_service.py -v -k "visual_query or facts_mode_still_uses_visual"`
Expected: FAIL — current news-mode prompt has no `"visual_query"` or `"VISUAL_QUERY RULES"` text.

- [ ] **Step 3: Implement the branch in `generate_script_with_search()`**

In `app/services/llm_service.py`, locate the block starting at line 260 (`context_block = ...`) through line 361 (end of the `prompt = f"""..."""` string) and replace it with:

```python
    context_block = f"\nNEWS CONTEXT — primary source of truth. The script MUST cover ALL angles and facts below. Do not omit any element:\n{context.strip()}\n" if context and context.strip() else ""
    video_style = visual_style_override if visual_style_override else random.choice(_VISUAL_STYLE_POOL)

    if script_mode == "facts":
        system_instruction = (
            "You are a scriptwriter for 'Tell Me Why', a YouTube Shorts channel where a conversational friend "
            "shares wild, surprising facts — like texting someone something they won't believe. "
            "Use Google Search to verify the fact and find supporting details. "
            "Structure every script as exactly 4 scenes:\n"
            "Scene 1 — Hook: MUST begin with the exact words 'Tell me why' followed by a casual, "
            "slightly incredulous claim. The complete first sentence must be 12 words or fewer "
            "(including 'Tell me why'). Sound like someone dropping a wild fact in a group chat — "
            "direct, present-tense, a little disbelieving. Lead with the most counterintuitive angle.\n"
            "Scene 2 — Mechanism: Explain the actual 'why' like you're telling a friend — no textbook "
            "phrasing, no passive voice. Specific numbers and mechanisms delivered conversationally.\n"
            "Scene 3 — Deeper Implication: The 'wait, it gets weirder' beat. Escalate the surprise "
            "with a nuance or extension most people don't know.\n"
            "Scene 4 — Shareable Payoff: A quotable one-liner the viewer immediately wants to "
            "screenshot or share. Lands the punchline. Reframes how they see the world.\n"
            "Narration rules: conversational English, 20-24 words per scene, present tense, punchy "
            "sentences. BANNED: 'Scientists have discovered', passive constructions, news-anchor "
            "phrasing, academic hedging ('it has been noted that', 'research suggests').\n"
            "Visual prompt rules: describe absurd, literal interpretations of the fact — not abstract "
            "or symbolic imagery. If the fact says bacteria outnumber human cells, show tiny cartoon "
            "bacteria in a crowd waving flags. Every visual prompt MUST begin with the brand style "
            "prefix (provided in the user prompt) followed by ' — ' then the scene description."
        )
        date_instruction = (
            f"TODAY'S DATE is {today_str}. Verify facts via Google Search — "
            "prefer the most current, peer-reviewed information available."
        )
        visual_field = "visual"
        visual_schema_hint = "VERY DETAILED image generation prompt in English"
        visual_rules_block = f"""VISUAL PROMPT RULES:
- Always write visual prompts in English, regardless of narration language.
- Every visual prompt MUST begin with this exact style prefix to keep all scenes visually consistent: "{video_style} — ". Apply it to every scene without exception.
- Real people (politicians, celebrities, journalists, public figures) ARE allowed and encouraged — describe them by name and role for Imagen to render a realistic portrait (e.g. "photorealistic portrait of a scientist presenting findings in a lab").
- Do NOT request company logos, brand marks, app icons, or any readable text in the image — Imagen cannot render text or logos accurately. Use abstract or thematic imagery instead (e.g. instead of "Google logo", use "a colourful abstract search interface on a glowing screen").
- STRICT: Avoid text-bearing compositions like newspaper front pages, posters, billboards, screenshots, UI panels, signs, or subtitles.
- Be highly specific: lighting, composition, mood, style, camera angle.
- Avoid copyrighted fictional characters/franchises (e.g., superheroes, movie/cartoon characters, game mascots), trademarked logos, or branded products.
- CRITICAL — VISUAL SAFETY: Visual prompts must NEVER depict violence, weapons, blood, physical harm, or injury, even for news stories about such events. Use symbolic or abstract representations instead — for example: a broken chain for conflict, a gavel for law/justice, a city skyline for politics, a shield for protection, a first-aid cross for medical events. Imagen will reject prompts containing violent or harmful imagery.

Example visual prompt:
"Wide-angle cinematic shot of a modern data centre with rows of glowing blue server racks, cool blue-white lighting, shallow depth of field, photorealistic 3D render style\""""
    else:
        system_instruction = (
            "You are an expert scriptwriter for educational YouTube videos. Use your Google Search "
            "tool to look up the latest information about this headline, then write a factually "
            "accurate video script. The script must faithfully represent ALL angles in the headline "
            "and news context. Do NOT substitute outdated training-data knowledge when current "
            "search results are available."
        )
        date_instruction = (
            f"CRITICAL: TODAY'S DATE is {today_str}. The NEWS CONTEXT below (if present) describes a RECENT event that occurred close to this date. "
            "When searching, look for the most recent version of this story — do NOT write about older events with similar topic names. "
            "If the context says \"As of [date]\", that is the event date to focus on."
        )
        visual_field = "visual_query"
        visual_schema_hint = "3-7 word Pexels search phrase in English (see rules below)"
        visual_rules_block = """VISUAL_QUERY RULES:
- 3-7 plain English words for a Pexels stock video search.
- Describe a concrete, real-world scene tied to the story — not an abstract concept or a single-word topic.
- Good: "scientist adjusting microscope lab", "stock traders watching screens", "journalist interviewing official podium", "researcher presenting data conference"
- Bad: "science", "technology progress", "breaking news", "politics"
- This is a search phrase for real stock footage, not an image-generation prompt — no style, lighting, or camera-angle jargon needed, just the concrete subject and action.
- Avoid graphic violence/harm terms — stock footage libraries return low-quality or irrelevant results for those anyway; prefer neutral scene descriptions (e.g. "courtroom gavel" instead of describing an assault).
- Always in English regardless of narration language.

Example visual_query:
"engineer testing robotic arm factory\""""

    prompt = f"""
{system_instruction}

{date_instruction}

Topic: {topic}{context_block}

Return ONLY a valid JSON array. No markdown, no explanation, no code fences.

Each scene object must have:
- "scene": integer
- "narration": substantive narration text in the required language
- "{visual_field}": {visual_schema_hint}

NARRATION RULES — follow strictly:
- SCENE 1 HOOK (CRITICAL): The very first sentence of scene 1 must be 12 words or fewer. Open with a specific number, a named person doing something surprising, or a direct question. No scene-setting, no context-building — the viewer decides to stay or swipe in the first 2 seconds.
- Write in simple, reader-friendly language (clear and natural; avoid jargon unless necessary).
- Write COMPLETE information. Never tease or leave a fact unresolved.
- Every sentence must teach something specific: include real figures, dates, mechanisms, or consequences where relevant.
- Ensure the topic adds practical value for the viewer (what happened, why it matters, and key takeaway).
- Do NOT use filler phrases like "let's explore", "stay tuned", "it's a game-changer", or "this is just the beginning".
- Do NOT summarise without substance — each narration must stand alone as a useful insight.
- Cover ALL key angles from the headline and news context. If the headline mentions multiple story elements (e.g. a main event AND a secondary detail), every element must appear somewhere in the script — typically scene 1 (hook) introduces the main angle and scene 3–4 covers the secondary detail.
- {lang_instruction}

{visual_rules_block}

FACTUAL / COPYRIGHT SAFETY:
- TODAY'S DATE: {today_str}. Use this to determine verb tense. Events that occurred before today MUST be written in past tense ("launched", "announced", "was approved"). Do NOT write "will", "is expected to", "is set to", or "is scheduled to" for any event that has already taken place as of {today_str}. If uncertain whether an event has occurred, hedge with "reportedly" or "as of [date]" — never assume it is still upcoming.
- CRITICAL — YEARS AND DATES: When NEWS CONTEXT is provided above, ALL specific years and dates in the narration MUST come exclusively from that context or from your live search results for this specific article. Do NOT fall back on training-data knowledge to supply a year — your training data may describe an older planned or scheduled version of the event that has since changed. If neither the context nor search results mention a specific year for a detail, omit the year or write "recently" rather than guessing.
- Prefer facts retrieved via search. For any fact NOT confirmed by search results or the provided context, only include it if you are confident it occurred. Phrase uncertain claims as "reportedly", "according to reports", or "as of [year]".
- Do NOT fabricate specific dates, statistics, or event details. If uncertain, omit or hedge explicitly.
- Do not include direct quotes longer than 8 words from songs, books, movies, or articles.
- Do not include song lyrics.

Format:
[
  {{
    "scene": 1,
    "narration": "...",
    "{visual_field}": "..."
  }}
]

Additional format constraints:
{format_hint}
- Total spoken duration should be between 15 and 58 seconds (ideal 30-55 seconds).
- Maximum {max_scenes} scenes total
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_service.py tests/test_tmw_identity.py tests/test_pipeline.py -v`
Expected: PASS — new tests pass, and `test_tmw_identity.py`'s facts-mode assertions and `test_pipeline.py`'s model-fallback tests are unaffected.

- [ ] **Step 5: Commit**

```bash
git add app/services/llm_service.py tests/test_llm_service.py
git commit -m "feat: news script mode emits visual_query (Pexels search phrase) instead of Imagen visual prompt"
```

---

### Task 3: llm_service — `generate_script()` fallback gains `script_mode` and matching `visual_query` behavior

**Files:**
- Modify: `app/services/llm_service.py:92-176` (`generate_script`)
- Modify: `app/agents/generator_agent.py:375,385` (news fallback call sites)
- Test: `tests/test_llm_service.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `generate_script(topic, language="en", aspect_ratio="16:9", context="", script_mode="facts") -> str`. **Default is `"facts"`** — this preserves the exact current behavior (Imagen `"visual"` field) for every existing caller that doesn't pass `script_mode` (`app/routes/generate.py:95`, and the two facts-fallback call sites in `generator_agent.py`). Only `generator_agent.py`'s two news-fallback call sites are updated to explicitly pass `script_mode="news"`.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_llm_service.py`:

```python
def test_generate_script_default_mode_still_uses_visual():
    """Default script_mode (no arg passed) preserves existing Imagen-style behavior for
    every caller that doesn't know about the new mode (routes/generate.py, facts fallback)."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"img"}]')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_script
        generate_script("black holes", language="en")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual"' in prompt_used
    assert "VISUAL PROMPT RULES" in prompt_used


def test_generate_script_news_mode_uses_visual_query():
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual_query":"city skyline"}]')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_script
        generate_script("AI update", language="en", aspect_ratio="9:16", script_mode="news")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual_query"' in prompt_used
    assert "VISUAL_QUERY RULES" in prompt_used
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_service.py -v -k "generate_script_default_mode or generate_script_news_mode"`
Expected: FAIL — `generate_script()` has no `script_mode` parameter yet.

- [ ] **Step 3: Implement `script_mode` in `generate_script()`**

Replace the full function body (`app/services/llm_service.py:92-176`) with:

```python
def generate_script(topic: str, language: str = "en", aspect_ratio: str = "16:9", context: str = "", script_mode: str = "facts"):
    from datetime import date
    today_str = date.today().isoformat()
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])

    if aspect_ratio == "9:16":
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Scene 1: first sentence must be 12 words or fewer — a specific number, a named person doing something surprising, or a direct question. No scene-setting, no context-building. The viewer decides to stay or swipe in the first 2 seconds.\n"
            "- Scenes 2–4: each must reveal a specific, concrete insight, fact, number, or implication — no filler\n"
            "- Final scene: strong closing insight or call-to-reflection — not a generic sign-off\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"
    else:
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Each narration: 20–24 words (approx 9–11 seconds when spoken aloud)"
        )
        max_scenes = "5"

    context_block = f"\nNEWS CONTEXT — primary source of truth. The script MUST cover ALL angles and facts below. Do not omit any element:\n{context.strip()}\n" if context and context.strip() else ""
    video_style = random.choice(_VISUAL_STYLE_POOL)

    if script_mode == "news":
        visual_field = "visual_query"
        visual_schema_hint = "3-7 word Pexels search phrase in English (see rules below)"
        visual_rules_block = """VISUAL_QUERY RULES:
- 3-7 plain English words for a Pexels stock video search.
- Describe a concrete, real-world scene tied to the story — not an abstract concept or a single-word topic.
- Good: "scientist adjusting microscope lab", "stock traders watching screens", "journalist interviewing official podium", "researcher presenting data conference"
- Bad: "science", "technology progress", "breaking news", "politics"
- This is a search phrase for real stock footage, not an image-generation prompt — no style, lighting, or camera-angle jargon needed, just the concrete subject and action.
- Avoid graphic violence/harm terms — stock footage libraries return low-quality or irrelevant results for those anyway; prefer neutral scene descriptions (e.g. "courtroom gavel" instead of describing an assault).
- Always in English regardless of narration language.

Example visual_query:
"engineer testing robotic arm factory\""""
    else:
        visual_field = "visual"
        visual_schema_hint = "VERY DETAILED image generation prompt in English"
        visual_rules_block = f"""VISUAL PROMPT RULES:
- Always write visual prompts in English, regardless of narration language.
- Every visual prompt MUST begin with this exact style prefix to keep all scenes visually consistent: "{video_style} — ". Apply it to every scene without exception.
- Real people (politicians, celebrities, journalists, public figures) ARE allowed and encouraged — describe them by name and role for Imagen to render a realistic portrait (e.g. "photorealistic portrait of a scientist presenting findings in a lab").
- Do NOT request company logos, brand marks, app icons, or any readable text in the image — Imagen cannot render text or logos accurately. Use abstract or thematic imagery instead (e.g. instead of "Google logo", use "a colourful abstract search interface on a glowing screen").
- STRICT: Avoid text-bearing compositions like newspaper front pages, posters, billboards, screenshots, UI panels, signs, or subtitles.
- Be highly specific: lighting, composition, mood, style, camera angle.
- Avoid copyrighted fictional characters/franchises (e.g., superheroes, movie/cartoon characters, game mascots), trademarked logos, or branded products.
- CRITICAL — VISUAL SAFETY: Visual prompts must NEVER depict violence, weapons, blood, physical harm, or injury, even for news stories about such events. Use symbolic or abstract representations instead — for example: a broken chain for conflict, a gavel for law/justice, a city skyline for politics, a shield for protection, a first-aid cross for medical events. Imagen will reject prompts containing violent or harmful imagery.

Example visual prompt:
"Wide-angle cinematic shot of a modern data centre with rows of glowing blue server racks, cool blue-white lighting, shallow depth of field, photorealistic 3D render style\""""

    prompt = f"""
You are an expert scriptwriter for educational YouTube videos. Write a factually accurate video script on the headline below. The script must faithfully represent ALL angles in the headline and news context. Do NOT substitute your own interpretation of the topic or use general knowledge to override the provided context.

Topic: {topic}{context_block}

Return ONLY a valid JSON array. No markdown, no explanation, no code fences.

Each scene object must have:
- "scene": integer
- "narration": substantive narration text in the required language
- "{visual_field}": {visual_schema_hint}

NARRATION RULES — follow strictly:
- SCENE 1 HOOK (CRITICAL): The very first sentence of scene 1 must be 12 words or fewer. Open with a specific number, a named person doing something surprising, or a direct question. No scene-setting, no context-building — the viewer decides to stay or swipe in the first 2 seconds.
- Write in simple, reader-friendly language (clear and natural; avoid jargon unless necessary).
- Write COMPLETE information. Never tease or leave a fact unresolved.
- Every sentence must teach something specific: include real figures, dates, mechanisms, or consequences where relevant.
- Ensure the topic adds practical value for the viewer (what happened, why it matters, and key takeaway).
- Do NOT use filler phrases like "let's explore", "stay tuned", "it's a game-changer", or "this is just the beginning".
- Do NOT summarise without substance — each narration must stand alone as a useful insight.
- Cover ALL key angles from the headline and news context. If the headline mentions multiple story elements (e.g. a main event AND a secondary detail), every element must appear somewhere in the script — typically scene 1 (hook) introduces the main angle and scene 3–4 covers the secondary detail.
- {lang_instruction}

{visual_rules_block}

FACTUAL / COPYRIGHT SAFETY:
- TODAY'S DATE: {today_str}. Use this to determine verb tense. Events that occurred before today MUST be written in past tense ("launched", "announced", "was approved"). Do NOT write "will", "is expected to", "is set to", or "is scheduled to" for any event that has already taken place as of {today_str}. If uncertain whether an event has occurred, hedge with "reportedly" or "as of [date]" — never assume it is still upcoming.
- CRITICAL — YEARS AND DATES: When NEWS CONTEXT is provided above, ALL specific years and dates in the narration MUST come exclusively from that context. Do NOT use your training-data knowledge to supply a year or date that is not explicitly stated in the context. If the context does not mention a specific year for an event, do NOT guess or infer one — omit the year entirely or write "recently". This rule exists because your training data may describe an older planned version of the event (e.g. an earlier scheduled date) that has since changed. The provided context reflects the actual published date of the article and supersedes your prior knowledge.
- If context articles are provided above, prefer those facts. For any fact NOT in the provided context, only include it if you are certain it occurred and is not date-sensitive. Phrase uncertain claims as "reportedly", "according to reports", or "as of [year]".
- Do NOT fabricate specific dates, statistics, or event details. If uncertain, omit or hedge explicitly.
- Do not include direct quotes longer than 8 words from songs, books, movies, or articles.
- Do not include song lyrics.

Format:
[
  {{
    "scene": 1,
    "narration": "...",
    "{visual_field}": "..."
  }}
]

Additional format constraints:
{format_hint}
- Total spoken duration should be between 15 and 58 seconds (ideal 30-55 seconds).
- Maximum {max_scenes} scenes total
"""

    response = _get_model().generate_content(prompt)
    return _response_text(response)
```

- [ ] **Step 4: Update `generator_agent.py`'s news-fallback call sites**

In `app/agents/generator_agent.py`, line 375 and line 385, change:

```python
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")
```

(both occurrences, inside the `else:` "News" branch starting at line 368) to:

```python
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "", script_mode="news")
```

Leave the two facts-branch call sites (lines 358, 367, inside `elif script_type == "facts":`) unchanged — they rely on the new default (`script_mode="facts"`) to keep emitting `"visual"`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_llm_service.py tests/test_generator_agent_facts.py tests/test_routes.py -v`
Expected: PASS — new tests pass; `test_generator_agent_facts.py` and `test_routes.py` (which exercises `app/routes/generate.py`'s call to `generate_script` with no `script_mode`) are unaffected since the default preserves old behavior.

- [ ] **Step 6: Commit**

```bash
git add app/services/llm_service.py app/agents/generator_agent.py tests/test_llm_service.py
git commit -m "feat: generate_script() gains script_mode param, news fallback emits visual_query"
```

---

### Task 4: llm_service — `apply_quality_controls()` / `fact_check_scenes()` branch on `visual_query` vs `visual`

**Files:**
- Modify: `app/services/llm_service.py:501-569` (`fact_check_scenes`, `apply_quality_controls`)
- Test: `tests/test_llm_service.py`

**Interfaces:**
- Consumes: scenes list where each dict has either `"visual"` or `"visual_query"` (never both).
- Produces: `apply_quality_controls(...)` and `fact_check_scenes(...)` preserve whichever key was present on input — output scenes never gain or lose the key, and `sanitize_visual_prompt_no_text()` (the "no embedded text" Imagen sanitizer) only runs when the key is `"visual"`.

- [ ] **Step 1: Write failing test**

Add to `tests/test_llm_service.py`:

```python
def test_apply_quality_controls_preserves_visual_query_key():
    """News scenes (visual_query) must not be force-converted to visual, and must skip
    the Imagen-specific 'no embedded text' sanitizer."""
    from app.services import llm_service
    scenes = [
        {"scene": 1, "narration": "This is fucking wild", "visual_query": "newspaper headline text closeup"}
    ]
    with patch("app.services.llm_service.fact_check_scenes", return_value=scenes):
        cleaned = llm_service.apply_quality_controls("topic", scenes, language="en")
    assert "[censored]" in cleaned[0]["narration"]
    assert "visual_query" in cleaned[0]
    assert "visual" not in cleaned[0]
    # sanitize_visual_prompt_no_text is Imagen-specific and must not run on visual_query
    assert "no text" not in cleaned[0]["visual_query"].lower()


def test_fact_check_scenes_prompt_requests_visual_query_field_when_present():
    from app.services import llm_service
    mock = _make_model_mock('[{"scene":1,"narration":"n","visual_query":"q"}]')
    scenes = [{"scene": 1, "narration": "n", "visual_query": "city skyline"}]
    with patch("app.services.llm_service._get_model", mock):
        llm_service.fact_check_scenes("topic", scenes, language="en")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert "scene, narration, visual_query" in prompt_used
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_service.py -v -k "preserves_visual_query or fact_check_scenes_prompt_requests"`
Expected: FAIL — current code hardcodes `"visual"` in both functions.

- [ ] **Step 3: Implement the branch in `fact_check_scenes()`**

Replace the function body (`app/services/llm_service.py:501-545`) with:

```python
def fact_check_scenes(topic: str, scenes: list[dict], language: str = "en", context: str = "") -> list[dict]:
    """Run a fast fact-check + safety rewrite pass while preserving structure."""
    if not scenes:
        return scenes

    visual_key = "visual_query" if any("visual_query" in s for s in scenes) else "visual"

    from datetime import date
    today_str = date.today().isoformat()
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])
    context_block = (
        f"\nSOURCE CONTEXT (authoritative — all dates/years must match this):\n{context.strip()}\n"
        if context and context.strip()
        else ""
    )
    visual_check_rule = (
        "6) Keep visuals contain zero readable text and remove risky copyright/trademark references."
        if visual_key == "visual"
        else "6) Keep visual_query phrases in English and remove risky copyright/trademark references."
    )
    prompt = f"""
You are a strict fact-check and policy safety editor for short educational videos.

TODAY'S DATE: {today_str}{context_block}

Task:
1) Review each scene's narration for likely factual errors, overclaims, or missing caution.
2) Flag and correct any claim that presents a past event (more than a few weeks ago) as if it just happened or is "breaking news".
3) CRITICAL — YEAR HALLUCINATION CHECK: Identify every specific year mentioned in the narrations. For each year, verify it is explicitly present in the SOURCE CONTEXT above. If a year appears in the narration but NOT in the source context, it was fabricated from training-data knowledge — remove it or replace with "recently". Training data often contains outdated planned/scheduled dates for ongoing events (e.g. a mission planned for 2025 that actually launched in 2026); the source context is always authoritative.
4) Replace fabricated or unverifiable specific dates/numbers with hedged language ("reportedly", "as of [year]", "estimated"). Remove them entirely if they add no value.
5) Keep same number of scenes and same `scene` numbers.
{visual_check_rule}
7) Remove profanity and offensive wording.

Topic: {topic}
Language rule: {lang_instruction}

Return ONLY valid JSON array with objects: scene, narration, {visual_key}.

Input scenes:
{_scene_list_to_json_prompt(scenes)}
"""
    try:
        response = _get_model().generate_content(prompt)
        from app.utils.helpers import extract_json
        checked = extract_json(_response_text(response))
        if isinstance(checked, list) and len(checked) >= len(scenes):
            return checked
    except Exception:
        pass
    return scenes
```

- [ ] **Step 4: Implement the branch in `apply_quality_controls()`**

Replace the function body (`app/services/llm_service.py:548-569`) with:

```python
def apply_quality_controls(topic: str, scenes: list[dict], language: str = "en", context: str = "", skip_fact_check: bool = False) -> list[dict]:
    """Apply fact-check + profanity + copyright sanitization.

    skip_fact_check=True skips the news-oriented fact_check_scenes LLM pass — appropriate
    for story scripts where fiction shouldn't be treated as news claims.
    """
    reviewed = scenes if skip_fact_check else fact_check_scenes(topic, scenes, language=language, context=context)
    cleaned = []
    for s in reviewed:
        narration = strip_markdown_formatting(str(s.get("narration", "")))
        narration = sanitize_profanity(narration)
        narration = sanitize_copyright_risks(narration)
        if "visual_query" in s:
            visual_query = sanitize_copyright_risks(str(s.get("visual_query", "")))
            cleaned.append(
                {
                    "scene": s.get("scene"),
                    "narration": narration.strip(),
                    "visual_query": visual_query.strip(),
                }
            )
        else:
            visual = sanitize_copyright_risks(str(s.get("visual", "")))
            visual = sanitize_visual_prompt_no_text(visual)
            cleaned.append(
                {
                    "scene": s.get("scene"),
                    "narration": narration.strip(),
                    "visual": visual.strip(),
                }
            )
    return cleaned
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_llm_service.py -v`
Expected: PASS — new tests pass, existing `test_apply_quality_controls_sanitizes_profanity_and_copyright` (which uses `"visual"`) still passes unchanged.

- [ ] **Step 6: Commit**

```bash
git add app/services/llm_service.py tests/test_llm_service.py
git commit -m "feat: apply_quality_controls and fact_check_scenes preserve visual_query vs visual key"
```

---

### Task 5: senior_script_reviewer — preserve `visual_query` through review/tighten/expand passes

**Files:**
- Modify: `app/agents/senior_script_reviewer.py:37-54` (`_tighten_if_too_long`)
- Modify: `app/services/llm_service.py:1181-1220` (`review_script_with_senior_reviewer`)
- Test: `tests/test_senior_script_reviewer.py` (new file)

**Interfaces:**
- Consumes: scenes list with either `"visual"` or `"visual_query"`.
- Produces: `_tighten_if_too_long()` and `review_script_with_senior_reviewer()` preserve whichever key is present; the senior-reviewer LLM prompt explicitly instructs it not to rewrite a `visual_query` into a full descriptive sentence.

- [ ] **Step 1: Write failing tests**

Create `tests/test_senior_script_reviewer.py`:

```python
from unittest.mock import MagicMock, patch


def test_tighten_if_too_long_preserves_visual_query_key():
    from app.agents.senior_script_reviewer import _tighten_if_too_long
    scenes = [
        {"scene": 1, "narration": " ".join(["word"] * 60), "visual_query": "city skyline sunrise"},
    ]
    out = _tighten_if_too_long(scenes, max_seconds=5)
    assert "visual_query" in out[0]
    assert out[0]["visual_query"] == "city skyline sunrise"
    assert "visual" not in out[0]


def test_tighten_if_too_long_preserves_visual_key_for_stories():
    from app.agents.senior_script_reviewer import _tighten_if_too_long
    scenes = [
        {"scene": 1, "narration": " ".join(["word"] * 60), "visual": "storybook illustration"},
    ]
    out = _tighten_if_too_long(scenes, max_seconds=5)
    assert "visual" in out[0]
    assert out[0]["visual"] == "storybook illustration"
    assert "visual_query" not in out[0]


def test_review_script_with_senior_reviewer_prompt_uses_visual_query_field():
    from app.services import llm_service

    def _make_model_mock(return_text: str):
        mock = MagicMock()
        mock.return_value.generate_content.return_value.text = return_text
        return mock

    mock = _make_model_mock('[{"scene":1,"narration":"n","visual_query":"q"}]')
    scenes = [{"scene": 1, "narration": "n", "visual_query": "city skyline"}]
    with patch("app.services.llm_service._get_model", mock):
        llm_service.review_script_with_senior_reviewer("topic", scenes, language="en")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert "scene, narration, visual_query" in prompt_used
    assert "short 3-7 word Pexels search phrases" in prompt_used
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_senior_script_reviewer.py -v`
Expected: FAIL — `_tighten_if_too_long` hardcodes `"visual"`; `review_script_with_senior_reviewer`'s prompt hardcodes `"visual"` too.

- [ ] **Step 3: Implement the fix in `_tighten_if_too_long()`**

In `app/agents/senior_script_reviewer.py`, replace lines 37-54 with:

```python
def _tighten_if_too_long(scenes: list[dict], max_seconds: int) -> list[dict]:
    cur = _estimate_seconds(scenes)
    if cur <= max_seconds or not scenes:
        return scenes
    scale = max_seconds / max(cur, 1)
    out = []
    for s in scenes:
        narration = str(s.get("narration", ""))
        word_count = len(narration.split())
        keep = max(8, int(word_count * scale))
        visual_key = "visual_query" if "visual_query" in s else "visual"
        out.append(
            {
                "scene": s.get("scene"),
                "narration": _truncate_at_sentence(narration, keep),
                visual_key: s.get(visual_key, ""),
            }
        )
    return out
```

- [ ] **Step 4: Implement the fix in `review_script_with_senior_reviewer()`**

In `app/services/llm_service.py`, replace the function body (lines 1181-1220) with:

```python
def review_script_with_senior_reviewer(
    topic: str,
    scenes: list[dict],
    language: str = "en",
    min_seconds: int = 15,
    max_seconds: int = 58,
) -> list[dict]:
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])
    visual_key = "visual_query" if any("visual_query" in s for s in scenes) else "visual"
    visual_field_instruction = (
        "Visual prompts must remain in English."
        if visual_key == "visual"
        else "Visual_query values must remain in English — keep them as short 3-7 word Pexels search phrases, do NOT rewrite them into full descriptive sentences."
    )
    prompt = f"""
You are a senior script reviewer for short videos.
Review and rewrite the script to be:
- reader-friendly and easy to understand
- insightful and complete (no abrupt or incomplete ending)
- fact-conscious and practical
- engaging but not clickbait
- suitable for voiceover timing constraints

Rules:
- Keep output as a JSON array only.
- Keep each object fields: scene, narration, {visual_key}.
- {visual_field_instruction}
- Voiceover total duration must be between {min_seconds} and {max_seconds} seconds.
- Keep script natural for narration and captions to stay in sync.
- Avoid overly complex words.

Topic: {topic}
Language: {lang_instruction}

Input scenes:
{_scene_list_to_json_prompt(scenes)}
"""
    try:
        response = _get_model().generate_content(prompt)
        from app.utils.helpers import extract_json
        reviewed = extract_json(_response_text(response))
        if isinstance(reviewed, list) and len(reviewed) >= len(scenes):
            return reviewed
    except Exception:
        pass
    return scenes
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_senior_script_reviewer.py tests/test_llm_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/agents/senior_script_reviewer.py app/services/llm_service.py tests/test_senior_script_reviewer.py
git commit -m "feat: senior script reviewer preserves visual_query key without rewriting it"
```

---

### Task 6: generator_agent.py — wire the News channel to Pexels

**Files:**
- Modify: `app/agents/generator_agent.py`
- Modify: `.github/workflows/generate-video.yml`
- Test: `tests/test_generator_agent_news_pexels.py` (new file)

**Interfaces:**
- Consumes: `pexels_service.fetch_clip(query, audio_duration, scene_idx, category="", orientation="landscape") -> str` (Task 1).
- Produces: for `channel_id == "news"`, the scene loop calls `pexels_service.fetch_clip(..., orientation="portrait")` instead of `image_service.generate_image(...)`; raises when `fetch_clip` returns `""` so it's handled by the existing failure path. `channel_id == "stories"` behavior is completely unchanged.

- [ ] **Step 1: Write failing test**

Create `tests/test_generator_agent_news_pexels.py`:

```python
from unittest.mock import MagicMock, patch


def test_generator_agent_news_branch_calls_pexels_not_imagen():
    """channel_id='news' scenes must call pexels_service.fetch_clip, not image_service.generate_image."""
    import app.agents.generator_agent as ga

    def mock_generate_script_with_search(topic, language="en", aspect_ratio="9:16", context="", visual_style_override="", script_mode="news"):
        return '[{"scene": 1, "narration": "Breaking news twenty words filler filler filler filler filler filler.", "visual_query": "city skyline sunrise"}]'

    pexels_calls = []

    def mock_fetch_pexels_clip(query, audio_duration, scene_idx, category="", orientation="landscape"):
        pexels_calls.append({"query": query, "category": category, "orientation": orientation})
        return "/tmp/pexels_0.mp4"

    imagen_calls = []

    def mock_generate_image(*args, **kwargs):
        imagen_calls.append((args, kwargs))
        return "/tmp/img.png"

    with patch.object(ga, "generate_script_with_search", mock_generate_script_with_search), \
         patch.object(ga, "fetch_pexels_clip", mock_fetch_pexels_clip), \
         patch.object(ga, "generate_image", mock_generate_image), \
         patch.object(ga, "_audio_duration", return_value=10.0), \
         patch("app.agents.generator_agent.firestore_service.get_job", return_value={}), \
         patch("app.agents.generator_agent.firestore_service.create_or_update_job"), \
         patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True), \
         patch("app.agents.generator_agent.firestore_service.release_video_lock"), \
         patch("app.agents.generator_agent.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}), \
         patch("app.agents.generator_agent.firestore_service.set_pipeline_and_batch_state"), \
         patch("app.agents.generator_agent.firestore_service.mark_scene_checkpoint"), \
         patch("app.agents.generator_agent.firestore_service.record_quota_event"), \
         patch("app.agents.generator_agent.generate_audio"), \
         patch("app.agents.generator_agent.choose_voice_for_video", return_value="en-US-Neural2-C"), \
         patch("app.agents.generator_agent.create_video"), \
         patch("app.agents.generator_agent.send_message"), \
         patch("app.agents.generator_agent.review_package", return_value={"scenes": [{"scene": 1, "narration": "test narration here", "visual_query": "city skyline sunrise"}], "title": "Test News", "caption": "cap"}), \
         patch.object(ga, "apply_quality_controls", side_effect=lambda t, s, **kw: s), \
         patch.object(ga, "classify_music_genre", return_value="News Bulletin"), \
         patch.object(ga, "get_cta_narration", return_value="Subscribe now."), \
         patch("app.services.storage_service.upload_video", return_value="gs://bucket/vid.mp4"), \
         patch("app.agents.social_media_agent.post", return_value="https://youtu.be/abc"):
        ga.run(
            headline="AI update",
            code="NEWS01",
            batch_id="b1",
            job_id="job-news-001",
            public_id="ABCD1234",
            force_run=True,
            genre="Artificial Intelligence",
            details="",
            channel_id="news",
            script_type="news",
            language="en",
        )

    assert len(pexels_calls) == 1, f"Expected exactly 1 Pexels call, got {len(pexels_calls)}"
    assert pexels_calls[0]["query"] == "city skyline sunrise"
    assert pexels_calls[0]["category"] == "Artificial Intelligence"
    assert pexels_calls[0]["orientation"] == "portrait"
    assert len(imagen_calls) == 0, "generate_image must NOT be called for channel_id='news'"


def test_generator_agent_stories_branch_still_calls_imagen():
    """channel_id='stories' scenes must keep calling image_service.generate_image, unchanged."""
    import app.agents.generator_agent as ga

    def mock_generate_script_with_search(topic, language="en", aspect_ratio="9:16", context="", visual_style_override="", script_mode="news"):
        return '[{"scene": 1, "narration": "Fact narration here twenty words filler filler filler.", "visual": "storybook illustration style"}]'

    pexels_calls = []
    imagen_calls = []

    def mock_generate_image(*args, **kwargs):
        imagen_calls.append((args, kwargs))
        return "/tmp/img.png"

    def mock_fetch_pexels_clip(*args, **kwargs):
        pexels_calls.append((args, kwargs))
        return "/tmp/pexels_0.mp4"

    with patch.object(ga, "generate_script_with_search", mock_generate_script_with_search), \
         patch.object(ga, "fetch_pexels_clip", mock_fetch_pexels_clip), \
         patch.object(ga, "generate_image", mock_generate_image), \
         patch("app.agents.generator_agent.firestore_service.get_job", return_value={}), \
         patch("app.agents.generator_agent.firestore_service.create_or_update_job"), \
         patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True), \
         patch("app.agents.generator_agent.firestore_service.release_video_lock"), \
         patch("app.agents.generator_agent.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}), \
         patch("app.agents.generator_agent.firestore_service.set_pipeline_and_batch_state"), \
         patch("app.agents.generator_agent.firestore_service.mark_scene_checkpoint"), \
         patch("app.agents.generator_agent.firestore_service.record_quota_event"), \
         patch("app.agents.generator_agent.generate_audio"), \
         patch("app.agents.generator_agent.choose_voice_for_video", return_value="en-US-Neural2-C"), \
         patch("app.agents.generator_agent.create_video"), \
         patch("app.agents.generator_agent.send_message"), \
         patch("app.agents.generator_agent.review_package", return_value={"scenes": [{"scene": 1, "narration": "test", "visual": "storybook illustration style"}], "title": "Test Fact", "caption": "cap"}), \
         patch.object(ga, "apply_quality_controls", side_effect=lambda t, s, **kw: s), \
         patch.object(ga, "classify_music_genre", return_value="Cheerful"), \
         patch.object(ga, "get_cta_narration", return_value="Subscribe now."), \
         patch("app.services.storage_service.upload_video", return_value="gs://bucket/vid.mp4"), \
         patch("app.agents.social_media_agent.post", return_value="https://youtu.be/abc"):
        ga.run(
            headline="Why do cats always land on their feet?",
            code="FACT01",
            batch_id="b1",
            job_id="job-facts-002",
            public_id="ABCD5678",
            force_run=True,
            genre="science & space",
            details="",
            channel_id="stories",
            script_type="facts",
            language="en",
        )

    assert len(imagen_calls) == 1
    assert len(pexels_calls) == 0, "fetch_pexels_clip must NOT be called for channel_id='stories'"


def test_generator_agent_news_scene_fails_when_pexels_exhausted():
    """An empty string from fetch_pexels_clip must be treated as a scene failure, not a
    silent success with an empty asset path."""
    import app.agents.generator_agent as ga

    def mock_generate_script_with_search(topic, language="en", aspect_ratio="9:16", context="", visual_style_override="", script_mode="news"):
        return '[{"scene": 1, "narration": "Breaking news twenty words filler filler filler filler filler filler.", "visual_query": "extremely rare unmatched query"}]'

    with patch.object(ga, "generate_script_with_search", mock_generate_script_with_search), \
         patch.object(ga, "fetch_pexels_clip", return_value=""), \
         patch.object(ga, "_audio_duration", return_value=10.0), \
         patch("app.agents.generator_agent.time.sleep"), \
         patch("app.agents.generator_agent.firestore_service.get_job", return_value={}), \
         patch("app.agents.generator_agent.firestore_service.create_or_update_job"), \
         patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True), \
         patch("app.agents.generator_agent.firestore_service.release_video_lock"), \
         patch("app.agents.generator_agent.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}), \
         patch("app.agents.generator_agent.firestore_service.set_pipeline_and_batch_state"), \
         patch("app.agents.generator_agent.firestore_service.mark_scene_checkpoint") as mock_checkpoint, \
         patch("app.agents.generator_agent.firestore_service.record_quota_event"), \
         patch("app.agents.generator_agent.generate_audio"), \
         patch("app.agents.generator_agent.choose_voice_for_video", return_value="en-US-Neural2-C"), \
         patch("app.agents.generator_agent.create_video"), \
         patch("app.agents.generator_agent.send_message") as mock_send, \
         patch("app.agents.generator_agent.review_package", return_value={"scenes": [{"scene": 1, "narration": "test narration here", "visual_query": "extremely rare unmatched query"}], "title": "Test News", "caption": "cap"}), \
         patch.object(ga, "apply_quality_controls", side_effect=lambda t, s, **kw: s), \
         patch.object(ga, "classify_music_genre", return_value="News Bulletin"), \
         patch.object(ga, "get_cta_narration", return_value="Subscribe now."):
        ga.run(
            headline="AI update",
            code="NEWS02",
            batch_id="b1",
            job_id="job-news-003",
            public_id="ABCD9999",
            force_run=True,
            genre="Artificial Intelligence",
            details="",
            channel_id="news",
            script_type="news",
            language="en",
        )

    # Scene must be marked "failed", never "completed" with an empty image_path
    failed_calls = [c for c in mock_checkpoint.call_args_list if c.args[2] == "failed"]
    completed_calls = [c for c in mock_checkpoint.call_args_list if c.args[2] == "completed"]
    assert len(failed_calls) >= 1
    assert len(completed_calls) == 0
    # All scenes failed -> Telegram notified, video dropped
    assert mock_send.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_generator_agent_news_pexels.py -v`
Expected: FAIL — `generator_agent.py` has no `fetch_pexels_clip` or `_audio_duration` names to patch yet, and always calls `generate_image` regardless of `channel_id`.

- [ ] **Step 3: Add imports and the `_audio_duration` helper**

In `app/agents/generator_agent.py`, change the import block (lines 22-24) from:

```python
from app.services.tts_service import generate_audio, choose_voice_for_video
from app.services.image_service import generate_image
from app.services.video_service import create_video
```

to:

```python
from app.services.tts_service import generate_audio, choose_voice_for_video
from app.services.image_service import generate_image
from app.services.pexels_service import fetch_clip as fetch_pexels_clip
from app.services.video_service import create_video
```

Then add this helper function right after `_is_safety_filter_error` (after line 139, before `_run_with_backoff` at line 142):

```python
def _audio_duration(path: str) -> float:
    try:
        from moviepy import AudioFileClip
    except Exception:
        from moviepy.editor import AudioFileClip
    clip = AudioFileClip(path)
    duration = clip.duration
    clip.close()
    return duration


def _fetch_pexels_clip_or_raise(query: str, audio_duration: float, scene_idx: int, category: str = "") -> str:
    path = fetch_pexels_clip(query, audio_duration, scene_idx, category=category, orientation="portrait")
    if not path:
        raise RuntimeError(f"Pexels exhausted all fallback queries for scene {scene_idx}")
    return path
```

- [ ] **Step 4: Update the scene loop's `visual` read and image/clip fetch branch**

In `app/agents/generator_agent.py`, change line 426 from:

```python
            visual = scene.get("visual")
```

to:

```python
            visual = scene.get("visual_query") or scene.get("visual")
```

Then change lines 467-469 from:

```python
                image_path, image_retries = _run_with_backoff(
                    lambda v=visual, idx=i: generate_image(v, idx, aspect_ratio="9:16")
                )
```

to:

```python
                if channel_id == "news":
                    scene_audio_duration = _audio_duration(audio_path)
                    image_path, image_retries = _run_with_backoff(
                        lambda q=visual, idx=i, dur=scene_audio_duration: _fetch_pexels_clip_or_raise(
                            q, dur, idx, category=genre
                        )
                    )
                else:
                    image_path, image_retries = _run_with_backoff(
                        lambda v=visual, idx=i: generate_image(v, idx, aspect_ratio="9:16")
                    )
```

- [ ] **Step 5: Add `PEXELS_API_KEY` to the GitHub Actions workflow**

In `.github/workflows/generate-video.yml`, in the `Generate video` step's `env:` block, add a line alongside the other secrets (near `BUCKET_NAME`):

```yaml
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_generator_agent_news_pexels.py tests/test_generator_agent_facts.py tests/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/agents/generator_agent.py .github/workflows/generate-video.yml tests/test_generator_agent_news_pexels.py
git commit -m "feat: wire News channel Shorts scenes to Pexels instead of Imagen"
```

---

### Task 7: video_service.py — assemble Pexels video clips into Shorts

**Files:**
- Modify: `app/services/video_service.py`
- Test: `tests/test_video_service.py` (new file)

**Interfaces:**
- Consumes: `item["image_path"]` may now be a `.mp4` path (news) or `.png` path (stories, unchanged).
- Produces: `create_video()`'s behavior for `.png` scenes is unchanged; for `.mp4` scenes it loads via `VideoFileClip`, trims (if longer than the narration audio) or loops (if shorter) to match audio duration exactly, replaces the clip's own audio with the narration audio via `_clip_audio`, and skips the Ken-Burns/`create_animated_scene_clip` path entirely. `target_w`/`target_h` are forced to 1080x1920 for `channel_id in ("stories", "news")`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_video_service.py`:

```python
# tests/test_video_service.py
import os
import pytest
from unittest.mock import MagicMock, patch


def _make_mock_audio(duration: float = 10.0):
    m = MagicMock()
    m.duration = duration
    return m


def _make_mock_clip(w: int = 1080, h: int = 1920, duration: float = 10.0):
    m = MagicMock()
    m.w = w
    m.h = h
    m.duration = duration
    return m


@pytest.fixture
def mock_moviepy():
    mock_audio = _make_mock_audio(10.0)
    mock_clip = _make_mock_clip()
    mock_final = MagicMock()
    mock_final.duration = 10.0
    mock_final.audio = _make_mock_audio(10.0)

    passthrough = lambda clip, *a, **kw: clip
    concat_mock = MagicMock(return_value=mock_clip)

    patches = {
        "AudioFileClip": patch("app.services.video_service.AudioFileClip", return_value=mock_audio),
        "VideoFileClip": patch("app.services.video_service.VideoFileClip", return_value=mock_clip),
        "ImageClip": patch("app.services.video_service.ImageClip", return_value=mock_clip),
        "concatenate_videoclips": patch("app.services.video_service.concatenate_videoclips", concat_mock),
        "CompositeVideoClip": patch("app.services.video_service.CompositeVideoClip", return_value=mock_final),
        "CompositeAudioClip": patch("app.services.video_service.CompositeAudioClip", return_value=mock_final.audio),
        "_pick_music": patch("app.services.video_service._pick_music", return_value=None),
        "_make_word_caption_clips": patch("app.services.video_service._make_word_caption_clips", return_value=[]),
        "_clip_audio": patch("app.services.video_service._clip_audio", side_effect=passthrough),
        "_clip_duration": patch("app.services.video_service._clip_duration", side_effect=passthrough),
        "_audio_fade_in": patch("app.services.video_service._audio_fade_in", side_effect=passthrough),
        "_audio_fade_out": patch("app.services.video_service._audio_fade_out", side_effect=passthrough),
        "_volume": patch("app.services.video_service._volume", side_effect=passthrough),
        "_fit_cover": patch("app.services.video_service._fit_cover", side_effect=passthrough),
        "_subclip": patch("app.services.video_service._subclip", side_effect=passthrough),
        "create_animated_scene_clip": patch("app.services.video_service.create_animated_scene_clip", return_value=mock_clip),
        "resolve_motion_hint": patch("app.services.video_service.resolve_motion_hint", return_value={}),
    }
    started = {k: p.start() for k, p in patches.items()}
    started["concatenate_videoclips"] = concat_mock
    yield started
    for p in patches.values():
        p.stop()


def test_create_video_uses_video_file_clip_for_mp4_scene(tmp_path, mock_moviepy):
    """A .mp4 scene asset must load via VideoFileClip, not ImageClip, and must not
    invoke the Ken-Burns animation path."""
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/pexels_0.mp4", "audio_path": str(tmp_path / "a.mp3"), "narration": "Breaking news today."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="news")

    mock_moviepy["VideoFileClip"].assert_called_once_with("/tmp/pexels_0.mp4")
    mock_moviepy["ImageClip"].assert_not_called()
    mock_moviepy["create_animated_scene_clip"].assert_not_called()


def test_create_video_static_image_scene_still_uses_imageclip_for_stories(tmp_path, mock_moviepy):
    """A .png scene asset for channel_id='stories' keeps using ImageClip, unchanged."""
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/scene_0.png", "audio_path": str(tmp_path / "a.mp3"), "narration": "Once upon a time."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="stories")

    mock_moviepy["ImageClip"].assert_called_once_with("/tmp/scene_0.png")
    mock_moviepy["VideoFileClip"].assert_not_called()


def test_create_video_trims_video_clip_longer_than_audio(tmp_path, mock_moviepy):
    """A Pexels clip longer than the narration audio is trimmed via _subclip, not looped."""
    mock_moviepy["VideoFileClip"].return_value.duration = 25.0
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/pexels_0.mp4", "audio_path": str(tmp_path / "a.mp3"), "narration": "Breaking news today."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="news")

    mock_moviepy["_subclip"].assert_any_call(mock_moviepy["VideoFileClip"].return_value, 0, 10.0)
    # concatenate_videoclips is only called once here — for final scene
    # assembly. No per-scene loop concatenation since the clip is already
    # long enough (no shorter-than-audio branch taken).
    assert mock_moviepy["concatenate_videoclips"].call_count == 1


def test_create_video_loops_video_clip_shorter_than_audio(tmp_path, mock_moviepy):
    """A Pexels clip shorter than the narration audio is looped via concatenate_videoclips
    before being trimmed to the exact audio duration."""
    mock_moviepy["VideoFileClip"].return_value.duration = 4.0
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/pexels_0.mp4", "audio_path": str(tmp_path / "a.mp3"), "narration": "Breaking news today."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="news")

    # concatenate_videoclips is called twice: once to loop the short source
    # clip (first call), once for final scene assembly (second call).
    assert mock_moviepy["concatenate_videoclips"].call_count == 2
    loop_call = mock_moviepy["concatenate_videoclips"].call_args_list[0]
    looped_list = loop_call[0][0]
    assert len(looped_list) == 3  # int(10.0 / 4.0) + 1 == 3


def test_create_video_forces_portrait_target_for_news(tmp_path, mock_moviepy):
    """News video-clip scenes must be cropped to 1080x1920, same as stories."""
    from app.services.video_service import create_video
    clips = [
        {"image_path": "/tmp/pexels_0.mp4", "audio_path": str(tmp_path / "a.mp3"), "narration": "Breaking news today."},
    ]
    create_video(clips, str(tmp_path / "out.mp4"), channel_id="news")

    mock_moviepy["_fit_cover"].assert_any_call(mock_moviepy["VideoFileClip"].return_value, 1080, 1920)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_video_service.py -v`
Expected: FAIL — `video_service.py` has no `VideoFileClip` import, no `.mp4` branch, and `target_w`/`target_h` isn't forced for `channel_id="news"`.

- [ ] **Step 3: Add `VideoFileClip` import and `_is_video_asset` helper**

In `app/services/video_service.py`, replace lines 3-6:

```python
try:
    from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, CompositeAudioClip
except Exception:
    from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, CompositeAudioClip
```

with:

```python
try:
    from moviepy import ImageClip, AudioFileClip, VideoFileClip, concatenate_videoclips, CompositeVideoClip, CompositeAudioClip
except Exception:
    from moviepy.editor import ImageClip, AudioFileClip, VideoFileClip, concatenate_videoclips, CompositeVideoClip, CompositeAudioClip
```

Add this helper near `_fit_cover` (after line 393):

```python
def _is_video_asset(path: str) -> bool:
    return path.lower().endswith((".mp4", ".mov", ".webm"))
```

- [ ] **Step 4: Update `create_video()`'s target dimensions and per-scene branch**

Change line 456-457 from:

```python
    target_w = 1080 if channel_id == "stories" else 0
    target_h = 1920 if channel_id == "stories" else 0
```

to:

```python
    target_w = 1080 if channel_id in ("stories", "news") else 0
    target_h = 1920 if channel_id in ("stories", "news") else 0
```

Replace the per-scene loop body (lines 469-501, from `for idx, item in enumerate(normalized):` through `base = _fit_cover(base, target_w, target_h)`) with:

```python
    for idx, item in enumerate(normalized):
        image_path = item["image_path"]
        audio_path = item["audio_path"]
        narration = item["narration"]
        audio = AudioFileClip(audio_path)
        audio = _audio_fade_in(audio, AUDIO_FADE_IN)
        audio = _audio_fade_out(audio, AUDIO_FADE_OUT)

        use_animation = False
        if _is_video_asset(image_path):
            raw = VideoFileClip(image_path)
            if raw.duration >= audio.duration:
                base = _subclip(raw, 0, audio.duration)
            else:
                loops_needed = int(audio.duration / raw.duration) + 1
                looped = concatenate_videoclips([raw] * loops_needed)
                base = _subclip(looped, 0, audio.duration)
            base = _clip_audio(base, audio)
        else:
            use_animation = (
                channel_id == "stories"
                and STORIES_ANIMATION_ENABLED
                and idx < max(0, STORIES_MAX_SCENES_ANIMATED)
            )
            if use_animation:
                try:
                    hint = resolve_motion_hint(item, idx, genre=story_genre, profile=STORIES_ANIMATION_PROFILE)
                    base = create_animated_scene_clip(
                        image_path=image_path,
                        duration=audio.duration,
                        motion_hint=hint,
                        profile=STORIES_ANIMATION_PROFILE,
                    )
                    base = _clip_audio(base, audio)
                except Exception as anim_err:
                    print(f"⚠️ Animation failed for scene {idx}, falling back to static: {anim_err}")
                    base = _clip_audio(_clip_duration(ImageClip(image_path), audio.duration), audio)
            else:
                base = _clip_audio(_clip_duration(ImageClip(image_path), audio.duration), audio)

        # Enforce full-frame output so every scene fills Shorts frame edge-to-edge.
        if target_w <= 0 or target_h <= 0:
            target_w, target_h = int(base.w), int(base.h)
        base = _fit_cover(base, target_w, target_h)
```

The rest of the loop body (caption overlay, `video_clips.append(clip)`) is unchanged — it already references `use_animation`, which is now defined in both branches (`False` for video assets, computed as before for image assets).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_video_service.py -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite**

Run: `pytest tests/ -v`
Expected: PASS — all existing tests (including `test_long_video_service.py`, `test_pexels_service.py`, `test_llm_service.py`, `test_generator_agent_facts.py`) plus every test added in this plan.

- [ ] **Step 7: Commit**

```bash
git add app/services/video_service.py tests/test_video_service.py
git commit -m "feat: video_service assembles Pexels clips for news scenes with trim/loop + portrait crop"
```

---

### Task 8: Manual production verification

**Files:** none (no code changes — this is a real-world smoke test, cannot be automated)

- [ ] **Step 1: Trigger one real News job**

Once Task 1-7 are merged to `main`, trigger a single news video generation manually:

```bash
gh workflow run generate-video.yml -f payload='{"headline": "test pexels integration", "code": "TESTPX1", "channel_id": "news", "script_type": "news", "genre": "Technology", "force_run": true}'
```

- [ ] **Step 2: Watch the run and capture the result**

```bash
sleep 15
RUN_ID=$(gh run list --workflow=generate-video.yml --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RUN_ID"
gh run view "$RUN_ID" --log | grep -iE "pexels|scene.*failed|error"
```

Confirm: no `imagen`/`Imagen` references in the log (Imagen is no longer called for this job), no `403`/`not visible` errors, and the job either completes or fails with a clear Pexels-related reason (not a crash).

- [ ] **Step 3: Visually inspect the output**

If the job completed, check the Telegram notification or YouTube upload for the resulting Short:
- Portrait footage looks reasonable (no jarring crops or obviously mismatched stock footage).
- Subtitles are still readable and stay within the bottom-30% safe zone.
- Each scene's video and narration audio are in sync (no clip cutting off mid-sentence).
- Total video length is still in the ~45-55 second Shorts range.

- [ ] **Step 4: Report back**

Summarize what was observed (pass/fail, any visual quality issues) so a decision can be made about whether the fallback query chain or category terms need tuning before this runs on the regular schedule.
