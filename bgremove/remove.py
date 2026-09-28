#!/usr/bin/env python3
"""Remove image background, keep the focus object. Transparent PNG out.

Model: rembg `isnet-general-use` (open-source MIT, best general-purpose
quality in the rembg model zoo). Downloads once (~170MB) into
bgremove/models/ on first run.

Usage:
    bgremove/venv/bin/python bgremove/remove.py input.jpg -o cutout.png
    bgremove/venv/bin/python bgremove/remove.py input.jpg -o person.png --model u2net_human_seg

The output PNG keeps its alpha channel, so it drops straight into video
with the edit.py {"op": "overlay", ...} step (start/end of a clip).
"""

import argparse
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_HOME = os.path.join(HERE, "models")
os.environ.setdefault("U2NET_HOME", MODEL_HOME)


def main():
    ap = argparse.ArgumentParser(
        description="Remove background, keep the focus object.")
    ap.add_argument("input", help="input image (jpg/png/webp...)")
    ap.add_argument("-o", "--output", required=True,
                    help="output transparent PNG")
    ap.add_argument("--model", default="isnet-general-use",
                    help="rembg model (default: isnet-general-use; "
                         "alternatives: u2net, u2net_human_seg)")
    args = ap.parse_args()

    from rembg import new_session, remove
    from PIL import Image

    os.makedirs(MODEL_HOME, exist_ok=True)
    print(f"model: {args.model} (downloads on first run if missing...)",
          flush=True)
    session = new_session(args.model)
    out = remove(Image.open(args.input).convert("RGB"), session=session)
    out.save(args.output)

    import numpy as np
    fg = (np.array(out.split()[-1]) > 16).mean() * 100
    w, h = out.size
    print(f"saved {args.output} ({w}x{h}), foreground ~{fg:.1f}% of frame")


if __name__ == "__main__":
    main()
