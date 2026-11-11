"""Wake word detection via pymicro-wakeword (microWakeWord streaming models).

Models live in assets/models/ as <name>.json + <name>.tflite pairs (the
JSON's "model" field resolves relative to its own directory). process_audio()
runs on the sounddevice capture thread; detections are marshaled onto the
asyncio loop captured at start() (Python 3.13: get_event_loop() fails in
non-main threads, so the loop ref is stored upfront).
"""

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

try:
    from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
    HAS_MWW = True
except ImportError:
    MicroWakeWord = None
    MicroWakeWordFeatures = None
    HAS_MWW = False
    log.warning("pymicro-wakeword not available — wake word detection disabled")

MODELS_DIR = Path(__file__).resolve().parent.parent / "assets" / "models"
DEFAULT_MODEL = "hey_frank_v5"


class WakeWordService:
    def __init__(self, config: dict):
        ww_cfg = config.get("wakeword", {})
        self._enabled = ww_cfg.get("enabled", True) and HAS_MWW
        self._model_name = ww_cfg.get("model", DEFAULT_MODEL)
        # None -> use the JSON's probability_cutoff. Firmware tuning landed
        # ~0.85 vs the JSON's conservative 0.81; expect to tune on device.
        self._cutoff = ww_cfg.get("cutoff")
        self._model: Optional[object] = None
        self._features: Optional[object] = None
        self._callback: Optional[Callable[[], Awaitable[None]]] = None
        self._paused = False
        self._active = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def is_active(self) -> bool:
        return self._active and not self._paused

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        self._paused = not self._enabled
        if self._enabled:
            self._reset_detector()  # drop stale buffers from before the toggle
        return self._enabled

    @property
    def model_name(self) -> str:
        return self._model_name

    @staticmethod
    def available_models() -> list[str]:
        return sorted(p.stem for p in MODELS_DIR.glob("*.json"))

    def set_model(self, name: str) -> bool:
        """Switch wake phrase at runtime (Settings). Reloads in place if a
        listener is armed; unknown names are ignored."""
        if not name or name == self._model_name:
            return False
        if not (MODELS_DIR / f"{name}.json").exists():
            log.warning("Wake word model %s not found in %s", name, MODELS_DIR)
            return False
        self._model_name = name
        if self._active and self._callback:
            cb, paused = self._callback, self._paused
            self.start(cb)
            self._paused = paused
        log.info("Wake word model -> %s", name)
        return True

    def start(self, callback: Callable[[], Awaitable[None]]):
        if not self._enabled:
            return
        self._callback = callback
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.get_event_loop()
        config_path = MODELS_DIR / f"{self._model_name}.json"
        try:
            self._model = MicroWakeWord.from_config(config_path)
            if self._cutoff is not None:
                self._model.probability_cutoff = self._cutoff
            self._features = MicroWakeWordFeatures()
            self._paused = False
            self._active = True
            log.info("Wake word started: %s (cutoff %.2f)",
                     self._model_name, self._model.probability_cutoff)
        except Exception as e:
            log.error("Failed to load wake word model %s: %s", config_path, e)
            self._model = None
            self._features = None
            self._active = False

    def process_audio(self, pcm_int16: bytes):
        """Feed raw 16kHz mono int16 PCM. Runs on the audio capture thread."""
        if not self._active or self._paused or not self._model or not self._features:
            return
        for features in self._features.process_streaming(pcm_int16):
            if self._model.process_streaming(features):
                log.info("Wake word detected: %s", self._model.wake_word)
                self._reset_detector()
                if self._callback and self._loop:
                    asyncio.run_coroutine_threadsafe(self._callback(), self._loop)
                break

    def _reset_detector(self):
        if self._model:
            self._model.reset()
        if self._features:
            self._features.reset()

    def pause(self):
        self._paused = True

    def resume(self):
        if self._enabled:
            self._reset_detector()  # drop stale audio so old buffers can't trigger
            self._paused = False

    def stop(self):
        self._active = False
        self._model = None
        self._features = None
        self._callback = None
