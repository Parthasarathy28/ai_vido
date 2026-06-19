"""Core data models shared across every pipeline stage.

A `Project` is the single source of truth: each stage reads it, fills in its
part, and saves it back to `project.json`. This makes the whole pipeline
resumable — if image generation crashes, you keep the script + audio and just
re-run from that stage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Scene(BaseModel):
    """One narration beat of the video: a line of speech + a visual."""

    index: int
    narration: str                      # the text the voice will read
    image_prompt: str                   # text-to-image prompt for the visual
    # Filled in by later stages:
    audio_path: Optional[str] = None    # generated voice clip for this scene
    image_path: Optional[str] = None    # generated/sourced visual
    duration: float = 0.0               # seconds (= length of the audio clip)


class Caption(BaseModel):
    """A timed subtitle line for the burned-in captions / .srt file."""

    start: float
    end: float
    text: str


class VideoMeta(BaseModel):
    """YouTube publishing metadata, derived from the script."""

    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    category_id: str = "24"             # 24 = Entertainment
    privacy: str = "private"            # private until you trust the output


class Project(BaseModel):
    """A single video project, persisted as project.json in its own folder."""

    id: str                              # slug, also the folder name
    prompt: str                          # the original story idea OR pasted script
    created_at: str = Field(default_factory=_utcnow_iso)

    # "idea"   → Claude writes the script from a short prompt
    # "script" → the prompt IS a detailed storyboard; we parse it verbatim
    mode: str = "idea"
    # "vertical" (9:16, Shorts/Reels) or "horizontal" (16:9)
    orientation: str = "horizontal"
    # Language code for script, narration & captions: en|hi|te|ta|kn
    language: str = "en"
    # Visual style code: cartoon3d|realistic|anime|storybook
    style: str = "cartoon3d"

    # If this project is a language version of another, the source project id.
    parent_id: str = ""

    # Estimated OpenAI cost breakdown for this video (USD). Empty if not tracked.
    cost: dict = Field(default_factory=dict)

    # Recurring main character description, injected into every scene's image
    # prompt so the same character appears throughout the video.
    character: str = ""
    # Fixed image seed → stable style/look across scenes (set once per project).
    seed: int = 0

    scenes: list[Scene] = Field(default_factory=list)
    captions: list[Caption] = Field(default_factory=list)
    meta: VideoMeta = Field(default_factory=VideoMeta)

    # Outputs
    voice_done: bool = False
    images_done: bool = False
    final_video_path: Optional[str] = None
    youtube_video_id: Optional[str] = None

    # Where everything for this project lives.
    workdir: str = ""

    # ---- persistence helpers ----
    @property
    def dir(self) -> Path:
        return Path(self.workdir)

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "project.json").write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, workdir: str | Path) -> "Project":
        p = Path(workdir) / "project.json"
        return cls.model_validate_json(p.read_text())

    def full_narration(self) -> str:
        return " ".join(s.narration for s in self.scenes)
