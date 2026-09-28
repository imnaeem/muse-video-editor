#!/usr/bin/env python3
"""Describe images with a tiny local vision model (SmolVLM-500M, CPU).

Usage: python3 describe.py frame1.png frame2.png
Model dir: vision/models/SmolVLM-500M-Instruct (downloaded via curl, see README).
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(HERE, "models", "SmolVLM-500M-Instruct")
PROMPT = ("Describe what is happening in this video frame in one short "
          "sentence. Mention people, actions, objects, and any visible text.")

_model = _processor = None


def _load():
    global _model, _processor
    if _model is None:
        import torch
        from transformers import AutoProcessor, AutoModelForImageTextToText
        _processor = AutoProcessor.from_pretrained(MODEL_DIR, trust_remote_code=True)
        _model = AutoModelForImageTextToText.from_pretrained(
            MODEL_DIR, dtype=torch.float32, trust_remote_code=True)
        _model.eval()
    return _model, _processor


def describe_frames(paths, verbose=True):
    model, processor = _load()
    from PIL import Image
    out = []
    for p in paths:
        img = Image.open(p).convert("RGB")
        # downscale: speed matters more than pixels for a 500M model
        img.thumbnail((768, 768))
        messages = [{"role": "user", "content": [
            {"type": "image"}, {"type": "text", "text": PROMPT}]}]
        prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = processor(text=prompt, images=[img], return_tensors="pt")
        import torch
        with torch.no_grad():
            gen = model.generate(**inputs, max_new_tokens=60, do_sample=False)
        text = processor.batch_decode(
            gen[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]
        out.append(text.strip())
        if verbose:
            print(f"  {os.path.basename(p)}: {text.strip()[:90]}", flush=True)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("frames", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    descs = describe_frames(a.frames, verbose=not a.json)
    if a.json:
        print(json.dumps(descs))
    else:
        for d in descs:
            print(d)
