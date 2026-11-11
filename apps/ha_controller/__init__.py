from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING

from apps.voice_base import VoiceAppBase
from core.display import (
    SCREEN_WIDTH, SCREEN_HEIGHT, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT,
    COLOR_BG, COLOR_TEXT, COLOR_DIM, COLOR_HIGHLIGHT, COLOR_SUCCESS, COLOR_ERROR,
)

if TYPE_CHECKING:
    from core.context import AppContext

log = logging.getLogger(__name__)

CONTENT_TOP = STATUS_BAR_HEIGHT
ENTITY_POLL_INTERVAL = 30.0


class HAState(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    EXECUTING = "executing"


class HAControllerApp(VoiceAppBase):
    name = "HA Controller"
    icon = "assets/icons/ha.png"
    uses_wakeword = True

    def __init__(self):
        super().__init__()
        self.state = HAState.IDLE
        self.entity_states: dict[str, str] = {}
        self.last_command: str = ""
        self.last_result: str = ""
        self._entity_ids: list[str] = []
        self._poll_timer: float = 0.0

    def on_enter(self, ctx: AppContext):
        super().on_enter(ctx)
        self.state = HAState.IDLE
        self._entity_ids = ctx.config.get("ha_controller", {}).get("entities", [])

        # Unconditional: recovers automatically if we entered while offline.
        self._attach_server_handlers()

        if ctx.wakeword and ctx.wakeword.is_enabled:
            ctx.wakeword.start(self._on_wakeword)
            self._arm_wakeword()  # detector needs the raw mic feed

        self.spawn(self._poll_entities())

    def on_exit(self):
        if self.ctx:
            self.ctx.audio.stop_all()
            if self.ctx.wakeword:
                self.ctx.wakeword.stop()
            # Detach, or a late LLM/TTS reply hits these handlers with
            # ctx=None and churns the client's receive loop.
            self._detach_server_handlers()
        self.state = HAState.IDLE
        super().on_exit()

    async def _on_wakeword(self):
        # No wake_text: detect-text goes to the LLM upstream ("hey there!").
        if self.state == HAState.IDLE:
            await self._start_listening()

    async def _start_listening(self):
        if not await self._listen_start():
            return
        self.state = HAState.LISTENING

    # --- VoiceAppBase hooks -------------------------------------------------

    def _on_stt_final(self, text: str):
        self.last_command = text
        self.state = HAState.EXECUTING

    def _on_stt_mid_reply(self, text: str):
        # Function-call second pass re-emits the command; keep it fresh but
        # stay in EXECUTING so the spoken confirmation isn't dropped.
        self.last_command = text

    def _on_tts_sentence(self, text: str):
        self.last_result = text

    async def _on_speaking_stop(self):
        self._arm_wakeword()
        self.state = HAState.IDLE

    async def _poll_entities(self):
        if not self.ctx or not self.ctx.xiaozhi or not self.ctx.xiaozhi.connected:
            return
        if not self._entity_ids:
            return
        for eid in self._entity_ids:
            try:
                await self.ctx.xiaozhi.send_json({
                    "session_id": self.ctx.xiaozhi.session_id,
                    "type": "mcp",
                    "payload": {
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "homeassistant.state.get",
                            "arguments": {"entity_id": eid},
                        },
                        "id": hash(eid) & 0xFFFF,
                    },
                })
            except Exception as e:
                log.debug("Entity poll failed for %s: %s", eid, e)

    def on_button(self, event: str):
        if event == "single":
            if self.state == HAState.IDLE:
                self.spawn(self._start_listening())
            elif self.state == HAState.LISTENING:
                self.spawn(self._listen_stop())

    def on_secondary(self, event: str):
        if event == "long" and self.ctx and self.ctx.wakeword:
            enabled = self.ctx.wakeword.toggle()
            if enabled and self.state == HAState.IDLE:
                if not self.ctx.wakeword.is_active:
                    self.ctx.wakeword.start(self._on_wakeword)
                self._arm_wakeword()
            elif not enabled and self.ctx.audio.is_raw_capturing:
                self.ctx.audio.stop_capture()
            log.info("Wake word %s", "enabled" if enabled else "disabled")

    async def update(self, dt: float):
        self._poll_timer += dt
        if self._poll_timer >= ENTITY_POLL_INTERVAL:
            self._poll_timer = 0.0
            await self._poll_entities()

    def draw(self, display):
        if not self.ctx:
            return
        font_sm = self.ctx.display.font_sm
        font_md = self.ctx.display.font_md

        self.ctx.display.draw_centered_text("HA Controller", CONTENT_TOP + 5, font=font_sm, color=COLOR_DIM)

        if self.state == HAState.IDLE and not self.last_command:
            y = CONTENT_TOP + 30
            for eid in self._entity_ids:
                state_val = self.entity_states.get(eid, "...")
                short_name = eid.split(".")[-1].replace("_", " ").title()
                self.ctx.display.draw_text(short_name, 8, y, font=font_sm, color=COLOR_TEXT)
                bbox = font_sm.getbbox(state_val)
                state_w = bbox[2] - bbox[0]
                self.ctx.display.draw_text(
                    state_val, SCREEN_WIDTH - 8 - state_w, y,
                    font=font_sm, color=COLOR_SUCCESS,
                )
                y += 28
        else:
            y = CONTENT_TOP + 30
            if self.last_command:
                self.ctx.display.draw_text("Command:", 8, y, font=font_sm, color=COLOR_DIM)
                self.ctx.display.draw_wrapped_text(
                    self.last_command,
                    8, y + 18, SCREEN_WIDTH - 16, 50,
                    font=font_sm,
                )
            if self.last_result:
                self.ctx.display.draw_wrapped_text(
                    self.last_result,
                    8, CONTENT_TOP + 110, SCREEN_WIDTH - 16, 60,
                    font=font_sm, color=COLOR_SUCCESS,
                )

        if self.state == HAState.LISTENING:
            level = self.ctx.audio.level
            bar_w = int((SCREEN_WIDTH - 40) * level)
            self.ctx.display.draw_rect(20, SCREEN_HEIGHT - 90, bar_w, 12, COLOR_HIGHLIGHT)

        state_labels = {
            HAState.IDLE: "Idle",
            HAState.LISTENING: "Listening...",
            HAState.EXECUTING: "Executing...",
        }
        if self.offline_flash:
            self.ctx.display.draw_bottom_bar("Server offline", color=COLOR_ERROR)
        else:
            self.ctx.display.draw_bottom_bar(state_labels[self.state])
