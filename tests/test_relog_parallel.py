"""The board-order rewrite's parallel sort (build_ranking_results._parallelSort,
XCP_RR_RELOG_PARALLEL / XCP_SEASON_REWRITE_PARALLEL) leaves the same table
as the one-statement INSERT ... ORDER BY: every (ctid, row), every column
definition, default, constraint and reloption, LOGGED.

    XCP_TWIN_TEST_DSN="host=127.0.0.1 port=5432 user=postgres password=pw dbname=postgres" \\
        python -m pytest -q tests/test_relog_parallel.py

Makes its own database (xcp_relog_parallel_test) and drops it afterwards.
Parallel plans are forced cheap (parallel_setup_cost 0 ...) so a small
table takes the Gather Merge path the 157.8M-row one takes.
"""
import contextlib
import io
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402

_DB = "xcp_relog_parallel_test"
_FORCE = ("SET parallel_setup_cost = 0", "SET parallel_tuple_cost = 0",
          "SET min_parallel_table_scan_size = 0", "SET max_parallel_workers_per_gather = 3",
          "SET jit = off")


@pytest.fixture(scope="module")
def dsn():
    base = os.environ.get("XCP_TWIN_TEST_DSN")
    if not base:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    admin = psycopg2.connect(base)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {_DB} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {_DB}")
    yield " ".join(kv for kv in base.split() if not kv.startswith("dbname=")) + f" dbname={_DB}"
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {_DB} WITH (FORCE)")
    admin.close()


_ROWS = 300000
_UNITS = ("division", "region", "conference", "league", "state_div", "section_div",
          "district", "county", "class", "area", "section")

# a shadow with everything LIKE copies: NOT NULL, defaults, CHECKs, a
# collation, typmods; rows of uneven width (so the INSERT's free-space map
# tops up earlier pages) in scrape order, not board order
_RR = f"""
CREATE UNLOGGED TABLE {{t}} (
    sport text NOT NULL, result_id bigint NOT NULL, person_id bigint NOT NULL,
    pool text COLLATE "C" NOT NULL, speed_rating real, race_date date, year integer DEFAULT 2026,
    state varchar(4), school text, grade text DEFAULT 'x', meet_id bigint, mark numeric(8,2),
    CONSTRAINT rr_t_pos CHECK (result_id <> 0), CONSTRAINT rr_t_year CHECK (year > 1900))
    WITH (autovacuum_enabled = false);
INSERT INTO {{t}}
SELECT CASE WHEN i % 5 < 3 THEN 'XC' ELSE 'TF' END, (i::bigint * 7919) % {_ROWS} + 1, i % 40000,
       (ARRAY['hs_m','hs_f','ms_m','ms_f','college_m','college_f'])[1 + i % 6],
       CASE WHEN i % 13 = 0 THEN NULL ELSE ((i * 31) % 2000) / 10.0 END,
       DATE '2020-01-01' + i % 2000, 2005 + i % 22, 'S' || i % 50,
       repeat('school ', 1 + (i * 17) % 23) || i, (i % 12)::text, i / 50, (i % 997) / 7.0
FROM generate_series(1, {_ROWS}) i ORDER BY i / 50, md5(i::text)
"""

_AS = f"""
CREATE UNLOGGED TABLE {{t}} (
    person_id bigint NOT NULL, pool text NOT NULL, sport text NOT NULL, year integer NOT NULL,
    mean_rating real, n_races integer DEFAULT 0, state text, school text,
    {", ".join(f'"{u}" text' for u in _UNITS)}, CONSTRAINT as_t_n CHECK (n_races >= 0))
    WITH (autovacuum_enabled = false);
INSERT INTO {{t}} (person_id, pool, sport, year, mean_rating, n_races, state, school)
SELECT i, (ARRAY['hs_m','hs_f','college_m','college_f'])[1 + i % 4],
       CASE WHEN i % 3 = 0 THEN 'TF' ELSE 'XC' END, 2010 + i % 16,
       CASE WHEN i % 11 = 0 THEN NULL ELSE (i * 37 % 1500) / 10.0 END, i % 9,
       CASE WHEN i % 17 = 0 THEN NULL ELSE 'S' || i % 50 END,
       CASE WHEN i % 19 = 0 THEN NULL ELSE 'School ' || (i * 13) % 900 END
FROM generate_series(1, {_ROWS}) i ORDER BY md5(i::text)
"""


def _q(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def _catalog(cur, t):
    oid = _q(cur, "SELECT %s::regclass::oid", (t,))[0][0]
    return (
        _q(cur, """SELECT attnum, attname, atttypid, atttypmod, attnotnull, atthasdef,
                          attcollation, attstorage, attcompression, attisdropped, attidentity,
                          attgenerated
                   FROM pg_attribute WHERE attrelid = %s AND attnum > 0 ORDER BY attnum""", (oid,)),
        _q(cur, "SELECT adnum, pg_get_expr(adbin, adrelid) FROM pg_attrdef "
                "WHERE adrelid = %s ORDER BY adnum", (oid,)),
        _q(cur, """SELECT conname, contype, pg_get_constraintdef(oid), convalidated,
                          conislocal, connoinherit
                   FROM pg_constraint WHERE conrelid = %s ORDER BY conname""", (oid,)),
        _q(cur, "SELECT reloptions, relpersistence, relkind, relam FROM pg_class "
                "WHERE oid = %s", (oid,)),
    )


def _rewrite(B, conn, monkeypatch, t, like, parallel, keys=None):
    monkeypatch.setattr(B, "_RELOG_PARALLEL", parallel)
    monkeypatch.setattr(B, "_SEASON_REWRITE_PARALLEL", parallel)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert B.orderedRewrite(conn, t, like, units_from=keys) is True
    return out.getvalue()


@pytest.mark.parametrize("like", ["ranking_results", "athlete_season"])
def test_the_parallel_sort_writes_the_same_table(dsn, monkeypatch, like):
    import psycopg2
    import rr_layout
    import build_ranking_results as B
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        for s in _FORCE:
            cur.execute(s)
        keys = None
        for t in ("t_old", "t_new"):
            cur.execute(f"DROP TABLE IF EXISTS {t}")
            cur.execute((_RR if like == "ranking_results" else _AS).format(t=t))
            cur.execute(f"ANALYZE {t}")
        if like == "athlete_season":
            # the unit keys as the build makes them: TEMP, one row a key
            keys = "tmp_keys"
            cur.execute(f"""CREATE TEMP TABLE {keys} AS
                SELECT DISTINCT school, state, ({B._restampPool('')}) AS pool,
                       {", ".join(f"'{u}-' || md5(coalesce(school, '') || coalesce(state, '')) AS \"{u}\""
                                  for u in _UNITS)}
                FROM t_old""")
            cur.execute(f"ANALYZE {keys}")
        before = _catalog(cur, "t_old")
    conn.commit()
    # the real board order (it ends in a unique key on these rows)
    monkeypatch.setitem(rr_layout.BOARD_ORDER, "t", rr_layout.BOARD_ORDER[like])
    log_old = _rewrite(B, conn, monkeypatch, "t_old", "t", False, keys)
    log_new = _rewrite(B, conn, monkeypatch, "t_new", "t", True, keys)
    assert "parallel sort," not in log_old
    assert "parallel sort, 3 workers" in log_new, log_new
    with conn.cursor() as cur:
        assert _q(cur, "SELECT to_regclass('t_new_psort'), to_regclass('t_new_psort_keys')") \
            == [(None, None)]
        old, new = _catalog(cur, "t_old"), _catalog(cur, "t_new")
        assert old == new
        assert old[0] == before[0] and old[2] == before[2]
        assert new[3][0][1] == "p"                                  # LOGGED
        assert _q(cur, "SELECT count(*) FROM t_new")[0][0] == _ROWS
        # every (ctid, row): same rows on the same pages at the same offsets
        diff = _q(cur, """SELECT count(*) FROM (
            (SELECT ctid::text, x::text FROM t_old x EXCEPT ALL SELECT ctid::text, y::text FROM t_new y)
            UNION ALL
            (SELECT ctid::text, y::text FROM t_new y EXCEPT ALL SELECT ctid::text, x::text FROM t_old x)) q""")
        assert diff == [(0,)]
        if keys:
            assert _q(cur, "SELECT count(*) FROM t_new WHERE division IS NULL "
                           "AND school IS NOT NULL") == [(0,)]
        cur.execute("DROP TABLE t_old, t_new")
    conn.commit()
    conn.close()
