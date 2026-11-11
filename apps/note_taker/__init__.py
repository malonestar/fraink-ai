from __future__ import annotations

import enum
import logging
import os
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from apps.base import AppBase
from core import notify
from core.secondbrain import SecondBrainShipper, build_candidate
from core.paths import app_path
from core.display import (
    SCREEN_WIDTH, SCREEN_HEIGHT, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT,
    COLOR_BG, COLOR_TEXT, COLOR_DIM, COLOR_SUCCESS, COLOR_ERROR,
)

if TYPE_CHECKING:
    from core.context import AppContext

log = logging.getLogger(__name__)

try:
    import opuslib
    HAS_OPUS = True
except (ImportError, OSError):
    HAS_OPUS = False

CONTENT_TOP = STATUS_BAR_HEIGHT
NOTES_DIR = app_path("notes")
RECORD_RATE = 16000
FRAME_MS = 60


class NTState(enum.Enum):
    READY = "ready"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    SENT = "sent"


def format_note_filename(year, month, day, hour, minute, second) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}_{hour:02d}-{minute:02d}-{second:02d}"


def save_note_markdown(directory: str, filename: str, text: str) -> str:
    Path(directory).mkdir(parents=True, exist_ok=True)
    path = os.path.join(directory, f"{filename}.md")
    with open(path, "w") as f:
        f.write(f"# Voice Memo\n\n{text}\n")
    return path


def save_note_wav(directory: str, filename: str, pcm_chunks: list[bytes],
                  rate: int = RECORD_RATE) -> Optional[str]:
    """Write buffered 16-bit mono PCM as a real WAV file.

    Memos used to be saved as concatenated raw opus packets in a .opus file —
    not a valid container, so neither on-device replay nor Telegram could
    actually play them. WAV is bigger (~1.9MB/min) but universally playable.
    """
    if not pcm_chunks:
        return None
    Path(directory).mkdir(parents=True, exist_ok=True)
    path = os.path.join(directory, f"{filename}.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(pcm_chunks))
    return path


class NoteTakerApp(AppBase):
    name = "Note Taker"
    icon = "assets/icons/notes.png"
    uses_wakeword = False
    uses_double = True  # double-tap = replay the last memo

    def __init__(self):
        super().__init__()
        self.state = NTState.READY
        self.last_transcription: str = ""
        self.recent_notes: list[dict] = []
        self._pcm_buffer: list[bytes] = []
        self._decoder = None
        self._record_elapsed: float = 0.0
        self._sent_timer: float = 0.0
        self._replay_until: float = 0.0
        self._shipper: SecondBrainShipper = SecondBrainShipper({})

    # WhisPlay LED, same convention as voice companion: green = mic open,
    # yellow = working. Pure 0/255 colors (SoftPWM flicker, see display.py).
    _LED = {
        NTState.READY: (0, 0, 0),
        NTState.RECORDING: (0, 255, 0),
        NTState.TRANSCRIBING: (255, 255, 0),
        NTState.SENT: (0, 0, 0),
    }

    @property
    def keep_awake(self) -> bool:
        # Never blank the screen mid-recording or while transcribing.
        return self.state in (NTState.RECORDING, NTState.TRANSCRIBING)

    def _set_state(self, new_state: NTState):
        self.state = new_state
        if self.ctx:
            self.ctx.display.set_led(*self._LED[new_state])

    def on_enter(self, ctx: AppContext):
        super().on_enter(ctx)
        self._set_state(NTState.READY)
        self._shipper = SecondBrainShipper(ctx.config.get("second_brain", {}))
        if self._shipper.enabled:
            self.spawn(self._shipper.flush())
        self._load_recent_notes()

    def on_exit(self):
        if self.ctx:
            self.ctx.audio.stop_all()
            if self.ctx.xiaozhi:
                # Detach, or a late STT reply hits this handler with
                # ctx=None (same bug class as voice companion).
                self.ctx.xiaozhi.detach()
            self.ctx.display.set_led(0, 0, 0)
        self.state = NTState.READY
        super().on_exit()

    def _load_recent_notes(self):
        self.recent_notes = []
        if not os.path.exists(NOTES_DIR):
            return
        files = sorted(
            [f for f in os.listdir(NOTES_DIR) if f.endswith(".md")],
            reverse=True,
        )[:5]
        for f in files:
            path = os.path.join(NOTES_DIR, f)
            with open(path) as fh:
                lines = fh.readlines()
                preview = ""
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        preview = stripped[:40]
                        break
            self.recent_notes.append({
                "filename": f,
                "preview": preview,
                "timestamp": f.replace(".md", "").replace("_", " ").replace("-", ":", 2),
            })

    async def _start_recording(self):
        if not self.ctx or not self.ctx.xiaozhi or not self.ctx.xiaozhi.connected:
            self.flash_offline()
            return
        self._set_state(NTState.RECORDING)
        self._pcm_buffer = []
        self._record_elapsed = 0.0
        rate = self.ctx.config.get("audio", {}).get("sample_rate_in", RECORD_RATE)
        frame_size = int(rate * FRAME_MS / 1000)
        self._decoder = opuslib.Decoder(rate, 1) if HAS_OPUS else None

        async def on_opus_frame(data: bytes):
            # Stream to the server for STT; keep a local PCM copy for the
            # replayable WAV artifact.
            if self._decoder:
                try:
                    self._pcm_buffer.append(self._decoder.decode(data, frame_size))
                except Exception as e:
                    log.debug("Memo opus decode failed: %s", e)
            await self.ctx.xiaozhi.send_audio(data)

        self.ctx.audio.start_capture(on_opus_frame)
        await self.ctx.xiaozhi.start_listening(mode="manual")
        self.ctx.xiaozhi.attach(on_json=self._handle_server_json)

    async def _stop_recording(self):
        if not self.ctx:
            return
        self.ctx.audio.stop_capture()
        await self.ctx.xiaozhi.stop_listening()
        self._set_state(NTState.TRANSCRIBING)

    async def _handle_server_json(self, msg: dict):
        if not self.ctx:
            return  # late message after exit; handler is detached too
        msg_type = msg.get("type")
        if msg_type == "stt":
            text = msg.get("text", "")
            self.last_transcription = text
            if self.ctx and self.ctx.xiaozhi:
                await self.ctx.xiaozhi.abort(reason="note_taker_stt_only")
            await self._save_and_send(text)
        elif msg_type == "tts":
            pass

    async def _save_and_send(self, text: str):
        now = datetime.now()
        filename = format_note_filename(
            now.year, now.month, now.day, now.hour, now.minute, now.second
        )
        save_note_markdown(NOTES_DIR, filename, text)

        audio_dir = os.path.join(NOTES_DIR, "audio")
        audio_path = save_note_wav(audio_dir, filename, self._pcm_buffer)

        sb_cfg = self.ctx.config.get("second_brain", {})
        scope = sb_cfg.get("scope", "voice_memos")
        fname, md = build_candidate(
            text=text, now=now, scope=scope,
            duration_secs=self._record_elapsed,
            audio_filename=f"{filename}.wav" if audio_path else "",
        )
        if self._shipper is None:
            self._shipper = SecondBrainShipper(sb_cfg)
        await self._shipper.submit(fname, md)

        # The transcript lands on the phone as an ntfy push (self-hosted,
        # replaced Telegram 2026-11-07) so a dictated number/address is
        # there to copy. Audio stays on the device + the second-brain note.
        await notify.push(self.ctx.config.get("ntfy", {}) or {}, text,
                          title=f"Voice memo {now:%I:%M %p}".replace(" 0", " "),
                          tags="studio_microphone")

        self._set_state(NTState.SENT)
        self._sent_timer = 3.0
        self._load_recent_notes()
        log.info("Note saved: %s", filename)

    def _replay_last(self):
        if not self.recent_notes or not self.ctx:
            return
        latest_wav = os.path.join(
            NOTES_DIR, "audio",
            self.recent_notes[0]["filename"].replace(".md", ".wav"),
        )
        if os.path.exists(latest_wav):
            duration = self.ctx.audio.play_sound_file(latest_wav)
            if duration > 0:
                self._replay_until = time.monotonic() + duration

    def on_button(self, event: str):
        if event == "single":
            if self.state == NTState.READY:
                self.spawn(self._start_recording())
            elif self.state == NTState.RECORDING:
                self.spawn(self._stop_recording())
        elif event == "double":
            if self.state == NTState.READY:
                self._replay_last()

    async def update(self, dt: float):
        if self.state == NTState.RECORDING:
            self._record_elapsed += dt
        elif self.state == NTState.SENT:
            self._sent_timer -= dt
            if self._sent_timer <= 0:
                self._set_state(NTState.READY)

    def draw(self, display):
        if not self.ctx:
            return
        font_sm = self.ctx.display.font_sm
        font_md = self.ctx.display.font_md
        font_lg = self.ctx.display.font_lg

        self.ctx.display.draw_centered_text("Note Taker", CONTENT_TOP + 5, font=font_sm, color=COLOR_DIM)

        if self.state == NTState.READY:
            y = CONTENT_TOP + 30
            for note in self.recent_notes:
                ts_text = note["filename"].replace(".md", "")
                self.ctx.display.draw_text(ts_text, 8, y, font=font_sm, color=COLOR_DIM)
                if note["preview"]:
                    self.ctx.display.draw_text(note["preview"], 8, y + 16, font=font_sm)
                y += 36

        elif self.state == NTState.RECORDING:
            mins = int(self._record_elapsed) // 60
            secs = int(self._record_elapsed) % 60
            time_str = f"{mins:02d}:{secs:02d}"
            self.ctx.display.draw_centered_text(time_str, CONTENT_TOP + 60, font=font_lg)
            level = self.ctx.audio.level
            bar_w = int((SCREEN_WIDTH - 40) * level)
            bar_y = CONTENT_TOP + 120
            self.ctx.display.draw_rect(20, bar_y, bar_w, 12, COLOR_ERROR)

        elif self.state == NTState.TRANSCRIBING:
            self.ctx.display.draw_centered_text("Transcribing...", CONTENT_TOP + 80, font=font_md)

        elif self.state == NTState.SENT:
            self.ctx.display.draw_centered_text("Saved!", CONTENT_TOP + 50, font=font_md, color=COLOR_SUCCESS)
            if self.last_transcription:
                self.ctx.display.draw_wrapped_text(
                    self.last_transcription, 8, CONTENT_TOP + 80, SCREEN_WIDTH - 16, 100,
                    font=font_sm,
                )

        state_labels = {
            NTState.READY: "Ready  (2x=replay last)",
            NTState.RECORDING: "Recording...",
            NTState.TRANSCRIBING: "Transcribing...",
            NTState.SENT: "Sent!",
        }
        if self.offline_flash:
            self.ctx.display.draw_bottom_bar("Server offline", color=COLOR_ERROR)
        elif time.monotonic() < self._replay_until:
            self.ctx.display.draw_bottom_bar("Playing memo...", color=COLOR_SUCCESS)
        else:
            color = COLOR_ERROR if self.state == NTState.RECORDING else COLOR_DIM
            self.ctx.display.draw_bottom_bar(state_labels[self.state], color=color)
