import os
import tempfile
import pytest

from apps.note_taker import NoteTakerApp, NTState, format_note_filename, save_note_markdown


def test_initial_state_is_ready():
    app = NoteTakerApp()
    assert app.state == NTState.READY


def test_format_filename():
    name = format_note_filename(2026, 5, 27, 14, 30, 22)
    assert name == "2026-05-27_14-30-22"


def test_save_note_markdown():
    with tempfile.TemporaryDirectory() as d:
        path = save_note_markdown(d, "2026-05-27_14-30-22", "Hello world test note")
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "# Voice Memo" in content
        assert "Hello world test note" in content


def test_wakeword_disabled():
    app = NoteTakerApp()
    assert app.uses_wakeword is False


def test_keep_awake_while_recording_and_transcribing():
    app = NoteTakerApp()
    assert app.keep_awake is False           # READY -> screensaver allowed
    app.state = NTState.RECORDING
    assert app.keep_awake is True
    app.state = NTState.TRANSCRIBING
    assert app.keep_awake is True
    app.state = NTState.SENT
    assert app.keep_awake is False


def test_set_state_drives_led():
    from unittest.mock import MagicMock
    app = NoteTakerApp()
    app.ctx = MagicMock()
    app._set_state(NTState.RECORDING)
    app.ctx.display.set_led.assert_called_with(0, 255, 0)    # mic open
    app._set_state(NTState.TRANSCRIBING)
    app.ctx.display.set_led.assert_called_with(255, 255, 0)  # working
    app._set_state(NTState.READY)
    app.ctx.display.set_led.assert_called_with(0, 0, 0)


def test_on_exit_unregisters_xiaozhi_handler():
    from unittest.mock import MagicMock
    app = NoteTakerApp()
    ctx = MagicMock()
    ctx.config = {"second_brain": {"enabled": False}}
    app.on_enter(ctx)
    app.on_exit()
    ctx.xiaozhi.detach.assert_called_once()


def test_uses_double_for_replay():
    # The replay double-tap was dead code: uses_double was never set, so the
    # button classifier never emitted DOUBLE for this app.
    assert NoteTakerApp.uses_double is True


def test_save_note_wav_writes_playable_wav(tmp_path):
    import wave
    from apps.note_taker import save_note_wav
    pcm = [(b"\x00\x01" * 480)] * 3  # 3 chunks of fake 16-bit PCM
    path = save_note_wav(str(tmp_path), "memo", pcm, rate=16000)
    assert path and os.path.exists(path)
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getnframes() == 480 * 3


def test_save_note_wav_empty_buffer_returns_none(tmp_path):
    from apps.note_taker import save_note_wav
    assert save_note_wav(str(tmp_path), "memo", []) is None


import asyncio
from unittest.mock import MagicMock
from apps.note_taker import NoteTakerApp


def test_save_submits_candidate_to_secondbrain(tmp_path, monkeypatch):
    import apps.note_taker as nt
    monkeypatch.setattr(nt, "NOTES_DIR", str(tmp_path))
    app = NoteTakerApp()
    app.ctx = MagicMock()
    app.ctx.config = {"telegram": {}, "second_brain": {"enabled": False, "scope": "voice_memos"}}
    app._pcm_buffer = [b"\x00\x01"]
    app._record_elapsed = 5.0
    submitted = {}
    async def fake_submit(fname, md): submitted["fname"], submitted["md"] = fname, md
    app._shipper.submit = fake_submit          # shipper created in on_enter/__init__
    asyncio.run(app._save_and_send("Call the dentist"))
    assert submitted["fname"].endswith("-voice_memos-call_the_dentist.md")
    assert "**VOICE MEMO:** Call the dentist" in submitted["md"]
