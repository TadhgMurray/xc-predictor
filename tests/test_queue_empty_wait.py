"""An empty meet waits for its own date (owner, 2026-10-15: "most of the
meets are skips"): the re-ask passes skip a meet dated after today, and ask a
long-past meet athletic.net says has no results weekly, not daily.

    XCP_QUEUE_TEST_DSN="host=... dbname=..." python -m pytest -q tests/test_queue_empty_wait.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import pytest                                                    # noqa: E402

import queue_meets as Q                                          # noqa: E402


def _pg():
    dsn = os.environ.get("XCP_QUEUE_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_QUEUE_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS queue_empty_test CASCADE; "
                "CREATE SCHEMA queue_empty_test; SET search_path = queue_empty_test")
    return conn, cur


def test_empty_meets_wait_for_their_date_on_postgres():
    conn, cur = _pg()
    try:
        cur.execute("""
            CREATE TABLE meet_queue (meet_id bigint, sport text, source text,
                                     scraped int, PRIMARY KEY (meet_id, sport, source));
            CREATE TABLE meets (meet_id bigint, source text);
            CREATE TABLE results (meet_id bigint, source text);
        """)
        Q.ensureEmptyColumns(cur)
        rows = [
            # id, empty_date,                  has_results, asked hours ago
            (101, "current_date + 10", "false", 30),   # not run yet: wait
            (102, "current_date - 40", "false", 48),   # stale, asked 2 days ago: weekly
            (103, "current_date - 40", "false", 200),  # stale, asked 8 days ago: ask
            (104, "NULL",              "NULL",  30),   # no facts: as before
            (105, "current_date - 40", "true",  48),   # it HAS results: daily
            (106, "current_date - 3",  "false", 30),   # just run, not stale: daily
            (107, "current_date - 3",  "false", 2),    # asked 2 h ago: rested
        ]
        for mid, d, hr, ago in rows:
            cur.execute(f"""INSERT INTO meet_queue VALUES ({mid}, 'XC', 'anet', 1,
                            now() - interval '{ago} hours', {d}, {hr})""")
        got = Q.emptyUnrecorded(cur, "XC", top=200, span=1000)
        assert got == [103, 104, 105, 106], got
    finally:
        conn.rollback()
        cur.execute("DROP SCHEMA IF EXISTS queue_empty_test CASCADE")
        conn.commit()
        conn.close()


def test_the_default_pass_is_one_week_and_deep_is_the_old_sweep_on_postgres():
    conn, cur = _pg()
    try:
        cur.execute("""
            CREATE TABLE meet_queue (meet_id bigint, sport text, source text,
                                     scraped int, PRIMARY KEY (meet_id, sport, source));
            CREATE TABLE meets (meet_id bigint, source text);
            CREATE TABLE results (meet_id bigint, source text);
        """)
        Q.ensureEmptyColumns(cur)
        # asked two days ago, empty, no meet row: dated 3 days ago, dated 14
        # days ago (outside the week, not yet stale), and never dated (asked
        # before the date was kept)
        for mid, d in ((201, "current_date - 3"), (202, "current_date - 14"),
                       (203, "NULL")):
            cur.execute(f"""INSERT INTO meet_queue VALUES ({mid}, 'XC', 'anet', 1,
                            now() - interval '48 hours', {d}, NULL)""")
        import datetime
        week = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        assert Q.emptyUnrecorded(cur, "XC", top=300, span=1000, since=week) == [201]
        assert Q.emptyUnrecorded(cur, "XC", top=300, span=1000) == [201, 202, 203]
    finally:
        conn.rollback()
        cur.execute("DROP SCHEMA IF EXISTS queue_empty_test CASCADE")
        conn.commit()
        conn.close()
