"""db_timing.FastRealDictCursor stands in for psycopg2's RealDictCursor on
every query the site runs (db_timing._TimedConn.cursor). It must hand back
exactly what RealDictCursor did: rows of class RealDictRow, the same keys in
the same order, the same values and types, the same pickle -- through
fetchone, fetchmany, fetchall, iteration and next(), on plain and named
(server-side) cursors, with NULLs, every common type, duplicate column
names, no rows and no columns.

    XCP_TWIN_TEST_DSN="host=... dbname=..." python -m pytest -q tests/test_fast_dict_cursor.py
"""
import os
import pickle
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts"):
    _p = os.path.join(_ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras                                      # noqa: E402

import db_timing                                            # noqa: E402

QUERIES = [
    # every common type, NULLs between them
    """SELECT g AS id, g * 7::bigint AS person_id, 'Name ' || g AS name,
              CASE WHEN mod(g, 3) = 0 THEN NULL ELSE 'S' || g END AS school,
              (mod(g, 900) + 400)::float8 AS rating, (g / 7.0)::numeric(10,3) AS num,
              date '2025-09-01' + g AS d, timestamp '2025-09-01 10:00' + g * interval '1 hour' AS ts,
              mod(g, 2) = 0 AS flag, ARRAY[g, g + 1] AS arr, json_build_object('a', g) AS js,
              'José Núñez ★'::text AS uni, ''::text AS blank
       FROM generate_series(1, %s) g""",
    # duplicate column names: first position, last value
    "SELECT g AS x, g * 2 AS y, g * 3 AS x FROM generate_series(1, %s) g",
    # no rows
    "SELECT g AS x FROM generate_series(1, %s) g WHERE false",
    # no columns
    "SELECT FROM generate_series(1, %s) g",
]


def _same(a, b):
    assert type(a) is type(b)
    if a is None:
        return
    assert isinstance(a, psycopg2.extras.RealDictRow)
    assert list(a.items()) == list(b.items())
    assert [type(v) for v in a.values()] == [type(v) for v in b.values()]
    assert pickle.dumps(a) == pickle.dumps(b)
    assert repr(a) == repr(b)


def _rows(conn, factory, how, sql, n, name=None):
    cur = conn.cursor(name=name, cursor_factory=factory) if name else conn.cursor(
        cursor_factory=factory)
    try:
        cur.execute(sql, (n,))
        if how == "fetchall":
            out = cur.fetchall()
        elif how == "fetchone":
            out = []
            while True:
                r = cur.fetchone()
                out.append(r)
                if r is None:
                    break
        elif how == "fetchmany":
            out = cur.fetchmany(3) + cur.fetchmany() + cur.fetchmany(size=1000)
        elif how == "iter":
            out = list(cur)
        elif how == "next":
            out = []
            try:
                while True:
                    out.append(next(cur))
            except StopIteration:
                pass
        # and a second query on the same cursor (the column mapping resets);
        # a named cursor runs one query only
        if not name:
            cur.execute("SELECT 1 AS one, 'b' AS two")
            out.append(cur.fetchone())
        return out
    finally:
        cur.close()


@pytest.fixture
def pgconn():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    conn = psycopg2.connect(dsn)
    yield conn
    conn.rollback()
    conn.close()


@pytest.mark.parametrize("how", ["fetchall", "fetchone", "fetchmany", "iter", "next"])
@pytest.mark.parametrize("named", [False, True])
def test_the_same_rows_as_realdictcursor(pgconn, how, named):
    for qi, sql in enumerate(QUERIES):
        for n in (0, 1, 5, 2500):
            if named and how == "next" and n == 2500:
                continue
            name = f"fast_{qi}_{n}" if named else None
            ref = _rows(pgconn, psycopg2.extras.RealDictCursor, how, sql, n, name)
            pgconn.rollback()
            got = _rows(pgconn, db_timing.FastRealDictCursor, how, sql, n, name)
            pgconn.rollback()
            assert len(got) == len(ref), (sql, n, how)
            for a, b in zip(got, ref):
                _same(a, b)


def test_the_site_gets_it_where_it_asked_for_realdictcursor(pgconn):
    conn = db_timing._TimedConn(pgconn)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        assert isinstance(cur._cur, db_timing.FastRealDictCursor)
        assert isinstance(cur._cur, psycopg2.extras.RealDictCursor)
        cur.execute("SELECT 1 AS a")
        _same(cur.fetchone(), psycopg2.extras.RealDictRow([("a", 1)]))
    with conn.cursor() as cur:                  # a plain cursor stays plain
        cur.execute("SELECT 1 AS a")
        assert cur.fetchone() == (1,)
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        assert type(cur._cur) is psycopg2.extras.DictCursor
