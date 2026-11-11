"""Two-way voice translator.

Bypasses xiaozhi entirely (the protocol can't speak arbitrary text): records
raw PCM, transcribes via speaches faster-whisper with per-utterance language
auto-detect, translates via gemma4:e2b on the AI server iGPU, synthesizes via a
per-language Piper voice, and plays the result.

Two-way logic with one button: speak the TARGET language and it comes back
in English; speak anything else (English included) and it comes back in the
target language. PiSugar long-press cycles the target language.

Buttons: single = start/stop listening, double = replay last translation,
PiSugar long = next target language.
"""

from __future__ import annotations

import enum
import logging
import time
from typing import TYPE_CHECKING, Optional

from apps.base import AppBase
from core.aiservices import AIServices, pcm_to_wav_bytes
from core.display import (
    SCREEN_WIDTH, SCREEN_HEIGHT, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT,
    COLOR_BG, COLOR_TEXT, COLOR_DIM, COLOR_HIGHLIGHT, COLOR_SUCCESS, COLOR_ERROR,
    CORNER_INSET,
)

if TYPE_CHECKING:
    from core.context import AppContext

log = logging.getLogger(__name__)

CONTENT_TOP = STATUS_BAR_HEIGHT
CONTENT_BOTTOM = SCREEN_HEIGHT - BOTTOM_BAR_HEIGHT
MAX_RECORD_SECS = 30.0  # bound STT latency; one utterance, not a lecture

# Piper voices pulled into the speaches cache on the AI server (2026-07-12).
DEFAULT_LANGUAGES = [
    {"code": "es", "name": "Spanish",
     "tts_model": "speaches-ai/piper-es_ES-davefx-medium", "voice": "davefx"},
    {"code": "fr", "name": "French",
     "tts_model": "speaches-ai/piper-fr_FR-siwis-medium", "voice": "siwis"},
    {"code": "de", "name": "German",
     "tts_model": "speaches-ai/piper-de_DE-thorsten-medium", "voice": "thorsten"},
    {"code": "it", "name": "Italian",
     "tts_model": "speaches-ai/piper-it_IT-paola-medium", "voice": "paola"},
    {"code": "pt", "name": "Portuguese",
     "tts_model": "speaches-ai/piper-pt_BR-faber-medium", "voice": "faber"},
]
DEFAULT_HOME = {"code": "en", "name": "English",
                "tts_model": "speaches-ai/piper-en_US-ryan-high", "voice": "ryan"}

# Display names for whisper's detected ISO codes beyond the configured set.
LANG_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "ru": "Russian", "uk": "Ukrainian", "zh": "Chinese", "ja": "Japanese",
    "ko": "Korean", "ar": "Arabic", "hi": "Hindi", "tr": "Turkish",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code or "?")


def translation_target(detected_code: str, target: dict, home: dict) -> dict:
    """Two-way direction: target-language speech comes back in the home
    language; everything else (home language included) goes to the target."""
    if detected_code == target["code"]:
        return home
    return target


def build_translate_messages(text: str, src_code: str, dst: dict) -> list[dict]:
    return [
        {"role": "system", "content": (
            f"You are a professional interpreter. Translate the user's "
            f"message from {lang_name(src_code)} to {dst['name']}. Output "
            f"ONLY the translation - no quotes, no notes, no explanations."
        )},
        {"role": "user", "content": text},
    ]


class TRState(enum.Enum):
    READY = "ready"
    RECORDING = "recording"
    PROCESSING = "processing"


class TranslatorApp(AppBase):
    name = "Translator"
    icon = "assets/icons/translate.png"
    uses_wakeword = False
    uses_double = True  # double-tap = replay last translation

    _LED = {
        TRState.READY: (0, 0, 0),
        TRState.RECORDING: (0, 255, 0),
        TRState.PROCESSING: (255, 255, 0),
    }

    def __init__(self):
        super().__init__()
        self.state = TRState.READY
        self.languages = list(DEFAULT_LANGUAGES)
        self.home = dict(DEFAULT_HOME)
        self.target_idx = 0
        self.heard_text: str = ""
        self.heard_lang: str = ""
        self.translated_text: str = ""
        self.translated_lang: str = ""
        self.status_msg: str = ""
        self._pcm: list[bytes] = []
        self._record_elapsed: float = 0.0
        self._last_tts: Optional[bytes] = None
        self._play_until: float = 0.0
        self._ai: Optional[AIServices] = None
        self._rate: int = 16000

    @property
    def target(self) -> dict:
        return self.languages[self.target_idx % len(self.languages)]

    @property
    def keep_awake(self) -> bool:
        return self.state != TRState.READY

    def _set_state(self, new_state: TRState):
        self.state = new_state
        if self.ctx:
            self.ctx.display.set_led(*self._LED[new_state])

    def on_enter(self, ctx: AppContext):
        super().on_enter(ctx)
        tr_cfg = ctx.config.get("translator", {})
        self.languages = tr_cfg.get("languages") or list(DEFAULT_LANGUAGES)
        self.home = tr_cfg.get("home") or dict(DEFAULT_HOME)
        self._rate = ctx.config.get("audio", {}).get("sample_rate_in", 16000)
        self._ai = AIServices(ctx.config)
        self._set_state(TRState.READY)
        self.status_msg = ""

    def on_exit(self):
        if self.ctx:
            self.ctx.audio.stop_all()
            self.ctx.display.set_led(0, 0, 0)
        self.state = TRState.READY
        super().on_exit()

    # --- recording ----------------------------------------------------------

    def _start_recording(self):
        if not self.ctx:
            return
        self._pcm = []
        self._record_elapsed = 0.0
        self.status_msg = ""
        self._set_state(TRState.RECORDING)
        self.ctx.audio.start_raw_capture(self._on_pcm)

    def _on_pcm(self, data: bytes):
        # PortAudio callback thread: append only (list.append is atomic).
        self._pcm.append(data)

    def _stop_recording(self):
        if not self.ctx:
            return
        self.ctx.audio.stop_capture()
        if not self._pcm:
            self._set_state(TRState.READY)
            return
        self._set_state(TRState.PROCESSING)
        self.spawn(self._process())

    # --- pipeline -----------------------------------------------------------

    async def _process(self):
        try:
            await self._process_inner()
        finally:
            if self.ctx and self.state == TRState.PROCESSING:
                self._set_state(TRState.READY)

    async def _process_inner(self):
        if not self.ctx or not self._ai:
            return
        wav = pcm_to_wav_bytes(self._pcm, self._rate)
        self._pcm = []

        transcript = await self._ai.transcribe(wav)
        if not self.ctx:
            return  # app exited mid-flight
        if transcript is None:
            self.status_msg = "Server unreachable"
            self.flash_offline()
            return
        if not transcript.text:
            self.status_msg = "Heard nothing"
            return
        self.heard_text = transcript.text
        self.heard_lang = transcript.language

        dst = translation_target(transcript.language, self.target, self.home)
        messages = build_translate_messages(
            transcript.text, transcript.language, dst)
        translated = await self._ai.chat(messages)
        if not self.ctx:
            return
        if not translated:
            self.status_msg = "Translation failed"
            return
        self.translated_text = translated
        self.translated_lang = dst["code"]

        tts = await self._ai.synthesize(translated, dst["tts_model"], dst["voice"])
        if not self.ctx:
            return
        self._last_tts = tts
        if tts:
            duration = self.ctx.audio.play_wav_bytes(tts)
            self._play_until = time.monotonic() + duration
        else:
            self.status_msg = "No audio (text only)"

    def _replay(self):
        if self._last_tts and self.ctx:
            duration = self.ctx.audio.play_wav_bytes(self._last_tts)
            self._play_until = time.monotonic() + duration

    # --- input --------------------------------------------------------------

    def on_button(self, event: str):
        if event == "single":
            if self.state == TRState.READY:
                self._start_recording()
            elif self.state == TRState.RECORDING:
                self._stop_recording()
        elif event == "double":
            if self.state == TRState.READY:
                self._replay()

    def on_secondary(self, event: str):
        if event == "long" and self.state == TRState.READY:
            self.target_idx = (self.target_idx + 1) % len(self.languages)
            log.info("Translator target -> %s", self.target["name"])

    async def update(self, dt: float):
        if self.state == TRState.RECORDING:
            self._record_elapsed += dt
            if self._record_elapsed >= MAX_RECORD_SECS:
                self._stop_recording()

    # --- drawing ------------------------------------------------------------

    def draw(self, display):
        if not self.ctx:
            return
        d = self.ctx.display
        font_sm, font_md, font_lg = d.font_sm, d.font_md, d.font_lg
        x = CORNER_INSET
        w = SCREEN_WIDTH - 2 * CORNER_INSET

        pair = f"{self.home['code'].upper()} <-> {self.target['code'].upper()}"
        d.draw_centered_text(f"Translator  {pair}", CONTENT_TOP + 5,
                             font=font_sm, color=COLOR_DIM)

        if self.state == TRState.RECORDING:
            secs = int(self._record_elapsed)
            d.draw_centered_text(f"{secs // 60:01d}:{secs % 60:02d}",
                                 CONTENT_TOP + 55, font=font_lg)
            level = self.ctx.audio.level
            bar_w = int(w * level)
            d.draw_rect(x, CONTENT_TOP + 110, bar_w, 12, COLOR_SUCCESS)
            d.draw_centered_text("Listening (any language)", CONTENT_TOP + 140,
                                 font=font_sm, color=COLOR_DIM)
        elif self.state == TRState.PROCESSING:
            d.draw_centered_text("Translating...", CONTENT_TOP + 80, font=font_md)
        else:
            y = CONTENT_TOP + 28
            if self.heard_text:
                d.draw_text(f"Heard ({lang_name(self.heard_lang)}):", x, y,
                            font=font_sm, color=COLOR_DIM)
                y = d.draw_wrapped_text(self.heard_text, x, y + 16, w, 56,
                                        font=font_sm) + 8
                d.draw_text(f"-> {lang_name(self.translated_lang)}:", x, y,
                            font=font_sm, color=COLOR_SUCCESS)
                d.draw_wrapped_text(self.translated_text, x, y + 16, w,
                                    CONTENT_BOTTOM - y - 20, font=font_sm)
            else:
                mid = (CONTENT_TOP + CONTENT_BOTTOM) // 2
                d.draw_centered_text("Tap to talk", mid - 20, font=font_md)
                d.draw_centered_text(f"Speak English or {self.target['name']}",
                                     mid + 8, font=font_sm, color=COLOR_DIM)

        if self.offline_flash or (self.status_msg and self.state == TRState.READY):
            label = self.status_msg or "Server offline"
            d.draw_bottom_bar(label, color=COLOR_ERROR)
        elif time.monotonic() < self._play_until:
            d.draw_bottom_bar("Playing...", color=COLOR_SUCCESS)
        elif self.state == TRState.RECORDING:
            d.draw_bottom_bar("Tap=translate", color=COLOR_ERROR)
        elif self.state == TRState.PROCESSING:
            d.draw_bottom_bar("Working...", color=COLOR_DIM)
        else:
            d.draw_bottom_bar("Tap=talk  2x=replay  PiSugar=lang")
