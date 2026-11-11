import asyncio
import enum
import json
import logging
import uuid
from typing import Awaitable, Callable, Optional

import websockets

log = logging.getLogger(__name__)

HELLO_TIMEOUT = 10
RECONNECT_BACKOFF_MAX = 30


class XiaozhiState(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


def build_hello_message() -> dict:
    return {
        "type": "hello",
        "version": 1,
        "features": {"mcp": True},
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    }


def build_listen_message(session_id: str, state: str,
                         mode: str = "auto", text: str = "") -> dict:
    msg = {
        "session_id": session_id,
        "type": "listen",
        "state": state,
    }
    if state == "start":
        msg["mode"] = mode
    if text:
        msg["text"] = text
    return msg


def build_abort_message(session_id: str, reason: str = "") -> dict:
    msg = {
        "session_id": session_id,
        "type": "abort",
    }
    if reason:
        msg["reason"] = reason
    return msg


def parse_server_message(raw: str) -> dict:
    return json.loads(raw)


class XiaozhiClient:
    def __init__(self, config: dict,
                 on_audio: Optional[Callable[[bytes], Awaitable[None]]] = None,
                 on_json: Optional[Callable[[dict], Awaitable[None]]] = None,
                 reconnect_backoff_base: float = 1.0):
        self._device_id = config.get("server", {}).get("device_id", "fraink")
        self._client_id = config.get("server", {}).get("client_id", str(uuid.uuid4()))
        self._protocol_version = config.get("server", {}).get("protocol_version", 1)
        self._reconnect_backoff_base = reconnect_backoff_base
        self._ws_url: str = ""
        self._url_source = ""
        self._wake: Optional[asyncio.Event] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._session_id: Optional[str] = None
        self._on_audio = on_audio
        self._on_json = on_json
        self._running = False
        self._supervisor_task: Optional[asyncio.Task] = None
        self._connected = False
        self._server_sample_rate = 24000

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def server_sample_rate(self) -> int:
        return self._server_sample_rate

    def attach(self, on_json: Optional[Callable[[dict], Awaitable[None]]] = None,
               on_audio: Optional[Callable[[bytes], Awaitable[None]]] = None):
        """Register the active app's server-message handlers.

        Exactly one app owns the handlers at a time; attaching replaces the
        previous owner. Apps MUST detach() in on_exit (a late LLM reply
        hitting a handler whose app has ctx=None crashes the receive loop).
        """
        self._on_json = on_json
        self._on_audio = on_audio

    def detach(self):
        """Clear the server-message handlers (call from the app's on_exit)."""
        self._on_json = None
        self._on_audio = None

    def start(self, ws_url):
        """Start the reconnect supervisor. Idempotent.

        ws_url: a URL, or a zero-arg callable returning one. The callable is
        re-evaluated before every (re)connect, so a device that moves from
        home Wi-Fi to a phone hotspot switches from the LAN URL to the
        tailnet URL by itself. Calling start() again while running swaps in
        the new source and retries immediately (WiFi Setup does this).
        """
        self._url_source = ws_url
        if self._supervisor_task is not None:
            self.kick()
            return
        self._running = True
        self._supervisor_task = asyncio.create_task(self._run())

    def kick(self):
        """Skip the current reconnect backoff (network just changed)."""
        if self._wake is not None:
            self._wake.set()

    async def _resolve_url(self) -> str:
        src = self._url_source
        if callable(src):
            try:
                return await asyncio.to_thread(src)
            except Exception as e:
                log.warning("Resolving server URL failed: %s", e)
                return self._ws_url
        return src or ""

    async def _run(self):
        """Maintain the connection: connect, serve, and reconnect on any drop
        or failure, forever (with backoff) until stop() / disconnect()."""
        self._wake = asyncio.Event()
        backoff = self._reconnect_backoff_base
        while self._running:
            ws_url = await self._resolve_url()
            if ws_url != self._ws_url:
                log.info("xiaozhi server URL: %s", ws_url)
                self._ws_url = ws_url
            try:
                headers = {
                    "Device-Id": self._device_id,
                    "Client-Id": self._client_id,
                    "Protocol-Version": str(self._protocol_version),
                }
                self._ws = await websockets.connect(
                    ws_url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=10,
                )
                await self._handshake()
                self._connected = True
                backoff = self._reconnect_backoff_base
                log.info("Connected to xiaozhi-server, session=%s", self._session_id)
                await self._recv_loop()  # blocks until the connection drops
                self._connected = False
                if self._running:
                    log.warning("xiaozhi connection dropped — reconnecting")
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("Connection failed: %s (retry in %.1fs)", e, backoff)
            finally:
                await self._close_ws()
            if not self._running:
                break
            try:  # backoff, cut short by kick()
                await asyncio.wait_for(self._wake.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)
        self._connected = False

    async def _close_ws(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _handshake(self):
        hello = build_hello_message()
        await self._ws.send(json.dumps(hello))
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=HELLO_TIMEOUT)
            msg = parse_server_message(raw)
            if msg.get("type") != "hello":
                raise ConnectionError(f"Expected hello, got {msg.get('type')}")
            self._session_id = msg.get("session_id")
            audio_params = msg.get("audio_params", {})
            self._server_sample_rate = audio_params.get("sample_rate", 24000)
        except asyncio.TimeoutError:
            raise ConnectionError("Server hello timeout")

    async def _recv_loop(self):
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    if self._on_audio:
                        await self._on_audio(message)
                else:
                    msg = parse_server_message(message)
                    if self._on_json:
                        await self._on_json(msg)
        except websockets.ConnectionClosed:
            log.warning("WebSocket connection closed")
        except asyncio.CancelledError:
            return
        finally:
            self._connected = False
            self._session_id = None

    async def send_audio(self, opus_data: bytes):
        if self._ws and self._connected:
            await self._ws.send(opus_data)

    async def send_json(self, msg: dict):
        if self._ws and self._connected:
            await self._ws.send(json.dumps(msg))

    async def start_listening(self, mode: str = "auto", wake_text: str = ""):
        if not self._session_id:
            return
        if wake_text:
            msg = build_listen_message(self._session_id, "detect", text=wake_text)
        else:
            msg = build_listen_message(self._session_id, "start", mode=mode)
        await self.send_json(msg)

    async def stop_listening(self):
        if not self._session_id:
            return
        msg = build_listen_message(self._session_id, "stop")
        await self.send_json(msg)

    async def abort(self, reason: str = ""):
        if not self._session_id:
            return
        msg = build_abort_message(self._session_id, reason)
        await self.send_json(msg)

    async def disconnect(self):
        self._running = False
        await self._close_ws()  # unblocks _recv_loop
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
            self._supervisor_task = None
        self._connected = False
        self._session_id = None
