# Project: xc-predictor / scripts
# File:    diag_impossible_distances.py
# Purpose: READ ONLY. Find divisions whose LABELLED DISTANCE IS PHYSICALLY
#          IMPOSSIBLE for the times recorded in them -- using raw times only,
#          with no ratings involved anywhere.
#
#     python scripts/diag_impossible_distances.py
#     python scripts/diag_impossible_distances.py --name minutemen
#     python scripts/diag_impossible_distances.py --meet 26359
#     python scripts/diag_impossible_distances.py --limit 200 --min-rows 5
#
# ============================================================================
# WHY EVERY OTHER TOOL MISSES THESE
# ============================================================================
# audit_overrides, kill_bad_overrides, propose_distances and diag_meets_outliers
# all measure a division's field against the RATINGS its athletes carry, and all
# of them gate on having enough RATED rows -- 15, 15, 15 and 10 respectively.
#
# That gate is right for what those tools do and it has one blind spot, which
# is exactly the shape of the worst divisions:
#
#     26359/0  Ox Bow Park, "JV Minutemen Classic - Mens Race", 2025-09-13
#     labelled 8046 m.  108 finishers.  SIX of them carry a rating.
#
# The winner ran 18:02.9. At 8046 m that is 3:36 per mile -- faster than the
# world record for the distance, by a JV runner. The label is wrong; it is a
# 5 km race. But 102 of the 108 rows were dropped upstream precisely BECAUSE
# their numbers were impossible, so the division never reaches 15 rated rows
# and is invisible to every tool above. The thing that makes it bad is the
# thing that hides it.
#
# ★ SO THIS ONE READS TIMES AND NOTHING ELSE. A finishing time and a distance
#   are enough to compute a pace, and a pace outside human limits is wrong
#   without needing to know who ran it, what they usually run, or whether the
#   engine managed to rate them. No pool, no season mean, no normalization, no
#   rated-row gate -- five finishers is enough.
#
# ⚠ IT PROVES THE LABEL WRONG, NOT WHAT IS RIGHT. `implied` divides the
#   winner's time by a reference winner pace, so it is a sanity figure to
#   compare against nearby standard distances, not a value to paste into
#   corrections.py. Confirm against the other divisions of the same meet and
#   the same course before writing anything.

import argparse

from psycopg2.extras import RealDictCursor

from database import getConn


_TABLE = {"XC": "results", "TF": "results_tf"}

MILE_M = 1609.344

# ! THE FAST BOUND HAS TO SCALE WITH DISTANCE, AND A FLAT ONE DOES NOT WORK.
#
#   A first version used a flat 3:30/mile. The Minutemen winner ran 18:02.9
#   against a 5-mile label -- 3:36/mile -- and sailed under it, even though
#   the 5-mile world record is 4:24/mile. One constant cannot bound the mile
#   and the 8K at once: it is either loose enough to miss five-mile nonsense
#   or tight enough to condemn real milers.
#
#   So the floor is the mile world record (3:43.13, i.e. 3.719 min/mile)
#   carried out to any distance by THIS PROJECT'S OWN distance exponent, the
#   same K the whole engine normalises on:
#
#       floor(d) = 3.719 * (d / 1609.344) ** (K - 1)
#
#   K = 1.06, so the exponent is 0.06 and the floor rises slowly:
#
#       1609 m -> 3:43/mi     5000 m -> 3:59/mi
#       3218 m -> 3:53/mi     8046 m -> 4:06/mi     10000 m -> 4:09/mi
#
#   Every one of those is still faster than the world record at that
#   distance, which is the property that matters: anything this flags is
#   impossible, not merely fast. The Minutemen winner's 3:36 is 30 s/mile
#   under an already-unreachable bound.
WR_MILE_PACE = 3.719        # min/mile -- 3:43.13, and nobody has gone under
DISTANCE_K = 1.06           # the engine's own XC exponent


def fastFloor(meters):
    """The pace no human has run at this distance, in min/mile."""
    if not meters or meters <= 0:
        return WR_MILE_PACE
    return WR_MILE_PACE * (meters / MILE_M) ** (DISTANCE_K - 1.0)


# The slow end is looser because walking a course is real: a 25 min/mile
# MEDIAN is not a slow field, it is a distance label several times too long.
# Using the median, not the last finisher, keeps one walker from firing it.
SLOW_PACE_CEIL = 25.0

# Times at or above this are the DNF sentinel, not results.
DNF_SENTINEL = 999999

# Divides the winner's time to suggest a distance. Not a claim about the
# field -- just a yardstick to read `implied` against.
REFERENCE_WINNER_PACE = 5.5   # min/mile

_SQL = """
WITH rows AS (
    SELECT r.meet_id, r.div_id, r.time_seconds
    FROM {table} r
    WHERE r.time_seconds IS NOT NULL
      AND r.time_seconds > 0
      AND r.time_seconds < {dnf}
),
fields AS (
    SELECT meet_id, div_id,
           count(*)                                             AS n,
           min(time_seconds)                                    AS fastest,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY time_seconds) AS median
    FROM rows
    GROUP BY meet_id, div_id
    HAVING count(*) >= %(min_rows)s
)
SELECT f.*, m.course_name, m.distance, m.division, m.date
FROM fields f
JOIN LATERAL (
    SELECT course_name, distance, division, date
    FROM meets
    WHERE meets.meet_id = f.meet_id AND meets.div_id = f.div_id
      AND distance IS NOT NULL AND distance > 0
    LIMIT 1
) m ON TRUE
WHERE (%(name)s IS NULL OR m.course_name ILIKE %(name)s)
  AND (%(meet)s IS NULL OR f.meet_id = %(meet)s)
  AND (
        f.fastest / (m.distance / {mile}) / 60.0
          < %(wr)s * power(m.distance / {mile}, {kexp}) * %(slack)s
     OR f.median  / (m.distance / {mile}) / 60.0 > %(slow)s
  )
ORDER BY f.fastest / (m.distance / {mile}) ASC
LIMIT %(limit)s
"""


def _pace(seconds, meters):
    """min/mile."""
    if not meters or meters <= 0:
        return None
    return seconds / (meters / MILE_M) / 60.0


def _mmss(minutes):
    if minutes is None:
        return "-"
    m = int(minutes)
    return f"{m}:{int(round((minutes - m) * 60)):02d}"


def _clock(seconds):
    m = int(seconds // 60)
    return f"{m}:{seconds - m * 60:04.1f}"


def main():
    ap = argparse.ArgumentParser(
        description="Divisions whose labelled distance the raw times "
                    "disprove. No ratings involved. Read only.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--min-rows", type=int, default=5,
                    help="finishers needed to judge a division (default 5 -- "
                         "this tool does not need a rated field)")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--name", default=None,
                    help="only courses whose name contains this")
    ap.add_argument("--meet", type=int, default=None)
    ap.add_argument("--slack", type=float, default=1.0,
                    help="multiply the world-record floor by this. 1.0 flags "
                         "only the impossible; 1.15 also flags the merely "
                         "unbelievable, with false positives")
    ap.add_argument("--slow", type=float, default=SLOW_PACE_CEIL,
                    help=f"median pace ceiling, min/mile "
                         f"(default {SLOW_PACE_CEIL})")
    args = ap.parse_args()

    sql = _SQL.format(table=_TABLE[args.sport], dnf=DNF_SENTINEL,
                      mile=MILE_M, kexp=DISTANCE_K - 1.0)
    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, {"min_rows": args.min_rows, "limit": args.limit,
                              "name": f"%{args.name}%" if args.name else None,
                              "meet": args.meet, "wr": WR_MILE_PACE,
                              "slack": args.slack, "slow": args.slow})
            rows = cur.fetchall()

    print(f"\n  IMPOSSIBLE DISTANCE LABELS ({args.sport}) -- winner under the "
          f"world-record pace for the")
    print(f"  labelled distance (x{args.slack:g}), or field median slower "
          f"than {args.slow:.0f}/mi")
    print(f"  judged on raw times only, {args.min_rows}+ finishers, no rated "
          f"rows required\n")
    if not rows:
        print("    nothing matched.\n")
        return 0

    print(f"    {'label':>6} {'implied':>7} {'n':>5} {'winner':>9} "
          f"{'w/mi':>6} {'floor':>6} {'med/mi':>7}  {'meet/div':>16}  course")
    for r in rows:
        wp = _pace(r["fastest"], r["distance"])
        mp = _pace(r["median"], r["distance"])
        implied = r["fastest"] / 60.0 / REFERENCE_WINNER_PACE * MILE_M
        print(f"    {r['distance']:>6.0f} {implied:>7.0f} {r['n']:>5} "
              f"{_clock(r['fastest']):>9} {_mmss(wp):>6} "
              f"{_mmss(fastFloor(r['distance'])):>6} {_mmss(mp):>7}  "
              f"{str(r['meet_id']) + '/' + str(r['div_id']):>16}  "
              f"{(r['course_name'] or '?')[:32]}")

    print(f"\n    label   = meets.distance")
    print(f"    implied = the winner's time at {REFERENCE_WINNER_PACE:.1f}/mi. "
          f"A YARDSTICK, not a value to write --")
    print(f"              read it against the standard distance it sits "
          f"nearest, and confirm")
    print(f"              against the meet's other divisions before "
          f"overriding.")
    print(f"    w/mi    = winner's pace at the label.  floor = the world "
          f"record's pace")
    print(f"              at that distance.  med/mi = the field median's "
          f"pace.\n")
    print("  Nothing was written. This script is read only.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
