"""Language registry — the single source of truth for multi-language support.

Each language maps to:
  - name:        human label shown in the UI
  - piper_voice: free offline Piper voice id, or None if unavailable
  - font:        font *family name* (as fc-list reports it) for caption rendering
  - native:      the language's own name (for nicer prompts/labels)

Free-voice languages (Piper): English, Hindi, Telugu.
Tamil & Kannada have NO free Piper voice — they require ElevenLabs
(multilingual) for narration. The script, captions and images still work in
those languages; only the voice falls back. `has_free_voice()` reflects this.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    native: str
    piper_voice: str | None     # None → no free offline voice
    font: str                   # caption font family (Noto, installed via apt)

    @property
    def tts_instruction(self) -> str:
        """Steering text for cloud TTS so it pronounces this language natively."""
        return (
            f"Narrate in fluent, natural {self.name} with correct native "
            f"{self.name} pronunciation, as a {self.name} storyteller would. "
            f"Warm, clear, cinematic delivery. Do not use an English accent."
        )


LANGUAGES: dict[str, Language] = {
    "en": Language("en", "English", "English",
                   "en_US-amy-medium", "DejaVu Sans"),
    "hi": Language("hi", "Hindi", "हिन्दी",
                   "hi_IN-pratham-medium", "Noto Sans Devanagari"),
    "te": Language("te", "Telugu", "తెలుగు",
                   "te_IN-venkatesh-medium", "Noto Sans Telugu"),
    # No free Piper voice — needs ElevenLabs for narration.
    "ta": Language("ta", "Tamil", "தமிழ்",
                   None, "Noto Sans Tamil"),
    "kn": Language("kn", "Kannada", "ಕನ್ನಡ",
                   None, "Noto Sans Kannada"),
}

DEFAULT_LANGUAGE = "en"


def get_language(code: str | None) -> Language:
    """Resolve a code to a Language, defaulting to English."""
    return LANGUAGES.get((code or "").lower(), LANGUAGES[DEFAULT_LANGUAGE])


def has_free_voice(code: str) -> bool:
    return get_language(code).piper_voice is not None


def voice_available(code: str) -> bool:
    """True if we can narrate this language at all: a free Piper voice, OR a
    cloud TTS key (OpenAI / ElevenLabs) that handles any language."""
    if has_free_voice(code):
        return True
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("ELEVENLABS_API_KEY"))


def ui_list() -> list[dict]:
    """List for the web UI's language selector."""
    cloud = bool(os.getenv("OPENAI_API_KEY") or os.getenv("ELEVENLABS_API_KEY"))
    return [
        {
            "code": l.code,
            "name": l.name,
            "native": l.native,
            "free_voice": l.piper_voice is not None,
            # Can we voice it right now (free OR via an available cloud key)?
            "voice_available": (l.piper_voice is not None) or cloud,
        }
        for l in LANGUAGES.values()
    ]
