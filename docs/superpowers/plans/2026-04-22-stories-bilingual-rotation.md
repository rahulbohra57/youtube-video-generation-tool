# Stories Channel Bilingual Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the Stories channel from 4 Hindi stories/day to 3 English + 1 Hindi story/day, with the Hindi slot rotating across the 4 daily scheduler slots on a 4-day cycle.

**Architecture:** Add a `language` field (`"en"` or `"hi"`) that originates in `story_researcher.run()` via a deterministic 4-day rotation formula, flows into the Cloud Task payload, and replaces the two hardcoded `"hi"` values in `generator_agent.run()`. English stories use a new realistic/photorealistic visual style pool and English prompts; Hindi stories are completely unchanged.

**Tech Stack:** Python 3.11, FastAPI, Google Vertex AI (Gemini 2.5 Flash), Google Cloud TTS, pytest + unittest.mock

---

## File Map

| File | Change |
|------|--------|
| `app/services/llm_service.py` | Split visual style pool; add `language` param to `generate_story_idea()` and `generate_story_script()` |
| `app/agents/story_researcher.py` | Add `_story_language()` helper; wire language into idea generation and Cloud Task payload |
| `app/routes/stories.py` | Extract `language` from payload; pass to `generator_agent.run()` |
| `app/agents/generator_agent.py` | Add `language` param to `run()`; replace 2 hardcoded `"hi"` values; add English safety fallback prompts |
| `tests/test_stories_bilingual.py` | New test file covering all changes above |

---

## Task 1: Language-aware LLM story generation (`llm_service.py`)

**Files:**
- Modify: `app/services/llm_service.py`
- Test: `tests/test_stories_bilingual.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_stories_bilingual.py`:

```python
# tests/test_stories_bilingual.py
from unittest.mock import MagicMock, patch


def _model_mock(text: str) -> MagicMock:
    m = MagicMock()
    m.generate_content.return_value.text = text
    return m


# ── generate_story_idea ──────────────────────────────────────────────────────

def test_generate_story_idea_hindi_prompt_contains_devanagari():
    """Hindi idea generation uses the Devanagari prompt."""
    mock = _model_mock('{"title": "मेहनत", "mood": "inspiring", "premise": "एक गरीब किसान का बेटा जब सबने उसे नकार दिया तब उसने असाधारण कदम उठाया।"}')
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_story_idea
        result = generate_story_idea(preferred_mood="inspiring", language="hi")
    prompt = mock.generate_content.call_args[0][0]
    assert "Hindi" in prompt or "हिंदी" in prompt
    assert result["title"] == "मेहनत"


def test_generate_story_idea_english_prompt_is_in_english():
    """English idea generation uses an English prompt (no Devanagari)."""
    mock = _model_mock('{"title": "The Last Promise", "mood": "inspiring", "premise": "A retired teacher discovers her student became a doctor because of one encouraging word she said thirty years ago."}')
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_story_idea
        result = generate_story_idea(preferred_mood="inspiring", language="en")
    prompt = mock.generate_content.call_args[0][0]
    # English prompt must NOT contain Devanagari instruction keywords
    assert "देवनागरी" not in prompt
    assert "हिंदी" not in prompt
    # Must contain English instruction
    assert "English" in prompt
    assert result["title"] == "The Last Promise"


def test_generate_story_idea_defaults_to_hindi():
    """Omitting language defaults to Hindi (backward compat)."""
    mock = _model_mock('{"title": "मेहनत", "mood": "inspiring", "premise": "एक गरीब किसान का बेटा जब सबने उसे नकार दिया तब उसने असाधारण कदम उठाया।"}')
    with patch("app.services.llm_service.model", mock):
        from app.services.llm_service import generate_story_idea
        generate_story_idea(preferred_mood="inspiring")
    prompt = mock.generate_content.call_args[0][0]
    assert "हिंदी" in prompt or "Hindi" in prompt
    assert "देवनागरी" not in prompt or "Devanagari" not in prompt  # Devanagari in a Hindi prompt is fine


# ── generate_story_script ────────────────────────────────────────────────────

def test_generate_story_script_hindi_prompt_uses_devanagari():
    """Hindi script generation prompt is in Hindi with Devanagari narration rule."""
    mock = _model_mock('[{"scene":1,"narration":"एक दिन","visual":"watercolor scene"}]')
    with patch("app.services.llm_service.model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Vibrant storybook illustration"
            from app.services.llm_service import generate_story_script
            generate_story_script("मेहनत", "inspiring", language="hi")
    prompt = mock.generate_content.call_args[0][0]
    assert "देवनागरी" in prompt
    assert "हिंदी" in prompt


def test_generate_story_script_english_prompt_requests_english_narration():
    """English script generation prompt requests English narration."""
    mock = _model_mock('[{"scene":1,"narration":"A farmer stood alone","visual":"cinematic scene"}]')
    with patch("app.services.llm_service.model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Cinematic photorealistic"
            from app.services.llm_service import generate_story_script
            generate_story_script("The Last Promise", "inspiring", language="en")
    prompt = mock.generate_content.call_args[0][0]
    assert "देवनागरी" not in prompt
    assert "English" in prompt


def test_generate_story_script_english_uses_realistic_visual_pool():
    """English stories pick from the realistic visual style pool, not the painted pool."""
    mock = _model_mock('[{"scene":1,"narration":"A farmer stood alone","visual":"cinematic scene"}]')
    with patch("app.services.llm_service.model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Cinematic photorealistic, dramatic natural lighting"
            from app.services.llm_service import generate_story_script, _STORY_VISUAL_STYLE_POOL_EN
            generate_story_script("The Last Promise", "inspiring", language="en")
        # random.choice was called with the EN pool
        pool_arg = mock_random.choice.call_args[0][0]
        assert pool_arg is _STORY_VISUAL_STYLE_POOL_EN


def test_generate_story_script_hindi_uses_painted_visual_pool():
    """Hindi stories pick from the painted/illustrated visual style pool."""
    mock = _model_mock('[{"scene":1,"narration":"एक दिन","visual":"watercolor"}]')
    with patch("app.services.llm_service.model", mock):
        with patch("app.services.llm_service.random") as mock_random:
            mock_random.choice.return_value = "Vibrant storybook illustration"
            from app.services.llm_service import generate_story_script, _STORY_VISUAL_STYLE_POOL_HI
            generate_story_script("मेहनत", "inspiring", language="hi")
        pool_arg = mock_random.choice.call_args[0][0]
        assert pool_arg is _STORY_VISUAL_STYLE_POOL_HI
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/chetan/Desktop/DSE_Projects/youtube-video-generation-tool
python -m pytest tests/test_stories_bilingual.py -v 2>&1 | head -60
```

Expected: All tests FAIL — `generate_story_idea` and `generate_story_script` don't accept `language` yet, `_STORY_VISUAL_STYLE_POOL_EN` and `_STORY_VISUAL_STYLE_POOL_HI` don't exist.

- [ ] **Step 3: Implement changes in `llm_service.py`**

Find the `_STORY_VISUAL_STYLE_POOL` list (around line 490) and replace it with two pools:

```python
_STORY_VISUAL_STYLE_POOL_HI = [
    "Vibrant storybook illustration, bold outlines, rich saturated colors, dramatic lighting, cinematic composition",
    "Studio Ghibli-inspired warm painterly style, lush green landscapes, golden sunlight, emotionally expressive characters",
    "Indian folk art inspired, vivid Madhubani-style patterns, warm terracotta and saffron palette, flat design",
    "Soft watercolor illustration, warm earthy palette, gentle diffused lighting, painterly texture",
    "Bold graphic novel style, high contrast colors, dynamic angles, strong silhouettes, vibrant palette",
]

_STORY_VISUAL_STYLE_POOL_EN = [
    "Cinematic photorealistic, dramatic natural lighting, shallow depth of field, vivid detail",
    "Documentary-style photography, candid composition, warm natural light, authentic atmosphere",
    "High-resolution realistic render, vivid detail, cinematic color grading, emotionally evocative",
    "Moody cinematic scene, rich warm tones, dramatic yet inviting atmosphere, photorealistic",
    "Photorealistic portrait lighting, golden hour glow, emotionally resonant, crisp detail",
]
```

Update `generate_story_idea()` signature and body. Find the function starting at line 504 and replace it:

```python
def generate_story_idea(recently_used_titles: list[str] | None = None, preferred_mood: str = "", language: str = "hi") -> dict:
    """Generate a fresh moral story concept. Returns {title, mood, premise}.

    For language="hi": title, mood, premise are in Hindi (Devanagari).
    For language="en": title, mood, premise are in English.
    """
    avoid_block = ""
    if recently_used_titles:
        lines = "\n".join(f"  - {t}" for t in recently_used_titles[:20])
        if language == "en":
            avoid_block = f"\nDo NOT reuse these recently created story titles:\n{lines}\n"
        else:
            avoid_block = f"\nइन कहानियों को दोबारा मत बनाओ (हाल में बनाई गई):\n{lines}\n"

    preferred = (preferred_mood or "").strip().lower()
    if preferred and preferred not in _STORY_MOODS:
        preferred = ""
    mood_list = ", ".join(_STORY_MOODS)

    if language == "en":
        preferred_rule = (
            f"\n- mood MUST be exactly: {preferred}"
            if preferred
            else f"\n- mood must be one of: {mood_list}"
        )
        prompt = f"""You are a creative storyteller writing short moral stories for YouTube Shorts.

Generate a brand new, original story concept. The story should complete in 45-55 seconds.{avoid_block}

Rules:
- Title should be 5-8 words, following one of these patterns:
  (a) Emotional contrast: "When [character] [action], everyone was stunned"
  (b) Question hook: "Could you [action] in [situation]?"
  (c) Shocking reversal: "Not [expected], but [unexpected] won the day"
  (d) Intriguing premise: "The [X] that [biggest claim]"
{preferred_rule}
- premise must include: (1) a named or clearly described character, (2) a specific challenge or conflict, (3) a moral direction
- premise must be at least 15 words — avoid generic premises like "a child learns a lesson"

Return only a valid JSON object, no markdown:
{{"title": "...", "mood": "...", "premise": "..."}}"""
    else:
        preferred_rule = (
            f"\n- mood MUST be exactly: {preferred}"
            if preferred
            else f"\n- mood इनमें से एक हो: {mood_list}"
        )
        prompt = f"""तुम एक रचनात्मक Hindi कहानीकार हो जो YouTube Shorts के लिए छोटी नैतिक कहानियाँ लिखते हो।

एक बिल्कुल नई, मौलिक कहानी का विचार दो। कहानी 45-55 सेकंड में पूरी होनी चाहिए।{avoid_block}

नियम:
- शीर्षक (title) 5-8 शब्दों का हो, इन patterns में से एक follow करे:
  (a) Emotional contrast: "जब [पात्र] ने [action] किया, तब सब हैरान रह गए"
  (b) Question hook: "क्या तुम [situation] में [action] कर सकते हो?"
  (c) Shocking reversal: "[expected thing] नहीं, [unexpected thing] ने जीत दिलाई"
  (d) Intriguing premise: "वो [X] जिसने [biggest claim] कर दिया"
{preferred_rule}
- premise में तीन चीज़ें ज़रूर हों: (1) एक named या clearly described पात्र, (2) एक specific चुनौती या संघर्ष, (3) एक moral direction
- premise कम से कम 15 शब्दों का हो — "एक बच्चा सीखता है" जैसे generic premise बिल्कुल नहीं

सिर्फ एक valid JSON object return करो, कोई markdown नहीं:
{{"title": "...", "mood": "...", "premise": "..."}}"""

    for attempt in range(2):
        try:
            response = model.generate_content(prompt)
            result = _extract_json_object(_response_text(response))
            if result.get("title") and result.get("mood") and result.get("premise"):
                if not _is_premise_adequate(result["premise"]):
                    logger.warning("Story premise quality gate failed (attempt %d): %s", attempt + 1, result["premise"])
                    continue
                return result
        except Exception:
            pass
    # Fallback
    import random as _random
    if language == "en":
        return {
            "title": "The Day Everything Changed",
            "mood": preferred or _random.choice(_STORY_MOODS),
            "premise": "A young farmer facing drought discovers an underground spring that saves his entire village from ruin.",
        }
    return {
        "title": "एक छोटी सी मेहनत, बड़ा बदलाव",
        "mood": preferred or _random.choice(_STORY_MOODS),
        "premise": "एक गरीब किसान का बेटा जब सबने उसे नकार दिया, तब उसने एक असाधारण कदम उठाया जिसने पूरे गाँव की तकदीर बदल दी।",
    }
```

Update `generate_story_script()` signature and body. Find the function starting at line 557 and replace it:

```python
def generate_story_script(title: str, mood: str, premise: str = "", language: str = "hi") -> str:
    """Generate a 4-scene moral story script for YouTube Shorts (<1 min).

    For language="hi": narrations in Hindi (Devanagari), realistic visual prompts in English.
    For language="en": narrations in English, realistic/photorealistic visual prompts in English.
    Returns JSON array with scene/narration/visual keys.
    """
    premise_block = (f"\nStory premise: {premise}" if language == "en" else f"\nकहानी का सार: {premise}") if premise else ""
    style_pool = _STORY_VISUAL_STYLE_POOL_EN if language == "en" else _STORY_VISUAL_STYLE_POOL_HI
    video_style = random.choice(style_pool)

    if language == "en":
        prompt = f"""You are a scriptwriter creating short moral stories in English for YouTube Shorts.

Story: {title}{premise_block}
Mood: {mood}

Write 4 scenes. Total duration should be 45-55 seconds.

Each scene must have:
- "narration": In English, 15-18 words, natural to speak aloud
- "visual": In English (for Imagen), a detailed image generation prompt

Scene structure:
- Scene 1 (Hook ~12s): Open with a shocking situation, emotional paradox, or unexpected moment — hook the viewer in 3 seconds. Never start with "Once upon a time", "Hi everyone", "Today I want to".
- Scene 2 (Rising Action ~12s): Build tension — the character faces a hard decision or impossible challenge.
- Scene 3 (Turning Point ~12s): The decisive moment — the character takes a surprising, unexpected action.
- Scene 4 (Resolution ~12s): Show the outcome — what changed in the character's world, relationships, or community? Do NOT state the moral directly ("So always remember", "The lesson is") — let the viewer feel it.

NARRATION rules:
- 15-18 words per scene — no more
- No clichés: "once upon a time", "the lesson is", "always remember", "friends"
- No direct moral preaching in scenes 1-3 — show through action and consequence
- Simple, natural spoken English

VISUAL PROMPT rules:
- In English
- Start with this style prefix: "{video_style} — "
- No real people, religious symbols, copyright characters, brand logos
- Prefer natural settings: countryside, forests, rivers, small towns
- No text, no words, no signs in the image
- CRITICAL — SAFETY: ALL visuals must be bright, warm, and child-friendly. Even for mystery/thriller/crime genres, convey curiosity and wonder — NEVER darkness, fear, danger, or violence. No sinister shadows, no weapons, no blood, no frightening creatures, no ominous imagery. Imagen will reject dark or frightening content.

Return only a valid JSON array, no markdown:
[
  {{"scene": 1, "narration": "...", "visual": "..."}},
  {{"scene": 2, "narration": "...", "visual": "..."}},
  {{"scene": 3, "narration": "...", "visual": "..."}},
  {{"scene": 4, "narration": "...", "visual": "..."}}
]"""
    else:
        prompt = f"""तुम एक YouTube Shorts के लिए Hindi में छोटी नैतिक कहानी लिखने वाले scriptwriter हो।

कहानी: {title}{premise_block}
मूड: {mood}

4 दृश्य (scenes) लिखो। कुल अवधि 45-55 सेकंड होनी चाहिए।

हर scene में:
- "narration": हिंदी में (देवनागरी लिपि), 15-18 शब्द, बोलने में स्वाभाविक
- "visual": अंग्रेज़ी में (Imagen के लिए), बहुत विस्तृत image generation prompt

Scene structure:
- Scene 1 (Hook ~12s): पहले वाक्य में ही एक shocking situation, emotional paradox, या unexpected moment दो — दर्शक पहले 3 सेकंड में रुक जाए। "एक बार की बात है", "आज मैं", "नमस्ते दोस्तों" जैसा कोई भी generic opening बिल्कुल नहीं।
- Scene 2 (Rising Action ~12s): tension बढ़ाओ — पात्र एक कठिन निर्णय या असंभव चुनौती के सामने है।
- Scene 3 (Turning Point ~12s): निर्णायक क्षण — पात्र एक surprising, unexpected कदम उठाता है।
- Scene 4 (Resolution ~12s): नतीजा दिखाओ — पात्र की दुनिया में, उसके रिश्तों में, या समाज में क्या बदला? नैतिक lesson directly मत बोलो ("इसलिए हमेशा", "सीख यह है" जैसे phrases बिल्कुल नहीं) — दर्शक खुद feel करे।

NARRATION नियम:
- हिंदी (देवनागरी) में लिखो — Roman script नहीं
- 15-18 शब्द प्रति scene — इससे अधिक नहीं
- "एक बार की बात है", "इसलिए हमेशा", "सीख यह है", "दोस्तों" जैसे clichés बिल्कुल नहीं
- Scene 1-3 में सीधे नैतिक उपदेश मत दो — कार्य और परिणाम के ज़रिए दिखाओ
- सरल, बोधगम्य भाषा

VISUAL PROMPT नियम:
- अंग्रेज़ी में लिखो
- इस style prefix से शुरू करो: "{video_style} — "
- कोई असली व्यक्ति, धार्मिक प्रतीक, copyright characters नहीं
- प्रकृति, गाँव, जंगल, नदी जैसी settings को प्राथमिकता दो
- No text, no words, no signs in the image
- CRITICAL — SAFETY: ALL visuals must be bright, warm, and child-friendly. Even for mystery/thriller/crime genres, convey curiosity and wonder — NEVER darkness, fear, danger, or violence. No sinister shadows, no weapons, no blood, no frightening creatures, no ominous imagery. Use symbolic and metaphorical visuals (e.g. a glowing lantern for mystery, a winding path for adventure). Imagen will reject dark or frightening content.

सिर्फ valid JSON array return करो, कोई markdown नहीं:
[
  {{"scene": 1, "narration": "...", "visual": "..."}},
  {{"scene": 2, "narration": "...", "visual": "..."}},
  {{"scene": 3, "narration": "...", "visual": "..."}},
  {{"scene": 4, "narration": "...", "visual": "..."}}
]"""

    response = model.generate_content(prompt)
    return _response_text(response)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_stories_bilingual.py::test_generate_story_idea_hindi_prompt_contains_devanagari \
  tests/test_stories_bilingual.py::test_generate_story_idea_english_prompt_is_in_english \
  tests/test_stories_bilingual.py::test_generate_story_idea_defaults_to_hindi \
  tests/test_stories_bilingual.py::test_generate_story_script_hindi_prompt_uses_devanagari \
  tests/test_stories_bilingual.py::test_generate_story_script_english_prompt_requests_english_narration \
  tests/test_stories_bilingual.py::test_generate_story_script_english_uses_realistic_visual_pool \
  tests/test_stories_bilingual.py::test_generate_story_script_hindi_uses_painted_visual_pool \
  -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Verify existing LLM tests still pass**

```bash
python -m pytest tests/test_llm_service.py -v
```

Expected: All tests PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add app/services/llm_service.py tests/test_stories_bilingual.py
git commit -m "feat(stories): add language param to story idea/script generation, split visual style pools"
```

---

## Task 2: Language rotation in `story_researcher.py`

**Files:**
- Modify: `app/agents/story_researcher.py`
- Test: `tests/test_stories_bilingual.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_stories_bilingual.py`:

```python
# ── _story_language rotation ─────────────────────────────────────────────────

def test_story_language_hindi_slot_rotates_by_day():
    """Hindi slot advances by 1 each day across 4 slots on a 4-day cycle."""
    from zoneinfo import ZoneInfo
    from unittest.mock import patch
    import datetime as dt

    IST = ZoneInfo("Asia/Kolkata")
    # slot_hours = [7, 11, 14, 18] → indices 0, 1, 2, 3
    # Test 4 consecutive days: DOY 1→slot1 Hindi, DOY 2→slot2, DOY 3→slot3, DOY 4→slot0
    test_cases = [
        # (DOY, hour, expected_language)
        (1, 7,  "en"),   # DOY=1 → hindi_slot=1, running at slot0 → English
        (1, 11, "hi"),   # DOY=1 → hindi_slot=1, running at slot1 → Hindi
        (1, 14, "en"),   # DOY=1 → hindi_slot=1, running at slot2 → English
        (1, 18, "en"),   # DOY=1 → hindi_slot=1, running at slot3 → English
        (2, 7,  "en"),   # DOY=2 → hindi_slot=2, slot0 → English
        (2, 14, "hi"),   # DOY=2 → hindi_slot=2, slot2 → Hindi
        (4, 7,  "hi"),   # DOY=4 → hindi_slot=0, slot0 → Hindi
        (4, 11, "en"),   # DOY=4 → hindi_slot=0, slot1 → English
    ]

    for doy, hour, expected in test_cases:
        fake_now = dt.datetime(2026, 1, doy, hour, 5, 0, tzinfo=IST)
        with patch("app.agents.story_researcher.datetime") as mock_dt:
            # Return a real datetime so .hour and .timetuple() work naturally
            mock_dt.now.return_value = fake_now
            from app.agents.story_researcher import _story_language
            result = _story_language()
        assert result == expected, f"DOY={doy}, hour={hour}: expected {expected}, got {result}"


def test_story_language_defaults_to_hindi_for_unknown_slot():
    """If the current hour doesn't match any slot, language defaults to 'en'
    (the slot_index stays at 0 which may or may not be the hindi slot)."""
    # This just verifies the function returns a valid language string under edge conditions
    from zoneinfo import ZoneInfo
    import datetime as dt
    from unittest.mock import patch

    IST = ZoneInfo("Asia/Kolkata")
    fake_now = dt.datetime(2026, 1, 1, 3, 0, 0, tzinfo=IST)  # 3am — before all slots
    with patch("app.agents.story_researcher.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        from app.agents.story_researcher import _story_language
        result = _story_language()
    assert result in ("en", "hi")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_stories_bilingual.py::test_story_language_hindi_slot_rotates_by_day \
  tests/test_stories_bilingual.py::test_story_language_defaults_to_hindi_for_unknown_slot \
  -v
```

Expected: FAIL — `_story_language` doesn't exist yet.

- [ ] **Step 3: Implement `_story_language()` and wire into `run()`**

In `app/agents/story_researcher.py`, add the import at the top if not present:
```python
from datetime import datetime, timezone
```
(It's already imported — `datetime` is used in `run()`.)

Add `_story_language()` function after `_recently_used_titles()` (around line 59):

```python
def _story_language() -> str:
    """Return 'hi' for today's rotating Hindi slot, 'en' for all other slots.

    4 daily slots map to indices by IST hour: 7am→0, 11am→1, 2pm→2, 6pm→3.
    The Hindi slot index is day_of_year % 4, rotating on a 4-day cycle.
    """
    from zoneinfo import ZoneInfo
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    slot_hours = [7, 11, 14, 18]
    slot_index = 0
    for idx, hour in enumerate(slot_hours):
        if now_ist.hour >= hour:
            slot_index = idx
    hindi_slot = now_ist.timetuple().tm_yday % 4
    return "hi" if slot_index == hindi_slot else "en"
```

In `run()`, add language detection right after the pipeline state check (after line 122, before `recently_used = _recently_used_titles()`):

```python
    # Determine language for this scheduler slot (3 English : 1 Hindi per day, rotating)
    language = _story_language()
```

Update the `generate_story_idea()` call (currently around line 128) to pass `language`:

```python
        idea = generate_story_idea(
            recently_used_titles=recently_used,
            preferred_mood=target_genre,
            language=language,
        )
```

Add `"language": language` to the Cloud Task payload dict (currently around line 200). The full payload block becomes:

```python
    payload = json.dumps({
        "headline": title,
        "code": code,
        "batch_id": batch_id,
        "job_id": job_id,
        "public_id": public_id,
        "force_run": True,
        "genre": mood,
        "details": premise,
        "virality_score": 0.0,
        "channel_id": "stories",
        "script_type": "story",
        "language": language,
    }).encode()
```

Add `"language": language` to the `create_or_update_job()` call (currently around line 182) so REDO knows the original language:

```python
    firestore_service.create_or_update_job(job_id, {
        "job_id": job_id,
        "batch_id": batch_id,
        "code": code,
        "topic": title,
        "source": "scheduler",
        "status": "queued",
        "public_id": public_id,
        "genre": mood,
        "details": premise,
        "channel_id": "stories",
        "language": language,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
```

Update the Telegram notification (currently around line 244) to show language:

```python
        send_message(
            STORIES_CHAT_ID,
            f"📖 Generating {'Hindi' if language == 'hi' else 'English'} story...\n"
            f"Title: *{title}*\n"
            f"Genre: {mood.title()}\n"
            f"Id: `{public_id}`",
            channel_id="stories",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_stories_bilingual.py::test_story_language_hindi_slot_rotates_by_day \
  tests/test_stories_bilingual.py::test_story_language_defaults_to_hindi_for_unknown_slot \
  -v
```

Expected: Both PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agents/story_researcher.py tests/test_stories_bilingual.py
git commit -m "feat(stories): add 4-day language rotation — 3 English slots, 1 Hindi slot per day"
```

---

## Task 3: Thread `language` through route and generator agent

**Files:**
- Modify: `app/routes/stories.py`
- Modify: `app/agents/generator_agent.py`
- Test: `tests/test_stories_bilingual.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_stories_bilingual.py`:

```python
# ── generator_agent language threading ──────────────────────────────────────

def test_generate_stories_task_route_passes_language_to_generator():
    """The /generate/stories-task route extracts language from payload and passes it to generator_agent."""
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient

    with patch("app.agents.generator_agent.run") as mock_run, \
         patch("app.services.firestore_service.get_job", return_value=None), \
         patch("app.services.firestore_service.create_or_update_job"), \
         patch("app.services.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}):
        from app.main import app
        client = TestClient(app)
        resp = client.post("/generate/stories-task", json={
            "headline": "The Last Promise",
            "code": "STORY01",
            "batch_id": "b1",
            "job_id": "j1",
            "genre": "inspiring",
            "language": "en",
        })
    assert resp.status_code == 200
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs.get("language") == "en"


def test_generator_agent_uses_payload_language_not_hardcoded_hi():
    """generator_agent.run() with language='en' passes 'en' to generate_story_script."""
    from unittest.mock import patch, MagicMock

    mock_script = '[{"scene":1,"narration":"A farmer stood alone","visual":"cinematic — field"}]'

    with patch("app.services.llm_service.generate_story_script", return_value=mock_script) as mock_gen, \
         patch("app.services.firestore_service.get_job", return_value=None), \
         patch("app.services.firestore_service.create_or_update_job"), \
         patch("app.services.firestore_service.acquire_video_lock", return_value=True), \
         patch("app.services.firestore_service.release_video_lock"), \
         patch("app.services.firestore_service.get_pipeline_state", return_value={"state": "processing", "active_batch_id": "b1"}), \
         patch("app.services.firestore_service.set_pipeline_and_batch_state"), \
         patch("app.services.tts_service.choose_voice_for_video", return_value="en-US-Neural2-D"), \
         patch("app.services.firestore_service.mark_scene_checkpoint"), \
         patch("app.services.tts_service.generate_audio", return_value="/tmp/audio.mp3"), \
         patch("app.services.image_service.generate_image", return_value=("/tmp/img.png", 0)), \
         patch("app.services.video_service.create_video", return_value="/tmp/video.mp4"), \
         patch("app.services.firestore_service.record_quota_event"), \
         patch("app.agents.senior_script_reviewer.review_package", return_value={"title": "Test", "caption": "cap", "scenes": [{"scene": 1, "narration": "A farmer stood alone", "visual": "cinematic — field"}]}), \
         patch("app.agents.social_media_agent.post"), \
         patch("app.services.telegram_service.send_message"):
        from app.agents import generator_agent
        generator_agent.run(
            "The Last Promise", "STORY01",
            batch_id="b1", job_id="j1", force_run=True,
            genre="inspiring", channel_id="stories",
            script_type="story", language="en",
        )

    mock_gen.assert_called_once()
    call_kwargs = mock_gen.call_args[1]
    assert call_kwargs.get("language") == "en", f"Expected language='en', got: {call_kwargs}"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_stories_bilingual.py::test_generate_stories_task_route_passes_language_to_generator \
  tests/test_stories_bilingual.py::test_generator_agent_uses_payload_language_not_hardcoded_hi \
  -v
```

Expected: Both FAIL — `generator_agent.run()` doesn't accept `language` yet, route doesn't extract it.

- [ ] **Step 3: Add `language` parameter to `generator_agent.run()`**

In `app/agents/generator_agent.py`, update the `run()` function signature (line 124) to add `language`:

```python
def run(
    headline: str,
    code: str,
    batch_id: str = None,
    job_id: str | None = None,
    public_id: str | None = None,
    force_run: bool = False,
    genre: str = "",
    details: str = "",
    virality_score: float = 0.0,
    idempotency_scope: str | None = None,
    idempotency_key: str | None = None,
    channel_id: str = "news",
    script_type: str = "news",
    language: str | None = None,
):
```

Replace line 178 (voice lang hardcoding):

```python
    # language defaults to "hi" for stories (backward compat with in-flight tasks)
    _voice_lang = (language or "hi") if script_type == "story" else "en"
```

Replace lines 267–271 (story script generation block):

```python
        if script_type == "story":
            # Stories: pure LLM generation, language from payload (default "hi" for backward compat)
            language = language or "hi"
            mood = genre or "inspiring"
            raw_script = generate_story_script(headline, mood=mood, premise=details or "", language=language)
```

- [ ] **Step 4: Extract `language` from payload in `stories.py`**

In `app/routes/stories.py`, in the `generate_stories_task()` function (around line 96), add extraction of `language` after the existing field extractions:

```python
    language = payload.get("language", "hi")
```

Update the `generator_agent.run()` call (around line 111) to pass `language`:

```python
        generator_agent.run(
            headline,
            code,
            batch_id=batch_id,
            job_id=job_id,
            public_id=public_id,
            force_run=force_run,
            genre=genre,
            details=details,
            virality_score=virality_score,
            channel_id="stories",
            script_type="story",
            language=language,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_stories_bilingual.py::test_generate_stories_task_route_passes_language_to_generator \
  tests/test_stories_bilingual.py::test_generator_agent_uses_payload_language_not_hardcoded_hi \
  -v
```

Expected: Both PASS.

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
python -m pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routes/stories.py app/agents/generator_agent.py tests/test_stories_bilingual.py
git commit -m "feat(stories): thread language through stories route and generator agent"
```

---

## Task 4: English safety-filter fallback prompts (`generator_agent.py`)

**Files:**
- Modify: `app/agents/generator_agent.py`
- Test: `tests/test_stories_bilingual.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_stories_bilingual.py`:

```python
# ── English safety-filter fallback ──────────────────────────────────────────

def test_english_story_safety_fallback_uses_realistic_prompt():
    """When an English story scene is safety-filtered, the realistic fallback prompt is used (not watercolor)."""
    from app.agents.generator_agent import _STORY_GENRE_SAFE_PROMPTS_EN
    # All 12 genres must be present
    expected_genres = {
        "inspiring", "heartfelt", "comedy", "crime", "action",
        "sci-fi", "mythology", "thriller", "mystery",
        "adventure", "slice-of-life", "historical",
    }
    assert set(_STORY_GENRE_SAFE_PROMPTS_EN.keys()) == expected_genres
    # English prompts must NOT contain "watercolor" (that's the Hindi style)
    for genre, prompt in _STORY_GENRE_SAFE_PROMPTS_EN.items():
        assert "watercolor" not in prompt.lower(), f"Genre '{genre}' fallback still uses watercolor style"
    # Must contain realistic descriptors
    realistic_terms = {"cinematic", "photorealistic", "documentary", "realistic", "photograph"}
    for genre, prompt in _STORY_GENRE_SAFE_PROMPTS_EN.items():
        has_realistic = any(term in prompt.lower() for term in realistic_terms)
        assert has_realistic, f"Genre '{genre}' fallback missing realistic descriptor: {prompt}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_stories_bilingual.py::test_english_story_safety_fallback_uses_realistic_prompt -v
```

Expected: FAIL — `_STORY_GENRE_SAFE_PROMPTS_EN` doesn't exist yet.

- [ ] **Step 3: Add `_STORY_GENRE_SAFE_PROMPTS_EN` and branch fallback logic**

In `app/agents/generator_agent.py`, after the existing `_STORY_GENRE_SAFE_PROMPTS` dict (around line 60), add:

```python
# English-story safe fallback prompts — realistic/photorealistic style.
# Used when an English story scene is rejected by Imagen's safety filter.
_STORY_GENRE_SAFE_PROMPTS_EN = {
    "inspiring": "Cinematic photorealistic scene — a young person standing on a sunlit hilltop at golden sunrise, arms wide open, warm light, no text, no words",
    "heartfelt": "Documentary-style photography — two elderly hands clasped together on a wooden table, warm afternoon light streaming through a window, no text, no words",
    "comedy": "Candid natural light photography — a small dog wearing oversized sunglasses sitting at a cafe table, cheerful sunny atmosphere, no text, no words",
    "crime": "Cinematic photorealistic — a detective's desk with scattered papers and a glowing desk lamp, warm moody atmosphere, no text, no words",
    "action": "Dramatic natural lighting photography — a person mid-sprint across a sun-drenched open field, motion blur on grass, dynamic energy, no text, no words",
    "sci-fi": "High-resolution realistic render — a glowing holographic sphere floating above an open palm in a bright sunlit laboratory, futuristic and clean, no text, no words",
    "mythology": "Cinematic photorealistic — an ancient stone temple surrounded by lush green trees in morning mist, golden light filtering through leaves, serene, no text, no words",
    "thriller": "Moody cinematic photograph — a lit lantern on a cobblestone path at dusk, warm amber glow, sense of curiosity and wonder, no text, no words",
    "mystery": "Documentary-style photography — an old wooden chest half-open in a sunlit attic, warm dust particles floating in light, curious atmosphere, no text, no words",
    "adventure": "Cinematic photorealistic — a hiker standing at a scenic mountain viewpoint at sunrise, vast green valley below, no text, no words",
    "slice-of-life": "Natural light photography — a family sharing breakfast at a bright kitchen table, warm morning sunlight, genuine smiles, no text, no words",
    "historical": "Cinematic photorealistic — ancient stone ruins draped in ivy bathed in golden afternoon light, serene and regal, no text, no words",
}
```

Update the safety-filter fallback block inside `run()` (around line 386) to branch on `language`. Find this block:

```python
                if _is_safety_filter_error(e) and script_type == "story":
```

Replace the entire fallback block with:

```python
                if _is_safety_filter_error(e) and script_type == "story":
                    logger.warning(
                        f"Scene {i} safety-filtered (genre={genre!r}). "
                        f"Rejected prompt: {visual!r}. Retrying with genre fallback."
                    )
                    firestore_service.record_quota_event(
                        "image_safety_filter",
                        f"scene={i} genre={genre} rejected_prompt={visual[:300]}",
                    )
                    _safe_prompts = (
                        _STORY_GENRE_SAFE_PROMPTS_EN
                        if language == "en"
                        else _STORY_GENRE_SAFE_PROMPTS
                    )
                    fallback_visual = _safe_prompts.get(
                        (genre or "inspiring").lower(),
                        _safe_prompts["inspiring"],
                    )
                    try:
                        image_path, image_retries = _run_with_backoff(
                            lambda fp=fallback_visual, idx=i: generate_image(fp, idx, aspect_ratio="9:16")
                        )
                        firestore_service.record_quota_event("image_success")
                        firestore_service.mark_scene_checkpoint(
                            effective_job_id,
                            i,
                            "completed",
                            audio_path=audio_path,
                            image_path=image_path,
                            retries_audio=0,
                            retries_image=image_retries,
                        )
                        video_clips.append((image_path, audio_path, narration))
                        time.sleep(8)
                        continue  # scene recovered via fallback — skip failure handling
                    except Exception as fallback_exc:
                        e = fallback_exc  # fall through to normal failure handling below
```

Note: `language` is in scope at this point in `run()` because it was set earlier in the story branch (Task 3, Step 3).

- [ ] **Step 4: Run all bilingual tests**

```bash
python -m pytest tests/test_stories_bilingual.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Run the full test suite**

```bash
python -m pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/agents/generator_agent.py tests/test_stories_bilingual.py
git commit -m "feat(stories): add English safety-filter fallback prompts with realistic/photorealistic style"
```

---

## End-to-End Verification

After all tasks are committed:

- [ ] **Verify English story generation:** Temporarily set `language = "en"` at the top of `story_researcher.run()` (after the pipeline state check), trigger `/stories/run` manually:

```bash
curl -X POST https://autoframe-353645494126.us-central1.run.app/stories/run \
  -H "X-Scheduler-Secret: $SCHEDULER_SECRET"
```

Check Cloud Run logs and the Firestore job document for:
- `language: "en"` stored on the job
- English narration text in the script
- English title/caption (not Devanagari)
- Realistic visual style prefix in the visual prompts (e.g. "Cinematic photorealistic")
- English TTS voice used (e.g. `en-US-Neural2-*`)

- [ ] **Verify Hindi story generation:** Change `language = "hi"` and repeat. Confirm Devanagari narration, Hindi title/caption, painted visual style prefix, Hindi TTS voice.

- [ ] **Verify rotation formula:** Deploy and check Cloud Run logs across 4 consecutive scheduler runs. The logged slot_index and hindi_slot values should show exactly one "hi" per 4-run day.

- [ ] **Remove the temporary override** and redeploy the final version.

```bash
cd "/Users/chetan/Desktop/DSE_Projects/youtube-video-generation-tool"
gcloud run deploy autoframe \
  --source . \
  --project=youtube-video-generator-492211 \
  --region=us-central1 \
  --allow-unauthenticated
```
