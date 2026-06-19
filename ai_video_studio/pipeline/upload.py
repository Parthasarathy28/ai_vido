"""Stage 6 — UPLOAD.

Upload the finished MP4 to YouTube via the YouTube Data API v3.

First-time setup (one-off):
  1. Create a Google Cloud project, enable "YouTube Data API v3".
  2. Create OAuth client credentials (type: Desktop app), download as
     client_secrets.json into the project root.
  3. Run any upload once; a browser window authorizes the channel and a
     youtube_token.json is cached for subsequent headless uploads.

Uploads default to PRIVATE (see config) so you can review before going public.
"""
from __future__ import annotations

import os

from ..config import Config
from ..models import Project
from ..utils import log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _get_service(cfg: Config):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_file = cfg.youtube.token_file
    secrets = cfg.youtube.client_secrets
    creds = None

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(secrets):
                raise RuntimeError(
                    f"Missing {secrets}. See upload.py docstring for setup."
                )
            flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_video(project: Project, cfg: Config) -> Project:
    if not cfg.youtube.enabled:
        log("YouTube upload disabled (youtube.enabled = false). Skipping.", "yellow")
        return project
    if not project.final_video_path or not os.path.exists(project.final_video_path):
        raise RuntimeError("No final video to upload — run the render stage first.")

    from googleapiclient.http import MediaFileUpload

    service = _get_service(cfg)
    meta = project.meta
    body = {
        "snippet": {
            "title": meta.title or project.prompt[:90],
            "description": meta.description,
            "tags": meta.tags,
            "categoryId": meta.category_id,
        },
        "status": {"privacyStatus": meta.privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(project.final_video_path, chunksize=-1, resumable=True)

    log(f"Uploading to YouTube as “{body['snippet']['title']}” ({meta.privacy})…")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"  {int(status.progress() * 100)}% uploaded", "dim")

    vid = response["id"]
    project.youtube_video_id = vid
    project.save()
    log(f"✅ Uploaded: https://youtu.be/{vid}", "bold green")
    return project
