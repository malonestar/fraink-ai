"""core/pisugar.py: battery/plugged parsing and boot-time RTC + safe-shutdown."""

import time

import core.pisugar as pisugar


def _patch_socket(monkeypatch, responses, sent=None):
    monkeypatch.setattr(pisugar, "_find_pisugar_socket", lambda: "/fake.sock")

    def fake_request(sock_path, command, timeout=1.5):
        if sent is not None:
            sent.append(command)
        return responses.get(command)

    monkeypatch.setattr(pisugar, "_pisugar_request", fake_request)


def test_battery_info_parses_pct_and_plugged(monkeypatch):
    _patch_socket(monkeypatch, {
        "get battery": "battery: 84.2",
        "get battery_power_plugged": "battery_power_plugged: true",
    })
    assert pisugar.get_battery_info() == (84, True)


def test_battery_info_falls_back_to_charging(monkeypatch):
    # Older firmware has no power_plugged; charging implies external power.
    _patch_socket(monkeypatch, {
        "get battery": "battery: 42",
        "get battery_power_plugged": None,
        "get battery_charging": "battery_charging: true",
    })
    assert pisugar.get_battery_info() == (42, True)


def test_battery_info_on_battery(monkeypatch):
    _patch_socket(monkeypatch, {
        "get battery": "battery: 100",
        "get battery_power_plugged": "battery_power_plugged: false",
        "get battery_charging": "battery_charging: false",
    })
    assert pisugar.get_battery_info() == (100, False)


def test_battery_info_no_socket(monkeypatch):
    monkeypatch.setattr(pisugar, "_find_pisugar_socket", lambda: None)
    assert pisugar.get_battery_info() == (-1, False)


def test_battery_info_garbage_response(monkeypatch):
    _patch_socket(monkeypatch, {"get battery": "nonsense"})
    pct, plugged = pisugar.get_battery_info()
    assert pct == -1
    assert plugged is False


def test_boot_init_valid_clock_pushes_pi_to_rtc(monkeypatch):
    sent = []
    _patch_socket(monkeypatch, {}, sent)
    # Test machine clock is valid (>= CLOCK_VALID_YEAR).
    assert time.localtime().tm_year >= pisugar.CLOCK_VALID_YEAR

    pisugar._boot_init_sync()

    assert "rtc_pi2rtc" in sent
    assert "rtc_rtc2pi" not in sent
    assert f"set_safe_shutdown_level {pisugar.SAFE_SHUTDOWN_LEVEL}" in sent
    assert f"set_safe_shutdown_delay {pisugar.SAFE_SHUTDOWN_DELAY}" in sent


def test_boot_init_invalid_clock_restores_from_rtc(monkeypatch):
    sent = []
    _patch_socket(monkeypatch, {}, sent)
    fake = time.struct_time((1970, 1, 1, 0, 0, 0, 3, 1, 0))
    monkeypatch.setattr(pisugar.time, "localtime", lambda: fake)

    pisugar._boot_init_sync()

    assert "rtc_rtc2pi" in sent
    assert "rtc_pi2rtc" not in sent


def test_boot_init_no_socket_is_noop(monkeypatch):
    monkeypatch.setattr(pisugar, "_find_pisugar_socket", lambda: None)
    pisugar._boot_init_sync()  # must not raise
