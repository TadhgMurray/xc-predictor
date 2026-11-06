#!/usr/bin/env python3
"""
course_track_check.py -- every course against its runners' own track level.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/course_track_check.py                       # since 2022, 20+ runners a course
    $P scripts/course_track_check.py --course "LaVern Gibson" --course Esarey
    $P scripts/course_track_check.py --since 2015-01-01 --min 40 --show 60

★ WHY (owner, 2026-11-06: Calvin Seitz and Robert Jazwinski "give me
  pause", Abdinasir Hassan and Marcelo Mantecon "should be higher"). Seitz's
  2026 cross country season is 149.7 against a spring track season of 142.4;
  most of the gap is two Indiana courses' credit (+5.6%, +6.0%) and the
  day terms. Runners in Indiana and Florida never meet, so their order rests
  on the courses' numbers alone -- and a course's number can be checked
  against something the course never touched: its runners' TRACK ratings.

★ THE READING. Each rated cross country race against the same runner's
  track season nearest to it (same level, its midpoint within WINDOW days),
  in log terms; a course's reading is the median over its races. Courses
  are compared with their level's median reading (cross country and track
  sit a little apart everywhere), so what is listed is how much MORE (or
  less) a course rates its runners than their track says -- a course
  credited too much reads high. READ-ONLY.
"""
import argparse
import math
import os
import statistics
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

WINDOW = 270
TF_MIN_RACES = 3

_SQL = """
    WITH tf AS (
        SELECT person_id, mean_rating, split_part(pool, '|', 1) AS pool,
               first_race + (last_race - first_race) / 2 AS mid
        FROM   athlete_season
        WHERE  sport = 'TF' AND n_races >= %(tfmin)s AND mean_rating > 0
          AND  first_race IS NOT NULL AND last_race IS NOT NULL),
    x AS (
        SELECT rr.person_id, rr.speed_rating AS s, rr.race_date AS d,
               split_part(rr.pool, '|', 1) AS pool,
               COALESCE(NULLIF(btrim(m.course_name), ''), NULLIF(btrim(mt.venue_name), '')) AS course,
               round(rr.distance::numeric, -2)::int AS dist
        FROM   ranking_results rr
        LEFT   JOIN meets m ON rr.result_id > 0 AND m.div_id = rr.div_id
        LEFT   JOIN meets_tfrrs mt ON rr.result_id < 0 AND mt.meet_id = rr.meet_id AND mt.sport = 'XC'
        WHERE  rr.sport = 'XC' AND rr.speed_rating > 0 AND rr.race_date >= %(since)s::date
          AND  split_part(rr.pool, '|', 1) IN ('hs_m', 'hs_f', 'college_m', 'college_f'))
    SELECT x.course, x.dist, x.pool, ln(x.s / t.mean_rating) AS gap
    FROM   x
    JOIN   LATERAL (
        SELECT tf.mean_rating FROM tf
        WHERE  tf.person_id = x.person_id AND tf.pool = x.pool
          AND  abs(x.d - tf.mid) <= %(win)s
        ORDER  BY abs(x.d - tf.mid) LIMIT 1) t ON true
    WHERE  x.course IS NOT NULL
"""


def readings(rows, min_n):
    """rows: [(course, dist, pool, gap)] -> ({pool: baseline}, [(pool,
    course, dist, n, median gap, excess)]) with min_n+ races a course. Pure."""
    by = defaultdict(list)
    for course, dist, pool, gap in rows:
        by[(pool, course, dist)].append(float(gap))
    meds = {k: (len(v), statistics.median(v)) for k, v in by.items() if len(v) >= min_n}
    base = {}
    for pool in {k[0] for k in meds}:
        vals = [m for (p, _c, _d), (_n, m) in meds.items() if p == pool]
        base[pool] = statistics.median(vals)
    out = [(p, c, d, n, m, m - base[p]) for (p, c, d), (n, m) in meds.items()]
    return base, sorted(out, key=lambda r: -r[5])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2022-08-01")
    ap.add_argument("--min", type=int, default=20, help="races with a track level, per course")
    ap.add_argument("--show", type=int, default=30)
    ap.add_argument("--course", action="append", default=[], help="name contains (repeatable)")
    a = ap.parse_args(argv)
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 0")
        cur.execute(_SQL, {"since": a.since, "win": WINDOW, "tfmin": TF_MIN_RACES})
        rows = cur.fetchall()
        conn.rollback()
    base, out = readings(rows, a.min)
    print(f"[course vs track] {len(rows):,} cross country races with the runner's own track "
          f"season within {WINDOW} days, since {a.since}; {len(out):,} courses with {a.min}+")
    for p in sorted(base):
        print(f"  {p}: the median course rates its runners {100 * base[p]:+.1f}% vs their track")
    fmt = lambda r: (f"    {100 * r[5]:+5.1f}%  {r[0]:<9} {str(r[1])[:44]:<44} {r[2]:>5} m  "  # noqa: E731
                     f"{r[3]:>5} races  ({100 * r[4]:+.1f}% raw)")
    if a.course:
        print("\n  the courses asked for:")
        for r in out:
            if any(q.lower() in str(r[1]).lower() for q in a.course):
                print(fmt(r))
        return 0
    print(f"\n  credited MOST beyond their runners' track level (a course number too generous):")
    for r in out[:a.show]:
        print(fmt(r))
    print(f"\n  credited LEAST (a course number too harsh):")
    for r in out[-a.show:][::-1]:
        print(fmt(r))
    spread = [r[5] for r in out]
    if spread:
        q = statistics.quantiles(spread, n=10)
        print(f"\n  spread of courses around their level: 10th {100 * q[0]:+.1f}%, "
              f"90th {100 * q[-1]:+.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
