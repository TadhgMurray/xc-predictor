#!/usr/bin/env python3
"""
diag_athlete_order.py -- one athlete's rows as the page gets them, and the
order the page puts them in.

    /srv/venv/bin/python scripts/diag_athlete_order.py 21741259
    /srv/venv/bin/python scripts/diag_athlete_order.py 21741259 --meet "Division III"

★ WHY (owner, 2026-09-28: "still not working" -- a prelim above its final
  on the athlete page). Prints each row's raw date (repr, so a time or a
  different shape shows), round, event, meet and the round rank the sort
  uses, first as the query returns them, then after dedupe_races -- the
  order the page renders. READ-ONLY.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import psycopg2.extras                                        # noqa: E402
import app as A                                               # noqa: E402
from database import getConn                                  # noqa: E402


def show(title, rows, meet):
    print(f"\n{title}")
    for r in rows:
        if meet and meet.lower() not in str(r.get("meet") or "").lower():
            continue
        print(f"  {r.get('date')!r:<24} rank {A.round_order(r)}  round={r.get('round')!r:<12} "
              f"event={str(r.get('event'))[:28]!r:<30} {str(r.get('meet'))[:40]:<40} "
              f"id {r.get('result_id')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("person_id", type=int)
    ap.add_argument("--meet", default="", help="only rows whose meet name contains this")
    a = ap.parse_args()
    with getConn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        rows = [dict(r) for r in A.get_races(cur, a.person_id)]
        conn.rollback()
    show("as the query returns them:", rows, a.meet)
    show("as the page orders them (dedupe_races):", A.dedupe_races(rows), a.meet)


if __name__ == "__main__":
    main()
