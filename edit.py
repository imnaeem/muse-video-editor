#!/usr/bin/env python3
"""
Muse Video Edit Engine
======================
A script-driven, non-linear video editor built on ffmpeg.

The user sends a video + editing instructions. I translate them into an
`edit.json` recipe (the "project file"), and this script applies it step by
step. The recipe is kept in the project folder, so any project can be
re-rendered, tweaked, or extended later -- the project setup persists.

Usage:
    python3 edit.py projects/<name>            # full render -> output/final.mp4
    python3 edit.py projects/<name> --preview  # fast low-res preview -> output/preview.mp4

edit.json format:
{
  "source": "input/clip.mp4",          # starting footage (relative to project dir)
  "output": "output/final.mp4",       # final render target (relative)
  "steps": [
    {"op": "trim", "start": 5, "end": 60},
    {"op": "cut", "segments": [[0, 30], [45, 90]]},
    {"op": "concat", "files": ["input/a.mp4", "input/b.mp4"],
     "transition": "xfade", "transition_duration": 1.0},
    {"op": "text", "text": "My Title", "start": 2, "end": 8,
     "fontsize": 64, "color": "white", "box": true},
    {"op": "subtitles", "file": "input/caps.srt"},
    {"op": "fade", "in": 1.0, "out": 1.0},
    {"op": "speed", "factor": 1.25},
    {"op": "volume", "db": 3},
    {"op": "bgm", "file": "input/music.mp3", "volume": 0.15, "duck": true},
    {"op": "resize", "width": 1280, "height": 720},
    {"op": "crop", "w": 1080, "h": 1920},
    {"op": "mute"},
    {"op": "captions", "model": "small", "max_words": 4,
     "highlight": "yellow", "uppercase": true, "position": "middle"},
    {"op": "overlay", "file": "input/cutout.png", "start": 0, "end": 5,
     "position": "bottom-right", "scale": 0.35, "fade": 0.5},
    {"op": "extract_audio", "format": "mp3"},
    {"op": "thumbnail", "at": 3.0}
  ]
}
Steps run in order; each step's output feeds the next. `concat` replaces the
current timeline with the joined files.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Intermediate quality: fast to encode, visually near-lossless.
V_OPTS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
          "-pix_fmt", "yuv420p"]
A_OPTS = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]


def run(cmd, desc):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{desc} failed:\n{p.stderr[-3000:]}")
    return p


def probe(path):
    p = run([FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path], f"probe {path}")
    d = json.loads(p.stdout)
    info = {"duration": float(d["format"].get("duration", 0)),
            "has_audio": False, "width": 0, "height": 0, "fps": 30.0}
    for s in d.get("streams", []):
        if s["codec_type"] == "video" and not info["width"]:
            info["width"] = s.get("width", 0)
            info["height"] = s.get("height", 0)
            fps = s.get("avg_frame_rate", "30/1")
            try:
                n, m = fps.split("/")
                info["fps"] = float(n) / float(m) if float(m) else 30.0
            except Exception:
                pass
        if s["codec_type"] == "audio":
            info["has_audio"] = True
    return info


def enc_args(final=False):
    v = V_OPTS if not final else ["-c:v", "libx264", "-preset", "veryfast",
                                  "-crf", "20", "-pix_fmt", "yuv420p"]
    return v + A_OPTS


def step_trim(inp, out, start, end):
    run([FFMPEG, "-y", "-v", "error", "-ss", str(start), "-to", str(end),
         "-i", inp] + enc_args() + [out], "trim")


def step_cut(inp, out, segments, tmpdir):
    parts = []
    for i, (s, e) in enumerate(segments):
        p = os.path.join(tmpdir, f"seg{i}.mp4")
        step_trim(inp, p, s, e)
        parts.append(p)
    lst = os.path.join(tmpdir, "list.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    # same-encoding segments -> stream copy is safe and fast
    try:
        run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", lst, "-c", "copy", out], "cut concat")
    except RuntimeError:
        run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", lst] + enc_args() + [out], "cut concat (re-encode)")


def _norm(idx, w, h, fps):
    return (f"[{idx}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
            f"fps={fps},format=yuv420p[v{idx}];")


def step_concat(out, files, tmpdir, transition=None, t_dur=1.0):
    infos = [probe(f) for f in files]
    w, h = infos[0]["width"], infos[0]["height"]
    fps = round(infos[0]["fps"]) or 30
    cmd = [FFMPEG, "-y", "-v", "error"]
    for f in files:
        cmd += ["-i", f]
    n = len(files)
    if not transition or n < 2:
        filt = "".join(_norm(i, w, h, fps) for i in range(n))
        filt += "".join(f"[v{i}]" for i in range(n))
        filt += f"concat=n={n}:v=1:a=0[vcat];"
        af = "".join(f"[{i}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}];"
                     for i in range(n) if infos[i]["has_audio"])
        na = sum(1 for x in infos if x["has_audio"])
        if na:
            af += "".join(f"[a{i}]" for i in range(n) if infos[i]["has_audio"])
            af += f"concat=n={na}:v=0:a=1[acat]"
            filt += af
            maps = ["-map", "[vcat]"]
            if na:
                maps += ["-map", "[acat]"]
        else:
            maps = ["-map", "[vcat]"]
        run(cmd + ["-filter_complex", filt] + maps + enc_args() + [out], "concat")
        return
    # xfade video + acrossfade audio chain
    filt = "".join(_norm(i, w, h, fps) for i in range(n))
    filt += "".join(f"[{i}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}];"
                    for i in range(n) if infos[i]["has_audio"])
    offset = infos[0]["duration"] - t_dur
    prev = "v0"
    for i in range(1, n):
        nxt = f"x{i}"
        filt += (f"[{prev}][v{i}]xfade=transition=fade:duration={t_dur}:"
                 f"offset={offset:.3f}[{nxt}];")
        offset += infos[i]["duration"] - t_dur
        prev = nxt
    aidx = [i for i in range(n) if infos[i]["has_audio"]]
    maps = ["-map", f"[{prev}]"]
    if len(aidx) >= 2:
        prev = f"a{aidx[0]}"
        for i in aidx[1:]:
            nxt = f"ax{i}"
            filt += (f"[{prev}][a{i}]acrossfade=d={t_dur}[{nxt}];")
            prev = nxt
        maps += ["-map", f"[{prev}]"]
    elif len(aidx) == 1:
        maps += ["-map", f"[a{aidx[0]}]"]
    run(cmd + ["-filter_complex", filt.rstrip(";")] + maps + enc_args() + [out],
        "concat xfade")


def step_text(inp, out, text, start, end, fontsize=48, color="white",
              x="(w-text_w)/2", y="h-text_h-60", box=True, tmpdir=""):
    tf = os.path.join(tmpdir, "caption.txt")
    with open(tf, "w", encoding="utf-8") as f:
        f.write(text)
    boxopt = ":box=1:boxcolor=black@0.55:boxborderw=14" if box else ""
    dt = (f"drawtext=fontfile={FONT}:textfile='{tf}':fontsize={fontsize}:"
          f"fontcolor={color}:x={x}:y={y}{boxopt}:"
          f"enable='between(t,{start},{end})'")
    run([FFMPEG, "-y", "-v", "error", "-i", inp, "-vf", dt,
         "-c:a", "copy", out], "text overlay")


def step_subtitles(inp, out, srt):
    run([FFMPEG, "-y", "-v", "error", "-i", inp,
         "-vf", f"subtitles='{srt}'", "-c:a", "copy", out], "subtitles")


def step_fade(inp, out, fade_in=0.0, fade_out=0.0):
    d = probe(inp)["duration"]
    vf, af = [], []
    if fade_in:
        vf.append(f"fade=t=in:st=0:d={fade_in}")
        af.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out:
        vf.append(f"fade=t=out:st={d - fade_out:.3f}:d={fade_out}")
        af.append(f"afade=t=out:st={d - fade_out:.3f}:d={fade_out}")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", inp]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if af and probe(inp)["has_audio"]:
        cmd += ["-af", ",".join(af)]
    run(cmd + enc_args() + [out], "fade")


def step_speed(inp, out, factor):
    atempo, f = [], factor
    while f > 2.0:
        atempo.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        atempo.append("atempo=0.5")
        f /= 0.5
    atempo.append(f"atempo={f:.4f}")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", inp,
           "-vf", f"setpts=PTS/{factor:.4f}"]
    if probe(inp)["has_audio"]:
        cmd += ["-af", ",".join(atempo)]
    else:
        cmd += ["-an"]
    run(cmd + enc_args() + [out], "speed")


def step_volume(inp, out, db=0.0, factor=None):
    expr = f"{factor:.4f}" if factor else f"{db}dB"
    run([FFMPEG, "-y", "-v", "error", "-i", inp, "-c:v", "copy",
         "-af", f"volume={expr}"] + A_OPTS + [out], "volume")


def step_bgm(inp, out, music, volume=0.15, duck=True):
    info = probe(inp)
    d = info["duration"]
    if info["has_audio"] and duck:
        filt = (f"[1:a]volume={volume},aformat=channel_layouts=stereo[bg];"
                f"[0:a][bg]sidechaincompress=threshold=0.02:ratio=8:"
                f"attack=20:release=400:makeup=1[ducked];"
                f"[ducked]aformat=channel_layouts=stereo[aout]")
        amap = ["-map", "0:v", "-map", "[aout]"]
    elif info["has_audio"]:
        filt = (f"[1:a]volume={volume},aformat=channel_layouts=stereo[bg];"
                f"[0:a][bg]amix=inputs=2:duration=first:"
                f"dropout_transition=0[aout]")
        amap = ["-map", "0:v", "-map", "[aout]"]
    else:
        filt = f"[1:a]volume={volume},atrim=0:{d:.3f}[aout]"
        amap = ["-map", "0:v", "-map", "[aout]"]
    run([FFMPEG, "-y", "-v", "error", "-i", inp,
         "-stream_loop", "-1", "-i", music,
         "-filter_complex", filt] + amap + enc_args() + [out], "bgm")


def step_resize(inp, out, width, height):
    run([FFMPEG, "-y", "-v", "error", "-i", inp, "-vf",
         f"scale={width}:{height}", "-c:a", "copy", out], "resize")


def step_crop(inp, out, w, h, x="(in_w-w)/2", y="(in_h-h)/2"):
    run([FFMPEG, "-y", "-v", "error", "-i", inp, "-vf",
         f"crop={w}:{h}:{x}:{y}", "-c:a", "copy", out], "crop")


def step_mute(inp, out):
    run([FFMPEG, "-y", "-v", "error", "-i", inp, "-c:v", "copy",
         "-an", out], "mute")


def step_overlay(inp, out, img, start=0, end=5, position="bottom-right",
                 scale=0.35, margin=40, fade=0.5):
    """Overlay a PNG (with alpha, e.g. bg-removed cutout) for a time window.

    position: center/top/bottom/left/right/top-left/top-right/
              bottom-left/bottom-right. scale: overlay width as a fraction
    of video width. fade: fade in/out seconds on the overlay.
    """
    v = probe(inp)
    W, H = v["width"], v["height"]
    im = probe(img)
    ow = max(1, int(W * scale))
    oh = max(1, int(ow * im["height"] / max(im["width"], 1)))
    m = margin
    pos = {
        "center": ((W - ow) / 2, (H - oh) / 2),
        "top": ((W - ow) / 2, m),
        "bottom": ((W - ow) / 2, H - oh - m),
        "left": (m, (H - oh) / 2),
        "right": (W - ow - m, (H - oh) / 2),
        "top-left": (m, m),
        "top-right": (W - ow - m, m),
        "bottom-left": (m, H - oh - m),
        "bottom-right": (W - ow - m, H - oh - m),
    }
    x, y = pos.get(position, pos["bottom-right"])
    dur = max(end - start, 0.1)
    ovf = f"[1:v]format=rgba,scale={ow}:{oh}"
    if fade and fade * 2 < dur:
        ovf += (f",fade=t=in:st=0:d={fade}:alpha=1,"
                f"fade=t=out:st={dur - fade:.3f}:d={fade}:alpha=1")
    filt = (ovf + f"[ov];[0:v][ov]overlay=x={x:.0f}:y={y:.0f}:"
            f"enable='between(t,{start},{end})'[v]")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", inp,
           "-loop", "1", "-framerate", "30", "-i", img,
           "-filter_complex", filt, "-map", "[v]"]
    if v["has_audio"]:
        cmd += ["-map", "0:a"]
    # image loop must not outlive the main video
    cmd += ["-shortest"]
    run(cmd + enc_args() + [out], "overlay image")


CAPTIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "captions")
CAPTIONS_VENV_PY = os.path.join(CAPTIONS_DIR, "venv", "bin", "python")


def step_captions(inp, out, projdir, model="small", max_words=4,
                  highlight="yellow", uppercase=True, position="middle"):
    """Transcribe speech (local whisper) and burn Reels-style karaoke captions."""
    info = probe(inp)
    if not info["has_audio"]:
        raise RuntimeError("captions op needs audio in the clip")
    outdir = os.path.join(projdir, "output")
    os.makedirs(outdir, exist_ok=True)
    wav = os.path.join(outdir, "_cap_audio.wav")
    words_json = os.path.join(outdir, "words.json")
    ass = os.path.join(outdir, "captions.ass")
    run([FFMPEG, "-y", "-v", "error", "-i", inp, "-vn",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav],
        "captions: extract audio")
    run([CAPTIONS_VENV_PY, os.path.join(CAPTIONS_DIR, "transcribe.py"),
         wav, "--model", model, "-o", words_json], "captions: transcribe")
    cmd = [sys.executable, os.path.join(CAPTIONS_DIR, "make_ass.py"),
           words_json, "-o", ass,
           "--video-height", str(info["height"] or 720),
           "--video-width", str(info["width"] or 1280),
           "--max-words", str(max_words), "--highlight", highlight,
           "--position", position]
    if not uppercase:
        cmd.append("--no-uppercase")
    run(cmd, "captions: style")
    run([FFMPEG, "-y", "-v", "error", "-i", inp, "-vf",
         f"ass='{ass}'", "-c:a", "copy", out], "captions: burn-in")
    os.remove(wav)


def run_project(projdir, preview=False):
    projdir = os.path.abspath(projdir)
    with open(os.path.join(projdir, "edit.json"), encoding="utf-8") as f:
        spec = json.load(f)

    def p(rel):
        return rel if os.path.isabs(rel) else os.path.join(projdir, rel)

    cur = p(spec["source"])
    if not os.path.exists(cur):
        raise FileNotFoundError(f"source not found: {cur}")
    steps = spec.get("steps", [])
    tmpdir = tempfile.mkdtemp(prefix="mvedit_")
    os.makedirs(os.path.join(projdir, "output"), exist_ok=True)

    try:
        for i, s in enumerate(steps):
            op = s["op"]
            nxt = os.path.join(tmpdir, f"s{i:02d}_{op}.mp4")
            print(f"[step {i+1}/{len(steps)}] {op}", flush=True)
            if op == "trim":
                step_trim(cur, nxt, s.get("start", 0), s.get("end"))
            elif op == "cut":
                step_cut(cur, nxt, s["segments"], tmpdir)
            elif op == "concat":
                step_concat(nxt, [p(x) for x in s["files"]], tmpdir,
                            s.get("transition"), s.get("transition_duration", 1.0))
            elif op == "text":
                step_text(cur, nxt, s["text"], s.get("start", 0),
                          s.get("end", 9999), s.get("fontsize", 48),
                          s.get("color", "white"), s.get("x", "(w-text_w)/2"),
                          s.get("y", "h-text_h-60"), s.get("box", True), tmpdir)
            elif op == "subtitles":
                step_subtitles(cur, nxt, p(s["file"]))
            elif op == "fade":
                step_fade(cur, nxt, s.get("in", 0), s.get("out", 0))
            elif op == "speed":
                step_speed(cur, nxt, s.get("factor", 1.0))
            elif op == "volume":
                step_volume(cur, nxt, s.get("db", 0.0), s.get("factor"))
            elif op == "bgm":
                step_bgm(cur, nxt, p(s["file"]), s.get("volume", 0.15),
                         s.get("duck", True))
            elif op == "resize":
                step_resize(cur, nxt, s["width"], s["height"])
            elif op == "crop":
                step_crop(cur, nxt, s["w"], s["h"], s.get("x", "(in_w-w)/2"),
                          s.get("y", "(in_h-h)/2"))
            elif op == "mute":
                step_mute(cur, nxt)
            elif op == "overlay":
                step_overlay(cur, nxt, p(s["file"]), s.get("start", 0),
                             s.get("end", 5), s.get("position", "bottom-right"),
                             s.get("scale", 0.35), s.get("margin", 40),
                             s.get("fade", 0.5))
            elif op == "captions":
                step_captions(cur, nxt, projdir, s.get("model", "small"),
                              s.get("max_words", 4), s.get("highlight", "yellow"),
                              s.get("uppercase", True), s.get("position", "middle"))
            else:
                raise ValueError(f"unknown op: {op}")
            cur = nxt

        final = p(spec.get("output", "output/final.mp4"))
        if preview:
            final = os.path.join(projdir, "output", "preview.mp4")
            run([FFMPEG, "-y", "-v", "error", "-i", cur, "-vf",
                 "scale=640:-2"] + ["-c:v", "libx264", "-preset", "ultrafast",
                 "-crf", "28"] + A_OPTS + [final], "preview")
        else:
            run([FFMPEG, "-y", "-v", "error", "-i", cur] + enc_args(final=True) +
                ["-movflags", "+faststart", final], "final encode")
        info = probe(final)
        print(f"DONE -> {final}  ({info['duration']:.1f}s, "
              f"{info['width']}x{info['height']}, audio={info['has_audio']})")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    run_project(sys.argv[1], preview="--preview" in sys.argv[2:])
