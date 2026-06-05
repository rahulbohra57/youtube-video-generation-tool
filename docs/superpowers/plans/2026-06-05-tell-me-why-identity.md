# Tell Me Why Channel Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Tell Me Why facts channel a distinct identity — playful/quirky brand visual, conversational-friend narration voice, and timeless topic guardrail — so videos no longer feel like news.

**Architecture:** All changes are prompt/constant rewrites inside two files: `app/services/llm_service.py` (visual style pools, script system instruction, topic generation prompt) and `app/agents/generator_agent.py` (safety fallback prompts). No pipeline, Firestore, or workflow changes.

**Tech Stack:** Python, Gemini 2.5 Flash via Vertex AI, pytest, `unittest.mock`

---

## Files

| File | What changes |
|---|---|
| `app/services/llm_service.py` | Replace `_FACT_VISUAL_STYLE_POOL_CINEMATIC` + `_FACT_VISUAL_STYLE_POOL_ILLUSTRATED` + `_CINEMATIC_CATEGORIES` with single `_TMW_VISUAL_STYLE` constant; simplify `_fact_visual_style()`; rewrite `script_mode="facts"` system instruction; rewrite `generate_fact_topic()` prompt |
| `app/agents/generator_agent.py` | Rewrite all 12 entries in `_STORY_GENRE_SAFE_PROMPTS_EN` to match brand aesthetic |
| `tests/test_tmw_identity.py` | New test file covering all four changes |

---

## Task 1: Brand visual constant

Replace the two category-split visual style pools and the `_CINEMATIC_CATEGORIES` set with a single
canonical brand string. Simplify `_fact_visual_style()` to always return it.

**Files:**
- Modify: `app/services/llm_service.py:79-118`
- Create: `tests/test_tmw_identity.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tmw_identity.py`:

```python
import pytest
from unittest.mock import patch, MagicMock


# ── Task 1 ──────────────────────────────────────────────────────────────────

def test_fact_visual_style_is_single_brand_string():
    """_fact_visual_style always returns the single brand constant."""
    from app.services import llm_service
    style_space = llm_service._fact_visual_style("science & space")
    style_psych = llm_service._fact_visual_style("psychology & dark psychology")
    assert style_space == style_psych, "All categories must share the same brand aesthetic"
    assert "flat" in style_space.lower(), "Brand style must be flat illustration"
    assert "electric blue" in style_space.lower(), "Brand palette must include electric blue"
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_tmw_identity.py::test_fact_visual_style_is_single_brand_string -v
```

Expected: FAIL — `_fact_visual_style` currently returns different strings per category.

- [ ] **Step 3: Replace the pools and simplify `_fact_visual_style()`**

In `app/services/llm_service.py`, replace lines 79–118 (the two pools, the category set, and the function) with:

```python
_TMW_VISUAL_STYLE = (
    "Bright flat digital illustration, thick bold outlines, vivid complementary color palette "
    "(electric blue, warm yellow, coral red), expressive cartoonish characters with exaggerated "
    "reactions, slightly surreal visual metaphors, clean white or gradient background, "
    "high contrast, playful and quirky composition"
)


def _fact_visual_style(category: str) -> str:
    return _TMW_VISUAL_STYLE
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_tmw_identity.py::test_fact_visual_style_is_single_brand_string -v
```

Expected: PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
pytest tests/ -q
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/llm_service.py tests/test_tmw_identity.py
git commit -m "feat: replace Tell Me Why visual style pools with single brand constant"
```

---

## Task 2: Facts script system instruction — conversational-friend voice

Rewrite the `script_mode="facts"` system instruction inside `generate_script_with_search()`.
The 4-scene structure is preserved; narration style rules and visual prompt rules change.

**Files:**
- Modify: `app/services/llm_service.py:292-308`
- Modify: `tests/test_tmw_identity.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tmw_identity.py`:

```python
# ── Task 2 ──────────────────────────────────────────────────────────────────

def test_facts_script_instruction_has_conversational_voice():
    """generate_script_with_search with script_mode='facts' sends conversational-friend rules."""
    from app.services import llm_service

    captured = {}

    mock_model = MagicMock()
    def _capture(prompt, **kw):
        captured["prompt"] = prompt
        resp = MagicMock()
        resp.text = '[{"scene":1,"narration":"Tell me why bananas are radioactive.","visual":"flat"}]'
        return resp
    mock_model.generate_content.side_effect = _capture

    with patch.object(llm_service, "_get_search_model", return_value=mock_model):
        try:
            llm_service.generate_script_with_search("bananas radioactive", script_mode="facts")
        except Exception:
            pass  # we only care about the captured prompt

    assert "prompt" in captured, "generate_content was not called"
    p = captured["prompt"]

    # New voice rules must be present
    assert "conversational friend" in p.lower(), "Must instruct conversational-friend voice"
    assert "wait, it gets weirder" in p.lower(), "Must describe scene 3 as escalation beat"
    assert "shareable" in p.lower(), "Must instruct shareable payoff for scene 4"

    # Old journalistic phrasing must be gone from the system instruction
    assert "mind-blowing facts" not in p, "Old journalistic opener must be removed"
    assert "scientists have discovered" not in p.lower(), "Banned phrase must be absent"
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_tmw_identity.py::test_facts_script_instruction_has_conversational_voice -v
```

Expected: FAIL — current instruction says "mind-blowing facts", no conversational-friend rules.

- [ ] **Step 3: Rewrite the `script_mode="facts"` system instruction**

In `app/services/llm_service.py`, replace the `if script_mode == "facts":` block (lines 292–309) with:

```python
    if script_mode == "facts":
        system_instruction = (
            "You are a scriptwriter for 'Tell Me Why', a YouTube Shorts channel where a curious "
            "friend shares wild, surprising facts — like texting someone something they won't believe. "
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_tmw_identity.py::test_facts_script_instruction_has_conversational_voice -v
```

Expected: PASS

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -q
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/llm_service.py tests/test_tmw_identity.py
git commit -m "feat: rewrite Tell Me Why script instruction with conversational-friend voice"
```

---

## Task 3: Topic generation prompt — casual titles + timeless guardrail

Rewrite the `generate_fact_topic()` prompt: replace academic title examples with casual
conversational ones, add an explicit timeless-only guardrail.

**Files:**
- Modify: `app/services/llm_service.py:851-897`
- Modify: `tests/test_tmw_identity.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tmw_identity.py`:

```python
# ── Task 3 ──────────────────────────────────────────────────────────────────

def test_fact_topic_prompt_has_timeless_guardrail_and_casual_titles():
    """generate_fact_topic sends a prompt with the timeless guardrail and casual example titles."""
    from app.services import llm_service

    captured = {}

    mock_model = MagicMock()
    def _capture(prompt, **kw):
        captured["prompt"] = prompt
        resp = MagicMock()
        resp.text = '{"title": "Your brain is hiding your nose from you right now", "premise": "The brain actively suppresses the image of your nose through a process called neural adaptation, filtering out constant stimuli to focus on novelty."}'
        return resp
    mock_model.generate_content.side_effect = _capture

    with patch.object(llm_service, "_get_model", return_value=mock_model):
        llm_service.generate_fact_topic("human body & biology")

    assert "prompt" in captured
    p = captured["prompt"]

    # Timeless guardrail must be present
    assert "timeless" in p.lower(), "Timeless guardrail must be in prompt"
    assert "recent news" in p.lower() or "trending" in p.lower(), (
        "Guardrail must explicitly ban news/trending topics"
    )

    # Casual title examples must be present; academic ones must be gone
    assert "radioactive" in p.lower() or "hiding your nose" in p.lower(), (
        "Casual example titles must appear in the prompt"
    )
    assert "ancient romans" not in p.lower(), "Old academic example must be removed"
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_tmw_identity.py::test_fact_topic_prompt_has_timeless_guardrail_and_casual_titles -v
```

Expected: FAIL — current prompt uses "Ancient Romans used crushed mouse brains", no timeless guardrail.

- [ ] **Step 3: Rewrite `generate_fact_topic()` prompt**

In `app/services/llm_service.py`, replace the `prompt = f"""..."""` string inside `generate_fact_topic()` (the block starting at line ~863) with:

```python
    prompt = f"""You are a researcher for a YouTube Shorts channel called "Tell Me Why" — where a curious friend shares wild, surprising facts, like texting someone something they won't believe.

Generate a specific, punchy fact topic for the category: {category.title()}{avoid_block}

Rules:
- title: 6-12 words, casual and conversational — sounds like something you'd say out loud to a friend. Must be a hook statement or rhetorical question. Examples:
  - "Your brain is actively hiding your nose from you right now"
  - "Bananas are technically radioactive and you eat them anyway"
  - "Your heartbreak is literally making your chest hurt — here's why"
  - "The loudest animal on Earth is smaller than your thumbnail"
  - "You share 60% of your DNA with a banana"
- premise: 1-2 sentences of factual context that the script writer can expand. Must include the core mechanism, number, or surprising detail. Minimum 15 words.
- Topic must be genuinely surprising or counterintuitive — avoid obvious or well-worn facts.
- IMPORTANT: Do NOT generate topics about recent news events, trending topics, or anything time-sensitive. These must be timeless facts — just as surprising today as they were 10 years ago and will be 10 years from now.
- Topic must be verifiable via Google Search.

Return only a valid JSON object, no markdown:
{{"title": "...", "premise": "..."}}"""
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_tmw_identity.py::test_fact_topic_prompt_has_timeless_guardrail_and_casual_titles -v
```

Expected: PASS

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -q
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/llm_service.py tests/test_tmw_identity.py
git commit -m "feat: rewrite generate_fact_topic prompt with casual titles and timeless guardrail"
```

---

## Task 4: Safety fallback prompts — brand alignment

Rewrite all 12 entries in `_STORY_GENRE_SAFE_PROMPTS_EN` in `generator_agent.py` to match
the Tell Me Why brand aesthetic (bright flat illustration, electric blue/yellow/coral palette,
cartoonish characters, playful and quirky). These are used when Imagen's safety filter rejects
the LLM-generated prompt.

**Files:**
- Modify: `app/agents/generator_agent.py:68-81`
- Modify: `tests/test_tmw_identity.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tmw_identity.py`:

```python
# ── Task 4 ──────────────────────────────────────────────────────────────────

def test_safe_prompts_match_brand_aesthetic():
    """All _STORY_GENRE_SAFE_PROMPTS_EN values must use the brand flat-illustration aesthetic."""
    from app.agents.generator_agent import _STORY_GENRE_SAFE_PROMPTS_EN

    assert len(_STORY_GENRE_SAFE_PROMPTS_EN) == 12, "Must still have exactly 12 fallback prompts"

    for category, prompt in _STORY_GENRE_SAFE_PROMPTS_EN.items():
        p = prompt.lower()
        assert "flat" in p, f"Category '{category}' safe prompt must use flat illustration style"
        assert any(color in p for color in ["electric blue", "blue", "yellow", "coral"]), (
            f"Category '{category}' safe prompt must reference brand palette"
        )
        assert "no text" in p, f"Category '{category}' safe prompt must include 'no text, no words'"
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_tmw_identity.py::test_safe_prompts_match_brand_aesthetic -v
```

Expected: FAIL — current safe prompts use watercolor/pencil sketch styles, not flat illustration.

- [ ] **Step 3: Rewrite `_STORY_GENRE_SAFE_PROMPTS_EN`**

In `app/agents/generator_agent.py`, replace lines 68–81 with:

```python
_STORY_GENRE_SAFE_PROMPTS_EN = {
    "science & space": (
        "Bright flat digital illustration, thick bold outlines, electric blue and warm yellow palette — "
        "a cartoon astronaut floating beside an oversized glowing planet with exaggerated wide eyes, "
        "playful and quirky composition, clean white background, no text, no words"
    ),
    "history & civilizations": (
        "Bright flat digital illustration, thick bold outlines, coral red and warm yellow palette — "
        "a cartoonish ancient explorer holding an oversized map with a look of total surprise, "
        "slightly surreal composition, clean white background, no text, no words"
    ),
    "human body & biology": (
        "Bright flat digital illustration, thick bold outlines, electric blue and coral red palette — "
        "a cartoon human silhouette with exaggeratedly large glowing organs and tiny cartoon cells "
        "waving flags inside, playful and quirky, clean white background, no text, no words"
    ),
    "technology & ai": (
        "Bright flat digital illustration, thick bold outlines, electric blue and warm yellow palette — "
        "a cartoonish robot with a giant lightbulb head wearing a graduation cap, exaggerated surprised "
        "expression, slightly surreal, clean gradient background, no text, no words"
    ),
    "health & fitness": (
        "Bright flat digital illustration, thick bold outlines, coral red and warm yellow palette — "
        "a cartoonish character doing an exaggerated stretch with tiny sweat drops flying everywhere, "
        "cheerful and quirky, clean white background, no text, no words"
    ),
    "psychology & dark psychology": (
        "Bright flat digital illustration, thick bold outlines, electric blue and coral red palette — "
        "a cartoon brain wearing sunglasses and smugly ignoring an oversized nose in the corner, "
        "absurd and playful composition, clean gradient background, no text, no words"
    ),
    "relationships & dating": (
        "Bright flat digital illustration, thick bold outlines, warm yellow and coral red palette — "
        "two cartoonish figures with exaggerated heart-eyes accidentally walking into each other, "
        "slightly surreal and funny, clean white background, no text, no words"
    ),
    "self-improvement & habits": (
        "Bright flat digital illustration, thick bold outlines, electric blue and warm yellow palette — "
        "a cartoon character planting a giant glowing seedling while standing on a stack of books, "
        "playful and quirky, clean white background, no text, no words"
    ),
    "business & finance": (
        "Bright flat digital illustration, thick bold outlines, coral red and warm yellow palette — "
        "a cartoonish figure surfing on an oversized rising graph with an exaggeratedly shocked "
        "expression, slightly surreal, clean gradient background, no text, no words"
    ),
    "culture & society": (
        "Bright flat digital illustration, thick bold outlines, electric blue and coral red palette — "
        "a group of cartoonish figures from different backgrounds each holding an oversized object "
        "representing their culture, playful mosaic composition, clean white background, no text, no words"
    ),
    "philosophy & life": (
        "Bright flat digital illustration, thick bold outlines, warm yellow and electric blue palette — "
        "a tiny cartoon figure sitting cross-legged on top of a giant question mark with a contemplative "
        "expression, slightly surreal, clean gradient background, no text, no words"
    ),
    "mysteries & unexplained": (
        "Bright flat digital illustration, thick bold outlines, electric blue and coral red palette — "
        "a cartoonish explorer shining a comically tiny flashlight at an enormous glowing door with "
        "exaggerated wide eyes, slightly surreal and quirky, clean dark gradient background, no text, no words"
    ),
}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_tmw_identity.py::test_safe_prompts_match_brand_aesthetic -v
```

Expected: PASS

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -q
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/agents/generator_agent.py tests/test_tmw_identity.py
git commit -m "feat: align Tell Me Why safety fallback prompts with brand aesthetic"
```

---

## Final verification

- [ ] Run the complete test suite one last time

```bash
pytest tests/ -v
```

Expected: all tests pass, no failures.
