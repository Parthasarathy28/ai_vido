"""Stage 2 — VOICE.

Synthesize narration audio per scene. Default provider is Piper (free, offline,
CPU). ElevenLabs is supported as a paid upgrade if ELEVENLABS_API_KEY is set and
provider == "elevenlabs".

Each scene gets a WAV clip; we measure its exact duration with ffprobe so the
video stage can sync visuals precisely.
"""
from __future__ import annotations

import json
import os
import shutil
import wave
from pathlib import Path

from ..config import Config
from ..languages import Language, get_language
from ..models import Project
from ..utils import log, run

# Where we cache downloaded Piper voice models.
PIPER_VOICE_DIR = Path(
    os.getenv("PIPER_VOICE_DIR", str(Path.home() / ".local" / "share" / "piper_voices"))
)


def _audio_duration(path: str) -> float:
    """Duration in seconds. Tries ffprobe, falls back to the wave module."""
    try:
        cp = run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path]
        )
        return float(json.loads(cp.stdout)["format"]["duration"])
    except Exception:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())


# ----------------------------- Piper (free) -----------------------------
def _ensure_piper_voice(voice: str) -> str:
    """Return a path to the voice's .onnx model, downloading it once if missing.

    Newer piper-tts requires the model to be downloaded explicitly; we cache it
    in PIPER_VOICE_DIR so subsequent (and headless/cron) runs are offline."""
    model_path = PIPER_VOICE_DIR / f"{voice}.onnx"
    if model_path.exists():
        return str(model_path)
    PIPER_VOICE_DIR.mkdir(parents=True, exist_ok=True)
    log(f"Downloading Piper voice '{voice}' (one-time)…")
    run(["python", "-m", "piper.download_voices", voice,
         "--download-dir", str(PIPER_VOICE_DIR)])
    if not model_path.exists():
        raise RuntimeError(f"Piper voice download did not produce {model_path}")
    return str(model_path)


def _clean_for_speech(text: str) -> str:
    """Strip non-speakable junk (markdown tables, bullets, symbols, code fences)
    so the TTS engine never receives content it can't voice."""
    import re

    t = text or ""
    t = re.sub(r"`{1,3}", " ", t)                 # code fences/inline code
    t = re.sub(r"^[\s>#*\-]+", "", t)             # leading markdown markers
    t = t.replace("|", " ").replace("*", " ")     # table pipes, bold stars
    t = re.sub(r"[-=]{3,}", " ", t)               # table/hr rules: ---, ===
    t = re.sub(r"[#_>]+", " ", t)                 # stray markdown
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _has_speakable(text: str) -> bool:
    """True if there's at least one letter/number to pronounce."""
    return any(c.isalnum() for c in (text or ""))


def _write_silence(out_path: str, seconds: float = 1.2) -> None:
    """Produce a short silent WAV so a junk/empty scene can't break the video."""
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        f"anullsrc=r=22050:cl=mono", "-t", f"{seconds:.2f}", out_path,
    ])


def _synthesize_piper(text: str, out_path: str, cfg: Config, voice: str) -> None:
    """Use the piper CLI (free, offline, CPU). The voice model is auto-downloaded
    and cached on first run."""
    piper = shutil.which("piper")
    if not piper:
        raise RuntimeError(
            "piper not found. Install it: `pip install piper-tts`. "
            "It runs free on CPU."
        )
    model = _ensure_piper_voice(voice)
    # piper reads text from stdin and writes a WAV to --output_file
    run([piper, "--model", model, "--output_file", out_path], input=text)


# ---------------------------- OpenAI TTS --------------------------------
def _synthesize_openai(text: str, out_path: str, cfg: Config,
                       lang: Language | None = None) -> None:
    """OpenAI text-to-speech (uses OPENAI_API_KEY). Multilingual — handles
    Tamil, Kannada, Hindi, Telugu, etc. Costs per character (cheap).

    We pass per-language `instructions` so gpt-4o-mini-tts pronounces the text
    natively instead of reading it with an English accent."""
    from openai import OpenAI

    client = OpenAI()
    mp3 = out_path.replace(".wav", ".mp3")
    kwargs = dict(model=cfg.voice.openai_model,
                  voice=cfg.voice.openai_voice, input=text)
    # `instructions` is supported by gpt-4o-mini-tts (not by tts-1).
    if lang is not None and "4o" in cfg.voice.openai_model:
        kwargs["instructions"] = lang.tts_instruction
    resp = client.audio.speech.create(**kwargs)
    resp.write_to_file(mp3)
    from ..costs import record_tts
    record_tts(cfg.voice.openai_model, len(text))
    run(["ffmpeg", "-y", "-i", mp3, out_path])  # normalize to wav


# -------------------------- ElevenLabs (paid) ---------------------------
def _synthesize_elevenlabs(text: str, out_path: str, cfg: Config) -> None:
    import requests

    api_key = os.environ["ELEVENLABS_API_KEY"]
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={"text": text, "model_id": "eleven_multilingual_v2"},
        timeout=120,
    )
    r.raise_for_status()
    mp3 = out_path.replace(".wav", ".mp3")
    with open(mp3, "wb") as f:
        f.write(r.content)
    run(["ffmpeg", "-y", "-i", mp3, out_path])  # normalize to wav


def _synth(text: str, out_path: str, cfg: Config, lang: Language) -> None:
    """Pick the narration engine for the chosen language.

    - ElevenLabs (multilingual) if a key is set & configured → works for ALL
      languages, including Tamil/Kannada.
    - Else the language's free Piper voice (English/Hindi/Telugu).
    - Else (Tamil/Kannada with no key) raise a clear, actionable error.

    Scenes with no speakable text (e.g. pasted markdown tables) get a short
    silence instead of crashing the whole pipeline.
    """
    text = _clean_for_speech(text)
    if not _has_speakable(text):
        _write_silence(out_path)
        return

    has_eleven = os.getenv("ELEVENLABS_API_KEY")
    has_openai = os.getenv("OPENAI_API_KEY")

    # Explicit provider choice wins.
    if cfg.voice.provider == "elevenlabs" and has_eleven:
        _synthesize_elevenlabs(text, out_path, cfg)
        return
    if cfg.voice.provider == "openai" and has_openai:
        _synthesize_openai(text, out_path, cfg, lang)
        return

    # Auto (piper default): use the free Piper voice if this language has one.
    if lang.piper_voice:
        _synthesize_piper(text, out_path, cfg, lang.piper_voice)
        return

    # No free voice (Tamil/Kannada): use a multilingual cloud TTS if available.
    if has_openai:
        _synthesize_openai(text, out_path, cfg, lang)
        return
    if has_eleven:
        _synthesize_elevenlabs(text, out_path, cfg)
        return
    raise RuntimeError(
        f"{lang.name} needs a cloud voice. Add OPENAI_API_KEY (or "
        f"ELEVENLABS_API_KEY) to your .env, or pick English / Hindi / Telugu "
        f"which have free offline voices."
    )


def _synth_scene(scene, audio_dir, cfg: Config, lang: Language) -> str:
    """Synthesize one scene's audio; return its path. Never raises — a failure
    falls back to silence so one bad scene can't break the batch."""
    out = str(audio_dir / f"scene_{scene.index:03d}.wav")
    if scene.audio_path and os.path.exists(scene.audio_path):
        return scene.audio_path
    try:
        _synth(scene.narration, out, cfg, lang)
    except Exception as e:
        log(f"  Scene {scene.index} voice failed ({e}); using silence.", "yellow")
        _write_silence(out)
    return out


def generate_voice(project: Project, cfg: Config) -> Project:
    from concurrent.futures import ThreadPoolExecutor

    lang = get_language(project.language)
    audio_dir = project.dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Network-bound providers (OpenAI/ElevenLabs) parallelize well; local Piper
    # is CPU-bound so we keep its concurrency modest to avoid thrashing.
    cloud = bool(os.getenv("OPENAI_API_KEY") or os.getenv("ELEVENLABS_API_KEY"))
    is_cloud = cloud and (lang.piper_voice is None
                          or cfg.voice.provider in ("openai", "elevenlabs"))
    workers = min(8, len(project.scenes)) if is_cloud else min(3, len(project.scenes))
    workers = max(1, workers)

    n = len(project.scenes)
    log(f"🎙️ Recording the narration for {n} scenes…")
    done = {"count": 0}
    done_lock = __import__("threading").Lock()

    def synth_and_log(s):
        path = _synth_scene(s, audio_dir, cfg, lang)
        with done_lock:
            done["count"] += 1
            log(f"🎙️ Voice ready: {done['count']} of {n} scenes")
        return path

    with ThreadPoolExecutor(max_workers=workers) as pool:
        paths = list(pool.map(synth_and_log, project.scenes))
    for scene, path in zip(project.scenes, paths):
        scene.audio_path = path
        scene.duration = round(_audio_duration(path), 3)

    project.voice_done = True
    project.save()
    total = sum(s.duration for s in project.scenes)
    log(f"✅ Narration done ({total:.0f} seconds of audio)", "green")
    return project
