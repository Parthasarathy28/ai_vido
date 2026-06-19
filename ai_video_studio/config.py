"""Central configuration, loaded from config.yaml with env-var overrides.

Everything has a sensible free default. You only touch this to swap providers
(e.g. turn on ElevenLabs voice) or tune video dimensions / quality.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


class ScriptConfig(BaseModel):
    # "auto" picks the best available: openai (OPENAI_API_KEY) →
    # anthropic (ANTHROPIC_API_KEY) → template (offline fallback).
    # Or force one: "openai" | "anthropic" | "template".
    provider: str = "auto"
    model: str = "claude-opus-4-8"          # used when provider == anthropic
    openai_model: str = "gpt-4o-mini"       # used when provider == openai
    target_scenes: int = 8           # roughly how many beats per video
    words_per_scene: int = 35        # ~13s of narration each → ~1.5 min videos


class VoiceConfig(BaseModel):
    # "piper" (free, local), "openai" (uses OPENAI_API_KEY, multilingual incl.
    # Tamil/Kannada), or "elevenlabs" (needs ELEVENLABS_API_KEY).
    provider: str = "piper"
    piper_voice: str = "en_US-amy-medium"   # auto-downloaded on first run
    openai_model: str = "gpt-4o-mini-tts"   # supports `instructions` steering
    openai_voice: str = "sage"              # warmer storyteller voice than alloy
    speaking_rate: float = 1.0


class ImageConfig(BaseModel):
    # "auto" -> stable diffusion if a CUDA GPU + diffusers are present,
    # else "dalle" if OPENAI_API_KEY set, else "pexels" if PEXELS_API_KEY set,
    # else free "pollinations", else "card" (gradient title cards).
    # Force one: "sd" | "dalle" | "pollinations" | "pexels" | "card".
    provider: str = "auto"
    # gpt-image-1 is OpenAI's current image model (dall-e-3 is deprecated/
    # unavailable on many accounts). Override via config if needed.
    dalle_model: str = "gpt-image-1"
    sd_model: str = "stabilityai/stable-diffusion-2-1-base"
    width: int = 768
    height: int = 768
    steps: int = 25
    style_suffix: str = ", cinematic, highly detailed, dramatic lighting, 4k"


class CaptionConfig(BaseModel):
    enabled: bool = True
    # whisper model size for timing: tiny/base/small. base is a good CPU balance.
    whisper_model: str = "base"
    font_size: int = 42


class VideoConfig(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 24                    # slideshow content; lower fps = faster render
    ken_burns: bool = True           # slow pan/zoom on still images
    music_volume: float = 0.12       # 0..1, ducked under narration
    crossfade: float = 0.5           # seconds between scenes


class YouTubeConfig(BaseModel):
    enabled: bool = False            # off until you've authorized + reviewed output
    client_secrets: str = "client_secrets.json"
    token_file: str = "youtube_token.json"


class Config(BaseModel):
    output_dir: str = str(ROOT / "output")
    music_dir: str = str(ROOT / "assets" / "music")

    script: ScriptConfig = Field(default_factory=ScriptConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    caption: CaptionConfig = Field(default_factory=CaptionConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)


@lru_cache
def load_config(path: str | None = None) -> Config:
    cfg_path = Path(path) if path else (ROOT / "config.yaml")
    data: dict = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text()) or {}
    cfg = Config.model_validate(data)
    return cfg
