"""Stage 3 — IMAGES.

One visual per scene. Provider selection (when image.provider == "auto"):
  1. Stable Diffusion  — if a CUDA GPU + `diffusers` are installed (free, local).
  2. Pexels stock      — if PEXELS_API_KEY is set (free API, real photos).
  3. Gradient card     — always works, zero deps: a colored card with the prompt.

The Stable Diffusion pipeline is loaded once and reused across scenes.
"""
from __future__ import annotations

import hashlib
import os
import textwrap

from ..config import Config
from ..models import Project
from ..utils import has_cuda, has_diffusers, log

_SD_PIPE = None  # module-level cache so we load the model only once


def _pick_provider(cfg: Config) -> str:
    if cfg.image.provider != "auto":
        return cfg.image.provider
    if has_cuda() and has_diffusers():
        return "sd"
    if os.getenv("OPENAI_API_KEY"):
        return "dalle"
    if os.getenv("PEXELS_API_KEY"):
        return "pexels"
    # Free, no-GPU, no-key text-to-image. Best default for this server —
    # produces a REAL image (incl. characters), unlike gradient cards.
    if os.getenv("DISABLE_POLLINATIONS") != "1":
        return "pollinations"
    return "card"


# --------------------------- Stable Diffusion ---------------------------
def _load_sd(cfg: Config):
    global _SD_PIPE
    if _SD_PIPE is not None:
        return _SD_PIPE
    import torch
    from diffusers import StableDiffusionPipeline

    log(f"Loading Stable Diffusion ({cfg.image.sd_model}) on GPU…")
    pipe = StableDiffusionPipeline.from_pretrained(
        cfg.image.sd_model, torch_dtype=torch.float16
    )
    pipe = pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    _SD_PIPE = pipe
    return pipe


def _gen_sd(prompt: str, out: str, cfg: Config, size: tuple[int, int], seed: int) -> None:
    import torch

    pipe = _load_sd(cfg)
    w, h = _sd_safe_size(size)
    # Same seed across scenes → consistent style/character look.
    generator = torch.Generator(device="cuda").manual_seed(seed)
    image = pipe(
        prompt,
        width=w,
        height=h,
        num_inference_steps=cfg.image.steps,
        generator=generator,
    ).images[0]
    image.save(out)


# ----------------------------- OpenAI images ---------------------------
def _gen_dalle(prompt: str, out: str, cfg: Config, size: tuple[int, int],
               seed: int) -> None:
    """OpenAI image generation (gpt-image-1 by default; dall-e-3 if configured).
    Costs per image. The model only allows a few fixed sizes, so we pick the
    closest to the requested orientation."""
    import base64

    import requests
    from openai import OpenAI

    w, h = size
    model = cfg.image.dalle_model
    if model.startswith("dall-e-3"):
        api_size = "1024x1792" if h > w else ("1792x1024" if w > h else "1024x1024")
    else:  # gpt-image-1 family
        api_size = "1024x1536" if h > w else ("1536x1024" if w > h else "1024x1024")

    client = OpenAI()  # reads OPENAI_API_KEY
    resp = client.images.generate(
        model=model, prompt=prompt[:3900], size=api_size, n=1,
    )
    usage = getattr(resp, "usage", None)
    if usage is not None:
        from ..costs import record_image
        record_image(model, getattr(usage, "total_tokens", 0) or 0, count=1)
    item = resp.data[0]
    # gpt-image-1 returns base64; dall-e-3 returns a URL. Handle both.
    if getattr(item, "b64_json", None):
        data = base64.b64decode(item.b64_json)
    else:
        data = requests.get(item.url, timeout=120).content
    with open(out, "wb") as f:
        f.write(data)


# ---------------------------- Pollinations -----------------------------
def _gen_pollinations(prompt: str, out: str, cfg: Config,
                      size: tuple[int, int], seed: int) -> None:
    """Free, no-key, no-GPU text-to-image. Generates a real image (characters,
    scenes) on any server. Same seed across scenes keeps the look consistent."""
    import urllib.parse

    import requests

    w, h = size
    encoded = urllib.parse.quote(prompt[:900])
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={w}&height={h}&seed={seed}&nologo=true&model=flux"
    )
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    if not r.content or len(r.content) < 1000:
        raise RuntimeError("Pollinations returned an empty image")
    with open(out, "wb") as f:
        f.write(r.content)


def _sd_safe_size(size: tuple[int, int]) -> tuple[int, int]:
    """SD needs dimensions that are multiples of 8 and not too large for VRAM.
    We cap the long side ~768 and round to /8 while keeping the aspect ratio."""
    w, h = size
    long = max(w, h)
    scale = min(1.0, 768 / long)
    w = max(8, int(round(w * scale / 8)) * 8)
    h = max(8, int(round(h * scale / 8)) * 8)
    return w, h


def _pexels_query(prompt: str) -> str:
    """Build a clean search query: drop our style suffix and bracketed notes,
    keep the first few meaningful words."""
    base = prompt.split(",")[0]
    base = base.replace("cinematic scene depicting:", "").replace("cinematic scene:", "")
    words = [w for w in base.split() if w.isalpha() or w.isalnum()]
    return " ".join(words[:6]) or "cinematic"


# ------------------------------- Pexels --------------------------------
def _gen_pexels(prompt: str, out: str, cfg: Config, size: tuple[int, int],
                seed: int) -> None:
    import requests

    orientation = "portrait" if size[1] > size[0] else "landscape"
    query = _pexels_query(prompt)
    r = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": os.environ["PEXELS_API_KEY"]},
        params={"query": query, "per_page": 1, "orientation": orientation},
        timeout=60,
    )
    r.raise_for_status()
    photos = r.json().get("photos", [])
    if not photos:
        raise RuntimeError(f"No Pexels results for '{query}'")
    img_url = photos[0]["src"]["large2x"]
    img = requests.get(img_url, timeout=60).content
    with open(out, "wb") as f:
        f.write(img)


# --------------------------- Gradient card -----------------------------
def _card_text(prompt: str) -> str:
    """Extract a clean, human-readable line from an image prompt for the
    placeholder card — drop the character-injection preamble and style suffix
    that are meant for the image model, not for display."""
    text = prompt
    # The scene-specific part follows our "Scene: " marker when a character
    # was injected; prefer that.
    if "Scene: " in text:
        text = text.split("Scene: ", 1)[1]
    text = text.split(",")[0]                      # drop style suffix
    text = text.split(". This exact")[0]           # safety: drop leftover cues
    return text.strip() or "Scene"


def _write_solid(out: str, size: tuple[int, int]) -> None:
    """Last-resort image: a plain dark frame. Never raises on normal input."""
    from PIL import Image
    Image.new("RGB", size, (18, 20, 38)).save(out)


def _gen_card(prompt: str, out: str, cfg: Config, size: tuple[int, int],
              seed: int = 0) -> None:
    """Deterministic, dependency-light visual: a vertical gradient derived from
    a hash of the prompt, with the scene text wrapped on top. Always works and
    is a placeholder — real images come from SD (GPU) or Pexels."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = size
    seed = int(hashlib.md5(prompt.encode()).hexdigest(), 16)
    top = ((seed >> 0) & 255, (seed >> 8) & 255, (seed >> 16) & 255)
    bot = ((seed >> 24) & 255, (seed >> 32) & 255, (seed >> 40) & 255)

    # Fast vertical gradient: build a 1xH strip and resize to full width.
    strip = Image.new("RGB", (1, h))
    sp = strip.load()
    for y in range(h):
        t = y / h
        sp[0, y] = tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3))
    img = strip.resize((w, h))

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=max(18, int(w / 18)))
    except Exception:
        font = ImageFont.load_default()

    text = _card_text(prompt)
    wrapped = textwrap.fill(text, width=24)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.multiline_text(
        ((w - tw) / 2, (h - th) / 2), wrapped, fill="white", font=font,
        align="center", stroke_width=2, stroke_fill="black",
    )
    img.save(out)


_GENERATORS = {
    "sd": _gen_sd,
    "dalle": _gen_dalle,
    "pollinations": _gen_pollinations,
    "pexels": _gen_pexels,
    "card": _gen_card,
}


def _target_size(project: Project, cfg: Config) -> tuple[int, int]:
    """Image generation size matched to the video orientation."""
    if project.orientation == "vertical":
        return 768, 1344          # ~9:16, /8-friendly for SD
    return 1344, 768              # ~16:9


def generate_images(project: Project, cfg: Config) -> Project:
    provider = _pick_provider(cfg)
    gen = _GENERATORS[provider]
    size = _target_size(project, cfg)
    seed = project.seed or 1
    if project.character:
        log("🎭 Keeping the same character across every scene")

    img_dir = project.dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    def make_one(scene):
        out = str(img_dir / f"scene_{scene.index:03d}.png")
        if scene.image_path and os.path.exists(scene.image_path):
            return out
        try:
            # Same seed for every scene → consistent style/character look.
            gen(scene.image_prompt, out, cfg, size, seed)
        except Exception as e:
            log(f"  Scene {scene.index}: {provider} failed ({e}); gradient card.",
                "yellow")
            try:
                _gen_card(scene.image_prompt, out, cfg, size, seed)
            except Exception as e2:
                log(f"  Scene {scene.index}: card also failed ({e2}); solid frame.",
                    "yellow")
        # Guarantee a file exists so the render can never crash on a missing image.
        if not os.path.exists(out):
            _write_solid(out, size)
        return out

    # Cloud providers (gpt-image/pollinations/pexels) are network-bound → run in
    # parallel for a big speedup. Local SD shares one GPU/model → keep serial.
    n = len(project.scenes)
    log(f"🎨 Creating the visuals for {n} scenes…")
    if provider == "sd":
        for i, scene in enumerate(project.scenes, 1):
            scene.image_path = make_one(scene)
            log(f"🎨 Scene {i} of {n} painted")
    else:
        from concurrent.futures import ThreadPoolExecutor
        workers = max(1, min(6, n))
        done = {"c": 0}
        lock = __import__("threading").Lock()

        def run_and_log(scene):
            p = make_one(scene)
            with lock:
                done["c"] += 1
                log(f"🎨 Scene {done['c']} of {n} painted")
            return p

        with ThreadPoolExecutor(max_workers=workers) as pool:
            paths = list(pool.map(run_and_log, project.scenes))
        for scene, p in zip(project.scenes, paths):
            scene.image_path = p

    project.images_done = True
    project.save()
    log(f"✅ All {n} visuals ready", "green")
    return project
