import pytest

from core.display import Display


@pytest.fixture
def display():
    return Display(headless=True)


@pytest.mark.parametrize("state", ["Running", "Stopped", "NeedsLogin", "NoState", "NotInstalled"])
def test_status_bar_draws_for_every_tailscale_state(display, state):
    # Should render without raising for all states (color/visibility handled internally).
    display.fill()
    display.draw_status_bar(battery_pct=80, wifi=True, tailscale_state=state,
                            wakeword=False, server=True)


def test_status_bar_accepts_default_tailscale_state(display):
    display.fill()
    display.draw_status_bar(battery_pct=50, wifi=True)  # tailscale_state defaults


def test_status_bar_plugged_indicator_renders(display):
    display.fill()
    display.draw_status_bar(battery_pct=64, wifi=True, server=True, plugged=True)


def test_status_bar_plugged_without_battery_reading(display):
    # plugged with pct=-1 must not draw (and must not raise)
    display.fill()
    display.draw_status_bar(battery_pct=-1, plugged=True)
