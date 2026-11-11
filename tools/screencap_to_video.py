#!/usr/bin/env python3
"""Turn a Screen Rec session into a crisp MP4 (runs on the PC, not the Pi).

    # copy a session off the device first, e.g.
    scp -r pi@puck:~/puck-ai/capture/2026-11-07_14-03-22 .
    python tools/screencap_to_video.py 2026-11-07_14-03-22            # -> .mp4 next to it
    python tools/screencap_to_video.py SESSION --scale 4 --fps 60 --png

The device only writes frames that changed, so timing is variable. Each
frame is held until the next one's timestamp (ffmpeg concat with
durations), then resampled to a constant frame rate. Nearest-neighbour
upscaling keeps the 240x280 pixels sharp (default 4x -> 960x1120).
Needs: numpy, Pillow, ffmpeg on PATH.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

W, H = 240, 280
FRAME_BYTES = W * H * 2


def rgb565_to_image(buf: bytes) -> Image.Image:
    px = np.frombuffer(buf, dtype=">u2").reshape(H, W).astype(np.uint32)
    r = ((px >> 11) & 0x1F) * 255 // 31
    g = ((px >> 5) & 0x3F) * 255 // 63
    b = (px & 0x1F) * 255 // 31
    return Image.fromarray(np.dstack([r, g, b]).astype(np.uint8))


def find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # bundled static binary, no system install needed
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def read_times(tsv_path: str) -> list[float]:
    times = []
    with open(tsv_path) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or parts[0] == "index":
                continue
            times.append(float(parts[1]))
    return times


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="capture session folder (frames.bin + frames.tsv)")
    ap.add_argument("-o", "--out", help="output .mp4 (default: <session>.mp4)")
    ap.add_argument("--scale", type=int, default=4, help="integer upscale (default 4)")
    ap.add_argument("--fps", type=int, default=30, help="output frame rate (default 30)")
    ap.add_argument("--tail", type=float, default=1.5,
                    help="seconds to hold the last frame (default 1.5)")
    ap.add_argument("--png", action="store_true",
                    help="also keep the per-frame PNGs in <session>/png/")
    args = ap.parse_args(argv)

    sess = args.session.rstrip("/\\")
    fb = os.path.join(sess, "frames.bin")
    times = read_times(os.path.join(sess, "frames.tsv"))
    size = os.path.getsize(fb)
    n = min(len(times), size // FRAME_BYTES)
    if n == 0:
        print("no frames in", sess, file=sys.stderr)
        return 1
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("ffmpeg not found (install it, or: pip install imageio-ffmpeg)", file=sys.stderr)
        return 1
    out = args.out or (sess + ".mp4")
    sw, sh = W * args.scale, H * args.scale

    work = os.path.join(sess, "png") if args.png else tempfile.mkdtemp(prefix="screencap_")
    os.makedirs(work, exist_ok=True)
    concat = os.path.join(work, "concat.txt")
    with open(fb, "rb") as f, open(concat, "w") as c:
        for i in range(n):
            img = rgb565_to_image(f.read(FRAME_BYTES))
            if args.scale != 1:
                img = img.resize((sw, sh), Image.NEAREST)
            name = f"f{i:06d}.png"
            img.save(os.path.join(work, name))
            dur = (times[i + 1] - times[i]) if i + 1 < n else args.tail
            c.write(f"file '{name}'\nduration {max(dur, 0.001):.4f}\n")
        c.write(f"file 'f{n - 1:06d}.png'\n")  # concat demuxer quirk: repeat last

    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", concat, "-vf", f"fps={args.fps},format=yuv420p",
           "-c:v", "libx264", "-preset", "slow", "-crf", "12",
           "-tune", "animation", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    if not args.png:
        shutil.rmtree(work, ignore_errors=True)
    span = times[n - 1] - times[0] + args.tail
    print(f"{n} frames, {span:.1f}s -> {out} ({sw}x{sh} @ {args.fps}fps)")
    ev = os.path.join(sess, "events.tsv")
    if os.path.exists(ev):
        print(f"LED/sync events: {ev} (t_mono is the same clock as frames.tsv; "
              f"video t=0 is t_mono {times[0]:.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
