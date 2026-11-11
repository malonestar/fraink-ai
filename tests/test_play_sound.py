"""play_sound_file: WAV playback via sounddevice.

Regression: the old implementation used pygame.mixer, which is not installed
on the device — every chime silently failed. sounddevice is already a dep.
"""

import struct
import wave
from unittest.mock import MagicMock

from core import audio as audio_mod
from core.audio import AudioService


def _write_wav(path, rate=22050, frames=100, channels=1):
    w = wave.open(str(path), "w")
    w.setnchannels(channels)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(struct.pack(f"{frames * channels}h", *([0] * frames * channels)))
    w.close()


def test_play_sound_file_plays_wav_at_file_rate(monkeypatch, tmp_path):
    played = {}

    def fake_play(data, rate):
        played["frames"] = len(data)
        played["rate"] = rate

    monkeypatch.setattr(audio_mod.sd, "play", fake_play)
    path = tmp_path / "s.wav"
    _write_wav(path, rate=22050, frames=100)

    AudioService.play_sound_file(str(path))

    assert played == {"frames": 100, "rate": 22050}


def test_play_sound_file_stereo(monkeypatch, tmp_path):
    played = {}

    def fake_play(data, rate):
        played["shape"] = data.shape

    monkeypatch.setattr(audio_mod.sd, "play", fake_play)
    path = tmp_path / "s.wav"
    _write_wav(path, frames=50, channels=2)

    AudioService.play_sound_file(str(path))

    assert played["shape"] == (50, 2)


def test_play_sound_file_missing_file_does_not_raise(monkeypatch):
    monkeypatch.setattr(audio_mod.sd, "play", MagicMock())
    AudioService.play_sound_file("does/not/exist.wav")  # warns, no crash


def test_play_sound_file_returns_duration(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_mod.sd, "play", MagicMock())
    path = tmp_path / "s.wav"
    _write_wav(path, rate=16000, frames=8000)  # 0.5s

    duration = AudioService.play_sound_file(str(path))

    assert abs(duration - 0.5) < 1e-6


def test_play_sound_file_failure_returns_zero(monkeypatch):
    monkeypatch.setattr(audio_mod.sd, "play", MagicMock())
    assert AudioService.play_sound_file("does/not/exist.wav") == 0.0
