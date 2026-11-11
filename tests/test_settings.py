import tempfile
from unittest.mock import MagicMock

from core import power
from core.context import AppContext
from core.display import Display
from core.screencap import ScreenCapture
from apps.settings import SettingsApp, SettingsState, TOGGLES, ACTIONS


class FakeSettings:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def _app(monkeypatch, config=None, settings=None):
    # Spy on every power action so nothing is ever executed during tests.
    spies = {}
    for name in ("restart_app", "reboot", "poweroff"):
        m = MagicMock()
        monkeypatch.setattr(power, name, m)
        spies[name] = m
    app = SettingsApp()
    display = Display(headless=True)
    display.capture = ScreenCapture(root=tempfile.mkdtemp())  # never touch ~
    store = settings if settings is not None else FakeSettings()
    app.on_enter(AppContext(display=display, config=config or {}, settings=store))
    return app, display, spies, store


# --- row layout: 4 toggles then 3 actions ---

def test_row_layout():
    assert len(TOGGLES) == 4
    assert [t["key"] for t in TOGGLES] == ["listen_mode", "wakeword_enabled", "wakeword_model", "screen_rec"]
    assert [a[1] for a in ACTIONS] == ["restart", "reboot", "poweroff"]


def test_menu_navigation_wraps(monkeypatch):
    app, _, _, _ = _app(monkeypatch)
    assert app.highlight == 0
    for expected in (1, 2, 3, 4, 5, 6, 0):  # 7 rows -> wraps
        app.on_button("single")
        assert app.highlight == expected


# --- toggles ---

def test_toggle_listen_mode_persists(monkeypatch):
    app, _, _, store = _app(monkeypatch)
    # highlight 0 == Listen Mode, defaults to "auto"
    app.on_secondary("long")
    assert store.data["listen_mode"] == "manual"
    assert app.state == SettingsState.MENU  # no confirm for toggles
    app.on_secondary("long")
    assert store.data["listen_mode"] == "auto"


def test_toggle_wakeword_persists(monkeypatch):
    app, _, _, store = _app(monkeypatch)
    app.on_button("single")   # -> Wake Word
    app.on_secondary("long")
    assert store.data["wakeword_enabled"] is False
    app.on_secondary("long")
    assert store.data["wakeword_enabled"] is True


def test_toggle_value_overlay_wins_over_config(monkeypatch):
    app, _, _, _ = _app(
        monkeypatch,
        config={"voice": {"listen_mode": "auto"}},
        settings=FakeSettings({"listen_mode": "manual"}),
    )
    assert app._toggle_value(TOGGLES[0]) == "manual"


def test_toggle_value_falls_back_to_config(monkeypatch):
    app, _, _, _ = _app(monkeypatch, config={"wakeword": {"enabled": False}})
    assert app._toggle_value(TOGGLES[1]) is False


def test_toggle_value_default_without_config_or_overlay(monkeypatch):
    app, _, _, _ = _app(monkeypatch)
    assert app._toggle_value(TOGGLES[0]) == "auto"
    assert app._toggle_value(TOGGLES[1]) is True


def test_toggle_starts_from_config_value(monkeypatch):
    # config says manual, no overlay -> first toggle flips to auto
    app, _, _, store = _app(monkeypatch, config={"voice": {"listen_mode": "manual"}})
    app.on_secondary("long")
    assert store.data["listen_mode"] == "auto"


def test_toggle_snaps_to_default_when_persisted_value_is_invalid(monkeypatch):
    # e.g. settings.json edited by hand with a typo
    app, _, _, store = _app(monkeypatch,
                            settings=FakeSettings({"listen_mode": "bogus"}))
    app.on_secondary("long")   # snaps to default "auto" -> advances to "manual"
    assert store.data["listen_mode"] == "manual"


# --- power actions (indices shifted by the 2 toggle rows) ---

def test_restart_app_runs_immediately_without_confirm(monkeypatch):
    app, _, spies, _ = _app(monkeypatch)
    app.on_button("single")   # Wake Word
    app.on_button("single")   # Phrase
    app.on_button("single")   # Screen Rec
    app.on_button("single")   # Restart App
    app.on_secondary("long")
    spies["restart_app"].assert_called_once()
    spies["reboot"].assert_not_called()
    spies["poweroff"].assert_not_called()
    assert app.state == SettingsState.MENU


def test_reboot_enters_confirm_without_running(monkeypatch):
    app, _, spies, _ = _app(monkeypatch)
    for _ in range(5):
        app.on_button("single")   # -> Reboot
    app.on_secondary("long")
    assert app.state == SettingsState.CONFIRM
    spies["reboot"].assert_not_called()


def test_confirm_cancel_returns_to_menu(monkeypatch):
    app, _, spies, _ = _app(monkeypatch)
    for _ in range(5):
        app.on_button("single")   # -> Reboot
    app.on_secondary("long")      # enter confirm (defaults to Cancel)
    app.on_secondary("long")      # activate Cancel
    assert app.state == SettingsState.MENU
    spies["reboot"].assert_not_called()


def test_confirm_confirm_runs_action_once(monkeypatch):
    app, _, spies, _ = _app(monkeypatch)
    for _ in range(5):
        app.on_button("single")   # -> Reboot
    app.on_secondary("long")      # enter confirm
    app.on_button("single")       # Cancel -> Confirm
    app.on_secondary("long")      # activate Confirm
    spies["reboot"].assert_called_once()


def test_power_down_routes_to_poweroff(monkeypatch):
    app, _, spies, _ = _app(monkeypatch)
    for _ in range(6):
        app.on_button("single")   # -> Power Down
    app.on_secondary("long")      # enter confirm
    app.on_button("single")       # -> Confirm
    app.on_secondary("long")      # activate
    spies["poweroff"].assert_called_once()
    spies["reboot"].assert_not_called()


def test_draw_does_not_raise_in_all_states(monkeypatch):
    app, display, _, _ = _app(monkeypatch)
    app.draw(display)             # MENU with toggles
    for _ in range(5):
        app.on_button("single")   # -> Reboot
    app.on_secondary("long")      # CONFIRM
    app.draw(display)


def test_draw_does_not_raise_without_settings_store(monkeypatch):
    # Device tolerance: ctx.settings may be None if store creation failed.
    spies = {}
    for name in ("restart_app", "reboot", "poweroff"):
        m = MagicMock()
        monkeypatch.setattr(power, name, m)
        spies[name] = m
    app = SettingsApp()
    display = Display(headless=True)
    app.on_enter(AppContext(display=display, config={}, settings=None))
    app.draw(display)
    app.on_secondary("long")      # toggle activate without store -> no crash


# --- Screen Rec (runtime-only toggle) ---

def test_screen_rec_toggle_starts_and_stops_capture(monkeypatch, tmp_path):
    from core.screencap import ScreenCapture
    app, display, _, store = _app(monkeypatch)
    display.capture = ScreenCapture(root=str(tmp_path))
    app.on_button("single")
    app.on_button("single")
    app.on_button("single")          # -> Screen Rec
    assert app._toggle_value(TOGGLES[3]) is False
    app.on_secondary("long")
    assert display.capture.active
    assert app._toggle_value(TOGGLES[3]) is True
    app.on_secondary("long")
    assert not display.capture.active
    assert "screen_rec" not in store.data   # never persisted


def test_phrase_cycles_persists_and_applies(monkeypatch):
    app, _, _, store = _app(monkeypatch)
    ww = MagicMock()
    app.ctx.wakeword = ww
    app.on_button("single")
    app.on_button("single")          # -> Phrase
    assert app._toggle_value(TOGGLES[2]) == "hey_frank_v5"   # default
    app.on_secondary("long")
    assert store.data["wakeword_model"] == "hey_stackchan_v1"
    ww.set_model.assert_called_once_with("hey_stackchan_v1")
    app.on_secondary("long")
    app.on_secondary("long")
    assert store.data["wakeword_model"] == "hey_frank_v5"
