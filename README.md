# 🎬 AI Video Studio

Turn a **story prompt** into a finished, **narrated YouTube video** — script, voice,
visuals, subtitles, music, and (optionally) auto-upload — fully automated.

Built **free-by-default**: it uses local/free tools (Piper voice, Stable Diffusion
on your GPU, ffmpeg) so you can produce videos at **$0 per video**. Every stage is
**pluggable**, so you can later swap in premium providers (ElevenLabs voice, talking
AI avatars, paid image models) without rewriting the pipeline.

```
prompt → [script] → [voice] → [images] → [subtitles] → [render] → [upload]
```

## How it works

| Stage | What it does | Free default | Paid upgrade |
|------|--------------|--------------|--------------|
| **script** | Expands your prompt into scene-by-scene narration + image prompts + YouTube title/desc/tags | offline template | **Claude** (`ANTHROPIC_API_KEY`) |
| **voice** | Narration audio per scene | **Piper** (offline, CPU) | ElevenLabs |
| **images** | One visual per scene | **Stable Diffusion** (GPU) → Pexels → gradient card | any image API |
| **subtitles** | Timed burned-in captions + `.srt` | scene-timed | **faster-whisper** word timing |
| **render** | Ken Burns zoom + voice + music + captions → MP4 | **ffmpeg** | — |
| **upload** | Publishes to YouTube (private by default) | YouTube Data API | — |

Each project is saved to `output/<id>/` with its `project.json`, audio, images, and
`final.mp4`. The pipeline is **resumable** — re-run any stage without redoing the rest.

## Quick start (Web UI — recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m ai_video_studio serve --port 8077
```

Then open the dashboard in your browser:

- On the server:        http://localhost:8077
- From your laptop:     http://<SERVER_IP>:8077   (open the port in your firewall/security group)

In the UI: type a story → click **Generate** → watch live progress → preview &
download the video. All past videos appear in the gallery below.

> Tip: put your `ANTHROPIC_API_KEY` in a `.env` file in the project root — the
> server loads it automatically (no manual `export` needed).

## Quick start (CLI)

```bash
# 1. Install (core is light; SD/whisper are optional — see below)
pip install -r requirements.txt

# 2. See what's detected on your machine
python -m ai_video_studio info

# 3. Make your first video (stops before upload)
python -m ai_video_studio create "The lighthouse keeper who found a city beneath the waves"

# → output/<timestamp>-the-lighthouse.../final.mp4
```

### Optional, for best quality / your GPU

```bash
# Stable Diffusion images on your NVIDIA GPU (free):
pip install torch diffusers transformers accelerate

# Best scripts (Claude):
export ANTHROPIC_API_KEY=sk-ant-...

# Accurate caption timing:
pip install faster-whisper

# Free stock photos instead of SD (no GPU):
export PEXELS_API_KEY=...
```

If none of the above are present, it still works — script falls back to a template,
images to gradient title cards, captions to per-scene timing. **Nothing blocks.**

## Commands

```bash
python -m ai_video_studio create "your story idea"          # one video
python -m ai_video_studio create-file topics/story.txt      # from a file
python -m ai_video_studio create "..." --to upload          # also upload
python -m ai_video_studio resume output/<proj> --from images  # resume a project
python -m ai_video_studio auto                              # process topic queue
python -m ai_video_studio info                              # show capabilities
```

## Fully automated channel (the "money machine")

1. Add story ideas to `topics/queue.txt` (one per line).
2. Enable upload + authorize YouTube (see below).
3. Add a cron job:

```cron
# one new video every day at 9am
0 9 * * * cd /path/to/project && python -m ai_video_studio auto >> logs/auto.log 2>&1
```

Each run renders the next queued topic, uploads it, and moves the line to `done.txt`.

## Enabling YouTube upload

1. In Google Cloud Console: create a project → enable **YouTube Data API v3**.
2. Create **OAuth client credentials** (type: *Desktop app*) → download as
   `client_secrets.json` in the project root.
3. Set `youtube.enabled: true` in `config.yaml`.
4. Run an upload once — a browser opens to authorize your channel; the token is cached.

> Uploads default to **private** (`config.yaml → youtube.privacy` via `VideoMeta`) so
> you review every video before making it public. Flip to `public` once you trust it.

## Add background music

Drop royalty-free `.mp3`/`.wav` files into `assets/music/`. A track is picked per
video and ducked under the narration. (Use copyright-safe music to keep monetization.)

## Project layout

```
ai_video_studio/
  config.py          # settings (config.yaml + env overrides)
  models.py          # Project/Scene/Caption data models (the source of truth)
  orchestrator.py    # runs the ordered, resumable pipeline
  cli.py             # `python -m ai_video_studio ...`
  scheduler.py       # topic-queue automation for cron
  pipeline/
    script_gen.py    # 1. prompt → scenes (Claude or template)
    voice.py         # 2. Piper / ElevenLabs TTS
    images.py        # 3. Stable Diffusion / Pexels / card
    captions.py      # 4. whisper / scene-timed subtitles
    video.py         # 5. ffmpeg Ken Burns + mix + burn-in
    upload.py        # 6. YouTube upload
config.yaml          # edit me to change providers/quality
topics/queue.txt     # automation: story ideas, one per line
assets/music/        # drop royalty-free tracks here
output/              # generated projects (gitignored)
```

## Roadmap (the pluggable upgrades you asked about)

- **Talking AI avatar** — add `pipeline/avatar.py` (SadTalker/Wav2Lip on your GPU for
  free, or D-ID/HeyGen API) that takes the voice clip + a portrait and produces a
  lip-synced face; the `video` stage already accepts per-scene clips.
- **Shorts mode** — a 9:16 vertical preset for YouTube Shorts / Reels / TikTok.
- **Web dashboard** — a small FastAPI UI over the same orchestrator.

## ⚠️ Notes on earning responsibly

- Use **royalty-free** music and ensure generated visuals don't infringe.
- YouTube monetization favors original, value-adding content — thin/spammy
  AI uploads risk demonetization. Curate your topic queue for quality.
