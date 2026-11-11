import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from apps.voice_companion import VoiceCompanionApp, VCState


def test_initial_state_is_idle():
    app = VoiceCompanionApp()
    assert app.state == VCState.IDLE


def test_start_listening_from_idle():
    app = VoiceCompanionApp()
    app._transition(VCState.LISTENING)
    assert app.state == VCState.LISTENING


def test_transition_to_thinking():
    app = VoiceCompanionApp()
    app._transition(VCState.LISTENING)
    app._transition(VCState.THINKING)
    assert app.state == VCState.THINKING


def test_transition_to_speaking():
    app = VoiceCompanionApp()
    app._transition(VCState.SPEAKING)
    assert app.state == VCState.SPEAKING


def test_back_to_idle():
    app = VoiceCompanionApp()
    app._transition(VCState.SPEAKING)
    app._transition(VCState.IDLE)
    assert app.state == VCState.IDLE


def test_stt_text_stored():
    app = VoiceCompanionApp()
    app._on_stt("hello world")
    assert app.last_stt == "hello world"


def test_tts_text_stored():
    app = VoiceCompanionApp()
    app._on_tts_sentence("Sure, I can help.")
    assert app.last_tts == "Sure, I can help."


def test_wakeword_enabled():
    app = VoiceCompanionApp()
    assert app.uses_wakeword is True


from core.face import Expression

def test_double_tap_toggles_view():
    app = VoiceCompanionApp()
    assert app.view == "face"          # default
    app.on_button("double")
    assert app.view == "transcript"
    app.on_button("double")
    assert app.view == "face"

def test_uses_double_enabled():
    assert VoiceCompanionApp.uses_double is True

def test_state_maps_to_expression():
    app = VoiceCompanionApp()
    assert app._expression_for(VCState.IDLE) == Expression.IDLE
    assert app._expression_for(VCState.LISTENING) == Expression.LISTENING
    assert app._expression_for(VCState.THINKING) == Expression.THINKING
    assert app._expression_for(VCState.SPEAKING) == Expression.SPEAKING


# ---------------------------------------------------------------------------
# Task 4: listen mode + wake word arming tests
# ---------------------------------------------------------------------------

class FakeSettings:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def _ctx(listen_mode_cfg="auto", settings=None):
    ctx = MagicMock()
    ctx.config = {"voice": {"listen_mode": listen_mode_cfg}}
    ctx.settings = settings
    ctx.xiaozhi.connected = True
    ctx.xiaozhi.server_sample_rate = 24000
    ctx.xiaozhi.start_listening = AsyncMock()
    ctx.xiaozhi.stop_listening = AsyncMock()
    ctx.wakeword.is_enabled = True
    ctx.wakeword.is_active = True
    ctx.audio.is_capturing = False
    ctx.audio.is_raw_capturing = False
    ctx.audio.level = 0.0
    return ctx


def test_listen_mode_defaults_to_auto():
    app = VoiceCompanionApp()
    ctx = _ctx()
    ctx.config = {}  # no voice block at all -> safe default
    app.on_enter(ctx)
    assert app.listen_mode == "auto"


def test_listen_mode_settings_overlay_wins_over_config():
    app = VoiceCompanionApp()
    ctx = _ctx(listen_mode_cfg="auto",
               settings=FakeSettings({"listen_mode": "manual"}))
    app.on_enter(ctx)
    assert app.listen_mode == "manual"


def test_on_enter_arms_wakeword_raw_capture():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    ctx.wakeword.start.assert_called_once()
    ctx.audio.start_raw_capture.assert_called_once_with(
        ctx.wakeword.process_audio)


def test_on_enter_respects_wakeword_disabled_in_settings():
    app = VoiceCompanionApp()
    ctx = _ctx(settings=FakeSettings({"wakeword_enabled": False}))
    app.on_enter(ctx)
    ctx.wakeword.start.assert_not_called()
    ctx.audio.start_raw_capture.assert_not_called()


def test_start_listening_stops_raw_capture_and_pauses_wakeword():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    ctx.audio.stop_capture.reset_mock()

    asyncio.run(app._start_listening())

    assert app.state == VCState.LISTENING
    ctx.audio.stop_capture.assert_called_once()   # closes the raw stream
    ctx.wakeword.pause.assert_called_once()
    ctx.audio.start_capture.assert_called_once()  # opus capture


def test_wakeword_detection_opens_mic_without_detect_text():
    # Regression: sending listen detect text="hey stackchan" makes the
    # upstream server feed the phrase to the LLM ("hey there!" replies).
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)

    asyncio.run(app._on_wakeword())

    assert app.state == VCState.LISTENING
    ctx.xiaozhi.start_listening.assert_awaited_once_with(mode="auto", wake_text="")


def test_tts_stop_manual_goes_idle_and_rearms():
    app = VoiceCompanionApp()
    ctx = _ctx(settings=FakeSettings({"listen_mode": "manual"}))
    app.on_enter(ctx)
    app._transition(VCState.SPEAKING)
    ctx.audio.start_raw_capture.reset_mock()
    ctx.wakeword.resume.reset_mock()

    asyncio.run(app._handle_server_json({"type": "tts", "state": "stop"}))

    assert app.state == VCState.IDLE
    ctx.wakeword.resume.assert_called_once()
    ctx.audio.start_raw_capture.assert_called_once()
    ctx.xiaozhi.start_listening.assert_not_awaited()
    ctx.audio.start_capture.assert_not_called()


def test_tts_stop_auto_relistens_and_keeps_wakeword_paused():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    app._transition(VCState.SPEAKING)
    ctx.wakeword.resume.reset_mock()
    ctx.audio.start_raw_capture.reset_mock()

    asyncio.run(app._handle_server_json({"type": "tts", "state": "stop"}))

    assert app.state == VCState.LISTENING
    ctx.audio.start_capture.assert_called_once()
    ctx.xiaozhi.start_listening.assert_awaited_once()
    # Wake word must NOT wake up mid-conversation (speaker/mic conflict).
    ctx.wakeword.resume.assert_not_called()
    ctx.audio.start_raw_capture.assert_not_called()


def test_silence_timeout_goes_idle_and_rearms():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    app._transition(VCState.LISTENING)
    ctx.audio.is_capturing = False
    ctx.audio.start_raw_capture.reset_mock()

    asyncio.run(app.update(6.0))  # > IDLE_TIMEOUT (5s)

    assert app.state == VCState.IDLE
    ctx.audio.start_raw_capture.assert_called_once()


def test_arm_skips_raw_capture_when_already_running():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    ctx.audio.is_raw_capturing = True
    ctx.audio.start_raw_capture.reset_mock()

    app._arm_wakeword()

    ctx.audio.start_raw_capture.assert_not_called()


def test_arm_skips_raw_capture_when_wakeword_inactive():
    app = VoiceCompanionApp()
    ctx = _ctx()
    ctx.wakeword.is_active = False  # model failed to load / lib missing
    app.on_enter(ctx)
    ctx.audio.start_raw_capture.assert_not_called()


def test_secondary_long_toggle_off_stops_raw_capture():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    ctx.audio.is_raw_capturing = True
    ctx.wakeword.toggle.return_value = False

    app.on_secondary("long")

    ctx.audio.stop_capture.assert_called()


def test_secondary_long_toggle_on_rearms_when_idle():
    app = VoiceCompanionApp()
    ctx = _ctx(settings=FakeSettings({"wakeword_enabled": False}))
    app.on_enter(ctx)  # wakeword not started
    ctx.wakeword.toggle.return_value = True
    ctx.wakeword.is_active = False  # never started

    app.on_secondary("long")

    ctx.wakeword.start.assert_called_once()  # late start with proper coroutine


def test_on_exit_unregisters_xiaozhi_handlers():
    # Regression: a late server reply (e.g. slow LLM) after app exit hit the
    # stale handler with ctx=None -> AttributeError inside the client's
    # receive loop -> spurious reconnect churn.
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    ctx.xiaozhi.attach.assert_called_once_with(
        on_json=app._handle_server_json, on_audio=app._handle_server_audio)

    app.on_exit()

    ctx.xiaozhi.detach.assert_called_once()


def test_handlers_attached_even_while_disconnected():
    # Entering the app while the server is down must still register the
    # handlers — the reconnect supervisor brings the session back and the
    # app has to work without being re-entered.
    app = VoiceCompanionApp()
    ctx = _ctx()
    ctx.xiaozhi.connected = False
    app.on_enter(ctx)
    ctx.xiaozhi.attach.assert_called_once()


def test_start_listening_offline_flashes_notice():
    app = VoiceCompanionApp()
    ctx = _ctx()
    ctx.xiaozhi.connected = False
    app.on_enter(ctx)

    asyncio.run(app._start_listening())

    assert app.state == VCState.IDLE       # no transition without a server
    assert app.offline_flash is True
    ctx.audio.start_capture.assert_not_called()


def test_silence_autostop_closes_open_mic():
    # Mic open, nobody speaks, server VAD never replies -> the device-side
    # silence auto-stop must close the session instead of listening forever.
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    app._transition(VCState.LISTENING)
    ctx.audio.is_capturing = True
    ctx.audio.level = 0.0  # below SILENCE_LEVEL
    ctx.audio.start_raw_capture.reset_mock()

    asyncio.run(app.update(10.0))  # > MIN_LISTEN_SEC + SILENCE_STOP_SEC

    assert app.state == VCState.IDLE
    ctx.xiaozhi.stop_listening.assert_awaited_once()
    ctx.audio.start_raw_capture.assert_called_once()  # wake word rearmed


def test_no_autostop_while_user_is_talking():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    app._transition(VCState.LISTENING)
    ctx.audio.is_capturing = True
    ctx.audio.level = 0.5  # loud

    asyncio.run(app.update(10.0))

    assert app.state == VCState.LISTENING
    ctx.xiaozhi.stop_listening.assert_not_awaited()


def test_handler_tolerates_late_message_after_exit():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    app.on_exit()  # ctx is now None

    # Even if a stale reference survives somewhere, the handler must not raise.
    asyncio.run(app._handle_server_json({"type": "tts", "state": "stop"}))
    asyncio.run(app._handle_server_audio(b"\x00"))


def test_keep_awake_only_during_conversation():
    app = VoiceCompanionApp()
    assert app.keep_awake is False           # IDLE -> screensaver allowed
    app.state = VCState.LISTENING
    assert app.keep_awake is True
    app.state = VCState.THINKING
    assert app.keep_awake is True
    app.state = VCState.SPEAKING
    assert app.keep_awake is True
    app.state = VCState.IDLE
    assert app.keep_awake is False


def test_led_follows_state_transitions():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    ctx.display.set_led.assert_called_with(255, 255, 255)  # idle = white in-app
    app._transition(VCState.LISTENING)
    ctx.display.set_led.assert_called_with(0, 255, 0)
    app._transition(VCState.THINKING)
    ctx.display.set_led.assert_called_with(255, 255, 0)
    app._transition(VCState.SPEAKING)
    ctx.display.set_led.assert_called_with(0, 0, 255)


def test_led_off_on_exit():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)
    app.on_exit()
    ctx.display.set_led.assert_called_with(0, 0, 0)


def test_wakeword_detection_plays_chime():
    app = VoiceCompanionApp()
    ctx = _ctx()
    app.on_enter(ctx)

    asyncio.run(app._on_wakeword())

    ctx.audio.play_sound_file.assert_called_once_with(
        "assets/sounds/wake_word_triggered.wav")


def test_wakeword_config_disabled_respected_without_settings_store():
    app = VoiceCompanionApp()
    ctx = _ctx()
    ctx.settings = None
    ctx.config = {"wakeword": {"enabled": False}}
    app.on_enter(ctx)
    ctx.wakeword.start.assert_not_called()
    ctx.audio.start_raw_capture.assert_not_called()
