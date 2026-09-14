# Project: xc-predictor / engine
# File:    record_pace.py
# Purpose: the pace no runner has run. A row faster than the open world
#          record for its distance and sex is a wrong distance or a wrong
#          time, never a performance. Pure: no database, no numpy.
#
# ★ THE OWNER'S RULE (2026-09-14): "impossible times: do for every but
#   college pool. And if this happens for a race, make the entire race
#   unrated / unranked." engine/impossible_race.py finds the races and
#   writes them down; the pack, the fill and the boards read that table.
#   This module is the one definition of "impossible" they all share.
#
#   Records as of 2025, seconds per km at each distance, interpolated in
#   log-distance between them and held flat beyond: men 800 1:40.91, 1500
#   3:26.00, mile 3:43.13, 3000 7:17.55, 5000 12:35.36, 10000 26:11.00;
#   women 800 1:53.28, 1500 3:49.04, mile 4:07.64, 3000 8:06.11, 5000
#   14:00.21, 10000 28:54.14. A row must be no faster than
#   PACE_FLOOR_SLACK of the record pace: 2% inside it is timing, rounding
#   and a short course, not a new record.
import math

# ⚠ THE TABLE HAS TO REACH THE SPRINTS (owner, 2026-09-14: "on best
#   times/marks there's issues with wrong times for the sprint races being
#   shown on the boards"). It began at 800 m and recordPace CLAMPS below its
#   first point, so every sprint was judged against the 800 m record pace --
#   126 s/km for men, which is 12.6 s for 100 m. Sprint pace is far faster
#   than that (the 100 m record is 95.8 s/km), so the rule had the sign of
#   its own answer wrong down there: every legitimate sprint looked
#   impossible and no impossible sprint could be told from a real one.
#
# ! THE CURVE IS NOT MONOTONIC, and that is correct: 100 m is the fastest
#   pace a human has ever run, and 60 m and 200 m are both slower. The
#   log-distance interpolation handles it; nothing here may assume the
#   sequence only rises.
_WR_PACE = {                                   # distance m -> seconds per km
    "M": ((55, 5.96 / 0.055), (60, 6.34 / 0.060), (100, 9.58 / 0.100),
          (200, 19.19 / 0.200), (400, 43.03 / 0.400),
          (800, 100.91 / 0.8), (1500, 206.00 / 1.5), (1609, 223.13 / 1.609),
          (3000, 437.55 / 3.0), (5000, 755.36 / 5.0), (10000, 1571.00 / 10.0)),
    "F": ((55, 6.54 / 0.055), (60, 6.92 / 0.060), (100, 10.49 / 0.100),
          (200, 21.34 / 0.200), (400, 47.60 / 0.400),
          (800, 113.28 / 0.8), (1500, 229.04 / 1.5), (1609, 247.64 / 1.609),
          (3000, 486.11 / 3.0), (5000, 840.21 / 5.0), (10000, 1734.14 / 10.0)),
}
PACE_FLOOR_SLACK = 0.98        # 2% inside the record: timing and rounding

# ★ THE SLOWEST PACE ANY RECORD ALLOWS, for a SQL prefilter: no row at or
#   above this many seconds per km can be impossible for either sex, so a
#   scan can skip everything slower before Python looks at sex and distance.
SLOWEST_RECORD_PACE = max(p for pts in _WR_PACE.values() for _, p in pts)

# ★ THE POOLS THE RULE LEAVES ALONE (owner: "every but college"). A
#   college race carries professionals and near-record fields; a youth
#   race that beats the record has the wrong distance on it.
EXEMPT_POOL_PREFIXES = ("college", "pro")

# ★ AND A FLOOR PER POOL ON TOP OF IT (owner, 2026-09-14: "should prob
#   throw something to stop crazy times for each pool -- whatever that
#   pool's record is"). The open record is a weak bar for a seventh
#   grader: 2:15 for 800 m is nowhere near Rudisha and is still not a
#   time a middle schooler runs.
#
# ⚠ THE NUMBERS ARE RATIOS TO THE OPEN RECORD, AND THE HIGH-SCHOOL ONE IS
#   SMALL ON PURPOSE. A pool factor may never flag a mark somebody in that
#   pool has actually run, so each is set under the TIGHTEST real ratio
#   across distances and both sexes -- and for high school that ratio is
#   the girls' 100 m: 10.65 against a 10.49 world record, 1.5% apart. So
#   the HS floor is barely inside the open record and does almost nothing
#   in the sprints, which is the honest answer: a high schooler really has
#   run within a blink of the world record. It bites where the gap is
#   genuinely wide, which is the young pools.
#
# ! MULTIPLIED BY PACE_FLOOR_SLACK, NOT INSTEAD OF IT. The slack is for
#   timing, rounding and a short course; the factor is for who is racing.
#   They are different corrections and both apply.
POOL_PACE_FACTOR = {"hs": 1.01, "ms": 1.12, "elem": 1.35}


def poolFactor(pool):
    """How much slower than the open record this pool's own floor is.
    1.0 for a pool with no entry, so an unknown pool is judged by the
    open record alone rather than by a guess."""
    bare = (pool or "").split("|", 1)[0]
    level = bare.split("_", 1)[0]
    return POOL_PACE_FACTOR.get(level, 1.0)


def recordPace(distance_m, sex="M"):
    """The world-record pace (s/km) at this distance, interpolated in
    log-distance; the nearest end beyond the table."""
    pts = _WR_PACE["F" if str(sex or "").upper().startswith("F") else "M"]
    d = float(distance_m)
    if d <= pts[0][0]:
        return pts[0][1]
    if d >= pts[-1][0]:
        return pts[-1][1]
    for (d0, p0), (d1, p1) in zip(pts, pts[1:]):
        if d0 <= d <= d1:
            t = (math.log(d) - math.log(d0)) / (math.log(d1) - math.log(d0))
            return p0 + t * (p1 - p0)
    return pts[-1][1]


def impossiblePace(time_seconds, distance_m, sex="M", slack=PACE_FLOOR_SLACK):
    """True when the row is faster than the record allows. False when it
    cannot be judged (no time or distance): a missing fact is not a
    finding, the anchor gate's own rule."""
    try:
        t = float(time_seconds); d = float(distance_m)
    except (TypeError, ValueError):
        return False
    if t <= 0 or d <= 0:
        return False
    return (t / (d / 1000.0)) < slack * recordPace(d, sex)


def exemptPool(pool):
    """Is this pool outside the rule? College pools are (owner, 2026-09-14).
    Takes either spelling ('college_f', 'college_f|TF')."""
    bare = (pool or "").split("|", 1)[0]
    return any(bare.startswith(p) for p in EXEMPT_POOL_PREFIXES)


def impossibleRow(time_seconds, distance_m, sex, pool):
    """The rule in one call: a pool the rule covers, judged against the
    open record scaled to that pool's own floor."""
    if exemptPool(pool):
        return False
    return impossiblePace(time_seconds, distance_m, sex,
                          slack=PACE_FLOOR_SLACK * poolFactor(pool))
