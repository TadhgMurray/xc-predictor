#!/usr/bin/env python3
"""
stray_rows.py -- results that probably belong to someone else: a row from a
school AND a state the athlete never otherwise runs for. READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/stray_rows.py                 # counts + the strongest 40
    /srv/venv/bin/python scripts/stray_rows.py --show 200 --csv /tmp/stray.csv
    /srv/venv/bin/python scripts/stray_rows.py --person 30178075

★ WHY (2026-10-02). Jackson Spencer (Herriman, UT; 120 races) carries one
  2021 row from the "Tallmadge Middle School Madness Meet" in Ohio -- another
  Jackson Spencer's race, linked to him by name. It is the 73 at the start
  of his chart. The twin rules catch two people merged into one; nothing
  catches a single foreign row.

! THE OWNER: "be careful". This WRITES NOTHING. It lists candidates with the
  evidence beside each, for a human to read; an unlink step comes after the
  list has been read and only with the owner's yes.

THE RULE, all of:
  - the athlete has a HOME: a state holding at least --min-home of their
    rated rows (default 5) and most of them
  - the row is from ANOTHER state, where the athlete has at most --max-away
    rows in all (default 2: one stray meet, or a prelim and a final)
  - the row's school is one the athlete never ran for in the home state
  The rating gap (row against the athlete's median that sport and season)
  is shown, not required: a mislinked row is often far off, but a true
  stray can land near by chance, and a real trip out of state is close.
  Sorted strongest first: largest gap, fewest away rows.
"""
import argparse
import csv
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from database import getConn                                   # noqa: E402

SQL = """
WITH rr AS (
    SELECT result_id, person_id, sport, race_date, year, state, school,
           speed_rating, meet_id, pool
    FROM   ranking_results
    WHERE  person_id IS NOT NULL AND state IS NOT NULL AND speed_rating IS NOT NULL
      {person}
),
per_state AS (
    SELECT person_id, state, count(*) AS n
    FROM rr GROUP BY 1, 2
),
home AS (
    SELECT DISTINCT ON (person_id) person_id, state AS home, n AS n_home,
           sum(n) OVER (PARTITION BY person_id) AS n_all
    FROM per_state ORDER BY person_id, n DESC, state
),
home_schools AS (
    SELECT DISTINCT r.person_id, r.school
    FROM rr r JOIN home h ON h.person_id = r.person_id AND r.state = h.home
),
season_med AS (
    SELECT person_id, sport, year,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating) AS med,
           count(*) AS n_season
    FROM rr GROUP BY 1, 2, 3
)
SELECT r.person_id, r.result_id, r.sport, r.race_date, r.state, r.school,
       r.meet_id, r.pool, r.speed_rating, m.med, m.n_season,
       h.home, h.n_home, h.n_all, ps.n AS n_away,
       (SELECT string_agg(DISTINCT hs.school, ' / ') FROM home_schools hs
        WHERE hs.person_id = r.person_id) AS home_school
FROM rr r
JOIN home h       ON h.person_id = r.person_id
JOIN per_state ps ON ps.person_id = r.person_id AND ps.state = r.state
JOIN season_med m ON m.person_id = r.person_id AND m.sport = r.sport AND m.year = r.year
WHERE r.state <> h.home
  AND h.n_home >= %(min_home)s
  AND h.n_home * 2 > h.n_all
  AND ps.n <= %(max_away)s
  AND NOT EXISTS (SELECT 1 FROM home_schools hs
                  WHERE hs.person_id = r.person_id AND hs.school = r.school)
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-home", type=int, default=5)
    ap.add_argument("--max-away", type=int, default=2)
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--person", type=int, default=None)
    ap.add_argument("--csv", default=None, help="write every candidate here")
    a = ap.parse_args()
    t0 = time.time()
    person = "AND person_id = %(pid)s" if a.person else ""
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = 0")
        cur.execute("SET work_mem = '1GB'")
        cur.execute(SQL.format(person=person),
                    {"min_home": a.min_home, "max_away": a.max_away, "pid": a.person})
        rows = cur.fetchall()
        names = {}
        if rows:
            ids = sorted({r[0] for r in rows})[:200000]
            cur.execute("""
                SELECT person_id,
                       max(NULLIF(btrim(concat_ws(' ', first_name, last_name)), ''))
                FROM athletes WHERE person_id = ANY(%s) GROUP BY 1""", (ids,))
            names = dict(cur.fetchall())
        conn.rollback()

    def gap(r):
        return abs(r[8] - r[9]) if r[9] is not None else 0.0
    rows.sort(key=lambda r: (-gap(r), r[14]))
    people = len({r[0] for r in rows})
    print(f"[stray] {len(rows):,} candidate rows on {people:,} athletes "
          f"(home >= {a.min_home} rows and a majority; away state <= {a.max_away} rows; "
          f"a school never run for at home)  ({time.time() - t0:.0f}s)")
    if rows:
        bands = [(10, "10+ points off the season median"), (5, "5-10"), (0, "under 5")]
        lo = float("inf")
        for cut, label in bands:
            n = sum(1 for r in rows if cut <= gap(r) < lo)
            print(f"    {n:>8,}  {label}")
            lo = cut
    print(f"\n  strongest {min(a.show, len(rows))} (rating vs the athlete's median that sport and season):")
    for r in rows[:a.show]:
        (pid, rid, sport, date, st, school, meet, pool, rat, med, n_season,
         home, n_home, n_all, n_away, home_school) = r
        print(f"    {names.get(pid) or '?':<24.24} /athlete/{pid:<9} home {home} ({n_home}/{n_all}: "
              f"{(home_school or '')[:28]})")
        print(f"        {date} {sport} {st} {school[:30] if school else '?':<30} meet {meet} "
              f"rated {rat:.1f} vs median {med:.1f} ({n_season} that season), "
              f"{n_away} row(s) in {st}")
    if a.csv and rows:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["person_id", "name", "result_id", "sport", "date", "state", "school",
                        "meet_id", "pool", "rating", "season_median", "n_season", "home",
                        "n_home", "n_all", "n_away", "home_schools"])
            for r in rows:
                w.writerow([r[0], names.get(r[0]) or ""] + list(r[1:]))
        print(f"\n[stray] all {len(rows):,} written to {a.csv}")
    print("[stray] read-only: nothing was changed")


if __name__ == "__main__":
    main()
