"""core/aiservices.py: WAV packing, chat payload rules, config defaults."""

import io
import wave

from core.aiservices import (
    AIServices, pcm_to_wav_bytes, build_chat_payload, build_openai_payload,
    extract_chat_content,
    DEFAULT_SPEACHES_URL, DEFAULT_FAST_MODEL, DEFAULT_DEEP_MODEL,
    DEFAULT_DEEP_URL,
)

MSGS = [{"role": "user", "content": "hi"}]


def test_pcm_to_wav_bytes_roundtrip():
    pcm = [b"\x00\x01" * 480, b"\x02\x03" * 480]
    data = pcm_to_wav_bytes(pcm, rate=16000)
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == 960


def test_chat_payload_think_disabled_for_thinking_families():
    p = build_chat_payload("gemma4:e2b-it-qat", [{"role": "user", "content": "x"}])
    assert p["think"] is False
    p = build_chat_payload("qwen3.6:35b-a3b", [])
    assert p["think"] is False


def test_chat_payload_no_think_for_other_models():
    # gpt-oss and friends error on the think param — must be absent.
    p = build_chat_payload("gpt-oss:20b", [])
    assert "think" not in p


def test_chat_payload_basics():
    msgs = [{"role": "user", "content": "hi"}]
    p = build_chat_payload("gemma4:e2b-it-qat", msgs, num_ctx=4096)
    assert p["stream"] is False
    assert p["messages"] is msgs
    assert p["options"]["num_ctx"] == 4096


def test_config_defaults():
    ai = AIServices({})
    assert ai.speaches_url == DEFAULT_SPEACHES_URL
    assert ai.fast_model == DEFAULT_FAST_MODEL
    assert ai.deep_model == DEFAULT_DEEP_MODEL


def test_config_overrides():
    ai = AIServices({"ai_services": {
        "speaches_url": "http://other:9000/",
        "fast_model": "gemma4:e4b",
        "deep_model": "qwen9:1t",
    }})
    assert ai.speaches_url == "http://other:9000"  # trailing slash stripped
    assert ai.fast_model == "gemma4:e4b"
    assert ai.deep_model == "qwen9:1t"


def test_chat_payload_omits_num_ctx_when_none():
    p = build_chat_payload("gemma4:e4b-it-qat", MSGS)
    assert "options" not in p


def test_fast_chat_uses_utility_runner_not_voice_runner():
    # Translator rides the 5060 Ti utility runner (:11434, e2b @ 8k, shared
    # with other apps) — never the voice runner (:11435). A ctx mismatch reloads
    # the runner, so fast always sends the runner's 8192 regardless of caller.
    ai = AIServices({})
    model, url, payload = ai._chat_request(MSGS, deep=False, num_ctx=16384)
    assert model == DEFAULT_FAST_MODEL == "gemma4:e2b-it-qat"
    assert url.endswith(":11434/api/chat")
    assert ":11435" not in url
    assert payload["options"]["num_ctx"] == 8192


def test_deep_defaults_to_llm_server_openai():
    ai = AIServices({})
    assert ai.deep_api == "openai"
    model, url, payload = ai._chat_request(MSGS, deep=True, num_ctx=16384)
    assert model == DEFAULT_DEEP_MODEL == "qwen3.8-27b"
    assert url == f"{DEFAULT_DEEP_URL}/chat/completions"
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "options" not in payload  # vLLM ctx is server-side


def test_legacy_ollama_deep_url_keeps_ollama_api():
    ai = AIServices({"ai_services": {"ollama_deep_url": "http://old:11434/"}})
    assert ai.deep_api == "ollama"
    _, url, payload = ai._chat_request(MSGS, deep=True, num_ctx=16384)
    assert url == "http://old:11434/api/chat"
    assert payload["options"]["num_ctx"] == 16384


def test_deep_url_wins_over_legacy_key():
    ai = AIServices({"ai_services": {"deep_url": "http://cx:8000/v1/",
                                     "ollama_deep_url": "http://old:11434"}})
    assert ai.deep_url == "http://cx:8000/v1"
    assert ai.deep_api == "openai"


def test_openai_payload_no_thinking_kwarg_for_non_qwen():
    p = build_openai_payload("llama-3", MSGS)
    assert "chat_template_kwargs" not in p
    assert p["stream"] is False and p["max_tokens"] > 0


def test_extract_chat_content_both_shapes():
    assert extract_chat_content({"message": {"content": " hi "}}) == "hi"
    assert extract_chat_content(
        {"choices": [{"message": {"content": "yo\n"}}]}) == "yo"
    assert extract_chat_content({"choices": []}) is None
    assert extract_chat_content({}) is None
