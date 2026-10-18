#!/usr/bin/env python3
"""
diag_island_seasons.py -- team-seasons rated far above the same runners'
seasons either side. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_island_seasons.py
    /srv/venv/bin/python scripts/diag_island_seasons.py --pool college_m --sport XC --top 40
    /srv/venv/bin/python scripts/diag_island_seasons.py --school Wyoming --year 2020

★ WHY (owner's all-time college men's XC board, 2026-10-17: places 1-8 are
  Colorado State and Wyoming runners from the 2020-21 COVID season). Those
  two teams raced almost only each other, at altitude, that autumn: an
  island the course and day terms cannot tie to anyone else, so its level
  floats. The go-live's field check (joint_golive.fieldCheck) compares a
  race with the SAME season's other races, and every race of an island
  season is high together, so it sees nothing. What does see it is the
  runners' own seasons either side.

THE MEASURE, per athlete with a rated season before AND after this one:
      jump = ln(r_y) - (ln(r_{y-1}) + ln(r_{y+1})) / 2
  which is ~0 for a runner improving steadily, and is centred on the pool's
  own median (so the normal year-on-year shape cancels). A team-season's
  excess is the median jump of its runners less the pool's median jump,
  in percent. A real island shows up as a whole roster several percent
  above both of its neighbours.
"""
import argparse
import math
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

_SQL = """
    SELECT person_id, split_part(pool, '|', 1) AS pool, year, school,
           mean_rating, n_races
    FROM   athlete_season
    WHERE  sport = %(sport)s AND mean_rating > 0
      AND  (%(pool)s IS NULL OR split_part(pool, '|', 1) = %(pool)s)
"""


def jumps(rows):
    """{(pool, school, year): [jump, ...]} and {pool: [jump, ...]}. Pure.

    rows: (person_id, pool, year, school, rating). A runner's seasons are
    taken within one pool; a season with a neighbour either side counts."""
    by = {}
    for pid, pool, year, school, r in rows:
        by.setdefault((pid, pool), {})[int(year)] = (school, float(r))
    team, pool_all = {}, {}
    for (pid, pool), seasons in by.items():
        for y, (school, r) in seasons.items():
            a, b = seasons.get(y - 1), seasons.get(y + 1)
            if not a or not b:
                continue
            j = math.log(r) - (math.log(a[1]) + math.log(b[1])) / 2.0
            team.setdefault((pool, school, y), []).append(j)
            pool_all.setdefault(pool, []).append(j)
    return team, pool_all


def excess(team, pool_all, min_runners=5):
    """[(excess %, n, pool, school, year)] biggest first. Pure."""
    med = {p: statistics.median(v) for p, v in pool_all.items() if v}
    out = []
    for (pool, school, y), js in team.items():
        if len(js) < min_runners or pool not in med:
            continue
        out.append((100.0 * (statistics.median(js) - med[pool]), len(js), pool, school, y))
    out.sort(reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--pool", help="e.g. college_m (default: every pool)")
    ap.add_argument("--school", help="only this school's seasons in the list")
    ap.add_argument("--year", type=int, help="only this season in the list")
    ap.add_argument("--min-runners", type=int, default=5)
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, {"sport": a.sport, "pool": a.pool})
        rows = [(p, pl, y, s, r) for p, pl, y, s, r, _n in cur.fetchall()]
        conn.rollback()
    team, pool_all = jumps(rows)
    for p in sorted(pool_all):
        print(f"{p}: {len(pool_all[p]):,} runner-seasons with a season either side, "
              f"median jump {100 * statistics.median(pool_all[p]):+.2f}%")
    got = excess(team, pool_all, a.min_runners)
    if a.school:
        got = [g for g in got if (g[3] or "").lower() == a.school.lower()]
    if a.year:
        got = [g for g in got if g[4] == a.year]
    print(f"\n{'excess':>7} {'n':>4}  pool        year  school")
    for ex, n, pool, school, y in got[:a.top]:
        print(f"{ex:+6.1f}% {n:4d}  {pool:10s}  {y}  {school}")
    by_year = {}
    for ex, n, pool, school, y in got:
        by_year.setdefault(y, []).append(ex)
    print("\nmedian team excess by season (a COVID year stands out here if it is a level problem):")
    for y in sorted(by_year):
        print(f"  {y}: {statistics.median(by_year[y]):+.2f}%  over {len(by_year[y])} team-seasons")


if __name__ == "__main__":
    main()
