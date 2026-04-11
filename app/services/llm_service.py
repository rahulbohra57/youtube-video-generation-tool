# app/services/llm_service.py

from vertexai.generative_models import GenerativeModel
import vertexai
import re
import json
import random

vertexai.init()

model = GenerativeModel("gemini-2.5-flash")

# Must match the subfolder names in assets/music/ exactly
_MUSIC_GENRES = ["Cheerful", "Happy", "News Bulletin", "Party", "Sad-Emotional", "Suspense"]

_LANG_INSTRUCTIONS = {
    "en": "Write the narration in English.",
    "hi": "Write the narration in Hindi (Devanagari script). Preserve all meaning accurately — do not simplify or lose nuance.",
}

_PROFANITY_PATTERNS = [
    r"\bfuck\b",
    r"\bfucking\b",
    r"\bshit\b",
    r"\bbitch\b",
    r"\basshole\b",
    r"\bdamn\b",
]

_COPYRIGHT_RISK_PATTERNS = [
    r"\bdisney\b",
    r"\bmarvel\b",
    r"\bpixar\b",
    r"\bstar wars\b",
    r"\bharry potter\b",
    r"\bpokemon\b",
    r"\bmickey\b",
    r"\bspider[- ]?man\b",
    r"\bbatman\b",
    r"\bsuperman\b",
    r"\bnetflix\b",
    r"\bapple logo\b",
    r"\bgoogle logo\b",
    r"\bbrand logo\b",
]

_NO_TEXT_VISUAL_SUFFIX = (
    " No text, no words, no letters, no numbers, no logos, no captions, "
    "no subtitles, no signs, no newspaper text, no watermark."
)

_VISUAL_STYLE_POOL = [
    "Cinematic 4K, dramatic side lighting, deep shadows, photorealistic",
    "Wide-angle cinematic shot, warm golden-hour lighting, photorealistic",
    "Overhead aerial perspective, cool blue tones, ultra-sharp 4K detail, photorealistic",
    "Close-up macro cinematic, shallow depth of field, soft bokeh background, photorealistic",
    "Dramatic low-angle shot, vibrant saturated colours, high contrast, cinematic 4K, photorealistic",
    "Documentary-style, natural soft lighting, gritty texture, ultra-realistic",
    "Futuristic neon-lit environment, deep blue and purple hues, cinematic 4K, photorealistic",
    "Epic wide establishing shot, overcast moody sky, high dynamic range, photorealistic",
]


def generate_script(topic: str, language: str = "en", aspect_ratio: str = "16:9", context: str = ""):
    from datetime import date
    today_str = date.today().isoformat()
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])

    if aspect_ratio == "9:16":
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Scene 1: open with the single most compelling fact or question — hook the viewer immediately\n"
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

    prompt = f"""
You are an expert scriptwriter for educational YouTube videos. Write a factually accurate video script on the headline below. The script must faithfully represent ALL angles in the headline and news context. Do NOT substitute your own interpretation of the topic or use general knowledge to override the provided context.

Topic: {topic}{context_block}

Return ONLY a valid JSON array. No markdown, no explanation, no code fences.

Each scene object must have:
- "scene": integer
- "narration": substantive narration text in the required language
- "visual": VERY DETAILED image generation prompt in English

NARRATION RULES — follow strictly:
- Write in simple, reader-friendly language (clear and natural; avoid jargon unless necessary).
- Write COMPLETE information. Never tease or leave a fact unresolved.
- Every sentence must teach something specific: include real figures, dates, mechanisms, or consequences where relevant.
- Ensure the topic adds practical value for the viewer (what happened, why it matters, and key takeaway).
- Do NOT use filler phrases like "let's explore", "stay tuned", "it's a game-changer", or "this is just the beginning".
- Do NOT summarise without substance — each narration must stand alone as a useful insight.
- Cover ALL key angles from the headline and news context. If the headline mentions multiple story elements (e.g. a main event AND a secondary detail), every element must appear somewhere in the script — typically scene 1 (hook) introduces the main angle and scene 3–4 covers the secondary detail.
- {lang_instruction}

VISUAL PROMPT RULES:
- Always write visual prompts in English, regardless of narration language.
- Every visual prompt MUST begin with this exact style prefix to keep all scenes visually consistent: "{video_style} — ". Apply it to every scene without exception.
- Do NOT depict or name any specific real individual (politician, celebrity, activist) unless they are a current sitting Prime Minister or President of a country. Use representative imagery instead (e.g. "a government official at a podium", "scientists in a lab").
- Do NOT request company logos, brand marks, app icons, or any readable text/labels in the image — Imagen cannot render text accurately. Use abstract or thematic imagery instead (e.g. instead of "Google logo", use "a colourful abstract search interface on a glowing screen").
- STRICT: Avoid text-bearing compositions like newspaper front pages, posters, billboards, screenshots, UI panels, signs, or subtitles.
- Be highly specific: lighting, composition, mood, style, camera angle.
- Avoid copyrighted fictional characters/franchises (e.g., superheroes, movie/cartoon characters, game mascots), trademarked logos, or branded products.

FACTUAL / COPYRIGHT SAFETY:
- TODAY'S DATE: {today_str}. Use this to determine verb tense. Events that occurred before today MUST be written in past tense ("launched", "announced", "was approved"). Do NOT write "will", "is expected to", "is set to", or "is scheduled to" for any event that has already taken place as of {today_str}. If uncertain whether an event has occurred, hedge with "reportedly" or "as of [date]" — never assume it is still upcoming.
- CRITICAL — YEARS AND DATES: When NEWS CONTEXT is provided above, ALL specific years and dates in the narration MUST come exclusively from that context. Do NOT use your training-data knowledge to supply a year or date that is not explicitly stated in the context. If the context does not mention a specific year for an event, do NOT guess or infer one — omit the year entirely or write "recently". This rule exists because your training data may describe an older planned version of the event (e.g. an earlier scheduled date) that has since changed. The provided context reflects the actual published date of the article and supersedes your prior knowledge.
- If context articles are provided above, prefer those facts. For any fact NOT in the provided context, only include it if you are certain it occurred and is not date-sensitive. Phrase uncertain claims as "reportedly", "according to reports", or "as of [year]".
- Do NOT fabricate specific dates, statistics, or event details. If uncertain, omit or hedge explicitly.
- Do not include direct quotes longer than 8 words from songs, books, movies, or articles.
- Do not include song lyrics.

Example visual prompt:
"Wide-angle cinematic shot of a modern data centre with rows of glowing blue server racks, cool blue-white lighting, shallow depth of field, photorealistic 3D render style"

Format:
[
  {{
    "scene": 1,
    "narration": "...",
    "visual": "..."
  }}
]

Additional format constraints:
{format_hint}
- Total spoken duration should be between 15 and 58 seconds (ideal 30-55 seconds).
- Maximum {max_scenes} scenes total
"""

    response = model.generate_content(prompt)
    return _response_text(response)


_search_model = None  # lazily initialised on first call


def _get_search_model():
    global _search_model
    if _search_model is None:
        from vertexai.generative_models import Tool, grounding as vgrounding
        search_tool = Tool.from_google_search_retrieval(vgrounding.GoogleSearchRetrieval())
        _search_model = GenerativeModel("gemini-2.5-flash", tools=[search_tool])
    return _search_model


def generate_script_with_search(topic: str, language: str = "en", aspect_ratio: str = "16:9", context: str = "") -> str:
    """Like generate_script() but with Gemini Google Search grounding enabled.

    Gemini will search the web for the topic before writing the script, ensuring
    the output reflects current facts rather than training-data knowledge.
    Falls back gracefully — callers should catch exceptions and fall back to
    generate_script() if this fails.
    """
    search_model = _get_search_model()

    from datetime import date
    today_str = date.today().isoformat()
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])

    if aspect_ratio == "9:16":
        format_hint = (
            "- MAXIMUM 5 scenes (target 45–55 seconds total when spoken at a natural pace — NEVER exceed 58 seconds)\n"
            "- Scene 1: open with the single most compelling fact or question — hook the viewer immediately\n"
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

    prompt = f"""
You are an expert scriptwriter for educational YouTube videos. Use your Google Search tool to look up the latest information about this headline, then write a factually accurate video script. The script must faithfully represent ALL angles in the headline and news context. Do NOT substitute outdated training-data knowledge when current search results are available.

CRITICAL: TODAY'S DATE is {today_str}. The NEWS CONTEXT below (if present) describes a RECENT event that occurred close to this date. When searching, look for the most recent version of this story — do NOT write about older events with similar topic names. If the context says "As of [date]", that is the event date to focus on.

Topic: {topic}{context_block}

Return ONLY a valid JSON array. No markdown, no explanation, no code fences.

Each scene object must have:
- "scene": integer
- "narration": substantive narration text in the required language
- "visual": VERY DETAILED image generation prompt in English

NARRATION RULES — follow strictly:
- Write in simple, reader-friendly language (clear and natural; avoid jargon unless necessary).
- Write COMPLETE information. Never tease or leave a fact unresolved.
- Every sentence must teach something specific: include real figures, dates, mechanisms, or consequences where relevant.
- Ensure the topic adds practical value for the viewer (what happened, why it matters, and key takeaway).
- Do NOT use filler phrases like "let's explore", "stay tuned", "it's a game-changer", or "this is just the beginning".
- Do NOT summarise without substance — each narration must stand alone as a useful insight.
- Cover ALL key angles from the headline and news context. If the headline mentions multiple story elements (e.g. a main event AND a secondary detail), every element must appear somewhere in the script — typically scene 1 (hook) introduces the main angle and scene 3–4 covers the secondary detail.
- {lang_instruction}

VISUAL PROMPT RULES:
- Always write visual prompts in English, regardless of narration language.
- Every visual prompt MUST begin with this exact style prefix to keep all scenes visually consistent: "{video_style} — ". Apply it to every scene without exception.
- Do NOT depict or name any specific real individual (politician, celebrity, activist) unless they are a current sitting Prime Minister or President of a country. Use representative imagery instead (e.g. "a government official at a podium", "scientists in a lab").
- Do NOT request company logos, brand marks, app icons, or any readable text/labels in the image — Imagen cannot render text accurately. Use abstract or thematic imagery instead (e.g. instead of "Google logo", use "a colourful abstract search interface on a glowing screen").
- STRICT: Avoid text-bearing compositions like newspaper front pages, posters, billboards, screenshots, UI panels, signs, or subtitles.
- Be highly specific: lighting, composition, mood, style, camera angle.
- Avoid copyrighted fictional characters/franchises (e.g., superheroes, movie/cartoon characters, game mascots), trademarked logos, or branded products.

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
    "visual": "..."
  }}
]

Additional format constraints:
{format_hint}
- Total spoken duration should be between 15 and 58 seconds (ideal 30-55 seconds).
- Maximum {max_scenes} scenes total
"""

    response = search_model.generate_content(prompt)
    return _response_text(response)


def _response_text(response) -> str:
    """Extract text safely even when SDK returns multiple content parts."""
    try:
        text = response.text
        if text:
            return text
    except Exception:
        pass

    chunks: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            txt = getattr(part, "text", None)
            if txt:
                chunks.append(txt)
    if chunks:
        return "\n".join(chunks).strip()

    raise RuntimeError("Model response did not contain text output.")


def strip_markdown_formatting(text: str) -> str:
    """Remove markdown bold/italic markers Gemini sometimes inserts into narrations."""
    out = text or ""
    out = re.sub(r'\*\*(.+?)\*\*', r'\1', out)  # **bold** → bold
    out = re.sub(r'\*(.+?)\*', r'\1', out)        # *italic* → italic
    out = re.sub(r'__(.+?)__', r'\1', out)         # __bold__ → bold
    out = re.sub(r'_(.+?)_', r'\1', out)           # _italic_ → italic
    return out


def sanitize_profanity(text: str) -> str:
    out = text or ""
    for pattern in _PROFANITY_PATTERNS:
        out = re.sub(pattern, "[censored]", out, flags=re.IGNORECASE)
    return out


def sanitize_copyright_risks(text: str) -> str:
    out = text or ""
    for pattern in _COPYRIGHT_RISK_PATTERNS:
        out = re.sub(pattern, "generic public-domain style", out, flags=re.IGNORECASE)
    return out


def sanitize_visual_prompt_no_text(text: str) -> str:
    out = text or ""
    # Replace terms that frequently cause gibberish text in generated images.
    out = re.sub(
        r"\b(text|headline|caption|subtitle|newspaper|poster|billboard|banner|placard|sign|logo|watermark|screenshot|ui|typography|lettering)\b",
        "abstract visual element",
        out,
        flags=re.IGNORECASE,
    )
    out = out.strip()
    if _NO_TEXT_VISUAL_SUFFIX.lower() not in out.lower():
        out = f"{out}{_NO_TEXT_VISUAL_SUFFIX}".strip()
    return out


def _scene_list_to_json_prompt(scenes: list[dict]) -> str:
    return json.dumps(scenes, ensure_ascii=False)


def _extract_json_object(text: str) -> dict:
    text = re.sub(r"```json|```", "", text or "")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(match.group())


def fact_check_scenes(topic: str, scenes: list[dict], language: str = "en", context: str = "") -> list[dict]:
    """Run a fast fact-check + safety rewrite pass while preserving structure."""
    if not scenes:
        return scenes

    from datetime import date
    today_str = date.today().isoformat()
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])
    context_block = (
        f"\nSOURCE CONTEXT (authoritative — all dates/years must match this):\n{context.strip()}\n"
        if context and context.strip()
        else ""
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
6) Keep visual prompts in English and remove risky copyright/trademark references.
7) Remove profanity and offensive wording.
8) Ensure visuals contain zero readable text.

Topic: {topic}
Language rule: {lang_instruction}

Return ONLY valid JSON array with objects: scene, narration, visual.

Input scenes:
{_scene_list_to_json_prompt(scenes)}
"""
    try:
        response = model.generate_content(prompt)
        from app.utils.helpers import extract_json
        checked = extract_json(_response_text(response))
        if isinstance(checked, list) and checked:
            return checked
    except Exception:
        pass
    return scenes


def apply_quality_controls(topic: str, scenes: list[dict], language: str = "en", context: str = "") -> list[dict]:
    """Apply fact-check + profanity + copyright sanitization."""
    reviewed = fact_check_scenes(topic, scenes, language=language, context=context)
    cleaned = []
    for s in reviewed:
        narration = strip_markdown_formatting(str(s.get("narration", "")))
        narration = sanitize_profanity(narration)
        narration = sanitize_copyright_risks(narration)
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


def classify_music_genre(topic: str) -> str:
    """Return the best matching genre folder name for a topic, or 'general'."""
    genre_list = ", ".join(_MUSIC_GENRES)
    prompt = f"""You are a music curator. Given a video topic, pick the single most fitting background music mood.

Available moods: {genre_list}, General

Topic: "{topic}"

Reply with ONLY the single mood name from the list above. Nothing else."""

    try:
        response = model.generate_content(prompt)
        genre = _response_text(response).strip().strip('"').strip("'")
        return genre if genre in _MUSIC_GENRES else "general"
    except Exception:
        return "general"


_STORY_MOODS = [
    "inspiring",
    "comedy",
    "heartfelt",
    "crime",
    "action",
    "sci-fi",
    "mythology",
    "thriller",
    "mystery",
    "adventure",
    "slice-of-life",
    "historical",
]

_STORY_VISUAL_STYLE = (
    "Soft watercolor illustration, warm earthy palette, gentle diffused lighting, "
    "painterly texture, no text, no words, no letters"
)


def generate_story_idea(recently_used_titles: list[str] | None = None, preferred_mood: str = "") -> dict:
    """Generate a fresh Hindi moral story concept. Returns {title, mood, premise} all in Hindi."""
    avoid_block = ""
    if recently_used_titles:
        lines = "\n".join(f"  - {t}" for t in recently_used_titles[:20])
        avoid_block = f"\nइन कहानियों को दोबारा मत बनाओ (हाल में बनाई गई):\n{lines}\n"

    preferred = (preferred_mood or "").strip().lower()
    if preferred and preferred not in _STORY_MOODS:
        preferred = ""
    mood_list = ", ".join(_STORY_MOODS)
    preferred_rule = (
        f"\n- mood MUST be exactly: {preferred}"
        if preferred
        else f"\n- mood इनमें से एक हो: {mood_list}"
    )
    prompt = f"""तुम एक रचनात्मक Hindi कहानीकार हो जो YouTube Shorts के लिए छोटी नैतिक कहानियाँ लिखते हो।

एक बिल्कुल नई, मौलिक कहानी का विचार दो। कहानी 45-55 सेकंड में पूरी होनी चाहिए।{avoid_block}

नियम:
- शीर्षक (title) 5-8 शब्दों का हो, जिज्ञासा जगाने वाला हो
{preferred_rule}
- premise एक वाक्य में हो: कौन + क्या चुनौती + कौन सी सीख

सिर्फ एक valid JSON object return करो, कोई markdown नहीं:
{{"title": "...", "mood": "...", "premise": "..."}}"""

    try:
        response = model.generate_content(prompt)
        result = _extract_json_object(_response_text(response))
        if result.get("title") and result.get("mood") and result.get("premise"):
            return result
    except Exception:
        pass
    # Fallback
    import random as _random
    return {
        "title": "एक छोटी सी मेहनत, बड़ा बदलाव",
        "mood": preferred or _random.choice(_STORY_MOODS),
        "premise": "एक बच्चा छोटी सी कोशिश से बड़ा सपना पूरा करता है।",
    }


def generate_story_script(title: str, mood: str, premise: str = "") -> str:
    """Generate a 3-scene Hindi moral story script for YouTube Shorts (<1 min).

    Returns JSON array with scene/narration/visual keys (same format as generate_script).
    Narrations are in Hindi (Devanagari). Visual prompts are in English for Imagen.
    """
    premise_block = f"\nकहानी का सार: {premise}" if premise else ""

    prompt = f"""तुम एक YouTube Shorts के लिए Hindi में छोटी नैतिक कहानी लिखने वाले scriptwriter हो।

कहानी: {title}{premise_block}
मूड: {mood}

3 दृश्य (scenes) लिखो। कुल अवधि 45-55 सेकंड होनी चाहिए।

हर scene में:
- "narration": हिंदी में (देवनागरी लिपि), 18-22 शब्द, बोलने में स्वाभाविक
- "visual": अंग्रेज़ी में (Imagen के लिए), बहुत विस्तृत image generation prompt

Scene structure:
- Scene 1 (शुरुआत ~15s): पात्र और उसकी चुनौती का परिचय। दर्शक को बाँधे।
- Scene 2 (मोड़ ~15s): निर्णायक क्षण — पात्र एक महत्वपूर्ण कदम उठाता है।
- Scene 3 (अंत ~15s): परिणाम और एक काव्यात्मक नैतिक वाक्य।

NARRATION नियम:
- हिंदी (देवनागरी) में लिखो — Roman script नहीं
- 18-22 शब्द प्रति scene — इससे अधिक नहीं
- "एक बार की बात है" से शुरू मत करो
- Scene 1-2 में सीधे नैतिक उपदेश मत दो — कार्य के ज़रिए दिखाओ
- सरल, बोधगम्य भाषा

VISUAL PROMPT नियम:
- अंग्रेज़ी में लिखो
- इस style prefix से शुरू करो: "{_STORY_VISUAL_STYLE} — "
- कोई असली व्यक्ति, धार्मिक प्रतीक, copyright characters नहीं
- प्रकृति, गाँव, जंगल, नदी जैसी settings को प्राथमिकता दो
- No text, no words, no signs in the image

सिर्फ valid JSON array return करो, कोई markdown नहीं:
[
  {{"scene": 1, "narration": "...", "visual": "..."}},
  {{"scene": 2, "narration": "...", "visual": "..."}},
  {{"scene": 3, "narration": "...", "visual": "..."}}
]"""

    response = model.generate_content(prompt)
    return _response_text(response)


def rate_and_select_news(
    articles: list[dict],
    top_performers: list[dict] | None = None,
    recently_covered: list[str] | None = None,
) -> list[dict]:
    from datetime import date
    today_str = date.today().isoformat()

    articles_text = "\n".join(
        f"{i + 1}. [published: {a.get('published_at', 'unknown')}] {a['headline']}"
        + (f" — {a['description']}" if a.get("description") else "")
        for i, a in enumerate(articles)
    )

    performers_block = ""
    if top_performers:
        lines = "\n".join(
            f"  - \"{p['topic']}\" ({p['view_count']} views, genre: {p.get('genre', 'N/A')})"
            for p in top_performers
        )
        performers_block = (
            f"\nFor reference, these stories performed best on this channel recently:\n{lines}\n"
            "Prefer stories with similar themes, depth, or audience appeal.\n"
        )

    fatigue_block = ""
    if recently_covered:
        lines = "\n".join(f"  - {h}" for h in recently_covered)
        fatigue_block = (
            f"\nCONTENT FATIGUE — these topics were already covered in the last 14 days. "
            f"Heavily penalise any article that covers the same event, story, or angle as one below. "
            f"A fresh development on the same story is acceptable only if it introduces genuinely new facts:\n{lines}\n"
        )

    prompt = f"""You are a senior news editor selecting stories for short educational videos.
TODAY'S DATE: {today_str}. Only select stories that are genuinely recent as of today. Penalise articles about events that have fully concluded weeks ago with no new angle.

Rate each article on a combined 1–5 scale using these criteria:
- Virality & public interest (will people share this?)
- Educational value (does it teach something specific and useful?)
- Freshness (is this breaking or very recent news as of {today_str}?)
- Story depth (is there a concrete fact, number, or consequence — not just a vague headline?)
- Content fatigue: heavily penalise stories already covered recently (see list below if present)
{performers_block}{fatigue_block}
Select the top 5 articles. For each, write a 2–3 sentence context summary that captures ALL key facts, angles, and notable details — including surprising, unusual, or quirky elements that make the story interesting. ALWAYS begin the context with the publication date (e.g. "As of April 7, 2026, ...") so the script writer knows exactly when this event occurred. Do NOT drop secondary details; they may be the most viral element. This summary will be used directly to write the video script.

Return ONLY a valid JSON array, no markdown, no explanation:
[{{"headline": "...", "context": "As of [date], ALL key facts and angles including secondary/unusual details.", "rating": 4.5}}, ...]

Articles:
{articles_text}"""
    response = model.generate_content(prompt)
    from app.utils.helpers import extract_json
    return extract_json(_response_text(response))


def enhance_caption(caption: str) -> str:
    prompt = f"""Improve this YouTube Shorts caption.
Add a strong hook as the very first line.
Add an engaging closing line asking viewers to like and subscribe.
Keep all existing hashtags. Return plain text only, no markdown.
Formatting rules:
- Keep clean spacing between sentences.
- Use 2 short paragraphs max before hashtags.
- Put all hashtags on one final line separated by spaces.

Caption:
{caption}"""
    response = model.generate_content(prompt)
    return format_caption_for_youtube(_response_text(response).strip())


def format_caption_for_youtube(text: str) -> str:
    """Normalize spacing and hashtag layout for YouTube description field."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in raw.split("\n")]
    lines = [ln for ln in lines if ln]

    non_tags = []
    tags = []
    for ln in lines:
        if ln.startswith("#"):
            tags.extend([tok for tok in ln.split() if tok.startswith("#")])
        else:
            non_tags.append(ln)

    body = " ".join(non_tags).strip()
    body = re.sub(r"\s+", " ", body)
    body = re.sub(r"\s+([,.!?;:])", r"\1", body)

    unique_tags = []
    seen = set()
    for t in tags:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_tags.append(t)
    hashtag_line = " ".join(unique_tags[:15]).strip()

    if body and hashtag_line:
        return f"{body}\n\n{hashtag_line}"
    if hashtag_line:
        return hashtag_line
    return body


def generate_shorts_caption(topic: str, language: str = "en") -> str:
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])

    prompt = f"""
Write a YouTube Shorts caption for a video about: {topic}

{lang_instruction}

Structure:
1. HOOK LINE — one punchy, curiosity-triggering opening sentence (a bold claim, shocking fact, or provocative question)
2. BODY — 1-2 sentences that tease what the viewer will learn or see
3. CALL TO ACTION — one engaging closing line that invites a reaction (e.g. "Drop your thoughts below 👇", "Follow for more 🔥", "Share this if it blew your mind")
4. HASHTAGS — 10-15 relevant hashtags on their own line (mix of broad popular and niche-specific)

Rules:
- Plain text only. Absolutely NO markdown — no asterisks (*), no bold (**), no underscores (_), no backticks, no headers (#)
- No extra labels, commentary, or JSON
- Keep language simple and readable.
- Return ONLY the caption lines followed by the hashtags
"""

    response = model.generate_content(prompt)
    return _response_text(response).strip()


def review_script_with_senior_reviewer(
    topic: str,
    scenes: list[dict],
    language: str = "en",
    min_seconds: int = 15,
    max_seconds: int = 58,
) -> list[dict]:
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])
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
- Keep each object fields: scene, narration, visual.
- Visual prompts must remain in English.
- Voiceover total duration must be between {min_seconds} and {max_seconds} seconds.
- Keep script natural for narration and captions to stay in sync.
- Avoid overly complex words.

Topic: {topic}
Language: {lang_instruction}

Input scenes:
{_scene_list_to_json_prompt(scenes)}
"""
    try:
        response = model.generate_content(prompt)
        from app.utils.helpers import extract_json
        reviewed = extract_json(_response_text(response))
        if isinstance(reviewed, list) and reviewed:
            return reviewed
    except Exception:
        pass
    return scenes


_TITLE_CAPTION_LANG_INSTRUCTIONS = {
    "en": "Write the title and caption in English.",
    "hi": "Write the title and caption in Hindi (Devanagari script). Do NOT use English for the title or caption body.",
}


def review_title_and_caption_with_senior_reviewer(
    topic: str,
    scenes: list[dict],
    language: str = "en",
    genre: str = "",
) -> dict:
    title_caption_lang = _TITLE_CAPTION_LANG_INSTRUCTIONS.get(language, _TITLE_CAPTION_LANG_INSTRUCTIONS["en"])
    genre_hint = f" Focus hashtags around the {genre} domain." if genre else ""
    prompt = f"""
You are a senior script reviewer.
Create:
1) A catchy but non-clickbait YouTube Shorts title — include the subject's name or key identifier if present in the script.
2) A reader-friendly caption aligned with the voiceover script (same core points), preserving all specific names, numbers, and facts.
3) 10-15 relevant hashtags derived from the script content and topic — mix broad popular tags with niche-specific ones.{genre_hint}

Rules:
- Keep language simple and natural.
- Caption body must be insightful, complete, and engaging (2 short paragraphs max).
- CRITICAL: Include every key specific detail from the script — names of people, places, organizations, ages, numbers, dates, and any other concrete facts. A caption missing a person's name or other key detail is unacceptable.
- Do not use vague placeholders like "a grandpa", "a man", "a woman", "a company" when the script contains their actual name or identity.
- Do not exaggerate or promise things not in script.
- Always include #Shorts and #YouTubeShorts in the hashtags.
- Return ONLY a valid JSON object with keys: title, caption, hashtags (array of strings).
- Language rule: {title_caption_lang}

Topic: {topic}
Script scenes:
{_scene_list_to_json_prompt(scenes)}
"""
    try:
        response = model.generate_content(prompt)
        payload = _extract_json_object(_response_text(response))
        title = strip_markdown_formatting((payload.get("title") or "").strip())
        caption_body = strip_markdown_formatting((payload.get("caption") or "").strip())
        hashtags = payload.get("hashtags") or []
        if isinstance(hashtags, list):
            hashtag_line = " ".join(
                h if h.startswith("#") else f"#{h}" for h in hashtags[:15]
            )
        else:
            hashtag_line = ""
        caption = f"{caption_body}\n\n{hashtag_line}".strip() if hashtag_line else caption_body
        if title or caption:
            return {"title": title, "caption": caption}
    except Exception:
        pass
    return {"title": topic.strip(), "caption": generate_shorts_caption(topic, language=language)}
