"""Send email over SMTP (Gmail app password), used for meeting summaries.

Config (device config.yaml):

    email:
      enabled: true
      smtp_host: "smtp.gmail.com"
      smtp_port: 465                 # 465 = implicit TLS, 587 = STARTTLS
      username: "sender@gmail.com"
      password: "app-password"       # Google app password, not the login
      from: "PiSugar <sender@gmail.com>"   # optional, defaults to username
      to: ["you@example.com"]

smtplib is blocking, so sends run in a worker thread. Best-effort: failures
log and return False, the meeting files stay on the device either way.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

log = logging.getLogger(__name__)

SMTP_TIMEOUT = 60


def _recipients(cfg: dict) -> list[str]:
    to = cfg.get("to") or []
    if isinstance(to, str):
        to = [t.strip() for t in to.split(",")]
    return [t for t in to if t]


def build_message(cfg: dict, subject: str, text: str, html: str = "",
                  attachments: list[tuple[str, bytes]] | None = None) -> EmailMessage:
    """attachments = [(filename, content_bytes), ...]; type guessed from the name."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("from") or cfg.get("username", "")
    msg["To"] = ", ".join(_recipients(cfg))
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="pisugar.local")
    msg.set_content(text or "(see HTML part)")
    if html:
        msg.add_alternative(html, subtype="html")
    for name, data in attachments or []:
        ctype, _ = mimetypes.guess_type(name)
        if name.endswith(".md"):
            ctype = "text/markdown"
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg


def configured(cfg: dict) -> bool:
    return bool(cfg.get("enabled", False) and cfg.get("username")
                and cfg.get("password") and _recipients(cfg))


def _send_blocking(cfg: dict, msg: EmailMessage) -> None:
    host = cfg.get("smtp_host", "smtp.gmail.com")
    port = int(cfg.get("smtp_port", 465))
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=SMTP_TIMEOUT) as s:
            s.login(cfg["username"], cfg["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as s:
            s.starttls(context=ctx)
            s.login(cfg["username"], cfg["password"])
            s.send_message(msg)


async def send_email(cfg: dict, subject: str, text: str, html: str = "",
                     attachments: list[tuple[str, bytes]] | None = None) -> bool:
    if not configured(cfg):
        log.debug("Email not configured, skipping")
        return False
    msg = build_message(cfg, subject, text, html, attachments)
    try:
        await asyncio.to_thread(_send_blocking, cfg, msg)
        log.info("Email sent: %s", subject)
        return True
    except Exception as e:
        log.error("Email send failed: %s", e)
        return False
