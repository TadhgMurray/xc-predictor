#!/usr/bin/env python3
"""scripts/stall_snapshot.py <out-file> -- what the database is doing, for a
pipeline step that has gone quiet.

Written by the pipeline's stall watcher (deploy/run_pipeline.sh, _stallWatch)
when no step log has changed for XCP_STALL_MIN minutes: every active session
of this database with how long its transaction and statement have run, what
it waits on, and which sessions block it -- so a hang (run 20261113_150959:
athlete_season's shards silent for an hour) leaves its cause on disk instead
of only a quiet log. Read-only; it changes nothing and kills nothing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SQL = """
SELECT a.pid, pg_blocking_pids(a.pid) AS blocked_by, a.state,
       a.wait_event_type || ':' || a.wait_event AS waiting,
       date_trunc('second', now() - a.xact_start)  AS in_xact,
       date_trunc('second', now() - a.query_start) AS in_query,
       a.application_name,
       left(regexp_replace(a.query, '\\s+', ' ', 'g'), 160) AS query
FROM   pg_stat_activity a
WHERE  a.datname = current_database() AND a.pid <> pg_backend_pid()
  AND (a.state <> 'idle' OR a.xact_start IS NOT NULL)
ORDER  BY a.xact_start NULLS LAST
"""

LOCKS = """
SELECT l.pid, l.locktype, l.mode, l.granted, c.relname
FROM   pg_locks l LEFT JOIN pg_class c ON c.oid = l.relation
WHERE  NOT l.granted
   OR  l.pid IN (SELECT unnest(pg_blocking_pids(pid)) FROM pg_stat_activity)
ORDER  BY l.granted, l.pid
"""


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "-"
    from database import getConn
    lines = []
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '30s'")
            for title, sql in (("sessions", SQL), ("locks waited on, and their holders", LOCKS)):
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                lines.append(f"== {title}")
                lines.append(" | ".join(cols))
                lines += [" | ".join("" if v is None else str(v) for v in row) for row in cur.fetchall()]
        conn.rollback()
    text = "\n".join(lines) + "\n"
    if out == "-":
        sys.stdout.write(text)
    else:
        with open(out, "w") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
