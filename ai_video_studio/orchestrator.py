"""The orchestrator — runs the full prompt → video → (upload) pipeline.

Stages are ordered and resumable. Each stage updates project.json, so a crash
mid-run can be resumed with `from_stage`. This is the single entry point the
CLI and the scheduler both call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# A progress callback: on_progress(stage_name, status, project)
# status is "start" | "done". Used by the web UI to stream live progress.
ProgressCb = Optional[Callable[[str, str, "Project"], None]]

from .config import Config, load_config
from .models import Project
from .pipeline import (
    assemble_video,
    generate_captions,
    generate_images,
    generate_script,
    generate_voice,
    upload_video,
)
from .utils import log, slugify, step

# Ordered pipeline. Name → function(project, cfg) -> project
STAGES = [
    ("script", generate_script),
    ("voice", generate_voice),
    ("images", generate_images),
    ("captions", generate_captions),
    ("render", assemble_video),
    ("upload", upload_video),
]
STAGE_NAMES = [name for name, _ in STAGES]


def _new_project(prompt: str, cfg: Config, mode: str, orientation: str,
                 language: str, style: str) -> Project:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    pid = f"{stamp}-{slugify(prompt, 32)}"
    workdir = str(Path(cfg.output_dir) / pid)
    project = Project(id=pid, prompt=prompt, workdir=workdir,
                      mode=mode, orientation=orientation, language=language,
                      style=style)
    project.save()
    return project


def create_video(
    prompt: str,
    cfg: Config | None = None,
    from_stage: str = "script",
    to_stage: str = "upload",
    resume_dir: str | None = None,
    on_progress: ProgressCb = None,
    mode: str = "auto",
    orientation: str = "horizontal",
    language: str = "en",
    style: str = "cartoon3d",
) -> Project:
    cfg = cfg or load_config()

    if resume_dir:
        project = Project.load(resume_dir)
        log(f"Resuming project {project.id}")
    else:
        project = _new_project(prompt, cfg, mode, orientation, language, style)
        log("🎬 Starting your video…")

    start = STAGE_NAMES.index(from_stage)
    end = STAGE_NAMES.index(to_stage)

    for name, fn in STAGES[start : end + 1]:
        step(name.upper())
        if on_progress:
            on_progress(name, "start", project)
        project = fn(project, cfg)
        if on_progress:
            on_progress(name, "done", project)

    log("🎉 Your video is ready!", "bold green")
    return project


def make_language_version(
    source_dir: str,
    language: str,
    cfg: Config | None = None,
    on_progress: ProgressCb = None,
) -> Project:
    """Create a new project that is `source_dir` re-voiced & re-captioned in
    `language`, REUSING the source's images (the expensive part). Returns the
    new project with its own final.mp4.
    """
    from copy import deepcopy

    from .languages import get_language
    from .pipeline import (
        assemble_video,
        generate_captions,
        generate_voice,
    )
    from .pipeline.script_gen import translate_narrations

    cfg = cfg or load_config()
    src = Project.load(source_dir)
    lang = get_language(language)

    # New project shell, sharing the source's prompt/orientation/character.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    pid = f"{stamp}-{slugify(src.prompt, 24)}-{lang.code}"
    workdir = str(Path(cfg.output_dir) / pid)
    # Group versions under the original (a version's parent is the root).
    root_id = src.parent_id or src.id
    proj = Project(id=pid, prompt=src.prompt, workdir=workdir,
                   mode=src.mode, orientation=src.orientation,
                   language=lang.code, style=src.style,
                   character=src.character, seed=src.seed,
                   parent_id=root_id)
    proj.scenes = deepcopy(src.scenes)
    proj.meta = deepcopy(src.meta)
    proj.save()

    # 1. Translate narration (keeps images & timing structure).
    if on_progress:
        on_progress("script", "start", proj)
    translated = translate_narrations([s.narration for s in proj.scenes], lang, cfg)
    for scene, text in zip(proj.scenes, translated):
        scene.narration = text
        scene.audio_path = ""        # force re-synthesis
    # Reuse source images (copy paths; files live in the source dir).
    for scene in proj.scenes:
        scene.duration = 0.0
    proj.save()
    if on_progress:
        on_progress("script", "done", proj)

    # 2. Re-voice, 3. re-caption, 4. re-render. Images are reused as-is.
    import time
    for name, fn in [("voice", generate_voice),
                     ("captions", generate_captions),
                     ("render", assemble_video)]:
        step(name.upper())
        if on_progress:
            on_progress(name, "start", proj)
        _t = time.monotonic()
        proj = fn(proj, cfg)
        log(f"⏱ {name} took {time.monotonic() - _t:.1f}s")
        if on_progress:
            on_progress(name, "done", proj)

    log(f"Language version ({lang.name}) complete: {proj.id}", "bold green")
    return proj
