"""Meeting recorder: long-form listen -> transcript + speakers -> summary -> email.

Records raw PCM continuously and streams it to speaches faster-whisper in
~45s chunks (transcribed while the next chunk records, so memory stays flat
and the transcript is ready moments after the meeting ends). Every chunk is
also appended to a WAV file on the SD card.

On stop:
  1. the whole WAV goes to the diarization service on the AI server ("who spoke
     when"), and each whisper segment is labelled Speaker 1, Speaker 2, ...
  2. the labelled transcript goes to the summarizer (qwen3.8-27b on the LLM server)
     for a summary, action items and a best guess at who each speaker is
  3. a formatted report (summary + full transcript) is saved as .md + .html,
     shipped to the second-brain inbox, and emailed with the report attached
  4. an optional one-line ntfy ping says the email went out (no content)

Telegram is no longer used for meetings (2026-11-07): long transcripts and
to-dos belong in email, not a chat app.

Buttons: single = start/stop meeting, PiSugar long = discard (no summary).
The screensaver is allowed during recording (long meetings; the green LED
is the recording indicator) but blocked while processing.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import wave
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from apps.base import AppBase
from apps.meeting.report import (
    Line, assign_speakers, clean_summary, extract_title, fmt_duration, merge_turns,
    render_html, slugify,
    render_markdown, talk_time, transcript_text,
)
from core import mailer, notify
from core.aiservices import AIServices, pcm_to_wav_bytes
from core.secondbrain import SecondBrainShipper, build_candidate
from core.paths import app_path
from core.display import (
    SCREEN_WIDTH, SCREEN_HEIGHT, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT,
    COLOR_TEXT, COLOR_DIM, COLOR_SUCCESS, COLOR_ERROR,
    CORNER_INSET,
)

if TYPE_CHECKING:
    from core.context import AppContext

log = logging.getLogger(__name__)

CONTENT_TOP = STATUS_BAR_HEIGHT
CONTENT_BOTTOM = SCREEN_HEIGHT - BOTTOM_BAR_HEIGHT
MEETINGS_DIR = app_path("meetings")

CHUNK_SECS = 45.0        # STT segment size; whisper-small CPU keeps up easily
MAX_MEETING_SECS = 3 * 3600
# Transcripts longer than this get map-reduced before the final summary pass.
DIRECT_SUMMARY_CHARS = 12000
BLOCK_CHARS = 8000


def format_stamp(secs: float) -> str:
    secs = int(secs)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"[{h}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"


def build_transcript(segments: list[tuple[float, str]]) -> str:
    """Plain timestamped transcript from (start, text) pairs (no speakers)."""
    return "\n".join(f"{format_stamp(t)} {text}" for t, text in segments if text)


def split_blocks(transcript: str, block_chars: int = BLOCK_CHARS) -> list[str]:
    """Split on line boundaries into blocks of at most ~block_chars."""
    blocks, current, size = [], [], 0
    for line in transcript.splitlines():
        if size + len(line) > block_chars and current:
            blocks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        blocks.append("\n".join(current))
    return blocks


def summary_messages(transcript: str, speakers: bool = False) -> list[dict]:
    sections = (
        "## Summary\n(3-6 bullets of what was discussed and decided)\n"
        "## Action Items\n(- [ ] one per line: the task, then the owner if "
        "stated and any deadline; write 'none' if there are none)\n"
    )
    speaker_note = ""
    if speakers:
        sections += (
            "## Speakers\n(exactly one line per speaker: '- Speaker N: ' then "
            "their name if someone clearly says it in the meeting, otherwise "
            "a few words on their role; at most 15 words, no reasoning or "
            "caveats)\n"
        )
        speaker_note = (
            "Lines are labelled 'Speaker N' by automatic voice clustering, "
            "which can occasionally mislabel a line. Refer to people as "
            "'Speaker N' unless their name is clearly stated, then write "
            "'Name (Speaker N)'. "
        )
    return [
        {"role": "system", "content": (
            "You are a meeting assistant. From the transcript, produce "
            "concise markdown. The first line is '# ' followed by a short "
            "title for the meeting (3-7 words, naming its topic, e.g. "
            "'# Q3 Budget Review'). Then exactly these sections:\n" + sections
            + speaker_note
            + "Use only information from the transcript. No preamble."
        )},
        {"role": "user", "content": transcript},
    ]


def condense_messages(block: str) -> list[dict]:
    return [
        {"role": "system", "content": (
            "Condense this meeting transcript section to at most 15 bullet "
            "points, preserving every decision, number, name, speaker label "
            "and action item verbatim. Output only the bullets."
        )},
        {"role": "user", "content": block},
    ]


class MTState(enum.Enum):
    READY = "ready"
    RECORDING = "recording"
    SUMMARIZING = "summarizing"
    DONE = "done"


class MeetingApp(AppBase):
    name = "Meeting"
    icon = "assets/icons/meeting.png"
    uses_wakeword = False
    uses_double = False

    _LED = {
        MTState.READY: (0, 0, 0),
        MTState.RECORDING: (0, 255, 0),
        MTState.SUMMARIZING: (255, 255, 0),
        MTState.DONE: (0, 0, 0),
    }

    def __init__(self):
        super().__init__()
        self.state = MTState.READY
        self.segments: list[Line] = []   # whisper segments, absolute seconds
        self.summary: str = ""
        self.status_msg: str = ""
        self.phase: str = ""
        self.done_msg: str = ""
        self._pcm: list[bytes] = []
        self._elapsed: float = 0.0
        self._chunk_elapsed: float = 0.0
        self._samples_flushed: int = 0
        self._stt_queue: Optional[asyncio.Queue] = None
        self._stt_worker: Optional[asyncio.Task] = None
        self._pending_chunks: int = 0
        self._ai: Optional[AIServices] = None
        self._shipper: SecondBrainShipper = SecondBrainShipper({})
        self._rate: int = 16000
        self._language: str = "en"
        self._cfg: dict = {}
        self._stamp: Optional[datetime] = None
        self._wav: Optional[wave.Wave_write] = None
        self._wav_path: str = ""

    @property
    def keep_awake(self) -> bool:
        # Long recordings may dim the screen (LED shows recording); the
        # processing phase is short and worth watching.
        return self.state == MTState.SUMMARIZING

    def _set_state(self, new_state: MTState):
        self.state = new_state
        if self.ctx:
            self.ctx.display.set_led(*self._LED[new_state])

    def on_enter(self, ctx: AppContext):
        super().on_enter(ctx)
        self._cfg = ctx.config.get("meeting", {}) or {}
        self._language = self._cfg.get("language", "en")
        self._rate = ctx.config.get("audio", {}).get("sample_rate_in", 16000)
        self._ai = AIServices(ctx.config)
        self._shipper = SecondBrainShipper(ctx.config.get("second_brain", {}))
        self._set_state(MTState.READY)
        self.status_msg = ""

    def on_exit(self):
        if self._stt_worker:
            self._stt_worker.cancel()
            self._stt_worker = None
        self._close_wav()
        if self.ctx:
            self.ctx.audio.stop_all()
            self.ctx.display.set_led(0, 0, 0)
        self.state = MTState.READY
        super().on_exit()

    # --- recording ----------------------------------------------------------

    def _start_meeting(self):
        if not self.ctx:
            return
        self.segments = []
        self.summary = ""
        self.status_msg = ""
        self.done_msg = ""
        self.phase = ""
        self._pcm = []
        self._elapsed = 0.0
        self._chunk_elapsed = 0.0
        self._samples_flushed = 0
        self._pending_chunks = 0
        self._stamp = datetime.now()
        self._wav, self._wav_path = None, ""
        self._stt_queue = asyncio.Queue()
        self._stt_worker = self.spawn(self._stt_loop())
        self._set_state(MTState.RECORDING)
        self.ctx.audio.start_raw_capture(self._on_pcm)
        log.info("Meeting recording started")

    def _on_pcm(self, data: bytes):
        # PortAudio callback thread: append only.
        self._pcm.append(data)

    def _flush_chunk(self):
        """Move the buffered PCM into the STT queue (loop context).

        Chunk start times come from the sample count, not the frame clock,
        so transcript timestamps line up exactly with the saved WAV (which
        is what the diarizer's timestamps refer to).
        """
        if not self._pcm or not self._stt_queue:
            return
        chunk, self._pcm = self._pcm, []
        start_t = self._samples_flushed / self._rate
        self._samples_flushed += sum(len(c) for c in chunk) // 2
        self._pending_chunks += 1
        self._stt_queue.put_nowait((start_t, chunk))
        self._chunk_elapsed = 0.0

    def _append_audio(self, chunk: list[bytes]):
        """Worker thread: append a chunk to the meeting WAV (opened lazily).

        wave patches the header on every write, so the file is always a
        valid WAV even if the device dies mid-meeting.
        """
        if self._wav is None:
            Path(MEETINGS_DIR).mkdir(parents=True, exist_ok=True)
            stamp = self._stamp or datetime.now()
            self._wav_path = os.path.join(MEETINGS_DIR, f"{stamp:%Y-%m-%d_%H-%M}-meeting.wav")
            self._wav = wave.open(self._wav_path, "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(self._rate)
        self._wav.writeframes(b"".join(chunk))

    def _close_wav(self):
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception as e:
                log.warning("Closing meeting WAV failed: %s", e)
            self._wav = None

    async def _stt_loop(self):
        """Serial STT consumer: one chunk at a time keeps the AI server's CPU polite
        and the transcript ordered."""
        while True:
            start_t, chunk = await self._stt_queue.get()
            try:
                if self._cfg.get("keep_audio", False) or self._cfg.get("diarize", True):
                    await asyncio.to_thread(self._append_audio, chunk)
                chunk_secs = sum(len(c) for c in chunk) / 2 / self._rate
                wav = pcm_to_wav_bytes(chunk, self._rate)
                del chunk  # 45s of PCM is ~1.4MB; don't hold it during STT
                transcript = await self._ai.transcribe(wav, language=self._language)
                if transcript and transcript.text:
                    if transcript.segments:
                        for s in transcript.segments:
                            self.segments.append(
                                Line(start_t + s.start, start_t + s.end, s.text))
                    else:
                        self.segments.append(Line(
                            start_t, start_t + (transcript.duration or chunk_secs),
                            transcript.text))
                elif transcript is None:
                    self.status_msg = "STT unreachable — chunk lost"
                    log.warning("Meeting chunk STT failed (t=%.0fs)", start_t)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Meeting STT worker error: %s", e)
            finally:
                self._pending_chunks -= 1

    def _stop_meeting(self):
        if not self.ctx:
            return
        self.ctx.audio.stop_capture()
        self._flush_chunk()
        self.phase = "Transcribing..."
        self._set_state(MTState.SUMMARIZING)
        self.spawn(self._finish())

    def _discard_meeting(self):
        if self.ctx:
            self.ctx.audio.stop_capture()
        if self._stt_worker:
            self._stt_worker.cancel()
            self._stt_worker = None
        self._close_wav()
        if self._wav_path and os.path.exists(self._wav_path):
            try:
                os.remove(self._wav_path)
            except OSError:
                pass
        self._pcm = []
        self.segments = []
        self.status_msg = "Discarded"
        self._set_state(MTState.READY)
        log.info("Meeting discarded")

    # --- process + ship -----------------------------------------------------

    async def _finish(self):
        try:
            await self._finish_inner()
        finally:
            if self._stt_worker:
                self._stt_worker.cancel()
                self._stt_worker = None
            self._close_wav()
            if self.ctx and self.state == MTState.SUMMARIZING:
                self._set_state(MTState.READY)

    async def _finish_inner(self):
        # Wait for queued chunks to transcribe (bounded: they arrive faster
        # than realtime, so this is normally a few seconds).
        for _ in range(600):
            if self._pending_chunks <= 0:
                break
            await asyncio.sleep(0.5)
        self._close_wav()

        if not self.ctx:
            return
        lines = sorted(self.segments, key=lambda l: l.start)
        if not lines:
            self.status_msg = "No speech captured"
            self._set_state(MTState.READY)
            return

        duration = self._samples_flushed / self._rate if self._samples_flushed else self._elapsed

        # 1. who spoke when
        turns = None
        if self._cfg.get("diarize", True) and self._wav_path and os.path.exists(self._wav_path):
            self.phase = "Finding speakers..."
            try:
                turns = await self._ai.diarize(self._wav_path,
                                               speakers=int(self._cfg.get("speakers", 0)))
            except Exception as e:
                log.warning("Diarization skipped: %s", e)
                turns = None
            if not self.ctx:
                return
        talk = {}
        if turns:
            assign_speakers(lines, turns)
            talk = talk_time(turns)
            log.info("Diarized: %d speakers, %d turns", len(talk), len(turns))
        turns_merged = merge_turns(lines)
        transcript = transcript_text(turns_merged)

        # 2. summary
        self.phase = "Summarizing..."
        text = transcript
        if len(text) > DIRECT_SUMMARY_CHARS:
            condensed = []
            for block in split_blocks(text):
                part = await self._ai.chat(condense_messages(block),
                                           deep=True, num_ctx=16384)
                if not self.ctx:
                    return
                condensed.append(part or block[:2000])
            text = "\n".join(condensed)

        summary = await self._ai.chat(summary_messages(text, speakers=bool(talk)),
                                      deep=True, num_ctx=16384)
        if not self.ctx:
            return
        if not summary:
            self.status_msg = "Summarizer unreachable — transcript saved"
            summary = ""
        topic, summary = extract_title(summary)
        summary = clean_summary(summary)

        # 3. save the report
        now = self._stamp or datetime.now()
        stamp = f"{now:%b} {now.day}, {now.strftime('%I:%M %p').lstrip('0')}"
        fname = f"{now:%Y-%m-%d_%H-%M}-" + (slugify(topic) or "meeting")
        when = f"{now:%a %b} {now.day}, {now:%Y} at {now.strftime('%I:%M %p').lstrip('0')}"
        title = topic or f"Meeting — {stamp}"
        n_spk = len(talk)
        meta = f"{when} · {fmt_duration(duration)}"
        if n_spk:
            meta += f" · {n_spk} speaker{'s' if n_spk != 1 else ''}"
        md = render_markdown(title, meta, summary, turns_merged, talk)
        page = render_html(title, meta, summary, turns_merged, talk,
                           footer="Recorded and summarized on PiSugar · local AI")
        Path(MEETINGS_DIR).mkdir(parents=True, exist_ok=True)
        md_path = os.path.join(MEETINGS_DIR, f"{fname}.md")
        html_path = os.path.join(MEETINGS_DIR, f"{fname}.html")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page)
        log.info("Meeting saved: %s (%d lines, %.0fs, %d speakers)",
                 md_path, len(lines), duration, n_spk)

        if not self._cfg.get("keep_audio", False) and self._wav_path:
            try:
                os.remove(self._wav_path)
            except OSError:
                pass

        # 4. ship: second brain, email, ping
        if summary:
            self.summary = summary
            scope = self._cfg.get("scope", "meetings")
            cand_name, cand_md = build_candidate(
                text=summary, now=now, scope=scope,
                duration_secs=duration, audio_filename=f"{fname}.md",
            )
            # Outbox-first like Note Taker: written locally even when
            # shipping is disabled/offline, shipped when possible.
            await self._shipper.submit(cand_name, cand_md)

        self.phase = "Emailing..."
        email_cfg = self.ctx.config.get("email", {}) or {}
        body_html = render_html(title, meta, summary, turns_merged, talk,
                                include_transcript=False,
                                footer="Full transcript attached.")
        # Styled PDF (same look as the HTML) rendered on the AI server; if that's
        # unreachable, attach the HTML page instead so nothing is lost.
        attachments = []
        pdf = None
        if mailer.configured(email_cfg):
            pdf = await self._ai.render_pdf(page)
            if not self.ctx:
                return
        if pdf:
            attachments.append((f"{fname}.pdf", pdf))
        else:
            attachments.append((f"{fname}.html", page.encode("utf-8")))
        attachments.append((f"{fname}.md", md.encode("utf-8")))
        subject = (f"Meeting: {topic} — {stamp} ({fmt_duration(duration)})" if topic
                   else f"Meeting summary — {stamp} ({fmt_duration(duration)})")
        emailed = await mailer.send_email(
            email_cfg,
            subject=subject,
            text=(summary or "(summarizer unavailable)") + "\n\nFull transcript attached.",
            html=body_html,
            attachments=attachments,
        )
        if not self.ctx:
            return
        if emailed and self._cfg.get("notify", True):
            await notify.push(
                self.ctx.config.get("ntfy", {}) or {},
                f"Summary emailed ({fmt_duration(duration)}"
                + (f", {n_spk} speaker{'s' if n_spk != 1 else ''}" if n_spk else "") + ")",
                title="Meeting done", tags="clipboard")

        if mailer.configured(email_cfg):
            self.done_msg = "Saved + emailed" if emailed else "Saved (email failed)"
        else:
            self.done_msg = "Saved"
        self.phase = ""
        if summary:
            self._set_state(MTState.DONE)
        else:
            self._set_state(MTState.READY)

    # --- input --------------------------------------------------------------

    def on_button(self, event: str):
        if event == "single":
            if self.state in (MTState.READY, MTState.DONE):
                self._start_meeting()
            elif self.state == MTState.RECORDING:
                self._stop_meeting()

    def on_secondary(self, event: str):
        if event == "long" and self.state == MTState.RECORDING:
            self._discard_meeting()

    async def update(self, dt: float):
        if self.state == MTState.RECORDING:
            self._elapsed += dt
            self._chunk_elapsed += dt
            if self._chunk_elapsed >= CHUNK_SECS:
                self._flush_chunk()
            if self._elapsed >= MAX_MEETING_SECS:
                self._stop_meeting()

    # --- drawing ------------------------------------------------------------

    def draw(self, display):
        if not self.ctx:
            return
        d = self.ctx.display
        font_sm, font_md, font_lg = d.font_sm, d.font_md, d.font_lg
        x = CORNER_INSET
        w = SCREEN_WIDTH - 2 * CORNER_INSET

        d.draw_centered_text("Meeting", CONTENT_TOP + 5, font=font_sm,
                             color=COLOR_DIM)

        if self.state == MTState.RECORDING:
            mins, secs = int(self._elapsed) // 60, int(self._elapsed) % 60
            d.draw_centered_text(f"{mins:02d}:{secs:02d}", CONTENT_TOP + 45,
                                 font=font_lg)
            level = self.ctx.audio.level
            d.draw_rect(x, CONTENT_TOP + 95, int(w * level), 10, COLOR_ERROR)
            d.draw_centered_text(f"{len(self.segments)} lines transcribed",
                                 CONTENT_TOP + 118, font=font_sm, color=COLOR_DIM)
            if self.segments:
                d.draw_wrapped_text("..." + self.segments[-1].text[-90:],
                                    x, CONTENT_TOP + 140, w, 56, font=font_sm,
                                    color=COLOR_DIM)
        elif self.state == MTState.SUMMARIZING:
            d.draw_centered_text(self.phase or "Working...", CONTENT_TOP + 70,
                                 font=font_md)
            d.draw_centered_text(f"{len(self.segments)} lines",
                                 CONTENT_TOP + 100, font=font_sm, color=COLOR_DIM)
        elif self.state == MTState.DONE and self.summary:
            d.draw_wrapped_text(self.summary, x, CONTENT_TOP + 26, w,
                                CONTENT_BOTTOM - CONTENT_TOP - 34, font=font_sm)
        else:
            mid = (CONTENT_TOP + CONTENT_BOTTOM) // 2
            d.draw_centered_text("Tap to start meeting", mid - 12, font=font_md)
            d.draw_centered_text("Records + summarizes", mid + 16,
                                 font=font_sm, color=COLOR_DIM)

        if self.status_msg:
            d.draw_bottom_bar(self.status_msg, color=COLOR_ERROR)
        elif self.state == MTState.RECORDING:
            d.draw_bottom_bar("Tap=finish  PiSugar hold=discard", color=COLOR_ERROR)
        elif self.state == MTState.SUMMARIZING:
            d.draw_bottom_bar("Working...", color=COLOR_DIM)
        elif self.state == MTState.DONE:
            d.draw_bottom_bar(f"{self.done_msg or 'Saved'}  (tap=new)", color=COLOR_SUCCESS)
        else:
            d.draw_bottom_bar("Tap=record meeting")
