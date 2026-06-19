"""FastAPI app — the web dashboard backend.

Endpoints:
  GET  /                       → the dashboard page (static index.html)
  GET  /api/info               → detected capabilities (GPU, providers)
  POST /api/generate           → start a generation job  {prompt, upload}
  GET  /api/jobs/{id}          → poll a job's live status
  GET  /api/jobs/{id}/video    → stream that job's final.mp4
  GET  /api/library            → list all generated projects (gallery)
  GET  /api/projects/{id}/video→ stream a library project's final.mp4

Run:  python -m ai_video_studio serve   (or: uvicorn ai_video_studio.web.app:app)
"""
from __future__ import annotations

import os
from pathlib import Path

# Load .env so ANTHROPIC_API_KEY etc. are available without manual export.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import load_config
from ..languages import get_language, ui_list, voice_available
from ..styles import get_style
from ..styles import ui_list as styles_ui_list
from ..models import Project
from ..utils import has_cuda, has_diffusers
from .jobs import manager

app = FastAPI(title="AI Video Studio")

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve index.html with version-stamped asset URLs (app.js?v=…&style.css?v=…)
    so the browser always fetches fresh JS/CSS after an update — no manual
    hard-refresh needed."""
    html = (STATIC_DIR / "index.html").read_text()
    try:
        v = int(max(
            (STATIC_DIR / "app.js").stat().st_mtime,
            (STATIC_DIR / "style.css").stat().st_mtime,
        ))
    except OSError:
        v = 1
    html = html.replace("/app.js", f"/app.js?v={v}")
    html = html.replace("/style.css", f"/style.css?v={v}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def _no_cache_static(request, call_next):
    """Never cache the app shell (html/js/css) so UI updates show up on refresh
    without a manual hard-reload. Videos/images can still be cached normally."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".css", ".html")):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


class GenerateRequest(BaseModel):
    prompt: str
    upload: bool = False
    mode: str = "auto"               # auto | idea | script
    orientation: str = "horizontal"  # horizontal | vertical
    language: str = "en"             # en | hi | te | ta | kn
    style: str = "cartoon3d"         # cartoon3d | realistic | anime | storybook


@app.get("/api/info")
def api_info():
    cfg = load_config()
    return {
        "gpu": has_cuda(),
        "diffusers": has_diffusers(),
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "pexels": bool(os.getenv("PEXELS_API_KEY")),
        "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
        "script_provider": cfg.script.provider,
        "image_provider": cfg.image.provider,
        "voice_provider": cfg.voice.provider,
        "youtube_enabled": cfg.youtube.enabled,
    }


@app.post("/api/generate")
def api_generate(req: GenerateRequest):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(400, "Prompt is required.")
    cfg = load_config()
    # Only allow upload if YouTube is configured + the user ticked the box.
    to_stage = "upload" if (req.upload and cfg.youtube.enabled) else "render"
    mode = req.mode if req.mode in ("auto", "idea", "script") else "auto"
    orientation = "vertical" if req.orientation == "vertical" else "horizontal"
    language = get_language(req.language).code  # validates + defaults to en

    # Guard: a language with no free voice needs a cloud TTS key (OpenAI/11Labs).
    if not voice_available(language):
        lang_name = get_language(language).name
        raise HTTPException(
            400,
            f"{lang_name} needs a cloud voice. Add OPENAI_API_KEY (or "
            f"ELEVENLABS_API_KEY) to your .env, or choose English, Hindi or "
            f"Telugu (free voice).",
        )

    style = get_style(req.style).code
    job = manager.start(prompt, to_stage=to_stage, mode=mode,
                        orientation=orientation, language=language, style=style)
    return job.as_dict()


@app.get("/api/styles")
def api_styles():
    return styles_ui_list()


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel(job_id: str):
    """Request stop on a running generation."""
    if not manager.cancel(job_id):
        raise HTTPException(404, "Job not running.")
    return {"ok": True}


@app.delete("/api/projects/{project_id}")
def api_delete(project_id: str):
    """Delete a generated video and all its files."""
    import shutil

    cfg = load_config()
    d = Path(cfg.output_dir) / project_id
    # Guard: must be a real project dir inside the output folder.
    if not (d / "project.json").exists():
        raise HTTPException(404, "Project not found.")
    try:
        shutil.rmtree(d)
    except Exception as e:
        raise HTTPException(500, f"Could not delete: {e}")
    return {"ok": True}


@app.get("/api/languages")
def api_languages():
    return ui_list()


@app.get("/api/projects/{project_id}/versions")
def api_versions(project_id: str):
    """All language versions available for this story: its own language plus
    any translated children, each with a playable video URL. Used by the player
    to swap languages in place."""
    cfg = load_config()
    out = Path(cfg.output_dir)
    src_pj = out / project_id / "project.json"
    if not src_pj.exists():
        raise HTTPException(404, "Project not found.")
    src = Project.load(out / project_id)
    root_id = src.parent_id or src.id

    versions = {}
    if out.exists():
        for d in out.iterdir():
            pj = d / "project.json"
            if not pj.exists():
                continue
            try:
                p = Project.load(d)
            except Exception:
                continue
            this_root = p.parent_id or p.id
            if this_root != root_id:
                continue
            if (d / "final.mp4").exists():
                versions[p.language] = {
                    "language": p.language,
                    "project_id": p.id,
                    "video_url": f"/api/projects/{p.id}/video",
                }
    return {"root_id": root_id, "versions": list(versions.values())}


class TranslateRequest(BaseModel):
    language: str


@app.post("/api/projects/{project_id}/translate")
def api_translate(project_id: str, req: TranslateRequest):
    """Create a new-language version of an existing project (reuses its images)."""
    cfg = load_config()
    src_dir = Path(cfg.output_dir) / project_id
    if not (src_dir / "project.json").exists():
        raise HTTPException(404, "Project not found.")

    language = get_language(req.language).code
    if not voice_available(language):
        lang_name = get_language(language).name
        raise HTTPException(
            400,
            f"{lang_name} needs a cloud voice. Add OPENAI_API_KEY (or "
            f"ELEVENLABS_API_KEY), or pick English, Hindi or Telugu.",
        )
    job = manager.start_translate(str(src_dir), language)
    return job.as_dict()


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "No such job.")
    return job.as_dict()


@app.get("/api/jobs/{job_id}/video")
def api_job_video(job_id: str):
    job = manager.get(job_id)
    if not job or not job.workdir:
        raise HTTPException(404, "No such job.")
    path = Path(job.workdir) / "final.mp4"
    if not path.exists():
        raise HTTPException(404, "Video not ready.")
    return FileResponse(path, media_type="video/mp4", filename=f"{job.project_id}.mp4")


@app.get("/api/library")
def api_library():
    """List every generated project (newest first) for the gallery."""
    cfg = load_config()
    out = Path(cfg.output_dir)
    items = []
    if out.exists():
        for d in sorted(out.iterdir(), reverse=True):
            pj = d / "project.json"
            if not pj.exists():
                continue
            try:
                p = Project.load(d)
            except Exception:
                continue
            has_video = (d / "final.mp4").exists()
            items.append({
                "id": p.id,
                "prompt": p.prompt,
                "title": p.meta.title,
                "created_at": p.created_at,
                "scenes": len(p.scenes),
                "has_video": has_video,
                "video_url": f"/api/projects/{p.id}/video" if has_video else None,
                "youtube_id": p.youtube_video_id,
                "cost": p.cost,
            })
    return items


@app.get("/api/projects/{project_id}/video")
def api_project_video(project_id: str):
    cfg = load_config()
    path = Path(cfg.output_dir) / project_id / "final.mp4"
    if not path.exists():
        raise HTTPException(404, "Video not found.")
    return FileResponse(path, media_type="video/mp4", filename=f"{project_id}.mp4")


# Serve the dashboard (index.html + assets) at the root.
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:  # pragma: no cover
    @app.get("/")
    def _missing():
        return JSONResponse({"error": "static dir missing"}, status_code=500)
