"""OpenAI usage & cost tracking, per video.

A thread-local accumulator collects usage from each pipeline stage (script,
images, voice) while a job runs, so we can show "this video cost ~$X" broken
down by stage. Prices are USD per the units OpenAI bills in; update PRICES if
OpenAI changes them.

Cost is an ESTIMATE — it's computed from the usage OpenAI returns, multiplied by
published list prices. Always treat it as approximate.
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field

# --- Published OpenAI list prices (USD). Update here if they change. ---
PRICES = {
    # chat models: per 1M tokens (input, output)
    "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
    "gpt-4o": (2.50 / 1_000_000, 10.00 / 1_000_000),
    # image model gpt-image-1: per 1M tokens (input text, output image)
    "gpt-image-1": (5.00 / 1_000_000, 40.00 / 1_000_000),
    # tts: per 1M characters
    "gpt-4o-mini-tts": (0.60 / 1_000_000, 0.0),
    "tts-1": (15.00 / 1_000_000, 0.0),
}


@dataclass
class CostBreakdown:
    script_usd: float = 0.0
    image_usd: float = 0.0
    voice_usd: float = 0.0
    # raw counts for transparency
    script_tokens_in: int = 0
    script_tokens_out: int = 0
    image_count: int = 0
    image_tokens: int = 0
    voice_chars: int = 0

    @property
    def total_usd(self) -> float:
        return round(self.script_usd + self.image_usd + self.voice_usd, 4)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total_usd"] = self.total_usd
        # round money for display
        for k in ("script_usd", "image_usd", "voice_usd"):
            d[k] = round(d[k], 4)
        return d


_local = threading.local()


def start_tracking() -> CostBreakdown:
    """Begin a fresh accumulator for the current thread (one per video)."""
    cb = CostBreakdown()
    _local.cb = cb
    return cb


def current() -> CostBreakdown | None:
    return getattr(_local, "cb", None)


def stop_tracking() -> None:
    _local.cb = None


def record_chat(model: str, tokens_in: int, tokens_out: int) -> None:
    cb = current()
    if cb is None:
        return
    pin, pout = PRICES.get(model, PRICES["gpt-4o-mini"])
    cb.script_usd += tokens_in * pin + tokens_out * pout
    cb.script_tokens_in += tokens_in
    cb.script_tokens_out += tokens_out


def record_image(model: str, image_tokens: int, count: int = 1) -> None:
    cb = current()
    if cb is None:
        return
    _, pout = PRICES.get(model, PRICES["gpt-image-1"])
    cb.image_usd += image_tokens * pout
    cb.image_tokens += image_tokens
    cb.image_count += count


def record_tts(model: str, chars: int) -> None:
    cb = current()
    if cb is None:
        return
    pin, _ = PRICES.get(model, PRICES["gpt-4o-mini-tts"])
    cb.voice_usd += chars * pin
    cb.voice_chars += chars


def summary_line(cb: CostBreakdown) -> str:
    """One human-readable line for the live log."""
    return (
        f"💰 OpenAI cost ≈ ${cb.total_usd:.3f} "
        f"(script ${cb.script_usd:.3f} · images ${cb.image_usd:.3f} "
        f"[{cb.image_count}] · voice ${cb.voice_usd:.3f})"
    )
