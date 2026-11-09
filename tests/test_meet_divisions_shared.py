"""app.get_meet_divisions is computed once per meet and feed for the box
(2026-11-09), keyed by results' oid; an empty answer is not kept.

    XCP_TWIN_TEST_DSN="host=... dbname=postgres" runs it
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import pytest  # noqa: E402


def test_once_per_meet_and_feed(monkeypatch, tmp_path):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import app as A
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sharedcache, "_OIDS", {})
    calls = []

    def rows(cur, meet_id, source=None):
        calls.append((meet_id, source))
        return [] if meet_id == 9 else [{"div_id": 1, "division": "Varsity", "n_results": 200}]
    monkeypatch.setattr(A, "_meetDivisionsRows", rows)
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS t_mdiv CASCADE; CREATE SCHEMA t_mdiv; SET search_path TO t_mdiv")
    cur.execute("CREATE TABLE results (result_id bigint)")
    try:
        a = A.get_meet_divisions(cur, 5, source="anet")
        a[0]["division"] = "edited"                  # a caller's edit is its own
        b = A.get_meet_divisions(cur, 5, source="anet")
        assert b == [{"div_id": 1, "division": "Varsity", "n_results": 200}]
        A.get_meet_divisions(cur, 5, source="tfrrs")
        A.get_meet_divisions(cur, 9)
        A.get_meet_divisions(cur, 9)
        assert calls == [(5, "anet"), (5, "tfrrs"), (9, None), (9, None)]
    finally:
        conn.rollback()
        conn.close()
