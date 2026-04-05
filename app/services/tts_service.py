# app/services/tts_service.py

from google.cloud import texttospeech
import random

client = texttospeech.TextToSpeechClient()

# Voice options for per-video shuffling.
# English pool: 3 male + 2 female (Neural2/WaveNet — within 1M chars/month free tier).
# Hindi pool: 3 male + 2 female (Neural2/WaveNet — Chirp3-HD removed: $160/1M, no free tier).
_VOICE_OPTIONS = {
    "en": [
        {"name": "en-US-Neural2-D", "gender": "male", "label": "Atlas (Male)"},
        {"name": "en-US-Neural2-J", "gender": "male", "label": "Orion (Male)"},
        {"name": "en-US-Wavenet-D", "gender": "male", "label": "Ridge (Male)"},
        {"name": "en-US-Neural2-F", "gender": "female", "label": "Nova (Female)"},
        {"name": "en-US-Neural2-H", "gender": "female", "label": "Luna (Female)"},
    ],
    "hi": [
        {"name": "hi-IN-Neural2-B", "gender": "male", "label": "Arjun (HI Male)"},
        {"name": "hi-IN-Neural2-C", "gender": "male", "label": "Vikram (HI Male)"},
        {"name": "hi-IN-Wavenet-D", "gender": "male", "label": "Rajan (HI Male)"},
        {"name": "hi-IN-Neural2-A", "gender": "female", "label": "Priya (HI Female)"},
        {"name": "hi-IN-Wavenet-A", "gender": "female", "label": "Ananya (HI Female)"},
    ],
}

_LANG_CODES = {
    "en": "en-US",
    "hi": "hi-IN",
}

# Domain-aware voice selection: each domain maps to a voice that fits its tone.
_DOMAIN_VOICE_MAP = {
    "technology":            "en-US-Neural2-D",   # Atlas — clear, authoritative male
    "artificial intelligence": "en-US-Neural2-J", # Orion — crisp, forward-looking male
    "current affairs":       "en-US-Wavenet-D",   # Ridge — gravitas, news-anchor feel
    "trending":              "en-US-Neural2-F",   # Nova — energetic, engaging female
    "science":               "en-US-Neural2-H",   # Luna — warm, curious female
}


def get_voice_options(language: str = "en") -> list[dict]:
    return _VOICE_OPTIONS.get(language, _VOICE_OPTIONS["en"])


def choose_voice_for_video(language: str = "en", preference: str = "shuffle", domain: str = "") -> str:
    # Domain-aware selection takes priority for English
    if language == "en" and domain:
        mapped = _DOMAIN_VOICE_MAP.get(domain.strip().lower())
        if mapped:
            return mapped

    options = get_voice_options(language)
    pref = (preference or "shuffle").strip()
    selected = options
    if pref.lower() in ("male", "female"):
        selected = [v for v in options if v.get("gender") == pref.lower()] or options
    elif pref and pref not in ("shuffle", "auto"):
        exact = [v for v in options if v.get("name") == pref]
        if exact:
            selected = exact
    return random.choice(selected)["name"]


def generate_audio(text: str, output_file: str, language: str = "en", voice_name: str | None = None):
    synthesis_input = texttospeech.SynthesisInput(text=text)

    lang_code = _LANG_CODES.get(language, "en-US")

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=1.0,   # Natural pace — avoids robotic feel from slight speedup
        pitch=0.0,
    )

    options = get_voice_options(language)
    voices = [v["name"] for v in options]

    if voice_name and voice_name in voices:
        voices = [voice_name] + [v for v in voices if v != voice_name]

    for voice_name in voices:
        try:
            voice = texttospeech.VoiceSelectionParams(
                language_code=lang_code,
                name=voice_name,
            )
            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config,
            )
            with open(output_file, "wb") as f:
                f.write(response.audio_content)
            print(f"[TTS] Voice: {voice_name}")
            try:
                from app.services import firestore_service
                firestore_service.record_quota_event("tts_chars", details=str(len(text or "")))
            except Exception:
                pass
            return
        except Exception as e:
            print(f"[TTS] {voice_name} failed, trying next: {e}")

    raise RuntimeError("All TTS voices failed")
