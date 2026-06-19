"""Parser for detailed, pre-written scripts (the "script" input mode).

Users often bring a fully structured storyboard like:

    ### Scene 1: The Discovery
     * **Visual Prompt for AI:** A lone astronaut in a derelict spaceship...
     * **Voiceover (VO):** "Earth went silent fifty years ago..."

In "idea" mode we send a short prompt to Claude. In "script" mode we instead
PARSE the document the user pasted, so the voice reads only the Voiceover lines
and the images use only the Visual Prompt lines — no headings spoken aloud.

The parser is tolerant of markdown bullets, bold markers, and minor label
variations (Voiceover / VO / Narration ; Visual Prompt / Visual / Image).
"""
from __future__ import annotations

import re

# Label aliases → which field they fill.
_NARRATION_LABELS = ("voiceover (vo)", "voiceover", "vo", "narration", "narrator", "voice")
_VISUAL_LABELS = ("visual prompt for ai", "visual prompt", "visual", "image prompt", "image")
_TITLE_LABELS = ("title", "title ideas")
_DESC_LABELS = ("description",)
_TAG_LABELS = ("tags",)

_SCENE_HEADER = re.compile(r"^\s*#{0,6}\s*scene\b", re.IGNORECASE)


def looks_like_detailed_script(text: str) -> bool:
    """Heuristic: does this read like a structured storyboard rather than a
    one-line idea? True if it has a 'Scene' header AND a voiceover/visual label."""
    low = text.lower()
    has_scene = bool(re.search(r"scene\s*\d", low)) or "scene-by-scene" in low
    has_vo = any(lbl in low for lbl in _NARRATION_LABELS)
    return has_scene and has_vo


def _clean(value: str) -> str:
    """Strip markdown bold/italics, surrounding quotes, and stray asterisks."""
    value = value.strip()
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)   # **bold**
    value = value.replace("*", "").strip()
    value = value.strip("“”\"'").strip()
    return value


def _extract_label(line: str) -> tuple[str | None, str]:
    """If a line is 'Label: value' (with optional bullets/bold), return
    (normalized_label, value). Otherwise (None, original_line)."""
    # drop leading bullet markers: -, *, •, digits.
    stripped = re.sub(r"^\s*[-*•]?\s*", "", line)
    m = re.match(r"\*{0,2}([A-Za-z /()]+?)\*{0,2}\s*[:：]\s*(.*)$", stripped)
    if not m:
        return None, line
    label = m.group(1).strip().lower().rstrip(":")
    return label, m.group(2).strip()


def parse_detailed_script(text: str) -> dict:
    """Return {title, description, tags, scenes:[{narration, image_prompt}]}.

    Scenes are split on 'Scene N' headers. Within each scene we collect the
    Voiceover line(s) as narration and the Visual Prompt as the image prompt.
    Metadata (title/description/tags) is pulled from anywhere in the document.
    """
    lines = text.splitlines()

    title = description = ""
    tags: list[str] = []
    characters: list[str] = []   # header- and scene-level Character: descriptions

    scenes: list[dict] = []
    cur: dict | None = None
    cur_field: str | None = None  # which field a continuation line appends to
    in_scene = False              # True only after the first "Scene N" header

    def flush():
        nonlocal cur
        if cur and (cur.get("narration") or cur.get("image_prompt")):
            scenes.append(cur)
        cur = None

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            cur_field = None
            continue

        if _SCENE_HEADER.match(line):
            flush()
            cur = {"narration": "", "image_prompt": ""}
            cur_field = None
            in_scene = True
            continue

        label, value = _extract_label(line)

        # Header-level metadata (before any Scene) must NOT become narration.
        # We only keep title/description/tags/character from the preamble.
        if not in_scene and label not in (
            *_TITLE_LABELS, *_DESC_LABELS, *_TAG_LABELS, "character",
        ):
            cur_field = None
            continue

        if label in _NARRATION_LABELS:
            if cur is None:
                cur = {"narration": "", "image_prompt": ""}
            cur["narration"] = (cur["narration"] + " " + _clean(value)).strip()
            cur_field = "narration"
        elif label in _VISUAL_LABELS:
            if cur is None:
                cur = {"narration": "", "image_prompt": ""}
            cur["image_prompt"] = (cur["image_prompt"] + " " + _clean(value)).strip()
            cur_field = "image_prompt"
        elif label in _TITLE_LABELS and not title:
            title = _clean(value) or title
            cur_field = None
        elif label in _DESC_LABELS and not description:
            description = _clean(value)
            cur_field = None
        elif label in _TAG_LABELS and not tags:
            tags = [t.strip() for t in re.split(r"[,;]", _clean(value)) if t.strip()]
            cur_field = None
        elif label == "character":
            # Captured for the character sheet, but never spoken.
            desc = _clean(value)
            if desc and desc.lower() not in (c.lower() for c in characters):
                characters.append(desc)
            cur_field = None
        elif label in ("background", "audio/sfx", "audio", "sfx", "format",
                       "total characters"):
            # storyboard metadata we intentionally don't speak — skip.
            cur_field = None
        elif cur_field and cur is not None and label is None:
            # continuation of a multi-line voiceover/visual block
            cur[cur_field] = (cur[cur_field] + " " + _clean(value)).strip()

    flush()

    # Fallbacks so we always have something usable.
    if not scenes:
        # No scene structure detected — treat each non-empty line as narration.
        for ln in lines:
            t = _clean(ln)
            if t and not t.lower().startswith(("#", "title", "tags", "description")):
                scenes.append({"narration": t,
                               "image_prompt": f"cinematic scene: {t}"})
    # Each scene needs an image prompt; derive from narration if missing.
    for sc in scenes:
        if not sc.get("image_prompt"):
            sc["image_prompt"] = f"cinematic scene depicting: {sc['narration']}"
        if not sc.get("narration"):
            sc["narration"] = sc["image_prompt"]

    if not title:
        # first non-empty title-ish line, else first narration
        title = (scenes[0]["narration"][:70] if scenes else "AI Story")

    # Build one consistent character description for the whole video.
    character = _merge_characters(characters)

    return {
        "title": title[:100],
        "description": description or "An AI-generated cinematic short.",
        "tags": tags[:15] or ["ai", "shorts", "story", "cinematic"],
        "scenes": scenes,
        "character": character,
    }


def _merge_characters(characters: list[str]) -> str:
    """Combine the Character: lines into a single recurring-character description
    used to keep the same look across every scene's image."""
    if not characters:
        return ""
    # Prefer the most descriptive (longest) line; keep it concise.
    best = max(characters, key=len)
    return best[:200]
