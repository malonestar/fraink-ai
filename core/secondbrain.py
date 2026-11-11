# Schema reconciliation (vs plan template, against the basic-memory vault schema
# + real _inbox examples): real candidate notes carry a `session_id` line between `source` and
# `status`; voice memos have no Claude session so we use the audio file stem as the session id.
# Field order: name / description / type / scope / source / session_id / status: candidate.
from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional
from core.paths import app_path

SOURCE = "pisugar-voice-memo"

log = logging.getLogger(__name__)

DEFAULT_OUTBOX = app_path("notes", "_outbox")


def slugify(text: str, max_len: int = 60) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    norm = norm.lower()
    words = re.findall(r"[a-z0-9]+", norm)
    slug = "_".join(words)[:max_len].strip("_")
    return slug or "memo"


def build_candidate(text: str, now: datetime, scope: str,
                    duration_secs: float, audio_filename: str) -> tuple[str, str]:
    short_slug = "_".join(slugify(text).split("_")[:5]) or "memo"
    fname = f"{now:%Y-%m-%d-%H%M}-{scope}-{short_slug}.md"
    dur = f"{int(duration_secs // 60):d}m{int(duration_secs % 60):02d}s"
    name = (text.strip().split("\n")[0][:70] or "Voice memo")
    session_id = audio_filename.rsplit(".", 1)[0]
    md = (
        "---\n"
        f"name: Voice memo — {name}\n"
        f"description: Voice memo captured on PiSugar ({now:%Y-%m-%d %H:%M})\n"
        "type: reference\n"
        f"scope: {scope}\n"
        f"source: {SOURCE}\n"
        f"session_id: {session_id}\n"
        "status: candidate\n"
        "---\n\n"
        f"**VOICE MEMO:** {text.strip()}\n\n"
        f"*Captured {now:%Y-%m-%dT%H:%M:%S} · {dur} · audio: {audio_filename}*\n"
    )
    return fname, md


async def _scp_runner(cmd: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        log.warning("scp failed (%s): %s", proc.returncode,
                    err.decode("utf-8", "replace")[:200])
    # None (process state unknown) is treated as a failure so the memo is kept.
    return proc.returncode if proc.returncode is not None else 1


class SecondBrainShipper:
    def __init__(self, cfg: dict,
                 runner: Optional[Callable[[list[str]], Awaitable[int]]] = None):
        self.enabled = bool(cfg.get("enabled", False))
        self.host = cfg.get("host", "")
        self.user = cfg.get("user", "")
        self.inbox = cfg.get("inbox_path", "")
        self.key = os.path.expanduser(cfg.get("ssh_key", "~/.ssh/id_ed25519"))
        self.outbox = Path(cfg.get("_outbox_dir", DEFAULT_OUTBOX))
        self._run = runner or _scp_runner

    def _scp_cmd(self, local: Path) -> list[str]:
        target = f"{self.user}@{self.host}:{self.inbox}/"
        return ["scp", "-i", self.key, "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=5", str(local), target]

    async def submit(self, filename: str, markdown: str):
        """Write to outbox (never lost), then try to ship immediately."""
        self.outbox.mkdir(parents=True, exist_ok=True)
        path = self.outbox / filename
        path.write_text(markdown)
        await self._try_ship(path)

    async def _try_ship(self, path: Path):
        if not (self.enabled and self.host and self.user):
            return
        rc = await self._run(self._scp_cmd(path))
        if rc == 0:
            try:
                path.unlink()
            except OSError:
                pass

    async def flush(self):
        """Retry every queued file (call periodically / on reconnect)."""
        if not self.outbox.exists():
            return
        for p in sorted(self.outbox.glob("*.md")):
            await self._try_ship(p)
