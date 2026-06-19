"""Small shared helpers: logging, slugs, GPU detection, ffmpeg checks."""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
from functools import lru_cache

from rich.console import Console

console = Console()

# Thread-local log sink. The web layer sets this to a per-job callable so every
# log()/step() call in the pipeline streams to that job's live log — without
# threading a callback through every function. One sink per worker thread.
_sink = threading.local()


def set_log_sink(fn) -> None:
    """fn(message: str) is called for each log/step line on THIS thread.
    Pass None to detach."""
    _sink.fn = fn


def _emit(msg: str) -> None:
    fn = getattr(_sink, "fn", None)
    if fn:
        try:
            fn(msg)
        except Exception:
            pass


def log(msg: str, style: str = "cyan") -> None:
    console.print(f"[{style}]›[/{style}] {msg}")
    _emit(msg)


def step(name: str) -> None:
    # Console-only divider. We deliberately DON'T send this to the live log —
    # each stage emits its own friendly message, so the user log stays clean.
    console.rule(f"[bold green]{name}")


def slugify(text: str, max_len: int = 50) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len].strip("-") or "video"


@lru_cache
def has_cuda() -> bool:
    """True if a CUDA GPU is usable via torch. Cached; safe to call freely."""
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:
        return False


@lru_cache
def has_diffusers() -> bool:
    try:
        import diffusers  # noqa: F401  type: ignore

        return True
    except Exception:
        return False


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError(
            "ffmpeg not found. Install it: `sudo apt install ffmpeg` (Linux) "
            "or `brew install ffmpeg` (macOS)."
        )
    return path


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, raising with captured output on failure."""
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)
