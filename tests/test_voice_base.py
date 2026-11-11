"""VoiceAppBase protocol rules, exercised through HAControllerApp — the app
that historically broke them (stale handlers, no ctx guards, post-tool audio
gated on app state).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from apps.ha_controller import HAControllerApp, HAState


def _ctx(connected=True):
    ctx = MagicMock()
    ctx.config = {"ha_controller": {"entities": []}}
    ctx.xiaozhi.connected = connected
    ctx.xiaozhi.server_sample_rate = 24000
    ctx.xiaozhi.start_listening = AsyncMock()
    ctx.xiaozhi.stop_listening = AsyncMock()
    ctx.xiaozhi.send_json = AsyncMock()
    ctx.audio.enqueue_opus = AsyncMock()
    ctx.audio.is_playing = False
    ctx.audio.is_capturing = False
    ctx.audio.is_raw_capturing = False
    ctx.wakeword.is_enabled = True
    ctx.wakeword.is_active = True
    return ctx


def _enter(app, ctx):
    async def go():
        app.on_enter(ctx)
        await asyncio.sleep(0)  # let spawned tasks (entity poll) run
    asyncio.run(go())


def test_on_enter_attaches_and_on_exit_detaches():
    app = HAControllerApp()
    ctx = _ctx()
    _enter(app, ctx)
    ctx.xiaozhi.attach.assert_called_once_with(
        on_json=app._handle_server_json, on_audio=app._handle_server_audio)

    app.on_exit()

    ctx.xiaozhi.detach.assert_called_once()


def test_on_enter_arms_wakeword_raw_capture():
    # The old app started the wake word service but never fed it the mic —
    # detection could never fire.
    app = HAControllerApp()
    ctx = _ctx()
    _enter(app, ctx)
    ctx.wakeword.start.assert_called_once()
    ctx.audio.start_raw_capture.assert_called_once_with(
        ctx.wakeword.process_audio)


def test_late_messages_after_exit_do_not_raise():
    # Regression class: late LLM/TTS reply after exit hit handlers that
    # dereferenced self.ctx.audio on None and crashed the receive loop.
    app = HAControllerApp()
    ctx = _ctx()
    _enter(app, ctx)
    app.on_exit()

    asyncio.run(app._handle_server_json({"type": "tts", "state": "stop"}))
    asyncio.run(app._handle_server_json({"type": "stt", "text": "late"}))
    asyncio.run(app._handle_server_audio(b"\x00"))


def test_reply_audio_plays_while_stream_open_regardless_of_state():
    # Post-tool fix (commit 6cd2514): a mid-reply stt used to knock the app
    # out of its speaking state and every subsequent audio frame was dropped.
    # The only valid gate is audio.is_playing.
    app = HAControllerApp()
    ctx = _ctx()
    _enter(app, ctx)
    app.state = HAState.IDLE          # wrong state on purpose
    ctx.audio.is_playing = True

    asyncio.run(app._handle_server_audio(b"\x01\x02"))

    ctx.audio.enqueue_opus.assert_awaited_once_with(b"\x01\x02")


def test_audio_dropped_when_stream_closed():
    app = HAControllerApp()
    ctx = _ctx()
    _enter(app, ctx)
    app.state = HAState.EXECUTING
    ctx.audio.is_playing = False

    asyncio.run(app._handle_server_audio(b"\x01\x02"))

    ctx.audio.enqueue_opus.assert_not_awaited()


def test_mid_reply_stt_keeps_executing_state():
    app = HAControllerApp()
    ctx = _ctx()
    _enter(app, ctx)
    app.state = HAState.EXECUTING
    ctx.audio.is_playing = True

    asyncio.run(app._handle_server_json({"type": "stt", "text": "turn on lamp"}))

    assert app.state == HAState.EXECUTING     # not reset — audio keeps playing
    assert app.last_command == "turn on lamp"
    ctx.audio.stop_capture.assert_not_called()


def test_tts_stop_goes_idle_and_rearms_wakeword():
    app = HAControllerApp()
    ctx = _ctx()
    _enter(app, ctx)
    app.state = HAState.EXECUTING
    ctx.audio.start_raw_capture.reset_mock()
    ctx.audio.is_raw_capturing = False

    asyncio.run(app._handle_server_json({"type": "tts", "state": "stop"}))

    assert app.state == HAState.IDLE
    ctx.audio.stop_playback.assert_called_once()
    ctx.wakeword.resume.assert_called()
    ctx.audio.start_raw_capture.assert_called_once()


def test_wakeword_never_sends_detect_text():
    # detect-text goes to the LLM upstream ("hey there!" replies) — the HA
    # app used to send wake_text="hey stackchan".
    app = HAControllerApp()
    ctx = _ctx()
    _enter(app, ctx)

    asyncio.run(app._on_wakeword())

    assert app.state == HAState.LISTENING
    ctx.xiaozhi.start_listening.assert_awaited_once_with(mode="auto", wake_text="")


def test_listen_while_offline_flashes_notice():
    app = HAControllerApp()
    ctx = _ctx(connected=False)
    _enter(app, ctx)

    asyncio.run(app._start_listening())

    assert app.state == HAState.IDLE
    assert app.offline_flash is True
    ctx.audio.start_capture.assert_not_called()
