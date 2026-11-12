"""Precomputed school pages (school_pages.py, build_school_pages.py,
2026-11-12): exact payloads, served only for the boards, code and age they
were built for, read through school.sharedSchool without calling the
function, and a missing table never breaks the page's transaction.

    XCP_TWIN_TEST_DSN="host=... dbname=postgres" runs the SQL half
"""
import datetime
import decimal
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import pytest  # noqa: E402

import school_pages as SP  # noqa: E402
from capped import Capped  # noqa: E402


def _best():
    c = Capped([{"person_id": 1, "rating": 151.5, "race_date": datetime.date(2025, 10, 4),
                 "avg": decimal.Decimal("150.2"), "pool": "hs_m", "name": None}])
    c.truncated, c.shown = True, 100
    return c


def test_payloads_round_trip_exactly():
    for v in (_best(), {"athletes": 812, "first_year": 2009, "state": "OH"}, 2026, None,
              [{"year": 2025, "sport": "XC", "label": 2025, "athletes": 61}]):
        back = SP.payloadFrom(SP.payloadText(v))
        assert SP.sameValue(back, v)
    back = SP.payloadFrom(SP.payloadText(_best()))
    assert type(back) is Capped and back.truncated is True and back.shown == 100
    assert type(back[0]["avg"]) is decimal.Decimal and type(back[0]["race_date"]) is datetime.date
    with pytest.raises(TypeError):
        SP.payloadText({"x": {1, 2}})                       # a set cannot come back


@pytest.fixture
def pg(monkeypatch, tmp_path):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sharedcache, "_OIDS", {})
    monkeypatch.setattr(SP, "_NO_TABLE", {"until": 0.0})
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS t_spages CASCADE; CREATE SCHEMA t_spages;"
                " SET search_path TO t_spages")
    cur.execute("CREATE TABLE athlete_season (school text)")
    cur.execute("CREATE TABLE ranking_results (school text)")
    conn.commit()
    yield conn
    conn.rollback()
    cur.execute("DROP SCHEMA IF EXISTS t_spages CASCADE")
    conn.commit()
    conn.close()


def _oids(conn):
    import build_school_pages as B
    return B.currentOids(conn)


def test_built_rows_are_served_and_only_while_valid(pg, monkeypatch):
    import build_school_pages as B
    import school as S
    conn = pg
    calls = []

    def schoolBest(cur, school, sport, limit=25, state=None, primary=None):
        calls.append(school)
        return _best()
    monkeypatch.setattr(B, "pageCalls", lambda cur, school: [
        (schoolBest, (school, "XC"), {"limit": 100, "state": "OH", "primary": "OH"})])
    oids = _oids(conn)
    rows, failed = B.computeAll(["Perrysburg"], 1, lambda: _ctx(conn), oids, log=lambda *_: None)
    assert not failed and len(rows["Perrysburg"]) == 1
    B.publish(conn, rows, 1, oids, in_place=True)
    cur = conn.cursor()
    cur.execute("SET search_path TO t_spages")
    got = SP.storedValue(cur, "schoolBest", ("Perrysburg", "XC"),
                         {"limit": 100, "state": "OH", "primary": "OH"}, oids)
    assert SP.sameValue(got, _best())
    # through the page's own entry point: no call to the function
    monkeypatch.setattr(S, "PRECOMPUTED", ("schoolBest",))
    calls.clear()
    v = S.sharedSchool(cur, schoolBest, "Perrysburg", "XC", limit=100, state="OH", primary="OH")
    assert calls == [] and SP.sameValue(v, _best())
    # other boards, other code, too old: a miss
    assert SP.storedValue(cur, "schoolBest", ("Perrysburg", "XC"),
                          {"limit": 100, "state": "OH", "primary": "OH"}, (1, 2)) is None
    monkeypatch.setattr(SP, "CODE", "other")
    assert SP.storedValue(cur, "schoolBest", ("Perrysburg", "XC"),
                          {"limit": 100, "state": "OH", "primary": "OH"}, oids) is None
    monkeypatch.undo()


def test_a_missing_table_is_a_miss_and_the_transaction_lives(pg):
    conn = pg
    cur = conn.cursor()
    cur.execute("SET search_path TO t_spages")
    cur.execute("SELECT 1")
    assert SP.storedValue(cur, "schoolBest", ("X", "XC"), {}, (1, 2)) is None
    cur.execute("SELECT 2")                       # not aborted
    assert cur.fetchone()[0] == 2


class _ctx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        with self.conn.cursor() as c:
            c.execute("SET search_path TO t_spages")
        return self.conn

    def __exit__(self, *a):
        return False
