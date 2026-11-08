#!/usr/bin/env python3
"""
warm_site.py -- after the site starts, ask it for the pages most visitors
land on, so the first real visitor to each one is not the one who pays to
build it.

    /srv/venv/bin/python scripts/warm_site.py            # default list
    XCP_WARM_URL=http://127.0.0.1:8000 XCP_WARM_DELAY=20 ...

★ WHY (2026-11-08). After a restart or a reboot every worker's caches are
  empty and Postgres reads from disk: the first view of the front page, each
  board and each state's "what it takes" line built it from nothing. The
  pages that are rendered once for the box (the front page, /scale, the
  shared caches) then serve every worker from what this run built.

! IT IS A VISITOR LIKE ANY OTHER. Plain GETs through the site's own port with
  the Host nginx passes (racecast.co), so the copies it fills are the ones a
  real request reads; nothing here touches the database directly. Started by
  deploy/gunicorn.conf.py's when_ready (a separate process, never a thread in
  the master); a failure is printed and ignored.
"""
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("XCP_WARM_URL") or "http://127.0.0.1:8000"
HOST = os.environ.get("XCP_WARM_HOST") or "racecast.co"
DELAY = float(os.environ.get("XCP_WARM_DELAY") or 20)
WORKERS = int(os.environ.get("XCP_WARM_WORKERS") or 3)

_POOLS = ("hs_m", "hs_f", "college_m", "college_f", "ms_m", "ms_f")

PATHS = (["/", "/rankings", "/scale", "/predictions", "/meets", "/records",
          "/recruiting", "/conversions", "/breakouts", "/projections",
          "/rankings/xc/hs-boys", "/rankings/xc/hs-girls"]
         + [f"/api/rankings?board={b}&sport=XC&pool={p}&limit=50"
            for b in ("ability", "performance") for p in _POOLS])


def fetch(path):
    req = urllib.request.Request(BASE + path, headers={
        "Host": HOST, "User-Agent": "racecast-warm/1.0 (racecast@racecast.co)"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            r.read()
            return path, r.status, time.time() - t
    except urllib.error.HTTPError as exc:
        return path, exc.code, time.time() - t
    except Exception as exc:                            # noqa: BLE001
        return path, type(exc).__name__, time.time() - t


# ★ THE SEARCH BOX'S FIRST TWO LETTERS (2026-11-08, p99). The dropdown asks
#   /search/api?q=<text> from two characters; a two-letter query matches a
#   hundred thousand rows or more (0.3-1.5 s), and its answer is shared by
#   every worker until search_index is rebuilt (app._searchShared). Asked
#   once, one at a time, after the pages: ~676 requests of a few tenths of
#   a second on one connection. XCP_WARM_SEARCH=0 skips them.
_LETTERS = "abcdefghijklmnopqrstuvwxyz"
SEARCH_PATHS = [f"/search/api?q={a}{b}" for a in _LETTERS for b in _LETTERS]


def main():
    time.sleep(DELAY)
    t0 = time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(fetch, PATHS))
    slow = sorted(results, key=lambda r: -r[2])[:5]
    bad = [r for r in results if r[1] != 200]
    print(f"[warm] {len(results)} pages in {time.time() - t0:.1f}s; slowest: "
          + ", ".join(f"{p} {s:.1f}s" for p, _c, s in slow)
          + (f"; NOT 200: {bad}" if bad else ""), flush=True)
    if os.environ.get("XCP_WARM_SEARCH", "1") == "1":
        t1 = time.time()
        res = [fetch(p) for p in SEARCH_PATHS]
        bad = [r for r in res if r[1] != 200]
        print(f"[warm] {len(res)} two-letter searches in {time.time() - t1:.0f}s"
              + (f"; {len(bad)} not 200" if bad else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
