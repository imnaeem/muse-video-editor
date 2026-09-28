#!/usr/bin/env python3
"""Video analyzer: timestamped understanding of what's happening in a video.

Pipeline (all local, free, open-source except the optional reasoning step):
  1. Scene detection (ffmpeg) -> scene boundaries + keyframe per scene
  2. Speech transcription (faster-whisper, local) -> word-level timestamps
  3. Visual description per keyframe (SmolVLM, local) -> "what's happening"
  4. Merge -> analysis.json timeline
  5. Reasoning (user's AgentRouter API key) -> edit suggestions:
     dead air, highlights, chapter titles, reel-worthy moments

Usage:
    captions/venv/bin/python analyze.py projects/<name>/input/video.mp4 [--out DIR] [--skip-vision] [--reason]

Output: <out>/analysis.json, <out>/frames/*.png, <out>/words.json
(requires the captions venv for whisper; vision auto-uses vision/venv)
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"failed: {' '.join(cmd)}\n{p.stderr[-2000:]}")
    return p


def probe_duration(path):
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path])
    return float(p.stdout.strip())


def detect_scenes(video, threshold=0.4):
    """Return sorted scene-change timestamps (seconds)."""
    p = run(["ffmpeg", "-i", video, "-filter:v",
             f"select='gt(scene,{threshold})',showinfo",
             "-f", "null", "-"])
    times = sorted({round(float(m.group(1)), 2)
                    for m in re.finditer(r"pts_time:([\d.]+)", p.stderr)})
    return times


def extract_keyframes(video, bounds, frames_dir):
    os.makedirs(frames_dir, exist_ok=True)
    paths = []
    for i, t in enumerate(bounds):
        out = os.path.join(frames_dir, f"s{i:03d}.png")
        # grab frame slightly after the cut to avoid transition frames
        run(["ffmpeg", "-y", "-v", "error", "-ss", str(t + 0.25),
             "-i", video, "-frames:v", "1", out])
        paths.append(out)
    return paths


REASON_SYSTEM = """You are a video editing assistant. You get a timestamped scene analysis of a video: per scene, what is visible and what is said.
Reply with STRICT JSON only (no markdown, no commentary) in this shape:
{
  "summary": "one-paragraph description of the video",
  "dead_air": [{"start": 0.0, "end": 0.0, "reason": "..."}],
  "highlights": [{"start": 0.0, "end": 0.0, "title": "...", "why": "..."}],
  "chapters": [{"start": 0.0, "title": "..."}],
  "suggested_cuts": [{"keep": [0.0, 0.0], "label": "..."}]
}
Rules: dead_air = silence, filler, repetition, nothing happening. highlights = moments worth turning into short reels (max 5). chapters = natural topic breaks. suggested_cuts = the segments to KEEP for a tight edit, in order."""


def run_reason(analysis_path, out_dir, models=None):
    """Ask the AgentRouter LLMs (auto-fallback) for edit suggestions."""
    import re
    with open(analysis_path, encoding="utf-8") as f:
        analysis = json.load(f)
    slim = [{"start": s["start"], "end": s["end"], "visual": s["visual"],
             "transcript": s["transcript"][:400]} for s in analysis["scenes"]]
    prompt = ("Analyze this video and reply with STRICT JSON only.\n\n"
              + json.dumps(slim, ensure_ascii=False)[:12000])
    cmd = [os.path.expanduser("~/workspace/skills/agentrouter/bin/chat"),
           "--system", REASON_SYSTEM, "--max-tokens", "4096",
           "--temperature", "0.3"]
    if models:
        cmd += ["--models", models]
    cmd.append(prompt)
    print("asking AgentRouter for edit suggestions...", flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"reasoning failed:\n{p.stderr[-2000:]}")
    m = re.search(r"\{.*\}", p.stdout, re.S)
    suggestions = json.loads(m.group(0)) if m else {"raw": p.stdout}
    spath = os.path.join(out_dir, "suggestions.json")
    with open(spath, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=1)
    print(f"wrote {spath}", flush=True)
    return spath


def build_timeline(video, out_dir, skip_vision=False):
    os.makedirs(out_dir, exist_ok=True)
    frames_dir = os.path.join(out_dir, "frames")
    duration = probe_duration(video)
    print(f"duration: {duration:.1f}s", flush=True)

    cuts = detect_scenes(video)
    print(f"scene cuts: {len(cuts)}", flush=True)
    bounds = [0.0] + [c for c in cuts if c > 1.0]
    ends = bounds[1:] + [duration]
    print(f"extracting {len(bounds)} keyframes...", flush=True)
    kf_paths = extract_keyframes(video, bounds, frames_dir)

    print("transcribing audio...", flush=True)
    sys.path.insert(0, os.path.join(HERE, "captions"))
    from transcribe import transcribe
    wav = os.path.join(out_dir, "_a.wav")
    run(["ffmpeg", "-y", "-v", "error", "-i", video, "-vn",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav])
    tr = transcribe(wav, "small")
    os.remove(wav)
    words = tr["words"]
    with open(os.path.join(out_dir, "words.json"), "w") as f:
        json.dump(tr, f, ensure_ascii=False, indent=1)

    visuals = [None] * len(bounds)
    if not skip_vision:
        print("describing scenes (local vision model)...", flush=True)
        vpy = os.path.join(HERE, "vision", "venv", "bin", "python")
        vscript = os.path.join(HERE, "vision", "describe.py")
        p = subprocess.run(
            [vpy, vscript, "--json"] + kf_paths,
            capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"vision failed:\n{p.stderr[-2000:]}")
        # last line is the JSON (model logs go to stderr)
        visuals = json.loads(p.stdout.strip().splitlines()[-1])

    scenes = []
    for i, (s, e) in enumerate(zip(bounds, ends)):
        seg_words = [w["word"] for w in words if s <= w["start"] < e]
        scenes.append({
            "start": round(s, 2), "end": round(e, 2),
            "keyframe": os.path.basename(kf_paths[i]),
            "visual": visuals[i],
            "transcript": " ".join(seg_words).strip(),
        })
    analysis = {"video": os.path.basename(video), "duration": round(duration, 2),
                "scenes": scenes}
    apath = os.path.join(out_dir, "analysis.json")
    with open(apath, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=1)
    print(f"wrote {apath} ({len(scenes)} scenes)", flush=True)
    return apath


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default=None)
    ap.add_argument("--skip-vision", action="store_true")
    ap.add_argument("--reason", action="store_true",
                    help="ask AgentRouter LLMs for edit suggestions")
    ap.add_argument("--models", default=None,
                    help="comma-separated model fallback order")
    a = ap.parse_args()
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.video)),
                                "..", "analysis")
    out = os.path.abspath(out)
    apath = build_timeline(os.path.abspath(a.video), out,
                           skip_vision=a.skip_vision)
    if a.reason:
        run_reason(apath, out, a.models)
