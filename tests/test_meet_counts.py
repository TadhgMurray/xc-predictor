"""/meets' counts by key (build_meet_counts.py, meets_filter.countsValid,
2026-11-12): the same rows as counting every result per meet, used only
while results is the table counted and unchanged since; an insert sends the
view back to counting live.

    XCP_TWIN_TEST_DSN="host=... dbname=postgres" runs it
"""
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

import build_meet_counts as B  # noqa: E402
import meets_filter as M  # noqa: E402


def _f(**kw):
    f = {"sport": "XC", "state": "", "school": "", "q": "", "year": None, "year_label": None,
         "champ": False, "unit": "", "kind": "", "units": {}, "level": "", "from": "", "to": ""}
    f.update(kw)
    return f


@pytest.fixture
def pg(monkeypatch, tmp_path):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import psycopg2.extras
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sharedcache, "tableOid", lambda cur, t, now=None: None)   # no sharing
    monkeypatch.setattr(M, "hasMeetUnits", lambda cur: False)
    monkeypatch.setattr(M, "meetDatesIndexed", lambda cur, sport: False)
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS t_mcounts CASCADE; CREATE SCHEMA t_mcounts;"
                " SET search_path TO t_mcounts")
    cur.execute("CREATE TABLE meets (meet_id bigint, div_id bigint, meet_name text,"
                " course_name text, state text, source text)")
    cur.execute("CREATE TABLE results (result_id bigint, meet_id bigint, date text)")
    rnd = random.Random(4)
    meets, res = [], []
    for mid in range(1, 400):
        meets.append((mid, 1, f"Meet {mid}", "Course", rnd.choice(["CA", "OR"]), "anet"))
        for k in range(rnd.randint(0, 30)):
            day = f"20{rnd.randint(15, 25)}-{rnd.randint(9, 11):02d}-{rnd.randint(10, 28):02d}"
            res.append((mid * 1000 + k, mid, day if rnd.random() > 0.05 else "junk"))
    psycopg2.extras.execute_values(cur, "INSERT INTO meets VALUES %s", meets)
    psycopg2.extras.execute_values(cur, "INSERT INTO results VALUES %s", res)
    conn.commit()
    cur.execute("SELECT pg_stat_force_next_flush()")
    conn.commit()
    yield conn, cur
    conn.rollback()
    cur.execute("DROP SCHEMA IF EXISTS t_mcounts CASCADE")
    conn.commit()
    conn.close()


def _build(conn):
    """The builder's aggregate and stamps, into this schema."""
    with conn.cursor() as c:
        st = B.stamp(c, "results")
        c.execute("""CREATE TABLE meet_counts (sport text, meet_id bigint, date text, n_results int,
                     PRIMARY KEY (sport, meet_id))""")
        c.execute("""INSERT INTO meet_counts SELECT 'XC', meet_id, max(date), count(*) FROM results
                     WHERE date ~ %s AND meet_id IS NOT NULL GROUP BY meet_id""", (B._SANE,))
        c.execute("CREATE TABLE meet_counts_meta (sport text PRIMARY KEY, tbl text, oid bigint,"
                  " stamp bigint, built_at timestamptz DEFAULT now())")
        c.execute("INSERT INTO meet_counts_meta (sport, tbl, oid, stamp) VALUES ('XC', 'results', %s, %s)",
                  st)
    conn.commit()


def test_same_rows_and_off_after_a_change(pg):
    conn, cur = pg
    views = ((), (("state", "CA"),), (("q", "Meet 1"),))
    live = {k: M.filteredMeets(cur, _f(**dict(k))) for k in views}
    _build(conn)
    assert M.countsValid(cur, "XC", "results")
    for k, rows in live.items():
        key = lambda r: (r["date"], r["meet_id"])           # noqa: E731
        assert sorted(M.filteredMeets(cur, _f(**dict(k))), key=key) == sorted(rows, key=key), k
        assert rows
    # a season filter always counts live (the counts have no season in them)
    assert M.filteredMeets(cur, _f(year=2020)) is not None
    cur.execute("INSERT INTO results VALUES (999999, 1, '2026-10-01')")
    conn.commit()
    cur.execute("SELECT pg_stat_force_next_flush()")
    conn.commit()
    assert not M.countsValid(cur, "XC", "results")          # changed: count live again
