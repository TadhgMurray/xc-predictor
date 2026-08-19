#!/usr/bin/env python
# Project: xc-predictor
# File:    scripts/course_era_break.py
# Purpose: decide, WITH EVIDENCE, whether course difficulty needs an era
#          dimension -- before anything in pair_all.py is touched.
#
#   THE CLAIM UNDER TEST
#   --------------------
#   Courses get re-routed and keep their name. If that happens, one cell holds
#   two different physical courses and its single delta is wrong for both eras.
#
#   ★ WHY NOT JUST DECAY-WEIGHT RECENT RACES. Two reasons, and both are why
#     this script exists instead of a one-line weight in pair_all.shrink:
#
#     1. IT DEGRADES THE GOOD CELLS TO FIX THE BAD ONES. Most courses do not
#        change. For those, every old race day is valid evidence about the SAME
#        delta, so down-weighting it only raises the variance -- and shrinkDelta
#        shrinks on PRECISION, so the inflated variance then pulls the estimate
#        harder toward zero. The venues with the longest, best-evidenced
#        histories would be flattened the most. Exactly backwards.
#
#     2. IT IS A SMOOTH FIX FOR A STEP CHANGE. A re-route is a discontinuity:
#        one delta until 2015, another after. An exponential decay cannot
#        represent that. It returns a blend biased toward the recent value --
#        still one number, still wrong for both eras.
#
#     The right instrument for a step change is a SPLIT CELL, on the same
#     argument that already splits (canonical_id, distance_m): a 2300m and an
#     8000m at one venue are different courses, and so are the pre- and
#     post-reroute versions.
#
#   ★ WHAT MAKES THIS TEST TRUSTWORTHY
#   ----------------------------------
#   It uses the MODEL'S OWN RESIDUAL, not raw times:
#
#       residual = results.speed_rating - pair_athlete_season.rating_seasonal
#
#   i.e. how far a performance at this course sat from what that athlete was
#   worth THAT SEASON. If the cell's delta is right, the mean residual is ~0 in
#   every year. A STEP in the yearly mean means the course stopped matching its
#   delta at a point in time.
#
#   Because the comparison is against each athlete's own contemporaneous
#   season, this is immune to era drift and to field strength: a venue that
#   simply started attracting faster runners moves BOTH terms together and
#   leaves the residual flat. Only a change in the COURSE moves the residual.
#
#   ⚠ WHAT IT CANNOT SEE. A change that coincides with the corpus-wide era
#     curve, and a cell whose delta was always wrong (no step, just a level
#     offset -- that is a fit problem, not an era problem).
#
# READ ONLY.
#
# USAGE (from the project root)
#   python scripts\course_era_break.py
#   python scripts\course_era_break.py --min-step 3.0 --top 40

import argparse
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "engine")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn, initPool


# ------------------------------------------------------------------ #
#  POLICY CONSTANTS -- the tunable parts, named, in one place
# ------------------------------------------------------------------ #

# A cell needs this many YEARS of history before a break can be located at all.
# Below it there is no "before" and "after" to compare.
MIN_YEARS = 8

# And this many results in a year before that year's mean is worth trusting.
# Thin years are dropped, not down-weighted -- a mean over three runners is
# noise, and averaging noise in is what makes a spurious break look real.
MIN_PER_YEAR = 20

# Each side of a candidate break needs this many YEARS. Three is the smallest
# number that can distinguish a level shift from a single odd season.
MIN_SIDE_YEARS = 3

# A break must clear BOTH gates: a real size AND statistical support.
#   step  -- rating points. Below ~2 nobody would notice or care.
#   t     -- Welch t on the two sides' weighted means.
MIN_STEP = 2.0
MIN_T = 4.0


# ------------------------------------------------------------------ #
# CHUNK 1 -- PRIMITIVES
# ------------------------------------------------------------------ #

def banner(title):
    print()
    print("-" * 76)
    print(title)
    print("-" * 76)


def fetch(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def _academicYear(alias="r"):
    """July onward belongs to the year the season STARTS, matching
    season_level._academicYearExpr and the grade backfill. Getting this wrong
    would split each XC season across two labels and halve every year's n."""
    return f"""
        CASE WHEN substr({alias}.date, 6, 2)::int >= 7
             THEN substr({alias}.date, 1, 4)::int
             ELSE substr({alias}.date, 1, 4)::int - 1
        END"""


# ------------------------------------------------------------------ #
# CHUNK 2 -- THE YEARLY RESIDUAL SERIES
# ------------------------------------------------------------------ #

def loadSeries(cur):
    """One row per (cell, year): n, mean residual, sd.

    ⚠ THE CELL KEY IS (canonical_id, distance_m), NOT course_name. There are
      21 Woodward Parks; joining on name alone merges them and any "break"
      found would be one venue's history bleeding into another's.

      The canonical join mirrors app.py's: name plus gps rounded to 5dp, then
      distance rounded to the nearest 100m, which is how the engine binned the
      cell in the first place.

    ⚠ THE DATE REGEX IS LOAD-BEARING. `results.date` is TEXT and holds corrupt
      years (0023, 2222); substr(...)::int throws without the guard.

      Aggregating in SQL keeps Python holding a few thousand rows instead of
      tens of millions.
    """
    return fetch(cur, f"""
        SELECT cc.canonical_id                       AS cell,
               (round(m.distance / 100.0) * 100)::int AS dist,
               max(m.course_name)                    AS name,
               {_academicYear('r')}                  AS yr,
               count(*)                              AS n,
               avg(r.speed_rating - pas.rating_seasonal)        AS mean_resid,
               stddev_samp(r.speed_rating - pas.rating_seasonal) AS sd_resid
        FROM results r
        JOIN meets m
              ON m.meet_id = r.meet_id
             AND m.div_id  = r.div_id
             AND m.source  = r.source
        JOIN course_canonical cc
              ON cc.course_name = m.course_name
             AND round(cc.gps_lat::numeric,  5) = round(m.gps_lat::numeric,  5)
             AND round(cc.gps_long::numeric, 5) = round(m.gps_long::numeric, 5)
        JOIN pair_athlete_season pas
              ON pas.person_id::bigint = r.person_id
             AND pas.season            = {_academicYear('r')}
        WHERE r.person_id     IS NOT NULL
          AND r.speed_rating  IS NOT NULL
          AND pas.rating_seasonal IS NOT NULL
          AND m.distance      IS NOT NULL
          AND r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}'
        GROUP BY 1, 2, 4
        HAVING count(*) >= {MIN_PER_YEAR}
        ORDER BY 1, 2, 4
    """)


def groupCells(rows):
    """Reshape flat (cell, dist, name, yr, n, mean, sd) into
    {(cell, dist): (name, [year points])}, years ascending."""
    out = defaultdict(list)
    names = {}
    for cell, dist, name, yr, n, mean, sd in rows:
        key = (cell, dist)
        names[key] = name
        # sd is NULL when a year has exactly one result; MIN_PER_YEAR makes
        # that impossible, but the coalesce keeps the arithmetic total.
        out[key].append((int(yr), int(n), float(mean), float(sd or 0.0)))
    return {k: (names[k], sorted(v)) for k, v in out.items()}


# ------------------------------------------------------------------ #
# CHUNK 3 -- FIND THE BREAK
# ------------------------------------------------------------------ #

def _weightedStats(points):
    """n-weighted mean of the yearly means, and the standard error of that
    mean, pooling each year's own spread.

    Weighting by n treats every RESULT equally rather than every YEAR equally,
    so a season with 400 runners is not outvoted by one with 25.
    """
    total_n = sum(n for _yr, n, _m, _sd in points)
    if not total_n:
        return 0.0, float("inf")
    mean = sum(n * m for _yr, n, m, _sd in points) / total_n
    # Pooled variance of the individual results, then the SE of their mean.
    var = sum(n * (sd ** 2 + (m - mean) ** 2)
              for _yr, n, m, sd in points) / total_n
    return mean, (var / total_n) ** 0.5


def bestBreak(points):
    """Scan every candidate split year; return the strongest one.

    Output: (break_year, step, t, n_before, n_after) or None.

    The scan is exhaustive rather than clever because a cell has at most a few
    dozen years -- and an exhaustive scan cannot miss the break for the same
    reason a plurality vote can miss a coherent race: no bucketing.

    ⚠ MULTIPLE TESTING IS REAL. Scanning k split points and keeping the max |t|
      inflates it. MIN_T = 4.0 rather than the usual 2 is the crude allowance;
      this is a SCREEN, and every hit is meant to be looked at.
    """
    best = None
    for i in range(MIN_SIDE_YEARS, len(points) - MIN_SIDE_YEARS + 1):
        before, after = points[:i], points[i:]
        m0, se0 = _weightedStats(before)
        m1, se1 = _weightedStats(after)
        se = (se0 ** 2 + se1 ** 2) ** 0.5
        if se <= 0:
            continue
        step = m1 - m0
        t = abs(step) / se
        if best is None or t > best[2]:
            best = (points[i][0], step, t,
                    sum(p[1] for p in before), sum(p[1] for p in after))
    return best


# ------------------------------------------------------------------ #
# CHUNK 4 -- REPORT
# ------------------------------------------------------------------ #

def report(cells, min_step, min_t, top):
    hits, screened = [], 0
    for (cell, dist), (name, points) in cells.items():
        if len(points) < MIN_YEARS:
            continue
        screened += 1
        found = bestBreak(points)
        if not found:
            continue
        yr, step, t, n0, n1 = found
        if abs(step) >= min_step and t >= min_t:
            hits.append((abs(step), cell, dist, name, yr, step, t, n0, n1))

    hits.sort(reverse=True)
    print(f"  {screened:,} cells had >= {MIN_YEARS} usable years; "
          f"{len(hits):,} show a break")
    if not hits:
        return 0, screened

    print(f"\n  {'course':<34}{'dist':>6}{'break':>7}{'step':>8}"
          f"{'t':>7}{'n before':>10}{'n after':>9}")
    for _abs, cell, dist, name, yr, step, t, n0, n1 in hits[:top]:
        print(f"  {str(name)[:34]:<34}{dist:>6}{yr:>7}{step:>+8.2f}"
              f"{t:>7.1f}{n0:>10,}{n1:>9,}")
    return len(hits), screened


def verdict(n_hits, n_screened):
    banner("VERDICT")
    if not n_screened:
        print("  nothing had enough history -- widen MIN_YEARS/MIN_PER_YEAR.")
        return
    rate = 100.0 * n_hits / n_screened
    print(f"  {n_hits:,} of {n_screened:,} long-history cells break "
          f"({rate:.1f}%)")
    if rate < 2:
        print("  RARE. Hand-split the listed cells; no engine change is "
              "warranted, and decay-weighting would cost every other cell "
              "precision to fix these few.")
    elif rate < 15:
        print("  MEANINGFUL. Worth an era-split with a detection gate, run as "
              "its own cycle. Still not a decay: these are steps.")
    else:
        print("  WIDESPREAD. Course identity itself is unstable over time and "
              "the cell key needs an era dimension. That is a structural "
              "change and should be designed, not patched.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-step", type=float, default=MIN_STEP)
    ap.add_argument("--min-t", type=float, default=MIN_T)
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    initPool()
    with getConn() as conn, conn.cursor() as cur:
        banner("loading yearly residual series")
        rows = loadSeries(cur)
        cells = groupCells(rows)
        print(f"  {len(rows):,} cell-years across {len(cells):,} cells")

        banner("step changes in course difficulty over time")
        n_hits, n_screened = report(cells, args.min_step, args.min_t, args.top)
        conn.rollback()                      # nothing here writes

    verdict(n_hits, n_screened)


if __name__ == "__main__":
    main()