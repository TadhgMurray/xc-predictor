"""schoolTopAthletes names the kept people after grouping (2026-11-09)
instead of every season row before it: the same rows as the old query,
names, blanks and pools included, with and without a state chip.

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

import school as S  # noqa: E402

_OLD = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "school_top_2026_11_08.sql")).read()


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
    monkeypatch.setattr(S, "_fillRowNames", lambda cur, rows: None)
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS t_stop CASCADE; CREATE SCHEMA t_stop; SET search_path TO t_stop")
    cur.execute("""CREATE TABLE athlete_season (person_id bigint, school text, sport text,
                   pool text, year int, best_rating real)""")
    cur.execute("CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text)")
    cur.execute("CREATE TABLE person_home_state (person_id bigint, state text)")
    rnd = random.Random(3)
    seasons, names, homes = [], [], []
    for pid in range(1, 400):
        pool = rnd.choice(("hs_m", "hs_f", "ms_m"))
        for year in rnd.sample(range(2012, 2026), rnd.randint(1, 4)):
            seasons.append((pid, "Oak", rnd.choice(("XC", "TF")), pool, year,
                            None if rnd.random() < 0.05 else round(rnd.uniform(80, 200), 1)))
        k = rnd.random()
        if k < 0.6:
            names.append((pid, "Ann" + str(pid), "Lee"))
        elif k < 0.7:
            names.append((pid, " ", ""))                      # blanks
        elif k < 0.8:
            names.append((pid, "Bo", None))
            names.append((pid, None, "Ray" + str(pid)))       # two profiles
        if rnd.random() < 0.7:
            homes.append((pid, rnd.choice(("OR", "IL"))))
    psycopg2.extras.execute_values(cur, "INSERT INTO athlete_season VALUES %s", seasons)
    psycopg2.extras.execute_values(cur, "INSERT INTO athletes VALUES %s", names)
    psycopg2.extras.execute_values(cur, "INSERT INTO person_home_state VALUES %s", homes)
    yield cur
    conn.rollback()
    cur.execute("DROP SCHEMA IF EXISTS t_stop CASCADE")
    conn.commit()
    conn.close()


@pytest.mark.parametrize("state,primary", [(None, None), ("OR", "OR"), ("IL", "OR")])
@pytest.mark.parametrize("limit", [5, 100])
def test_the_same_rows_as_the_old_query(pg, state, primary, limit):
    cur = pg
    for sport in ("XC", "TF"):
        new = [dict(r) for r in S.schoolTopAthletes(cur, "Oak", sport, limit=limit,
                                                     state=state, primary=primary)]
        sf, sfp = S.stateFilterSql("s", state, primary, "Oak")
        cur.execute(_OLD.replace("{sf}", sf),
                    {"school": "Oak", "sport": sport, "lim": limit + 1, **sfp})
        from capped import fetchCappedPerPool
        old = [dict(r) for r in fetchCappedPerPool(cur, limit)]
        key = lambda r: (-r["best"], r["person_id"])              # noqa: E731
        assert sorted(new, key=key) == sorted(old, key=key), (sport, state)
        assert new
