"""Visual style registry — controls the LOOK of the generated images.

Each style maps a short prompt suffix that's appended to every scene's image
prompt. This is what turns the same scene description into a Pixar-style 3D
cartoon vs. a photorealistic shot vs. anime, etc.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    code: str
    name: str
    suffix: str   # appended to each image prompt


STYLES: dict[str, Style] = {
    "cartoon3d": Style(
        "cartoon3d", "3D Cartoon",
        ", 3D animated cartoon style, Pixar/Disney style, cute stylized "
        "characters, big expressive eyes, soft lighting, vibrant colors, "
        "high quality 3D render",
    ),
    "realistic": Style(
        "realistic", "Realistic",
        ", cinematic, photorealistic, highly detailed, dramatic lighting, 4k",
    ),
    "anime": Style(
        "anime", "Anime",
        ", anime style, Japanese animation, cel shaded, vibrant, detailed "
        "anime illustration",
    ),
    "storybook": Style(
        "storybook", "Storybook",
        ", children's storybook illustration, hand-painted, soft watercolor, "
        "warm and whimsical",
    ),
}

DEFAULT_STYLE = "cartoon3d"


def get_style(code: str | None) -> Style:
    return STYLES.get((code or "").lower(), STYLES[DEFAULT_STYLE])


def ui_list() -> list[dict]:
    return [{"code": s.code, "name": s.name} for s in STYLES.values()]
