import asyncio
import enum
import logging
import os
import socket
import time
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

PISUGAR_SOCKET_CANDIDATES = (
    "/tmp/pisugar-server.sock",
    "/run/pisugar-server.sock",
)
TRIGGER_DIR = "/tmp/pisugar-companion"


class ButtonEvent(enum.Enum):
    SINGLE = "single"
    DOUBLE = "double"
    LONG = "long"


LONG_PRESS_SEC = 0.6
DOUBLE_TAP_SEC = 0.25
DEBOUNCE_SEC = 0.025


class TapClassifier:
    """Classifies raw press/release timestamps into SINGLE/DOUBLE/LONG.

    Time is injected (no clock dependency) so the logic is fully testable.
    Call press()/release() on raw edges and poll() periodically to flush
    time-based decisions (long-press while held, single after the double
    window lapses). Emitted events are collected and read via drain().
    """

    def __init__(self, *, long_press_sec: float = LONG_PRESS_SEC,
                 double_tap_sec: float = DOUBLE_TAP_SEC,
                 debounce_sec: float = DEBOUNCE_SEC):
        self.long_press_sec = long_press_sec
        self.double_tap_sec = double_tap_sec
        self.debounce_sec = debounce_sec
        self.double_enabled = False
        self._pressed = False
        self._press_t = 0.0
        self._long_fired = False
        self._pending_single_t: Optional[float] = None
        self._await_double_release = False
        self._out: list[ButtonEvent] = []

    def press(self, t: float):
        if (self.double_enabled and self._pending_single_t is not None
                and (t - self._pending_single_t) <= self.double_tap_sec):
            # Second tap of a potential double — resolve on its release.
            self._await_double_release = True
            self._pending_single_t = None
        self._pressed = True
        self._press_t = t
        self._long_fired = False

    def release(self, t: float):
        if not self._pressed:
            return
        self._pressed = False
        hold = t - self._press_t
        if hold < self.debounce_sec:
            self._await_double_release = False
            return
        if self._long_fired:
            self._long_fired = False
            self._await_double_release = False
            return
        if hold >= self.long_press_sec:
            self._out.append(ButtonEvent.LONG)
            self._await_double_release = False
            self._pending_single_t = None
            return
        if self._await_double_release:
            self._await_double_release = False
            self._out.append(ButtonEvent.DOUBLE)
            return
        if self.double_enabled:
            self._pending_single_t = t
        else:
            self._out.append(ButtonEvent.SINGLE)

    def poll(self, t: float):
        if (self._pressed and not self._long_fired
                and (t - self._press_t) >= self.long_press_sec):
            self._out.append(ButtonEvent.LONG)
            self._long_fired = True
        if (self._pending_single_t is not None
                and (t - self._pending_single_t) > self.double_tap_sec):
            self._pending_single_t = None
            self._out.append(ButtonEvent.SINGLE)

    def drain(self) -> list[ButtonEvent]:
        out = self._out
        self._out = []
        return out


def parse_pisugar_event(data: str) -> Optional[ButtonEvent]:
    cleaned = data.strip().lower()
    try:
        return ButtonEvent(cleaned)
    except ValueError:
        return None


def _pisugar_request(sock_path: str, command: str, timeout: float = 1.5) -> Optional[str]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(sock_path)
            s.sendall((command.strip() + "\n").encode())
            chunks = []
            while True:
                try:
                    data = s.recv(4096)
                except socket.timeout:
                    break
                if not data:
                    break
                chunks.append(data)
                if b"\n" in data:
                    break
            if chunks:
                return b"".join(chunks).decode("utf-8", "replace").strip()
    except Exception:
        return None
    return None


def _find_pisugar_socket() -> Optional[str]:
    for path in PISUGAR_SOCKET_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


# The WhisPlay button (read raw via core.display) is the primary input and
# classifies its own single/double/long in software. The PiSugar button is the
# secondary back/home + power gesture, so it only needs single + long. Leaving
# the server's double-tap enabled would force it to wait out the (hardware,
# ~300ms) double-tap window before firing single — pure latency for no benefit.
PISUGAR_BUTTON_EVENTS = (("single", "1"), ("double", "0"), ("long", "1"))


def _setup_button_triggers(sock_path: str):
    os.makedirs(TRIGGER_DIR, exist_ok=True)
    for event, enable in PISUGAR_BUTTON_EVENTS:
        resp = _pisugar_request(sock_path, f"set_button_enable {event} {enable}")
        log.debug("PiSugar enable %s -> %s", event, resp)
        if enable == "1":
            trigger = os.path.join(TRIGGER_DIR, event)
            resp = _pisugar_request(sock_path, f"set_button_shell {event} touch {trigger}")
            log.debug("PiSugar shell %s -> %s", event, resp)
    log.info("PiSugar button triggers configured -> %s", TRIGGER_DIR)


def get_battery_level() -> int:
    sock_path = _find_pisugar_socket()
    if not sock_path:
        return -1
    resp = _pisugar_request(sock_path, "get battery")
    if resp:
        try:
            val = float(resp.split(":")[-1].strip())
            return min(100, max(0, int(val)))
        except (ValueError, IndexError):
            pass
    return -1


ButtonCallback = Callable[[ButtonEvent, str], Awaitable[None]]


class ButtonListener:
    """Watches the PiSugar server's trigger files (secondary button)."""

    WATCHED = ("single", "long")

    def __init__(self, callback: ButtonCallback,
                 poll_interval: float = 0.08, source: str = "pisugar"):
        self._callback = callback
        self._poll_interval = poll_interval
        self._source = source
        self._task: Optional[asyncio.Task] = None
        self._mtimes: dict[str, float] = {}

    async def start(self):
        sock_path = _find_pisugar_socket()
        if sock_path:
            _setup_button_triggers(sock_path)
        else:
            log.warning("PiSugar socket not found — secondary button disabled")

        for event in self.WATCHED:
            path = os.path.join(TRIGGER_DIR, event)
            try:
                self._mtimes[event] = os.path.getmtime(path)
            except OSError:
                self._mtimes[event] = 0.0

        self._task = asyncio.create_task(self._poll_loop())

    async def _emit(self, event: ButtonEvent):
        log.info("PiSugar button: %s", event.value)
        await self._callback(event, self._source)

    async def _poll_loop(self):
        while True:
            try:
                for event_name in self.WATCHED:
                    path = os.path.join(TRIGGER_DIR, event_name)
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    if mtime > self._mtimes.get(event_name, 0.0):
                        self._mtimes[event_name] = mtime
                        event = parse_pisugar_event(event_name)
                        if event:
                            await self._emit(event)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug("Button poll error: %s", e)
            await asyncio.sleep(self._poll_interval)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


class WhisPlayButton:
    """Primary input: raw press/release from the WhisPlay GPIO button, with
    software single/double/long classification (see TapClassifier).

    The board's runtime fires press/release from its own polling thread, so
    those are bounced into the asyncio loop via call_soon_threadsafe. A light
    poll task flushes time-based decisions (long-press, single-vs-double).
    """

    def __init__(self, display, callback: ButtonCallback, *,
                 source: str = "whisplay", poll_interval: float = 0.02,
                 long_press_sec: float = LONG_PRESS_SEC,
                 double_tap_sec: float = DOUBLE_TAP_SEC,
                 debounce_sec: float = DEBOUNCE_SEC):
        self._display = display
        self._callback = callback
        self._source = source
        self._poll_interval = poll_interval
        self._clf = TapClassifier(long_press_sec=long_press_sec,
                                  double_tap_sec=double_tap_sec,
                                  debounce_sec=debounce_sec)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None

    @property
    def double_enabled(self) -> bool:
        return self._clf.double_enabled

    @double_enabled.setter
    def double_enabled(self, value: bool):
        self._clf.double_enabled = bool(value)

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._display.set_raw_button_callbacks(
            on_press=lambda: self._loop.call_soon_threadsafe(self._on_press),
            on_release=lambda: self._loop.call_soon_threadsafe(self._on_release),
        )
        self._task = asyncio.create_task(self._poll_loop())
        log.info("WhisPlay button listener started")

    def _on_press(self):
        self._clf.press(time.monotonic())
        self._dispatch()

    def _on_release(self):
        self._clf.release(time.monotonic())
        self._dispatch()

    def _dispatch(self):
        for event in self._clf.drain():
            log.info("WhisPlay button: %s", event.value)
            self._loop.create_task(self._callback(event, self._source))

    async def _poll_loop(self):
        while True:
            try:
                self._clf.poll(time.monotonic())
                self._dispatch()
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug("WhisPlay button poll error: %s", e)
            await asyncio.sleep(self._poll_interval)

    async def stop(self):
        self._display.set_raw_button_callbacks(None, None)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
