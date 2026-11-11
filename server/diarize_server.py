"""Speaker diarization service for the frAInk Meeting app (runs on your AI server, CPU only).

POST /diarize  body = WAV (16 kHz mono 16-bit preferred; other rates are
               resampled linearly).  Query params (optional):
                 speakers=N     exact speaker count, if known
                 threshold=0.x  clustering threshold when N is unknown
                                (lower = more speakers; default 0.9)
                 min_speaker=S  auto mode only: a "speaker" with less than
                                max(S seconds, 3% of talk) is a clustering
                                artefact; its segments go to the nearest
                                real speaker (default 15; 0 disables)
-> 200 {"duration": s, "speakers": n,
        "segments": [{"start": s, "end": s, "speaker": 0}, ...]}

POST /pdf      body = self-contained report HTML (utf-8) -> application/pdf
               (WeasyPrint; added 2026-11-09 for the PiSugar meeting email)

GET /health -> {"ok": true}

sherpa-onnx offline diarization: pyannote segmentation-3.0 (ONNX) +
NeMo TitaNet-large embeddings + fast clustering. No gated HF models, CPU only
(leaves the GPU free). Speaker numbers are renumbered in order of first
appearance, so speaker 0 is whoever talks first.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import sherpa_onnx

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
SEG_MODEL = os.path.join(MODELS, "sherpa-onnx-pyannote-segmentation-3-0", "model.onnx")
# 2026-11-09: TitaNet-large replaced WeSpeaker ResNet34. On a real Pi-mic
# recording of a loudspeaker playing AMI ES2002a, ResNet34 could not tell
# the 4 voices apart (45% confusion even told there were 4); TitaNet: 5.6%.
EMB_MODEL = os.path.join(MODELS, os.environ.get("DIARIZE_EMB_MODEL", "nemo_en_titanet_large.onnx"))
PORT = int(os.environ.get("DIARIZE_PORT", "8895"))
THREADS = int(os.environ.get("DIARIZE_THREADS", "8"))
# Tuned 2026-11-09 with TitaNet, scored against the AMI human segments:
#   clean AMI 10 min   thr 0.9 -> 4 speakers,  7.7% confusion
#   Pi mic re-record   thr 0.9 -> 3 speakers,  5.6% confusion (4th talker has ~30 s)
DEFAULT_THRESHOLD = float(os.environ.get("DIARIZE_THRESHOLD", "0.9"))
DEFAULT_MIN_SPEAKER = float(os.environ.get("DIARIZE_MIN_SPEAKER", "15"))
MIN_SPEAKER_FRAC = 0.03
MAX_BODY = 1024 * 1024 * 1024  # 1 GiB (~9 h of 16 kHz mono s16)
MAX_HTML = 20 * 1024 * 1024

# Print overrides for the meeting report: same look as the email/HTML, but
# paged Letter with page numbers, white page, Inter.
PRINT_CSS = """
@page { size: Letter; margin: 16mm 14mm 18mm;
        @bottom-right { content: counter(page) " / " counter(pages);
                        font: 9px Inter, 'DejaVu Sans', sans-serif; color: #8c959f; } }
html, body { background: #fff !important; }
body { font-family: Inter, 'DejaVu Sans', sans-serif !important; font-size: 11pt; }
.wrap { max-width: none !important; padding: 0 !important; }
.card { border-color: #e3e5e8; box-shadow: none; }
.turn, li, h2, h3 { break-inside: avoid; }
h2, h3 { break-after: avoid; }
.chip { -weasy-print-color-adjust: exact; }
"""

log = logging.getLogger("diarize")
_lock = threading.Lock()  # one job at a time; requests queue


def build(num_clusters: int, threshold: float) -> sherpa_onnx.OfflineSpeakerDiarization:
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=SEG_MODEL),
            num_threads=THREADS,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=EMB_MODEL, num_threads=THREADS),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_clusters, threshold=threshold),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("invalid diarization config (models missing?)")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width != 2:
        raise ValueError(f"need 16-bit PCM WAV, got {8 * width}-bit")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    return audio, rate


def resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(audio) == 0:
        return audio
    n = int(round(len(audio) * dst / src))
    x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def renumber(segments: list[dict]) -> list[dict]:
    order: dict[int, int] = {}
    for s in segments:
        order.setdefault(s["speaker"], len(order))
    for s in segments:
        s["speaker"] = order[s["speaker"]]
    return segments


def absorb_minor(segments: list[dict], min_secs: float) -> list[dict]:
    """Fold tiny clusters (coughs, crosstalk, one-word blips) into the
    temporally nearest real speaker."""
    if min_secs <= 0 or not segments:
        return segments
    tot: dict[int, float] = {}
    for s in segments:
        tot[s["speaker"]] = tot.get(s["speaker"], 0.0) + s["end"] - s["start"]
    floor = max(min_secs, MIN_SPEAKER_FRAC * sum(tot.values()))
    minor = {k for k, v in tot.items() if v < floor}
    major = [s for s in segments if s["speaker"] not in minor]
    if not minor or not major:
        return segments
    for s in segments:
        if s["speaker"] in minor:
            mid = (s["start"] + s["end"]) / 2
            near = min(major, key=lambda m: 0.0 if m["start"] <= mid <= m["end"]
                       else min(abs(mid - m["start"]), abs(mid - m["end"])))
            s["speaker"] = near["speaker"]
    return segments


def diarize(data: bytes, speakers: int, threshold: float,
            min_speaker: float = DEFAULT_MIN_SPEAKER) -> dict:
    audio, rate = read_wav(data)
    sd = build(speakers if speakers > 0 else -1, threshold)
    audio = resample(audio, rate, sd.sample_rate)
    t0 = time.time()
    result = sd.process(audio).sort_by_start_time()
    segments = [
        {"start": round(r.start, 2), "end": round(r.end, 2), "speaker": int(r.speaker)}
        for r in result
    ]
    if speakers <= 0:
        segments = absorb_minor(segments, min_speaker)
    segments = renumber(segments)
    took = time.time() - t0
    duration = len(audio) / sd.sample_rate
    log.info("diarized %.1fs audio in %.1fs -> %d speakers, %d segments",
             duration, took, len({s["speaker"] for s in segments}), len(segments))
    return {
        "duration": round(duration, 2),
        "speakers": len({s["speaker"] for s in segments}),
        "segments": segments,
        "elapsed": round(took, 2),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "pisugar-diarize/1.0"

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def _pdf(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_HTML:
            self._json(400, {"error": "send the report HTML with Content-Length"})
            return
        html = self.rfile.read(length).decode("utf-8", "replace")
        try:
            from weasyprint import CSS, HTML  # lazy: diarization doesn't need it
            t0 = time.time()
            pdf = HTML(string=html).write_pdf(stylesheets=[CSS(string=PRINT_CSS)])
            log.info("rendered PDF %d KB in %.1fs", len(pdf) // 1024, time.time() - t0)
        except Exception as e:  # noqa: BLE001
            log.exception("pdf render failed")
            self._json(500, {"error": str(e)})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(pdf)))
        self.end_headers()
        self.wfile.write(pdf)

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/pdf":
            self._pdf()
            return
        if url.path != "/diarize":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._json(400, {"error": "send a WAV body with Content-Length"})
            return
        data = self.rfile.read(length)
        q = parse_qs(url.query)
        try:
            speakers = int(q.get("speakers", ["0"])[0])
            threshold = float(q.get("threshold", [str(DEFAULT_THRESHOLD)])[0])
            min_speaker = float(q.get("min_speaker", [str(DEFAULT_MIN_SPEAKER)])[0])
            with _lock:
                out = diarize(data, speakers, threshold, min_speaker)
            self._json(200, out)
        except Exception as e:  # noqa: BLE001 - report, never crash the server
            log.exception("diarization failed")
            self._json(500, {"error": str(e)})

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build(-1, DEFAULT_THRESHOLD)  # fail fast if models are missing
    log.info("listening on :%d", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
