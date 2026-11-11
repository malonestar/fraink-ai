import json
import types

import pytest

from core import tailscale
from core.tailscale import TailscaleStatus, parse_status, extract_login_url


def test_parse_status_running():
    data = {
        "BackendState": "Running",
        "Self": {
            "TailscaleIPs": ["100.101.102.103", "fd7a::1"],
            "DNSName": "pi-sugar.example-tailnet.ts.net.",
            "Online": True,
        },
        "Peer": {"abc": {}, "def": {}},
    }
    st = parse_status(data)
    assert st.backend_state == "Running"
    assert st.self_ip == "100.101.102.103"
    assert st.self_dnsname == "pi-sugar.example-tailnet.ts.net"
    assert st.online is True
    assert st.peer_count == 2


def test_parse_status_needs_login_no_peers():
    data = {"BackendState": "NeedsLogin", "Self": {"TailscaleIPs": [], "Online": False}}
    st = parse_status(data)
    assert st.backend_state == "NeedsLogin"
    assert st.self_ip is None
    assert st.self_dnsname is None
    assert st.online is False
    assert st.peer_count == 0


def test_parse_status_missing_self_defaults():
    st = parse_status({})
    assert st.backend_state == "NoState"
    assert st.self_ip is None
    assert st.peer_count == 0


def test_parse_status_falls_back_to_first_ip_when_no_cgnat():
    data = {"BackendState": "Running", "Self": {"TailscaleIPs": ["fd7a::1"]}}
    st = parse_status(data)
    assert st.self_ip == "fd7a::1"


def test_extract_login_url_strips_trailing_punctuation():
    assert extract_login_url("(see https://login.tailscale.com/a/zzz).") == \
        "https://login.tailscale.com/a/zzz"


def test_extract_login_url_found():
    out = "\nTo authenticate, visit:\n\n\thttps://login.tailscale.com/a/1a2b3c4d5e\n\n"
    assert extract_login_url(out) == "https://login.tailscale.com/a/1a2b3c4d5e"


def test_extract_login_url_absent():
    assert extract_login_url("Success.\n") is None


def test_extract_login_url_none_input():
    assert extract_login_url(None) is None


def _proc(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.mark.asyncio
async def test_get_status_not_installed(monkeypatch):
    async def fake_run(cmd, timeout):
        return None  # binary missing / timeout
    monkeypatch.setattr(tailscale, "_run", fake_run)
    st = await tailscale.get_status()
    assert st.backend_state == "NotInstalled"


@pytest.mark.asyncio
async def test_get_status_parses_running(monkeypatch):
    payload = json.dumps({
        "BackendState": "Running",
        "Self": {"TailscaleIPs": ["100.1.2.3"], "DNSName": "x.ts.net.", "Online": True},
        "Peer": {"a": {}},
    })
    async def fake_run(cmd, timeout):
        assert cmd == ["tailscale", "status", "--json"]
        return _proc(returncode=0, stdout=payload)
    monkeypatch.setattr(tailscale, "_run", fake_run)
    st = await tailscale.get_status()
    assert st.backend_state == "Running"
    assert st.self_ip == "100.1.2.3"
    assert st.peer_count == 1


@pytest.mark.asyncio
async def test_get_status_parses_json_even_on_nonzero_exit(monkeypatch):
    # `tailscale status --json` exits non-zero when logged out but still emits JSON.
    payload = json.dumps({"BackendState": "NeedsLogin", "Self": {}})
    async def fake_run(cmd, timeout):
        return _proc(returncode=1, stdout=payload)
    monkeypatch.setattr(tailscale, "_run", fake_run)
    st = await tailscale.get_status()
    assert st.backend_state == "NeedsLogin"


@pytest.mark.asyncio
async def test_up_returns_login_url_from_stderr(monkeypatch):
    async def fake_run(cmd, timeout):
        assert cmd == ["sudo", "tailscale", "up"]
        return _proc(returncode=0, stdout="", stderr="visit https://login.tailscale.com/a/zzz\n")
    monkeypatch.setattr(tailscale, "_run", fake_run)
    url = await tailscale.up()
    assert url == "https://login.tailscale.com/a/zzz"


@pytest.mark.asyncio
async def test_up_returns_none_when_already_authed(monkeypatch):
    async def fake_run(cmd, timeout):
        return _proc(returncode=0, stdout="Success.\n", stderr="")
    monkeypatch.setattr(tailscale, "_run", fake_run)
    assert await tailscale.up() is None


@pytest.mark.asyncio
async def test_down_issues_command(monkeypatch):
    calls = []
    async def fake_run(cmd, timeout):
        calls.append(cmd)
        return _proc(returncode=0)
    monkeypatch.setattr(tailscale, "_run", fake_run)
    await tailscale.down()
    assert calls == [["sudo", "tailscale", "down"]]
