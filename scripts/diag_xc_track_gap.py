#!/usr/bin/env python3
"""
diag_xc_track_gap.py -- does a cross country rating's "on a track" time
match the track times the same runners actually ran? READ-ONLY.

★ WHY (owner, 2026-10-28). The 5K column reads a high school XC rating on
  the XC scale at the track's zero: a 147.2 (14:23 at Portage West, "+8.4%")
  is "13:25 on a track". Read the same way, the #1 collegian (22:31 at
  Nuttycombe, "+8.7%") is ~12:50 -- both look too fast to a coach. College
  is read through the TRACK event's scale today (13:31), which disagrees
  with its own ratings. Which is right is a question about the model's gap
  between cross country and the track, and runners who did both answer it.

  For every runner with a fall XC season (Aug-Nov of year Y) and a spring
  track race (Mar-Jun of Y+1) at the level's distance (high school 3200 /
  2 mile, college 5000) in the same level, it compares their BEST spring
  track time with what their BEST fall XC rating says, two ways:
    xc    ratingSeconds(r, pool, 'XC', d, track=True)   -- the 5K column now
    tf    ratingSeconds(r, pool, 'TF', d)               -- the track event's scale
  and prints the median ratio (predicted / actual): above 1 the conversion
  is slow, below 1 it is fast. Bests against bests, so both sides are the
  runner's sharpest day; a spring PR after a winter of growth makes high
  school read a little FAST here, which the college rows mostly do not.

    /srv/venv/bin/python scripts/diag_xc_track_gap.py [--year 2025] [--min-rating 0]
"""
import argparse
import math
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

DIST = {"hs": (3200.0, 3218.69), "college": (5000.0,)}

_SQL = """
    WITH xc AS (
        SELECT person_id, pool, max(speed_rating) AS best
        FROM   ranking_results
        WHERE  sport = 'XC' AND speed_rating IS NOT NULL
          AND  race_date::date BETWEEN make_date(%(y)s, 8, 1) AND make_date(%(y)s, 11, 30)
          AND  split_part(pool, '_', 1) IN ('hs', 'college')
        GROUP  BY 1, 2
    ),
    tf AS (
        SELECT person_id, pool, round(distance) AS d, min(time_seconds) AS best_t
        FROM   ranking_results
        WHERE  sport = 'TF' AND time_seconds > 0
          AND  race_date::date BETWEEN make_date(%(y)s + 1, 3, 1) AND make_date(%(y)s + 1, 6, 30)
          AND  time_seconds < 999999
          AND  round(distance) IN (3200, 3219, 5000)
        GROUP  BY 1, 2, 3
    )
    SELECT x.pool, x.best, t.d, t.best_t
    FROM   xc x JOIN tf t ON t.person_id = x.person_id AND t.pool = x.pool
"""


def main(year, min_rating=0.0):
    import conversions as cv
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, {"y": int(year)})
        rows = cur.fetchall()
    by = {}
    for pool, best, d, t in rows:
        level = (pool or "").split("_")[0]
        if float(d) not in [round(x) for x in DIST.get(level, ())] or best is None:
            continue
        if float(best) < min_rating:
            continue
        dd = 3218.69 if int(d) == 3219 else float(d)
        xc = cv.ratingSeconds(float(best), pool, "XC", dd, track=True)
        tf = cv.ratingSeconds(float(best), pool, "TF", dd)
        if not xc or not tf:
            continue
        band = "top (130+)" if best >= 130 else "middle (100-130)" if best >= 100 else "below 100"
        for key in ((pool, "all"), (pool, band)):
            by.setdefault(key, {"xc": [], "tf": []})
            by[key]["xc"].append(math.log(xc / float(t)))
            by[key]["tf"].append(math.log(tf / float(t)))
    print(f"[gap] fall {year} XC best rating vs spring {year + 1} track best "
          f"(median predicted/actual; >0 the conversion is SLOW, <0 FAST)\n")
    print(f"  {'pool':<10} {'ratings':<17} {'n':>6}  {'xc scale on a track':>20}  {'track-event scale':>18}")
    for (pool, band), v in sorted(by.items()):
        n = len(v["xc"])
        if n < 20:
            continue
        mx = (math.exp(statistics.median(v["xc"])) - 1) * 100
        mt = (math.exp(statistics.median(v["tf"])) - 1) * 100
        print(f"  {pool:<10} {band:<17} {n:>6}  {mx:>+19.1f}%  {mt:>+17.1f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--min-rating", type=float, default=0.0)
    a = ap.parse_args()
    main(a.year, a.min_rating)
