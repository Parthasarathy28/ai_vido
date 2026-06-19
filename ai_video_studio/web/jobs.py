"""In-memory job manager for the web UI.

Each "generate" request becomes a Job that runs the pipeline in a background
thread. The orchestrator's on_progress callback updates the job's stage/status,
which the API streams to the browser. Kept deliberately simple (single-process,
in-memory) — fine for a personal/enterprise single-node dashboard.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field

from ..costs import start_tracking, stop_tracking, summary_line
from ..orchestrator import STAGE_NAMES, create_video, make_language_version
from ..utils import set_log_sink


class _Cancelled(Exception):
    """Raised at a stage boundary when the user requested a stop."""


def _finalize_cost(job, project, cb) -> None:
    """Log the cost summary and persist the breakdown onto the project."""
    job.cost = cb.as_dict()
    job.add_log(summary_line(cb))
    try:
        project.cost = cb.as_dict()
        project.save()
    except Exception:
        pass

# Human-friendly labels shown in the UI for each pipeline stage.
STAGE_LABELS = {
    "script": "Writing script",
    "voice": "Generating voice",
    "images": "Creating visuals",
    "captions": "Adding subtitles",
    "render": "Rendering video",
    "upload": "Uploading to YouTube",
}


@dataclass
class Job:
    id: str
    prompt: str
    to_stage: str
    mode: str = "auto"
    orientation: str = "horizontal"
    language: str = "en"
    style: str = "cartoon3d"
    status: str = "queued"          # queued | running | done | error
    stage: str = ""                 # current pipeline stage key
    stage_label: str = ""
    progress: float = 0.0           # 0..1 across the stages we will run
    error: str = ""
    project_id: str | None = None
    workdir: str | None = None
    video_url: str | None = None    # set when final.mp4 exists
    youtube_id: str | None = None
    cost: dict = field(default_factory=dict)        # OpenAI cost breakdown (USD)
    cancelled: bool = False                          # user requested stop
    log: list[str] = field(default_factory=list)    # live, human-readable steps

    def add_log(self, msg: str) -> None:
        self.log.append(msg)
        # Keep memory bounded for very long runs.
        if len(self.log) > 400:
            del self.log[:200]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "status": self.status,
            "stage": self.stage,
            "stage_label": self.stage_label,
            "progress": round(self.progress, 3),
            "error": self.error,
            "project_id": self.project_id,
            "video_url": self.video_url,
            "youtube_id": self.youtube_id,
            "cost": self.cost,
            "log": self.log,
        }


# Max videos generating at the same time. Extra jobs wait in the queue.
MAX_CONCURRENT = 5


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Limits how many jobs RUN at once; the rest stay "queued" until a slot frees.
        self._slots = threading.Semaphore(MAX_CONCURRENT)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. The worker stops at the next stage boundary."""
        job = self._jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.cancelled = True
            job.add_log("⏹ Stopping… (will stop at the next step)")
            return True
        return False

    def all(self) -> list[Job]:
        return list(self._jobs.values())

    def start(self, prompt: str, to_stage: str = "render",
              mode: str = "auto", orientation: str = "horizontal",
              language: str = "en", style: str = "cartoon3d") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], prompt=prompt, to_stage=to_stage,
                  mode=mode, orientation=orientation, language=language, style=style)
        with self._lock:
            self._jobs[job.id] = job
        t = threading.Thread(target=self._run, args=(job,), daemon=True)
        t.start()
        return job

    def start_translate(self, source_dir: str, language: str) -> Job:
        """Start a job that makes a language version of an existing project."""
        job = Job(id=uuid.uuid4().hex[:12], prompt=f"[language version] {language}",
                  to_stage="render", language=language)
        with self._lock:
            self._jobs[job.id] = job
        t = threading.Thread(target=self._run_translate,
                             args=(job, source_dir), daemon=True)
        t.start()
        return job

    def _run_translate(self, job: Job, source_dir: str) -> None:
        if job.cancelled:
            job.status = "cancelled"; return
        self._slots.acquire()
        try:
            if job.cancelled:
                job.status = "cancelled"; return
            self._run_translate_inner(job, source_dir)
        finally:
            self._slots.release()

    def _run_translate_inner(self, job: Job, source_dir: str) -> None:
        job.status = "running"
        planned = ["script", "voice", "captions", "render"]
        total = len(planned)

        def on_progress(stage: str, status: str, project) -> None:
            if job.cancelled:
                raise _Cancelled()
            job.project_id = project.id
            job.workdir = project.workdir
            if status == "start":
                job.stage = stage
                job.stage_label = STAGE_LABELS.get(stage, stage)
            elif status == "done":
                job.progress = (planned.index(stage) + 1) / total
                if project.final_video_path:
                    job.video_url = f"/api/jobs/{job.id}/video"

        set_log_sink(job.add_log)
        cb = start_tracking()
        try:
            project = make_language_version(source_dir, job.language,
                                            on_progress=on_progress)
            _finalize_cost(job, project, cb)
            job.project_id = project.id
            job.workdir = project.workdir
            job.video_url = f"/api/jobs/{job.id}/video"
            job.status = "done"
            job.progress = 1.0
            job.stage_label = "Finished"
        except _Cancelled:
            job.status = "cancelled"
            job.stage_label = "Stopped"
            job.add_log("⏹ Stopped by user.")
        except Exception as e:
            job.status = "error"
            job.error = f"{type(e).__name__}: {e}"
            job.add_log(f"❌ {job.error}")
            traceback.print_exc()
        finally:
            stop_tracking()
            set_log_sink(None)

    # ------------------------------------------------------------------
    def _run(self, job: Job) -> None:
        # Wait for a free slot (max MAX_CONCURRENT videos at once); stays "queued".
        if job.cancelled:
            job.status = "cancelled"; return
        self._slots.acquire()
        try:
            if job.cancelled:
                job.status = "cancelled"; return
            self._run_inner(job)
        finally:
            self._slots.release()

    def _run_inner(self, job: Job) -> None:
        job.status = "running"
        # Which stages will actually run, so progress is a real fraction.
        end = STAGE_NAMES.index(job.to_stage)
        planned = STAGE_NAMES[: end + 1]
        total = len(planned)

        def on_progress(stage: str, status: str, project) -> None:
            if job.cancelled:
                raise _Cancelled()
            job.project_id = project.id
            job.workdir = project.workdir
            if status == "start":
                job.stage = stage
                job.stage_label = STAGE_LABELS.get(stage, stage)
            elif status == "done":
                done_count = planned.index(stage) + 1
                job.progress = done_count / total
                if project.final_video_path:
                    job.video_url = f"/api/jobs/{job.id}/video"
                if project.youtube_video_id:
                    job.youtube_id = project.youtube_video_id

        set_log_sink(job.add_log)
        cb = start_tracking()
        try:
            project = create_video(
                job.prompt, to_stage=job.to_stage, on_progress=on_progress,
                mode=job.mode, orientation=job.orientation, language=job.language,
                style=job.style,
            )
            _finalize_cost(job, project, cb)
            job.project_id = project.id
            job.workdir = project.workdir
            if project.final_video_path:
                job.video_url = f"/api/jobs/{job.id}/video"
            job.youtube_id = project.youtube_video_id
            job.status = "done"
            job.progress = 1.0
            job.stage_label = "Finished"
        except _Cancelled:
            job.status = "cancelled"
            job.stage_label = "Stopped"
            job.add_log("⏹ Stopped by user.")
        except Exception as e:  # surface the failure to the UI
            job.status = "error"
            job.error = f"{type(e).__name__}: {e}"
            job.add_log(f"❌ {job.error}")
            traceback.print_exc()
        finally:
            stop_tracking()
            set_log_sink(None)


# Singleton used by the FastAPI app.
manager = JobManager()
