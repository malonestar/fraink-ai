"""start_raw_capture feeds raw PCM to a sync callback (wake word path)."""

from unittest.mock import MagicMock

import numpy as np

from core import audio as audio_mod
from core.audio import AudioService


def _service(monkeypatch) -> AudioService:
    # Never touch real ALSA/PortAudio devices in tests.
    monkeypatch.setattr(audio_mod.sd, "InputStream", MagicMock())
    return AudioService({})


def test_start_raw_capture_opens_input_stream(monkeypatch):
    svc = _service(monkeypatch)
    svc.start_raw_capture(lambda pcm: None)
    assert svc.is_raw_capturing is True
    audio_mod.sd.InputStream.assert_called_once()
    kwargs = audio_mod.sd.InputStream.call_args.kwargs
    assert kwargs["samplerate"] == 16000
    assert kwargs["channels"] == 1
    assert kwargs["dtype"] == "int16"


def test_raw_callback_passes_pcm_bytes(monkeypatch):
    svc = _service(monkeypatch)
    received = []
    svc.start_raw_capture(received.append)

    frames = 960
    indata = (np.ones((frames, 1), dtype=np.int16) * 1000)
    svc._raw_callback(indata, frames, None, None)

    assert len(received) == 1
    assert received[0] == indata[:, 0].tobytes()


def test_stop_capture_clears_raw_state(monkeypatch):
    svc = _service(monkeypatch)
    svc.start_raw_capture(lambda pcm: None)
    svc.stop_capture()
    assert svc.is_raw_capturing is False
    assert svc._input_stream is None
    # Callback after stop must be a no-op, not an error.
    svc._raw_callback(np.zeros((960, 1), dtype=np.int16), 960, None, None)


def test_raw_capture_works_without_opus(monkeypatch):
    monkeypatch.setattr(audio_mod, "HAS_OPUS", False)
    svc = _service(monkeypatch)
    svc.start_raw_capture(lambda pcm: None)  # must not refuse like start_capture
    assert svc.is_raw_capturing is True


def test_start_raw_while_another_stream_active_does_not_leak(monkeypatch):
    svc = _service(monkeypatch)
    existing = MagicMock()
    svc._input_stream = existing
    svc._capturing = True

    svc.start_raw_capture(lambda pcm: None)

    existing.stop.assert_called_once()
    existing.close.assert_called_once()
    assert svc._capturing is False
    assert svc.is_raw_capturing is True
