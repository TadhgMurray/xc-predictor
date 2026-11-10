"""backfill/backfill_normalize._drainParallel (2026-11-10): several forked
workers, each every n-th meet, write the SAME staging rows and the SAME
census (counts, extremes, suspect divisions, pool split) as the one-process
_drainStream -- including rows with a NULL or negative meet_id, which the
shard filter must hand to exactly one worker. Needs a scratch Postgres:
XCP_TEST_PG_DSN="host=... port=... user=... dbname=..." (skipped without)."""
import os
import random
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))

if "corrections" not in sys.modules:            # 165 MB, not in git (test_backfill_wx_cache)
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: ({} if not name.startswith("__") else getattr(object(), name))
    sys.modules["corrections"] = _c

DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def _dsnDict():
    return dict(kv.split("=", 1) for kv in DSN.split())


@pytest.fixture
def bf(monkeypatch):
    import psycopg2
    import database
    BF = pytest.importorskip("backfill_normalize")
    cfg = dict(database._PG_CONFIG_WITH_TIMEOUT)
    cfg.update(_dsnDict())
    cfg.pop("password", None)
    monkeypatch.setattr(database, "_PG_CONFIG_WITH_TIMEOUT", cfg)
    database._pool = None
    database._slots = None
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS results; DROP TABLE IF EXISTS bf_staging_xc")
    cur.execute("""CREATE TABLE results (result_id bigint, source text, meet_id bigint,
                   div_id bigint, time_seconds double precision, grade int, athlete_id bigint,
                   date text, person_id bigint, canon_meet_id bigint, school text,
                   rating_pool text)""")
    rnd = random.Random(7)
    rows = []
    for i in range(1, 6001):
        rows.append((i, rnd.choice(["anet", "tfrrs"]),
                     rnd.choice([None, -3, -1] + list(range(0, 40))),
                     rnd.randint(1, 4), rnd.uniform(700, 1500), rnd.choice([9, 12, None]),
                     i, "2025-10-01", i % 900, None, "S",
                     rnd.choice([None, "hs_m", "hs_f", "college_m"])))
    cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    conn.commit()
    yield BF, conn
    database._pool = None
    database._slots = None
    conn.close()


def _rowFn(BF):
    pool_split = {}

    def fn(row):
        rid, meet, div, t = row[BF._ID], row[BF._MEET], row[BF._DIV], row[BF._TIME]
        pool = "hs_m" if (rid % 3) else "hs_f"
        trace = (rid, t, 5000.0, row[BF._SRC], row[BF._DATE], pool, meet, div)
        if rid % 7 == 0:
            return rid, None, BF._SkipReason.SENTINEL_TIME, trace
        if rid % 11 == 0:
            return rid, None, BF._SkipReason.INSANE_DISTANCE, trace
        BF._countSplit(pool_split, row, pool)
        return rid, round(t * 1.01, 3), BF._SkipReason.WRITTEN, trace
    fn.pool_split = pool_split
    return fn


def _staged(conn, table):
    cur = conn.cursor()
    cur.execute(f"SELECT result_id, nt FROM {table} ORDER BY result_id")
    out = cur.fetchall()
    conn.commit()                    # no read lock left for the next DROP
    return out


def _census(c):
    return (dict(c.counts), c.fastest.sorted_items(), c.slowest.sorted_items(),
            {k: (v["n"], v["min"], v["under"], v["corrupt"], v["sample"])
             for k, v in c.suspects.stats.items()},
            c.suspects.flagged(), dict(c.pool_split), c.reservoir._seen)


def test_workers_write_the_one_pass_rows_and_census(bf):
    BF, conn = bf
    import database
    cfg = BF._configFor("XC", "copy")
    cfg.batch = 500
    # the one pass
    with database.getConn() as rc, database.getConn() as wc:
        staging = BF._createStaging(wc, "XC")
        fn = _rowFn(BF)
        stream = BF._openStream(rc, cfg)
        one = BF._drainStream(stream, wc, cfg, fn, True, 0, staging)
        one[3].pool_split = fn.pool_split
        BF._closeStreamQuietly(stream)
        BF._releaseReadLocks(rc)
    want = _staged(conn, staging)
    # the workers
    with database.getConn() as wc:
        staging = BF._createStaging(wc, "XC")
    par = BF._drainParallel(cfg, _rowFn(BF), staging, 3)
    got = _staged(conn, staging)
    # the copy path stages only rows with a value (a skip comes out NULL
    # through the merge's LEFT JOIN): every written row exactly once
    assert len(want) == one[0] - one[2] > 4000
    assert got == want
    assert len({r[0] for r in got}) == len(got)
    assert par[:3] == one[:3]
    assert _census(par[3]) == _census(one[3])
    assert par[3].suspects.flagged()                     # the audit had something to say
    assert BF._SHARD["n"] == 1                           # the parent never sharded itself


def test_a_failed_worker_fails_the_drain(bf, monkeypatch):
    BF, conn = bf
    import database
    cfg = BF._configFor("XC", "copy")
    with database.getConn() as wc:
        staging = BF._createStaging(wc, "XC")

    def bad(row):
        if row[BF._MEET] == 5:
            raise ValueError("boom")
        return row[BF._ID], 1.0, BF._SkipReason.WRITTEN, None
    bad.pool_split = {}
    with pytest.raises(RuntimeError, match="worker"):
        BF._drainParallel(cfg, bad, staging, 3)
