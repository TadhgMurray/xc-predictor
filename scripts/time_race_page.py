#!/usr/bin/env python3
"""time_race_page.py -- where a race page's seconds go, phase by phase.

    /srv/venv/bin/python scripts/time_race_page.py xc 260432 1034143
    /srv/venv/bin/python scripts/time_race_page.py tf <meet_id> <event_id> <div_id>

Runs the route's own query functions in the route's own order, each
timed, on one connection, so a 14-second page (owner, 2026-09-06) is
named by the phase that owns it instead of guessed at. Nothing is
rendered and nothing is written.
"""
import os
import sys
import time

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for d in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, d))

import psycopg2.extras                                # noqa: E402
from database import getConn                         # noqa: E402
import app as A                                       # noqa: E402
from meet_compile import (scoreRows, publishedScores, annotateScoring,   # noqa: E402
                          splitCollisionTeams)


def timed(label, fn):
    t0 = time.time()
    out = fn()
    dt = time.time() - t0
    n = len(out) if isinstance(out, (list, dict)) else ""
    print(f"  {dt:7.2f}s  {label:<22} {n}")
    return out


def xc(meet_id, div_id):
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header = timed("get_race_header", lambda: A.get_race_header(cur, meet_id, div_id))
            results = timed("get_race_results", lambda: A.get_race_results(cur, meet_id, div_id))
            timed("publishedScores", lambda: publishedScores(cur, meet_id))
            if header:
                timed("raceExtras", lambda: A.raceExtras(cur, meet_id, div_id, header.get("source")))
                timed("stampRowsHs", lambda: A.stampRowsHs(cur, "XC", results,
                                                          distance=header.get("distance")))
            if header and results:
                timed("stampRecordFlags", lambda: A.stampRecordFlags(
                    cur, "XC", results, header.get("distance"), results[0].get("date")))
            timed("raceDayEffect", lambda: A.raceDayEffect(
                cur, "XC", header, results[0].get("date") if results else None))
            timed("annotateScoring", lambda: annotateScoring(results))
            ranked = [dict(r) for r in results]
            timed("splitCollisionTeams", lambda: splitCollisionTeams(cur, ranked))
            timed("scoreRows", lambda: scoreRows(ranked))


def tf(meet_id, event_id, div_id):
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            header = timed("get_tf_race_header", lambda: A.get_tf_race_header(cur, meet_id, div_id, event_id))
            results = timed("get_tf_race_results", lambda: A.get_tf_race_results(cur, meet_id, div_id, event_id))
            if header:
                timed("stampRowsHs", lambda: A.stampRowsHs(cur, "TF", results,
                                                          distance=header.get("distance_meters")))
            if header and results:
                timed("stampRecordFlags", lambda: A.stampRecordFlags(
                    cur, "TF", results, header.get("distance_meters"), results[0].get("date")))
            timed("raceDayEffect", lambda: A.raceDayEffect(
                cur, "TF", header, results[0].get("date") if results else None))


def main():
    t0 = time.time()
    if len(sys.argv) >= 4 and sys.argv[1].lower() == "xc":
        xc(int(sys.argv[2]), int(sys.argv[3]))
    elif len(sys.argv) >= 5 and sys.argv[1].lower() == "tf":
        tf(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    else:
        print(__doc__)
        sys.exit(2)
    print(f"  {time.time() - t0:7.2f}s  total (queries only; the template is not timed)")


if __name__ == "__main__":
    main()
