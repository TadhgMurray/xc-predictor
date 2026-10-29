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
  2 mile, and 5000; college 5000) in the same level, it compares their
  BEST spring track time with what their BEST fall XC rating says, two ways:
    xc    the XC scale at the track's zero                -- the 5K column then
    tf    ratingSeconds(r, pool, 'TF', d)               -- the track event's scale
  and prints the median ratio (predicted / actual): above 1 the conversion
  is slow, below 1 it is fast. Bests against bests, so both sides are the
  runner's sharpest day; a spring PR after a winter of growth makes high
  school read a little FAST here, which the college rows mostly do not.

★ BEFORE AND AFTER IN ONE RUN (2026-10-29). The course pages now read a
  cross country % against where the average outdoor track lands on the XC
  scale in seconds (conversions.xcTrackZero), not against the stored 0.0.
  So the table prints the XC scale at both zeros:
    xc @0.0     the XC scale at difficulty 0.0 -- the old "track"
    xc @track   the XC scale at the course pages' track, e^z - 1 with z
                the one pooled number every course % is read against
  and, per pool, the model's own z at 3200 and at 5000 (the two scales'
  distance curves differ in shape, so the gap moves with the distance) and
  the pooled z. "xc @track" agreeing with the track column is the check
  that a course's "+X% slower than an average outdoor track" is true.

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

# ! HIGH SCHOOL AT 5000 TOO (2026-10-29): a course % is a 5K course against
#   a 5K track, and the 3200 rows also measure the XC distance curve's
#   3200-5000 shape (a Riegel exponent of ~1.10 for high school, against
#   ~1.07 on the track curves). The 5000 rows are few; they print when 20+.
DIST = {"hs": (3200.0, 3218.69, 5000.0), "college": (5000.0,)}

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


def xcAt(cv, rating, pool, d, difficulty):
    """Seconds on the XC scale at a stated course difficulty."""
    norm = cv._norm_from_rating(rating, pool, 0.0, "XC")
    if not norm:
        return None
    t = cv.normalized_to_time(norm, {"distance": d, "pool": pool, "sport": "XC",
                                     "difficulty": difficulty})
    return t if t and t > 0 else None


def zAt(cv, pool, d):
    """The model's gap for a 100 at `d`: ln(track / XC at 0.0) / tilt."""
    xc = xcAt(cv, 100.0, pool, d, 0.0)
    tf = cv.ratingSeconds(100.0, pool, "TF", d)
    if not xc or not tf:
        return None
    return math.log(tf / xc) / cv._tilt(100.0, pool)


def main(year, min_rating=0.0):
    import conversions as cv
    from database import getConn
    z = cv.xcTrackZero()
    track_d = math.expm1(z)
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
        xc = xcAt(cv, float(best), pool, dd, 0.0)
        xz = xcAt(cv, float(best), pool, dd, track_d)
        tf = cv.ratingSeconds(float(best), pool, "TF", dd)
        if not xc or not xz or not tf:
            continue
        band = "top (130+)" if best >= 130 else "middle (100-130)" if best >= 100 else "below 100"
        dm = 5000 if int(d) == 5000 else 3200         # 3200 and 2 mile together
        for key in ((pool, dm, "all"), (pool, dm, band)):
            by.setdefault(key, {"xc": [], "xz": [], "tf": []})
            by[key]["xc"].append(math.log(xc / float(t)))
            by[key]["xz"].append(math.log(xz / float(t)))
            by[key]["tf"].append(math.log(tf / float(t)))
    print(f"[gap] fall {year} XC best rating vs spring {year + 1} track best "
          f"(median predicted/actual; >0 the conversion is SLOW, <0 FAST)\n")
    print(f"  {'pool':<10} {'track':>5} {'ratings':<17} {'n':>6}  {'xc @0.0 (before)':>17}  "
          f"{'xc @track (after)':>18}  {'track-event scale':>18}")
    for (pool, dm, band), v in sorted(by.items()):
        n = len(v["xc"])
        if n < 20:
            continue
        mx, mz, mt = ((math.exp(statistics.median(v[k])) - 1) * 100
                      for k in ("xc", "xz", "tf"))
        print(f"  {pool:<10} {dm:>5} {band:<17} {n:>6}  {mx:>+16.1f}%  {mz:>+17.1f}%  {mt:>+17.1f}%")

    # the zero itself: per pool, the model's gap at both distances
    print(f"\n[gap] where the average outdoor track sits on the XC scale, in "
          f"seconds, for a 100 (conversions.xcTrackZero; + = the XC scale's "
          f"0.0 is faster than a track)")
    print(f"  {'pool':<10} {'at 3200':>8} {'at 5000':>8} {'used':>8} {'xc rows':>9}")
    rows = cv._scale.get("rows") or {}
    for (p, sp) in sorted(cv._scale["map"]):
        if sp != "XC" or (p or "").partition("_")[0] not in cv.XC_TRACK_M:
            continue
        g = [zAt(cv, p, dd) for dd in (3200.0, 5000.0)]
        print(f"  {p:<10} " + " ".join(f"{100 * x:>+7.2f}%" if x is not None else f"{'-':>8}"
                                       for x in g)
              + f" {100 * cv.xcTrackZero(p):>+7.2f}% {rows.get((p, sp), 0):>9,}")
    print(f"  {'course %':<10} {'':>8} {'':>8} {100 * z:>+7.2f}%  (pooled: every "
          f"cross country course % is read against this)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--min-rating", type=float, default=0.0)
    a = ap.parse_args()
    main(a.year, a.min_rating)
