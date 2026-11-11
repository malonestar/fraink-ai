import os

from core import screencap
from core.screencap import ScreenCapture

FRAME = 240 * 280 * 2


def test_capture_writes_frames_and_events(tmp_path):
    cap = ScreenCapture(root=str(tmp_path))
    assert not cap.active
    d = cap.start()
    assert cap.active and os.path.isdir(d)
    cap.offer(b"\x00" * FRAME)
    cap.offer(b"\xff" * FRAME)
    cap.event("led 255,0,0")
    cap.stop()
    assert not cap.active
    assert os.path.getsize(os.path.join(d, "frames.bin")) == 2 * FRAME
    rows = open(os.path.join(d, "frames.tsv")).read().splitlines()
    assert rows[0].startswith("index") and len(rows) == 3
    assert "led 255,0,0" in open(os.path.join(d, "events.tsv")).read()


def test_offer_when_inactive_is_noop(tmp_path):
    cap = ScreenCapture(root=str(tmp_path))
    cap.offer(b"\x00" * FRAME)
    cap.event("x")
    cap.stop()
    assert os.listdir(tmp_path) == []


def test_auto_stop_after_max(tmp_path, monkeypatch):
    cap = ScreenCapture(root=str(tmp_path))
    cap.start()
    monkeypatch.setattr(screencap, "MAX_SECS", -1)
    cap.offer(b"\x00" * FRAME)
    assert not cap.active


def test_converter_roundtrip(tmp_path):
    import numpy as np
    from tools.screencap_to_video import rgb565_to_image
    px = np.full((280, 240), 0xF800, dtype=">u2")  # pure red
    img = rgb565_to_image(px.tobytes())
    assert img.size == (240, 280)
    assert img.getpixel((0, 0)) == (255, 0, 0)
