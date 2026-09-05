"""
shrink_images.py -- the home page's tiles as WebP, a tenth the bytes.

    /srv/venv/bin/python scripts/shrink_images.py

racecast/static/m1..m9.png and hero.png are 0.4-1.9 MB each, ~10 MB on
the home page, and Lighthouse's 7.4 s largest paint on slow 4G is
almost all of it (2026-09-05). This writes <name>.webp beside each at
most 1600 px wide, quality 80; home.html prefers the .webp when it
exists (static_exists), so nothing changes until this has run. Needs
Pillow in the venv (`pip install pillow`).
"""
import glob
import os
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is not installed: /srv/venv/bin/pip install pillow")

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "racecast", "static")
MAX_W = 1600

for path in sorted(glob.glob(os.path.join(STATIC, "m[0-9].png"))
                   + [os.path.join(STATIC, "hero.png")]):
    if not os.path.exists(path):
        continue
    out = path[:-4] + ".webp"
    im = Image.open(path).convert("RGB")
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)),
                       Image.LANCZOS)
    im.save(out, "WEBP", quality=80, method=6)
    print(f"  {os.path.basename(path):<10} {os.path.getsize(path) / 1e6:5.2f} MB "
          f"-> {os.path.basename(out):<11} {os.path.getsize(out) / 1e6:5.2f} MB")
