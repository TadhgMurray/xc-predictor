#!/usr/bin/env python3
"""make_app_icons.py -- the home-screen icons, drawn from favicon.svg's bars.

    python scripts/make_app_icons.py      # writes racecast/static/icons/*.png

★ WHY A SCRIPT AND NOT AN EXPORT. The favicon is five skewed bars in an SVG;
  phones want PNGs (Android 192/512, a "maskable" 512 the launcher may crop
  to a circle, iOS 180). Pillow draws the same geometry so the icons cannot
  drift from the favicon -- re-run this if favicon.svg changes.

! MASKABLE = THE SAFE ZONE. Android may crop a maskable icon to any shape
  inside the central 80% circle, so that variant draws the bars at 60% of
  the canvas; the plain ones fill the 128-unit box as the favicon does.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "racecast", "static", "icons")
INK = (17, 17, 17, 255)
BG = (255, 255, 255, 255)
SKEW = 0.17633                     # tan(10 deg), favicon's skewX(-10)
SHIFT = 11.0                       # favicon's translate(11 0)
# favicon.svg's bars: x, y, w, h, rx in its 128-unit box
BARS = [(16.00, 71.68, 17.0, 40.32), (35.75, 42.88, 17.0, 69.12),
        (55.50, 16.00, 17.0, 96.00), (75.25, 42.88, 17.0, 69.12),
        (95.00, 71.68, 17.0, 40.32)]
RX = 6.54
SS = 4                             # supersample, then downscale: smooth edges


def _bar(draw, x, y, w, h, s, off):
    """One rounded bar, sheared the way skewX(-10) shears it: x' = x - y*tan."""
    pts = []
    import math
    r = RX
    # rounded rectangle outline (corner arcs), then shear each point
    for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0),
                       (x + r, y + h - r, 90), (x + r, y + r, 180)):
        for k in range(0, 91, 10):
            a = math.radians(a0 + k)
            px, py = cx + r * math.cos(a), cy + r * math.sin(a)
            px = px + SHIFT - py * SKEW
            pts.append((off[0] + px * s, off[1] + py * s))
    draw.polygon(pts, fill=INK)


def icon(size, frac):
    """size px square; the 128-unit box drawn at frac of the canvas, centred."""
    big = size * SS
    img = Image.new("RGBA", (big, big), BG)
    d = ImageDraw.Draw(img)
    s = big * frac / 128.0
    # centre the sheared drawing: its x extent is [16+11-112*tan, 112+11-16*tan]
    x0 = 16 + SHIFT - 112 * SKEW
    x1 = 112 + SHIFT - 16 * SKEW
    off = ((big - (x0 + x1) * s) / 2, (big - 128 * s) / 2)
    for b in BARS:
        _bar(d, *b, s, off)
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, size, frac in (("icon-192.png", 192, 1.0), ("icon-512.png", 512, 1.0),
                             ("icon-maskable-512.png", 512, 0.6),
                             ("apple-touch-icon.png", 180, 0.8)):
        icon(size, frac).convert("RGB").save(os.path.join(OUT, name), optimize=True)
        print("wrote", name)


if __name__ == "__main__":
    main()
