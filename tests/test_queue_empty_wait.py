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


def test_shed_stale_puts_old_re_asks_back_and_keeps_this_week_on_postgres():
    conn, cur = _pg()
    try:
        cur.execute("""
            CREATE TABLE meet_queue (meet_id bigint, sport text, source text,
                                     scraped int, PRIMARY KEY (meet_id, sport, source));
            CREATE TABLE meets (meet_id bigint, source text, meet_date text);
            CREATE TABLE results (meet_id bigint, source text);
        """)
        Q.ensureEmptyColumns(cur)
        rows = [
            # id, state, requeued?, empty_date
            (301, 0, True,  "current_date - 40"),  # old re-ask: shed
            (302, 0, True,  "NULL"),               # undated re-ask: shed
            (303, 0, True,  "current_date - 2"),   # this week's: kept
            (304, 0, False, "NULL"),               # never asked (a new id): kept
            (305, 3, True,  "current_date - 40"),  # claimed right now: kept
            (306, 0, True,  "NULL"),               # meet row dated this week: kept
            (307, 0, True,  "NULL"),               # has results now: kept
            (308, 0, False, "NULL"),               # unstamped, saved meet row
                                                   # dated long ago: shed
        ]
        for mid, st, rq, d in rows:
            cur.execute(f"""INSERT INTO meet_queue VALUES ({mid}, 'XC', 'anet', {st},
                            {"now() - interval '3 days'" if rq else "NULL"}, {d}, NULL)""")
        cur.execute("INSERT INTO meets VALUES (306, 'anet', "
                    "to_char(current_date - 1, 'YYYY-MM-DD'))")
        cur.execute("INSERT INTO meets VALUES (308, 'anet', "
                    "to_char(current_date - 60, 'YYYY-MM-DD'))")
        cur.execute("INSERT INTO results VALUES (307, 'anet')")
        assert Q.dueBreakdown(cur, "XC") == {
            "re-asked by a pass": 5, "a saved meet, not stamped": 1,
            "never saved, not stamped (new ids, or re-queued before 10-13)": 1}
        assert Q.shedStale(cur, "XC") == 3
        assert Q.shedStale(cur, "XC", write=True) == 3
        cur.execute("SELECT meet_id FROM meet_queue WHERE scraped = 1 ORDER BY 1")
        assert [r[0] for r in cur.fetchall()] == [301, 302, 308]
    finally:
        conn.rollback()
        cur.execute("DROP SCHEMA IF EXISTS queue_empty_test CASCADE")
        conn.commit()
        conn.close()
