"""Playback must drive AudioService.level so the face can lip-sync TTS.

Regression: level was only computed in the capture callback, so during
SPEAKING (capture stopped, level pinned to 0) the mouth never opened.
"""

import numpy as np

from core.audio import AudioService


def _make_service() -> AudioService:
    return AudioService({})


def _playback_frame(svc: AudioService, frames: int) -> np.ndarray:
    outdata = np.zeros((frames, 1), dtype=np.int16)
    svc._playback_callback(outdata, frames, None, None)
    return outdata


def test_playback_callback_sets_level_from_pcm():
    svc = _make_service()
    frames = 240
    loud = (np.ones(frames, dtype=np.int16) * 8000).tobytes()
    svc._playback_queue.append(loud)

    _playback_frame(svc, frames)

    assert svc.level > 0.3  # rms 8000 / 16000 = 0.5


def test_playback_callback_zero_level_when_queue_empty():
    svc = _make_service()
    frames = 240
    loud = (np.ones(frames, dtype=np.int16) * 8000).tobytes()
    svc._playback_queue.append(loud)
    _playback_frame(svc, frames)
    assert svc.level > 0.0

    _playback_frame(svc, frames)  # queue now empty -> silence

    assert svc.level == 0.0


def test_stop_playback_resets_level():
    svc = _make_service()
    svc._level = 0.7

    svc.stop_playback()

    assert svc.level == 0.0
