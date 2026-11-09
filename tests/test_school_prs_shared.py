"""A school's PR rows are fetched once per school, not once per ?year= /
?course= link (school_prs._sharedRows, 2026-11-09): the same rows as a
plain fetch, a second variant reads no SQL, a small school is not filed,
and a rebuilt (swapped) table is a new copy.

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

import school_prs as P  # noqa: E402


@pytest.fixture
def pg(monkeypatch, tmp_path):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import psycopg2.extras
    import rankings
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sharedcache, "_OIDS", {})
    monkeypatch.setattr(rankings, "_hasEventKind", lambda: False)
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS t_prs CASCADE; CREATE SCHEMA t_prs; SET search_path TO t_prs")
    cur.execute("""CREATE TABLE ranking_results (result_id bigint, person_id bigint, pool text,
                   speed_rating real, time_seconds real, distance real, race_date date,
                   year int, grade text, meet_id bigint, div_id bigint, event_id bigint,
                   school text, sport text)""")
    cur.execute("CREATE TABLE meets (meet_id bigint, div_id bigint, course_name text)")
    cur.execute("""INSERT INTO ranking_results
                   SELECT g, g % 300, CASE WHEN g % 2 = 0 THEN 'hs_m' ELSE 'hs_f' END,
                          100 + g % 50, 900 + g % 300, 5000, DATE '2011-09-01' + g % 60,
                          2010 + g % 5, '11', g % 40, g % 40, NULL,
                          CASE WHEN g <= 1200 THEN 'Big' ELSE 'Small' END, 'XC'
                   FROM generate_series(1, 1250) g""")
    cur.execute("""CREATE TABLE athletes (athlete_id bigint, person_id bigint, first_name text,
                   last_name text, gender text, school text)""")
    cur.execute("INSERT INTO athletes SELECT g, g, 'A', 'B' || g, 'M', 'Big' FROM generate_series(0, 299) g")
    cur.execute("INSERT INTO meets SELECT g, g, 'Course ' || g % 7 FROM generate_series(0, 39) g")
    yield conn, cur
    conn.rollback()
    cur.execute("DROP SCHEMA IF EXISTS t_prs CASCADE")
    conn.commit()
    conn.close()


class _Count:
    def __init__(self, cur):
        self.cur, self.n = cur, 0
        self.connection = cur.connection

    def execute(self, sql, *a):
        if "FROM   ranking_results rr" in sql:
            self.n += 1
        return self.cur.execute(sql, *a)

    def fetchall(self):
        return self.cur.fetchall()


def _plain(cur, school):
    cur.execute(P.runningSql("XC"), {"school": school, "sport": "XC"})
    return [dict(r) for r in cur.fetchall()]


def test_shared_rows_are_the_plain_rows_and_read_once(pg):
    _conn, cur = pg
    c = _Count(cur)
    first = P._runningRows(c, "Big", "XC")
    again = P._runningRows(c, "Big", "XC")
    assert first == again == _plain(cur, "Big") and len(first) == 1200
    assert c.n == 1                      # the second variant read no SQL
    a = P.schoolPrData(c, "Big", "XC", year_label=None)
    b = P.schoolPrData(c, "Big", "XC", course="Course 3")
    assert c.n == 1 and a["courses"] == b["courses"]


def test_a_small_school_is_fetched_each_time(pg):
    _conn, cur = pg
    c = _Count(cur)
    assert P._runningRows(c, "Small", "XC") == _plain(cur, "Small")
    P._runningRows(c, "Small", "XC")
    assert c.n == 2


def test_a_swapped_table_is_a_new_copy(pg):
    _conn, cur = pg
    import sharedcache
    c = _Count(cur)
    P._runningRows(c, "Big", "XC")
    cur.execute("CREATE TABLE ranking_results_new AS SELECT * FROM ranking_results")
    cur.execute("UPDATE ranking_results_new SET time_seconds = 1 WHERE result_id = 5")
    cur.execute("DROP TABLE ranking_results; ALTER TABLE ranking_results_new RENAME TO ranking_results")
    sharedcache._OIDS.clear()            # the minute's memo has passed
    rows = P._runningRows(c, "Big", "XC")
    assert c.n == 2 and rows == _plain(cur, "Big")
    assert any(r["time_seconds"] == 1 for r in rows)
