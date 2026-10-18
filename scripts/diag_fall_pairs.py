#!/usr/bin/env python3
"""
diag_fall_pairs.py -- does a track race rate the same as the same runner's
cross country races that fortnight? READ ONLY, seconds.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_fall_pairs.py
    /srv/venv/bin/python scripts/diag_fall_pairs.py --days 10 --pool hs_m
    /srv/venv/bin/python scripts/diag_fall_pairs.py --before 10-18   # every year cut
                         # at this year's date, so a season in progress compares fairly

★ WHY (owner, 2026-10-18: Mid-Season Mania's 4:17.4 rated 130.7 as cross
  country and 134.6 as track -- "how can you show those two facts?"). The
  XC and track scales are stitched together by stated constants (the grass
  cost XC_TRACK_GAP, the per-level sport offsets, the indoor level) and two
  distance layers on top of the curve. The cleanest test of the stitching
  is a runner who raced BOTH within a fortnight in the autumn: their form
  cannot have moved much, so

      gap = ln(track rating / median of their XC ratings within +-days)

  should sit at zero. A median of +3% says track rates 3% high against
  cross country -- whatever constant is off. Split by track distance it
  also says whether the distance layers agree with each other (an 800 at
  +5% and a 3200 at 0 is the distance curve, not the grass).

  The same day at a time within a second is one run stored twice (the
  xc_track_copy twin) and is left out: it would test the duplicate, not the
  scales. Reads the published ratings, so it tests the live site as it is.
"""
import argparse
import math
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

_SQL = """
    WITH tf AS (
        SELECT result_id, person_id, split_part(pool, '|', 1) AS pool,
               race_date::date AS d, speed_rating AS r, time_seconds AS t,
               distance::float AS m
        FROM   ranking_results
        WHERE  sport = 'TF' AND speed_rating > 0
          AND  extract(month FROM race_date::date) BETWEEN 8 AND 11
          AND  distance BETWEEN 750 AND 10100
          AND  (%(pool)s IS NULL OR split_part(pool, '|', 1) = %(pool)s)
          AND  (%(before)s IS NULL OR to_char(race_date::date, 'MM-DD') < %(before)s)
    ), xc AS (
        SELECT person_id, race_date::date AS d, speed_rating AS r, time_seconds AS t
        FROM   ranking_results
        WHERE  sport = 'XC' AND speed_rating > 0
          AND  extract(month FROM race_date::date) BETWEEN 8 AND 12
          AND  person_id IN (SELECT person_id FROM tf)
    )
    SELECT tf.pool, tf.m, extract(year FROM tf.d)::int, tf.r,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY xc.r), count(*)
    FROM   tf
    JOIN   xc ON xc.person_id = tf.person_id
             AND abs(xc.d - tf.d) <= %(days)s
             AND NOT (xc.d = tf.d AND abs(COALESCE(xc.t, 0) - COALESCE(tf.t, 0)) < 1.0)
    GROUP  BY tf.result_id, tf.pool, tf.m, tf.d, tf.r
"""

BUCKETS = ((750, 900, "800"), (900, 1300, "1000-1200"), (1300, 1700, "1500/1600/mile"),
           (1700, 2600, "2000-2400"), (2600, 3500, "3000/3200/2 mile"),
           (3500, 10100, "5000+"))


def bucket(m):
    for lo, hi, name in BUCKETS:
        if lo <= m < hi:
            return name
    return None


def summarise(rows):
    """[(key, n, median %, IQR lo %, IQR hi %)] per key. Pure.
    rows: (key, gap) with gap = ln(tf / xc)."""
    by = {}
    for k, g in rows:
        by.setdefault(k, []).append(g)
    out = []
    for k, gs in by.items():
        gs.sort()
        n = len(gs)
        q = lambda p: gs[min(n - 1, int(p * n))]
        out.append((k, n, 100 * statistics.median(gs), 100 * q(0.25), 100 * q(0.75)))
    return sorted(out, key=lambda t: str(t[0]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--pool", help="e.g. hs_m (default: every pool)")
    ap.add_argument("--min", type=int, default=30, help="rows before a cell is printed")
    ap.add_argument("--before", help="MM-DD: only track races before this date in every year")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, {"days": a.days, "pool": a.pool, "before": a.before})
        got = cur.fetchall()
        conn.rollback()
    pairs = []
    for pool, m, yr, r, xr, _n in got:
        b = bucket(float(m))
        if b and r and xr:
            # ! THE LEVEL IS THE GEOMETRIC MEAN OF THE TWO SIDES, not the track
            #   rating: binning by the track rating puts a lucky track race in
            #   the top quartile and calls its luck "track rates high" -- a
            #   slope out of nothing. The mean of both carries each side's
            #   noise with opposite signs into the gap, so it cancels.
            pairs.append((pool, b, int(yr), math.sqrt(float(r) * float(xr)),
                          math.log(float(r) / float(xr))))
    print(f"{len(pairs):,} autumn track races with the same runner's cross country "
          f"within {a.days} days (duplicates of one run left out)")
    print("gap = track rating vs their XC ratings that fortnight; 0 = the scales agree, "
          "+ = track rates HIGH\n")

    def show(title, rows):
        print(title)
        print(f"  {'':<26}{'races':>8}{'median':>9}{'middle half':>18}")
        for k, n, med, lo, hi in summarise(rows):
            if n >= a.min:
                print(f"  {str(k):<26}{n:>8,}{med:>+8.2f}%   {lo:>+6.2f}% .. {hi:>+5.2f}%")
        print()

    show("ALL", [("all", g) for _p, _b, _y, _r, g in pairs])
    show("BY POOL", [(p, g) for p, _b, _y, _r, g in pairs])
    show("BY TRACK DISTANCE (a slope here is the distance layers, not the grass)",
         [(b, g) for _p, b, _y, _r, g in pairs])
    show("BY POOL x DISTANCE", [(f"{p} {b}", g) for p, b, _y, _r, g in pairs])
    rs = sorted(r for _p, _b, _y, r, _g in pairs)
    if rs:
        cut = [rs[int(q * (len(rs) - 1))] for q in (0.25, 0.5, 0.75)]
        band = lambda r: ("Q1" if r < cut[0] else "Q2" if r < cut[1] else "Q3" if r < cut[2] else "Q4")
        show(f"BY ABILITY (quartiles of the runner's level, the mean of both sides; cuts {cut[0]:.0f}/{cut[1]:.0f}/{cut[2]:.0f})",
             [(band(r), g) for _p, _b, _y, r, g in pairs])
    show("BY YEAR (a jump between years is a run that moved the stitching)",
         [(y, g) for _p, _b, y, _r, g in pairs])


if __name__ == "__main__":
    main()
