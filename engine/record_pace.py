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

_WR_PACE = {                                   # distance m -> seconds per km
    "M": ((800, 100.91 / 0.8), (1500, 206.00 / 1.5), (1609, 223.13 / 1.609),
          (3000, 437.55 / 3.0), (5000, 755.36 / 5.0), (10000, 1571.00 / 10.0)),
    "F": ((800, 113.28 / 0.8), (1500, 229.04 / 1.5), (1609, 247.64 / 1.609),
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
EXEMPT_POOL_PREFIXES = ("college",)


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
    """The rule in one call: impossible pace AND a pool the rule covers."""
    if exemptPool(pool):
        return False
    return impossiblePace(time_seconds, distance_m, sex)
