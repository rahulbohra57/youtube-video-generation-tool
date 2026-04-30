# app/services/llm_service.py

from vertexai.generative_models import GenerativeModel
import vertexai
import re
import json
import random
import logging

logger = logging.getLogger(__name__)

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


def _extract_article_year(context: str) -> int | None:
    """Extract publication year from context string (from [Article published: YYYY-...] or 'As of Month D, YYYY')."""
    m = re.search(r'\[Article published: (\d{4})-', context)
    if m:
        return int(m.group(1))
    m = re.search(r'As of \w+ \d+,\s*(\d{4})', context)
    if m:
        return int(m.group(1))
    return None


def _enforce_article_year(scenes: list[dict], context: str) -> list[dict]:
    """Deterministically replace years in narrations that are implausibly far from the article year.

    Catches cases where the LLM reviewer fails to remove hallucinated years sourced from
    training data (e.g. writing '2024' for an article published in 2026).
    Allows the publication year ±1 to cover events that started the previous year.
    """
    article_year = _extract_article_year(context)
    if not article_year:
        return scenes
    result = []
    for scene in scenes:
        narration = scene.get("narration", "")
        def _replace(m: re.Match) -> str:
            y = int(m.group(0))
            return m.group(0) if abs(y - article_year) <= 1 else "recently"
        result.append({**scene, "narration": re.sub(r'\b(19|20)\d{2}\b', _replace, narration)})
    return result


def _build_context_block(context: str, label: str = "NEWS CONTEXT — primary source of truth. The script MUST cover ALL angles and facts below. Do not omit any element") -> str:
    """Wrap context with a label and a prominent date anchor when a publication year is present."""
    if not context or not context.strip():
        return ""
    article_year = _extract_article_year(context)
    year_anchor = (
        f"\n⚠️ DATE ANCHOR: This article was published in {article_year}. "
        f"Every specific year you write in the narration MUST be {article_year} or a year explicitly present in the context below. "
        f"Do NOT write any other year — if unsure, omit it or write 'recently'."
    ) if article_year else ""
    return f"\n{label}:{year_anchor}\n{context.strip()}\n"


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

    context_block = _build_context_block(context)
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
- CRITICAL — VISUAL SAFETY: Visual prompts must NEVER depict violence, weapons, blood, physical harm, or injury, even for news stories about such events. Use symbolic or abstract representations instead — for example: a broken chain for conflict, a gavel for law/justice, a city skyline for politics, a shield for protection, a first-aid cross for medical events. Imagen will reject prompts containing violent or harmful imagery.

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
_SEARCH_MODEL_CANDIDATES = (
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
)
_search_grounding_disabled = False


class SearchGroundingUnavailable(RuntimeError):
    """Raised when Vertex search grounding is unavailable for all candidate models."""
    pass


def _init_search_model(model_name: str):
    from vertexai.generative_models import Tool, grounding as vgrounding
    search_tool = Tool.from_google_search_retrieval(vgrounding.GoogleSearchRetrieval())
    return GenerativeModel(model_name, tools=[search_tool])


def _get_search_model(candidate_index: int = 0):
    global _search_model
    if _search_model is None:
        model_name = _SEARCH_MODEL_CANDIDATES[min(candidate_index, len(_SEARCH_MODEL_CANDIDATES) - 1)]
        _search_model = _init_search_model(model_name)
    return _search_model


def _is_model_not_found_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "404" in msg
        or "not found" in msg
        or "publishers/google/models/" in msg
    )


def generate_script_with_search(topic: str, language: str = "en", aspect_ratio: str = "16:9", context: str = "") -> str:
    """Like generate_script() but with Gemini Google Search grounding enabled.

    Gemini will search the web for the topic before writing the script, ensuring
    the output reflects current facts rather than training-data knowledge.
    Falls back gracefully — callers should catch exceptions and fall back to
    generate_script() if this fails.
    """
    global _search_grounding_disabled
    if _search_grounding_disabled:
        raise SearchGroundingUnavailable(
            "Search grounding is unavailable for this project/region. Falling back to standard generation."
        )

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

    context_block = _build_context_block(context)
    video_style = random.choice(_VISUAL_STYLE_POOL)

    prompt = f"""
You are an expert scriptwriter for educational YouTube videos. Use your Google Search tool to look up the latest information about this headline, then write a factually accurate video script. The script must faithfully represent ALL angles in the headline and news context. Do NOT substitute outdated training-data knowledge when current search results are available.

CRITICAL: TODAY'S DATE is {today_str}. The NEWS CONTEXT below (if present) describes a RECENT event that occurred close to this date. When searching, look for the most recent version of this story — do NOT write about older events with similar topic names. If the context says "As of [date]", that is the event date to focus on. If your search returns an older similar story, IGNORE IT — the video is about the specific event described in the NEWS CONTEXT below.

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
- CRITICAL — VISUAL SAFETY: Visual prompts must NEVER depict violence, weapons, blood, physical harm, or injury, even for news stories about such events. Use symbolic or abstract representations instead — for example: a broken chain for conflict, a gavel for law/justice, a city skyline for politics, a shield for protection, a first-aid cross for medical events. Imagen will reject prompts containing violent or harmful imagery.

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

    last_exc: Exception | None = None
    not_found_count = 0
    for idx, model_name in enumerate(_SEARCH_MODEL_CANDIDATES):
        search_model = _get_search_model(candidate_index=idx)
        logger.info("generate_script_with_search using model: %s", model_name)
        try:
            response = search_model.generate_content(prompt)
            return _response_text(response)
        except Exception as exc:
            last_exc = exc
            if _is_model_not_found_error(exc):
                not_found_count += 1
                logger.warning(
                    "Search model '%s' unavailable for grounding: %s. Trying fallback model.",
                    model_name,
                    exc,
                )
                global _search_model
                _search_model = None
                continue
            logger.warning("generate_script_with_search failed on model '%s': %s", model_name, exc)
            break
    if not_found_count == len(_SEARCH_MODEL_CANDIDATES):
        _search_grounding_disabled = True
        raise SearchGroundingUnavailable(
            "Search grounding is unavailable for this project/region. Falling back to standard generation."
        )
    raise last_exc  # type: ignore[misc]


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
    context_block = _build_context_block(context, label="SOURCE CONTEXT (authoritative — all dates/years must match this)")
    prompt = f"""
You are a strict fact-check and policy safety editor for short educational videos.

TODAY'S DATE: {today_str}{context_block}

Task:
1) Review each scene's narration for likely factual errors, overclaims, or missing caution.
2) Flag and correct any claim that presents a past event (more than a few weeks ago) as if it just happened or is "breaking news".
3) CRITICAL — DATE & YEAR HALLUCINATION CHECK: Identify every specific year AND every specific date (e.g. "April 27th", "March 3rd") mentioned in the narrations. The DATE ANCHOR above states the article's publication year. Any year in the narration that differs from the DATE ANCHOR year (unless explicitly present in the SOURCE CONTEXT) was fabricated — remove it or replace with "recently". Any specific day-month combination that contradicts the SOURCE CONTEXT must also be corrected or removed. Training data often contains older similar events with different dates — the SOURCE CONTEXT is always authoritative.
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
        if isinstance(checked, list) and len(checked) >= len(scenes):
            return checked
    except Exception:
        pass
    return scenes


def apply_quality_controls(topic: str, scenes: list[dict], language: str = "en", context: str = "", skip_fact_check: bool = False) -> list[dict]:
    """Apply fact-check + profanity + copyright sanitization.

    skip_fact_check=True skips the news-oriented fact_check_scenes LLM pass — appropriate
    for story scripts where fiction shouldn't be treated as news claims.
    """
    reviewed = scenes if skip_fact_check else fact_check_scenes(topic, scenes, language=language, context=context)
    if not skip_fact_check and context:
        reviewed = _enforce_article_year(reviewed, context)
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


_STORY_GENRE_TO_MUSIC = {
    "inspiring": "Cheerful",
    "comedy": "Happy",
    "heartfelt": "Sad-Emotional",
    "crime": "Suspense",
    "action": "Suspense",
    "sci-fi": "Suspense",
    "mythology": "Sad-Emotional",
    "thriller": "Suspense",
    "mystery": "Suspense",
    "adventure": "Happy",
    "slice-of-life": "Happy",
    "historical": "Sad-Emotional",
}

_NEWS_KEYWORD_TO_MUSIC = {
    "Suspense": ["crime", "fraud", "hack", "scam", "murder", "scandal", "arrest", "stolen", "terror", "attack", "lawsuit", "ban"],
    "Sad-Emotional": ["death", "dies", "dead", "tragedy", "disaster", "flood", "earthquake", "crash", "killed", "victim", "mourning"],
    "Cheerful": ["award", "record", "wins", "victory", "celebrate", "breakthrough", "milestone", "innovation", "launch", "festival"],
    "Happy": ["sport", "cricket", "football", "tournament", "entertainment", "film", "movie", "concert", "comedy"],
    "Party": ["viral", "trending", "meme", "social media"],
}


def classify_music_genre(topic: str, story_genre: str = "") -> str:
    """Return the best matching music folder name using a keyword lookup. No LLM call."""
    if story_genre and story_genre.lower() in _STORY_GENRE_TO_MUSIC:
        return _STORY_GENRE_TO_MUSIC[story_genre.lower()]
    lower = topic.lower()
    for music_genre, keywords in _NEWS_KEYWORD_TO_MUSIC.items():
        if any(kw in lower for kw in keywords):
            return music_genre
    return "News Bulletin"


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

_CTA_NEWS = [
    "Subscribe now for daily breaking news updates.",
    "Stay informed — hit Subscribe and never miss a headline.",
    "For the latest news first, Subscribe now.",
    "Like, Share and Subscribe for real-time news alerts.",
    "Join our community for trusted news every day.",
    "Subscribe now and stay ahead of the story.",
    "Get facts, updates, and breaking news — Subscribe today.",
    "Turn on notifications for instant news updates.",
    "Stay connected to what matters — Subscribe now.",
    "Your daily news starts here — Subscribe today.",
    "Don't miss the next big update — Subscribe now.",
    "Like this video and Subscribe for more news coverage.",
    "Stay aware, stay updated, stay subscribed.",
    "For unbiased news and quick updates, Subscribe now.",
    "Be the first to know — hit Subscribe now.",
    "More updates in 60 seconds — Subscribe now.",
    "Fast news, real facts — Subscribe today.",
    "One minute news daily — Follow and Subscribe.",
    "Quick headlines daily — Join now.",
    "Stay updated in under a minute — Subscribe.",
]

_CTA_STORIES_EN = [
    "Subscribe for a new story every day.",
    "Love stories? Hit Subscribe now.",
    "Stay till the end for the next amazing story.",
    "New twists and endings daily — Subscribe now.",
    "Enter the world of stories — Subscribe today.",
    "One minute stories, endless emotions — Subscribe.",
    "Don't miss tomorrow's story — Subscribe now.",
    "Like, Share and Subscribe for more powerful stories.",
    "Every story has a lesson — Subscribe for more.",
    "Ready for the next twist? Subscribe now.",
    "Stories that stay with you — Subscribe today.",
    "Turn on notifications for daily story drops.",
    "Subscribe now for mystery, horror and emotional stories.",
    "A new ending awaits you — Subscribe now.",
]

_CTA_STORIES_HI = [
    "रोज़ नई कहानियों के लिए अभी Subscribe करें।",
    "अगली कहानी मिस मत करना — Subscribe now।",
    "हर दिन नई ट्विस्ट वाली कहानी देखने के लिए Subscribe करें।",
    "1 मिनट की शानदार कहानियों के लिए चैनल Subscribe करें।",
    "अगला जबरदस्त अंत देखने के लिए जुड़े रहें।",
    "Like, Share और Subscribe करना मत भूलिए।",
    "हर कहानी में छुपी है एक सीख — Subscribe करें।",
    "नई कहानी, नया एहसास — रोज़ देखें।",
    "अगर कहानी पसंद आई हो तो Subscribe करें।",
    "रोज़ाना मनोरंजक कहानियों के लिए Bell Icon दबाएं।",
    "अगली कहानी का ट्विस्ट आपको चौंका देगा — Subscribe करें।",
]

def get_cta_narration(channel_id: str = "news", language: str = "en") -> str:
    """Return a randomly chosen CTA narration string. No visual — caller reuses last frame."""
    if channel_id == "stories":
        pool = _CTA_STORIES_HI if language == "hi" else _CTA_STORIES_EN
    else:
        pool = _CTA_NEWS
    return random.choice(pool)


def _is_premise_adequate(premise: str) -> bool:
    """Quality gate: premise must be at least 15 words to carry enough story specificity."""
    return len((premise or "").strip().split()) >= 15


def generate_story_idea(recently_used_titles: list[str] | None = None, preferred_mood: str = "", language: str = "hi") -> dict:
    """Generate a fresh moral story concept. Returns {title, mood, premise}.

    language="hi": output in Hindi (Devanagari).
    language="en": output in English.
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
        prompt = f"""You are a creative storyteller writing short moral stories in English for YouTube Shorts.

Generate a brand new, original story concept. The story should complete in 45-55 seconds.{avoid_block}

Rules:
- Title should be 5-8 words in English, following one of these patterns:
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


def generate_story_script(title: str, mood: str, premise: str = "", language: str = "hi") -> str:
    """Generate a 4-scene moral story script for YouTube Shorts (<1 min).

    language="hi": narrations in Hindi (Devanagari), painted visual style.
    language="en": narrations in English, realistic/photorealistic visual style.
    Returns JSON array with scene/narration/visual keys.
    """
    if language == "en":
        premise_block = f"\nStory premise: {premise}" if premise else ""
        video_style = random.choice(_STORY_VISUAL_STYLE_POOL_EN)
        prompt = f"""You are a scriptwriter writing punchy, complete 60-second stories for YouTube Shorts.

Story: {title}{premise_block}
Mood: {mood}

Write exactly 4 scenes. Total duration: 55-65 seconds (~15 seconds per scene).

Each scene must have:
- "narration": In English, 25-35 words, COMPLETE sentences (never a fragment or mid-sentence break)
- "visual": In English (for Imagen), a detailed image generation prompt

CRITICAL: Each scene's narration must be SELF-CONTAINED. A viewer who sees only that scene must understand what is happening. Never write narration that is a continuation of the previous scene's sentence.

Scene structure:
- Scene 1 (Setup + Hook): Name the character, establish the world, then drop a shocking twist or emotional punch. The viewer must be hooked in the first 3 words.
- Scene 2 (Rising Action): The character discovers something that escalates the stakes — an unexpected detail, revelation, or obstacle that makes the situation harder.
- Scene 3 (Turning Point): The decisive, surprising action the character takes. Show the moment of choice or courage — something the viewer didn't expect.
- Scene 4 (Resolution): What changes — in the character, a relationship, or the world around them. End with a line that lands emotionally. No direct moral preaching ("The lesson is", "Always remember").

STYLE EXAMPLES (match this energy and completeness):

Heartfelt example:
Scene 1: "When Aarav got his first salary, he bought expensive sneakers and proudly showed them to his father — who was still wearing the same torn shoes he'd had for five years."
Scene 2: "That night, Aarav opened an old drawer. Inside: unpaid medical bills, loan slips, and receipts from every year of his school fees."
Scene 3: "He didn't sleep. By morning, he had placed a brand-new pair of shoes quietly beside the door before his father woke up."
Scene 4: "His father stood there, confused. Aarav knelt down and said softly, 'Now it's my turn to carry you.' His father cried for the first time in years."

Comedy example:
Scene 1: "Rohan bought the world's smartest fridge. First morning, it greeted him with: 'Good morning, Rohan. Maybe skip the cake today.'"
Scene 2: "At midnight, he sneaked in for ice cream. The fridge light snapped on. 'Interesting choice for someone whose diet started this morning.'"
Scene 3: "Guests came over. Rohan opened the fridge proudly — and it announced loudly, 'Warning: owner ate half the chocolate cake at 2:14 AM.' Everyone burst out laughing."
Scene 4: "Furious, Rohan yanked out the power cord. The fridge switched to battery backup and said calmly: 'Nice try.'"

NARRATION rules:
- 25-35 words per scene — complete, punchy sentences
- Each scene ends on a period or an exclamation mark — never a comma or mid-thought
- No clichés: "once upon a time", "the lesson is", "always remember"
- Show emotion through action and specific detail, not adjectives

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
        premise_block = f"\nकहानी का सार: {premise}" if premise else ""
        video_style = random.choice(_STORY_VISUAL_STYLE_POOL_HI)
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
    try:
        response = model.generate_content(prompt)
        return format_caption_for_youtube(_response_text(response).strip())
    except Exception:
        logger.warning("enhance_caption: Gemini call failed, using original caption")
        return caption


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


def inject_source_url(caption: str, source_url: str) -> str:
    """Insert 'Read More: <url>' before the hashtag line in a YouTube description.

    Result structure:
        {body text}

        Read More: <url>

        {#hashtag1 #hashtag2 ...}
    """
    if not source_url or not caption:
        return caption
    raw = (caption or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    # Find the index of the first hashtag-only line (the tag block is always last)
    first_tag_idx = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped and all(tok.startswith("#") for tok in stripped.split()):
            first_tag_idx = i
        elif stripped:
            break
    body_lines = lines[:first_tag_idx]
    tag_lines = lines[first_tag_idx:]
    # Strip trailing blank lines from body
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    read_more = f"Read More: {source_url}"
    if tag_lines:
        return "\n".join(body_lines) + f"\n\n{read_more}\n\n" + "\n".join(tag_lines)
    return "\n".join(body_lines) + f"\n\n{read_more}"


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
        if isinstance(reviewed, list) and len(reviewed) >= len(scenes):
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
