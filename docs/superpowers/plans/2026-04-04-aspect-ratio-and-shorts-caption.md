# Aspect Ratio Toggle + YouTube Shorts Caption Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 16:9 / 9:16 aspect ratio selection to video generation and a separate on-demand YouTube Shorts caption generator in the results section.

**Architecture:** `aspect_ratio` flows as a query param from the frontend through the `/generate` route into both `generate_script()` (adjusts scene count/tone) and `generate_image()` (adjusts Imagen 3 ratio + style hint). The caption agent is a separate `POST /generate-caption` endpoint backed by a new `generate_shorts_caption()` LLM function; the frontend triggers it via a button in the results section that reveals an inline card with a Copy button.

**Tech Stack:** FastAPI, Vertex AI (Gemini 2.5 Flash, Imagen 3), MoviePy, pytest + httpx (tests)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `app/services/llm_service.py` | Modify | Add `aspect_ratio` param to `generate_script()`; add new `generate_shorts_caption()` |
| `app/services/image_service.py` | Modify | Add `aspect_ratio` param to `generate_image()`; update style hint and Imagen 3 call |
| `app/routes/generate.py` | Modify | Add `aspect_ratio` param to `/generate`; add new `/generate-caption` endpoint |
| `app/static/index.html` | Modify | Ratio toggle UI + JS; caption button + card UI + JS |
| `tests/test_llm_service.py` | Create | Unit tests for `generate_script()` and `generate_shorts_caption()` |
| `tests/test_routes.py` | Create | Route-level tests for `/generate` and `/generate-caption` |

---

## Task 1: Test Infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Install test dependencies**

```bash
pip install pytest httpx pytest-mock
```

Expected: packages install without error.

- [ ] **Step 2: Create `tests/__init__.py`**

```python
```
(Empty file — marks `tests/` as a package.)

- [ ] **Step 3: Create `tests/conftest.py`**

```python
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def mock_llm_model():
    """Patches the Gemini model in llm_service so no real API calls are made."""
    with patch("app.services.llm_service.model") as mock:
        yield mock


@pytest.fixture
def mock_image_model():
    """Patches the Imagen model in image_service so no real API calls are made."""
    with patch("app.services.image_service.model") as mock:
        yield mock


@pytest.fixture
def client():
    """FastAPI TestClient with all external services mocked."""
    with patch("app.services.llm_service.model"), \
         patch("app.services.image_service.model"), \
         patch("app.services.tts_service.texttospeech"), \
         patch("app.services.video_service.concatenate_videoclips"):
        from app.main import app
        yield TestClient(app)
```

- [ ] **Step 4: Verify pytest discovers tests**

```bash
cd "/Users/chetan/Desktop/Data Science/youtube-video-generation-tool"
pytest tests/ --collect-only
```

Expected: `no tests ran` (no test files yet), no import errors.

- [ ] **Step 5: Commit**

```bash
git add tests/__init__.py tests/conftest.py
git commit -m "test: add pytest infrastructure with fixtures"
```

---

## Task 2: Update `generate_script()` with `aspect_ratio`

**Files:**
- Modify: `app/services/llm_service.py`
- Create: `tests/test_llm_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_llm_service.py`:

```python
from unittest.mock import MagicMock, patch


def _make_model_mock(return_text: str):
    mock = MagicMock()
    mock.generate_content.return_value.text = return_text
    return mock


def test_generate_script_default_is_16x9():
    """With no aspect_ratio arg, prompt allows max 5 scenes."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"img"}]')
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_script
        generate_script("black holes", language="en")
    prompt_used = mock.generate_content.call_args[0][0]
    assert "5 scenes" in prompt_used
    assert "3 scenes" not in prompt_used
    assert "Shorts" not in prompt_used


def test_generate_script_shorts_mode():
    """With aspect_ratio='9:16', prompt instructs 3 scenes and short narrations."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"img"}]')
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_script
        generate_script("black holes", language="en", aspect_ratio="9:16")
    prompt_used = mock.generate_content.call_args[0][0]
    assert "3 scenes" in prompt_used
    assert "15 seconds" in prompt_used


def test_generate_script_invalid_ratio_falls_back():
    """An unknown aspect_ratio value is treated as 16:9."""
    mock = _make_model_mock('[{"scene":1,"narration":"hi","visual":"img"}]')
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_script
        generate_script("black holes", language="en", aspect_ratio="4:3")
    prompt_used = mock.generate_content.call_args[0][0]
    assert "5 scenes" in prompt_used
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_llm_service.py -v
```

Expected: `ImportError` or `TypeError` — `generate_script` does not accept `aspect_ratio` yet.

- [ ] **Step 3: Update `generate_script()` in `app/services/llm_service.py`**

Replace the entire `generate_script` function:

```python
def generate_script(topic: str, language: str = "en", aspect_ratio: str = "16:9"):
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])

    if aspect_ratio == "9:16":
        format_hint = (
            "- Maximum 3 scenes\n"
            "- Keep each narration very short and punchy (≤15 seconds when spoken aloud)\n"
            "- Hook-first structure: open with the single most interesting fact or question"
        )
        max_scenes = "3"
    else:
        format_hint = ""
        max_scenes = "5"

    prompt = f"""
Generate a YouTube video script on: {topic}

Return ONLY JSON.

Each scene must include:
- narration (short, {lang_instruction})
- visual (VERY DETAILED image prompt, always in English regardless of language)

Example visual:
"futuristic AI robot in a research lab with glowing neural network screens, cinematic lighting"

Format:
[
  {{
    "scene": 1,
    "narration": "...",
    "visual": "detailed image prompt in English"
  }}
]

Ensure:
- Maximum {max_scenes} scenes
- Visual prompts are always in English (for image generation)
- Narration follows: {lang_instruction}
{format_hint}
"""

    response = model.generate_content(prompt)
    return response.text
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_llm_service.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/llm_service.py tests/test_llm_service.py
git commit -m "feat: add aspect_ratio param to generate_script"
```

---

## Task 3: Update `generate_image()` with `aspect_ratio`

**Files:**
- Modify: `app/services/image_service.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_llm_service.py` (append at bottom — reusing same file for all service tests):

```python
# ── image_service tests ──────────────────────────────────────────────────────

def test_generate_image_uses_16x9_by_default():
    """Default call uses 16:9 aspect ratio and landscape style hint."""
    mock_model = MagicMock()
    mock_img = MagicMock()
    mock_model.generate_images.return_value = [mock_img]
    mock_img.save = MagicMock()

    with patch("app.services.image_service.model", mock_model), \
         patch("app.services.image_service.ensure_dir"):
        from app.services.image_service import generate_image
        generate_image("a robot", 0)

    call_kwargs = mock_model.generate_images.call_args[1]
    assert call_kwargs["aspect_ratio"] == "16:9"
    assert "16:9" in call_kwargs["prompt"] or "cinematic" in call_kwargs["prompt"]


def test_generate_image_uses_9x16_ratio():
    """9:16 aspect_ratio is passed to Imagen and portrait hint appears in prompt."""
    mock_model = MagicMock()
    mock_img = MagicMock()
    mock_model.generate_images.return_value = [mock_img]
    mock_img.save = MagicMock()

    with patch("app.services.image_service.model", mock_model), \
         patch("app.services.image_service.ensure_dir"):
        from app.services.image_service import generate_image
        generate_image("a robot", 0, aspect_ratio="9:16")

    call_kwargs = mock_model.generate_images.call_args[1]
    assert call_kwargs["aspect_ratio"] == "9:16"
    assert "portrait" in call_kwargs["prompt"] or "Shorts" in call_kwargs["prompt"]
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_llm_service.py::test_generate_image_uses_16x9_by_default tests/test_llm_service.py::test_generate_image_uses_9x16_ratio -v
```

Expected: `TypeError` — `generate_image` does not accept `aspect_ratio` yet.

- [ ] **Step 3: Update `generate_image()` in `app/services/image_service.py`**

Replace the `generate_image` function:

```python
def generate_image(prompt: str, idx: int, aspect_ratio: str = "16:9"):

    GLOBAL_STYLE = """
    animated explainer video, flat design,
    consistent color palette, modern UI style,
    clean vector illustration
    """

    if aspect_ratio == "9:16":
        style_hint = (
            "vertical short-form video, portrait orientation, "
            "YouTube Shorts style, high quality"
        )
    else:
        style_hint = (
            "youtube educational thumbnail style, "
            "high quality, cinematic lighting, 16:9"
        )

    enhanced_prompt = f"""
    {prompt}, {GLOBAL_STYLE}
    style: animated explainer video,
    flat design, consistent color palette,
    {style_hint}
    """

    for attempt, wait in enumerate(_RETRY_DELAYS, start=1):
        try:
            images = model.generate_images(
                prompt=enhanced_prompt,
                number_of_images=1,
                aspect_ratio=aspect_ratio,
            )

            path = f"{TEMP_DIR}/scene_{idx}.png"
            images[0].save(location=path)
            return path

        except Exception as e:
            err = str(e)
            is_rate_limit = "429" in err or "quota" in err.lower() or "resource exhausted" in err.lower()

            if is_rate_limit:
                print(f"Retry {attempt} failed (rate limit – waiting {wait}s): {e}")
                time.sleep(wait)
            else:
                print(f"Retry {attempt} failed: {e}")
                time.sleep(5)

    raise Exception(f"Image generation failed after {len(_RETRY_DELAYS)} retries using {MODEL_NAME}")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_llm_service.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/image_service.py tests/test_llm_service.py
git commit -m "feat: add aspect_ratio param to generate_image"
```

---

## Task 4: Update `/generate` Endpoint with `aspect_ratio`

**Files:**
- Modify: `app/routes/generate.py`
- Create: `tests/test_routes.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_routes.py`:

```python
from unittest.mock import MagicMock, patch, call
import json


def _mock_services(aspect_ratio="16:9"):
    """Returns a dict of patches needed to exercise /generate without real APIs."""
    mock_script = MagicMock(return_value='[{"scene":1,"narration":"Test narration","visual":"Test visual"}]')
    mock_audio = MagicMock()
    mock_image = MagicMock(return_value="tmp/scene_0.png")
    mock_video = MagicMock(return_value="tmp/final.mp4")
    return mock_script, mock_audio, mock_image, mock_video


def test_generate_passes_aspect_ratio_to_services():
    """aspect_ratio query param is forwarded to generate_script and generate_image."""
    mock_script = MagicMock(return_value='[{"scene":1,"narration":"hi","visual":"img"}]')
    mock_audio = MagicMock()
    mock_image = MagicMock(return_value="tmp/scene_0.png")
    mock_video = MagicMock(return_value="tmp/final.mp4")

    with patch("app.routes.generate.generate_script", mock_script), \
         patch("app.routes.generate.generate_audio", mock_audio), \
         patch("app.routes.generate.generate_image", mock_image), \
         patch("app.routes.generate.create_video", mock_video), \
         patch("app.routes.generate.ensure_dir"):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.post("/generate?topic=black+holes&aspect_ratio=9:16")

    assert resp.status_code == 200
    mock_script.assert_called_once_with("black holes", language="en", aspect_ratio="9:16")
    mock_image.assert_called_once_with("img", 0, aspect_ratio="9:16")


def test_generate_defaults_to_16x9():
    """When aspect_ratio is omitted, 16:9 is used."""
    mock_script = MagicMock(return_value='[{"scene":1,"narration":"hi","visual":"img"}]')
    mock_audio = MagicMock()
    mock_image = MagicMock(return_value="tmp/scene_0.png")
    mock_video = MagicMock(return_value="tmp/final.mp4")

    with patch("app.routes.generate.generate_script", mock_script), \
         patch("app.routes.generate.generate_audio", mock_audio), \
         patch("app.routes.generate.generate_image", mock_image), \
         patch("app.routes.generate.create_video", mock_video), \
         patch("app.routes.generate.ensure_dir"):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        client.post("/generate?topic=black+holes")

    mock_script.assert_called_once_with("black holes", language="en", aspect_ratio="16:9")


def test_generate_invalid_aspect_ratio_falls_back_to_16x9():
    """An unrecognised aspect_ratio value is silently corrected to 16:9."""
    mock_script = MagicMock(return_value='[{"scene":1,"narration":"hi","visual":"img"}]')
    mock_audio = MagicMock()
    mock_image = MagicMock(return_value="tmp/scene_0.png")
    mock_video = MagicMock(return_value="tmp/final.mp4")

    with patch("app.routes.generate.generate_script", mock_script), \
         patch("app.routes.generate.generate_audio", mock_audio), \
         patch("app.routes.generate.generate_image", mock_image), \
         patch("app.routes.generate.create_video", mock_video), \
         patch("app.routes.generate.ensure_dir"):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        client.post("/generate?topic=black+holes&aspect_ratio=4:3")

    mock_script.assert_called_once_with("black holes", language="en", aspect_ratio="16:9")
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_routes.py -v
```

Expected: `TypeError` — `/generate` does not accept `aspect_ratio` yet.

- [ ] **Step 3: Update `/generate` in `app/routes/generate.py`**

Change the function signature and first lines of the try block:

```python
@router.post("/generate")
def generate_video(topic: str, language: str = "en", aspect_ratio: str = "16:9"):

    if not topic or topic.strip() == "":
        raise HTTPException(status_code=400, detail="Topic is required")

    if language not in ("en", "hi"):
        language = "en"

    if aspect_ratio not in ("16:9", "9:16"):
        aspect_ratio = "16:9"

    try:
        ensure_dir(TEMP_DIR)

        # 1. Generate script
        raw_script = generate_script(topic, language=language, aspect_ratio=aspect_ratio)

        print("\n================ RAW LLM OUTPUT ================\n")
        print(raw_script)
        print("\n================================================\n")

        # 2. Parse JSON
        try:
            scenes = extract_json(raw_script)
        except Exception as e:
            print("⚠️ JSON parsing failed. Using fallback...")
            scenes = [
                {
                    "scene": 1,
                    "narration": raw_script[:150],
                    "visual": "AI related concept illustration"
                },
                {
                    "scene": 2,
                    "narration": raw_script[150:300] if len(raw_script) > 150 else raw_script,
                    "visual": "technology and future visuals"
                }
            ]

        if not isinstance(scenes, list) or len(scenes) == 0:
            raise ValueError("No valid scenes generated")

        video_clips = []

        # 3. Process scenes
        for i, scene in enumerate(scenes):

            narration = scene.get("narration")
            visual = scene.get("visual")

            if not narration or not visual:
                print(f"⚠️ Skipping invalid scene: {scene}")
                continue

            try:
                audio_path = os.path.join(TEMP_DIR, f"audio_{i}.mp3")
                generate_audio(narration, audio_path, language=language)

                image_path = generate_image(visual, i, aspect_ratio=aspect_ratio)

                video_clips.append((image_path, audio_path))

                time.sleep(2)

            except Exception as scene_error:
                print(f"⚠️ Scene {i} failed:", scene_error)
                continue

        if len(video_clips) == 0:
            raise ValueError("No video clips could be generated")

        # 4. Create final video
        output_path = os.path.join(TEMP_DIR, "final.mp4")
        create_video(video_clips, output_path)

        return {
            "status": "success",
            "video_path": output_path,
            "video_url": "/media/final.mp4",
            "num_scenes": len(video_clips)
        }

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    except Exception as e:
        print("❌ ERROR:", str(e))
        raise HTTPException(status_code=500, detail="Video generation failed")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_routes.py -v
```

Expected: all 3 route tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/generate.py tests/test_routes.py
git commit -m "feat: pass aspect_ratio through /generate endpoint"
```

---

## Task 5: Ratio Toggle UI in Frontend

**Files:**
- Modify: `app/static/index.html`

- [ ] **Step 1: Add CSS for ratio buttons**

In `index.html`, find the `/* ─── LANGUAGE SELECTOR ─── */` CSS block (around line 432). Add the following block **after** it (before the `/* ─── PROCESSING SECTION ─── */` comment):

```css
    /* ─────────────────────────────────────────
       RATIO SELECTOR
    ───────────────────────────────────────── */
    .ratio-selector {
      display: flex;
      gap: 8px;
      margin-bottom: 20px;
      animation: fadeUp 0.8s ease 0.38s both;
    }

    .ratio-btn {
      padding: 8px 22px;
      border-radius: 24px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text-mid);
      font-family: var(--font-mono);
      font-size: 12px;
      letter-spacing: 1px;
      cursor: pointer;
      transition: all 0.2s ease;
    }

    .ratio-btn:hover {
      border-color: var(--teal);
      color: var(--teal);
      background: rgba(0,207,180,0.05);
    }

    .ratio-btn.active {
      border-color: var(--teal);
      background: rgba(0,207,180,0.1);
      color: var(--teal);
      box-shadow: var(--glow-teal);
    }
```

- [ ] **Step 2: Add ratio selector HTML**

In the `<section id="heroSection">`, find the `<div class="lang-selector"` block. Add the ratio selector **immediately after** the closing `</div>` of the lang-selector:

```html
      <div class="ratio-selector" id="ratioSelector">
        <button class="ratio-btn active" id="ratio169" onclick="setRatio('16:9')">▬ 16:9 &nbsp; LANDSCAPE</button>
        <button class="ratio-btn" id="ratio916" onclick="setRatio('9:16')">▯ 9:16 &nbsp; SHORTS</button>
      </div>
```

- [ ] **Step 3: Add JS state variable and `setRatio()` function**

In the `<script>` block, find the `STATE` section. Add `selectedRatio` next to `selectedLanguage`:

```js
    let selectedRatio = '16:9';
```

Then add `setRatio()` after the `setLanguage()` function:

```js
    function setRatio(ratio) {
      selectedRatio = ratio;
      document.getElementById('ratio169').classList.toggle('active', ratio === '16:9');
      document.getElementById('ratio916').classList.toggle('active', ratio === '9:16');
    }
```

- [ ] **Step 4: Wire `selectedRatio` into the fetch call**

In `generateVideo()`, find the line:

```js
        const res = await fetch(`/generate?topic=${encodeURIComponent(topic)}&language=${selectedLanguage}`, {
```

Replace it with:

```js
        const res = await fetch(`/generate?topic=${encodeURIComponent(topic)}&language=${selectedLanguage}&aspect_ratio=${encodeURIComponent(selectedRatio)}`, {
```

- [ ] **Step 5: Manual smoke test**

Start the server and open `http://localhost:8080` (or whichever port is configured):

```bash
cd "/Users/chetan/Desktop/Data Science/youtube-video-generation-tool"
uvicorn app.main:app --reload --port 8080
```

Verify:
- Two pill buttons `▬ 16:9  LANDSCAPE` and `▯ 9:16  SHORTS` appear between language selector and input box.
- Clicking `9:16` highlights it in teal; `16:9` deselects.
- Clicking `16:9` switches back.

- [ ] **Step 6: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add aspect ratio toggle UI (16:9 / 9:16)"
```

---

## Task 6: Add `generate_shorts_caption()` to LLM Service

**Files:**
- Modify: `app/services/llm_service.py`
- Modify: `tests/test_llm_service.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_llm_service.py`:

```python
# ── generate_shorts_caption tests ────────────────────────────────────────────

def test_generate_shorts_caption_returns_string():
    """Function returns the model's response text directly."""
    mock = _make_model_mock("Black holes bend spacetime! 🌌\n#space #blackholes #science")
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_shorts_caption
        result = generate_shorts_caption("black holes")
    assert isinstance(result, str)
    assert len(result) > 0


def test_generate_shorts_caption_prompt_contains_topic():
    """The topic appears in the prompt sent to the model."""
    mock = _make_model_mock("Some caption #tag")
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_shorts_caption
        generate_shorts_caption("quantum computing")
    prompt_used = mock.generate_content.call_args[0][0]
    assert "quantum computing" in prompt_used


def test_generate_shorts_caption_hindi_uses_hindi_instruction():
    """When language='hi', the Hindi lang instruction appears in the prompt."""
    mock = _make_model_mock("कुछ कैप्शन #टैग")
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_shorts_caption
        generate_shorts_caption("black holes", language="hi")
    prompt_used = mock.generate_content.call_args[0][0]
    assert "Hindi" in prompt_used or "Devanagari" in prompt_used
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_llm_service.py::test_generate_shorts_caption_returns_string tests/test_llm_service.py::test_generate_shorts_caption_prompt_contains_topic tests/test_llm_service.py::test_generate_shorts_caption_hindi_uses_hindi_instruction -v
```

Expected: `ImportError` — `generate_shorts_caption` does not exist yet.

- [ ] **Step 3: Add `generate_shorts_caption()` to `app/services/llm_service.py`**

Append after the `generate_script` function:

```python
def generate_shorts_caption(topic: str, language: str = "en") -> str:
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])

    prompt = f"""
Write a YouTube Shorts caption for a video about: {topic}

{lang_instruction}

The caption must include:
- 2-3 punchy, engaging lines that tease or describe the topic (hook the viewer immediately)
- 10-15 relevant hashtags (mix of broad popular hashtags and niche-specific ones)

Return ONLY the caption text followed by the hashtags. No JSON. No explanation. No extra commentary.
"""

    response = model.generate_content(prompt)
    return response.text.strip()
```

- [ ] **Step 4: Run all llm_service tests**

```bash
pytest tests/test_llm_service.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/llm_service.py tests/test_llm_service.py
git commit -m "feat: add generate_shorts_caption to llm_service"
```

---

## Task 7: Add `/generate-caption` Endpoint

**Files:**
- Modify: `app/routes/generate.py`
- Modify: `tests/test_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_routes.py`:

```python
# ── /generate-caption tests ──────────────────────────────────────────────────

def test_generate_caption_returns_caption():
    """/generate-caption returns { caption: '...' } for a valid topic."""
    mock_caption = MagicMock(return_value="Black holes are wild! 🌌\n#space #physics")
    with patch("app.routes.generate.generate_shorts_caption", mock_caption):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.post("/generate-caption?topic=black+holes&language=en")
    assert resp.status_code == 200
    data = resp.json()
    assert "caption" in data
    assert data["caption"] == "Black holes are wild! 🌌\n#space #physics"
    mock_caption.assert_called_once_with("black holes", language="en")


def test_generate_caption_empty_topic_returns_400():
    """/generate-caption returns 400 when topic is empty."""
    with patch("app.routes.generate.generate_shorts_caption"):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.post("/generate-caption?topic=")
    assert resp.status_code == 400


def test_generate_caption_defaults_language_to_en():
    """When language is omitted, defaults to 'en'."""
    mock_caption = MagicMock(return_value="Some caption #tag")
    with patch("app.routes.generate.generate_shorts_caption", mock_caption):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        client.post("/generate-caption?topic=jazz")
    mock_caption.assert_called_once_with("jazz", language="en")
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_routes.py::test_generate_caption_returns_caption tests/test_routes.py::test_generate_caption_empty_topic_returns_400 tests/test_routes.py::test_generate_caption_defaults_language_to_en -v
```

Expected: `404 Not Found` — endpoint doesn't exist yet.

- [ ] **Step 3: Add the import and endpoint to `app/routes/generate.py`**

At the top of the file, add `generate_shorts_caption` to the existing import:

```python
from app.services.llm_service import generate_script, generate_shorts_caption
```

Append at the bottom of the file:

```python
@router.post("/generate-caption")
def generate_caption(topic: str, language: str = "en"):
    if not topic or topic.strip() == "":
        raise HTTPException(status_code=400, detail="Topic is required")

    if language not in ("en", "hi"):
        language = "en"

    try:
        caption = generate_shorts_caption(topic, language=language)
        return {"caption": caption}
    except Exception as e:
        print("❌ Caption ERROR:", str(e))
        raise HTTPException(status_code=500, detail="Caption generation failed")
```

- [ ] **Step 4: Run all route tests**

```bash
pytest tests/test_routes.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/generate.py tests/test_routes.py
git commit -m "feat: add /generate-caption endpoint"
```

---

## Task 8: Caption Button + Card UI in Frontend

**Files:**
- Modify: `app/static/index.html`

- [ ] **Step 1: Add CSS for caption button and card**

In `index.html`, find the `/* ─── ERROR STATE ─── */` CSS comment. Add the following block **before** it:

```css
    /* ─────────────────────────────────────────
       CAPTION
    ───────────────────────────────────────── */
    .caption-trigger-row {
      margin-top: 16px;
      display: flex;
      justify-content: flex-start;
    }

    .caption-btn {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 22px;
      border: 1px solid var(--teal);
      border-radius: 8px;
      background: rgba(0,207,180,0.06);
      color: var(--teal);
      font-family: var(--font-mono);
      font-size: 12px;
      letter-spacing: 2px;
      cursor: pointer;
      transition: all 0.25s ease;
    }

    .caption-btn:hover {
      background: rgba(0,207,180,0.12);
      box-shadow: var(--glow-teal);
    }

    .caption-btn.loading {
      opacity: 0.55;
      cursor: not-allowed;
      pointer-events: none;
    }

    .caption-card {
      margin-top: 16px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      overflow: hidden;
      animation: fadeIn 0.4s ease;
    }

    .caption-card-header {
      padding: 12px 18px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .caption-card-title {
      font-family: var(--font-mono);
      font-size: 10px;
      letter-spacing: 3px;
      color: var(--teal);
    }

    .copy-btn {
      padding: 5px 14px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: transparent;
      color: var(--text-mid);
      font-family: var(--font-mono);
      font-size: 10px;
      letter-spacing: 1px;
      cursor: pointer;
      transition: all 0.2s ease;
    }

    .copy-btn:hover {
      border-color: var(--teal);
      color: var(--teal);
    }

    .caption-text {
      padding: 18px;
      font-size: 14px;
      color: var(--text);
      line-height: 1.7;
      white-space: pre-wrap;
      font-family: var(--font-body);
    }
```

- [ ] **Step 2: Add caption HTML to results section**

In the results section, find the closing `</div>` of `.video-section`. Add the caption trigger row and card **inside** `.video-section`, immediately after `</div>` of `#videoPlayerWrap`:

```html
        <div class="caption-trigger-row" id="captionTriggerRow" style="display:none">
          <button class="caption-btn" id="captionBtn" onclick="generateCaption()">
            <span id="captionBtnLabel">✦ GENERATE CAPTION</span>
          </button>
        </div>

        <div class="caption-card" id="captionCard" style="display:none">
          <div class="caption-card-header">
            <div class="caption-card-title">YOUTUBE SHORTS CAPTION</div>
            <button class="copy-btn" id="copyBtn" onclick="copyCaption()">COPY</button>
          </div>
          <div class="caption-text" id="captionText"></div>
        </div>
```

- [ ] **Step 3: Add `generateCaption()` and `copyCaption()` JS functions**

In the `<script>` block, append before the closing `</script>` tag:

```js
    /* ─────────────────────────────────────────
       CAPTION
    ───────────────────────────────────────── */
    async function generateCaption() {
      const btn = document.getElementById('captionBtn');
      const label = document.getElementById('captionBtnLabel');
      btn.classList.add('loading');
      label.textContent = 'GENERATING…';

      try {
        const res = await fetch(
          `/generate-caption?topic=${encodeURIComponent(currentTopic)}&language=${selectedLanguage}`,
          { method: 'POST' }
        );

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || `HTTP ${res.status}`);
        }

        const data = await res.json();
        document.getElementById('captionText').textContent = data.caption;
        document.getElementById('captionCard').style.display = 'block';
        label.textContent = '✦ REGENERATE CAPTION';

      } catch (err) {
        showError(err.message);
        label.textContent = '✦ GENERATE CAPTION';
      } finally {
        btn.classList.remove('loading');
      }
    }

    function copyCaption() {
      const text = document.getElementById('captionText').textContent;
      navigator.clipboard.writeText(text).then(() => {
        const btn = document.getElementById('copyBtn');
        btn.textContent = 'COPIED ✓';
        setTimeout(() => { btn.textContent = 'COPY'; }, 2000);
      });
    }
```

- [ ] **Step 4: Show caption trigger in `showResults()` and reset in `resetUI()`**

In `showResults()`, before `// Re-enable button`, add:

```js
      // Show caption trigger, reset any previous caption
      document.getElementById('captionTriggerRow').style.display = 'flex';
      document.getElementById('captionCard').style.display = 'none';
      document.getElementById('captionText').textContent = '';
      document.getElementById('captionBtnLabel').textContent = '✦ GENERATE CAPTION';
```

In `resetUI()`, after `document.getElementById('topicInput').value = '';`, add:

```js
      document.getElementById('captionTriggerRow').style.display = 'none';
      document.getElementById('captionCard').style.display = 'none';
      document.getElementById('captionText').textContent = '';
```

- [ ] **Step 5: Manual smoke test**

```bash
uvicorn app.main:app --reload --port 8080
```

Verify:
- After generating a video, a teal `✦ GENERATE CAPTION` button appears below the video player.
- Clicking it shows a loading state, then reveals the caption card with the generated text.
- The `COPY` button copies the caption text to clipboard and briefly shows `COPIED ✓`.
- Clicking `↩ NEW VIDEO` hides the caption card.
- Clicking `✦ REGENERATE CAPTION` a second time replaces the caption text.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add YouTube Shorts caption button and inline card to results"
```

---

## Self-Review

**Spec coverage:**
- ✅ 16:9 / 9:16 toggle in hero section (Task 5)
- ✅ `aspect_ratio` flows through `/generate` → `generate_script` → `generate_image` (Tasks 2–4)
- ✅ Imagen 3 receives correct `aspect_ratio` value (Task 3)
- ✅ LLM prompt adjusts for Shorts (max 3 scenes, ≤15s narrations, hook-first) (Task 2)
- ✅ `generate_shorts_caption()` function with 2–3 punchy lines + 10–15 hashtags (Task 6)
- ✅ `POST /generate-caption` endpoint, 400 on empty topic (Task 7)
- ✅ Teal `GENERATE CAPTION` button in results section (Task 8)
- ✅ Inline caption card with Copy button + `COPIED ✓` flash (Task 8)
- ✅ Caption reuses `currentTopic` and `selectedLanguage` state (Task 8)
- ✅ Card hidden on `resetUI()` (Task 8)

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:**
- `generate_script(topic, language, aspect_ratio)` — defined Task 2, called Task 4 ✅
- `generate_image(prompt, idx, aspect_ratio)` — defined Task 3, called Task 4 ✅
- `generate_shorts_caption(topic, language)` — defined Task 6, imported + called Task 7 ✅
- `selectedRatio` — defined Task 5, used in fetch call Task 5 ✅
- `currentTopic` / `selectedLanguage` — already in existing JS state ✅
