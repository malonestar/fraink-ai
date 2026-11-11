from __future__ import annotations

import enum
import math
from dataclasses import dataclass


class Expression(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class ExprParams:
    eye_open: float   # 0..1 resting eyelid openness
    gaze_y: float     # -1 (up) .. 1 (down) bias added to drift
    drift: bool       # apply idle gaze drift


EXPRESSION_PARAMS = {
    Expression.IDLE:      ExprParams(eye_open=0.55, gaze_y=0.0,  drift=True),
    Expression.LISTENING: ExprParams(eye_open=1.0,  gaze_y=0.0,  drift=False),
    Expression.THINKING:  ExprParams(eye_open=0.7,  gaze_y=-1.0, drift=False),
    Expression.SPEAKING:  ExprParams(eye_open=0.9,  gaze_y=0.0,  drift=False),
}


def blink_open_factor(t: float, cycle: float = 4.8, close: float = 0.18) -> float:
    """1.0 = eyes open, 0.0 = shut. Shut only during the last `close` seconds
    of each `cycle`, with a quick symmetric ramp so it reads as a blink."""
    phase = t % cycle
    into_close = phase - (cycle - close)
    if into_close < 0:
        return 1.0
    # Triangle: shut at the midpoint of the close window.
    half = close / 2.0
    return abs(into_close - half) / half


def breath_offset(t: float, cycle: float = 5.0, amp: float = 3.0) -> float:
    return math.sin((t % cycle) / cycle * 2.0 * math.pi) * amp


def gaze_drift(t: float) -> tuple[float, float]:
    """Calm deterministic wander in [-1, 1]^2."""
    return (math.sin(t * 0.37), math.cos(t * 0.23) * 0.6)


def mouth_smooth(current: float, target: float, dt: float, rate: float = 12.0) -> float:
    return current + (target - current) * min(1.0, dt * rate)


def eye_open_for(expr: Expression, blink: float) -> float:
    return EXPRESSION_PARAMS[expr].eye_open * blink


from core.display import (
    SCREEN_WIDTH, STATUS_BAR_HEIGHT, BOTTOM_BAR_HEIGHT, COLOR_BG, COLOR_TEXT,
)

_CONTENT_TOP = STATUS_BAR_HEIGHT
_CONTENT_BOTTOM = 280 - BOTTOM_BAR_HEIGHT
_CX = SCREEN_WIDTH // 2                       # 120
_CY = (_CONTENT_TOP + _CONTENT_BOTTOM) // 2   # 134

EYE_DX = 52
EYE_CY = 116
EYE_DIAM = 34
EYE_MIN, EYE_MAX = 14, 44
GAZE_X, GAZE_Y = 12, 10
MOUTH_CY = 172
MOUTH_W = 64
MOUTH_MIN_H, MOUTH_MAX_H = 8, 52


class Face:
    """Procedural StackChan-style avatar. Time-driven; call update(dt) each
    frame then draw(display). State is set via set_state()."""

    def __init__(self, color=COLOR_TEXT, bg=COLOR_BG):
        self.color = color
        self.bg = bg
        self.clock = 0.0
        self.expr = Expression.IDLE
        self.audio_level = 0.0
        self.mouth_open = 0.0

    def set_state(self, expr: Expression, audio_level: float = 0.0):
        self.expr = expr
        self.audio_level = max(0.0, min(1.0, audio_level))

    def _mouth_target(self) -> float:
        if self.expr == Expression.SPEAKING:
            return min(1.0, self.audio_level * 1.6)
        return 0.0

    def update(self, dt: float):
        self.clock += dt
        self.mouth_open = mouth_smooth(self.mouth_open, self._mouth_target(), dt)

    def draw(self, display):
        p = EXPRESSION_PARAMS[self.expr]
        blink = blink_open_factor(self.clock)
        eye_open = eye_open_for(self.expr, blink)
        bob = breath_offset(self.clock)

        gx, gy = (gaze_drift(self.clock) if p.drift else (0.0, 0.0))
        gy += p.gaze_y
        off_x = int(gx * GAZE_X)
        off_y = int(max(-1.0, min(1.0, gy)) * GAZE_Y + bob)

        # Eyes
        r = EYE_DIAM // 2
        for sign in (-1, +1):
            cx = _CX + sign * EYE_DX + off_x
            cy = EYE_CY + off_y
            display.draw_ellipse(cx - r, cy - r, cx + r, cy + r, self.color)
            # Eyelid: black rect from the top covering (1-eye_open) of height
            lid_h = int((1.0 - eye_open) * EYE_DIAM)
            if lid_h > 0:
                display.draw_rect(cx - r, cy - r, EYE_DIAM, lid_h, self.bg)

        # Mouth: ellipse, height grows with openness
        h = int(MOUTH_MIN_H + self.mouth_open * (MOUTH_MAX_H - MOUTH_MIN_H))
        mx, my = _CX + off_x, MOUTH_CY + off_y
        display.draw_ellipse(mx - MOUTH_W // 2, my - h // 2,
                             mx + MOUTH_W // 2, my + h // 2, self.color)
