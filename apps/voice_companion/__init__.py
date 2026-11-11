from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING

from apps.voice_base import VoiceAppBase
from core.display import (
    SCREEN_WIDTH, SCREEN_HEIGHT, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT,
    COLOR_BG, COLOR_TEXT, COLOR_DIM, COLOR_HIGHLIGHT, COLOR_SUCCESS, COLOR_ERROR,
    CORNER_INSET,
)
from core.face import Face, Expression

if TYPE_CHECKING:
    from core.context import AppContext

log = logging.getLogger(__name__)

CONTENT_TOP = STATUS_BAR_HEIGHT
CONTENT_BOTTOM = SCREEN_HEIGHT - BOTTOM_BAR_HEIGHT
CONTENT_HEIGHT = CONTENT_BOTTOM - CONTENT_TOP
IDLE_TIMEOUT = 5.0

# Device-side silence auto-stop: if the mic is open and the room stays below
# this RMS level (AudioService.level, rms/16000) for SILENCE_STOP_SEC, close
# the session and go idle. Safety net for when the server's VAD never replies
# (nobody actually spoke) — otherwise the mic stays open forever.
SILENCE_LEVEL = 0.012
SILENCE_STOP_SEC = 6.0
MIN_LISTEN_SEC = 1.5


class VCState(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class VoiceCompanionApp(VoiceAppBase):
    name = "Voice Companion"
    icon = "assets/icons/voice.png"
    uses_wakeword = True
    uses_double = True

    _EXPR = {
        VCState.IDLE: Expression.IDLE,
        VCState.LISTENING: Expression.LISTENING,
        VCState.THINKING: Expression.THINKING,
        VCState.SPEAKING: Expression.SPEAKING,
    }

    # WhisPlay RGB LED per state — pure 0/255 components only (intermediate
    # SoftPWM duty cycles can flicker on the Pi Zero 2W). Off outside the app.
    _LED = {
        VCState.IDLE: (255, 255, 255),
        VCState.LISTENING: (0, 255, 0),
        VCState.THINKING: (255, 255, 0),
        VCState.SPEAKING: (0, 0, 255),
    }

    WAKE_SOUND = "assets/sounds/wake_word_triggered.wav"

    def __init__(self):
        super().__init__()
        self.state = VCState.IDLE
        self.last_stt: str = ""
        self.last_tts: str = ""
        self._silence_timer: float = 0.0
        self._listen_elapsed: float = 0.0
        self._quiet_timer: float = 0.0
        self.view = "face"            # "face" | "transcript"
        self.face = Face()
        self.listen_mode = "auto"     # "auto" | "manual" (one-shot)

    @property
    def keep_awake(self) -> bool:
        # Mid-conversation the screen stays on; this is also the
        # wake-on-wakeword path (detection -> LISTENING -> saver disengages).
        return self.state != VCState.IDLE

    def _expression_for(self, state: VCState) -> Expression:
        return self._EXPR[state]

    def _transition(self, new_state: VCState):
        log.debug("VC state: %s -> %s", self.state.value, new_state.value)
        self.state = new_state
        if self.ctx:
            self.ctx.display.set_led(*self._LED[new_state])

    def _on_stt(self, text: str):
        self.last_stt = text
        self._transition(VCState.THINKING)

    def on_enter(self, ctx: AppContext):
        super().on_enter(ctx)
        self.state = VCState.IDLE
        self.last_stt = ""
        self.last_tts = ""
        self._silence_timer = 0.0
        self._listen_elapsed = 0.0
        self._quiet_timer = 0.0

        # Effective settings: data/settings.json overlay wins over config.yaml.
        voice_cfg = ctx.config.get("voice", {})
        default_mode = voice_cfg.get("listen_mode", "auto")
        if ctx.settings:
            self.listen_mode = ctx.settings.get("listen_mode", default_mode)
            ww_on = ctx.settings.get("wakeword_enabled", True)
        else:
            self.listen_mode = default_mode
            ww_on = ctx.config.get("wakeword", {}).get("enabled", True)

        # Unconditional: if we enter while offline, the handlers are ready the
        # moment the reconnect supervisor brings the connection back.
        self._attach_server_handlers()

        if ctx.wakeword and ctx.wakeword.is_enabled and ww_on:
            ctx.wakeword.start(self._on_wakeword)
            self._arm_wakeword()

        ctx.display.set_led(*self._LED[VCState.IDLE])

    def on_exit(self):
        if self.ctx:
            self.ctx.audio.stop_all()
            if self.ctx.wakeword:
                self.ctx.wakeword.stop()
            # Detach, or a late server reply (slow LLM) hits these handlers
            # with ctx=None and churns the client's receive loop.
            self._detach_server_handlers()
            self.ctx.display.set_led(0, 0, 0)  # LED only lit inside the app
        self.state = VCState.IDLE
        super().on_exit()

    async def _on_wakeword(self):
        # No wake_text: a "listen detect" message makes upstream xiaozhi-server
        # feed the phrase to the LLM (it replies "hey there!"). Detection should
        # just open the mic, exactly like a button press.
        if self.state == VCState.IDLE and self.ctx:
            # Chime first — output buffering (~50-100ms) means the tone lands
            # right as the mic opens (same trick as the echo-pyramid firmware).
            self.ctx.audio.play_sound_file(self.WAKE_SOUND)
            await self._start_listening()

    def _go_idle(self):
        self._transition(VCState.IDLE)
        self._silence_timer = 0.0
        self._arm_wakeword()

    async def _start_listening(self, wake_text: str = ""):
        if not await self._listen_start(wake_text=wake_text):
            return
        self._transition(VCState.LISTENING)
        self._silence_timer = 0.0
        self._listen_elapsed = 0.0
        self._quiet_timer = 0.0

    # --- VoiceAppBase hooks -------------------------------------------------

    def _on_stt_final(self, text: str):
        self._on_stt(text)

    def _on_stt_mid_reply(self, text: str):
        # Keep the transcript fresh but do NOT flip to THINKING — that knocks
        # us out of SPEAKING and the reply audio gets dropped (journal:
        # "AUDIO DROPPED: N bytes in state thinking").
        self.last_stt = text

    def _on_speaking_start(self):
        self._transition(VCState.SPEAKING)

    def _on_tts_sentence(self, text: str):
        self.last_tts = text

    async def _on_speaking_stop(self):
        if self.listen_mode == "manual":
            # One-shot: back to idle, wake word rearmed.
            self._go_idle()
        else:
            # Continuous conversation; wake word stays paused until idle.
            self._transition(VCState.LISTENING)
            self._silence_timer = 0.0
            self._listen_elapsed = 0.0
            self._quiet_timer = 0.0
            self.ctx.audio.start_capture(self.ctx.xiaozhi.send_audio)
            await self.ctx.xiaozhi.start_listening()

    def on_button(self, event: str):
        if event == "single":
            if self.state == VCState.IDLE:
                self.spawn(self._start_listening())
            elif self.state == VCState.LISTENING:
                self.spawn(self._listen_stop())
        elif event == "double":
            self.view = "transcript" if self.view == "face" else "face"

    def on_secondary(self, event: str):
        if event == "long" and self.ctx and self.ctx.wakeword:
            # Transient toggle (resets on next enter); Settings app persists.
            enabled = self.ctx.wakeword.toggle()
            if enabled and self.state == VCState.IDLE:
                if not self.ctx.wakeword.is_active:
                    self.ctx.wakeword.start(self._on_wakeword)
                self._arm_wakeword()
            elif not enabled and self.ctx.audio.is_raw_capturing:
                self.ctx.audio.stop_capture()
            log.info("Wake word %s", "enabled" if enabled else "disabled")

    async def update(self, dt: float):
        if not self.ctx:
            return
        if self.state == VCState.LISTENING:
            if self.ctx.audio.is_capturing:
                # Mic open: silence auto-stop (see SILENCE_* constants).
                self._listen_elapsed += dt
                if (self._listen_elapsed >= MIN_LISTEN_SEC
                        and self.ctx.audio.level < SILENCE_LEVEL):
                    self._quiet_timer += dt
                    if self._quiet_timer >= SILENCE_STOP_SEC:
                        log.info("VC: silence auto-stop after %.1fs", self._quiet_timer)
                        await self._listen_stop()
                        self._go_idle()
                else:
                    self._quiet_timer = 0.0
            else:
                # Mic already closed (manual stop) but no reply: bail to idle.
                self._silence_timer += dt
                if self._silence_timer >= IDLE_TIMEOUT:
                    self._go_idle()

        level = 0.0
        if self.state in (VCState.SPEAKING, VCState.LISTENING):
            level = self.ctx.audio.level if self.ctx else 0.0
        self.face.set_state(self._expression_for(self.state), level)
        self.face.update(dt)

    def draw(self, display):
        if not self.ctx:
            return
        font_sm = self.ctx.display.font_sm
        font_md = self.ctx.display.font_md

        if self.view == "face":
            self.face.draw(self.ctx.display)
        else:
            content_x = CORNER_INSET
            content_y = CONTENT_TOP + 10
            content_w = SCREEN_WIDTH - 2 * CORNER_INSET

            if not self.last_stt and not self.last_tts:
                hint_y = (CONTENT_TOP + CONTENT_BOTTOM) // 2
                self.ctx.display.draw_centered_text(
                    "Say 'hey stackchan'", hint_y, font=font_sm, color=COLOR_DIM
                )

            if self.last_stt:
                self.ctx.display.draw_text("You:", content_x, content_y, font=font_sm, color=COLOR_DIM)
                y = self.ctx.display.draw_wrapped_text(
                    self.last_stt,
                    content_x, content_y + 20, content_w, 60,
                    font=font_sm,
                )
            else:
                y = content_y

            if self.last_tts:
                ai_y = max(y + 10, content_y + 80)
                self.ctx.display.draw_text("AI:", content_x, ai_y, font=font_sm, color=COLOR_SUCCESS)
                self.ctx.display.draw_wrapped_text(
                    self.last_tts,
                    content_x, ai_y + 20, content_w, 80,
                    font=font_sm,
                )

            if self.state == VCState.LISTENING:
                level = self.ctx.audio.level
                bar_y = CONTENT_BOTTOM - 30
                bar_w = int((SCREEN_WIDTH - 40) * level)
                self.ctx.display.draw_rect(20, bar_y, bar_w, 12, COLOR_HIGHLIGHT)

        state_labels = {
            VCState.IDLE: "Idle",
            VCState.LISTENING: "Listening...",
            VCState.THINKING: "Thinking...",
            VCState.SPEAKING: "Speaking...",
        }
        if self.offline_flash:
            self.ctx.display.draw_bottom_bar("Server offline", color=COLOR_ERROR)
        else:
            self.ctx.display.draw_bottom_bar(state_labels[self.state])
