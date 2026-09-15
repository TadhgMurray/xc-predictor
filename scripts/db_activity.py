#!/usr/bin/env python3
"""
db_activity.py -- what the database is doing, and the pipeline's part of it
stopped. No psql, no pager.

    set -a; . /etc/xc-predictor.env; set +a
    python scripts/db_activity.py                  # who is running what, vacuums, lock waits
    python scripts/db_activity.py --kill-pipeline  # terminate every backend the pipeline opened
    python scripts/db_activity.py --kill-vacuum    # cancel autovacuum workers (they come back later)

★ WHY (owner, 2026-09-15: "even after I ctrl-c'd the script the website is
  slow as shit"). Ctrl-C kills the Python; it does not kill the statement
  Postgres is running for it -- a CREATE TABLE AS keeps scanning until it
  is done, with nobody to send the rows to -- and the pipeline's parallel
  children ignore SIGINT altogether. The backends are labelled
  application_name = 'xcp-pipeline' (quiet mode, scripts/database.py), so
  they can be found and ended by name, and run_pipeline.sh now does this
  itself on the way out.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_QUIET", "0")      # this tool is not the pipeline
from database import getConn                    # noqa: E402

PIPELINE_APP = "xcp-pipeline"


def _rows(cur, sql, params=None):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def report(cur):
    print("== active backends (not idle) ==")
    act = _rows(cur, """
        SELECT pid, application_name AS app, usename,
               to_char(now() - query_start, 'HH24:MI:SS') AS age, state,
               COALESCE(wait_event_type || ':' || wait_event, '') AS waiting,
               left(regexp_replace(query, '\\s+', ' ', 'g'), 90) AS query
        FROM   pg_stat_activity
        WHERE  state <> 'idle' AND pid <> pg_backend_pid() AND backend_type = 'client backend'
        ORDER  BY query_start""")
    if not act:
        print("  none")
    for r in act:
        print(f"  pid {r['pid']:<7} {r['app'][:16]:<16} {r['age']}  {r['state']:<20} {r['waiting']:<24} {r['query']}")
    print("\n== idle in transaction (holding locks for nothing) ==")
    idle = _rows(cur, """
        SELECT pid, application_name AS app, to_char(now() - state_change, 'HH24:MI:SS') AS age,
               left(regexp_replace(query, '\\s+', ' ', 'g'), 80) AS query
        FROM   pg_stat_activity WHERE state = 'idle in transaction' ORDER BY state_change""")
    print("  none" if not idle else "\n".join(f"  pid {r['pid']:<7} {r['app'][:16]:<16} {r['age']}  {r['query']}" for r in idle))
    print("\n== autovacuum / vacuum in progress ==")
    vac = _rows(cur, """
        SELECT p.pid, c.relname, p.phase, p.heap_blks_scanned, p.heap_blks_total,
               to_char(now() - a.query_start, 'HH24:MI:SS') AS age
        FROM   pg_stat_progress_vacuum p
        JOIN   pg_class c ON c.oid = p.relid
        LEFT JOIN pg_stat_activity a ON a.pid = p.pid""")
    if not vac:
        print("  none")
    for r in vac:
        pct = (100.0 * r["heap_blks_scanned"] / r["heap_blks_total"]) if r["heap_blks_total"] else 0.0
        print(f"  pid {r['pid']:<7} {r['relname']:<28} {r['phase']:<22} {pct:5.1f}% of heap  running {r['age']}")
    print("\n== index builds in progress ==")
    idx = _rows(cur, """
        SELECT p.pid, c.relname, p.phase, p.blocks_done, p.blocks_total
        FROM   pg_stat_progress_create_index p JOIN pg_class c ON c.oid = p.relid""")
    print("  none" if not idx else "\n".join(f"  pid {r['pid']:<7} {r['relname']:<28} {r['phase']}  {r['blocks_done']}/{r['blocks_total']}" for r in idx))
    print("\n== lock waits (a site query queued behind something) ==")
    lw = _rows(cur, """
        SELECT w.pid AS waiter, w.application_name AS waiter_app,
               to_char(now() - w.query_start, 'HH24:MI:SS') AS waited,
               b.pid AS blocker, b.application_name AS blocker_app, b.state AS blocker_state,
               left(regexp_replace(b.query, '\\s+', ' ', 'g'), 60) AS blocker_query
        FROM   pg_stat_activity w
        JOIN   LATERAL unnest(pg_blocking_pids(w.pid)) AS bp(pid) ON TRUE
        JOIN   pg_stat_activity b ON b.pid = bp.pid
        WHERE  w.wait_event_type = 'Lock'""")
    print("  none" if not lw else "\n".join(
        f"  {r['waiter']} ({r['waiter_app']}) waited {r['waited']} on {r['blocker']} ({r['blocker_app']}, {r['blocker_state']}): {r['blocker_query']}" for r in lw))
    print("\n== connections by application ==")
    for r in _rows(cur, """SELECT application_name AS app, count(*) AS n, count(*) FILTER (WHERE state <> 'idle') AS busy
                           FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1 ORDER BY 2 DESC"""):
        print(f"  {r['app'] or '(none)':<24} {r['n']:>4} connections, {r['busy']:>3} busy")
    return act, vac


def indexes(cur, tables=("ranking_results", "athlete_season")):
    """The indexes the site's pages need, present or missing."""
    for t in tables:
        rows = _rows(cur, """SELECT indexname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
                             FROM pg_indexes i JOIN pg_class c ON c.relname = i.indexname
                             JOIN pg_index x ON x.indexrelid = c.oid
                             WHERE i.schemaname = 'public' AND i.tablename = %s
                             ORDER BY indexname""", (t,))
        cur.execute("SELECT pg_size_pretty(pg_total_relation_size(%s)), (SELECT n_live_tup FROM pg_stat_user_tables WHERE relname = %s)", (t, t))
        size, live = cur.fetchone()
        print(f"\n== indexes on {t} ({size}, ~{(live or 0):,} rows) ==")
        print("  NONE -- every page query on it is a full scan" if not rows else
              "\n".join(f"  {r['indexname']:<40} {r['size']}" for r in rows))
        cur.execute("""SELECT count(*) FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid
                       JOIN pg_class t ON t.oid = x.indrelid WHERE t.relname = %s AND NOT x.indisvalid""", (t,))
        bad = cur.fetchone()[0]
        if bad:
            print(f"  ⚠ {bad} INVALID index(es): a CREATE INDEX CONCURRENTLY died; drop and rebuild (step 11b)")
    need = {"ranking_results": ("school", "person_id", "meet_id"), "athlete_season": ("school", "person_id")}
    for t, cols in need.items():
        for col in cols:
            cur.execute("""SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s
                           AND (indexdef ~ ('\\(' || %s || '[,)]'))""", (t, col))
            if not cur.fetchone()[0]:
                print(f"  ⚠ {t} has no index led by {col}: the pages that filter by it scan the table "
                      f"(python scripts/add_page_indexes.py builds it CONCURRENTLY, step 11b)")


def killPipeline(cur):
    rows = _rows(cur, """SELECT pid, pg_terminate_backend(pid) AS done,
                                left(regexp_replace(query, '\\s+', ' ', 'g'), 70) AS query
                         FROM pg_stat_activity
                         WHERE application_name = %s AND pid <> pg_backend_pid()""", (PIPELINE_APP,))
    print(f"[db] terminated {sum(1 for r in rows if r['done'])} of {len(rows)} pipeline backend(s)")
    for r in rows:
        print(f"      pid {r['pid']}  {r['query']}")
    return len(rows)


def killVacuum(cur):
    rows = _rows(cur, """SELECT a.pid, c.relname, pg_cancel_backend(a.pid) AS done
                         FROM pg_stat_progress_vacuum p
                         JOIN pg_class c ON c.oid = p.relid
                         JOIN pg_stat_activity a ON a.pid = p.pid""")
    print(f"[db] cancelled {sum(1 for r in rows if r['done'])} vacuum worker(s): "
          + ", ".join(r["relname"] for r in rows) + "\n      (autovacuum will try again later; "
          "the pipeline's own gentle VACUUM at step 18 is what should do this)")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kill-pipeline", action="store_true")
    ap.add_argument("--kill-vacuum", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="no report, only the kills")
    ap.add_argument("--indexes", action="store_true", help="the boards tables' indexes, and which the pages miss")
    args = ap.parse_args()
    with getConn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            if args.kill_pipeline:
                killPipeline(cur)
            if args.kill_vacuum:
                killVacuum(cur)
            if not args.quiet:
                report(cur)
            if args.indexes:
                indexes(cur)
    return 0


if __name__ == "__main__":
    sys.exit(main())
