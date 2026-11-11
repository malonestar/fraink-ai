"""Cover-flow launcher: one big centered icon, dimmed neighbors peeking at
the screen edges. A button press advances `highlight` immediately and eases
a slot-offset back to 0 (200ms, ease-out cubic) so the strip slides; a
press mid-flight overwrites the offset — that IS the snap. Icons come from
core.icons (procedural, cached); entries' "icon" field is a glyph name."""

from __future__ import annotations

import logging
import math
from typing import Optional, TYPE_CHECKING

from core import icons
from core.display import SCREEN_WIDTH, COLOR_TEXT, COLOR_DIM

if TYPE_CHECKING:
    from core.context import AppContext
    from core.display import Display
    from apps.base import AppBase

log = logging.getLogger(__name__)

SLOT_W = 110          # px between adjacent icon centers
CENTER_X = SCREEN_WIDTH // 2
ICON_CENTER_Y = 112   # vertical center of the icon strip
CENTER_SIZE = 96
SIDE_SIZE = 56
NAME_Y = 180
DOTS_Y = 212
DOT_GAP = 14
ANIM_SECONDS = 0.2    # slide duration
BOB_AMPLITUDE = 2.5   # px idle bob of the centered icon
BOB_HZ = 0.5
DOT_INACTIVE = (70, 70, 70)


class Launcher:
    def __init__(self, entries: list[dict]):
        self._entries = entries
        self.highlight: int = 0
        self.active_app: Optional[AppBase] = None
        self._offset = 0.0       # slide displacement in slots; eases to 0
        self._anim_from = 0.0
        self._anim_t = 0.0
        self._bob_phase = 0.0

    @property
    def in_app(self) -> bool:
        return self.active_app is not None

    def next(self):
        self._start_slide(+1)

    def prev(self):
        self._start_slide(-1)

    def _start_slide(self, direction: int):
        self.highlight = (self.highlight + direction) % len(self._entries)
        self._offset = float(direction)
        self._anim_from = float(direction)
        self._anim_t = 0.0

    def update(self, dt: float) -> bool:
        """Advance slide + idle bob. Returns True while a redraw is needed —
        always at the home screen (the screensaver bounds how long the bob
        keeps the loop drawing)."""
        if self._offset != 0.0:
            self._anim_t += dt
            t = min(1.0, self._anim_t / ANIM_SECONDS)
            eased = 1.0 - (1.0 - t) ** 3
            self._offset = self._anim_from * (1.0 - eased)
            if t >= 1.0:
                self._offset = 0.0
        self._bob_phase = (self._bob_phase
                           + dt * 2 * math.pi * BOB_HZ) % (2 * math.pi)
        return True

    def launch(self, ctx: AppContext):
        entry = self._entries[self.highlight]
        app = entry["app"]
        self.active_app = app
        app.on_enter(ctx)
        log.info("Launched app: %s", entry["name"])

    def exit_app(self):
        if self.active_app:
            self.active_app.on_exit()
            log.info("Exited app: %s", self.active_app.name)
            self.active_app = None

    def draw(self, display: Display):
        n = len(self._entries)
        for i, entry in enumerate(self._entries):
            # Wrapped signed slot distance from the highlight (-n//2..n//2).
            rel = ((i - self.highlight + n // 2) % n) - n // 2
            pos = rel + self._offset
            if abs(pos) > 1.6:
                continue  # offscreen
            centered = abs(pos) < 0.5
            size = CENTER_SIZE if centered else SIDE_SIZE
            color = COLOR_TEXT if centered else COLOR_DIM
            icon = icons.render(entry["icon"], size, color)
            x = int(CENTER_X + pos * SLOT_W - size / 2)
            y = ICON_CENTER_Y - size // 2
            if centered and self._offset == 0.0:
                y += int(round(math.sin(self._bob_phase) * BOB_AMPLITUDE))
            display.canvas.paste(icon, (x, y), icon)

        display.draw_centered_text(self._entries[self.highlight]["name"],
                                   NAME_Y, font=display.font_md)

        x0 = CENTER_X - (n - 1) * DOT_GAP // 2
        for i in range(n):
            r = 4 if i == self.highlight else 3
            color = COLOR_TEXT if i == self.highlight else DOT_INACTIVE
            display.draw_ellipse(x0 + i * DOT_GAP - r, DOTS_Y - r,
                                 x0 + i * DOT_GAP + r, DOTS_Y + r, color)
