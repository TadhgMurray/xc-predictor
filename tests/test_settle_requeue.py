"""A meet scraped on its own day is asked once more, days later (owner,
2026-11-02: ICCAC 2026, scraped at 04:12 on race day and never again).

Needs a scratch Postgres, like test_partial_meet_requeue.py:
  XCP_TWIN_TEST_DSN="host=127.0.0.1 user=postgres password=... dbname=postgres"
"""
import datetime
import os
import sys

import pytest

import _env  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import queue_meets as Q                                          # noqa: E402

DB = "xcp_settle_test"
TODAY = datetime.date.today()


def _d(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


@pytest.fixture
def cur():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
        c.execute(f"CREATE DATABASE {DB}")
    cx = psycopg2.connect(" ".join(p for p in dsn.split() if not p.startswith("dbname="))
                          + f" dbname={DB}")
    c = cx.cursor()
    c.execute("""
        CREATE TABLE meet_queue (meet_id bigint, sport text, source text, scraped int,
                                 requeued_at timestamptz, empty_date date,
                                 empty_has_results boolean,
                                 PRIMARY KEY (meet_id, sport, source));
        CREATE TABLE meets (div_id bigint, meet_id bigint, source text, meet_date text);
        CREATE TABLE results (result_id bigint, meet_id bigint, div_id bigint, source text,
                              scraped_at timestamp);
    """)
    meets = {1: (_d(5), _d(5)),      # scraped on race day, 5 days ago: ask again
             2: (_d(5), _d(2)),      # scraped 3 days after: settled
             3: (_d(1), _d(1)),      # race yesterday: too soon
             4: (_d(40), _d(40))}    # too old
    for m, (day, scraped) in meets.items():
        c.execute("INSERT INTO meets VALUES (%s, %s, 'anet', %s)", (m * 10, m, day))
        c.execute("INSERT INTO results VALUES (%s, %s, %s, 'anet', %s)", (m * 100, m, m * 10, scraped))
        c.execute("INSERT INTO meet_queue (meet_id, sport, source, scraped) VALUES (%s, 'XC', 'anet', 1)", (m,))
    yield c
    cx.close()


def test_scraped_on_the_day_is_asked_again(cur):
    assert Q.settleRecent(cur, "XC") == [1]


def test_asked_today_waits(cur):
    cur.execute("UPDATE meet_queue SET requeued_at = now() WHERE meet_id = 1")
    assert Q.settleRecent(cur, "XC") == []
