# Video Editing Setup

Script-driven video editing on this machine. The user sends a video file +
editing instructions; I translate them into an `edit.json` recipe and render
with `edit.py` (ffmpeg under the hood).

## Setup

Prerequisites: `ffmpeg` on PATH.

```bash
# speech transcription env (~450MB)
python3 -m venv captions/venv
captions/venv/bin/pip install -r captions/requirements.txt

# vision env, CPU-only torch (~1.2GB)
python3 -m venv vision/venv
vision/venv/bin/pip install -r vision/requirements.txt
```

Models download automatically on first run and are cached locally
(gitignored): faster-whisper `small` (~500MB) into `captions/models/`,
SmolVLM-500M-Instruct (~1GB) into `vision/models/`. On restricted networks
they can be pre-fetched with plain `curl` from their Hugging Face repos
(`Systran/faster-whisper-small`, `HuggingFaceTB/SmolVLM-500M-Instruct`).

Copy `projects/_template/` to `projects/<name>/`, drop source footage in
`input/`, write `edit.json`, render.

## Why script-driven instead of a GUI editor

R&D 2026-09-28: top open-source editors by GitHub stars are Blender (~20k),
Shotcut (~15k), OpenShot (~6.5k), Kdenlive (~5.7k). All are GUI apps designed
for a human clicking a timeline. On a headless VM the useful, AI-drivable
layer is a scripted pipeline: it does the same timeline operations (cut, join,
transitions, overlays, audio mix) deterministically and repeatably, and the
`edit.json` recipe IS the project file — kept forever, re-renderable,
tweakable. (Shotcut/Kdenlive sit on MLT; rendering their `.mlt` XML with the
`melt` CLI remains an option if GUI-compat project files are ever wanted.)

## Project layout

    projects/<name>/
      input/        # source footage, music, .srt subtitles the user provided
      edit.json     # the edit recipe (the "project file") - never delete
      notes.md      # what was asked, what was decided, revision history
      output/       # final.mp4, preview.mp4, thumbnails

## Workflow for each new video

1. User sends video (+ instructions like "cut the boring intro, add title
   'My Talk', background music low").
2. Save upload to `projects/<name>/input/`.
3. Write `edit.json` from the instructions. If instructions are vague, ask.
4. Render a fast preview first: `python3 edit.py projects/<name> --preview`
   (640px, quick). Share preview with user for approval on big edits.
5. Full render: `python3 edit.py projects/<name>` -> `output/final.mp4`.
6. Log decisions in `notes.md`.

## edit.json reference

See `edit.py` docstring for the full op list. Common patterns:

- Keep highlights: `{"op": "cut", "segments": [[12, 48], [75, 130]]}`
  (seconds; keeps 0:12-0:48 and 1:15-2:10, drops the rest)
- Join clips with crossfade:
  `{"op": "concat", "files": ["input/a.mp4", "input/b.mp4"], "transition": "xfade", "transition_duration": 1.0}`
- Title card: `{"op": "text", "text": "My Title", "start": 1, "end": 6, "fontsize": 56}`
- Burn subtitles: `{"op": "subtitles", "file": "input/caps.srt"}`
- Polish: `{"op": "fade", "in": 1.0, "out": 1.0}`
- Music bed: `{"op": "bgm", "file": "input/music.mp3", "volume": 0.15, "duck": true}`
  (duck lowers music while speech plays)
- Vertical crop for Reels/TikTok: `{"op": "crop", "w": 1080, "h": 1920}`
- Fancy karaoke captions (Reels style, transcribed locally):
  `{"op": "captions", "model": "small", "max_words": 4, "highlight": "yellow", "uppercase": true, "position": "middle"}`
  Transcribes speech with word timestamps (free local whisper, ~1x realtime)
  and burns bold ALL-CAPS captions with the spoken word highlighted.
  Also saves `output/words.json` + `output/captions.ass` for reuse.
- Speed: `{"op": "speed", "factor": 1.25}`

Steps run in order; each feeds the next. `concat` replaces the timeline.

## Video analyzer (`analyze.py`)

Understands what's in a video and suggests edits. Hybrid: everything heavy
runs locally and free; only the final reasoning step uses the user's
AgentRouter API key (any model, auto-fallback).

    python3 analyze.py projects/<name>/input/video.mp4 [--reason]

Pipeline:
1. Scene detection (ffmpeg) -> scene boundaries + one keyframe per scene.
2. Speech transcription (local faster-whisper, word timestamps) -> words
   assigned to their scene.
3. Visual description per keyframe (local SmolVLM-500M, CPU) -> one-sentence
   "what's happening".
4. Merged timeline -> `analysis/analysis.json` (+ `frames/`, `words.json`).
5. `--reason`: sends the timeline to the AgentRouter LLMs, gets strict JSON
   back -> `analysis/suggestions.json` with summary, dead_air (cut list),
   highlights (reel-worthy moments), chapters, suggested_cuts (keep-list).

Flags: `--skip-vision` (transcript-only analysis), `--models m1,m2` (custom
fallback order). The suggested_cuts keep-list can be turned straight into an
`edit.json` `{"op": "cut", "segments": [...]}` step.

Speed notes (2 CPUs): transcription ~1x realtime; vision ~60-90s per scene on
CPU (model loads once per run, so few scenes = fine, dozens = slow);
a 10-min talking-head video with a handful of scenes analyzes in roughly
10-15 min end to end.

## Practical notes

- 5-20 min videos are fine. A 20-min 1080p render takes a few minutes on
  this box (2 CPU); use `--preview` for quick approval cycles.
- Final encodes: h264, crf 20, aac 128k, faststart (web-friendly mp4).
- Intermediates are re-encoded per step (crf 18, veryfast) - quality loss
  across a handful of steps is negligible.
- Fonts available: DejaVu family (text overlays), Noto (unicode).
- Subtitles: burn an .srt with `{"op": "subtitles", ...}`, or auto-transcribe +
  burn Reels-style karaoke captions with `{"op": "captions", ...}` (no .srt
  needed; I can also draft an .srt from any transcript).
