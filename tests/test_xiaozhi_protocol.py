import json
import pytest

from core.xiaozhi_client import (
    build_hello_message,
    build_listen_message,
    build_abort_message,
    parse_server_message,
    XiaozhiState,
)


def test_build_hello_message():
    msg = build_hello_message()
    assert msg["type"] == "hello"
    assert msg["version"] == 1
    assert msg["transport"] == "websocket"
    assert msg["audio_params"]["format"] == "opus"
    assert msg["audio_params"]["sample_rate"] == 16000
    assert msg["audio_params"]["channels"] == 1
    assert msg["audio_params"]["frame_duration"] == 60


def test_build_hello_with_mcp():
    msg = build_hello_message()
    assert msg["features"]["mcp"] is True


def test_build_listen_start():
    msg = build_listen_message("sess-123", "start", mode="auto")
    assert msg["session_id"] == "sess-123"
    assert msg["type"] == "listen"
    assert msg["state"] == "start"
    assert msg["mode"] == "auto"


def test_build_listen_detect():
    msg = build_listen_message("sess-123", "detect", text="hey stackchan")
    assert msg["state"] == "detect"
    assert msg["text"] == "hey stackchan"


def test_build_abort():
    msg = build_abort_message("sess-123", reason="wake_word_detected")
    assert msg["type"] == "abort"
    assert msg["reason"] == "wake_word_detected"
    assert msg["session_id"] == "sess-123"


def test_parse_server_hello():
    raw = json.dumps({
        "type": "hello",
        "session_id": "srv-456",
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 24000,
            "channels": 1,
            "frame_duration": 60,
        },
    })
    msg = parse_server_message(raw)
    assert msg["type"] == "hello"
    assert msg["session_id"] == "srv-456"
    assert msg["audio_params"]["sample_rate"] == 24000


def test_parse_stt_message():
    raw = json.dumps({
        "session_id": "srv-456",
        "type": "stt",
        "text": "turn on the lights",
    })
    msg = parse_server_message(raw)
    assert msg["type"] == "stt"
    assert msg["text"] == "turn on the lights"


def test_parse_tts_start():
    raw = json.dumps({
        "session_id": "srv-456",
        "type": "tts",
        "state": "start",
    })
    msg = parse_server_message(raw)
    assert msg["type"] == "tts"
    assert msg["state"] == "start"


def test_parse_tts_sentence():
    raw = json.dumps({
        "session_id": "srv-456",
        "type": "tts",
        "state": "sentence_start",
        "text": "Sure, turning on the lights.",
    })
    msg = parse_server_message(raw)
    assert msg["state"] == "sentence_start"
    assert msg["text"] == "Sure, turning on the lights."


def test_state_enum():
    assert XiaozhiState.IDLE.value == "idle"
    assert XiaozhiState.LISTENING.value == "listening"
    assert XiaozhiState.THINKING.value == "thinking"
    assert XiaozhiState.SPEAKING.value == "speaking"


# ---------------------------------------------------------------------------
# Public handler API: attach()/detach() replaces apps poking _on_json/_on_audio.
# ---------------------------------------------------------------------------

def test_attach_detach_handlers():
    from core.xiaozhi_client import XiaozhiClient

    client = XiaozhiClient({})

    async def on_json(msg):
        pass

    async def on_audio(data):
        pass

    client.attach(on_json=on_json, on_audio=on_audio)
    assert client._on_json is on_json
    assert client._on_audio is on_audio

    client.detach()
    assert client._on_json is None
    assert client._on_audio is None


def test_attach_json_only_clears_audio():
    from core.xiaozhi_client import XiaozhiClient

    client = XiaozhiClient({})

    async def on_json(msg):
        pass

    client.attach(on_json=on_json)  # note taker style: no audio handler
    assert client._on_json is on_json
    assert client._on_audio is None
