"""scripts/page_timing.py: the timed cursor and the report's arithmetic."""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "tests"))
import _env  # noqa: E402,F401
import page_timing as PT  # noqa: E402


def test_summary():
    rows = [("athlete", "/a/1", 200, 100.0, [(1, "x")] * 3),
            ("athlete", "/a/2", 200, 300.0, [(1, "x")]),
            ("static", "/", 200, 50.0, [])]
    s = PT.summarise(rows)
    assert s["athlete"][0] == 2 and s["athlete"][3] == 300.0 and s["athlete"][4] == 2.0
    assert s["static"][1] == 50.0


def test_the_timed_cursor_records_and_behaves():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import psycopg2.extras
    cx = psycopg2.connect(dsn)
    conn = PT._Conn(cx)
    PT._QUERIES.clear()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 AS one")
        assert cur.fetchone()[0] == 1
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT %s AS two", (2,))
        assert cur.fetchone()["two"] == 2
        cur.execute("SELECT generate_series(1, 3)")
        assert [r["generate_series"] for r in cur] == [1, 2, 3]
    assert [q[1] for q in PT._QUERIES] == ["SELECT 1 AS one", "SELECT %s AS two",
                                           "SELECT generate_series(1, 3)"]
    conn.rollback()
    cx.close()
