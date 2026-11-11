"""PiSugar 3 power-management helpers beyond the button.

Talks to pisugar-server over its Unix socket (same channel core/button.py
uses for trigger setup). Everything here is synchronous socket I/O with up
to a 1.5s timeout per command — call the sync functions via
asyncio.to_thread, or use the provided async wrappers, never directly from
the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Tuple

from core.button import _find_pisugar_socket, _pisugar_request

log = logging.getLogger(__name__)

# Below this % (and not on charger) pisugar-server powers the Pi off cleanly
# instead of letting the battery hard-cut mid-write.
SAFE_SHUTDOWN_LEVEL = 5
SAFE_SHUTDOWN_DELAY = 30

# System clocks before this year mean "no NTP yet" (Pi has no battery-backed
# system clock) — restore from the PiSugar's hardware RTC instead.
CLOCK_VALID_YEAR = 2024


def _parse_bool(resp: str | None) -> bool:
    return bool(resp) and resp.split(":")[-1].strip().lower() == "true"


def get_battery_info() -> Tuple[int, bool]:
    """(battery %, on external power). (-1, False) when unavailable.

    Blocking — call via asyncio.to_thread.
    """
    sock_path = _find_pisugar_socket()
    if not sock_path:
        return -1, False
    pct = -1
    resp = _pisugar_request(sock_path, "get battery")
    if resp:
        try:
            pct = min(100, max(0, int(float(resp.split(":")[-1].strip()))))
        except (ValueError, IndexError):
            pass
    plugged = _parse_bool(_pisugar_request(sock_path, "get battery_power_plugged"))
    if not plugged:
        # Older firmware doesn't report power_plugged; charging implies power.
        plugged = _parse_bool(_pisugar_request(sock_path, "get battery_charging"))
    return pct, plugged


async def get_battery_info_async() -> Tuple[int, bool]:
    return await asyncio.to_thread(get_battery_info)


def _boot_init_sync():
    sock_path = _find_pisugar_socket()
    if not sock_path:
        log.warning("PiSugar socket not found — power init skipped")
        return

    # RTC <-> system clock. The PiSugar 3 has a battery-backed RTC the Pi
    # lacks: if the system clock is obviously wrong (no NTP, e.g. roaming),
    # restore from the RTC; if it's valid (NTP got there first), push it to
    # the RTC so the RTC stays fresh for the next offline boot.
    if time.localtime().tm_year < CLOCK_VALID_YEAR:
        resp = _pisugar_request(sock_path, "rtc_rtc2pi")
        log.info("System clock invalid — restored from PiSugar RTC (%s)", resp)
    else:
        resp = _pisugar_request(sock_path, "rtc_pi2rtc")
        log.info("PiSugar RTC synced from system clock (%s)", resp)

    # Safe low-battery shutdown: clean poweroff instead of a hard cut at 0%.
    resp = _pisugar_request(sock_path, f"set_safe_shutdown_level {SAFE_SHUTDOWN_LEVEL}")
    log.info("PiSugar safe_shutdown_level=%d (%s)", SAFE_SHUTDOWN_LEVEL, resp)
    resp = _pisugar_request(sock_path, f"set_safe_shutdown_delay {SAFE_SHUTDOWN_DELAY}")
    log.info("PiSugar safe_shutdown_delay=%d (%s)", SAFE_SHUTDOWN_DELAY, resp)


async def boot_init():
    """RTC sync + safe-shutdown config. Never raises (best-effort at boot)."""
    try:
        await asyncio.to_thread(_boot_init_sync)
    except Exception as e:
        log.warning("PiSugar boot init failed: %s", e)
