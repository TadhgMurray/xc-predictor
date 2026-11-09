"""Runners like you reads eight numbers per comp and the five examples whole
(2026-11-09): the same answer as the old c.* read, ties and trajectories
included, and the band served by an index-only scan once the table is
vacuumed with the INCLUDE index build_comps makes.

    XCP_TWIN_TEST_DSN="host=... dbname=postgres" runs it
"""
import importlib.util
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

import comps as NEW  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "comps_old", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "comps_2026_11_08.py"))
OLD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OLD)


@pytest.fixture
def pg(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import psycopg2.extras
    for M in (NEW, OLD):
        monkeypatch.setattr(M, "_minRaces", lambda cur, sport, pool, now=None: 2)
        monkeypatch.setattr(M, "_nameRows", lambda cur, rows: rows)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS t_comps CASCADE; CREATE SCHEMA t_comps; SET search_path TO t_comps")
    cur.execute("""CREATE TABLE season_comps (person_id bigint, sport text, pool text, year int,
                   grade int, rating real, n_races int, school text, state text,
                   prev_rating real, prev_n int, next_rating real, senior_rating real,
                   college text, college_state text, college_division text,
                   college_rating real, college_year int)""")
    cur.execute("""CREATE TABLE season_comps_meta (sport text, pool text, sigma real,
                   n_seasons int, newest int)""")
    cur.execute("INSERT INTO season_comps_meta VALUES ('XC', 'hs_m', 3.3, 1000, 2026)")
    rnd = random.Random(8)
    rows = []
    for pid in range(1, 6001):
        g = rnd.choice((9, 10, 11, 12))
        y = rnd.randint(2015, 2026)
        r = round(rnd.uniform(80, 110), 1)          # coarse: many exact ties
        prev = None if rnd.random() < 0.3 else round(r - rnd.uniform(-3, 8), 1)
        rows.append((pid, "XC", "hs_m", y, g, r, rnd.randint(2, 9), "S", "OR",
                     prev, None if prev is None else rnd.randint(2, 8),
                     None if rnd.random() < 0.3 else r + 2, None if rnd.random() < 0.5 else r + 5,
                     None if rnd.random() < 0.6 else "Coll", None,
                     rnd.choice((None, "NCAA DI", "NAIA")), None, None))
    psycopg2.extras.execute_values(cur, "INSERT INTO season_comps VALUES %s", rows)
    cur.execute("""CREATE INDEX idx_season_comps_match ON season_comps (sport, pool, grade, rating)
                   INCLUDE (person_id, year, n_races, prev_rating, prev_n, next_rating,
                            senior_rating, college, college_division)""")
    cur.execute("CREATE INDEX ON season_comps (person_id)")
    cur.execute("CREATE TABLE recruit_projection (person_id bigint, sport text, year int,"
                " proj_rating real, proj_sigma_pct real, horizon_weeks int)")
    cur.execute("VACUUM (ANALYZE) season_comps")
    conn.autocommit = False                 # the page runs in a transaction
    cur.execute("SET search_path TO t_comps")
    yield cur
    conn.rollback()
    conn.autocommit = True
    cur.execute("DROP SCHEMA IF EXISTS t_comps CASCADE")
    conn.close()


def test_the_same_answer_as_the_whole_row_read(pg):
    cur = pg
    cur.execute("SELECT person_id FROM season_comps WHERE year = 2026 ORDER BY person_id LIMIT 60")
    subjects = [r["person_id"] for r in cur.fetchall()]
    checked = 0
    for pid in subjects:
        new = NEW.runnersLikeYou(cur, pid, "XC")
        old = OLD.runnersLikeYou(cur, pid, "XC")
        if old is None:
            assert new is None
            continue
        ex_key = lambda e: (float(e["dist"]), -e["year"], e["person_id"])   # noqa: E731
        assert sorted(map(dict, new.pop("examples")), key=ex_key) == \
            sorted(map(dict, old.pop("examples")), key=ex_key), pid
        # divisions tied on count come in row order, which no plan defines
        for r in (new, old):
            if r.get("college"):
                r["college"]["divisions"] = sorted(r["college"]["divisions"],
                                                   key=lambda kv: (-kv[1], kv[0]))
        assert new == old, pid
        checked += old["n"] > 0
    assert checked > 20


def test_the_band_is_an_index_only_scan(pg):
    cur = pg
    cur.execute("SET enable_seqscan = off")
    cur.execute("""EXPLAIN SELECT c.person_id, c.year, c.next_rating, c.senior_rating,
                          c.college, c.college_division,
                          abs(c.rating - 95) / sqrt(1 + 10.0 / c.n_races) AS dist
                   FROM season_comps c
                   WHERE c.sport = 'XC' AND c.pool = 'hs_m' AND c.grade = 11
                     AND c.rating BETWEEN 92 AND 98 AND c.person_id <> 1 AND c.year < 2026
                     AND c.prev_rating IS NOT NULL""")
    plan = " ".join(r["QUERY PLAN"] for r in cur.fetchall())
    assert "Index Only Scan" in plan, plan
