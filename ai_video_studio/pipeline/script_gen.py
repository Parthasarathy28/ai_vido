"""Stage 1 — SCRIPT.

Turn a freeform story prompt into a structured, scene-by-scene script:
each scene has narration (what the voice reads) and an image prompt (the visual).
Also produces YouTube title/description/tags.

Provider "anthropic" uses Claude for the best results. If no API key is present
(or provider == "template"), a fully-offline fallback splits/expands the prompt
so the pipeline still runs free and without any network.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import textwrap

from ..config import Config
from ..languages import Language, get_language
from ..models import Project, Scene, VideoMeta
from ..styles import get_style
from ..utils import log
from .script_parser import looks_like_detailed_script, parse_detailed_script

SYSTEM_PROMPT = """You are a YouTube scriptwriter for a faceless narrated-video channel.
Given a story idea, produce a JSON object for an engaging short video.

Return ONLY valid JSON, no prose, with this exact shape:
{
  "title": "click-worthy YouTube title (<= 70 chars)",
  "description": "2-3 sentence YouTube description",
  "tags": ["lowercase", "keyword", "tags"],
  "character": "a SINGLE detailed visual description of the main character (age, build, hair, clothing, distinguishing features) — reused in every scene so the SAME person appears throughout",
  "scenes": [
    {"narration": "one or two spoken sentences",
     "image_prompt": "a vivid visual description of THIS scene's setting and action (do NOT re-describe the character's face/outfit — that is added automatically)"}
  ]
}

Rules:
- Use VERY SIMPLE, easy, everyday words that anyone (even a child) can
  understand. Short sentences. Avoid difficult or fancy vocabulary.
- Narration must sound natural read aloud. No stage directions, no 'Scene 1'.
- Each image_prompt is a concrete, photographic/cinematic visual (no text in image).
- The "character" must be specific enough that an image model draws the same person each time.
- Hook the viewer in the first scene. End with a subtle call to engage.
"""


def _build_user_prompt(prompt: str, cfg: Config, lang: "Language") -> str:
    lang_rule = ""
    if lang.code != "en":
        lang_rule = (
            f"\n\nWrite the narration, title and description in {lang.name} "
            f"({lang.native}), using the native {lang.name} script — NOT "
            f"transliteration. Use SIMPLE, easy, commonly-spoken {lang.name} "
            f"words that everyone understands (not literary/complex words). "
            f"Keep each \"image_prompt\" in ENGLISH (image models need English). "
            f"\"tags\" may be English."
        )
    return (
        f"Story idea: {prompt}\n\n"
        f"Make about {cfg.script.target_scenes} scenes, "
        f"~{cfg.script.words_per_scene} words of narration each."
        f"{lang_rule}"
    )


def _generate_anthropic(prompt: str, cfg: Config, lang: "Language") -> dict:
    import anthropic  # imported lazily so it's optional

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    msg = client.messages.create(
        model=cfg.script.model,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(prompt, cfg, lang)}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    return _parse_json(text)


def translate_narrations(texts: list[str], lang: "Language", cfg: Config) -> list[str]:
    """Translate each scene's narration into `lang`, preserving order/count.

    Uses whichever LLM provider is available (OpenAI → Anthropic). Returns the
    originals unchanged if no provider is configured or on any failure — the
    caller keeps a working video either way.
    """
    if lang.code == "en" or not texts:
        return texts

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    instruction = (
        f"Translate each numbered line into {lang.name} ({lang.native}), using "
        f"the native {lang.name} script (no transliteration). Keep the SAME "
        f"numbering and the same number of lines. Return ONLY the numbered "
        f"translated lines, nothing else.\n\n{numbered}"
    )
    provider = _pick_provider(cfg)
    try:
        if provider == "openai":
            from openai import OpenAI

            from ..costs import record_chat
            r = OpenAI().chat.completions.create(
                model=cfg.script.openai_model,
                messages=[{"role": "user", "content": instruction}],
            )
            if r.usage:
                record_chat(cfg.script.openai_model,
                            r.usage.prompt_tokens, r.usage.completion_tokens)
            out = r.choices[0].message.content
        elif provider == "anthropic":
            import anthropic
            msg = anthropic.Anthropic().messages.create(
                model=cfg.script.model, max_tokens=4000,
                messages=[{"role": "user", "content": instruction}],
            )
            out = "".join(b.text for b in msg.content
                          if getattr(b, "type", None) == "text")
        else:
            return texts  # no translator available
    except Exception as e:
        log(f"Translation failed ({e}); keeping original text.", "yellow")
        return texts

    # Parse "N. text" lines back into a list, tolerant of blank lines.
    by_num: dict[int, str] = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
        if m:
            by_num[int(m.group(1))] = m.group(2).strip()
    result = [by_num.get(i + 1, texts[i]) for i in range(len(texts))]
    return result


def _generate_openai(prompt: str, cfg: Config, lang: "Language") -> dict:
    from openai import OpenAI  # imported lazily so it's optional

    from ..costs import record_chat

    client = OpenAI()  # reads OPENAI_API_KEY
    resp = client.chat.completions.create(
        model=cfg.script.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(prompt, cfg, lang)},
        ],
        response_format={"type": "json_object"},
    )
    if resp.usage:
        record_chat(cfg.script.openai_model,
                    resp.usage.prompt_tokens, resp.usage.completion_tokens)
    return _parse_json(resp.choices[0].message.content)


def _parse_json(text: str) -> dict:
    """Extract the JSON object even if the model wrapped it in fences/prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    return json.loads(text)


def _generate_template(prompt: str, cfg: Config) -> dict:
    """Offline fallback. Splits the prompt into sentence-ish beats and wraps
    each into narration + a generic-but-usable image prompt. Not as good as the
    LLM, but it always works with zero dependencies / network."""
    # Strip markdown/table/symbol lines so pasted documents don't become junk
    # "scenes" (e.g. a row of '|---|---|' or '### Heading').
    cleaned = []
    for line in prompt.splitlines():
        s = re.sub(r"[|#>*`_=-]{2,}", " ", line)          # table/hr/markdown runs
        s = re.sub(r"^[\s>#*\-]+", "", s).strip()          # leading markers
        if s and any(c.isalnum() for c in s):
            cleaned.append(s)
    base = " ".join(cleaned) if cleaned else prompt.strip()

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", base) if s.strip()]
    if not sentences:
        sentences = [base or "An untold story unfolds."]
    # Cap scenes so a huge pasted doc doesn't explode into dozens of clips.
    sentences = sentences[: max(3, cfg.script.target_scenes)]

    # Pad/merge toward the target scene count.
    target = max(3, cfg.script.target_scenes)
    while len(sentences) < target:
        sentences.append(
            "And so the story continued, each moment building toward what came next."
        )
    sentences = sentences[:target]

    scenes = []
    for s in sentences:
        # naive but serviceable visual prompt derived from the narration
        visual = textwrap.shorten(s, width=120, placeholder="")
        scenes.append(
            {
                "narration": s,
                "image_prompt": f"cinematic scene depicting: {visual}",
            }
        )

    head = textwrap.shorten(prompt, width=60, placeholder="")
    return {
        "title": head.title() or "An AI-Narrated Story",
        "description": f"An AI-generated narrated video about: {prompt}",
        "tags": ["story", "ai", "narrated", "shorts"],
        "scenes": scenes,
    }


def _pick_provider(cfg: Config) -> str:
    p = cfg.script.provider
    if p == "auto":
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        return "template"
    return p


def generate_script(project: Project, cfg: Config) -> Project:
    lang = get_language(project.language)
    if lang.code != "en":
        log(f"Language: {lang.name} ({lang.native})")

    # Decide the input mode. "auto" detects a pasted storyboard.
    mode = project.mode
    if mode == "auto":
        mode = "script" if looks_like_detailed_script(project.prompt) else "idea"

    if mode == "script":
        log("📖 Reading your script…")
        try:
            data = parse_detailed_script(project.prompt)
        except Exception as e:
            log(f"Couldn't read the script ({e}); using a simple version.", "yellow")
            data = _generate_template(project.prompt, cfg)
    else:
        provider = _pick_provider(cfg)
        log("✍️ Writing the story…")
        try:
            if provider == "openai":
                data = _generate_openai(project.prompt, cfg, lang)
            elif provider == "anthropic":
                data = _generate_anthropic(project.prompt, cfg, lang)
            else:
                data = _generate_template(project.prompt, cfg)
        except Exception as e:  # robust: never let script gen kill the run
            log(f"Script provider '{provider}' failed ({e}); using offline template.",
                "yellow")
            data = _generate_template(project.prompt, cfg)

    style = get_style(project.style).suffix
    character = (data.get("character") or "").strip()
    project.character = character[:200]

    # A stable per-project seed keeps the visual style/character consistent
    # across scenes. Derive it deterministically from the content (no RNG).
    seed_source = (data.get("title", "") + project.prompt)[:200]
    project.seed = (
        int(hashlib.md5(seed_source.encode()).hexdigest(), 16) % 1_000_000
    ) or 1

    def _scene_prompt(raw: str) -> str:
        """Build a per-scene image prompt that keeps the SAME character and the
        chosen visual style across every scene."""
        raw = raw.strip()
        if character:
            return (
                f"The SAME recurring main character in every scene: {character} "
                f"Keep this character's look consistent. "
                f"Scene: {raw}{style}"
            )
        return raw + style

    project.scenes = [
        Scene(
            index=i,
            narration=sc["narration"].strip(),
            image_prompt=_scene_prompt(sc["image_prompt"]),
        )
        for i, sc in enumerate(data["scenes"])
        if sc.get("narration")
    ]
    project.meta = VideoMeta(
        title=data.get("title", "")[:100],
        description=data.get("description", ""),
        tags=data.get("tags", [])[:15],
    )
    log(f"✅ Story ready: “{project.meta.title}” ({len(project.scenes)} scenes)",
        "green")
    project.save()
    return project
