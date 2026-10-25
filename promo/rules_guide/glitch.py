"""Find (and optionally repair) single-frame glitches in a menu clip.

The offscreen capture of a menu sometimes returns one frame that is partly scrambled blocks (the
text and the map half-uploaded). A glitch frame differs a lot from the frame before AND the frame
after, while those two are alike. Moving footage never looks like that, so this is only used on
menu clips.

    python glitch.py scan  clip.mp4            # print the glitch frames
    python glitch.py fix   clip.mp4 out.mp4    # replace each by the previous frame
"""
from __future__ import annotations

import subprocess
import sys

import numpy as np

W, H = 960, 540


def read(path):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, stdout=subprocess.PIPE).stdout
    n = len(raw) // (W * H)
    return np.frombuffer(raw[: n * W * H], np.uint8).reshape(n, H, W).astype(int)


def find(fr, thr=0.25):
    bad = []
    for i in range(1, len(fr) - 1):
        a = np.abs(fr[i] - fr[i - 1]).mean()
        b = np.abs(fr[i] - fr[i + 1]).mean()
        c = np.abs(fr[i - 1] - fr[i + 1]).mean()
        if a > thr and b > thr and c < 0.3 * min(a, b):
            bad.append(i)
    return bad


if __name__ == "__main__":
    mode, src = sys.argv[1], sys.argv[2]
    fr = read(src)
    bad = find(fr)
    print(f"{src}: {len(fr)} frames, {len(bad)} glitch frames", bad[:40])
    if mode == "fix":
        out = sys.argv[3]
        drop = set(bad)
        # re-encode, dropping glitch frames from the sequence and holding the previous frame there
        sel = "+".join(f"eq(n\\,{i})" for i in bad) or "0"
        # 'select' drops; use a frame-replace via the 'freezeframes' filter would need ranges, so
        # build the list of (first, last, replacement) from the glitch runs instead
        runs, k = [], 0
        bad_sorted = sorted(bad)
        while k < len(bad_sorted):
            a = z = bad_sorted[k]
            while k + 1 < len(bad_sorted) and bad_sorted[k + 1] == z + 1:
                k += 1
                z = bad_sorted[k]
            runs.append((a, z))
            k += 1
        vf = ",".join(f"freezeframes=first_frame={a}:last_frame={z}:replace={a - 1}" for a, z in runs) or "null"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-vf", vf,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", out], check=True)
        print("fixed ->", out)
