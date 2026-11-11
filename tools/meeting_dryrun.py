#!/usr/bin/env python3
"""Run the real Meeting app pipeline on a WAV file, no device needed.

Feeds the file through MeetingApp exactly as the mic would (45 s chunks ->
speaches STT -> WAV on disk), then stops the meeting: diarization ->
summary -> .md/.html report -> email -> ntfy ping. Uses the same code the
PiSugar runs, so it's the quickest way to test/debug the backend.

    python tools/meeting_dryrun.py meeting.wav --config config.yaml --out out/
    python tools/meeting_dryrun.py meeting.wav --speakers 4 --no-email

Secrets can come from the environment instead of the YAML:
    PISUGAR_SMTP_USER / PISUGAR_SMTP_PASSWORD / PISUGAR_EMAIL_TO / PISUGAR_NTFY_TOKEN
Second-brain shipping is always off in a dry run.
WAV: 16-bit mono; 16 kHz is what the device records.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import wave
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

import apps.meeting as meeting  # noqa: E402
from apps.meeting import CHUNK_SECS, MeetingApp, MTState  # noqa: E402


class FakeAudio:
    def __init__(self):
        self.cb = None

    def start_raw_capture(self, cb):
        self.cb = cb

    def stop_capture(self):
        pass

    def stop_all(self):
        pass


class FakeDisplay:
    def set_led(self, *rgb):
        pass


def load_config(path: str | None) -> dict:
    cfg = {}
    if path:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    email = cfg.setdefault("email", {}) or {}
    for key, env in (("username", "PISUGAR_SMTP_USER"), ("password", "PISUGAR_SMTP_PASSWORD"),
                     ("to", "PISUGAR_EMAIL_TO")):
        if os.environ.get(env):
            email[key] = os.environ[env]
    cfg["email"] = email
    if os.environ.get("PISUGAR_NTFY_TOKEN"):
        cfg.setdefault("ntfy", {})["token"] = os.environ["PISUGAR_NTFY_TOKEN"]
    cfg["second_brain"] = {"enabled": False}
    return cfg


async def run(args) -> int:
    cfg = load_config(args.config)
    m = cfg.setdefault("meeting", {}) or {}
    cfg["meeting"] = m
    if args.speakers is not None:
        m["speakers"] = args.speakers
    m["keep_audio"] = True
    m["notify"] = not args.no_notify
    if args.no_email:
        cfg["email"]["enabled"] = False
    if args.no_diarize:
        m["diarize"] = False

    os.makedirs(args.out, exist_ok=True)
    meeting.MEETINGS_DIR = os.path.abspath(args.out)

    with wave.open(args.wav, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            print("need 16-bit mono WAV", file=sys.stderr)
            return 2
        rate = w.getframerate()
        pcm = w.readframes(w.getnframes())
    cfg.setdefault("audio", {})["sample_rate_in"] = rate

    app = MeetingApp()
    ctx = SimpleNamespace(config=cfg, audio=FakeAudio(), display=FakeDisplay())
    app.on_enter(ctx)
    app._start_meeting()

    t0 = time.time()
    step = int(CHUNK_SECS * rate) * 2
    for i in range(0, len(pcm), step):
        ctx.audio.cb(pcm[i:i + step])
        app._flush_chunk()
        await asyncio.sleep(0)          # let the STT worker pick it up
    print(f"queued {len(pcm) / 2 / rate:.0f}s of audio in {app._pending_chunks} chunks")

    app._stop_meeting()
    last = None
    while app.state == MTState.SUMMARIZING:
        msg = app.phase or "..."
        if app.phase == "Transcribing..." :
            msg += f" ({app._pending_chunks} chunks left)"
        if msg != last:
            print(f"[{time.time() - t0:6.1f}s] {msg}")
            last = msg
        await asyncio.sleep(0.5)

    print(f"[{time.time() - t0:6.1f}s] done: state={app.state.value} "
          f"msg={app.done_msg or app.status_msg!r}")
    for f in sorted(os.listdir(args.out)):
        print("  ", os.path.join(args.out, f))
    return 0 if app.state == MTState.DONE else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("--config", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"))
    ap.add_argument("--out", default="meeting_dryrun_out")
    ap.add_argument("--speakers", type=int, default=None, help="known head count (0 = auto)")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--no-diarize", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
