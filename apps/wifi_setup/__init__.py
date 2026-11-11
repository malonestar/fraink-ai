from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from aiohttp import web

from apps.base import AppBase
from core.display import (
    SCREEN_WIDTH, SCREEN_HEIGHT, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT,
    COLOR_BG, COLOR_TEXT, COLOR_DIM, COLOR_SUCCESS, COLOR_ERROR,
)

if TYPE_CHECKING:
    from core.context import AppContext

log = logging.getLogger(__name__)

CONTENT_TOP = STATUS_BAR_HEIGHT
PORTAL_PORT = 80
PORTAL_HTML = os.path.join(os.path.dirname(__file__), "portal.html")


class WiFiSetupApp(AppBase):
    name = "WiFi Setup"
    icon = "assets/icons/wifi.png"
    uses_wakeword = False

    def __init__(self):
        super().__init__()
        self._ap_active = False
        self._ap_ip: str = ""
        self._web_runner: web.AppRunner | None = None
        self._connecting = False
        self._connect_result: str = ""

    @property
    def keep_awake(self) -> bool:
        # The AP/connecting screens carry the info the user needs (SSID, IP,
        # progress); the screensaver hid them mid-setup (seen 2026-11-09).
        return self._ap_active or self._connecting

    def on_enter(self, ctx: AppContext):
        super().on_enter(ctx)
        self._connecting = False
        self._connect_result = ""

    def on_exit(self):
        if self._ap_active and self.ctx:
            # Snapshot the network handle: super().on_exit() clears ctx before
            # the task runs, and the AP must still be torn down.
            self.spawn(self._stop_ap(self.ctx.network))
        super().on_exit()

    async def _start_ap(self):
        if not self.ctx:
            return
        self._ap_ip = await self.ctx.network.start_ap()
        self._ap_active = True
        await self._start_web_server()
        log.info("AP mode active at %s", self._ap_ip)

    async def _stop_ap(self, network=None):
        await self._stop_web_server()
        network = network or (self.ctx.network if self.ctx else None)
        if network:
            await network.stop_ap()
        self._ap_active = False

    async def _start_web_server(self):
        app = web.Application()
        app.router.add_get("/", self._handle_portal)
        app.router.add_get("/api/scan", self._handle_scan)
        app.router.add_post("/api/connect", self._handle_connect)
        # Anything else (iOS/Android captive-portal probes, typed URLs) gets
        # the portal page, so the phone pops the setup sheet by itself.
        app.router.add_get("/{tail:.*}", self._handle_portal)
        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, "0.0.0.0", PORTAL_PORT)
        await site.start()
        log.info("Portal web server started on port %d", PORTAL_PORT)

    async def _stop_web_server(self):
        if self._web_runner:
            await self._web_runner.cleanup()
            self._web_runner = None

    async def _handle_portal(self, request: web.Request) -> web.Response:
        with open(PORTAL_HTML) as f:
            html = f.read()
        return web.Response(text=html, content_type="text/html")

    async def _handle_scan(self, request: web.Request) -> web.Response:
        networks = self.ctx.network.scan_networks() if self.ctx else []
        return web.json_response(networks)

    async def _handle_connect(self, request: web.Request) -> web.Response:
        data = await request.json()
        ssid = data.get("ssid", "")
        password = data.get("password", "")
        if not ssid:
            return web.json_response({"ok": False, "error": "No SSID provided"})
        self.ctx.network.save_wifi_credentials(ssid, password)
        self._connecting = True
        self._connect_result = ""
        self.spawn(self._attempt_connect())
        return web.json_response({"ok": True})

    async def _attempt_connect(self):
        await self._stop_ap()
        await asyncio.sleep(2)
        if self.ctx:
            connected = await self.ctx.network.wait_for_wifi()
            self._connecting = False
            if connected:
                self._connect_result = "connected"
                # Starts the supervisor on first-time provisioning; if it is
                # already running this skips its backoff and reconnects now.
                self.ctx.xiaozhi.start(self.ctx.network.current_websocket_url)
            else:
                self._connect_result = "failed"
                await self._start_ap()

    def on_secondary(self, event: str):
        # PiSugar long: force AP mode so a new network can be configured.
        if event == "long" and not self._ap_active:
            self.spawn(self._start_ap())

    async def update(self, dt: float):
        pass

    def draw(self, display):
        if not self.ctx:
            return
        font_sm = self.ctx.display.font_sm
        font_md = self.ctx.display.font_md

        self.ctx.display.draw_centered_text("WiFi Setup", CONTENT_TOP + 5, font=font_sm, color=COLOR_DIM)

        if self._ap_active:
            self.ctx.display.draw_centered_text("AP Mode Active", CONTENT_TOP + 40, font=font_md, color=COLOR_SUCCESS)
            self.ctx.display.draw_centered_text(f"SSID: {self.ctx.network._ap_ssid}", CONTENT_TOP + 70, font=font_sm)
            self.ctx.display.draw_centered_text(f"IP: {self._ap_ip}", CONTENT_TOP + 95, font=font_sm)
            self.ctx.display.draw_centered_text("Connect with phone", CONTENT_TOP + 130, font=font_sm, color=COLOR_DIM)
            self.ctx.display.draw_centered_text("to configure WiFi", CONTENT_TOP + 150, font=font_sm, color=COLOR_DIM)
            self.ctx.display.draw_bottom_bar("PiSugar=Force AP")
        elif self._connecting:
            self.ctx.display.draw_centered_text("Connecting...", CONTENT_TOP + 80, font=font_md)
            self.ctx.display.draw_bottom_bar("Please wait")
        elif self._connect_result == "failed":
            self.ctx.display.draw_centered_text("Connection Failed", CONTENT_TOP + 80, font=font_md, color=COLOR_ERROR)
            self.ctx.display.draw_bottom_bar("PiSugar=Retry AP")
        elif self.ctx.network.is_connected:
            ssid = self.ctx.network.ssid or "Unknown"
            ip = self.ctx.network.device_ip or "..."
            mode = "LAN" if self.ctx.network.is_home else "Tailscale"
            if len(ssid) > 24:  # long SSIDs overflowed the 240px screen
                ssid = ssid[:23] + "…"
            self.ctx.display.draw_centered_text("Connected", CONTENT_TOP + 36, font=font_md, color=COLOR_SUCCESS)
            self.ctx.display.draw_centered_text(ssid, CONTENT_TOP + 62, font=font_sm)
            self.ctx.display.draw_centered_text(f"IP: {ip}", CONTENT_TOP + 88, font=font_sm)
            self.ctx.display.draw_centered_text(f"Mode: {mode}", CONTENT_TOP + 110, font=font_sm, color=COLOR_DIM)
            self.ctx.display.draw_bottom_bar("PiSugar=Force AP")
        else:
            self.ctx.display.draw_centered_text("No WiFi", CONTENT_TOP + 80, font=font_md, color=COLOR_ERROR)
            self.ctx.display.draw_bottom_bar("PiSugar=Start AP")
