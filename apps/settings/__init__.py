from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING

from apps.base import AppBase
from core import power
from core.display import (
    SCREEN_WIDTH, SCREEN_HEIGHT, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT,
    COLOR_TEXT, COLOR_DIM, COLOR_ERROR,
)

if TYPE_CHECKING:
    from core.context import AppContext

log = logging.getLogger(__name__)

CONTENT_TOP = STATUS_BAR_HEIGHT
# 27px keeps 7 rows (start y=CONTENT_TOP+26) above the bottom bar:
# 50 + 7*27 = 239 <= 280 - BOTTOM_BAR_HEIGHT (244).
ROW_HEIGHT = 27

# Toggle rows sit above the power actions. PiSugar long flips them
# immediately (no confirm) and persists via ctx.settings (data/settings.json
# overlay). Displayed value: overlay > config.yaml > hardcoded default.
TOGGLES = [
    {"label": "Listen Mode", "key": "listen_mode",
     "values": ["auto", "manual"], "names": {"auto": "Auto", "manual": "Manual"},
     "config_section": "voice", "config_key": "listen_mode", "default": "auto"},
    {"label": "Wake Word", "key": "wakeword_enabled",
     "values": [True, False], "names": {True: "On", False: "Off"},
     "config_section": "wakeword", "config_key": "enabled", "default": True},
    # Which wake phrase arms the mic. Persisted like the others; applied
    # live through ctx.wakeword.set_model (the "apply" hook below).
    {"label": "Phrase", "key": "wakeword_model", "apply": "wakeword_model",
     "values": ["hey_frank_v5", "hey_stackchan_v1", "hey_m5_v3"],
     "names": {"hey_frank_v5": "Hey Frank", "hey_stackchan_v1": "StackChan",
               "hey_m5_v3": "Hey M5"},
     "config_section": "wakeword", "config_key": "model", "default": "hey_frank_v5"},
    # Runtime-only (never persisted, so it is always Off after a restart):
    # records every pushed frame for video B-roll — see core/screencap.py.
    {"label": "Screen Rec", "key": "screen_rec", "runtime": True,
     "values": [False, True], "names": {True: "On", False: "Off"},
     "default": False},
]

# (label, action key, needs confirmation)
ACTIONS = [
    ("Restart App", "restart", False),
    ("Reboot", "reboot", True),
    ("Power Down", "poweroff", True),
]

NUM_ROWS = len(TOGGLES) + len(ACTIONS)

RUNNING_LABELS = {
    "restart": "Restarting...",
    "reboot": "Rebooting...",
    "poweroff": "Powering down...",
}


class SettingsState(enum.Enum):
    MENU = "menu"
    CONFIRM = "confirm"


class SettingsApp(AppBase):
    name = "Settings"
    icon = "assets/icons/settings.png"
    uses_wakeword = False

    def __init__(self):
        super().__init__()
        self.state = SettingsState.MENU
        self.highlight = 0
        self.confirm_choice = 0  # 0 = Cancel, 1 = Confirm
        self.pending_key: str | None = None
        self.status: str = ""

    def on_enter(self, ctx: AppContext):
        super().on_enter(ctx)
        self._reset()

    def on_exit(self):
        self._reset()
        super().on_exit()

    def _reset(self):
        self.state = SettingsState.MENU
        self.highlight = 0
        self.confirm_choice = 0
        self.pending_key = None
        self.status = ""

    def _run_action(self, key: str):
        self.status = RUNNING_LABELS.get(key, "Working...")
        if key == "restart":
            power.restart_app()
        elif key == "reboot":
            power.reboot()
        elif key == "poweroff":
            power.poweroff()

    def _toggle_value(self, row: dict):
        if row.get("runtime"):
            cap = getattr(getattr(self.ctx, "display", None), "capture", None)
            return bool(cap and cap.active)
        cfg = (self.ctx.config if self.ctx else {}).get(row["config_section"], {})
        default = cfg.get(row["config_key"], row["default"])
        store = self.ctx.settings if self.ctx else None
        return store.get(row["key"], default) if store else default

    def _toggle(self, row: dict):
        values = row["values"]
        current = self._toggle_value(row)
        if current not in values:
            current = row["default"]
        new = values[(values.index(current) + 1) % len(values)]
        if row.get("runtime"):
            display = getattr(self.ctx, "display", None)
            if display is not None and hasattr(display, "set_capture"):
                try:
                    display.set_capture(bool(new))
                except Exception as e:  # e.g. SD card full / not writable
                    log.warning("Screen capture toggle failed: %s", e)
            return
        if self.ctx and self.ctx.settings:
            self.ctx.settings.set(row["key"], new)
        if row.get("apply") == "wakeword_model":
            ww = getattr(self.ctx, "wakeword", None)
            if ww is not None and hasattr(ww, "set_model"):
                ww.set_model(new)
        log.info("Setting %s -> %s", row["key"], new)

    def on_button(self, event: str):
        # WhisPlay single: move highlight (menu) / toggle choice (confirm).
        if event != "single":
            return
        if self.state == SettingsState.MENU:
            self.highlight = (self.highlight + 1) % NUM_ROWS
        elif self.state == SettingsState.CONFIRM:
            self.confirm_choice = (self.confirm_choice + 1) % 2

    def on_secondary(self, event: str):
        # PiSugar long: select (menu) / activate choice (confirm).
        if event != "long":
            return
        if self.state == SettingsState.MENU:
            if self.highlight < len(TOGGLES):
                self._toggle(TOGGLES[self.highlight])
                return
            label, key, needs_confirm = ACTIONS[self.highlight - len(TOGGLES)]
            if needs_confirm:
                self.state = SettingsState.CONFIRM
                self.pending_key = key
                self.confirm_choice = 0
            else:
                self._run_action(key)
        elif self.state == SettingsState.CONFIRM:
            if self.confirm_choice == 1:
                self._run_action(self.pending_key)
            else:
                self.state = SettingsState.MENU
                self.pending_key = None

    def draw(self, display):
        d = self.ctx.display if self.ctx else display
        font_sm = d.font_sm
        font_md = d.font_md

        d.draw_centered_text("Settings", CONTENT_TOP + 4, font=font_sm, color=COLOR_DIM)

        if self.status:
            d.draw_centered_text(self.status, SCREEN_HEIGHT // 2 - 10,
                                 font=font_md, color=COLOR_TEXT)
            return

        if self.state == SettingsState.MENU:
            y = CONTENT_TOP + 26
            for i, row in enumerate(TOGGLES):
                if i == self.highlight:
                    d.draw_highlight_row(y, ROW_HEIGHT)
                value_name = row["names"].get(self._toggle_value(row), "?")
                d.draw_text(f"{row['label']}: {value_name}", 14,
                            y + (ROW_HEIGHT - 15) // 2,
                            font=font_md, color=COLOR_TEXT)
                y += ROW_HEIGHT
            for j, (label, _key, _confirm) in enumerate(ACTIONS):
                if len(TOGGLES) + j == self.highlight:
                    d.draw_highlight_row(y, ROW_HEIGHT)
                d.draw_text(label, 14, y + (ROW_HEIGHT - 15) // 2,
                            font=font_md, color=COLOR_TEXT)
                y += ROW_HEIGHT
            d.draw_bottom_bar("single=move  PiSugar=select")
        else:
            label = next((l for l, k, _ in ACTIONS if k == self.pending_key), "")
            d.draw_centered_text(f"{label}?", CONTENT_TOP + 30,
                                 font=font_md, color=COLOR_ERROR)
            choices = ["Cancel", "Confirm"]
            y = CONTENT_TOP + 70
            for i, choice in enumerate(choices):
                if i == self.confirm_choice:
                    d.draw_highlight_row(y, ROW_HEIGHT)
                d.draw_text(choice, 14, y + (ROW_HEIGHT - 15) // 2,
                            font=font_md, color=COLOR_TEXT)
                y += ROW_HEIGHT
            d.draw_bottom_bar("single=move  PiSugar=select")
