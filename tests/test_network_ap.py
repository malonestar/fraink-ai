import subprocess
from types import SimpleNamespace

from core.network import NetworkManager


def test_scan_parses_nmcli_terse(monkeypatch):
    out = (r"Home\:Net:78:WPA2" "\nmalonestar iPhone:65:WPA2\nmalonestar iPhone:40:WPA2\n"
           ":30:WPA2\nCafe:55:\n")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout=out, returncode=0))
    nets = NetworkManager._scan_now()
    assert [n["ssid"] for n in nets] == ["Home:Net", "malonestar iPhone", "Cafe"]
    assert nets[1]["signal"] == 65            # strongest duplicate wins
    assert nets[2]["secure"] is False         # open network


def test_scan_uses_cache_while_ap_up():
    nm = NetworkManager({})
    nm._ap_active, nm._scan_cache = True, [{"ssid": "X", "signal": 50, "secure": True}]
    assert nm.scan_networks() == [{"ssid": "X", "signal": 50, "secure": True}]


def test_save_credentials_writes_keyfile_without_password_on_argv(monkeypatch):
    runs, nm_calls = [], []
    def fake_run(args, **kw):
        runs.append((args, kw.get("input")))
        return SimpleNamespace(returncode=0, stderr="", stdout="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(NetworkManager, "_nmcli",
                        staticmethod(lambda *a, **k: nm_calls.append(a) or
                                     SimpleNamespace(returncode=0, stderr="", stdout="")))
    nm = NetworkManager({})
    assert nm.save_wifi_credentials("malonestar iPhone", "pw;123")
    tee_args, tee_input = runs[0]
    assert tee_args[:2] == ["sudo", "tee"] and tee_args[2].endswith(".nmconnection")
    assert all("pw;123" not in str(a) for a, _ in runs)          # never on a command line
    assert all("pw;123" not in " ".join(c) for c in nm_calls)
    assert r"psk=pw\;123" in tee_input and "ssid=malonestar iPhone" in tee_input
    assert ("connection", "reload") in nm_calls
    assert nm._pending_ssid == "malonestar iPhone"


def test_open_network_keyfile_has_no_security_block():
    from core.network import build_keyfile
    kf = build_keyfile("Cafe", "")
    assert "[wifi-security]" not in kf and "ssid=Cafe" in kf
