import asyncio

import pytest

from core import wakeword
from core.wakeword import WakeWordService


class FakeModel:
    """Mimics pymicro_wakeword.MicroWakeWord."""
    def __init__(self):
        self.probability_cutoff = 0.81  # value "from the JSON"
        self.wake_word = "hey stackchan"
        self.detect_next = False
        self.reset_calls = 0

    @classmethod
    def from_config(cls, config_path):
        cls.last_config_path = config_path
        return cls()

    def process_streaming(self, features):
        return self.detect_next

    def reset(self):
        self.reset_calls += 1


class FakeFeatures:
    """Mimics pymicro_wakeword.MicroWakeWordFeatures."""
    def __init__(self):
        self.reset_calls = 0

    def process_streaming(self, audio_bytes):
        yield "features"

    def reset(self):
        self.reset_calls += 1


@pytest.fixture
def mww(monkeypatch):
    monkeypatch.setattr(wakeword, "HAS_MWW", True)
    monkeypatch.setattr(wakeword, "MicroWakeWord", FakeModel)
    monkeypatch.setattr(wakeword, "MicroWakeWordFeatures", FakeFeatures)


def _service(config=None):
    return WakeWordService(config or {})


def test_disabled_when_library_missing(monkeypatch):
    monkeypatch.setattr(wakeword, "HAS_MWW", False)
    svc = _service({"wakeword": {"enabled": True}})
    assert svc.is_enabled is False
    svc.start(lambda: None)  # must not raise
    assert svc.is_active is False


def test_config_defaults(mww):
    svc = _service()
    assert svc.is_enabled is True
    assert svc._model_name == "hey_frank_v5"
    assert svc._cutoff is None


def test_config_disabled(mww):
    svc = _service({"wakeword": {"enabled": False}})
    assert svc.is_enabled is False


def test_start_loads_model_json(mww):
    async def run():
        svc = _service({"wakeword": {"model": "hey_frank_v5"}})
        svc.start(_noop_cb)
        assert svc.is_active is True
        assert str(FakeModel.last_config_path).endswith("hey_frank_v5.json")
    asyncio.run(run())


def test_cutoff_override(mww):
    async def run():
        svc = _service({"wakeword": {"cutoff": 0.85}})
        svc.start(_noop_cb)
        assert svc._model.probability_cutoff == 0.85
    asyncio.run(run())


def test_no_cutoff_keeps_json_value(mww):
    async def run():
        svc = _service()
        svc.start(_noop_cb)
        assert svc._model.probability_cutoff == 0.81
    asyncio.run(run())


async def _noop_cb():
    pass


def test_detection_fires_callback_on_loop(mww):
    async def run():
        fired = asyncio.Event()

        async def cb():
            fired.set()

        svc = _service()
        svc.start(cb)
        svc._model.detect_next = True
        svc.process_audio(b"\x00" * 1920)
        await asyncio.wait_for(fired.wait(), timeout=1.0)
        # Detector state must be reset after a detection.
        assert svc._model.reset_calls == 1
        assert svc._features.reset_calls == 1
    asyncio.run(run())


def test_paused_skips_detection(mww):
    async def run():
        fired = asyncio.Event()

        async def cb():
            fired.set()

        svc = _service()
        svc.start(cb)
        svc._model.detect_next = True
        svc.pause()
        assert svc.is_active is False
        svc.process_audio(b"\x00" * 1920)
        await asyncio.sleep(0.05)
        assert not fired.is_set()
    asyncio.run(run())


def test_resume_resets_detector_state(mww):
    async def run():
        svc = _service()
        svc.start(_noop_cb)
        svc.pause()
        svc.resume()
        assert svc.is_active is True
        assert svc._model.reset_calls == 1
        assert svc._features.reset_calls == 1
    asyncio.run(run())


def test_model_load_failure_leaves_inactive(mww, monkeypatch):
    def boom(config_path):
        raise FileNotFoundError("no model")
    monkeypatch.setattr(FakeModel, "from_config", boom)

    async def run():
        svc = _service()
        svc.start(_noop_cb)
        assert svc.is_active is False
        svc.process_audio(b"\x00" * 1920)  # must not raise
    asyncio.run(run())


def test_toggle_flips_enabled(mww):
    svc = _service()
    assert svc.toggle() is False
    assert svc.is_enabled is False
    assert svc.toggle() is True
    assert svc.is_enabled is True


def test_stop_clears_state(mww):
    async def run():
        svc = _service()
        svc.start(_noop_cb)
        svc.stop()
        assert svc.is_active is False
        svc.process_audio(b"\x00" * 1920)  # must not raise
    asyncio.run(run())


def test_toggle_back_on_resets_detector(mww):
    async def run():
        svc = _service()
        svc.start(_noop_cb)
        svc.toggle()   # off
        svc.toggle()   # on again
        assert svc._model.reset_calls == 1
        assert svc._features.reset_calls == 1
    asyncio.run(run())
