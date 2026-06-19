"""Command-line interface.

Examples:
  python -m ai_video_studio create "The legend of the lost city of gold"
  python -m ai_video_studio create-file topics/story.txt
  python -m ai_video_studio resume output/20260617-...-the-legend --from images
  python -m ai_video_studio auto          # run the topic-queue scheduler once
  python -m ai_video_studio info          # show detected providers / capabilities
"""
from __future__ import annotations

from pathlib import Path

import typer

from .config import load_config
from .orchestrator import STAGE_NAMES, create_video
from .utils import console, has_cuda, has_diffusers

app = typer.Typer(add_completion=False, help="AI Video Studio — story → narrated video.")


@app.command()
def create(
    prompt: str = typer.Argument(..., help="The story idea / prompt."),
    to: str = typer.Option("render", help=f"Stop after this stage: {STAGE_NAMES}"),
):
    """Generate a video from a prompt (stops before upload by default)."""
    create_video(prompt, to_stage=to)


@app.command("create-file")
def create_file(
    path: Path = typer.Argument(..., exists=True, readable=True),
    to: str = typer.Option("render"),
):
    """Generate a video from a .txt story file."""
    create_video(path.read_text().strip(), to_stage=to)


@app.command()
def resume(
    workdir: Path = typer.Argument(..., help="An output/<project> folder."),
    from_: str = typer.Option("script", "--from", help=f"Stage: {STAGE_NAMES}"),
    to: str = typer.Option("render", help=f"Stage: {STAGE_NAMES}"),
):
    """Resume an existing project from a given stage."""
    create_video("", from_stage=from_, to_stage=to, resume_dir=str(workdir))


@app.command()
def auto(
    once: bool = typer.Option(True, help="Run one pass over the topic queue and exit."),
):
    """Run the automated topic-queue → video → upload pipeline."""
    from .scheduler import run_queue

    run_queue(once=once)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address."),
    port: int = typer.Option(8000, help="Port."),
):
    """Launch the web dashboard UI, then open the printed URL in your browser."""
    import uvicorn

    console.print(f"\n[bold green]AI Video Studio UI[/bold green] → "
                  f"[cyan]http://localhost:{port}[/cyan]  (Ctrl+C to stop)\n")
    uvicorn.run("ai_video_studio.web.app:app", host=host, port=port, reload=False)


@app.command()
def info():
    """Show detected capabilities and active providers."""
    cfg = load_config()
    import os

    rows = [
        ("CUDA GPU", "yes" if has_cuda() else "no"),
        ("diffusers (Stable Diffusion)", "yes" if has_diffusers() else "no"),
        ("ANTHROPIC_API_KEY", "set" if os.getenv("ANTHROPIC_API_KEY") else "unset"),
        ("PEXELS_API_KEY", "set" if os.getenv("PEXELS_API_KEY") else "unset"),
        ("ELEVENLABS_API_KEY", "set" if os.getenv("ELEVENLABS_API_KEY") else "unset"),
        ("Script provider", cfg.script.provider),
        ("Voice provider", cfg.voice.provider),
        ("Image provider", cfg.image.provider),
        ("YouTube upload", "enabled" if cfg.youtube.enabled else "disabled"),
        ("Output dir", cfg.output_dir),
    ]
    console.print("\n[bold]AI Video Studio — capabilities[/bold]")
    for k, v in rows:
        console.print(f"  {k:<32} [cyan]{v}[/cyan]")


if __name__ == "__main__":
    app()
