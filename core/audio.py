import asyncio
import logging
from collections import deque
from typing import Awaitable, Callable, Optional

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

try:
    import opuslib
    HAS_OPUS = True
except (ImportError, OSError):
    HAS_OPUS = False
    log.warning("opuslib not available — audio encode/decode disabled")


class AudioService:
    def __init__(self, config: dict):
        audio_cfg = config.get("audio", {})
        self._device = audio_cfg.get("alsa_device", "hw:1,0")
        self._rate_in = audio_cfg.get("sample_rate_in", 16000)
        self._rate_out = audio_cfg.get("sample_rate_out", 24000)
        self._frame_duration = audio_cfg.get("opus_frame_duration", 60)
        self._frame_size_in = int(self._rate_in * self._frame_duration / 1000)
        self._frame_size_out = int(self._rate_out * self._frame_duration / 1000)
        self._encoder: Optional[object] = None
        self._decoder: Optional[object] = None
        self._input_stream: Optional[sd.InputStream] = None
        self._output_stream: Optional[sd.OutputStream] = None
        self._on_opus_frame: Optional[Callable[[bytes], Awaitable[None]]] = None
        # deque, NOT asyncio.Queue: the consumer is the PortAudio callback
        # thread, and asyncio.Queue is not thread-safe. deque's append/popleft
        # are atomic, which is exactly the single-producer/single-consumer
        # contract here.
        self._playback_queue: deque = deque()
        self._level: float = 0.0
        self._capturing = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._playing = False
        self._on_pcm: Optional[Callable[[bytes], None]] = None
        self._raw_capturing = False

    @property
    def level(self) -> float:
        return self._level

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    @property
    def is_raw_capturing(self) -> bool:
        return self._raw_capturing

    @property
    def is_playing(self) -> bool:
        return self._playing

    def set_output_sample_rate(self, rate: int):
        self._rate_out = rate
        self._frame_size_out = int(rate * self._frame_duration / 1000)
        if HAS_OPUS:
            self._decoder = opuslib.Decoder(rate, 1)

    def start_capture(self, on_opus_frame: Callable[[bytes], Awaitable[None]]):
        if not HAS_OPUS:
            log.error("Cannot capture: opuslib not available")
            return
        if self._input_stream is not None:
            self.stop_capture()  # never leak the single WM8960 input stream
        self._on_opus_frame = on_opus_frame
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.get_event_loop()
        self._encoder = opuslib.Encoder(self._rate_in, 1, opuslib.APPLICATION_VOIP)
        self._capturing = True
        self._input_stream = sd.InputStream(
            samplerate=self._rate_in,
            channels=1,
            dtype="int16",
            blocksize=self._frame_size_in,
            callback=self._capture_callback,
        )
        self._input_stream.start()
        log.info("Audio capture started (%dHz, %dms frames)", self._rate_in, self._frame_duration)

    def _capture_callback(self, indata, frames, time_info, status):
        if status:
            log.debug("Audio capture status: %s", status)
        pcm = indata[:, 0].tobytes()
        rms = np.sqrt(np.mean(np.frombuffer(pcm, dtype=np.int16).astype(float) ** 2))
        self._level = min(1.0, rms / 16000.0)
        if self._encoder and self._on_opus_frame and self._loop:
            opus_data = self._encoder.encode(pcm, self._frame_size_in)
            asyncio.run_coroutine_threadsafe(
                self._on_opus_frame(opus_data), self._loop
            )

    def stop_capture(self):
        self._capturing = False
        self._raw_capturing = False
        if self._input_stream:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None
        self._encoder = None
        self._on_pcm = None
        self._level = 0.0
        log.info("Audio capture stopped")

    def start_raw_capture(self, on_pcm: Callable[[bytes], None]):
        """16kHz mono int16 mic stream -> raw PCM bytes to a sync callback.

        Feeds the wake word detector while idle (no opus encode). WM8960
        allows one input stream at a time — callers stop_capture() first.
        """
        if self._input_stream is not None:
            self.stop_capture()  # never leak the single WM8960 input stream
        self._on_pcm = on_pcm
        self._raw_capturing = True
        self._input_stream = sd.InputStream(
            samplerate=self._rate_in,
            channels=1,
            dtype="int16",
            blocksize=self._frame_size_in,
            callback=self._raw_callback,
        )
        self._input_stream.start()
        log.info("Raw audio capture started (%dHz, wake word feed)", self._rate_in)

    def _raw_callback(self, indata, frames, time_info, status):
        if status:
            log.debug("Raw capture status: %s", status)
        if self._on_pcm:
            self._on_pcm(indata[:, 0].tobytes())

    def start_playback(self):
        if not HAS_OPUS:
            log.error("Cannot play: opuslib not available")
            return
        self._decoder = opuslib.Decoder(self._rate_out, 1)
        self._playing = True
        self._output_stream = sd.OutputStream(
            samplerate=self._rate_out,
            channels=1,
            dtype="int16",
            blocksize=self._frame_size_out,
            callback=self._playback_callback,
        )
        self._output_stream.start()
        log.info("Audio playback started (%dHz)", self._rate_out)

    def _playback_callback(self, outdata, frames, time_info, status):
        try:
            pcm = self._playback_queue.popleft()
            data = np.frombuffer(pcm, dtype=np.int16)
            if len(data) < frames:
                padded = np.zeros(frames, dtype=np.int16)
                padded[: len(data)] = data
                data = padded
            outdata[:, 0] = data[:frames]
            rms = np.sqrt(np.mean(outdata[:, 0].astype(float) ** 2))
            self._level = min(1.0, rms / 16000.0)
        except IndexError:
            outdata.fill(0)
            self._level = 0.0

    async def enqueue_opus(self, opus_data: bytes):
        if self._decoder:
            pcm = self._decoder.decode(opus_data, self._frame_size_out)
            self._playback_queue.append(pcm)

    def stop_playback(self):
        self._playing = False
        self._level = 0.0
        if self._output_stream:
            self._output_stream.stop()
            self._output_stream.close()
            self._output_stream = None
        self._decoder = None
        self._playback_queue.clear()
        log.info("Audio playback stopped")

    def stop_all(self):
        self.stop_capture()
        self.stop_playback()

    @staticmethod
    def play_wav_bytes(data: bytes) -> float:
        """Play an in-memory WAV (e.g. TTS audio fetched over HTTP).

        Same semantics as play_sound_file: fire-and-forget, a new call
        replaces any sound still playing, returns duration (0.0 on failure).
        """
        try:
            import io
            import wave
            with wave.open(io.BytesIO(data), "rb") as w:
                rate = w.getframerate()
                nframes = w.getnframes()
                pcm = np.frombuffer(w.readframes(nframes), dtype=np.int16)
                if w.getnchannels() == 2:
                    pcm = pcm.reshape(-1, 2)
            sd.play(pcm, rate)
            return nframes / rate if rate else 0.0
        except Exception as e:
            log.warning("Failed to play wav bytes: %s", e)
            return 0.0

    @staticmethod
    def play_sound_file(path: str) -> float:
        """Fire-and-forget WAV playback via sounddevice.

        Used for short UI sounds (wake chime, pomodoro chime, memo replay).
        pygame is not installed on the device, so this goes straight to
        PortAudio; the WAV header supplies the sample rate. A new call
        replaces any sound still playing (sd.play semantics).

        Returns the clip duration in seconds (0.0 on failure) so callers can
        show playback state or sequence sounds without overlap.
        """
        try:
            import wave
            with wave.open(path, "rb") as w:
                rate = w.getframerate()
                nframes = w.getnframes()
                data = np.frombuffer(w.readframes(nframes), dtype=np.int16)
                if w.getnchannels() == 2:
                    data = data.reshape(-1, 2)
            sd.play(data, rate)
            return nframes / rate if rate else 0.0
        except Exception as e:
            log.warning("Failed to play sound %s: %s", path, e)
            return 0.0
