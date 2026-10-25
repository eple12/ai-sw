"""Find when the RACE CONTROL message bar is on screen in each cut clip.

The bar is a near-black strip (about #12121a) at the top centre of the picture, with text on it. A
frame has a message when enough of a strip of that region is that colour. Writes
assets/footage/msg_windows.json: {clip: [[start, end], ...]} in seconds of the clip, so a card can
show the magnified message only while there is one.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
FOOT = HERE / "assets" / "footage"
CLIPS = ("msgs", "lim_msgs", "contact", "yellow", "jump", "tl_line")
FPS = 10
X0, Y0, W, H = 760, 40, 400, 36            # a strip across the bar, clear of the icon square
BAR = np.array([18, 18, 26])


def scan(clip: str, thr: float = 0.30):
    cmd = ["ffmpeg", "-v", "error", "-i", str(FOOT / f"{clip}.mp4"), "-vf",
           f"fps={FPS},crop={W}:{H}:{X0}:{Y0}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, stdout=subprocess.PIPE).stdout
    n = len(raw) // (W * H * 3)
    fr = np.frombuffer(raw[: n * W * H * 3], np.uint8).reshape(n, H, W, 3).astype(int)
    near = (np.abs(fr - BAR).sum(axis=3) < 40).mean(axis=(1, 2))
    on = near > thr
    runs, a = [], None
    for i, v in enumerate(on):
        if v and a is None:
            a = i
        if not v and a is not None:
            runs.append((a, i))
            a = None
    if a is not None:
        runs.append((a, n))
    # close short gaps, drop flickers
    merged = []
    for s, e in runs:
        if merged and s - merged[-1][1] <= 2:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [[round(s / FPS, 2), round(e / FPS, 2)] for s, e in merged if e - s >= 4], near


if __name__ == "__main__":
    out = {}
    for c in CLIPS:
        if not (FOOT / f"{c}.mp4").exists():
            continue
        w, near = scan(c)
        out[c] = w
        print(c, w, " max", round(float(near.max()), 2))
    (FOOT / "msg_windows.json").write_text(json.dumps(out))
