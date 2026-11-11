import logging
import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

from core.screencap import ScreenCapture

log = logging.getLogger(__name__)

SCREEN_WIDTH = 240
SCREEN_HEIGHT = 280
FPS = 30
CORNER_RADIUS = 20

COLOR_BG = (0, 0, 0)
COLOR_TEXT = (255, 255, 255)
COLOR_HIGHLIGHT = (0, 120, 215)
COLOR_DIM = (128, 128, 128)
COLOR_SUCCESS = (0, 200, 80)
COLOR_ERROR = (200, 50, 50)
COLOR_BAR_BG = (30, 30, 30)

STATUS_BAR_HEIGHT = 24
BOTTOM_BAR_HEIGHT = 36
CORNER_INSET = 22

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _image_to_rgb565(image: Image.Image) -> bytes:
    try:
        import numpy as np
        arr = np.asarray(image.convert("RGB"), dtype=np.uint16)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        return rgb565.astype(">u2").tobytes()
    except ImportError:
        pass
    image = image.convert("RGB")
    pixels = image.tobytes()
    length = len(pixels) // 3
    output = bytearray(length * 2)
    for i in range(length):
        off = i * 3
        r, g, b = pixels[off], pixels[off + 1], pixels[off + 2]
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        output[i * 2] = (rgb565 >> 8) & 0xFF
        output[i * 2 + 1] = rgb565 & 0xFF
    return bytes(output)


class Display:
    def __init__(self, headless: bool = False):
        self._headless = headless
        self._board = None

        if not headless:
            try:
                sys.path.insert(0, os.path.expanduser("~/Whisplay/runtime"))
                from whisplay import WhisplayBoard
                self._board = WhisplayBoard()
                # Disable SoftPWM backlight — use simple on/off to avoid flicker
                self._board.set_backlight_mode(False)
                self._board.set_backlight(100)
                log.info("WhisplayBoard initialized (SPI)")
            except Exception as e:
                log.warning("WhisplayBoard unavailable: %s (headless mode)", e)
                self._headless = True

        self.canvas = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), COLOR_BG)
        self.draw_ctx = ImageDraw.Draw(self.canvas)
        self._prev_frame: bytes = b""
        self._last_tick = time.monotonic()
        self._frame_count = 0
        self._backlight_on = True
        self._led: tuple | None = None
        self.capture = ScreenCapture()

        self.font_sm = _load_font(12)
        self.font_md = _load_font(15)
        self.font_lg = _load_font(22)
        self.font_xl = _load_font(44)

    def tick(self) -> float:
        # Non-blocking: just report elapsed time. Frame pacing is done by the
        # main loop's `await asyncio.sleep`, which keeps the event loop free for
        # button input. A blocking time.sleep() here would stall the whole loop.
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        return dt

    def set_raw_button_callbacks(self, on_press=None, on_release=None):
        """Register raw GPIO button press/release callbacks with the WhisPlay
        board. Callbacks fire from the board's polling thread. No-op headless."""
        if not self._board:
            return
        self._board.on_button_press(on_press)
        self._board.on_button_release(on_release)

    def fill(self, color=COLOR_BG):
        self.draw_ctx.rectangle(
            [0, 0, SCREEN_WIDTH - 1, SCREEN_HEIGHT - 1], fill=color
        )

    def draw_status_bar(self, battery_pct: int = -1, wifi: bool = False,
                        tailscale_state: str = "NotInstalled", wakeword: bool = False,
                        server: bool = False, plugged: bool = False):
        self.draw_ctx.rectangle(
            [0, 0, SCREEN_WIDTH - 1, STATUS_BAR_HEIGHT - 1], fill=COLOR_BAR_BG
        )
        x = CORNER_INSET
        y = (STATUS_BAR_HEIGHT - 12) // 2
        indicators = []
        indicators.append(("W", COLOR_SUCCESS if wifi else COLOR_ERROR))
        if tailscale_state == "Running":
            indicators.append(("TS", COLOR_SUCCESS))
        elif tailscale_state != "NotInstalled":
            indicators.append(("TS", COLOR_DIM))
        indicators.append(("S", COLOR_SUCCESS if server else COLOR_ERROR))
        if wakeword:
            indicators.append(("WW", COLOR_SUCCESS))
        for label, color in indicators:
            bbox = self.font_sm.getbbox(label)
            w = bbox[2] - bbox[0]
            self.draw_ctx.text((x, y), label, fill=color, font=self.font_sm)
            x += w + 6
        if battery_pct >= 0:
            batt = f"{battery_pct}%"
            bbox = self.font_sm.getbbox(batt)
            w = bbox[2] - bbox[0]
            batt_x = SCREEN_WIDTH - w - CORNER_INSET
            # Green % + lightning bolt while on external power. The bolt is
            # drawn (DejaVu has no U+26A1 glyph).
            self.draw_ctx.text(
                (batt_x, y),
                batt, fill=COLOR_SUCCESS if plugged else COLOR_TEXT,
                font=self.font_sm
            )
            if plugged:
                bx = batt_x - 12
                by = y + 1
                self.draw_ctx.polygon(
                    [(bx + 6, by), (bx + 1, by + 7), (bx + 4, by + 7),
                     (bx + 2, by + 12), (bx + 8, by + 5), (bx + 5, by + 5)],
                    fill=COLOR_SUCCESS,
                )

    def draw_bottom_bar(self, text: str, color=COLOR_DIM):
        y0 = SCREEN_HEIGHT - BOTTOM_BAR_HEIGHT
        self.draw_ctx.rectangle(
            [0, y0, SCREEN_WIDTH - 1, SCREEN_HEIGHT - 1], fill=COLOR_BAR_BG
        )
        bbox = self.font_sm.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (SCREEN_WIDTH - tw) // 2
        y = y0 + (BOTTOM_BAR_HEIGHT - th) // 2
        self.draw_ctx.text((x, y), text, fill=color, font=self.font_sm)

    def draw_centered_text(self, text: str, y: int, font=None, color=COLOR_TEXT):
        font = font or self.font_md
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        x = (SCREEN_WIDTH - tw) // 2
        self.draw_ctx.text((x, y), text, fill=color, font=font)

    def draw_text(self, text: str, x: int, y: int, font=None, color=COLOR_TEXT):
        font = font or self.font_md
        self.draw_ctx.text((x, y), text, fill=color, font=font)

    def draw_wrapped_text(self, text: str, rect_x: int, rect_y: int,
                          rect_w: int, rect_h: int,
                          font=None, color=COLOR_TEXT) -> int:
        font = font or self.font_sm
        words = text.split()
        lines = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            bbox = font.getbbox(test)
            if (bbox[2] - bbox[0]) <= rect_w:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        y = rect_y
        line_h = font.getbbox("Ay")[3] - font.getbbox("Ay")[1] + 2
        for line in lines:
            if y + line_h > rect_y + rect_h:
                break
            self.draw_ctx.text((rect_x, y), line, fill=color, font=font)
            y += line_h
        return y

    def draw_highlight_row(self, y: int, height: int):
        self.draw_ctx.rectangle(
            [0, y, SCREEN_WIDTH - 1, y + height - 1], fill=COLOR_HIGHLIGHT
        )

    def draw_progress_bar(self, x: int, y: int, width: int, height: int,
                          progress: float, color=COLOR_HIGHLIGHT):
        self.draw_ctx.rectangle(
            [x, y, x + width - 1, y + height - 1], fill=COLOR_BAR_BG
        )
        fill_w = int(width * max(0.0, min(1.0, progress)))
        if fill_w > 0:
            self.draw_ctx.rectangle(
                [x, y, x + fill_w - 1, y + height - 1], fill=color
            )

    def draw_rect(self, x: int, y: int, w: int, h: int, color):
        if w <= 0 or h <= 0:
            return
        self.draw_ctx.rectangle([x, y, x + w - 1, y + h - 1], fill=color)

    def draw_ellipse(self, x0: int, y0: int, x1: int, y1: int, color):
        if x1 <= x0 or y1 <= y0:
            return
        self.draw_ctx.ellipse([x0, y0, x1, y1], fill=color)

    def draw_session_dots(self, cx: int, y: int, total: int, filled: int,
                          radius: int = 4, gap: int = 14,
                          on=COLOR_HIGHLIGHT, off=COLOR_DIM):
        if total <= 0:
            return
        span = (total - 1) * gap
        x = cx - span // 2
        for i in range(total):
            color = on if i < filled else off
            self.draw_ctx.ellipse(
                [x - radius, y - radius, x + radius, y + radius], fill=color)
            x += gap

    def draw_pause_glyph(self, cx: int, cy: int, size: int, paused: bool,
                         color=COLOR_DIM):
        h = size
        if paused:
            # two vertical bars
            bar_w = max(3, size // 4)
            gap = max(3, size // 5)
            for sign in (-1, 1):
                x0 = cx + sign * (gap // 2 + (bar_w if sign > 0 else 0)) - (0 if sign > 0 else bar_w)
                self.draw_ctx.rectangle([x0, cy - h // 2, x0 + bar_w, cy + h // 2], fill=color)
        else:
            # play triangle
            self.draw_ctx.polygon(
                [(cx - h // 3, cy - h // 2), (cx - h // 3, cy + h // 2),
                 (cx + 2 * h // 3, cy)], fill=color)

    def flip(self):
        if not self._board:
            return
        rgb565 = _image_to_rgb565(self.canvas)
        if rgb565 != self._prev_frame:
            self._board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, rgb565)
            self._prev_frame = rgb565
            if self.capture.active:
                self.capture.offer(rgb565)

    def set_capture(self, on: bool) -> str:
        """Start/stop recording pushed frames (Settings -> Screen Rec)."""
        if on:
            path = self.capture.start()
            if self._prev_frame:
                self.capture.offer(self._prev_frame)  # current screen as frame 0
            if self._led is not None:
                self.capture.event("led %d,%d,%d" % self._led)
            return path
        self.capture.stop()
        return self.capture.dir

    def set_led(self, r: int, g: int, b: int):
        """Set the WhisPlay RGB LED (deduped — safe to call every frame).
        Prefer pure 0/255 components: they hold the line steady, while
        intermediate SoftPWM duty cycles can flicker on the Pi Zero 2W."""
        if not self._board or (r, g, b) == self._led:
            return
        self._led = (r, g, b)
        if self.capture.active:
            self.capture.event(f"led {r},{g},{b}")
        try:
            self._board.set_rgb(r, g, b)
        except Exception as e:
            log.debug("set_led failed: %s", e)

    def set_backlight(self, on: bool):
        """Binary backlight switch (deduped). PWM dimming is deliberately
        not exposed — SoftPWM backlight flickers on the Pi Zero 2W."""
        if not self._board or on == self._backlight_on:
            return
        self._backlight_on = on
        try:
            self._board.set_backlight(100 if on else 0)
        except Exception as e:
            log.debug("set_backlight failed: %s", e)

    def quit(self):
        self.capture.stop()
        if self._board:
            self.set_led(0, 0, 0)
            self._board.fill_screen(0)
            self._board.set_backlight(0)
            self._board.cleanup()
