#!/usr/bin/env python3
"""
diag_late_season_credit.py -- do late-season ratings rise more than the
stopwatch says? READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_late_season_credit.py            # this season, high school
    /srv/venv/bin/python scripts/diag_late_season_credit.py --since 2025-08-15 --until 2025-12-31

★ WHY (owner, 2026-11-02: every Indiana regional read +4.9 to +6.5 over its
  runners' own season averages -- "5.5 is a lot"). Real improvement or extra
  credit? The same runner on the SAME course at the SAME distance twice in a
  season leaves the course out of it: the time change is the improvement,
  and the rating change should match it. Rating change minus time change is
  what the day and weather corrections added (+ = the later race was
  credited more than the stopwatch shows).

  Prints, by the month of the later race: pairs, median time change %,
  median rating change %, and the gap. Then the courses with the biggest
  gap, so a course or a day that over-credits shows by name.
"""
import argparse
import math
import os
import statistics
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_ROWS = """
    SELECT r.person_id, r.date::text, r.time_seconds, r.speed_rating,
           COALESCE(cc.canonical_id::text, 'name:' || m.course_name) AS course,
           (round(COALESCE(dov.distance, m.distance) / 100.0) * 100)::int AS dist,
           left(m.meet_name, 40) AS meet
    FROM   results r
    JOIN   meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id AND m.source = r.source
    LEFT   JOIN dist_override dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
    LEFT   JOIN course_canonical cc
           ON cc.course_name = m.course_name
          AND round(cc.gps_lat::numeric, 5) = round(m.gps_lat::numeric, 5)
          AND round(cc.gps_long::numeric, 5) = round(m.gps_long::numeric, 5)
    WHERE  r.source = 'anet' AND r.person_id IS NOT NULL
      AND  r.date >= %(since)s AND r.date <= %(until)s
      AND  r.speed_rating > 0 AND r.time_seconds > 0
      AND  split_part(COALESCE(r.rating_pool, ''), '_', 1) = %(level)s
      AND  m.course_name IS NOT NULL
"""


def pairs(rows, min_days=14):
    """rows [(person, date, time, rating, course, dist, meet)] -> for each
    person, course and distance, the FIRST race against every later one at
    least min_days on: [(later_month, time_chg %, rating_chg %, course, meet)].
    Pure."""
    import datetime as _dt
    by = defaultdict(list)
    for p, d, t, r, c, dist, meet in rows:
        by[(p, c, dist)].append((d[:10], float(t), float(r), meet))
    out = []
    for (p, c, dist), races in by.items():
        if len(races) < 2:
            continue
        races.sort()
        d0, t0, r0, _m0 = races[0]
        day0 = _dt.date.fromisoformat(d0)
        for d1, t1, r1, m1 in races[1:]:
            if (_dt.date.fromisoformat(d1) - day0).days < min_days:
                continue
            out.append((d1[:7], 100 * math.log(t0 / t1), 100 * math.log(r1 / r0), c, m1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2026-08-15")
    ap.add_argument("--until", default="2026-12-31")
    ap.add_argument("--level", default="hs", help="hs, ms or college")
    ap.add_argument("--show", type=int, default=20)
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '30min'")
        cur.execute(_ROWS, {"since": a.since, "until": a.until, "level": a.level})
        rows = cur.fetchall()
        conn.rollback()
    ps = pairs(rows)
    print(f"{len(rows):,} {a.level} races {a.since}..{a.until}; {len(ps):,} same-runner, "
          f"same-course, same-distance pairs 14+ days apart\n")
    print(f"  {'later race':<11}{'pairs':>8}{'time chg':>10}{'rating chg':>12}{'gap':>8}")
    by_month = defaultdict(list)
    for mo, tc, rc, _c, _m in ps:
        by_month[mo].append((tc, rc))
    for mo in sorted(by_month):
        v = by_month[mo]
        tc = statistics.median(x for x, _ in v)
        rc = statistics.median(y for _, y in v)
        gap = statistics.median(y - x for x, y in v)
        print(f"  {mo:<11}{len(v):>8,}{tc:>+9.2f}%{rc:>+11.2f}%{gap:>+7.2f}%")
    print("\n  gap = rating change minus time change, same runner and course: what the day and "
          "weather corrections added (+ = the later race credited more than the stopwatch)")
    by_course = defaultdict(list)
    for _mo, tc, rc, c, m in ps:
        by_course[(c, m)].append(rc - tc)
    big = [(statistics.median(v), len(v), c, m) for (c, m), v in by_course.items() if len(v) >= 30]
    print(f"\n  later races with 30+ pairs, biggest gap first:")
    for g, n, c, m in sorted(big, reverse=True)[:a.show]:
        print(f"   {g:>+6.2f}%  {n:>5}  {m:<40} course {c}")
    print("  ... and the most negative:")
    for g, n, c, m in sorted(big)[:5]:
        print(f"   {g:>+6.2f}%  {n:>5}  {m:<40} course {c}")


if __name__ == "__main__":
    main()
