"""school.sharedSchool (2026-11-09): a school's year-independent pieces are
computed once for the box -- rows come back as plain dicts, a Capped list
keeps its truncated flag, a degraded header or a missing school is not
kept, and a swapped-in table is a new copy.

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

import school as S  # noqa: E402
from capped import Capped  # noqa: E402


@pytest.fixture
def pg(monkeypatch, tmp_path):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import psycopg2.extras
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sharedcache, "_OIDS", {})
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS t_school CASCADE; CREATE SCHEMA t_school;"
                " SET search_path TO t_school")
    cur.execute("CREATE TABLE athlete_season (person_id bigint, mean_rating real)")
    cur.execute("CREATE TABLE ranking_results (result_id bigint, school text, time_seconds real)")
    cur.execute("INSERT INTO ranking_results VALUES (1, 'Oak', 900), (2, 'Oak', 910), (3, 'Oak', 920)")
    yield cur
    conn.rollback()
    cur.execute("DROP SCHEMA IF EXISTS t_school CASCADE")
    conn.commit()
    conn.close()


def _best(cur, school, sport, limit=2):
    _best.calls += 1
    cur.execute("SELECT result_id, time_seconds FROM ranking_results WHERE school = %s"
                " ORDER BY time_seconds LIMIT %s", (school, limit + 1))
    rows = cur.fetchall()
    out = Capped(rows[:limit])
    out.truncated = len(rows) > limit
    return out


def test_shared_once_plain_rows_and_the_cap_flag(pg):
    cur = pg
    _best.calls = 0
    a = S.sharedSchool(cur, _best, "Oak", "XC", limit=2)
    b = S.sharedSchool(cur, _best, "Oak", "XC", limit=2)
    assert _best.calls == 1
    assert a == b == [{"result_id": 1, "time_seconds": 900.0},
                      {"result_id": 2, "time_seconds": 910.0}]
    assert isinstance(b, Capped) and b.truncated is True
    assert type(b[0]) is dict
    S.sharedSchool(cur, _best, "Oak", "XC", limit=5)       # other args: own copy
    assert _best.calls == 2


def test_degraded_answers_are_not_kept(pg):
    cur = pg
    calls = []

    def header(cur, school):
        calls.append(school)
        return {"athletes": None, "first_year": None} if school == "Slow" else None
    S.sharedSchool(cur, header, "Slow")
    S.sharedSchool(cur, header, "Slow")
    S.sharedSchool(cur, header, "Gone")
    S.sharedSchool(cur, header, "Gone")
    assert calls == ["Slow", "Slow", "Gone", "Gone"]


def test_a_swapped_table_is_a_new_copy(pg):
    import sharedcache
    cur = pg
    _best.calls = 0
    S.sharedSchool(cur, _best, "Oak", "XC")
    cur.execute("CREATE TABLE ranking_results_new AS SELECT * FROM ranking_results")
    cur.execute("UPDATE ranking_results_new SET time_seconds = 1 WHERE result_id = 3")
    cur.execute("DROP TABLE ranking_results; ALTER TABLE ranking_results_new RENAME TO ranking_results")
    sharedcache._OIDS.clear()
    rows = S.sharedSchool(cur, _best, "Oak", "XC")
    assert _best.calls == 2 and rows[0]["result_id"] == 3
