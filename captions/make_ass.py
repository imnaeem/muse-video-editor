#!/usr/bin/env python3
"""Turn word timings into Reels-style karaoke captions (ASS subtitles).

Style: 2-4 words on screen at once, ALL CAPS, bold, white with the
currently-spoken word highlighted in a pop color, centered on screen --
the look every Reels/TikTok talking-head video uses.

Each word gets its own event showing the whole phrase chunk with that word
highlighted, timed to the word's spoken duration. Works with any ASS
renderer (ffmpeg libass included).

Usage: python3 make_ass.py words.json -o captions.ass --video-height 720
"""
import argparse
import json
import re

COLORS = {
    "yellow": "&H0000FFFF&",
    "green": "&H0000FF00&",
    "cyan": "&H00FFFF00&",
    "pink": "&H00FF66FF&",
    "orange": "&H000099FF&",
    "white": "&H00FFFFFF&",
}

ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: {playresx}
PlayResY: {playresy}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Reels,{font},{size},{white},{white},&H00000000&,&H99000000&,-1,0,0,0,100,100,0.5,0,1,{outline},0,{align},20,20,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ts(t):
    t = max(0, t)
    h, rem = divmod(t, 3600)
    m, rem = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{int(rem):05.2f}"


def clean(w):
    return re.sub(r"\s+", " ", w).strip()


def chunk_words(words, max_words=4, max_gap=0.45):
    """Group words into natural phrase chunks."""
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        gap = (words[i + 1]["start"] - w["end"]) if i + 1 < len(words) else 99
        punct = w["word"][-1:] in ",.!?;:"
        if len(cur) >= max_words or punct or gap > max_gap:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


def make_ass(words_json, out, video_height=720, video_width=1280, max_words=4,
             highlight="yellow", uppercase=True, position="middle",
             font="DejaVu Sans"):
    with open(words_json, encoding="utf-8") as f:
        data = json.load(f)
    words = [w for w in data["words"] if clean(w["word"])]
    if not words:
        raise ValueError("no words to caption")

    size = max(28, int(video_height * 0.075))
    outline = max(2, int(video_height * 0.004))
    align = {"middle": 5, "bottom": 2, "top": 8}[position]
    marginv = int(video_height * 0.08) if position == "bottom" else 0
    hl = COLORS.get(highlight, highlight)

    def fmt(w):
        t = clean(w["word"])
        return t.upper() if uppercase else t

    def fit_size(line, size):
        # shrink text if the longest line would overflow the frame width
        plain = re.sub(r"\{[^}]*\}", "", line)
        longest = max(len(seg) for seg in plain.split(r"\N"))
        est = longest * size * 0.60  # rough width for bold all-caps
        limit = video_width * 0.94
        if est > limit:
            return max(20, int(size * limit / est))
        return size

    events = []
    for chunk in chunk_words(words, max_words):
        texts = [fmt(w) for w in chunk]
        chunk_end = chunk[-1]["end"]
        for i, w in enumerate(chunk):
            parts = []
            for j, t in enumerate(texts):
                if j == i:
                    parts.append(r"{\c" + hl + r"\}" + t + r"{\c&H00FFFFFF&\}")
                else:
                    parts.append(t)
            line = " ".join(parts)
            # wrap long chunks onto two lines at the middle
            if len(chunk) > 2:
                mid = (len(texts) + 1) // 2
                line = " ".join(parts[:mid]) + r"\N" + " ".join(parts[mid:])
            fs = fit_size(line, size)
            if fs != size:
                line = r"{\fs" + str(fs) + r"}" + line
            start = w["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk_end + 0.25
            end = min(end, chunk_end + 0.4)
            end = max(end, start + 0.08)  # libass skips zero-duration events
            events.append((start, end, line))

    head = ASS_HEAD.format(font=font, size=size, white=COLORS["white"],
                           outline=outline, align=align, marginv=marginv,
                           playresx=video_width, playresy=video_height)
    with open(out, "w", encoding="utf-8") as f:
        f.write(head)
        for s, e, line in sorted(events, key=lambda x: x[0]):
            f.write(f"Dialogue: 0,{ts(s)},{ts(e)},Reels,,0,0,0,,{line}\n")
    print(f"{len(events)} caption events -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("words_json")
    ap.add_argument("-o", "--out", default="captions.ass")
    ap.add_argument("--video-height", type=int, default=720)
    ap.add_argument("--video-width", type=int, default=1280)
    ap.add_argument("--max-words", type=int, default=4)
    ap.add_argument("--highlight", default="yellow",
                    choices=list(COLORS) + ["custom"])
    ap.add_argument("--highlight-hex", default="",
                    help="raw ASS color like &H0000FFFF& when --highlight custom")
    ap.add_argument("--no-uppercase", action="store_true")
    ap.add_argument("--position", default="middle",
                    choices=["middle", "bottom", "top"])
    a = ap.parse_args()
    hl = a.highlight_hex if a.highlight == "custom" else a.highlight
    make_ass(a.words_json, a.out, a.video_height, a.video_width, a.max_words, hl,
             uppercase=not a.no_uppercase, position=a.position)
