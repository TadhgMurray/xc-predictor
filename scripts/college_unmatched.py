#!/usr/bin/env python3
"""college_unmatched.py -- the college-pool schools the directory cannot
place, biggest first (issue 304). Run on the server, paste the output:
each line is a school name as a feed spells it, its athlete count this
season, and what the fuzzy lookup would guess. An alias goes into
build_college_directory.ALIASES; a wrong guess means the lookup needs
tightening.

    /srv/venv/bin/python scripts/college_unmatched.py [--year 2025] [--limit 80]
"""
import argparse
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")
from database import getConn                        # noqa: E402
from build_college_directory import normName, lookup, loadDirectory  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int)
    ap.add_argument("--limit", type=int, default=80)
    args = ap.parse_args()
    with getConn() as conn, conn.cursor() as cur:
        known = loadDirectory(cur, "name", "division", "state")
        cur.execute("""
            SELECT school, count(DISTINCT person_id) AS n, mode() WITHIN GROUP (ORDER BY state)
            FROM   athlete_season
            WHERE  pool LIKE 'college%%' AND school IS NOT NULL
              AND  (%(y)s::int IS NULL OR year = %(y)s)
            GROUP  BY school ORDER BY n DESC
        """, {"y": args.year})
        rows = cur.fetchall()
    exact = fuzzy = miss = 0
    out = []
    for school, n, st in rows:
        if normName(school) in known and len(known[normName(school)]) == 1:
            exact += 1
            continue
        hit = lookup(known, school, state=st)
        if hit:
            fuzzy += 1
            out.append((n, school, st, f"fuzzy -> {hit[0]} ({hit[1]}, {hit[2]})"))
        else:
            miss += 1
            out.append((n, school, st, "NO MATCH"))
    print(f"{len(rows)} college schools: {exact} exact, {fuzzy} fuzzy, {miss} unmatched")
    for n, school, st, what in sorted(out, reverse=True)[:args.limit]:
        print(f"{n:5d}  {school!r:45} {st or '--':3}  {what}")


if __name__ == "__main__":
    main()
