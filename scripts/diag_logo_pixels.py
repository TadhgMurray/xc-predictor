#!/usr/bin/env python3
"""
diag_logo_pixels.py -- what is actually IN a stored crest, so the black
background is diagnosed instead of theorised.

    python scripts/diag_logo_pixels.py --school Amherst
    python scripts/diag_logo_pixels.py --worst 20        # the darkest stored
    python scripts/diag_logo_pixels.py --file 8310f01af241205c.png
    python scripts/diag_logo_pixels.py --url 'https://lh3...=s512'

★ WHY (owner, 2026-09-17: "How do you want to prove the backgrounds?"). I
  have been reasoning about files I cannot see -- this sandbox's proxy 403s
  athletic.net, so every claim about where the black comes from has been a
  theory. This reads the PNG on disk and prints the four things that
  separate the possible causes:

    mode / bands      what the file IS
    corner pixels     the ground, as stored
    alpha histogram   whether there is any transparency at all
    opaque share      how much of the square is painted

  Then the answer is not a guess:

    corners (0,0,0,255) and alpha all 255
        an OPAQUE BLACK ground. normalise's keying handles it -- but only
        when the file is WRITTEN, so an existing file needs a re-fetch.
    corners (0,0,0,0) and alpha mostly 0
        the file is fine and transparent; the black is coming from
        somewhere else (a CSS surface, a thumbnail, a viewer) and every
        fix in the scraper is irrelevant.
    corners near-black but not equal, mode CMYK on the source
        a CMYK JPEG read as RGB -- a different bug, in Pillow's hands,
        fixed by converting through ImageCms or by inverting.
    corners (255,255,255,255)
        a WHITE ground survived, so keying did not run on this file at
        all: it predates the fix.

⚠ IT READS, IT NEVER WRITES. Safe to run against the live logo directory
  while anything else is running.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def describe(path_or_bytes, label=""):
    """Print what one image is. Returns a dict of the same facts."""
    from PIL import Image
    import io as _io
    try:
        if isinstance(path_or_bytes, (bytes, bytearray)):
            im = Image.open(_io.BytesIO(path_or_bytes))
            size_note = f"{len(path_or_bytes):,} bytes"
        else:
            im = Image.open(path_or_bytes)
            size_note = f"{os.path.getsize(path_or_bytes):,} bytes"
        im.load()
    except Exception as exc:                                  # noqa: BLE001
        print(f"  {label}: unreadable ({type(exc).__name__}: {exc})")
        return None
    raw_mode, fmt = im.mode, im.format
    rgba = im.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    alpha = rgba.getchannel("A").histogram()
    n = w * h
    clear = alpha[0]
    solid = alpha[255]
    opaque_share = solid / n if n else 0.0
    # the mean luminance of the fully opaque pixels: a black ground shows up
    # here even when the corners were cropped away
    lum_sum = lum_n = 0
    for x in range(0, w, max(1, w // 64)):
        for y in range(0, h, max(1, h // 64)):
            r, g, b, a = px[x, y]
            if a > 200:
                lum_sum += (r * 299 + g * 587 + b * 114) // 1000
                lum_n += 1
    lum = (lum_sum / lum_n) if lum_n else None

    verdict = "?"
    if all(c[3] >= 250 for c in corners):
        if max(max(c[:3]) for c in corners) <= 24:
            verdict = "OPAQUE BLACK GROUND -- needs a re-fetch to be keyed"
        elif min(min(c[:3]) for c in corners) >= 232:
            verdict = "OPAQUE WHITE GROUND -- predates the keying fix"
        else:
            verdict = "opaque coloured ground"
    elif clear > n * 0.2:
        verdict = "TRANSPARENT -- the file is fine; the black is elsewhere"
    else:
        verdict = "mostly opaque, corners transparent (cropped mark)"

    print(f"  {label}")
    print(f"      {fmt} {raw_mode} {w}x{h}  {size_note}")
    print(f"      corners      {corners}")
    print(f"      alpha        {clear:,} clear / {solid:,} solid of {n:,} "
          f"({opaque_share:.0%} opaque)")
    print(f"      mean lum     {lum:.0f} of 255" if lum is not None else
          "      mean lum     n/a")
    print(f"      -> {verdict}")
    return {"mode": raw_mode, "format": fmt, "corners": corners,
            "clear": clear, "solid": solid, "n": n, "lum": lum,
            "verdict": verdict}


def _rows(cur, school=None, limit=20):
    where, params = [], {}
    if school:
        where.append("school ILIKE %(s)s")
        params["s"] = f"%{school}%"
    sql = ("SELECT school, state, COALESCE(level, '') AS level, path, kind, "
           "sha, fetched FROM school_logo WHERE path IS NOT NULL")
    if where:
        sql += " AND " + " AND ".join(where)
    sql += " ORDER BY school, state LIMIT %(lim)s"
    params["lim"] = int(limit)
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) if not isinstance(r, dict) else r
            for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--school", default=None, help="ILIKE match")
    ap.add_argument("--file", default=None, help="one stored file name")
    ap.add_argument("--url", default=None, help="fetch and describe a URL")
    ap.add_argument("--worst", type=int, default=0,
                    help="scan this many stored crests and list the darkest")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--dir", default=None, help="default XCP_LOGO_DIR")
    a = ap.parse_args()

    import school_logo as SL
    logo_dir = a.dir or SL.LOGO_DIR
    print(f"\n[logo pixels] {logo_dir}\n")

    if a.url:
        import urllib.request
        req = urllib.request.Request(a.url, headers={"User-Agent": "xcp/1.0"})
        describe(urllib.request.urlopen(req, timeout=20).read(), a.url[:70])
        return 0

    if a.file:
        describe(os.path.join(logo_dir, a.file), a.file)
        return 0

    if a.worst:
        # ! NO DATABASE NEEDED for this one: the darkest files on disk are
        #   the evidence, whatever row points at them.
        names = sorted(f for f in os.listdir(logo_dir) if f.endswith(".png"))
        print(f"  scanning {min(len(names), a.worst):,} of {len(names):,} files")
        got = []
        for name in names[:a.worst]:
            d = describe(os.path.join(logo_dir, name), name)
            if d and d["lum"] is not None:
                got.append((d["lum"], name, d["verdict"]))
        got.sort()
        print("\n  DARKEST FIRST")
        for lum, name, verdict in got[:a.limit]:
            print(f"    lum {lum:>5.0f}  {name}  {verdict}")
        return 0

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        rows = _rows(cur, a.school, a.limit)
    if not rows:
        print("  no school_logo rows matched")
        return 1
    for r in rows:
        label = (f"{r['school']!r} ({r['state']}"
                 + (f", {r['level']}" if r["level"] else "")
                 + f")  kind={r['kind']} fetched={r['fetched']} {r['path']}")
        describe(os.path.join(logo_dir, r["path"]), label)
        print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
