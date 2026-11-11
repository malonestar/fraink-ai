import asyncio

import pytest

from core import tailscale
from core.tailscale import TailscaleStatus
from core.context import AppContext
from core.display import Display
from apps.tailscale_manager import (
    TailscaleManagerApp, TSState, ACTIONS, REFRESH_INTERVAL,
)


def _app():
    app = TailscaleManagerApp()
    app.on_enter(AppContext(display=Display(headless=True), config={}))
    return app


@pytest.mark.asyncio
async def test_refresh_caches_status(monkeypatch):
    async def fake_status():
        return TailscaleStatus(backend_state="Running", self_ip="100.1.2.3", peer_count=2)
    monkeypatch.setattr(tailscale, "get_status", fake_status)
    app = _app()
    await app.refresh()
    assert app.status.backend_state == "Running"
    assert app.status.self_ip == "100.1.2.3"


def test_menu_navigation_wraps():
    app = _app()
    assert app.highlight == 0
    for _ in range(len(ACTIONS)):
        app.on_button("single")
    assert app.highlight == 0  # wrapped


@pytest.mark.asyncio
async def test_connect_shows_auth_url_when_needs_login(monkeypatch):
    async def fake_up():
        return "https://login.tailscale.com/a/zzz"
    monkeypatch.setattr(tailscale, "up", fake_up)
    app = _app()
    await app._do_connect()
    assert app.state == TSState.AUTH
    assert app.auth_url == "https://login.tailscale.com/a/zzz"
    assert app.busy_label == ""


@pytest.mark.asyncio
async def test_connect_refreshes_when_already_authed(monkeypatch):
    async def fake_up():
        return None
    async def fake_status():
        return TailscaleStatus(backend_state="Running")
    monkeypatch.setattr(tailscale, "up", fake_up)
    monkeypatch.setattr(tailscale, "get_status", fake_status)
    app = _app()
    await app._do_connect()
    assert app.state == TSState.STATUS
    assert app.status.backend_state == "Running"


@pytest.mark.asyncio
async def test_disconnect_calls_down_then_refreshes(monkeypatch):
    calls = []
    async def fake_down():
        calls.append("down")
    async def fake_status():
        return TailscaleStatus(backend_state="Stopped")
    monkeypatch.setattr(tailscale, "down", fake_down)
    monkeypatch.setattr(tailscale, "get_status", fake_status)
    app = _app()
    await app._do_disconnect()
    assert calls == ["down"]
    assert app.status.backend_state == "Stopped"


@pytest.mark.asyncio
async def test_secondary_on_connect_starts_connect_op(monkeypatch):
    captured = []
    monkeypatch.setattr(TailscaleManagerApp, "_start", lambda self, coro: captured.append(coro))
    async def fake_up():
        return "https://login.tailscale.com/a/zzz"
    monkeypatch.setattr(tailscale, "up", fake_up)
    app = _app()
    app.highlight = 0  # "Connect"
    assert ACTIONS[0][1] == "connect"
    app.on_secondary("long")
    assert len(captured) == 1
    await captured[0]  # drive the captured coroutine
    assert app.state == TSState.AUTH


def test_secondary_back_from_auth_returns_to_status(monkeypatch):
    monkeypatch.setattr(TailscaleManagerApp, "_start", lambda self, coro: coro.close())
    app = _app()
    app.state = TSState.AUTH
    app.auth_url = "https://login.tailscale.com/a/zzz"
    app.on_secondary("long")
    assert app.state == TSState.STATUS


@pytest.mark.asyncio
async def test_update_skips_auto_refresh_while_op_in_flight(monkeypatch):
    calls = []
    async def fake_status():
        calls.append(1)
        return TailscaleStatus(backend_state="Running")
    monkeypatch.setattr(tailscale, "get_status", fake_status)
    app = _app()

    # Busy op in flight -> auto-refresh gated out.
    app.busy_label = "Connecting..."
    await app.update(REFRESH_INTERVAL + 1)
    assert calls == []

    # Scheduled task still running -> still gated out.
    app.busy_label = ""
    app._task = asyncio.create_task(asyncio.sleep(0.05))
    await app.update(REFRESH_INTERVAL + 1)
    assert calls == []
    await app._task

    # Idle -> auto-refresh runs.
    await app.update(REFRESH_INTERVAL + 1)
    assert calls == [1]


def test_draw_does_not_raise_in_all_states():
    app = _app()
    d = app.ctx.display
    app.draw(d)                                   # STATUS, status is None
    app.status = TailscaleStatus(backend_state="Running", self_ip="100.1.2.3",
                                 self_dnsname="pi-sugar.example-tailnet.ts.net", peer_count=3)
    app.draw(d)                                   # STATUS with data
    app.busy_label = "Connecting..."
    app.draw(d)                                   # BUSY
    app.busy_label = ""
    app.state = TSState.AUTH
    app.auth_url = "https://login.tailscale.com/a/zzz"
    app.draw(d)                                   # AUTH
