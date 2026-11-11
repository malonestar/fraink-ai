"""Screen capture for video B-roll: record exactly what the display shows.

When on, every frame the display actually pushes to the panel (only frames
that changed) is appended, raw RGB565, to a session folder:

    <app dir>/capture/<YYYY-mm-dd_HH-MM-SS>/
        frames.bin   back-to-back 240x280 RGB565 frames (134,400 B each)
        frames.tsv   one line per frame: index, monotonic s, wall-clock ISO
        events.tsv   LED colour changes (handy sync marks vs. camera footage)

Raw bytes, not PNGs: encoding on a Pi Zero would cost frames; the PC does
the conversion afterwards (tools/screencap_to_video.py -> crisp MP4).
Writes happen on a background thread with a bounded queue; if the SD card
can't keep up, frames are dropped rather than stalling the UI.

Toggle: Settings app -> "Screen Rec". Auto-stops after MAX_SECS, and is
always off after a reboot/restart so it can't silently fill the card.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from datetime import datetime
from core.paths import app_path

log = logging.getLogger(__name__)

CAPTURE_ROOT = app_path("capture")
MAX_SECS = 15 * 60
QUEUE_FRAMES = 120  # ~16 MB of RGB565 buffered at most


class ScreenCapture:
    def __init__(self, root: str = CAPTURE_ROOT):
        self.root = root
        self.dir: str = ""
        self._q: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        self._t0 = 0.0
        self._count = 0
        self.dropped = 0
        self._events = None

    @property
    def active(self) -> bool:
        return self._q is not None

    def start(self) -> str:
        if self.active:
            return self.dir
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.dir = os.path.join(self.root, stamp)
        os.makedirs(self.dir, exist_ok=True)
        self._q = queue.Queue(maxsize=QUEUE_FRAMES)
        self._t0 = time.monotonic()
        self._count = 0
        self.dropped = 0
        self._events = open(os.path.join(self.dir, "events.tsv"), "a", buffering=1)
        self._events.write("t_mono\twall\tevent\n")
        self._thread = threading.Thread(target=self._writer, args=(self._q, self.dir),
                                         name="screencap", daemon=True)
        self._thread.start()
        log.info("Screen capture started: %s", self.dir)
        return self.dir

    def stop(self) -> None:
        if not self.active:
            return
        q, self._q = self._q, None
        q.put(None)
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        if self._events:
            self._events.close()
            self._events = None
        log.info("Screen capture stopped: %d frames, %d dropped -> %s",
                 self._count, self.dropped, self.dir)

    def offer(self, rgb565: bytes) -> None:
        """Called by Display.flip() for every frame actually pushed."""
        q = self._q
        if q is None:
            return
        now = time.monotonic()
        if now - self._t0 > MAX_SECS:
            log.info("Screen capture hit %d min cap, stopping", MAX_SECS // 60)
            self.stop()
            return
        try:
            q.put_nowait((self._count, now, datetime.now().isoformat(timespec="milliseconds"), rgb565))
            self._count += 1
        except queue.Full:
            self.dropped += 1

    def event(self, text: str) -> None:
        if self._events:
            self._events.write(f"{time.monotonic():.3f}\t"
                               f"{datetime.now().isoformat(timespec='milliseconds')}\t{text}\n")

    @staticmethod
    def _writer(q: queue.Queue, folder: str) -> None:
        with open(os.path.join(folder, "frames.bin"), "ab") as fb, \
                open(os.path.join(folder, "frames.tsv"), "a") as ft:
            ft.write("index\tt_mono\twall\n")
            while True:
                item = q.get()
                if item is None:
                    break
                idx, t, wall, data = item
                fb.write(data)
                ft.write(f"{idx}\t{t:.4f}\t{wall}\n")
