"""A recent meet scraped while half posted is asked again (2026-11-01:
Mt. SAC 2026, anet XC 273894 -- races 1-40 in, races 41-87 empty).

Needs a scratch Postgres, like test_empty_meet_requeue.py:
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

DB = "xcp_partial_meet_test"
RECENT = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()


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
        CREATE TABLE results (meet_id bigint, div_id bigint, source text);
    """)
    rows = []
    # Mt. SAC: 8 registration divisions (empty for good), races 1-40 full, 41-87 empty
    for d in range(100, 108):
        rows.append((d, 1))
    for d in range(200, 287):
        rows.append((d, 1))
    # a complete meet that only has its registration divisions left empty
    for d in range(300, 304):
        rows.append((d, 2))
    for d in range(400, 410):
        rows.append((d, 2))
    c.executemany("INSERT INTO meets VALUES (%s, %s, 'anet', %s)",
                  [(d, m, RECENT) for d, m in rows])
    c.executemany("INSERT INTO results VALUES (%s, %s, 'anet')",
                  [(1, d) for d in range(200, 240)] + [(2, d) for d in range(400, 410)])
    c.executemany("INSERT INTO meet_queue (meet_id, sport, source, scraped) VALUES (%s, 'XC', 'anet', 1)",
                  [(1,), (2,)])
    yield c
    cx.close()


def test_half_a_meet_is_asked_again(cur):
    since = (datetime.date.today() - datetime.timedelta(days=11)).isoformat()
    assert Q.partialRecent(cur, "XC", since) == [1]
    assert Q.partialRecent(cur, "TF", since) == []
