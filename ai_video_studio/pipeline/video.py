"""Stage 5 — ASSEMBLE.

Render the final MP4 with ffmpeg:
  * each scene = its image with a slow Ken Burns zoom, held for the scene's
    audio duration, scaled/padded to the target resolution;
  * scenes concatenated into one video track;
  * narration clips concatenated into one audio track;
  * optional background music mixed in, ducked under the narration;
  * optional burned-in subtitles from captions.srt.

We shell out to ffmpeg (already required) rather than depend on moviepy — fewer
moving parts, faster, and rock-solid.
"""
from __future__ import annotations

import os
import random
from pathlib import Path

from ..config import Config
from ..languages import get_language
from ..models import Project
from ..utils import log, require_ffmpeg, run


# ------------------------------ audio utils ------------------------------
def concat_audio(wavs: list[str], out: str) -> None:
    """Concatenate WAV files into one, re-encoding to a uniform format."""
    require_ffmpeg()
    list_file = Path(out).with_suffix(".txt")
    list_file.write_text("".join(f"file '{os.path.abspath(w)}'\n" for w in wavs))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-ar", "44100", "-ac", "2", out])
    list_file.unlink(missing_ok=True)


# --------------------------- dimensions ---------------------------------
def resolve_dimensions(orientation: str, cfg: Config) -> tuple[int, int]:
    """Map orientation → (width, height). Vertical = 9:16 Shorts/Reels."""
    if orientation == "vertical":
        return 1080, 1920
    # horizontal default uses the configured (landscape) size
    w, h = cfg.video.width, cfg.video.height
    return (w, h) if w >= h else (h, w)


# ------------------------------ scene clips ------------------------------
def _render_scene_clip(image: str, duration: float, out: str, cfg: Config,
                       width: int, height: int) -> None:
    """One image → an MP4 clip of `duration` seconds with a Ken Burns zoom,
    sized to (width, height)."""
    v = cfg.video
    frames = max(1, int(round(duration * v.fps)))

    # Ken Burns zoom. We run zoompan at ~1.25x the target (not 2x) — enough
    # headroom for a smooth zoom while keeping the per-frame cost far lower,
    # which is the single biggest render-time saver for these slideshows.
    if v.ken_burns:
        bw, bh = int(width * 1.25) // 2 * 2, int(height * 1.25) // 2 * 2
        zoom = (
            f"scale={bw}:{bh},"
            f"zoompan=z='min(zoom+0.0008,1.2)':d={frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={width}x{height}:fps={v.fps}"
        )
    else:
        zoom = f"scale={width}:{height}:force_original_aspect_ratio=increase," \
               f"crop={width}:{height}"

    vf = (
        f"{zoom},setsar=1,"
        # ensure exact target box with padding if needed
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    )

    run([
        "ffmpeg", "-y", "-loop", "1", "-i", image,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-r", str(v.fps),
        out,
    ])


def _concat_clips(clips: list[str], out: str) -> None:
    list_file = Path(out).with_suffix(".clips.txt")
    list_file.write_text("".join(f"file '{os.path.abspath(c)}'\n" for c in clips))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", out])
    list_file.unlink(missing_ok=True)


def _pick_music(cfg: Config) -> str | None:
    music_dir = Path(cfg.music_dir)
    if not music_dir.exists():
        return None
    tracks = [p for p in music_dir.iterdir()
              if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg"}]
    if not tracks:
        return None
    # Deterministic-ish: seed by track count so repeated renders are stable.
    random.seed(len(tracks))
    return str(random.choice(tracks))


# ------------------------------- the mix --------------------------------
def assemble_video(project: Project, cfg: Config) -> Project:
    require_ffmpeg()
    v = cfg.video
    width, height = resolve_dimensions(project.orientation, cfg)
    tmp = project.dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    log(f"Output: {width}x{height} ({project.orientation})")

    # 1. per-scene silent video clips (rendered in parallel — big speedup)
    log("🎬 Putting the scenes together…")
    from concurrent.futures import ThreadPoolExecutor

    def render_clip(scene):
        clip = str(tmp / f"clip_{scene.index:03d}.mp4")
        _render_scene_clip(scene.image_path, scene.duration, clip, cfg, width, height)
        return clip

    workers = max(1, min(4, len(project.scenes)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        clips = list(pool.map(render_clip, project.scenes))

    # 2. concat video + concat narration audio
    log("🎬 Syncing the voice with the visuals…")
    silent_video = str(tmp / "video_silent.mp4")
    _concat_clips(clips, silent_video)

    narration = str(tmp / "narration.wav")
    concat_audio([s.audio_path for s in project.scenes], narration)

    # 3. build the ffmpeg command: video + narration (+ music) (+ subtitles)
    final = str(project.dir / "final.mp4")
    music = _pick_music(cfg)

    inputs = ["-i", silent_video, "-i", narration]
    if music:
        inputs += ["-i", music]
        log("🎵 Adding background music")

    filtergraph_parts = []
    if music:
        # duck music under narration via volume, then mix
        filtergraph_parts.append(
            f"[2:a]volume={v.music_volume}[mus];"
            f"[1:a][mus]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = "1:a"

    # subtitles burned into the video stream
    srt = project.dir / "captions.srt"
    if cfg.caption.enabled and srt.exists() and project.captions:
        # Vertical videos want bigger captions sitting higher off the bottom.
        is_vertical = height > width
        font_size = 28 if is_vertical else max(16, cfg.caption.font_size // 2)
        margin_v = int(height * 0.18) if is_vertical else 60
        # Use a font that can render the chosen language's script (Devanagari,
        # Telugu, Tamil, Kannada). Installed via Noto fonts.
        font_name = get_language(project.language).font
        style = (
            f"FontName={font_name},FontSize={font_size},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV={margin_v}"
        )
        sub_filter = f"subtitles='{srt.as_posix()}':force_style='{style}'"
        filtergraph_parts.append(f"[0:v]{sub_filter}[vout]")
        video_map = "[vout]"
    else:
        video_map = "0:v"

    cmd = ["ffmpeg", "-y", *inputs]
    if filtergraph_parts:
        cmd += ["-filter_complex", ";".join(filtergraph_parts)]
    cmd += [
        "-map", video_map, "-map", audio_map,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-r", str(v.fps),
        final,
    ]

    log("🎬 Finalizing the video (almost done)…")
    run(cmd)

    project.final_video_path = final
    project.save()
    log("✅ Video rendered", "bold green")
    return project
