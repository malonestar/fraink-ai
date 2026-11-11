from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Bounded char class (not \S+) so a URL in prose like "...zzz." or "(...zzz)"
# doesn't swallow trailing punctuation. Tailscale auth URLs are path-only.
_LOGIN_RE = re.compile(r"https://login\.tailscale\.com/[A-Za-z0-9/_-]+")


@dataclass
class TailscaleStatus:
    backend_state: str = "NotInstalled"  # Running|Stopped|NeedsLogin|NoState|NotInstalled
    self_ip: Optional[str] = None
    self_dnsname: Optional[str] = None
    online: bool = False
    peer_count: int = 0


def parse_status(data: dict) -> TailscaleStatus:
    self_ = data.get("Self") or {}
    ips = self_.get("TailscaleIPs") or []
    ip = next((a for a in ips if a.startswith("100.")), ips[0] if ips else None)
    dns = (self_.get("DNSName") or "").rstrip(".") or None
    peers = data.get("Peer") or {}
    return TailscaleStatus(
        # "NoState" here (vs the dataclass's "NotInstalled" default): a parsed
        # payload means the daemon answered, just without a BackendState key.
        backend_state=data.get("BackendState", "NoState"),
        self_ip=ip,
        self_dnsname=dns,
        online=bool(self_.get("Online", False)),
        peer_count=len(peers),
    )


def extract_login_url(text: Optional[str]) -> Optional[str]:
    m = _LOGIN_RE.search(text or "")
    return m.group(0) if m else None


async def _run(cmd: list[str], timeout: float) -> Optional[subprocess.CompletedProcess]:
    """Run a command off the event loop. Returns None if the binary is missing
    or the call times out. Never raises."""
    try:
        return await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as e:
        # OSError covers FileNotFoundError/PermissionError/exec errors;
        # SubprocessError covers TimeoutExpired. The UI relies on never raising.
        log.warning("tailscale command failed (%s): %s", " ".join(cmd), e)
        return None


async def get_status() -> TailscaleStatus:
    proc = await _run(["tailscale", "status", "--json"], timeout=5)
    if proc is None:
        return TailscaleStatus(backend_state="NotInstalled")
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        # Non-JSON output: treat a clean exit as unknown, an error as not installed.
        return TailscaleStatus(backend_state="NoState" if proc.returncode == 0 else "NotInstalled")
    return parse_status(data)


async def up() -> Optional[str]:
    """Bring Tailscale up. Returns a login URL when authentication is required,
    else None.

    No --accept-routes: the companion only needs the xiaozhi server, reached on
    the LAN at home and via the AI server's tailnet IP when roaming. Accepting a
    subnet router's LAN route caused a hairpin (LAN traffic routed
    through the tailnet) that broke the home LAN path to the AI server."""
    proc = await _run(["sudo", "tailscale", "up"], timeout=30)
    if proc is None:
        return None
    return extract_login_url((proc.stdout or "") + "\n" + (proc.stderr or ""))


async def down() -> None:
    await _run(["sudo", "tailscale", "down"], timeout=10)
