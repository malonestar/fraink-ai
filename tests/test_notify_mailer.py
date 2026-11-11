import asyncio

from core import mailer, notify

NTFY = {"enabled": True, "url": "https://ntfy.example.ts.net/", "topic": "memos",
        "token": "tk_test"}


def test_push_disabled_or_unconfigured():
    assert notify.build_push({}, "x") is None
    assert notify.build_push({**NTFY, "enabled": False}, "x") is None
    assert notify.build_push({**NTFY, "topic": ""}, "x") is None
    assert asyncio.run(notify.push({}, "x")) is False


def test_push_request_shape():
    url, headers, body = notify.build_push(NTFY, "buy milk", title="Voice memo 3:04 PM",
                                           tags="studio_microphone")
    assert url == "https://ntfy.example.ts.net/memos"
    assert headers["Authorization"] == "Bearer tk_test"
    assert headers["Title"] == "Voice memo 3:04 PM"
    assert headers["Tags"] == "studio_microphone"
    assert body == b"buy milk"


def test_push_headers_are_latin1_safe():
    _, headers, body = notify.build_push(NTFY, "café \U0001F600", title="Memo — \U0001F600")
    headers["Title"].encode("latin-1")  # must not raise
    assert body.decode("utf-8").endswith("\U0001F600")


EMAIL = {"enabled": True, "smtp_host": "smtp.gmail.com", "smtp_port": 465,
         "username": "lab@example.com", "password": "app-pass",
         "to": "a@example.com, b@example.com"}


def test_mailer_configured():
    assert mailer.configured(EMAIL)
    assert not mailer.configured({**EMAIL, "password": ""})
    assert not mailer.configured({**EMAIL, "enabled": False})


def test_build_message_with_attachments():
    msg = mailer.build_message(EMAIL, "Meeting summary", "plain", "<p>hi</p>",
                               [("m.html", b"<html></html>"), ("m.md", b"# m")])
    assert msg["From"] == "lab@example.com"
    assert msg["To"] == "a@example.com, b@example.com"
    names = {p.get_filename(): p.get_content_type() for p in msg.iter_attachments()}
    assert names == {"m.html": "text/html", "m.md": "text/markdown"}
    assert any(p.get_content_type() == "text/html" for p in msg.walk()
               if not p.get_filename())


def test_send_email_unconfigured_returns_false():
    assert asyncio.run(mailer.send_email({}, "s", "t")) is False
