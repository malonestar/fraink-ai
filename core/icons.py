"""Procedural launcher icons drawn with PIL primitives.

No binary assets: assets/icons/ was never populated (PNGs are gitignored),
so the old launcher silently fell back to text rows. Each glyph is drawn at
4x and downscaled with LANCZOS for anti-aliasing, then cached per
(name, size, color) — the carousel asks for the same few variants every
frame, so the cache stays tiny (see the perf guard in the carousel plan).
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

_SS = 4  # supersample factor

_CACHE: dict[tuple, Image.Image] = {}


def _stroke(S: int) -> int:
    return max(_SS, S // 12)


def _mic(d: ImageDraw.ImageDraw, S: int, c: tuple):
    w = _stroke(S)
    d.rounded_rectangle((0.36 * S, 0.08 * S, 0.64 * S, 0.52 * S),
                        radius=0.14 * S, fill=c)
    d.arc((0.24 * S, 0.26 * S, 0.76 * S, 0.66 * S), start=0, end=180,
          fill=c, width=w)
    d.line((0.5 * S, 0.66 * S, 0.5 * S, 0.82 * S), fill=c, width=w)
    d.line((0.34 * S, 0.84 * S, 0.66 * S, 0.84 * S), fill=c, width=w)


def _note(d: ImageDraw.ImageDraw, S: int, c: tuple):
    w = _stroke(S)
    d.rounded_rectangle((0.24 * S, 0.10 * S, 0.76 * S, 0.90 * S),
                        radius=0.08 * S, outline=c, width=w)
    for fy in (0.32, 0.50, 0.68):
        d.line((0.36 * S, fy * S, 0.64 * S, fy * S), fill=c,
               width=max(2, w * 2 // 3))


def _tomato(d: ImageDraw.ImageDraw, S: int, c: tuple):
    d.ellipse((0.14 * S, 0.30 * S, 0.86 * S, 0.92 * S), fill=c)
    d.polygon([(0.50 * S, 0.34 * S), (0.34 * S, 0.12 * S),
               (0.50 * S, 0.24 * S), (0.66 * S, 0.12 * S)], fill=c)


def _house(d: ImageDraw.ImageDraw, S: int, c: tuple):
    w = _stroke(S)
    d.line((0.10 * S, 0.50 * S, 0.50 * S, 0.12 * S), fill=c, width=w)
    d.line((0.50 * S, 0.12 * S, 0.90 * S, 0.50 * S), fill=c, width=w)
    d.rectangle((0.22 * S, 0.50 * S, 0.78 * S, 0.88 * S), outline=c, width=w)
    d.rectangle((0.44 * S, 0.64 * S, 0.56 * S, 0.88 * S), fill=c)


def _wifi(d: ImageDraw.ImageDraw, S: int, c: tuple):
    w = _stroke(S)
    cx, cy = 0.5 * S, 0.80 * S
    for r in (0.18, 0.36, 0.54):
        R = r * S
        d.arc((cx - R, cy - R, cx + R, cy + R), start=225, end=315,
              fill=c, width=w)
    d.ellipse((cx - 0.05 * S, cy - 0.05 * S, cx + 0.05 * S, cy + 0.05 * S),
              fill=c)


def _gear(d: ImageDraw.ImageDraw, S: int, c: tuple):
    w = _stroke(S)
    cx = cy = 0.5 * S
    r_ring, r_tooth = 0.28 * S, 0.42 * S
    for k in range(8):
        a = k * math.pi / 4
        d.line((cx + r_ring * math.cos(a), cy + r_ring * math.sin(a),
                cx + r_tooth * math.cos(a), cy + r_tooth * math.sin(a)),
               fill=c, width=int(w * 1.6))
    d.ellipse((cx - r_ring, cy - r_ring, cx + r_ring, cy + r_ring),
              outline=c, width=w)
    d.ellipse((cx - 0.10 * S, cy - 0.10 * S, cx + 0.10 * S, cy + 0.10 * S),
              outline=c, width=w)


def _tailscale(d: ImageDraw.ImageDraw, S: int, c: tuple):
    # 3x3 dot grid; bottom row + center solid, the rest faint (Tailscale mark).
    r = 0.09 * S
    solid = {(0, 2), (1, 2), (2, 2), (1, 1)}
    faint = c[:3] + (70,)
    for gx in range(3):
        for gy in range(3):
            cx = (0.22 + 0.28 * gx) * S
            cy = (0.22 + 0.28 * gy) * S
            d.ellipse((cx - r, cy - r, cx + r, cy + r),
                      fill=c if (gx, gy) in solid else faint)


def _translate(d: ImageDraw.ImageDraw, S: int, c: tuple):
    w = _stroke(S)
    # two speech bubbles trading places
    d.rounded_rectangle((0.08 * S, 0.10 * S, 0.58 * S, 0.46 * S),
                        radius=0.08 * S, outline=c, width=w)
    d.polygon([(0.20 * S, 0.45 * S), (0.34 * S, 0.45 * S),
               (0.20 * S, 0.58 * S)], fill=c)
    for fy in (0.22, 0.32):
        d.line((0.18 * S, fy * S, 0.48 * S, fy * S), fill=c,
               width=max(2, w * 2 // 3))
    d.rounded_rectangle((0.42 * S, 0.52 * S, 0.92 * S, 0.88 * S),
                        radius=0.08 * S, outline=c, width=w)
    d.polygon([(0.66 * S, 0.53 * S), (0.80 * S, 0.53 * S),
               (0.80 * S, 0.40 * S)], fill=c)
    for fy in (0.64, 0.74):
        d.line((0.52 * S, fy * S, 0.82 * S, fy * S), fill=c,
               width=max(2, w * 2 // 3))


def _meeting(d: ImageDraw.ImageDraw, S: int, c: tuple):
    w = _stroke(S)
    # clipboard with action-item rows
    d.rounded_rectangle((0.22 * S, 0.16 * S, 0.78 * S, 0.90 * S),
                        radius=0.06 * S, outline=c, width=w)
    d.rounded_rectangle((0.40 * S, 0.08 * S, 0.60 * S, 0.22 * S),
                        radius=0.04 * S, fill=c)
    for fy in (0.38, 0.54, 0.70):
        r = 0.035 * S
        d.ellipse((0.32 * S - r, fy * S - r, 0.32 * S + r, fy * S + r), fill=c)
        d.line((0.42 * S, fy * S, 0.68 * S, fy * S), fill=c,
               width=max(2, w * 2 // 3))


def _fallback(d: ImageDraw.ImageDraw, S: int, c: tuple):
    w = _stroke(S)
    d.rounded_rectangle((0.18 * S, 0.18 * S, 0.82 * S, 0.82 * S),
                        radius=0.12 * S, outline=c, width=w)


_GLYPHS = {
    "voice": _mic,
    "notes": _note,
    "timer": _tomato,
    "ha": _house,
    "wifi": _wifi,
    "settings": _gear,
    "tailscale": _tailscale,
    "translate": _translate,
    "meeting": _meeting,
}


def render(name: str, size: int, color: tuple) -> Image.Image:
    """Render (and cache) an icon glyph at `size` px in `color` (RGB)."""
    key = (name, size, tuple(color[:3]))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    S = size * _SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rgba = tuple(color[:3]) + (255,)
    _GLYPHS.get(name, _fallback)(d, S, rgba)
    img = img.resize((size, size), Image.LANCZOS)
    _CACHE[key] = img
    return img
