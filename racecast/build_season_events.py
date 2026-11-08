#!/usr/bin/env python3
"""
build_season_events.py -- the ability board's Events presets, precomputed:
athlete_season_ev, one row per (dist_min, person, pool, sport, year), and
athlete_season_ev_grade, one row per (dist_min, gkey, person, pool, sport,
year) -- the same seasons cut by the grade each race was run in.

    python racecast/build_season_events.py                # both tables
    python racecast/build_season_events.py --workers 8
    python racecast/build_season_events.py --only grade   # one of them

★ WHY (owner, 2026-11-06: "build it"). With an Events preset ("3000m and up")
  the ability board cannot read athlete_season, which has aggregated the
  events away: it recomputes every season of the pool from ranking_results
  (rankings._abilitySource) -- a sort and four ordered-set aggregates over
  hundreds of thousands of seasons per request. The slow log had it at
  26-55 s cold. The page offers four presets (rankings.html: 800, 1500,
  3000, 5000 and up), so the four answers are computed once per run here.

★ THE SAME SQL, SO THE SAME NUMBERS. Each preset is _abilitySource's own
  query -- not a copy of it -- run over every pool, sport and year with the
  board's default scope (US races; rankings._whereClauses' scope clause).
  The board's other filters on the precomputed path (pool, sport, year,
  gender) pick whole seasons, so a board read from this table is the board
  the live query computes; rankings._evPreset only sends a board here when
  that holds (no state, school, grade or unit filter, scope usa) and every
  other board still runs live.

★ NEVER STALE. The table is built from one ranking_results and says which
  (COMMENT ON TABLE: rr_oid=<its oid>); the board uses it only while that is
  still the live ranking_results (a rebuild swaps in a new table, a new oid),
  so between a go-live and this step the board runs live, as before.

! IN SHARDS, IN PARALLEL: each (preset, person shard) is one INSERT on its
  own connection, as build_ranking_results shards athlete_season.

★ ONE GRADE TOO (owner, 2026-11-08: the grade-10 Events board at 3-22 s).
  A grade filter picks RACES -- a grade-10 board aggregates only the races
  the row's grade key (rankings.GRADE_KEY_SQL) calls 10th grade -- so it
  cannot be a filter on athlete_season_ev. athlete_season_ev_grade is the
  same query with that key added to every window partition and the GROUP BY
  (rankings._abilitySource(by_grade=True)): the group with key k holds
  exactly the races the live query keeps for grade=k, and the same SQL runs
  over them. rankings._evGradePreset sends a board here for ONE grade key,
  on the same terms as the plain table; several grades run live. Built
  after athlete_season_ev (that one swaps in first), with the same rr_oid
  stamp. XCP_SEASON_EV_GRADE=0 skips it.
"""
import argparse
import concurrent.futures as cf
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "..", "scripts"), os.path.join(_HERE, "..", "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                   # noqa: E402
from dbfast import swapTable                                   # noqa: E402
import rankings as R                                           # noqa: E402

TABLE = R.EV_TABLE
NEW = f"{TABLE}_new"
GRADE_TABLE = R.EV_GRADE_TABLE
SHARDS = int(os.environ.get("XCP_SEASON_EV_SHARDS", "4"))
# the indexes the boards order by (rankings._SORTS_ABILITY's rating and best,
# with and without a year, with and without a pool/sport), and the rank line
INDEXES = (
    ("mean", "(dist_min, pool, sport, mean_rating DESC NULLS LAST, person_id)"),
    ("year_mean", "(dist_min, pool, sport, year, mean_rating DESC NULLS LAST, person_id)"),
    ("best", "(dist_min, pool, sport, best_rating DESC NULLS LAST, mean_rating DESC, person_id)"),
    ("pool_mean", "(dist_min, pool, mean_rating DESC NULLS LAST, person_id)"),
    ("person", "(person_id)"),
)
# the same orders, after the grade key: a board reads one (dist_min, gkey)
GRADE_INDEXES = tuple((n, cols if n == "person" else cols.replace("(dist_min,", "(dist_min, gkey,", 1))
                      for n, cols in INDEXES)


def presetSql(dist_min, shard=None, shards=SHARDS, by_grade=False):
    """(sql, params): _abilitySource's season aggregate for one preset over
    every pool, sport and year, the board's default scope (US races), one
    person shard when `shard` is given; by_grade, per race grade key too."""
    f = {"board": "ability", "pool": "all", "sport": "both", "scope": "usa",
         "dist_min": float(dist_min), "dist_max": None}
    params = {}
    where = R._whereClauses(f, params, with_dates=False)
    if shard is not None:
        where += f" AND mod(abs(person_id), {int(shards)}) = {int(shard)}"
    src = R._abilitySource(f, params, where, by_grade=by_grade)
    return f"SELECT {int(dist_min)}::int AS dist_min, s.* FROM {src}", params


def build(workers=4, presets=R.EV_PRESETS, shards=SHARDS, log=print,
          tables=("ev", "grade")):
    """Both tables by default (`tables`: "ev", "grade"); returns the rows."""
    total = 0
    if "ev" in tables:
        total += buildTable(TABLE, INDEXES, False, workers, presets, shards, log)
    if "grade" in tables:
        total += buildTable(GRADE_TABLE, GRADE_INDEXES, True, workers, presets, shards, log)
    return total


def buildTable(table, indexes, by_grade, workers=4, presets=R.EV_PRESETS,
               shards=SHARDS, log=print):
    """One table: prepare (an empty shadow), the (preset, shard) INSERTs in
    parallel, then indexes, the rr_oid stamp and the swap."""
    t0 = time.time()
    new = f"{table}_new"
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("SELECT 'ranking_results'::regclass::oid")
            rr_oid = int(cur.fetchone()[0])
            cur.execute(f"DROP TABLE IF EXISTS {new}")
            sql, params = presetSql(presets[0], by_grade=by_grade)
            cur.execute(f"CREATE TABLE {new} AS {sql} WITH NO DATA", params)
        conn.commit()

    def one(job):
        d, k = job
        sql, params = presetSql(d, k, shards, by_grade=by_grade)
        t = time.time()
        with getConn() as c:
            with c.cursor() as cur:
                cur.execute("SET statement_timeout = 0")
                cur.execute("SET LOCAL work_mem = '512MB'")
                cur.execute(f"INSERT INTO {new} {sql}", params)
                n = cur.rowcount
            c.commit()
        return d, k, n, time.time() - t

    jobs = [(d, k) for d in presets for k in range(shards)]
    total = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for d, k, n, dt in pool.map(one, jobs):
            total += n
            log(f"  [{dt:7.1f}s] {table} {d}m and up, shard {k + 1}/{shards}: "
                f"{n:,} seasons")

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            for name, cols in indexes:
                cur.execute(f"CREATE INDEX idx_{new}_{name} ON {new} {cols}")
            cur.execute(f"ANALYZE {new}")
            cur.execute(f"COMMENT ON TABLE {new} IS %s", (f"rr_oid={rr_oid}",))
            cur.execute("SELECT 'ranking_results'::regclass::oid")
            now_oid = int(cur.fetchone()[0])
        conn.commit()
        if now_oid != rr_oid:
            log("  ! ranking_results was swapped during the build: the table is "
                "kept, and the board will not read it (it runs live) until the "
                "next build")
        swapTable(conn, table, renames=[(f"idx_{new}_{n}", f"idx_{table}_{n}")
                                        for n, _c in indexes])
    log(f"  {table}: {total:,} seasons over {len(presets)} presets "
        f"[{time.time() - t0:.0f}s]")
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("XCP_SEASON_EV_WORKERS", "4")))
    ap.add_argument("--only", choices=("ev", "grade"),
                    help="build one table: ev (athlete_season_ev) or grade "
                         "(athlete_season_ev_grade)")
    a = ap.parse_args(argv)
    if a.only:
        tables = (a.only,)
    elif os.environ.get("XCP_SEASON_EV_GRADE", "1") == "0":
        tables = ("ev",)
    else:
        tables = ("ev", "grade")
    build(workers=a.workers, tables=tables)
    return 0


if __name__ == "__main__":
    sys.exit(main())
