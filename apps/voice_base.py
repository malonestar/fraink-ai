from __future__ import annotations

import logging

from apps.base import AppBase

log = logging.getLogger(__name__)


class VoiceAppBase(AppBase):
    """Shared xiaozhi voice-session plumbing for conversational apps.

    Owns the subtle protocol rules every voice app was hand-rolling:

    - Handler lifecycle via the client's public attach()/detach() — attach in
      on_enter (unconditionally, so an app entered while offline recovers when
      the connection comes back), detach in on_exit.
    - The listen boilerplate: pause wake word, close the raw mic stream, set
      the output sample rate from the server hello, open opus capture.
    - stt/tts dispatch including the post-tool-reply rules (2026-06-15,
      commit 6cd2514): the server re-emits an `stt` message mid-reply during
      its function-call flow, and reply audio must be played while the
      playback stream is open (`audio.is_playing`), never gated on app state.

    Subclasses implement the hooks at the bottom to drive their own state.
    """

    def _attach_server_handlers(self):
        if self.ctx and self.ctx.xiaozhi:
            self.ctx.xiaozhi.attach(on_json=self._handle_server_json,
                                    on_audio=self._handle_server_audio)

    def _detach_server_handlers(self):
        if self.ctx and self.ctx.xiaozhi:
            self.ctx.xiaozhi.detach()

    def _arm_wakeword(self):
        """IDLE only: resume detection and feed the mic to it (raw PCM)."""
        ctx = self.ctx
        if not ctx or not ctx.wakeword:
            return
        ctx.wakeword.resume()
        if ctx.wakeword.is_active and not ctx.audio.is_raw_capturing:
            ctx.audio.start_raw_capture(ctx.wakeword.process_audio)

    async def _listen_start(self, mode: str = "auto", wake_text: str = "") -> bool:
        """Open the mic to the server. Returns False (and flashes the offline
        notice) when the server is unreachable."""
        ctx = self.ctx
        if not ctx or not ctx.xiaozhi or not ctx.xiaozhi.connected:
            self.flash_offline()
            return False
        if ctx.wakeword:
            ctx.wakeword.pause()
        ctx.audio.stop_capture()  # closes the raw wake word stream
        ctx.audio.set_output_sample_rate(ctx.xiaozhi.server_sample_rate)
        ctx.audio.start_capture(ctx.xiaozhi.send_audio)
        await ctx.xiaozhi.start_listening(mode=mode, wake_text=wake_text)
        return True

    async def _listen_stop(self):
        if not self.ctx:
            return
        self.ctx.audio.stop_capture()
        if self.ctx.xiaozhi:
            await self.ctx.xiaozhi.stop_listening()

    async def _handle_server_json(self, msg: dict):
        if not self.ctx:
            return  # late message after exit; handlers are detached too
        msg_type = msg.get("type")
        if msg_type == "stt":
            if self.ctx.audio.is_playing:
                # Spurious mid-reply stt: the server re-emits the query text
                # during its function-call / 2nd-pass flow. Do NOT leave the
                # speaking state — that drops the rest of the reply audio.
                self._on_stt_mid_reply(msg.get("text", ""))
            else:
                self.ctx.audio.stop_capture()
                self._on_stt_final(msg.get("text", ""))
        elif msg_type == "tts":
            tts_state = msg.get("state")
            if tts_state == "start":
                self.ctx.audio.start_playback()
                self._on_speaking_start()
            elif tts_state == "sentence_start":
                self._on_tts_sentence(msg.get("text", ""))
            elif tts_state == "stop":
                self.ctx.audio.stop_playback()
                await self._on_speaking_stop()

    async def _handle_server_audio(self, data: bytes):
        # Play whatever arrives while the TTS playback stream is open,
        # regardless of app state (see class docstring).
        if self.ctx and self.ctx.audio.is_playing:
            await self.ctx.audio.enqueue_opus(data)

    # --- hooks ------------------------------------------------------------

    def _on_stt_final(self, text: str):
        """Transcription arrived (capture already stopped by the base)."""

    def _on_stt_mid_reply(self, text: str):
        """Server re-emitted stt while reply audio is playing."""

    def _on_speaking_start(self):
        """TTS playback stream just opened."""

    def _on_tts_sentence(self, text: str):
        """A TTS sentence's text (for transcripts)."""

    async def _on_speaking_stop(self):
        """TTS finished (playback already stopped by the base)."""
