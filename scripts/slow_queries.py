#!/usr/bin/env python3
"""
slow_queries.py -- what the database is doing right now, longest first.

    /srv/venv/bin/python scripts/slow_queries.py          # once
    /srv/venv/bin/python scripts/slow_queries.py --watch  # every 3 s
    /srv/venv/bin/python scripts/slow_queries.py --terminate 543606 544061

! A Ctrl-C'd script can leave its query running on the SERVER (2026-09-25:
  three backends of an interrupted link_freshmen dry run held results_tf
  and blocked the backfill's swap). --terminate ends those backends by pid.

For "the site is slow": a page query that runs for seconds shows here with
its text, next to whatever else (a pipeline step, a repair, an extraction)
is holding the machine. READ-ONLY.
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from database import getConn                                  # noqa: E402

SQL = """
    SELECT pid, application_name AS app, state,
           round(extract(epoch FROM now() - query_start)::numeric, 1) AS secs,
           wait_event_type AS wait,
           left(regexp_replace(query, '\\s+', ' ', 'g'), %s) AS query
    FROM   pg_stat_activity
    WHERE  state <> 'idle' AND pid <> pg_backend_pid()
      AND  datname = current_database()
    ORDER  BY query_start ASC NULLS LAST
    LIMIT  25
"""


def show(width):
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL, (width,))
            rows = cur.fetchall()
    print(time.strftime("%H:%M:%S"), f"-- {len(rows)} active")
    for pid, app, state, secs, wait, q in rows:
        print(f"  {secs:>8}s  pid {pid:<7} {(app or '-')[:18]:<18} "
              f"{state:<20} {wait or '':<8} {q}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--width", type=int, default=160)
    ap.add_argument("--terminate", type=int, nargs="+", metavar="PID",
                    help="end these backends (pg_terminate_backend)")
    a = ap.parse_args()
    if a.terminate:
        with getConn() as conn:
            with conn.cursor() as cur:
                for pid in a.terminate:
                    cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
                    print(f"  pid {pid}: {'ended' if cur.fetchone()[0] else 'not found'}")
            conn.commit()
        return
    while True:
        show(a.width)
        if not a.watch:
            return
        time.sleep(3)


if __name__ == "__main__":
    main()
