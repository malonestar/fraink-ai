"""Two-stage idle screensaver, home screen and apps alike.

After `dim_after` seconds with no button activity: DIM — dark screen with
a half-bright clock. (True 50% backlight would need SoftPWM, which flickers
on the Pi Zero 2W — same gotcha as the backlight mode — so the dimming is
done in pixels.) After `off_after` seconds: OFF — backlight fully off.

Any button press wakes from either stage and is consumed (it must not
scroll, launch, or trigger the app's action). Apps opt out while busy via
`AppBase.keep_awake` (voice companion mid-conversation, note taker while
recording); that path also implements wake-on-wakeword — a detection flips
the voice companion to LISTENING and tick() snaps the saver back awake.
"""

from __future__ import annotations


class IdleScreensaver:
    AWAKE = "awake"
    DIM = "dim"
    OFF = "off"

    def __init__(self, dim_after: float = 15.0, off_after: float = 60.0):
        self.dim_after = dim_after
        self.off_after = off_after
        self._idle = 0.0
        self.stage = self.AWAKE

    @property
    def active(self) -> bool:
        return self.stage != self.AWAKE

    def _stage_for(self, idle: float) -> str:
        if idle >= self.off_after:
            return self.OFF
        if idle >= self.dim_after:
            return self.DIM
        return self.AWAKE

    def tick(self, dt: float, allow: bool = True) -> bool:
        """Advance the idle timer. Returns True when the stage changed so
        the caller can redraw and sync the backlight."""
        if not allow:
            self._idle = 0.0
            changed = self.stage != self.AWAKE
            self.stage = self.AWAKE
            return changed
        self._idle += dt
        new_stage = self._stage_for(self._idle)
        if new_stage != self.stage:
            self.stage = new_stage
            return True
        return False

    def on_activity(self) -> bool:
        """Register a button press. Returns True if it woke the saver —
        the caller must consume that press instead of acting on it."""
        self._idle = 0.0
        woke = self.stage != self.AWAKE
        self.stage = self.AWAKE
        return woke
