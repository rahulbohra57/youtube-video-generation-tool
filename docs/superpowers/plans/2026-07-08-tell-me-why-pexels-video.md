# Tell Me Why Shorts Imagen→Pexels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch Tell Me Why (`channel_id="stories"`, `script_type="facts"`) Shorts scene generation from Imagen (currently 403ing — no Vertex entitlement) to Pexels stock video, mirroring the already-shipped News channel migration, without touching the legacy Hindi `story` pipeline or the unrelated `/generate` FastAPI route.

**Architecture:** `llm_service.py`'s two script-generation functions gain a `visual_query`-emitting branch for `script_mode="facts"` (alongside the existing `"news"` branch). `generator_agent.py`'s scene-fetch branch, currently gated on `channel_id == "news"`, is broadened to also cover `script_type == "facts"`. `pexels_service.py` and `video_service.py` need no changes — they were already made channel-agnostic during the News migration.

**Tech Stack:** Python, pytest, unittest.mock. No new dependencies.

## Global Constraints

- Legacy Hindi `script_type == "story"` pipeline (dead code, Cloud Tasks retired) must keep working exactly as before — do not change its behavior even incidentally.
- `app/routes/generate.py` (legacy `/generate` FastAPI route, not wired into Vercel, has its own tests in `tests/test_routes.py`) must keep working exactly as before.
- No new GCP credentials or GitHub Actions secrets — reuse existing `PEXELS_API_KEY`.
- Every task must leave `pytest tests/` fully green before moving to the next task.

---

### Task 1: `generate_script_with_search()` facts mode emits `visual_query`

**Files:**
- Modify: `app/services/llm_service.py:282-323` (the `if script_mode == "facts":` branch inside `generate_script_with_search`)
- Test: `tests/test_llm_service.py:166-176` (replace `test_generate_script_with_search_facts_mode_still_uses_visual`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `generate_script_with_search(topic, script_mode="facts")` now returns a prompt whose scene schema uses `"visual_query"` instead of `"visual"`. `generator_agent.py` (Task 3) relies on this.

- [ ] **Step 1: Replace the test to assert the new behavior**

Replace the existing test (currently asserting Imagen behavior) with:

```python
def test_generate_script_with_search_facts_mode_uses_visual_query():
    """script_mode='facts' (Tell Me Why) now emits a Pexels visual_query, not an Imagen prompt."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual_query":"tiny bacteria crowd microscope"}]')
    with patch("app.services.llm_service._get_search_model", return_value=mock.return_value), \
         patch("app.services.llm_service._SEARCH_MODEL_CANDIDATES", ("gemini-2.5-flash",)):
        from app.services.llm_service import generate_script_with_search
        generate_script_with_search("bananas radioactive", script_mode="facts")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual_query"' in prompt_used
    assert "VISUAL_QUERY RULES" in prompt_used
    assert "Pexels" in prompt_used
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_service.py::test_generate_script_with_search_facts_mode_uses_visual_query -v`
Expected: FAIL — prompt still contains `"visual"` / `VISUAL PROMPT RULES`, not `visual_query` / `Pexels`.

- [ ] **Step 3: Replace the facts-mode prompt block**

In `app/services/llm_service.py`, inside `generate_script_with_search`, replace the `if script_mode == "facts":` block's tail (the `visual_field = "visual"` / `visual_schema_hint` / `visual_rules_block` assignment, currently lines ~310-323) with:

```python
        visual_field = "visual_query"
        visual_schema_hint = "3-7 word Pexels search phrase in English (see rules below)"
        visual_rules_block = """VISUAL_QUERY RULES:
- 3-7 plain English words for a Pexels stock video search.
- Describe the fact's real-world subject concretely — not an illustrated or cartoon interpretation of it.
- Good: "bacteria colony microscope closeup", "octopus camouflage reef", "astronaut floating space station", "ancient ruins aerial drone"
- Bad: "bacteria", "surprising fact", "science", "mind blown"
- This is a search phrase for real stock footage, not an image-generation prompt — no style, illustration, or cartoon language needed, just the concrete subject and action.
- Always in English regardless of narration language.

Example visual_query:
"deep sea creature bioluminescent glow\""""
```

Leave `system_instruction` and `date_instruction` for the facts branch unchanged (still the "Tell me why" conversational voice — only the visual field changes).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_service.py::test_generate_script_with_search_facts_mode_uses_visual_query -v`
Expected: PASS

- [ ] **Step 5: Run the full llm_service test file to check for collateral breakage**

Run: `pytest tests/test_llm_service.py -v`
Expected: All PASS. (`test_generate_script_with_search_news_mode_uses_visual_query` and the news-mode tests must still pass unchanged.)

- [ ] **Step 6: Commit**

```bash
git add app/services/llm_service.py tests/test_llm_service.py
git commit -m "feat: generate_script_with_search facts mode emits visual_query for Pexels"
```

---

### Task 2: `generate_script()` fallback gains a facts→visual_query branch without breaking legacy callers

**Files:**
- Modify: `app/services/llm_service.py:92-145` (`generate_script` signature and its `if script_mode == "news":` / `else:` branch)
- Test: `tests/test_llm_service.py:181-199` (add new test; verify existing two still pass unmodified)

**Interfaces:**
- Consumes: nothing new.
- Produces: `generate_script(topic, script_mode="facts")` now returns `visual_query`-schema prompts. `generate_script(topic)` (no `script_mode` arg) is UNCHANGED — still returns the Imagen `visual` prompt, because the default value changes from `"facts"` to `"legacy"`. `generator_agent.py` (Task 3) must pass `script_mode="facts"` explicitly wherever it wants the new behavior.

- [ ] **Step 1: Write the new failing test**

Add to `tests/test_llm_service.py`, near `test_generate_script_news_mode_uses_visual_query`:

```python
def test_generate_script_facts_mode_uses_visual_query():
    """Explicit script_mode='facts' now uses the Pexels visual_query schema."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual_query":"tiny bacteria crowd"}]')
    with patch("app.services.llm_service._get_model", mock):
        from app.services.llm_service import generate_script
        generate_script("bananas radioactive", language="en", script_mode="facts")
    prompt_used = mock.return_value.generate_content.call_args[0][0]
    assert '"visual_query"' in prompt_used
    assert "VISUAL_QUERY RULES" in prompt_used
```

Do NOT modify `test_generate_script_default_mode_still_uses_visual` (tests/test_llm_service.py:181-190) — it calls `generate_script("black holes", language="en")` with no `script_mode` arg and must keep passing unchanged; that's what proves the legacy `/generate` route and the default value are unaffected.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_service.py::test_generate_script_facts_mode_uses_visual_query -v`
Expected: FAIL — `script_mode="facts"` currently falls into the `else` branch and returns an Imagen-style `visual` prompt.

- [ ] **Step 3: Change the default parameter and add the facts branch**

In `app/services/llm_service.py`, change the function signature (line 92):

```python
def generate_script(topic: str, language: str = "en", aspect_ratio: str = "16:9", context: str = "", script_mode: str = "legacy"):
```

Then change the branching (currently `if script_mode == "news": ... else: ...` around lines 116-144) to a three-way branch:

```python
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
    elif script_mode == "facts":
        visual_field = "visual_query"
        visual_schema_hint = "3-7 word Pexels search phrase in English (see rules below)"
        visual_rules_block = """VISUAL_QUERY RULES:
- 3-7 plain English words for a Pexels stock video search.
- Describe the fact's real-world subject concretely — not an illustrated or cartoon interpretation of it.
- Good: "bacteria colony microscope closeup", "octopus camouflage reef", "astronaut floating space station", "ancient ruins aerial drone"
- Bad: "bacteria", "surprising fact", "science", "mind blown"
- This is a search phrase for real stock footage, not an image-generation prompt — no style, illustration, or cartoon language needed, just the concrete subject and action.
- Always in English regardless of narration language.

Example visual_query:
"deep sea creature bioluminescent glow\""""
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
```

(The `else` branch body is copied verbatim from the current code — only the condition changes from implicit/bare-`else` to explicit fallthrough for `"legacy"` and any unrecognized value.)

- [ ] **Step 4: Run the new test and the two existing generate_script tests**

Run: `pytest tests/test_llm_service.py::test_generate_script_facts_mode_uses_visual_query tests/test_llm_service.py::test_generate_script_default_mode_still_uses_visual tests/test_llm_service.py::test_generate_script_news_mode_uses_visual_query -v`
Expected: All 3 PASS.

- [ ] **Step 5: Run the full test suite for collateral breakage**

Run: `pytest tests/ -q`
Expected: All PASS (this changes a public default parameter — check `tests/test_routes.py` and anywhere else `generate_script` is called with no `script_mode` still behaves the same).

- [ ] **Step 6: Commit**

```bash
git add app/services/llm_service.py tests/test_llm_service.py
git commit -m "feat: generate_script gains facts mode emitting visual_query, default renamed to legacy"
```

---

### Task 3: `generator_agent.py` routes Tell Me Why facts scenes to Pexels

**Files:**
- Modify: `app/agents/generator_agent.py:367-374` (facts search-grounded call — already passes `script_mode="facts"`, no change needed here, confirm only)
- Modify: `app/agents/generator_agent.py:377` and `:386` (facts fallback `generate_script(...)` calls — add explicit `script_mode="facts"`)
- Modify: `app/agents/generator_agent.py:486` (scene-fetch branch condition)
- Test: `tests/test_generator_agent_news_pexels.py` — replace `test_generator_agent_stories_branch_still_calls_imagen` (lines 69-125) with a Pexels-routing assertion; add a new legacy-story-still-uses-imagen test

**Interfaces:**
- Consumes: `generate_script_with_search`/`generate_script` from Tasks 1-2 (no signature change, only prompt content changed — this task doesn't need to know the prompt internals, only that `script_mode="facts"` scenes now come back with a `visual_query` key, which `generator_agent.py` already reads via `scene.get("visual_query") or scene.get("visual")` at line 445 — no change needed there).
- Produces: for `channel_id="stories"` + `script_type="facts"` jobs, scene visuals are fetched via `pexels_service.fetch_clip` instead of `image_service.generate_image`. For `channel_id="stories"` + `script_type="story"` (legacy Hindi), behavior is unchanged (still `generate_image`).

- [ ] **Step 1: Replace the outdated test with one asserting the new Pexels routing**

In `tests/test_generator_agent_news_pexels.py`, replace `test_generator_agent_stories_branch_still_calls_imagen` (lines 69-125) with:

```python
def test_generator_agent_stories_facts_branch_calls_pexels_not_imagen():
    """channel_id='stories' + script_type='facts' scenes must call pexels_service.fetch_clip, not image_service.generate_image."""
    import app.agents.generator_agent as ga

    def mock_generate_script_with_search(topic, language="en", aspect_ratio="9:16", context="", visual_style_override="", script_mode="news"):
        return '[{"scene": 1, "narration": "Fact narration here twenty words filler filler filler.", "visual_query": "bacteria colony microscope closeup"}]'

    pexels_calls = []
    imagen_calls = []

    def mock_fetch_pexels_clip(query, audio_duration, scene_idx, category="", orientation="landscape"):
        pexels_calls.append({"query": query, "category": category, "orientation": orientation})
        return "/tmp/pexels_0.mp4"

    def mock_generate_image(*args, **kwargs):
        imagen_calls.append((args, kwargs))
        return "/tmp/img.png"

    with ExitStack() as stack:
        stack.enter_context(patch.object(ga, "generate_script_with_search", mock_generate_script_with_search))
        stack.enter_context(patch.object(ga, "fetch_pexels_clip", mock_fetch_pexels_clip))
        stack.enter_context(patch.object(ga, "generate_image", mock_generate_image))
        stack.enter_context(patch.object(ga, "_audio_duration", return_value=10.0))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_job", return_value={}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.create_or_update_job"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.release_video_lock"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.set_pipeline_and_batch_state"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.mark_scene_checkpoint"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.record_quota_event"))
        stack.enter_context(patch("app.agents.generator_agent.generate_audio"))
        stack.enter_context(patch("app.agents.generator_agent.choose_voice_for_video", return_value="en-US-Neural2-C"))
        stack.enter_context(patch("app.agents.generator_agent.create_video"))
        stack.enter_context(patch("app.agents.generator_agent.send_message"))
        stack.enter_context(patch("app.agents.generator_agent.review_package", return_value={"scenes": [{"scene": 1, "narration": "test", "visual_query": "bacteria colony microscope closeup"}], "title": "Test Fact", "caption": "cap"}))
        stack.enter_context(patch.object(ga, "apply_quality_controls", side_effect=lambda t, s, **kw: s))
        stack.enter_context(patch.object(ga, "classify_music_genre", return_value="Cheerful"))
        stack.enter_context(patch.object(ga, "get_cta_narration", return_value="Subscribe now."))
        stack.enter_context(patch("app.services.storage_service.upload_video", return_value="gs://bucket/vid.mp4"))
        stack.enter_context(patch("app.agents.social_media_agent.post", return_value="https://youtu.be/abc"))

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

    assert len(pexels_calls) == 1, f"Expected exactly 1 Pexels call, got {len(pexels_calls)}"
    assert pexels_calls[0]["query"] == "bacteria colony microscope closeup"
    assert pexels_calls[0]["category"] == "science & space"
    assert pexels_calls[0]["orientation"] == "portrait"
    assert len(imagen_calls) == 0, "generate_image must NOT be called for channel_id='stories' script_type='facts'"


def test_generator_agent_legacy_story_branch_still_calls_imagen():
    """channel_id='stories' + script_type='story' (legacy Hindi pipeline) must keep calling image_service.generate_image, unchanged."""
    import app.agents.generator_agent as ga

    pexels_calls = []
    imagen_calls = []

    def mock_generate_image(*args, **kwargs):
        imagen_calls.append((args, kwargs))
        return "/tmp/img.png"

    def mock_fetch_pexels_clip(*args, **kwargs):
        pexels_calls.append((args, kwargs))
        return "/tmp/pexels_0.mp4"

    def mock_generate_story_script(headline, mood="inspiring", premise="", language="hi"):
        return '[{"scene": 1, "narration": "कहानी यहां है", "visual": "storybook illustration style"}]'

    with ExitStack() as stack:
        stack.enter_context(patch.object(ga, "generate_story_script", mock_generate_story_script))
        stack.enter_context(patch.object(ga, "fetch_pexels_clip", mock_fetch_pexels_clip))
        stack.enter_context(patch.object(ga, "generate_image", mock_generate_image))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_job", return_value={}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.create_or_update_job"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.acquire_video_lock", return_value=True))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.release_video_lock"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.set_pipeline_and_batch_state"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.mark_scene_checkpoint"))
        stack.enter_context(patch("app.agents.generator_agent.firestore_service.record_quota_event"))
        stack.enter_context(patch("app.agents.generator_agent.generate_audio"))
        stack.enter_context(patch("app.agents.generator_agent.choose_voice_for_video", return_value="hi-IN-Neural2-A"))
        stack.enter_context(patch("app.agents.generator_agent.create_video"))
        stack.enter_context(patch("app.agents.generator_agent.send_message"))
        stack.enter_context(patch("app.agents.generator_agent.review_package", return_value={"scenes": [{"scene": 1, "narration": "test", "visual": "storybook illustration style"}], "title": "Test Story", "caption": "cap"}))
        stack.enter_context(patch.object(ga, "apply_quality_controls", side_effect=lambda t, s, **kw: s))
        stack.enter_context(patch.object(ga, "classify_music_genre", return_value="Cheerful"))
        stack.enter_context(patch.object(ga, "get_cta_narration", return_value="Subscribe now."))
        stack.enter_context(patch("app.services.storage_service.upload_video", return_value="gs://bucket/vid.mp4"))
        stack.enter_context(patch("app.agents.social_media_agent.post", return_value="https://youtu.be/abc"))

        ga.run(
            headline="Test story headline",
            code="STORY01",
            batch_id="b1",
            job_id="job-story-003",
            public_id="ABCD1111",
            force_run=True,
            genre="inspiring",
            details="",
            channel_id="stories",
            script_type="story",
            language="hi",
        )

    assert len(imagen_calls) == 1
    assert len(pexels_calls) == 0, "fetch_pexels_clip must NOT be called for legacy script_type='story'"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_generator_agent_news_pexels.py -v`
Expected: `test_generator_agent_stories_facts_branch_calls_pexels_not_imagen` FAILS (currently routes to `generate_image`); `test_generator_agent_legacy_story_branch_still_calls_imagen` PASSES already (no code change yet); `test_generator_agent_news_branch_calls_pexels_not_imagen` and `test_generator_agent_news_scene_fails_when_pexels_exhausted` still PASS.

- [ ] **Step 3: Add explicit `script_mode="facts"` to the two fallback `generate_script` calls**

In `app/agents/generator_agent.py`, inside the `elif script_type == "facts":` block, change both fallback calls (currently `raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "")` at lines 377 and 386) to:

```python
                raw_script = generate_script(headline, language="en", aspect_ratio="9:16", context=details or "", script_mode="facts")
```

(Both occurrences — the `SearchGroundingUnavailable` except block and the generic `Exception` except block.)

- [ ] **Step 4: Broaden the scene-fetch branch condition**

In `app/agents/generator_agent.py`, change line 486 from:

```python
                if channel_id == "news":
```

to:

```python
                if channel_id == "news" or script_type == "facts":
```

- [ ] **Step 5: Run the generator_agent Pexels test file**

Run: `pytest tests/test_generator_agent_news_pexels.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Run the full test suite**

Run: `pytest tests/ -q`
Expected: All PASS. In particular check `tests/test_pipeline.py` for any test that runs `script_type="facts"` end-to-end and asserts `generate_image` is called — update any such assertion if it exists (search first: `grep -n "script_type=\"facts\"\|script_type='facts'" tests/test_pipeline.py`).

- [ ] **Step 7: Commit**

```bash
git add app/agents/generator_agent.py tests/test_generator_agent_news_pexels.py
git commit -m "feat: wire Tell Me Why facts Shorts scenes to Pexels instead of Imagen"
```

---

### Task 4: Manual verification note (no code)

**Files:** none — this is a verification checklist, not a code task.

- [ ] **Step 1: Confirm scope did not leak**

Run: `grep -n "channel_id == \"news\" or script_type == \"facts\"" app/agents/generator_agent.py`
Expected: exactly one match, at the scene-fetch branch.

Run: `grep -n 'script_mode: str = "legacy"' app/services/llm_service.py`
Expected: exactly one match, the `generate_script` signature.

- [ ] **Step 2: Full regression run**

Run: `pytest tests/ -q`
Expected: all tests pass, no skips introduced.

- [ ] **Step 3: Note for the user (not a commit)**

After merging, trigger one Tell Me Why `CREATE <topic>` job manually via the Telegram bot and visually confirm the resulting Short: stock footage reasonably matches the fact, subtitles are readable, audio/video sync is correct per scene, and total length is still in the ~45-55s Shorts range. This cannot be unit-tested (external Pexels API + real GCS/YouTube upload).
