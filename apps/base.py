from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Coroutine

if TYPE_CHECKING:
    from core.context import AppContext
    from core.display import Display

log = logging.getLogger(__name__)

OFFLINE_FLASH_SEC = 2.0


class AppBase:
    name: str = "App"
    icon: str = ""
    uses_wakeword: bool = False
    # When True, the WhisPlay button defers single-tap by the double-tap window
    # so it can detect double-taps. Costs ~250ms of single-tap latency, so leave
    # it off unless an app genuinely needs double-tap.
    uses_double: bool = False

    def __init__(self):
        self.ctx: AppContext | None = None
        self._bg_tasks: set[asyncio.Task] = set()
        self._offline_until: float = 0.0

    @property
    def keep_awake(self) -> bool:
        """When True, the idle screensaver never blanks this app's screen.
        Override for state-dependent behavior (e.g. voice companion
        mid-conversation, note taker while recording)."""
        return False

    def spawn(self, coro: Coroutine) -> asyncio.Task:
        """Fire-and-forget task with a kept reference and error logging.

        Bare asyncio.create_task() results can be GC'd mid-flight and their
        exceptions vanish as unretrieved results — every app-level background
        action goes through here instead.
        """
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_task_done)
        return task

    def _bg_task_done(self, task: asyncio.Task):
        self._bg_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("Background task failed in %s: %s", self.name, exc,
                      exc_info=exc)

    def flash_offline(self, secs: float = OFFLINE_FLASH_SEC):
        """Show 'Server offline' in the bottom bar for a couple of seconds —
        called when a voice action is pressed while the server is down."""
        self._offline_until = time.monotonic() + secs

    @property
    def offline_flash(self) -> bool:
        return time.monotonic() < self._offline_until

    def on_enter(self, ctx: AppContext):
        self.ctx = ctx

    def on_exit(self):
        self.ctx = None

    def on_button(self, event: str):
        pass

    def on_secondary(self, event: str):
        """Handle the PiSugar (secondary) button's long-press while in-app."""
        pass

    async def update(self, dt: float):
        pass

    def draw(self, display: Display):
        pass
