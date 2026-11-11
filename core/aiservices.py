"""Direct HTTP clients for the non-conversation AI pipeline.

The xiaozhi protocol has no "transcribe only" + "speak arbitrary text"
combination, so the Translator and Meeting apps bypass it and talk straight
to the services already running on the AI server:

- speaches (:8001)   — STT via faster-whisper (multilingual, per-utterance
                       language auto-detect) and TTS via Piper voices.
- Ollama fast (:11434) — gemma4:e2b on the AI server's RTX 5060 Ti: the warm UTILITY
                       runner (pinned, 8k ctx) for
                       cleanup; translation-class prompts. Kept OFF the voice
                       runner (:11435) so the Translator never disturbs
                       xiaozhi / HA Assist. Must send the runner's num_ctx
                       (8192) — a different ctx reloads the model.
- Deep (vLLM :8000/v1) — qwen3.8-27b on the LLM server, OpenAI API;
                       summarization-class prompts. (Legacy: an Ollama deep
                       endpoint still works via deep_api: ollama.)

Point these at tailnet addresses (config.yaml -> ai_services) so they work
both at home and when roaming over a phone hotspot.

Every call is best-effort: failures log and return None — callers show
"offline" UX, never crash.
"""

from __future__ import annotations

import io
import logging
import os
import wave
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

AI_SERVER = os.environ.get("PUCK_AI_SERVER", "ai-server")      # set real URLs in config.yaml -> ai_services
LLM_SERVER = os.environ.get("PUCK_AI_LLM_SERVER", AI_SERVER)  # big-model box (vLLM / OpenAI API)

DEFAULT_SPEACHES_URL = f"http://{AI_SERVER}:8001"
DEFAULT_OLLAMA_FAST_URL = f"http://{AI_SERVER}:11434"
DEFAULT_DEEP_URL = f"http://{LLM_SERVER}:8000/v1"
DEFAULT_DEEP_API = "openai"   # "openai" (vLLM /v1/chat/completions) | "ollama"
DEFAULT_STT_MODEL = "Systran/faster-whisper-small"
DEFAULT_FAST_MODEL = "gemma4:e2b-it-qat"
DEFAULT_FAST_NUM_CTX = 8192    # = the utility runner's server default (OLLAMA_CONTEXT_LENGTH)
DEFAULT_DEEP_MODEL = "qwen3.8-27b"
DEEP_MAX_TOKENS = 2048

DEFAULT_DIARIZE_URL = f"http://{AI_SERVER}:8895"  # sherpa-onnx diarization (AI server CPU)
DEFAULT_STT_BACKEND = "whispercpp"   # "whispercpp" (GPU, falls back to speaches) | "speaches"
DEFAULT_WHISPERCPP_URL = f"http://{AI_SERVER}:8890"  # whisper-flow, 5060 Ti

STT_TIMEOUT = 90       # long meeting chunks on CPU whisper
DIARIZE_TIMEOUT = 1800  # whole-meeting upload + diarization (3 h cap)
PDF_TIMEOUT = 90        # report HTML -> PDF on the AI server
TTS_TIMEOUT = 30
CHAT_TIMEOUT = 60
DEEP_CHAT_TIMEOUT = 300  # summarizing a long transcript

# Thinking-capable families take the native `think` param; others error on it
# (same whitelist as the AI server ollama patch).
_THINK_FAMILIES = ("qwen3", "gemma4")


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    text: str
    language: str = ""
    duration: float = 0.0
    # whisper's own sentence-ish segments (seconds relative to the clip);
    # the Meeting app uses them to line the transcript up with speakers.
    segments: list[Segment] = field(default_factory=list)


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: int  # 0-based, numbered in order of first appearance


def parse_segments(data: dict) -> list[Segment]:
    out = []
    for s in data.get("segments") or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        try:
            out.append(Segment(float(s["start"]), float(s["end"]), text))
        except (KeyError, TypeError, ValueError):
            continue
    return out


# whisper.cpp reports the detected language by name ("spanish"); the rest of
# the app uses ISO-639-1 codes like speaches does.
_LANG_CODES = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de",
    "italian": "it", "portuguese": "pt", "dutch": "nl", "russian": "ru",
    "japanese": "ja", "korean": "ko", "chinese": "zh", "arabic": "ar",
    "hindi": "hi", "polish": "pl", "turkish": "tr", "swedish": "sv",
    "ukrainian": "uk", "greek": "el", "czech": "cs", "vietnamese": "vi",
}


def lang_code(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if len(v) <= 3:
        return v
    return _LANG_CODES.get(v, v[:2])


def pcm_to_wav_bytes(pcm_chunks: list[bytes], rate: int = 16000) -> bytes:
    """16-bit mono PCM chunks -> in-memory WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(pcm_chunks))
    return buf.getvalue()


def build_chat_payload(model: str, messages: list[dict],
                       num_ctx: Optional[int] = None) -> dict:
    """Ollama native /api/chat payload. num_ctx=None = server default."""
    payload = {
        "model": model,
        "stream": False,
        "messages": messages,
    }
    if num_ctx:
        payload["options"] = {"num_ctx": num_ctx}
    if model.lower().startswith(_THINK_FAMILIES):
        payload["think"] = False
    return payload


def build_openai_payload(model: str, messages: list[dict],
                         max_tokens: int = DEEP_MAX_TOKENS) -> dict:
    """OpenAI-compatible /chat/completions payload (vLLM). Thinking off for
    qwen3-family chat templates — summaries want the answer, not the trace."""
    payload = {
        "model": model,
        "stream": False,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if model.lower().startswith("qwen3"):
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def extract_chat_content(data: dict) -> Optional[str]:
    """Reply text from either an Ollama or an OpenAI-style response."""
    if "choices" in data:
        choices = data.get("choices") or [{}]
        content = (choices[0].get("message") or {}).get("content") or ""
    else:
        content = (data.get("message") or {}).get("content") or ""
    return content.strip() or None


class AIServices:
    def __init__(self, config: dict):
        ai_cfg = config.get("ai_services", {})
        self.speaches_url = ai_cfg.get("speaches_url", DEFAULT_SPEACHES_URL).rstrip("/")
        self.ollama_fast_url = ai_cfg.get("ollama_fast_url", DEFAULT_OLLAMA_FAST_URL).rstrip("/")
        # deep_url/deep_api are current; a lone legacy ollama_deep_url keeps
        # the old Ollama behaviour so an un-migrated device config still works.
        if "deep_url" in ai_cfg:
            self.deep_url = ai_cfg["deep_url"].rstrip("/")
            self.deep_api = ai_cfg.get("deep_api", DEFAULT_DEEP_API)
        elif "ollama_deep_url" in ai_cfg:
            self.deep_url = ai_cfg["ollama_deep_url"].rstrip("/")
            self.deep_api = ai_cfg.get("deep_api", "ollama")
        else:
            self.deep_url = DEFAULT_DEEP_URL
            self.deep_api = ai_cfg.get("deep_api", DEFAULT_DEEP_API)
        self.stt_model = ai_cfg.get("stt_model", DEFAULT_STT_MODEL)
        self.fast_model = ai_cfg.get("fast_model", DEFAULT_FAST_MODEL)
        self.fast_num_ctx = ai_cfg.get("fast_num_ctx", DEFAULT_FAST_NUM_CTX)
        self.deep_model = ai_cfg.get("deep_model", DEFAULT_DEEP_MODEL)
        self.diarize_url = ai_cfg.get("diarize_url", DEFAULT_DIARIZE_URL).rstrip("/")
        self.stt_backend = ai_cfg.get("stt_backend", DEFAULT_STT_BACKEND)
        self.whispercpp_url = ai_cfg.get("whispercpp_url", DEFAULT_WHISPERCPP_URL).rstrip("/")

    async def diarize(self, wav_path: str,
                      speakers: int = 0) -> Optional[list[SpeakerTurn]]:
        """Whole-meeting WAV file -> speaker turns ("who spoke when").

        Streams the file from disk (a long meeting is hundreds of MB, more
        than the Pi Zero's RAM). speakers=0 lets the server guess the count.
        """
        url = f"{self.diarize_url}/diarize"
        if speakers > 0:
            url += f"?speakers={int(speakers)}"
        try:
            timeout = aiohttp.ClientTimeout(total=DIARIZE_TIMEOUT)
            size = os.path.getsize(wav_path)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                with open(wav_path, "rb") as f:
                    async with session.post(
                            url, data=f,
                            headers={"Content-Type": "audio/wav",
                                     "Content-Length": str(size)}) as resp:
                        if resp.status != 200:
                            log.warning("Diarize failed: HTTP %d %s", resp.status,
                                        (await resp.text())[:200])
                            return None
                        data = await resp.json()
            return [SpeakerTurn(float(s["start"]), float(s["end"]), int(s["speaker"]))
                    for s in data.get("segments", [])]
        except Exception as e:
            log.warning("Diarize request failed: %s", e)
            return None

    async def render_pdf(self, html: str) -> Optional[bytes]:
        """Self-contained report HTML -> PDF bytes, rendered on the AI server
        (WeasyPrint next to the diarizer; far too heavy for a Pi Zero)."""
        try:
            timeout = aiohttp.ClientTimeout(total=PDF_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                        f"{self.diarize_url}/pdf", data=html.encode("utf-8"),
                        headers={"Content-Type": "text/html; charset=utf-8"}) as resp:
                    if resp.status != 200:
                        log.warning("PDF render failed: HTTP %d %s", resp.status,
                                    (await resp.text())[:200])
                        return None
                    data = await resp.read()
            return data if data.startswith(b"%PDF") else None
        except Exception as e:
            log.warning("PDF render request failed: %s", e)
            return None

    async def transcribe(self, wav_bytes: bytes,
                         language: str = "") -> Optional[Transcript]:
        """WAV -> Transcript with detected language ('' language = auto).

        GPU first (whisper.cpp large-v3-turbo on the AI server's 5060 Ti, ~0.4 s for
        a short utterance), CPU speaches faster-whisper as the fallback.
        """
        if self.stt_backend == "whispercpp":
            result = await self._transcribe_whispercpp(wav_bytes, language)
            if result is not None:
                return result
            log.warning("GPU STT unavailable, falling back to speaches")
        return await self._transcribe_speaches(wav_bytes, language)

    async def _transcribe_whispercpp(self, wav_bytes: bytes,
                                     language: str) -> Optional[Transcript]:
        """whisper.cpp server /inference. The shared whisper-flow instance is
        English-pinned by default; language=auto per
        request detects the language for this call only."""
        try:
            form = aiohttp.FormData()
            form.add_field("file", wav_bytes, filename="audio.wav",
                           content_type="audio/wav")
            form.add_field("language", language or "auto")
            form.add_field("response_format", "verbose_json")
            timeout = aiohttp.ClientTimeout(total=STT_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.whispercpp_url}/inference",
                                        data=form) as resp:
                    if resp.status != 200:
                        log.warning("GPU STT failed: HTTP %d %s", resp.status,
                                    (await resp.text())[:200])
                        return None
                    data = await resp.json(content_type=None)
            return Transcript(
                text=(data.get("text") or "").strip(),
                language=lang_code(data.get("detected_language")
                                   or data.get("language") or language),
                duration=float(data.get("duration") or 0.0),
                segments=parse_segments(data),
            )
        except Exception as e:
            log.warning("GPU STT request failed: %r", e)
            return None

    async def _transcribe_speaches(self, wav_bytes: bytes,
                                   language: str) -> Optional[Transcript]:
        try:
            form = aiohttp.FormData()
            form.add_field("file", wav_bytes, filename="audio.wav",
                           content_type="audio/wav")
            form.add_field("model", self.stt_model)
            form.add_field("response_format", "verbose_json")
            if language:
                form.add_field("language", language)
            timeout = aiohttp.ClientTimeout(total=STT_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                        f"{self.speaches_url}/v1/audio/transcriptions",
                        data=form) as resp:
                    if resp.status != 200:
                        log.warning("STT failed: HTTP %d %s", resp.status,
                                    (await resp.text())[:200])
                        return None
                    data = await resp.json()
            return Transcript(
                text=(data.get("text") or "").strip(),
                language=data.get("language", ""),
                duration=float(data.get("duration") or 0.0),
                segments=parse_segments(data),
            )
        except Exception as e:
            log.warning("STT request failed: %s", e)
            return None

    async def synthesize(self, text: str, tts_model: str,
                         voice: str) -> Optional[bytes]:
        """Text -> WAV bytes via a speaches Piper voice."""
        try:
            payload = {
                "model": tts_model,
                "voice": voice,
                "input": text,
                "response_format": "wav",
            }
            timeout = aiohttp.ClientTimeout(total=TTS_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                        f"{self.speaches_url}/v1/audio/speech",
                        json=payload) as resp:
                    if resp.status != 200:
                        log.warning("TTS failed: HTTP %d %s", resp.status,
                                    (await resp.text())[:200])
                        return None
                    return await resp.read()
        except Exception as e:
            log.warning("TTS request failed: %s", e)
            return None

    def _chat_request(self, messages: list[dict], deep: bool,
                      num_ctx: Optional[int]) -> tuple[str, str, dict]:
        """-> (model, url, payload) for one chat call.

        fast: Ollama on the shared utility runner, always fast_num_ctx (the
        caller's num_ctx is ignored so the runner shape never changes).
        deep: OpenAI-compatible (vLLM) by default; num_ctx only matters for
        the legacy Ollama deep path (vLLM's context is fixed server-side).
        """
        if not deep:
            return (self.fast_model, f"{self.ollama_fast_url}/api/chat",
                    build_chat_payload(self.fast_model, messages,
                                       num_ctx=self.fast_num_ctx))
        if self.deep_api == "ollama":
            return (self.deep_model, f"{self.deep_url}/api/chat",
                    build_chat_payload(self.deep_model, messages,
                                       num_ctx=num_ctx))
        return (self.deep_model, f"{self.deep_url}/chat/completions",
                build_openai_payload(self.deep_model, messages))

    async def chat(self, messages: list[dict], deep: bool = False,
                   num_ctx: Optional[int] = None) -> Optional[str]:
        """Chat -> reply content. deep=True routes to the summarizer."""
        model, url, payload = self._chat_request(messages, deep, num_ctx)
        try:
            timeout = aiohttp.ClientTimeout(
                total=DEEP_CHAT_TIMEOUT if deep else CHAT_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        log.warning("Chat failed: HTTP %d %s", resp.status,
                                    (await resp.text())[:200])
                        return None
                    data = await resp.json()
            return extract_chat_content(data)
        except Exception as e:
            log.warning("Chat request failed (%s): %s", model, e)
            return None
