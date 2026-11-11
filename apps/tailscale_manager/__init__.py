from __future__ import annotations

import asyncio
import enum
import logging
from typing import TYPE_CHECKING, Optional

from apps.base import AppBase
from core import tailscale
from core.tailscale import TailscaleStatus
from core.display import (
    SCREEN_WIDTH, SCREEN_HEIGHT, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT, CORNER_INSET,
    COLOR_TEXT, COLOR_DIM, COLOR_SUCCESS,
)

if TYPE_CHECKING:
    from core.context import AppContext

log = logging.getLogger(__name__)

CONTENT_TOP = STATUS_BAR_HEIGHT
ROW_HEIGHT = 36
REFRESH_INTERVAL = 3.0

# (label, key)
ACTIONS = [("Connect", "connect"), ("Disconnect", "disconnect"), ("Refresh", "refresh")]


class TSState(enum.Enum):
    STATUS = "status"
    AUTH = "auth"


class TailscaleManagerApp(AppBase):
    name = "Tailscale"
    icon = "assets/icons/tailscale.png"
    uses_wakeword = False

    def __init__(self):
        super().__init__()
        self.state = TSState.STATUS
        self.highlight = 0
        self.status: Optional[TailscaleStatus] = None
        self.busy_label = ""
        self.auth_url = ""
        self._task: Optional[asyncio.Task] = None
        self._since_refresh = 0.0

    def on_enter(self, ctx: AppContext):
        super().on_enter(ctx)
        self.state = TSState.STATUS
        self.highlight = 0
        self.status = None
        self.busy_label = ""
        self.auth_url = ""
        self._since_refresh = REFRESH_INTERVAL  # force a refresh on the first update tick

    def on_exit(self):
        self._task = None
        super().on_exit()

    # ---- async operations ----
    async def refresh(self):
        self.status = await tailscale.get_status()

    async def _do_connect(self):
        self.busy_label = "Connecting..."
        try:
            url = await tailscale.up()
        finally:
            self.busy_label = ""
        if url:
            self.auth_url = url
            self.state = TSState.AUTH
        else:
            await self.refresh()

    async def _do_disconnect(self):
        self.busy_label = "Disconnecting..."
        try:
            await tailscale.down()
        finally:
            self.busy_label = ""
        await self.refresh()

    def _start(self, coro):
        """Schedule a long-running op on the event loop (overridable in tests).
        One op at a time — callers gate on the busy/task state before calling."""
        self._task = asyncio.create_task(coro)
        # Surface (rather than swallow) any failure; core.tailscale shouldn't
        # raise, but a wedged busy_label/UI is worse than a logged error.
        self._task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("tailscale op failed: %s", exc)
            self.busy_label = ""

    # ---- input ----
    def on_button(self, event: str):
        # WhisPlay single: move highlight in STATUS; no-op in AUTH.
        if event != "single":
            return
        if self.state == TSState.STATUS:
            self.highlight = (self.highlight + 1) % len(ACTIONS)

    def _op_in_flight(self) -> bool:
        return bool(self.busy_label) or (self._task is not None and not self._task.done())

    def on_secondary(self, event: str):
        # PiSugar long: activate. Ignore while an op is scheduled or running —
        # busy_label alone isn't enough since the op body runs on a later tick.
        if event != "long" or self._op_in_flight():
            return
        if self.state == TSState.AUTH:
            self.state = TSState.STATUS
            self._start(self.refresh())
            return
        _label, key = ACTIONS[self.highlight]
        if key == "connect":
            self._start(self._do_connect())
        elif key == "disconnect":
            self._start(self._do_disconnect())
        elif key == "refresh":
            self._start(self.refresh())

    # ---- loop ----
    async def update(self, dt: float):
        self._since_refresh += dt
        if (self.state == TSState.STATUS and not self._op_in_flight()
                and self._since_refresh >= REFRESH_INTERVAL):
            self._since_refresh = 0.0
            await self.refresh()

    def draw(self, display):
        d = self.ctx.display if self.ctx else display
        d.draw_centered_text("Tailscale", CONTENT_TOP + 4, font=d.font_sm, color=COLOR_DIM)

        if self.busy_label:
            d.draw_centered_text(self.busy_label, SCREEN_HEIGHT // 2 - 10, font=d.font_md)
            return

        if self.state == TSState.AUTH:
            d.draw_text("Open in browser,", CORNER_INSET, CONTENT_TOP + 24, font=d.font_sm)
            d.draw_text("sign in with GitHub:", CORNER_INSET, CONTENT_TOP + 42, font=d.font_sm)
            d.draw_wrapped_text(self.auth_url, CORNER_INSET, CONTENT_TOP + 66,
                                SCREEN_WIDTH - 2 * CORNER_INSET, 120,
                                font=d.font_sm, color=COLOR_TEXT)
            d.draw_bottom_bar("PiSugar=back")
            return

        # STATUS view
        y = CONTENT_TOP + 24
        st = self.status
        if st is None:
            d.draw_text("Checking...", CORNER_INSET, y, font=d.font_sm, color=COLOR_DIM)
        else:
            state_color = COLOR_SUCCESS if st.backend_state == "Running" else COLOR_DIM
            d.draw_text(f"State: {st.backend_state}", CORNER_INSET, y,
                        font=d.font_sm, color=state_color); y += 20
            d.draw_text(f"IP: {st.self_ip or '-'}", CORNER_INSET, y, font=d.font_sm); y += 20
            d.draw_text(f"Name: {st.self_dnsname or '-'}", CORNER_INSET, y, font=d.font_sm); y += 20
            d.draw_text(f"Peers: {st.peer_count}", CORNER_INSET, y, font=d.font_sm); y += 20

        menu_y = SCREEN_HEIGHT - BOTTOM_BAR_HEIGHT - len(ACTIONS) * ROW_HEIGHT - 4
        for i, (label, _key) in enumerate(ACTIONS):
            if i == self.highlight:
                d.draw_highlight_row(menu_y, ROW_HEIGHT)
            d.draw_text(label, 14, menu_y + (ROW_HEIGHT - 15) // 2, font=d.font_md)
            menu_y += ROW_HEIGHT
        d.draw_bottom_bar("single=move  PiSugar=select")
