#!/usr/bin/env python3
"""
diag_tfrrs_course_cells.py -- how many college (tfrrs) cross country races
does the predictor convert on their own course's difficulty, and why not
the rest? READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_tfrrs_course_cells.py
    /srv/venv/bin/python scripts/diag_tfrrs_course_cells.py --since 2025-08-01 --show 40

★ WHY (owner, 2026-10-22, against Kailo). All nine Kailo races read
  "canonical None, cell None" in our target spec, so every rating-served
  time was converted at difficulty 0.0 -- the fast end of the scale (a
  typical XC course is ~+0.05) -- and came out 6.4% fast. predict's
  _tfrrsXcSpecSql finds a course by (venue_name, gps rounded to 5 places)
  in course_canonical, and TFRRS pages carry no GPS: a meet has
  coordinates only if a geocoding backfill filled them.

Each tfrrs XC meet with results falls in the first of:
    no gps        meets_tfrrs.gps_lat/long empty -- never geocoded
    no canonical  coordinates, but no course_canonical row at them under
                  that name (course_canonical built before the geocode, or
                  rounding) -- build_course_canonical.py --apply
    no cell       a venue, but no course_difficulties row at the division's
                  distance (rounded to 100 m) -- not enough races there yet
    cell          converted on its own course
Counted by finishers, since that is what a wrong conversion costs.
"""
import argparse
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

_SQL = """
    WITH fin AS (
        SELECT meet_id, div_id, count(*) AS n
        FROM   results
        WHERE  source = 'tfrrs' AND date >= %(since)s AND time_seconds > 0
        GROUP  BY meet_id, div_id
    )
    SELECT mt.meet_id, mt.meet_name, substr(mt.date::text, 1, 10), mt.venue_name, mt.state,
           mt.gps_lat, mt.gps_long, f.div_id, f.n,
           (mt.division_distances -> f.div_id::text ->> 'distance')::real AS dist,
           cc.canonical_id,
           (SELECT cd.difficulty FROM course_difficulties cd
            WHERE  cd.canonical_id = cc.canonical_id
              AND  cd.distance_m = (round((mt.division_distances -> f.div_id::text
                                           ->> 'distance')::real / 100.0) * 100)::int
            LIMIT 1) AS cell,
           (SELECT count(*) FROM course_canonical c2
            WHERE  c2.course_name = mt.venue_name) AS same_name
    FROM   fin f
    JOIN   meets_tfrrs mt ON mt.meet_id = f.meet_id AND mt.sport = 'XC'
    LEFT   JOIN course_canonical cc
           ON cc.course_name = mt.venue_name
          AND round(cc.gps_lat::numeric, 5)  = round(mt.gps_lat::numeric, 5)
          AND round(cc.gps_long::numeric, 5) = round(mt.gps_long::numeric, 5)
"""


def bucket(gps_lat, gps_long, canonical_id, cell):
    """Why a tfrrs race does or does not have its course cell. Pure."""
    if not gps_lat or not gps_long:
        return "no gps"
    if canonical_id is None:
        return "no canonical"
    if cell is None:
        return "no cell"
    return "cell"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2026-08-01")
    ap.add_argument("--show", type=int, default=25, help="biggest uncovered races to list")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '10min'")
        cur.execute(_SQL, {"since": a.since})
        rows = cur.fetchall()
        conn.rollback()
    races, runners = Counter(), Counter()
    worst = []
    cells = []
    for (mid, name, day, venue, state, lat, lng, div, n, dist, cid, cell, same) in rows:
        b = bucket(lat, lng, cid, cell)
        races[b] += 1
        runners[b] += n
        if b == "cell":
            cells.append(float(cell))
        else:
            worst.append((n, b, mid, div, day, name, venue, state, dist, same))
    tot_r, tot_n = sum(races.values()), sum(runners.values())
    print(f"tfrrs XC races since {a.since}: {tot_r:,} races, {tot_n:,} finishers\n")
    print(f"  {'':<14}{'races':>8}{'share':>8}{'finishers':>12}{'share':>8}")
    for b in ("cell", "no cell", "no canonical", "no gps"):
        print(f"  {b:<14}{races[b]:>8,}{100 * races[b] / max(tot_r, 1):>7.1f}%"
              f"{runners[b]:>12,}{100 * runners[b] / max(tot_n, 1):>7.1f}%")
    if cells:
        cells.sort()
        print(f"\n  the cells that exist: median {cells[len(cells) // 2]:+.3f} "
              f"(the 0.0 a missing cell is converted at is the fast end)")
    worst.sort(reverse=True)
    if worst:
        print(f"\n  the {min(a.show, len(worst))} biggest races without their course cell:")
        for n, b, mid, div, day, name, venue, state, dist, same in worst[:a.show]:
            print(f"    {n:>4}  {b:<13} {day}  meet {mid}/{div}  {(name or '')[:38]:<38}  "
                  f"{(venue or '?')[:30]:<30} {state or '':<3} {dist or '?'} m"
                  + (f"  ({same} course_canonical row(s) by this name)" if same else ""))
    print("\n" + json.dumps({"races": dict(races), "finishers": dict(runners)}))


if __name__ == "__main__":
    main()
