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

    context_block = f"\nBackground context (use to enrich facts and details in the script):\n{context.strip()}\n" if context and context.strip() else ""
    video_style = random.choice(_VISUAL_STYLE_POOL)

    prompt = f"""
You are an expert scriptwriter for educational YouTube videos. Research and write a complete, insightful, reader-friendly script on:

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
- TODAY'S DATE: {today_str}. Never present a past event as if it just happened.
- If context articles are provided above, prefer those facts. For any fact NOT in the provided context, only include it if you are confident it occurred. Phrase uncertain claims as "reportedly", "according to reports", or "as of [year]".
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


def fact_check_scenes(topic: str, scenes: list[dict], language: str = "en") -> list[dict]:
    """Run a fast fact-check + safety rewrite pass while preserving structure."""
    if not scenes:
        return scenes

    from datetime import date
    today_str = date.today().isoformat()
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])
    prompt = f"""
You are a strict fact-check and policy safety editor for short educational videos.

TODAY'S DATE: {today_str}

Task:
1) Review each scene's narration for likely factual errors, overclaims, or missing caution.
2) Flag and correct any claim that presents a past event (more than a few weeks ago) as if it just happened or is "breaking news".
3) Replace fabricated or unverifiable specific dates/numbers with hedged language ("reportedly", "as of [year]", "estimated"). Remove them entirely if they add no value.
4) Keep same number of scenes and same `scene` numbers.
5) Keep visual prompts in English and remove risky copyright/trademark references.
6) Remove profanity and offensive wording.
7) Ensure visuals contain zero readable text.

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


def apply_quality_controls(topic: str, scenes: list[dict], language: str = "en") -> list[dict]:
    """Apply fact-check + profanity + copyright sanitization."""
    reviewed = fact_check_scenes(topic, scenes, language=language)
    cleaned = []
    for s in reviewed:
        narration = sanitize_profanity(str(s.get("narration", "")))
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


def rate_and_select_news(articles: list[dict], top_performers: list[dict] | None = None) -> list[dict]:
    articles_text = "\n".join(
        f"{i + 1}. {a['headline']}"
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

    prompt = f"""You are a senior news editor selecting stories for short educational videos.

Rate each article on a combined 1–5 scale using these criteria:
- Virality & public interest (will people share this?)
- Educational value (does it teach something specific and useful?)
- Freshness (is this breaking or very recent news?)
- Story depth (is there a concrete fact, number, or consequence — not just a vague headline?)
{performers_block}
Select the top 5 articles. For each, write a 2-sentence context summary that captures the key fact and why it matters — this will be used to write the video script.

Return ONLY a valid JSON array, no markdown, no explanation:
[{{"headline": "...", "context": "2-sentence summary with key fact and significance.", "rating": 4.5}}, ...]

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


def review_title_and_caption_with_senior_reviewer(
    topic: str,
    scenes: list[dict],
    language: str = "en",
    genre: str = "",
) -> dict:
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["en"])
    genre_hint = f" Focus hashtags around the {genre} domain." if genre else ""
    prompt = f"""
You are a senior script reviewer.
Create:
1) A catchy but non-clickbait YouTube Shorts title.
2) A reader-friendly caption aligned with the voiceover script (same core points).
3) 10-15 relevant hashtags derived from the script content and topic — mix broad popular tags with niche-specific ones.{genre_hint}

Rules:
- Keep language simple and natural.
- Caption body must be insightful, complete, and engaging (2 short paragraphs max).
- Do not exaggerate or promise things not in script.
- Always include #Shorts and #YouTubeShorts in the hashtags.
- Return ONLY a valid JSON object with keys: title, caption, hashtags (array of strings).

Topic: {topic}
Language: {lang_instruction}
Script scenes:
{_scene_list_to_json_prompt(scenes)}
"""
    try:
        response = model.generate_content(prompt)
        payload = _extract_json_object(_response_text(response))
        title = (payload.get("title") or "").strip()
        caption_body = (payload.get("caption") or "").strip()
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
