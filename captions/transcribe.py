#!/usr/bin/env python3
"""Transcribe audio with word-level timestamps using faster-whisper (local, free).

Usage: python3 transcribe.py input_audio.wav --model small -o words.json
Models: tiny/base/small/medium (bigger = better, slower). Runs on CPU.
Output: {"words": [{"word": str, "start": s, "end": s, "prob": f}], "text": str, "language": str}
"""
import argparse
import json
import os

from faster_whisper import WhisperModel

HERE = os.path.dirname(os.path.abspath(__file__))


def resolve_model(name):
    """Prefer a locally cached model dir (HF hub downloads break behind
    this VM's proxy); fall back to the hub name otherwise."""
    local = os.path.join(HERE, "models", name)
    if os.path.isdir(local) and os.path.exists(os.path.join(local, "model.bin")):
        return local
    return name


def transcribe(audio_path, model_name="small"):
    model = WhisperModel(resolve_model(model_name), device="cpu",
                         compute_type="int8")
    segments, info = model.transcribe(audio_path, word_timestamps=True,
                                      vad_filter=True)
    words = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                words.append({"word": w.word.strip(), "start": round(w.start, 2),
                              "end": round(w.end, 2),
                              "prob": round(w.probability, 3)})
    return {"words": words,
            "text": " ".join(w["word"] for w in words),
            "language": info.language}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--model", default="small")
    ap.add_argument("-o", "--out", default="words.json")
    a = ap.parse_args()
    res = transcribe(a.audio, a.model)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"{len(res['words'])} words, lang={res['language']} -> {a.out}")
