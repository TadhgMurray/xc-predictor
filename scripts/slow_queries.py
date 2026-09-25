#!/usr/bin/env python3
"""
slow_queries.py -- what the database is doing right now, longest first.

    /srv/venv/bin/python scripts/slow_queries.py          # once
    /srv/venv/bin/python scripts/slow_queries.py --watch  # every 3 s

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
    a = ap.parse_args()
    while True:
        show(a.width)
        if not a.watch:
            return
        time.sleep(3)


if __name__ == "__main__":
    main()
