from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING

from apps.base import AppBase
from core.display import (
    SCREEN_WIDTH, SCREEN_HEIGHT, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT,
    COLOR_BG, COLOR_TEXT, COLOR_DIM, COLOR_HIGHLIGHT, COLOR_SUCCESS, COLOR_ERROR,
)

if TYPE_CHECKING:
    from core.context import AppContext

log = logging.getLogger(__name__)

CONTENT_TOP = STATUS_BAR_HEIGHT
CONTENT_BOTTOM = SCREEN_HEIGHT - BOTTOM_BAR_HEIGHT


def session_dots(session: int, sessions_for_long: int) -> tuple[int, int]:
    """(total, filled) where filled = completed work sessions in the current cycle."""
    total = max(1, sessions_for_long)
    return total, session % total


class PomState(enum.Enum):
    RUNNING = "running"
    PAUSED = "paused"


class PomPhase(enum.Enum):
    WORK = "Work"
    SHORT_BREAK = "Short Break"
    LONG_BREAK = "Long Break"


class PomodoroApp(AppBase):
    name = "Pomodoro"
    icon = "assets/icons/timer.png"
    uses_wakeword = False
    uses_double = True

    def __init__(self):
        super().__init__()
        self.work_secs: int = 25 * 60
        self.short_break_secs: int = 5 * 60
        self.long_break_secs: int = 15 * 60
        self.sessions_for_long: int = 4
        self.phase = PomPhase.WORK
        self.pom_state = PomState.PAUSED
        self.remaining: float = float(self.work_secs)
        self.session: int = 0
        self._flash_timer: float = 0.0

    # WhisPlay RGB LED: red = focus, green = break, off = paused / outside the
    # app. Pure 0/255 colors only (SoftPWM flicker, see display.py).
    def _update_led(self):
        if not self.ctx:
            return
        if self.pom_state != PomState.RUNNING:
            self.ctx.display.set_led(0, 0, 0)
        elif self.phase == PomPhase.WORK:
            self.ctx.display.set_led(255, 0, 0)
        else:
            self.ctx.display.set_led(0, 255, 0)

    def on_enter(self, ctx: AppContext):
        super().on_enter(ctx)
        pom_cfg = ctx.config.get("pomodoro", {})
        self.work_secs = pom_cfg.get("work_minutes", 25) * 60
        self.short_break_secs = pom_cfg.get("short_break_minutes", 5) * 60
        self.long_break_secs = pom_cfg.get("long_break_minutes", 15) * 60
        self.sessions_for_long = pom_cfg.get("sessions_before_long_break", 4)
        self.phase = PomPhase.WORK
        self.pom_state = PomState.PAUSED
        self.remaining = float(self.work_secs)
        self.session = 0
        self._update_led()

    def on_exit(self):
        self.pom_state = PomState.PAUSED
        if self.ctx:
            self.ctx.display.set_led(0, 0, 0)
        super().on_exit()

    def toggle_pause(self):
        if self.pom_state == PomState.PAUSED:
            self.pom_state = PomState.RUNNING
        else:
            self.pom_state = PomState.PAUSED
        self._update_led()

    def tick(self, dt: float):
        if self.pom_state != PomState.RUNNING:
            return
        self.remaining = max(0.0, self.remaining - dt)

    def next_phase(self) -> tuple[PomPhase, int]:
        if self.phase == PomPhase.WORK:
            if self.session > 0 and self.session % self.sessions_for_long == 0:
                return PomPhase.LONG_BREAK, self.long_break_secs
            return PomPhase.SHORT_BREAK, self.short_break_secs
        return PomPhase.WORK, self.work_secs

    def skip_phase(self):
        if self.phase == PomPhase.WORK:
            self.session += 1
        next_phase, next_dur = self.next_phase()
        self.phase = next_phase
        self.remaining = float(next_dur)
        self._update_led()
        log.info("Pomodoro: %s (session %d)", self.phase.value, self.session)

    def _on_phase_complete(self):
        if self.ctx:
            self.ctx.audio.play_sound_file("assets/sounds/chime.wav")
            self._fire_ha_scene()
        self.skip_phase()
        self._flash_timer = 1.5

    def _fire_ha_scene(self):
        if not self.ctx or not self.ctx.xiaozhi or not self.ctx.xiaozhi.connected:
            return
        pom_cfg = self.ctx.config.get("pomodoro", {})
        if self.phase == PomPhase.WORK:
            scene = pom_cfg.get("ha_scene_break", "")
        else:
            scene = pom_cfg.get("ha_scene_focus", "")
        if not scene:
            return
        self.spawn(self.ctx.xiaozhi.send_json({
            "session_id": self.ctx.xiaozhi.session_id,
            "type": "mcp",
            "payload": {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "homeassistant.scene.turn_on",
                    "arguments": {"entity_id": scene},
                },
                "id": 1,
            },
        }))

    def _phase_duration(self) -> int:
        if self.phase == PomPhase.WORK:
            return self.work_secs
        if self.phase == PomPhase.SHORT_BREAK:
            return self.short_break_secs
        return self.long_break_secs

    def reset_phase(self):
        self.remaining = float(self._phase_duration())
        self.pom_state = PomState.PAUSED
        self._update_led()

    def on_button(self, event: str):
        if event == "single":
            self.toggle_pause()
        elif event == "double":
            self.skip_phase()

    def on_secondary(self, event: str):
        if event == "long":
            self.reset_phase()

    async def update(self, dt: float):
        self.tick(dt)
        if self._flash_timer > 0:
            self._flash_timer -= dt
        if self.remaining <= 0 and self.pom_state == PomState.RUNNING:
            self._on_phase_complete()

    def draw(self, display):
        if not self.ctx:
            return
        font_sm = self.ctx.display.font_sm
        font_md = self.ctx.display.font_md
        font_xl = self.ctx.display.font_xl

        self.ctx.display.draw_centered_text("Pomodoro", CONTENT_TOP + 5, font=font_sm, color=COLOR_DIM)

        mins = int(self.remaining) // 60
        secs = int(self.remaining) % 60
        time_str = f"{mins:02d}:{secs:02d}"

        if self._flash_timer > 0:
            time_color = COLOR_SUCCESS if int(self._flash_timer * 6) % 2 == 0 else COLOR_TEXT
        else:
            time_color = COLOR_TEXT

        self.ctx.display.draw_centered_text(time_str, CONTENT_TOP + 50, font=font_xl, color=time_color)

        phase_color = COLOR_ERROR if self.phase == PomPhase.WORK else COLOR_SUCCESS
        self.ctx.display.draw_centered_text(self.phase.value, CONTENT_TOP + 130, font=font_md, color=phase_color)

        total, filled = session_dots(self.session, self.sessions_for_long)
        self.ctx.display.draw_session_dots(
            cx=SCREEN_WIDTH // 2, y=CONTENT_TOP + 165, total=total, filled=filled,
            on=phase_color, off=COLOR_DIM)

        if self.pom_state == PomState.PAUSED and self._flash_timer <= 0:
            self.ctx.display.draw_pause_glyph(SCREEN_WIDTH - 30, CONTENT_TOP + 72, 18, paused=True)

        progress = 0.0
        if self.phase == PomPhase.WORK:
            total_secs = self.work_secs
        elif self.phase == PomPhase.SHORT_BREAK:
            total_secs = self.short_break_secs
        else:
            total_secs = self.long_break_secs
        if total_secs > 0:
            progress = 1.0 - (self.remaining / total_secs)
        self.ctx.display.draw_progress_bar(20, CONTENT_TOP + 190, SCREEN_WIDTH - 40, 8, progress, phase_color)

        if self.pom_state == PomState.RUNNING:
            state_label = "Tap=Pause  2x=Skip"
        else:
            state_label = "Tap=Start  Hold=Reset"
        self.ctx.display.draw_bottom_bar(state_label)
