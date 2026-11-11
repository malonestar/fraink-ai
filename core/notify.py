"""Push notifications to the phone via a self-hosted ntfy server.

Replaces Telegram for voice memos (2026-11-07): the server is ours (a
homelab container, reached over Tailscale at https://ntfy.<tailnet>.ts.net), the text
never sits in a third-party chat service, and the ntfy app keeps a history
you can scroll back through and copy from (phone numbers, etc.).

Config (device config.yaml):

    ntfy:
      enabled: true
      url: "https://ntfy.your-tailnet.ts.net"
      topic: "memos"
      token: "tk_..."        # write-only token for the device user

Best-effort like everything else: failures log and return False.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger(__name__)

PUSH_TIMEOUT = 20


def _header_safe(value: str) -> str:
    """ntfy reads Title/Tags from HTTP headers, which must be latin-1."""
    return value.encode("latin-1", "replace").decode("latin-1").replace("\n", " ")


def build_push(cfg: dict, message: str, title: str = "", tags: str = "",
               priority: int = 0) -> tuple[str, dict, bytes] | None:
    """-> (url, headers, body) or None when push is disabled/unconfigured."""
    if not cfg.get("enabled", False):
        return None
    url = (cfg.get("url") or "").rstrip("/")
    topic = cfg.get("topic") or ""
    if not url or not topic:
        return None
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    token = cfg.get("token") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if title:
        headers["Title"] = _header_safe(title)
    if tags:
        headers["Tags"] = _header_safe(tags)
    if priority:
        headers["Priority"] = str(int(priority))
    body = (message or "(empty)").encode("utf-8")
    return f"{url}/{topic}", headers, body


async def push(cfg: dict, message: str, title: str = "", tags: str = "",
               priority: int = 0) -> bool:
    req = build_push(cfg, message, title=title, tags=tags, priority=priority)
    if req is None:
        log.debug("ntfy not configured, skipping push")
        return False
    url, headers, body = req
    try:
        timeout = aiohttp.ClientTimeout(total=PUSH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=body, headers=headers) as resp:
                if resp.status != 200:
                    log.warning("ntfy push failed: HTTP %d %s", resp.status,
                                (await resp.text())[:200])
                    return False
        log.info("ntfy push sent (%d chars)", len(body))
        return True
    except Exception as e:
        log.warning("ntfy push failed: %s", e)
        return False
