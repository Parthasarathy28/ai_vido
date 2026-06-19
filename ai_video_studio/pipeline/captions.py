"""Stage 4 — SUBTITLES.

Build timed captions and write an .srt file. Two strategies:

  * "whisper"  — transcribe the combined audio with faster-whisper for accurate
    word/segment timestamps. Best sync, but downloads a model (CPU is fine).
  * "scene"    — fallback that times one caption per scene using the audio
    durations we already measured in the voice stage. Zero downloads.

We default to scene-timing if faster-whisper isn't installed, so the pipeline
never blocks on a model download.
"""
from __future__ import annotations

from ..config import Config
from ..models import Caption, Project
from ..utils import log


def _srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap_caption(text: str, max_chars: int = 34) -> str:
    """Wrap a caption to a few short lines so it fits on screen and doesn't
    overlap. Word-wrap; falls back to hard char-wrap for scripts without
    spaces. Caps at 3 lines (trailing text is dropped from the on-screen
    caption only — narration audio is unaffected)."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        if len(w) > max_chars:                      # very long token: hard-split
            if cur:
                lines.append(cur); cur = ""
            for i in range(0, len(w), max_chars):
                lines.append(w[i:i + max_chars])
            continue
        if len(cur) + len(w) + (1 if cur else 0) <= max_chars:
            cur = f"{cur} {w}".strip()
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines[:3])


def write_srt(captions: list[Caption], path: str) -> None:
    lines = []
    for i, c in enumerate(captions, 1):
        lines += [str(i), f"{_srt_timestamp(c.start)} --> {_srt_timestamp(c.end)}",
                  _wrap_caption(c.text), ""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


def _captions_by_scene(project: Project) -> list[Caption]:
    """One caption block per scene, timed by sequential audio durations."""
    caps, t = [], 0.0
    for scene in project.scenes:
        caps.append(Caption(start=t, end=t + scene.duration, text=scene.narration))
        t += scene.duration
    return caps


def _captions_by_whisper(project: Project, cfg: Config) -> list[Caption]:
    from faster_whisper import WhisperModel

    # Concatenate scene audio so timestamps are continuous, then transcribe.
    from .video import concat_audio  # local import to avoid cycle at import time

    combined = str(project.dir / "audio" / "_combined.wav")
    concat_audio([s.audio_path for s in project.scenes], combined)

    model = WhisperModel(cfg.caption.whisper_model, device="cpu", compute_type="int8")
    # Pin the transcription language so Whisper doesn't misdetect (e.g. reading
    # Telugu as Arabic). Falls back to auto if the language is unknown to it.
    lang = project.language if project.language != "en" else None
    segments, _ = model.transcribe(combined, language=lang, word_timestamps=False)
    caps = [Caption(start=seg.start, end=seg.end, text=seg.text.strip())
            for seg in segments if seg.text.strip()]
    return caps or _captions_by_scene(project)


def generate_captions(project: Project, cfg: Config) -> Project:
    if not cfg.caption.enabled:
        log("Captions disabled", "dim")
        return project

    # For non-English languages, transcription is unreliable AND unnecessary —
    # we already have the exact narration text. Use scene-timed captions with
    # the real script text instead of Whisper.
    if project.language != "en":
        log("💬 Adding subtitles…")
        caps = _captions_by_scene(project)
    else:
        try:
            import faster_whisper  # noqa: F401
            log("💬 Adding subtitles…")
            caps = _captions_by_whisper(project, cfg)
        except Exception as e:
            log("💬 Adding subtitles…")
            caps = _captions_by_scene(project)

    project.captions = caps
    srt_path = str(project.dir / "captions.srt")
    write_srt(caps, srt_path)
    project.save()
    log("✅ Subtitles added", "green")
    return project
