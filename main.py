import asyncio
import logging
import os
import signal
import sys
import time

from core.config import load_config, generate_client_id_if_missing
from core.display import Display, COLOR_BG, COLOR_TEXT, SCREEN_WIDTH, SCREEN_HEIGHT, FPS
from core.button import ButtonListener, WhisPlayButton, ButtonEvent
from core import pisugar
from core.network import NetworkManager
from core.xiaozhi_client import XiaozhiClient
from core.audio import AudioService
from core.wakeword import WakeWordService
from core.settings_store import SettingsStore
from core.screensaver import IdleScreensaver
from core.context import AppContext
from core.launcher import Launcher
from core import tailscale

from apps.voice_companion import VoiceCompanionApp
from apps.note_taker import NoteTakerApp
from apps.translator import TranslatorApp
from apps.meeting import MeetingApp
from apps.pomodoro import PomodoroApp
from apps.ha_controller import HAControllerApp
from apps.wifi_setup import WiFiSetupApp
from apps.settings import SettingsApp
from apps.tailscale_manager import TailscaleManagerApp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("main")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
LOW_BATTERY_PCT = 20  # home-screen LED turns orange below this

APPS = [
    {"name": "Voice Companion", "icon": "voice", "app_class": VoiceCompanionApp},
    {"name": "Note Taker", "icon": "notes", "app_class": NoteTakerApp},
    {"name": "Translator", "icon": "translate", "app_class": TranslatorApp},
    {"name": "Meeting", "icon": "meeting", "app_class": MeetingApp},
    {"name": "Pomodoro", "icon": "timer", "app_class": PomodoroApp},
    {"name": "HA Controller", "icon": "ha", "app_class": HAControllerApp},
    {"name": "WiFi Setup", "icon": "wifi", "app_class": WiFiSetupApp},
    {"name": "Settings", "icon": "settings", "app_class": SettingsApp},
    {"name": "Tailscale", "icon": "tailscale", "app_class": TailscaleManagerApp},
]


async def main():
    config = load_config(CONFIG_PATH)
    config = generate_client_id_if_missing(config, CONFIG_PATH)

    display = Display()
    display.set_led(0, 0, 0)  # WhisPlay powers up with the LED white
    display.fill()
    display.draw_centered_text("PiSugar Companion", 120, font=display.font_lg)
    display.draw_centered_text("Starting...", 160, font=display.font_sm)
    display.flip()

    network = NetworkManager(config)
    audio = AudioService(config)
    wakeword = WakeWordService(config)
    xiaozhi = XiaozhiClient(config)
    settings = SettingsStore(os.path.join(os.path.dirname(__file__), "data", "settings.json"))
    if settings.get("wakeword_model"):
        wakeword.set_model(settings.get("wakeword_model"))  # Settings overlay > config.yaml

    ctx = AppContext(
        display=display,
        network=network,
        audio=audio,
        wakeword=wakeword,
        xiaozhi=xiaozhi,
        config=config,
        settings=settings,
    )

    app_instances = []
    entries = []
    for app_def in APPS:
        instance = app_def["app_class"]()
        app_instances.append(instance)
        entries.append({
            "name": app_def["name"],
            "icon": app_def["icon"],
            "app": instance,
        })

    launcher = Launcher(entries)
    disp_cfg = config.get("display", {})
    saver = IdleScreensaver(
        dim_after=disp_cfg.get("screensaver_dim_seconds", 15),
        off_after=disp_cfg.get("screensaver_off_seconds", 60))

    dirty = True
    battery_poll = 0.0
    saver_clock = ""

    def sync_double_enabled():
        app = launcher.active_app
        whisplay_button.double_enabled = bool(app and app.uses_double)

    def exit_to_launcher():
        app = launcher.active_app
        if app is None:
            return
        if app.uses_wakeword:
            wakeword.stop()
        audio.stop_all()
        launcher.exit_app()
        sync_double_enabled()

    def launch_highlighted():
        if launcher.active_app is not None:
            return
        launcher.launch(ctx)
        sync_double_enabled()
        # Wake word startup is owned by the app's on_enter (it has the
        # coroutine callback and arms the raw mic feed itself).

    async def on_button(event: ButtonEvent, source: str):
        nonlocal dirty
        if saver.on_activity():
            dirty = True
            display.set_backlight(True)
            return  # waking press is consumed — no scroll/launch/app action
        if source == "pisugar":
            # Secondary button: back/home in-app; previous app at home.
            if event == ButtonEvent.SINGLE:
                if launcher.in_app:
                    exit_to_launcher()
                else:
                    launcher.prev()
            elif event == ButtonEvent.LONG and launcher.in_app:
                launcher.active_app.on_secondary(event.value)
        elif launcher.in_app:
            if event == ButtonEvent.LONG:
                exit_to_launcher()
            else:
                launcher.active_app.on_button(event.value)
        else:
            if event == ButtonEvent.SINGLE:
                launcher.next()
            elif event == ButtonEvent.LONG:
                launch_highlighted()
        dirty = True

    whisplay_button = WhisPlayButton(display, on_button)
    pisugar_button = ButtonListener(on_button, source="pisugar")
    ctx.button = whisplay_button
    await whisplay_button.start()
    await pisugar_button.start()

    # RTC sync + safe low-battery shutdown config (best-effort, off-loop).
    power_init_task = asyncio.create_task(pisugar.boot_init())

    # systemd stop sends SIGTERM: exit the loop cleanly so the teardown below
    # clears the screen and LED instead of leaving them lit.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass  # not available on this platform (e.g. Windows dev)

    display.fill()
    display.draw_centered_text("Connecting to WiFi...", 130, font=display.font_md)
    display.flip()

    wifi_ok = await network.wait_for_wifi()

    if not wifi_ok:
        log.warning("WiFi not connected — launching WiFi Setup")
        launcher.highlight = 4
        launcher.launch(ctx)
        sync_double_enabled()
    else:
        xiaozhi.start(network.current_websocket_url)  # re-picks LAN vs tailnet on every reconnect
        ctx.tailscale_state = (await tailscale.get_status()).backend_state

    battery_poll = 30.0  # prime: first loop iteration polls immediately
    ts_skip = 0
    try:
        while not stop_event.is_set():
            dt = display.tick()

            battery_poll += dt
            if battery_poll >= 30.0:
                battery_poll = 0.0
                # Blocking unix-socket I/O (up to 1.5s/command if pisugar-server
                # hangs) — keep it off the event loop or the UI stalls.
                pct, plugged = await asyncio.to_thread(pisugar.get_battery_info)
                ctx.battery_pct, ctx.battery_plugged = pct, plugged
                # Tailscale can't appear without a reinstall: when absent,
                # re-check every ~5min instead of spawning a CLI every 30s.
                ts_skip += 1
                if ctx.tailscale_state != "NotInstalled" or ts_skip >= 10:
                    ts_skip = 0
                    ctx.tailscale_state = (await tailscale.get_status()).backend_state
                dirty = True

            app = launcher.active_app
            if saver.tick(dt, allow=app is None or not app.keep_awake):
                dirty = True  # stage changed: dim, off, or snapped awake (wakeword)
                display.set_backlight(saver.stage != saver.OFF)

            if app is not None:
                # Apps keep running while the screen is dark/off (pomodoro timer,
                # wake word raw capture, note shipping).
                await app.update(dt)

            if app is None:
                # Home-screen LED: orange when the battery is low and we're on
                # battery (apps own the LED while active). Deduped in set_led.
                if 0 <= ctx.battery_pct < LOW_BATTERY_PCT and not ctx.battery_plugged:
                    display.set_led(255, 128, 0)
                else:
                    display.set_led(0, 0, 0)

            if app is None and not saver.active and launcher.update(dt):
                dirty = True

            if saver.stage == saver.OFF:
                pass  # backlight is off; nothing to draw
            elif saver.stage == saver.DIM:
                # Dark screen, half-bright clock; redraw on minute change.
                clock = time.strftime("%H:%M")
                if dirty or clock != saver_clock:
                    saver_clock = clock
                    display.fill()
                    display.draw_centered_text(clock, SCREEN_HEIGHT // 2 - 24,
                                               font=display.font_lg,
                                               color=(128, 128, 128))
                    display.flip()
                    dirty = False
            elif dirty or launcher.in_app:
                display.fill()

                display.draw_status_bar(
                    battery_pct=ctx.battery_pct,
                    wifi=network.is_connected,
                    tailscale_state=ctx.tailscale_state,
                    wakeword=wakeword.is_active,
                    server=xiaozhi.connected,
                    plugged=ctx.battery_plugged,
                )

                if launcher.in_app:
                    launcher.active_app.draw(display)
                else:
                    launcher.draw(display)
                    display.draw_bottom_bar("PiSugar=prev  single=next  long=launch")

                display.flip()
                dirty = False

            await asyncio.sleep(1.0 / FPS)
    finally:
        # Reached on SIGTERM (stop_event), KeyboardInterrupt, or task
        # cancellation — leave the hardware dark instead of frozen.
        log.info("Shutting down")
        try:
            power_init_task.cancel()
            audio.stop_all()
            wakeword.stop()
            await xiaozhi.disconnect()
            await whisplay_button.stop()
            await pisugar_button.stop()
        except Exception as e:
            log.warning("Teardown error: %s", e)
        finally:
            display.quit()


if __name__ == "__main__":
    asyncio.run(main())
