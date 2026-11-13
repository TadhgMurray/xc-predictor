"""The school pages' whole-history reads run on rr_school_season_idx alone
(2026-11-12, cold reads): the meet list (school.schoolMeets), the PRs'
running rows (school_prs.runningSql) and roster.racesRun are index-only
scans with no heap fetch once the table is vacuumed -- with the state chip,
a season and a level too.

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


def _index_def():
    """rr_school_season_idx as build_ranking_results declares it."""
    import re
    src = open(os.path.join(_ROOT, "racecast", "build_ranking_results.py")).read()
    i = src.index('("rr_school_season_idx", ')
    j = src.index("),", src.index(")", src.index("INCLUDE", i)) + 1)
    body = src[i:j + 1]
    return " ".join(re.findall(r'"([^"]*)"', body)[1:])


@pytest.fixture
def pg(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import psycopg2.extras
    import school_identity
    monkeypatch.setitem(school_identity._LABELS, "athlete_state", False)
    monkeypatch.setattr(school_identity, "_collegeState", lambda s: None)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS t_shist CASCADE; CREATE SCHEMA t_shist; SET search_path TO t_shist")
    cur.execute("""CREATE TABLE ranking_results (result_id bigint, person_id bigint, pool text,
                   speed_rating real, time_seconds real, distance real, sport text, year int,
                   grade text, race_date date, meet_id bigint, div_id bigint, event_id bigint,
                   event_kind text, school text, state text, filler text)""")
    cur.execute("CREATE TABLE person_home_state (person_id bigint, state text)")
    cur.execute("CREATE TABLE meets (meet_id bigint, div_id bigint, meet_name text, course_name text, source text)")
    cur.execute("CREATE TABLE meets_tfrrs (meet_id bigint, sport text, meet_name text)")
    rnd = random.Random(2)
    rows = []
    for i in range(1, 60001):
        rows.append((i, rnd.randint(1, 5000), rnd.choice(("hs_m", "hs_f")), round(rnd.uniform(90, 190), 1),
                     rnd.uniform(800, 1200), 5000, rnd.choice(("XC", "TF")), rnd.randint(2015, 2025),
                     "11", f"2024-10-{rnd.randint(1, 28):02d}", rnd.randint(1, 900), 1, None, None,
                     f"School {i % 40}", "OH", "x" * 200))
    psycopg2.extras.execute_values(cur, "INSERT INTO ranking_results VALUES %s", rows)
    psycopg2.extras.execute_values(cur, "INSERT INTO person_home_state VALUES %s",
                                   [(p, rnd.choice(("OH", "MI"))) for p in range(1, 5001)])
    psycopg2.extras.execute_values(cur, "INSERT INTO meets VALUES %s",
                                   [(m, 1, f"Meet {m}", "Course", "anet") for m in range(1, 901)])
    cur.execute(f"CREATE INDEX rr_school_season_idx ON ranking_results {_index_def()}")
    cur.execute("CREATE INDEX ON person_home_state (person_id)")
    cur.execute("CREATE INDEX ON meets (meet_id, div_id)")
    cur.execute("VACUUM (ANALYZE) ranking_results")
    cur.execute("SET enable_seqscan = off")
    cur.execute("SET enable_bitmapscan = off")
    yield cur
    cur.execute("DROP SCHEMA IF EXISTS t_shist CASCADE")
    conn.close()


class _Rec:
    """A cursor that keeps every statement, for EXPLAIN."""
    def __init__(self, cur):
        self.cur, self.sent = cur, []
        self.connection = cur.connection

    def execute(self, sql, params=None):
        self.sent.append((sql, params))
        return self.cur.execute(sql, params)

    def __getattr__(self, k):
        return getattr(self.cur, k)


def _rr_scans(cur, sql, params):
    cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
    plan = [r["QUERY PLAN"] for r in cur.fetchall()]
    scans = [i for i, line in enumerate(plan) if "on ranking_results" in line]
    assert scans, "\n".join(plan)
    for i in scans:
        assert "Index Only Scan using rr_school_season_idx" in plan[i], "\n".join(plan)
        fetch = next(line for line in plan[i:i + 8] if "Heap Fetches" in line)
        assert fetch.strip().endswith("Heap Fetches: 0"), "\n".join(plan)
    return plan


def test_the_index_carries_every_column_the_readers_read():
    d = _index_def()
    for col in ("result_id", "race_date", "div_id", "speed_rating", "pool", "distance",
                "person_id", "time_seconds", "grade", "event_id", "event_kind"):
        assert col in d, col


@pytest.mark.parametrize("kw", [{}, {"year": 2024}, {"state": "OH", "primary": "OH"},
                                {"level": "hs"}])
def test_the_meet_list_is_index_only(pg, kw):
    import school as S
    rec = _Rec(pg)
    S.schoolMeets(rec, "School 3", "XC", **kw)
    sql, params = next((q, p) for q, p in rec.sent if "WITH g AS" in q)
    _rr_scans(pg, sql, params)


@pytest.mark.parametrize("event_kind", [False, True])
def test_the_prs_running_rows_are_index_only(pg, event_kind):
    import school_prs as P
    _rr_scans(pg, P.runningSql("XC", event_kind=event_kind), {"school": "School 3", "sport": "XC"})
    _rr_scans(pg, P.runningSql("TF", event_kind=event_kind), {"school": "School 3", "sport": "TF"})


def test_races_run_is_index_only(pg):
    import roster as R
    rec = _Rec(pg)
    R.racesRun(rec, ["School 3", "School 4"], "XC", 2024)
    sql, params = rec.sent[-1]
    _rr_scans(pg, sql, params)
