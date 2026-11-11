"""Meeting app: transcript building, block splitting, state machine."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import apps.meeting as meeting_mod
from apps.meeting import (
    MeetingApp, MTState, build_transcript, split_blocks, format_stamp,
    summary_messages, CHUNK_SECS,
)
from core.aiservices import Transcript


def _ctx():
    ctx = MagicMock()
    ctx.config = {"second_brain": {"enabled": False}}
    ctx.audio.level = 0.0
    return ctx


def _app(ctx=None):
    app = MeetingApp()
    app.on_enter(ctx or _ctx())
    return app


# --- pure logic -------------------------------------------------------------

def test_format_stamp():
    assert format_stamp(0) == "[00:00]"
    assert format_stamp(65) == "[01:05]"
    assert format_stamp(3661) == "[1:01:01]"


def test_build_transcript_skips_empty():
    segs = [(0.0, "hello there"), (45.0, ""), (90.0, "action item: ship it")]
    t = build_transcript(segs)
    assert t == "[00:00] hello there\n[01:30] action item: ship it"


def test_split_blocks_respects_line_boundaries():
    lines = [f"[{i:02d}:00] line {i} " + "x" * 80 for i in range(100)]
    blocks = split_blocks("\n".join(lines), block_chars=1000)
    assert len(blocks) > 1
    # no line is ever split across blocks
    rejoined = "\n".join(blocks).splitlines()
    assert rejoined == lines


def test_split_blocks_single_when_small():
    assert split_blocks("short transcript", block_chars=1000) == ["short transcript"]


def test_summary_prompt_has_sections():
    msgs = summary_messages("[00:00] we decided X")
    assert "## Summary" in msgs[0]["content"]
    assert "## Action Items" in msgs[0]["content"]
    assert msgs[1]["content"] == "[00:00] we decided X"


# --- app behavior -----------------------------------------------------------

def test_start_stop_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_mod, "MEETINGS_DIR", str(tmp_path))
    ctx = _ctx()
    app = _app(ctx)

    async def scenario():
        app._start_meeting()
        assert app.state == MTState.RECORDING
        ctx.audio.start_raw_capture.assert_called_once()

        # simulate 2 chunks of captured speech
        app._ai = MagicMock()
        app._ai.transcribe = AsyncMock(
            return_value=Transcript(text="we agreed to ship v5", language="en"))
        app._ai.chat = AsyncMock(
            return_value="## Summary\n- shipped v5\n## Action Items\n- none")
        app._shipper.submit = AsyncMock()

        app._pcm = [b"\x00\x01" * 100]
        await app.update(CHUNK_SECS + 0.1)  # rolls the first chunk
        app._pcm = [b"\x02\x03" * 100]

        app._stop_meeting()
        assert app.state == MTState.SUMMARIZING
        # let the stt worker and finish task run
        for _ in range(50):
            await asyncio.sleep(0.01)
            if app.state == MTState.DONE:
                break
        assert app.state == MTState.DONE
        assert "shipped v5" in app.summary
        app._shipper.submit.assert_awaited_once()

    asyncio.run(scenario())
    # note saved on disk with summary + transcript
    files = list(tmp_path.glob("*-meeting.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "## Transcript" in content
    assert "we agreed to ship v5" in content


def test_discard_drops_everything():
    app = _app()

    async def scenario():
        app._start_meeting()
        app._pcm = [b"\x00\x01"]
        app.on_secondary("long")

    asyncio.run(scenario())
    assert app.state == MTState.READY
    assert app.segments == []
    assert app.status_msg == "Discarded"


def test_no_speech_returns_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_mod, "MEETINGS_DIR", str(tmp_path))
    app = _app()

    async def scenario():
        app._start_meeting()
        app._ai = MagicMock()
        app._ai.transcribe = AsyncMock(return_value=Transcript(text="", language="en"))
        app._pcm = [b"\x00\x01"]
        app._stop_meeting()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if app.state == MTState.READY:
                break

    asyncio.run(scenario())
    assert app.state == MTState.READY
    assert app.status_msg == "No speech captured"
    assert list(tmp_path.glob("*.md")) == []


def test_summarizer_down_still_saves_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_mod, "MEETINGS_DIR", str(tmp_path))
    app = _app()

    async def scenario():
        app._start_meeting()
        app._ai = MagicMock()
        app._ai.transcribe = AsyncMock(
            return_value=Transcript(text="important facts", language="en"))
        app._ai.chat = AsyncMock(return_value=None)  # qwen unreachable
        app._pcm = [b"\x00\x01"]
        app._stop_meeting()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if app.state == MTState.READY:
                break

    asyncio.run(scenario())
    assert app.state == MTState.READY
    files = list(tmp_path.glob("*-meeting.md"))
    assert len(files) == 1
    assert "important facts" in files[0].read_text()


def test_keep_awake_only_while_summarizing():
    app = MeetingApp()
    assert app.keep_awake is False
    app.state = MTState.RECORDING
    assert app.keep_awake is False  # long meetings may dim the screen
    app.state = MTState.SUMMARIZING
    assert app.keep_awake is True
