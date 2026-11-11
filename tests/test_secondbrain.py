from datetime import datetime

from core.secondbrain import slugify, build_candidate


def test_slugify_basic():
    assert slugify("Buy milk and eggs") == "buy_milk_and_eggs"
    assert slugify("  Café déjà vu!! ") == "cafe_deja_vu"
    assert slugify("") == "memo"
    assert len(slugify("word " * 40)) <= 60


def test_build_candidate_filename_and_frontmatter():
    now = datetime(2026, 6, 3, 14, 30, 5)
    fname, md = build_candidate(
        text="Remember to call the dentist tomorrow",
        now=now, scope="voice_memos", duration_secs=7.0,
        audio_filename="2026-06-03_14-30-05.opus",
    )
    assert fname == "2026-06-03-1430-voice_memos-remember_to_call_the_dentist.md"
    assert md.startswith("---\n")
    assert "type: reference" in md
    assert "scope: voice_memos" in md
    assert "source: pisugar-voice-memo" in md
    assert "status: candidate" in md
    assert "**VOICE MEMO:**" in md
    assert "Remember to call the dentist tomorrow" in md
    # Real schema reconciliation: candidate notes carry a session_id between
    # source and status; for voice memos the audio file stem is the session id.
    assert "session_id: 2026-06-03_14-30-05" in md
    # frontmatter field ordering matches the vault convention
    src = md.index("source:")
    sid = md.index("session_id:")
    sts = md.index("status:")
    assert src < sid < sts


def test_build_candidate_empty_text_is_safe():
    fname, md = build_candidate("", now=datetime(2026, 1, 1, 0, 0, 0),
                                scope="voice_memos", duration_secs=0.0,
                                audio_filename="x.opus")
    assert fname.endswith("-voice_memos-memo.md")
    assert "**VOICE MEMO:**" in md


import asyncio

from core.secondbrain import SecondBrainShipper


def _cfg(tmp, enabled=True):
    return {"enabled": enabled, "host": "h", "user": "u",
            "inbox_path": "/inbox", "ssh_key": "~/k", "scope": "voice_memos",
            "_outbox_dir": str(tmp)}   # test-only override


def test_enqueue_always_writes_outbox_even_when_disabled(tmp_path):
    s = SecondBrainShipper(_cfg(tmp_path, enabled=False))
    asyncio.run(s.submit("a.md", "body"))
    assert (tmp_path / "a.md").read_text() == "body"


def test_successful_ship_removes_from_outbox(tmp_path):
    sent = []
    async def ok(cmd): sent.append(cmd); return 0
    s = SecondBrainShipper(_cfg(tmp_path), runner=ok)
    asyncio.run(s.submit("b.md", "body"))
    assert sent and "scp" in sent[0][0]
    assert not (tmp_path / "b.md").exists()      # shipped -> removed


def test_failed_ship_keeps_file_and_retries_later(tmp_path):
    calls = []
    async def flaky(cmd):
        calls.append(cmd)
        return 0 if len(calls) > 1 else 1        # fail first, succeed second
    s = SecondBrainShipper(_cfg(tmp_path), runner=flaky)
    asyncio.run(s.submit("c.md", "body"))
    assert (tmp_path / "c.md").exists()          # first attempt failed -> kept
    asyncio.run(s.flush())                        # retry
    assert not (tmp_path / "c.md").exists()
