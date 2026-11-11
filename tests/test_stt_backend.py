import asyncio

from core import aiservices
from core.aiservices import AIServices, Transcript, lang_code


def test_lang_code_maps_whispercpp_names():
    assert lang_code("spanish") == "es"
    assert lang_code("English") == "en"
    assert lang_code("es") == "es"
    assert lang_code(None) == ""


def test_gpu_stt_first_then_speaches_fallback(monkeypatch):
    ai = AIServices({})
    assert ai.stt_backend == "whispercpp"
    calls = []

    async def gpu_down(wav, lang):
        calls.append("gpu")
        return None

    async def cpu(wav, lang):
        calls.append("cpu")
        return Transcript(text="hola", language="es")

    monkeypatch.setattr(ai, "_transcribe_whispercpp", gpu_down)
    monkeypatch.setattr(ai, "_transcribe_speaches", cpu)
    out = asyncio.run(ai.transcribe(b"wav"))
    assert calls == ["gpu", "cpu"] and out.language == "es"


def test_speaches_only_backend(monkeypatch):
    ai = AIServices({"ai_services": {"stt_backend": "speaches"}})
    async def boom(*a):
        raise AssertionError("GPU path must not run")
    async def cpu(wav, lang):
        return Transcript(text="hi", language="en")
    monkeypatch.setattr(ai, "_transcribe_whispercpp", boom)
    monkeypatch.setattr(ai, "_transcribe_speaches", cpu)
    assert asyncio.run(ai.transcribe(b"wav")).text == "hi"
