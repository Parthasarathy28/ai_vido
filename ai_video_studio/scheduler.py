"""The automated pipeline.

Reads story ideas from `topics/queue.txt` (one prompt per line), generates a
full video for each, optionally uploads it, then moves the line to
`topics/done.txt`. Designed to be run by cron for a hands-off channel.

Cron example (one video every day at 9am):
  0 9 * * *  cd /path/to/project && python -m ai_video_studio auto >> logs/auto.log 2>&1
"""
from __future__ import annotations

from pathlib import Path

from .config import load_config
from .orchestrator import STAGE_NAMES, create_video
from .utils import console, log

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "topics" / "queue.txt"
DONE = ROOT / "topics" / "done.txt"


def _read_queue() -> list[str]:
    if not QUEUE.exists():
        return []
    return [ln.strip() for ln in QUEUE.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _pop_to_done(prompt: str) -> None:
    """Remove the prompt from the queue and append it to done.txt."""
    remaining = [ln for ln in QUEUE.read_text().splitlines() if ln.strip() != prompt]
    QUEUE.write_text("\n".join(remaining).strip() + "\n")
    with open(DONE, "a") as f:
        f.write(prompt + "\n")


def run_queue(once: bool = True) -> None:
    cfg = load_config()
    # upload only if the user enabled it in config
    to_stage = "upload" if cfg.youtube.enabled else "render"

    prompts = _read_queue()
    if not prompts:
        log(f"Queue empty: add story ideas to {QUEUE}", "yellow")
        return

    log(f"{len(prompts)} prompt(s) queued; rendering up to stage '{to_stage}'.")
    todo = prompts[:1] if once else prompts

    for prompt in todo:
        console.rule(f"[bold magenta]TOPIC: {prompt[:60]}")
        try:
            project = create_video(prompt, cfg=cfg, to_stage=to_stage)
            _pop_to_done(prompt)
            log(f"Done: {project.final_video_path}", "green")
        except Exception as e:  # keep the queue moving on failures
            log(f"Failed on '{prompt[:40]}…': {e}", "red")
            if once:
                raise


# Guard: keep STAGE_NAMES import used (and document valid stages for readers).
_VALID_STAGES = STAGE_NAMES
