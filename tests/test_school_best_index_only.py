"""schoolBest ranks on rr_school_idx alone and reads only the kept rows whole
(2026-11-12, cold reads): the same rows as the old whole-row query, chip on
and off; a school whose rows repeat a result_id takes the old query; and the
ranking is an index-only scan once the table is vacuumed.

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
from capped import fetchCappedPerPool  # noqa: E402


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
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS t_sbest CASCADE; CREATE SCHEMA t_sbest; SET search_path TO t_sbest")
    cur.execute("""CREATE TABLE ranking_results (result_id bigint, person_id bigint, pool text,
                   speed_rating real, distance real, sport text, year int, grade text,
                   race_date date, meet_id bigint, div_id bigint, event_id bigint, school text)""")
    cur.execute("CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text)")
    cur.execute("CREATE TABLE person_home_state (person_id bigint, state text)")
    rnd = random.Random(6)
    rows, names, homes = [], [], []
    for i in range(1, 6001):
        pid = rnd.randint(1, 900)
        school = "Oak" if i <= 5000 else "Elm"
        rows.append((i, pid, rnd.choice(("hs_m", "hs_f", "ms_m")),
                     None if rnd.random() < 0.05 else round(rnd.uniform(90, 190), 0),
                     5000, rnd.choice(("XC", "TF")), rnd.randint(2015, 2025), "11",
                     f"2024-10-{rnd.randint(1, 28):02d}", rnd.randint(1, 80), 1, None, school))
    for pid in range(1, 901):
        names.append((pid, f"A{pid}", "B"))
        if rnd.random() < 0.7:
            homes.append((pid, rnd.choice(("OH", "MI"))))
    rows.append((5, 77, "hs_m", 185.0, 5000, "XC", 2024, "11", "2024-10-01", 1, 1, None, "Elm"))
    rows.append((5, 77, "hs_m", 185.0, 5000, "XC", 2024, "11", "2024-10-01", 1, 1, None, "Elm"))
    psycopg2.extras.execute_values(cur, "INSERT INTO ranking_results VALUES %s", rows)
    psycopg2.extras.execute_values(cur, "INSERT INTO athletes VALUES %s", names)
    psycopg2.extras.execute_values(cur, "INSERT INTO person_home_state VALUES %s", homes)
    cur.execute("CREATE INDEX rr_school_idx ON ranking_results (school, sport, speed_rating DESC)"
                " INCLUDE (pool, result_id, person_id)")
    cur.execute("CREATE INDEX ON ranking_results (result_id)")
    cur.execute("CREATE INDEX ON person_home_state (person_id)")
    cur.execute("VACUUM (ANALYZE) ranking_results")
    yield cur
    cur.execute("DROP SCHEMA IF EXISTS t_sbest CASCADE")
    conn.close()


def _old(cur, school, sport, limit, state, primary):
    sf, sfp = S.stateFilterSql("rr", state, primary, school)
    cur.execute(S._BEST_WHOLE_ROWS.format(sf=sf),
                {"school": school, "sport": sport, "lim": limit + 1, **sfp})
    return fetchCappedPerPool(cur, limit)


def _same(new, old):
    """Equal up to the order ties at the cut fall in: the same ratings per
    pool, and the same rows above each pool's lowest kept rating (the old
    query's row_number broke ties at the cut arbitrarily too)."""
    def by_pool(rows):
        out = {}
        for r in rows:
            out.setdefault(r["pool"], []).append(dict(r))
        return out
    a, b = by_pool(new), by_pool(old)
    if set(a) != set(b):
        return False
    for pool in a:
        ra = sorted(r["rating"] for r in a[pool])
        if ra != sorted(r["rating"] for r in b[pool]):
            return False
        cut = ra[0]
        key = lambda r: r["result_id"]          # noqa: E731
        above = lambda rows: sorted((r for r in rows if r["rating"] > cut), key=key)  # noqa: E731
        if above(a[pool]) != above(b[pool]):
            return False
    return True


@pytest.mark.parametrize("state,primary", [(None, None), ("OH", "OH"), ("MI", "OH")])
@pytest.mark.parametrize("limit", [5, 100])
def test_same_rows_as_the_whole_row_query(pg, state, primary, limit):
    cur = pg
    for school in ("Oak", "Elm"):           # Elm repeats a result_id
        for sport in ("XC", "TF"):
            new = S.schoolBest(cur, school, sport, limit=limit, state=state, primary=primary)
            old = _old(cur, school, sport, limit, state, primary)
            assert _same(new, old), (school, sport, state, limit)
            assert new.truncated == old.truncated
            assert all("_dup" not in r for r in new)


def test_the_ranking_reads_the_index_alone(pg):
    cur = pg
    cur.execute("SET enable_seqscan = off")
    cur.execute("""EXPLAIN (ANALYZE, BUFFERS)
                   SELECT rr.result_id, row_number() OVER (PARTITION BY rr.pool
                          ORDER BY rr.speed_rating DESC) AS rn,
                          count(*) OVER (PARTITION BY rr.result_id) AS dup
                   FROM ranking_results rr
                   WHERE rr.school = 'Oak' AND rr.sport = 'XC' AND rr.speed_rating IS NOT NULL""")
    plan = " ".join(r["QUERY PLAN"] for r in cur.fetchall())
    assert "Index Only Scan using rr_school_idx" in plan, plan
    assert "Heap Fetches: 0" in plan, plan
