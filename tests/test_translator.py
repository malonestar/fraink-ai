"""Translator app: direction logic, prompts, state machine, language cycling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from apps.translator import (
    TranslatorApp, TRState, translation_target, build_translate_messages,
    lang_name, DEFAULT_LANGUAGES, DEFAULT_HOME, MAX_RECORD_SECS,
)
from core.aiservices import Transcript


def _ctx():
    ctx = MagicMock()
    ctx.config = {}
    ctx.audio.level = 0.0
    ctx.audio.play_wav_bytes = MagicMock(return_value=1.5)
    return ctx


def _app(ctx=None):
    app = TranslatorApp()
    app.on_enter(ctx or _ctx())
    return app


# --- pure logic -------------------------------------------------------------

def test_two_way_direction():
    target = {"code": "es", "name": "Spanish"}
    home = {"code": "en", "name": "English"}
    # target language spoken -> comes back home
    assert translation_target("es", target, home) is home
    # home language spoken -> goes to target
    assert translation_target("en", target, home) is target
    # any third language -> goes to target
    assert translation_target("fr", target, home) is target


def test_translate_prompt_names_languages():
    msgs = build_translate_messages("hola", "es", {"code": "en", "name": "English"})
    assert "Spanish" in msgs[0]["content"]
    assert "English" in msgs[0]["content"]
    assert msgs[1]["content"] == "hola"


def test_lang_name_fallback():
    assert lang_name("es") == "Spanish"
    assert lang_name("xx") == "xx"
    assert lang_name("") == "?"


def test_defaults_have_tts_voices():
    for lang in DEFAULT_LANGUAGES + [DEFAULT_HOME]:
        assert lang["tts_model"].startswith("speaches-ai/piper-")
        assert lang["voice"]


# --- app behavior -----------------------------------------------------------

def test_single_starts_and_stops_recording():
    app = _app()
    app.on_button("single")
    assert app.state == TRState.RECORDING
    app.ctx.audio.start_raw_capture.assert_called_once()

    app._pcm = [b"\x00\x01"]
    # keep the pipeline from actually running
    app.spawn = lambda coro: coro.close()
    app.on_button("single")
    assert app.state == TRState.PROCESSING
    app.ctx.audio.stop_capture.assert_called()


def test_stop_with_no_audio_returns_ready():
    app = _app()
    app._start_recording()
    app._pcm = []
    app._stop_recording()
    assert app.state == TRState.READY


def test_secondary_long_cycles_language():
    app = _app()
    first = app.target["code"]
    app.on_secondary("long")
    assert app.target["code"] != first
    for _ in range(len(app.languages) - 1):
        app.on_secondary("long")
    assert app.target["code"] == first  # wrapped around


def test_max_record_autostops():
    app = _app()
    app.spawn = lambda coro: coro.close()
    app._start_recording()
    app._pcm = [b"\x00\x01"]
    asyncio.run(app.update(MAX_RECORD_SECS + 1))
    assert app.state == TRState.PROCESSING


def test_process_translates_and_plays():
    ctx = _ctx()
    app = _app(ctx)
    app._pcm = [b"\x00\x01" * 100]
    app.state = TRState.PROCESSING
    app._ai = MagicMock()
    app._ai.transcribe = AsyncMock(return_value=Transcript(text="hola amigo", language="es"))
    app._ai.chat = AsyncMock(return_value="hello friend")
    app._ai.synthesize = AsyncMock(return_value=b"RIFFfake")

    asyncio.run(app._process())

    assert app.heard_text == "hola amigo"
    assert app.heard_lang == "es"
    # es == default target -> translated back to English
    assert app.translated_lang == "en"
    assert app.translated_text == "hello friend"
    ctx.audio.play_wav_bytes.assert_called_once_with(b"RIFFfake")
    assert app.state == TRState.READY


def test_process_english_goes_to_target():
    ctx = _ctx()
    app = _app(ctx)
    app._pcm = [b"\x00\x01"]
    app.state = TRState.PROCESSING
    app._ai = MagicMock()
    app._ai.transcribe = AsyncMock(return_value=Transcript(text="hello", language="en"))
    app._ai.chat = AsyncMock(return_value="hola")
    app._ai.synthesize = AsyncMock(return_value=b"RIFFx")

    asyncio.run(app._process())

    assert app.translated_lang == app.target["code"]
    # the TTS voice used must be the target's
    called_model = app._ai.synthesize.await_args.args[1]
    assert called_model == app.target["tts_model"]


def test_process_offline_flashes_and_recovers():
    app = _app()
    app._pcm = [b"\x00\x01"]
    app.state = TRState.PROCESSING
    app._ai = MagicMock()
    app._ai.transcribe = AsyncMock(return_value=None)

    asyncio.run(app._process())

    assert app.state == TRState.READY
    assert app.offline_flash is True
    assert app.status_msg == "Server unreachable"


def test_double_replays_last_audio():
    ctx = _ctx()
    app = _app(ctx)
    app._last_tts = b"RIFFlast"
    app.on_button("double")
    ctx.audio.play_wav_bytes.assert_called_once_with(b"RIFFlast")


def test_exit_mid_process_is_safe():
    app = _app()
    app._pcm = [b"\x00\x01"]
    app.state = TRState.PROCESSING
    ai = MagicMock()

    async def transcribe_then_exit(*a, **k):
        app.on_exit()  # user backed out mid-request
        return Transcript(text="hola", language="es")

    ai.transcribe = AsyncMock(side_effect=transcribe_then_exit)
    app._ai = ai
    asyncio.run(app._process())  # must not raise
