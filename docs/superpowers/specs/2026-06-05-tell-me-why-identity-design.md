# Tell Me Why — Channel Identity Redesign

**Date:** 2026-06-05
**Status:** Approved
**Scope:** `app/services/llm_service.py` only — no pipeline, Firestore, or workflow changes.

---

## Problem

The Tell Me Why channel lacks a distinct identity. Despite having a different script structure from the
news channel (4-scene "Tell me why" format), the content feels news-like because:

1. **Voice** — narration sounds journalistic, not like a curious friend sharing a wild fact.
2. **Topics** — `generate_fact_topic()` drifts toward trending/news-adjacent topics without an explicit
   "timeless only" constraint.
3. **Visuals** — `_STORY_VISUAL_STYLE_POOL_EN` is a pool of 4 different styles; every video looks
   different, so there is no recognizable brand aesthetic.

---

## Goal

Every Tell Me Why video should feel like a friend dropping a wild fact in a group chat — playful,
quirky, with a consistent visual identity that viewers recognize in 0.5 seconds.

Emotional arc per video: **wonder (hook) → insight (mechanism) → "wait, it gets weirder" → shareable punchline.**

---

## Changes

All changes are in `app/services/llm_service.py`. Three areas:

### 1. Script voice — `generate_script_with_search` (`script_mode="facts"`)

Rewrite the `system_instruction` block for `script_mode="facts"`. The 4-scene structure is preserved
but narration style rules are replaced:

**Scene roles (unchanged):**
- Scene 1 — Hook
- Scene 2 — Mechanism
- Scene 3 — Deeper Implication
- Scene 4 — Shareable Payoff

**New narration rules:**
- Voice: conversational friend — sounds like someone texting you a wild fact they just learned.
- Scene 1 hook: still starts with "Tell me why" but the following sentence must be casual, direct,
  and slightly incredulous. Max 12 words including "Tell me why".
- Scene 2: explain the "why" like you're telling a friend — no textbook phrasing, no passive voice,
  specific numbers delivered conversationally.
- Scene 3: the "wait, it gets weirder" beat — escalates the surprise.
- Scene 4: a quotable one-liner the viewer wants to screenshot or share. Lands the punchline.
- Banned: "Scientists have discovered", passive constructions, news-anchor phrasing,
  academic hedging ("it has been noted that...").
- Required: present tense, punchy sentences, first-person-adjacent delivery.

**New visual prompt rules (added to the facts system instruction):**
- Visuals must be absurd, literal interpretations of the fact — not abstract or symbolic.
- Example: for "bacteria outnumber human cells" → tiny cartoon bacteria in a crowd waving flags.
- Every scene's visual must match the Tell Me Why brand aesthetic (see Section 2).

### 2. Visual brand system — `_STORY_VISUAL_STYLE_POOL_EN` and `_STORY_GENRE_SAFE_PROMPTS_EN`

**`_STORY_VISUAL_STYLE_POOL_EN`**: Replace the pool of 4 different style strings with a single
canonical brand constant. No more `random.choice` — every Tell Me Why video gets the same art direction.

**New brand aesthetic (single string):**
> "Bright flat digital illustration, thick bold outlines, vivid complementary color palette
> (electric blue, warm yellow, coral red), expressive cartoonish characters with exaggerated
> reactions, slightly surreal visual metaphors, clean white or gradient background, high contrast,
> playful and quirky composition"

**`_STORY_GENRE_SAFE_PROMPTS_EN`**: Rewrite all 12 safety-filter fallback prompts to match the same
brand aesthetic. Currently they are generic storybook illustrations that don't look like the same channel.
Each fallback prompt should describe a playful, flat-illustration scene that fits the brand — not a
generic fairy-tale image.

### 3. Topic generation — `generate_fact_topic()`

Two changes to the prompt:

**Title format:** Change from "hook question OR shocking fact statement (academic framing)" to
casual, present-tense, slightly incredulous — sounds like something you'd say out loud.

New example titles in the prompt:
- "Your heartbreak is literally making your chest hurt — here's why"
- "Bananas are technically radioactive and you eat them anyway"
- "Your brain is actively hiding your nose from you right now"
- "The loudest animal on Earth is smaller than your thumbnail"

Old examples (to be removed): "Why do humans feel heartbreak as physical pain?",
"Ancient Romans used crushed mouse brains as toothpaste" — too academic/listicle.

**Timeless-only guardrail (new rule added to prompt):**
> "Do NOT generate topics about recent news events, trending topics, or anything time-sensitive.
> These must be timeless facts — just as surprising today as they were 10 years ago."

The `premise` field format is unchanged (1-2 sentences of factual context) but the style guide
is updated to match: direct, specific, no journalistic hedging.

---

## What Is Not Changing

- Pipeline, Firestore schema, GitHub Actions workflows — untouched.
- The 4-scene script structure — preserved.
- `_STORY_VISUAL_STYLE_POOL_HI` — Hindi channel visual styles not in scope.
- `_STORY_GENRE_SAFE_PROMPTS_EN` count — still 12 fallbacks, just rewritten.
- Music genre classification — unchanged.
- All other `llm_service.py` functions — unchanged.

---

## Files Touched

| File | Change |
|---|---|
| `app/services/llm_service.py` | Rewrite `generate_fact_topic` prompt, `generate_script_with_search` facts system instruction, `_STORY_VISUAL_STYLE_POOL_EN`, `_STORY_GENRE_SAFE_PROMPTS_EN` |

---

## Success Criteria

- Tell Me Why videos narrate in a casual, curious-friend voice — no news-anchor phrasing.
- Every video has the same recognizable flat-illustration visual style.
- Topics are timeless, counterintuitive facts — not trending news topics.
- Scene 4 closes with a quotable, shareable punchline, not a news summary.
