#!/usr/bin/env python3
"""
explain_athlete_race.py -- the athlete page's race list and the race page's feed
lookup, timed statement by statement, with their plans. explain_board.py's
twin for the two page kinds the 2026-11-06 slow-request mails named.

    /srv/venv/bin/python scripts/explain_athlete_race.py --athlete 30413149 --plans
    /srv/venv/bin/python scripts/explain_athlete_race.py --race-xc 289197 3000002938985 --plans

--athlete runs app.get_races (the "XC half: results + meets" UNION) and the
link-pin probe (_stampFeedPins); --race-xc runs _xc_meet_sources (the
feed list, `SELECT source, count(*) ... GROUP BY source`, and the division
probe) and get_race_results. Read-only: EXPLAIN ANALYZE executes each
SELECT, and the transaction is rolled back at the end.

Paste the output back. What to look for: "Heap Fetches" on an Index Only
Scan (the visibility map is stale -- VACUUM the table), "Seq Scan on
results" (an index is missing or invalid), and "Buffers: shared read="
(pages that came off disk, the cold-cache cost).
"""
import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from explain_board import _Timed                               # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--athlete", type=int, metavar="PERSON_ID")
    ap.add_argument("--race-xc", type=int, nargs=2, metavar=("MEET_ID", "DIV_ID"))
    ap.add_argument("--plans", action="store_true", help="print EXPLAIN ANALYZE")
    a = ap.parse_args()
    if not (a.athlete or a.race_xc):
        ap.error("give --athlete or --race-xc")
    os.environ.setdefault("XCP_DB_QUIET", "1")
    import psycopg2.extras
    import app as A
    from database import getConn
    with A.app.test_request_context("/"), getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as raw:
            cur = _Timed(raw, a.plans)
            t0 = time.time()
            if a.athlete:
                races = A.get_races(cur, a.athlete)
                t1 = time.time()
                A._stampFeedPins(cur, races)
                t2 = time.time()
                pinned = sum(1 for r in races if r.get("feed_pin"))
                print(f"\nget_races {t1 - t0:6.2f}s ({len(races)} rows)   "
                      f"feed pins {t2 - t1:6.2f}s ({pinned} links keep ?r=)")
            if a.race_xc:
                meet, div = a.race_xc
                t0 = time.time()
                src, alt, _others = A._xc_meet_sources(cur, meet, {}, div)
                t1 = time.time()
                rows = A.get_race_results(cur, meet, div, source=src)
                t2 = time.time()
                print(f"\nfeed lookup {t1 - t0:6.2f}s (feed {src}, alt {alt})   "
                      f"race results {t2 - t1:6.2f}s ({len(rows)} rows)")
            conn.rollback()
    for secs, sql, plan in cur.log:
        print(f"\n  {secs:6.2f}s  {sql}")
        if plan:
            print("    " + plan.replace("\n", "\n    "))


if __name__ == "__main__":
    main()
