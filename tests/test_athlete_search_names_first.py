"""The predictions page's "add anyone" search (/api/predict/athletes),
2026-11-08: when a token has three letters or more, the matching names are
read first through the trigram index (up to a cap) and only their seasons
are read. Pinned
here: every query answers exactly what the old one-join walk answered, short
tokens and gender included, and a query of only short tokens keeps the walk.

    XCP_TWIN_TEST_DSN="host=... dbname=postgres" runs the SQL half
"""
import contextlib
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import pytest  # noqa: E402

_FIRST = ["Collin", "Colin", "Collins", "Hannah", "Haley", "Jo", "John", "Joanna",
          "Mia", "Liam", "Noah", "Ava", None, "Al", "Ha"]
_LAST = ["Harper", "Hall", "Collins", "Smith", "Johnson", "Ha", "Li", "Nguyen",
         "Shah", "Haas", None, "O'Hara", "Ng"]


_CONN = []


@pytest.fixture
def pg(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import psycopg2.extras
    import app as A
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    cur.execute("DROP SCHEMA IF EXISTS t_asearch CASCADE; CREATE SCHEMA t_asearch;"
                " SET search_path TO t_asearch, public")
    cur.execute("""CREATE TABLE athletes (athlete_id bigint PRIMARY KEY,
                   first_name text, last_name text, gender text, school text)""")
    cur.execute("""CREATE TABLE athlete_season (person_id bigint, pool text, sport text,
                   year int, mean_rating double precision, school text)""")
    rnd = random.Random(5)
    ath, seas = [], []
    for pid in range(1, 3001):
        ath.append((pid, rnd.choice(_FIRST), rnd.choice(_LAST)))
        g = rnd.choice("mf")
        for year in rnd.sample(range(2019, 2027), rnd.randint(1, 4)):
            seas.append((pid, rnd.choice(("hs_", "ms_", "college_")) + g,
                         rnd.choice(("XC", "TF")), year,
                         None if rnd.random() < 0.1 else round(rnd.uniform(60, 200), 3),
                         f"School {pid % 50}"))
    psycopg2.extras.execute_values(
        cur, "INSERT INTO athletes (athlete_id, first_name, last_name) VALUES %s", ath)
    psycopg2.extras.execute_values(cur, "INSERT INTO athlete_season VALUES %s", seas)
    cur.execute("""CREATE INDEX ON athletes USING gin
                   ((COALESCE(first_name,'') || ' ' || COALESCE(last_name,'')) gin_trgm_ops)""")
    cur.execute("CREATE INDEX ON athlete_season (person_id)")
    cur.execute("ANALYZE athletes; ANALYZE athlete_season")
    conn.commit()                 # the route's rollback must not take the fixture

    @contextlib.contextmanager
    def _conn():
        try:
            yield conn
        finally:
            conn.rollback()
    monkeypatch.setattr(A, "getConn", _conn)
    _CONN[:] = [conn]
    yield A, conn
    conn.rollback()
    cur.execute("DROP SCHEMA IF EXISTS t_asearch CASCADE")
    conn.commit()
    conn.close()


_OLD_WALK = """
    SELECT * FROM (
        SELECT DISTINCT ON (s.person_id)
               s.person_id, s.school, s.year, s.mean_rating, s.pool,
               COALESCE(a.first_name,'') || ' ' || COALESCE(a.last_name,'') AS name
        FROM   athlete_season s JOIN athletes a ON a.athlete_id = s.person_id
        WHERE  {w}
        ORDER  BY s.person_id, s.year DESC
        LIMIT  200
    ) x
    ORDER  BY (CASE WHEN lower(name) LIKE %(prefix)s THEN 0 ELSE 1 END),
              mean_rating DESC NULLS LAST, person_id
    LIMIT  40"""


def _oldRows(conn, where, params):
    """The search as it ran before 2026-11-08: one join, DISTINCT ON."""
    import psycopg2.extras
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute(_OLD_WALK.format(w=" AND ".join(where)), params)
        rows = c.fetchall()
    conn.rollback()
    return rows


def _both(A, monkeypatch, query):
    """(the route's rows, the old walk's rows) for one request."""
    seen = {}
    real = A._athleteSearchRows

    def spy(where, params, tokens=()):
        rows = real(where, params, tokens)
        seen["args"] = (list(where), dict(params))
        return rows
    monkeypatch.setattr(A, "_athleteSearchRows", spy)
    r = A.app.test_client().get("/api/predict/athletes?" + query)
    assert r.status_code == 200, r.get_data(as_text=True)
    where, params = seen["args"]
    old = _oldRows(_CONN[0],
                   where, params)                 # the query as it was
    monkeypatch.setattr(A, "_athleteSearchRows", real)
    new = real(where, params, [t for t in params_tokens(params)])
    return [dict(x) for x in new], [dict(x) for x in old], r.get_json()


def params_tokens(params):
    out = []
    i = 0
    while f"t{i}" in params:
        out.append(params[f"t{i}"].strip("%"))
        i += 1
    return out


@pytest.mark.parametrize("q", [
    "q=Collin+ha&sport=XC&gender=M", "q=Collin+ha&sport=TF&gender=F",
    "q=collin&sport=XC", "q=ha+col&sport=XC&gender=M", "q=Hannah+Shah&sport=TF",
    "q=jo&sport=XC", "q=jo+li&sport=XC&gender=F", "q=o'h&sport=XC",
    "q=smith+jo+al+ng&sport=TF&gender=M", "q=zzz&sport=XC", "q=ng+ha&sport=TF",
])
def test_names_first_answers_what_the_walk_answered(pg, monkeypatch, q):
    A, _conn = pg
    new, old, body = _both(A, monkeypatch, q)
    assert new == old, q
    if q.startswith(("q=Collin", "q=collin", "q=jo&")):
        assert new, q                     # not two empty lists agreeing
    assert [a["person_id"] for a in body["athletes"]] == [r["person_id"] for r in new[:25]]


def test_the_sql_shape_follows_the_tokens(pg, monkeypatch):
    A, conn = pg
    sent = []
    real_cursor = conn.cursor

    class Rec:
        def __init__(self, c):
            self.c = c

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.c.close()

        def execute(self, sql, params=None):
            sent.append(sql)
            return self.c.execute(sql, params)

        def fetchall(self):
            return self.c.fetchall()

    class C:
        def cursor(self, **kw):
            return Rec(real_cursor(**kw))

        def rollback(self):
            conn.rollback()

    monkeypatch.setattr(A, "getConn", lambda: contextlib.nullcontext(C()))
    A.app.test_client().get("/api/predict/athletes?q=Collin+ha&sport=XC&gender=M")
    assert "unnest(%(m_ids)s" in sent[-1] and "m.name ILIKE %(t1)s" in sent[-1]
    assert "athletes a WHERE" in sent[-2] and "LIMIT" in sent[-2]
    assert any("statement_timeout" in s for s in sent)
    A.app.test_client().get("/api/predict/athletes?q=jo+li&sport=XC")
    assert "unnest(" not in sent[-1]
    assert "CROSS  JOIN LATERAL" in sent[-1] and "ORDER  BY a.athlete_id" in sent[-1]
    assert any("enable_bitmapscan = off" in x for x in sent)


def test_a_name_past_the_cap_keeps_the_walk_and_the_rows(pg, monkeypatch):
    A, _conn = pg
    q = "q=Collin+ha&sport=XC&gender=M"
    under, old, _ = _both(A, monkeypatch, q)
    monkeypatch.setattr(A, "NAME_FIRST_CAP", 3)      # "Collin" is now common
    over, old2, _ = _both(A, monkeypatch, q)
    assert under == over == old == old2 and under
