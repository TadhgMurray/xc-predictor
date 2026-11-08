#!/usr/bin/env python3
"""
build_board_seek.py -- where every default board's pages start: board_seek,
one row per SEEK_EVERY-th row of each board (its rating and unique id), and
one per board with its length.

    python racecast/build_board_seek.py                 # every pool, both boards
    python racecast/build_board_seek.py --workers 4

★ WHY (2026-11-08: /api/rankings 25.4 s at offset 150,850 of the hs_m XC
  5000 m performance board, 6.6 s at offset 44,750 of the 2012 ability board,
  a crawler walking them). OFFSET makes Postgres produce every row before a
  page, and the performance board first reads offset x CANDIDATE_FACTOR
  candidates. With a stored position a page is an index range read of the
  rows past it: rankings "DEEP PAGES" (_seekPoint) has the whole argument.

★ WHICH BOARDS: the default ones (rankings.seekFamily) -- performance and
  ability, every pool, the sport and both sports, every season and all of
  them, every distance of the performance board's list and none, the ability
  board's default floor ('d') and a typed minimum of three ('e3'), the US
  scope, the rating order. That is the board a reader pages through without
  filters, and the one the board's own links and the crawlers ask for.

★ THE SAME BOARD THE LIVE QUERY BUILDS. Each pool's rows are read with the
  live query's own WHERE (rankings._whereClauses for the pool at the default
  scope) and the live floors (season_floor.floorSql); each family's rows are
  the ones its filter keeps (its sport, its season, its distance band -- the
  band test is the live one, `distance BETWEEN lo AND hi` with the same
  bounds); the performance board's copies of one race are folded with the
  live dedup key (rankings.PERF_DUP_KEY) and order, within the family; and
  each family is numbered in the live order. tests/test_board_seek.py holds
  every page of the seek to the live board's.

★ NEVER STALE: COMMENT ON TABLE says which ranking_results and athlete_season
  the positions came from (their oids) and which code (rankings.seekSig); the
  boards seek only while all three are still live. A rebuild of either table
  swaps in a new oid, so between a go-live and this step every page runs live
  (as before this table existed), and nothing waits for it.

! SIZE: rows / SEEK_EVERY per family, each row in up to 8 families (sport or
  both, season or all, distance or none) -- on the live corpus about
  61M x 8 / 1000 = 0.5M rows plus one length row per family; tens of MB.
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

import rankings as R                                           # noqa: E402
from season_floor import floorSql, DEFAULT_FLOOR               # noqa: E402

TABLE = R.SEEK_TABLE
NEW = f"{TABLE}_new"
INDEXES = (
    # rankings._seekPoint: WHERE fam = ? AND pos = -1, and the last pos <= offset
    ("fam_pos", "UNIQUE", "(fam, pos)"),
)
WORK_MEM = os.environ.get("XCP_BOARD_SEEK_WORK_MEM", "512MB")


def _filters(board, pool, **extra):
    """The board's own filter dict for one pool at the default scope, as
    parseFilters makes it (so _whereClauses sees exactly a request's)."""
    from werkzeug.datastructures import MultiDict
    args = {"board": board, "pool": pool, "sport": "both"}
    args.update({k: str(v) for k, v in extra.items()})
    f, err = R.parseFilters(MultiDict(args))
    if err:
        raise ValueError(err)
    return f


def _bands(params):
    """The performance board's distance list as one CASE: each preset's
    band is the live test (`distance BETWEEN %(lo)s AND %(hi)s`, the bounds
    _whereClauses binds for that distance). The bands are disjoint -- checked
    here, since a row in two bands would belong to two boards and a CASE
    names one."""
    arms, spans = [], []
    for d in R.PR_DISTANCES:
        p = {}
        R._whereClauses(_filters("performance", "hs_m", distance=d), p, with_dates=True)
        lo, hi = p["dist_lo"], p["dist_hi"]
        params[f"band_lo_{d}"], params[f"band_hi_{d}"] = lo, hi
        arms.append(f"WHEN distance BETWEEN %(band_lo_{d})s AND %(band_hi_{d})s THEN {int(d)}")
        spans.append((lo, hi, d))
    spans.sort()
    for (lo1, hi1, d1), (lo2, _hi2, d2) in zip(spans, spans[1:]):
        if not hi1 < lo2:
            raise ValueError(f"distance bands {d1} and {d2} overlap")
    return "CASE " + " ".join(arms) + " END"


def _fid(spc, y, d, fl):
    """SQL: one bigint per family -- sport code (0 both, 1 XC, 2 TF), season
    (0: all), distance (0: all), floor (0 'd', 1 'e3'). A board numbers its
    rows in a sort on (fid, k, id); a text key there was most of the build's
    time (a locale-collated sort of millions of rows)."""
    return (f"(({spc})::bigint * 1000000000000 + COALESCE({y}, 0)::bigint * 10000000"
            f" + COALESCE({d}, 0)::bigint * 10 + ({fl}))")


def _famText(board):
    """SQL: board_seek.fam (rankings.seekFamilyKey) of a fid."""
    return (f"'{board}|' || %(pool)s || '|' || "
            f"(CASE fid / 1000000000000 WHEN 0 THEN 'both' WHEN 1 THEN 'XC' ELSE 'TF' END)"
            f" || '|' || (CASE WHEN (fid / 10000000) % 100000 = 0 THEN '*'"
            f" ELSE ((fid / 10000000) % 100000)::text END)"
            f" || '|' || (CASE WHEN (fid / 10) % 1000000 = 0 THEN '*'"
            f" ELSE ((fid / 10) % 1000000)::text END)"
            f" || '|' || " + ("'-'" if board == "performance" else
                              "(CASE fid % 10 WHEN 0 THEN 'd' ELSE 'e3' END)")).replace("%", "%%").replace("%%(pool)s", "%(pool)s")


def _positions(board, fam_rows, every, k1):
    """Numbered families -> the rows board_seek keeps: a length row per
    family (pos -1, k2 = rows) and every `every`-th row whose key starts its
    tie group (k1 = the order column as the board compares it, from `k1`;
    k2 = the id). `fam_rows` is a query of (fid, k, id), k ascending in
    board order; it is read twice (the lengths, then the numbering)."""
    fam = _famText(board)
    return f"""
        SELECT {fam} AS fam, -1 AS pos, NULL::float8 AS k1, count(*) AS k2
        FROM   ({fam_rows}) fr
        GROUP  BY fid
        UNION ALL
        SELECT {fam}, pos::int, ({k1})::float8, id
        FROM  (SELECT fid, k, id,
                      row_number() OVER w - 1 AS pos,
                      lag(k)  OVER w          AS pk,
                      lag(id) OVER w          AS pid
               FROM   ({fam_rows}) fr
               WINDOW w AS (PARTITION BY fid ORDER BY k, id)) numbered
        WHERE  pos > 0 AND pos %% {int(every)} = 0
          AND  (pk IS DISTINCT FROM k OR pid IS DISTINCT FROM id)"""


_SPC = "CASE sport WHEN 'XC' THEN 1 WHEN 'TF' THEN 2 ELSE 3 END"
assert R.SPORTS == {"XC", "TF", "both"}, "the sport codes follow rankings.SPORTS"

# PERF_DUP_KEY, the most selective column first: the dedup sort compares
# person_id and mostly stops there (a partition's columns are a set; the
# order only decides how fast the sort runs)
_DUP_SORTED = ("person_id", "COALESCE(canon_meet_id, -result_id)",
               "round(time_seconds::numeric, 1)", "sport")
assert sorted(_DUP_SORTED) == sorted(R.PERF_DUP_KEY), "the dedup key moved"


# the four ways a performance board pins its rows: (season, distance)
VARIANTS = ((False, False), (True, False), (False, True), (True, True))


def performanceSql(pool, every=None, variants=(0, 1, 2, 3)):
    """(sql, params): board_seek rows for the performance families of one
    pool. Four ways to pin a row (VARIANTS: season or not, distance or not;
    `variants` picks which, so one pool can be built by several workers); in
    each the copies of one race fold within the family (the live dedup), then
    each survivor is numbered twice: in its sport's family and in 'both'.

    k is the order as ascending: -rating (the board is rating DESC), or the
    clock on a sprint distance (below UNRATED_BELOW the board ranks time
    ASC); ties go to the lower result_id, as on the board."""
    every = every or R.SEEK_EVERY
    f = _filters("performance", pool)
    params = {}
    where = R._whereClauses(f, params, with_dates=True)
    band = _bands(params)
    rated = "CASE WHEN speed_rating IS NOT NULL THEN -(speed_rating::float8) END"
    banded = (f"CASE WHEN band < {int(R.UNRATED_BELOW)} "
              f"THEN CASE WHEN time_seconds > 0 THEN time_seconds::float8 END "
              f"ELSE kr END")
    # one SELECT per variant: its season and distance (NULL when not
    # pinned), its order key; a row with no season / distance to pin is in
    # no family of that variant
    arms = []
    for v in variants:
        pin_y, pin_d = VARIANTS[v]
        arms.append(f"""
            SELECT {{cols}}, {v} AS v, {"year" if pin_y else "NULL::int"} AS y,
                   {"band" if pin_d else "NULL::int"} AS d, {"kb" if pin_d else "kr"} AS k
            FROM   base
            WHERE  {"kb" if pin_d else "kr"} IS NOT NULL
                   {"AND year IS NOT NULL" if pin_y else ""}
                   {"AND band IS NOT NULL" if pin_d else ""}""")
    dup_cols = ", ".join(f"{e} AS dk{i}" for i, e in enumerate(_DUP_SORTED))
    dup = ", ".join(f"dk{i}" + (' COLLATE "C"' if e == "sport" else "")
                    for i, e in enumerate(_DUP_SORTED))
    cols = f"{', '.join(f'dk{i}' for i in range(len(_DUP_SORTED)))}, result_id, spc"
    pinned = "\n            UNION ALL".join(arms).replace("{cols}", cols)
    fam_rows = f"""
            SELECT {_fid('spc', 'y', 'd', '0')} AS fid, k, result_id AS id
            FROM   kept WHERE spc < 3
            UNION ALL
            SELECT {_fid('0', 'y', 'd', '0')}, k, result_id FROM kept"""
    sql = f"""
        WITH base AS MATERIALIZED (
            SELECT *, {banded} AS kb FROM (
                SELECT {dup_cols}, result_id, {_SPC} AS spc, year, time_seconds,
                       {band} AS band, {rated} AS kr
                FROM   ranking_results
                WHERE  (speed_rating IS NOT NULL OR time_seconds > 0) {where}
            ) b0
        ),
        pinned AS ({pinned}
        ),
        kept AS MATERIALIZED (
            SELECT result_id, spc, y, d, k FROM (
                SELECT result_id, spc, y, d, k,
                       row_number() OVER (PARTITION BY {dup}, v, y, d
                                          ORDER BY k, result_id) AS dn
                FROM   pinned
            ) q WHERE dn = 1
        )
        {_positions("performance", fam_rows, every,
                    f"CASE WHEN (fid / 10) % 1000000 BETWEEN 1 AND {int(R.UNRATED_BELOW) - 1}"
                    f" THEN k ELSE -k END".replace("%", "%%"))}"""
    return sql, params


def abilitySql(pool, every=None):
    """(sql, params): board_seek rows for every ability family of one pool:
    the sport or both, the season or all of them, the default floor ('d')
    or a typed minimum of DEFAULT_FLOOR ('e3'). k is -mean_rating (the board
    is mean_rating DESC NULLS LAST; the floor keeps unrated seasons out), ties
    to the lower person_id, as on the board."""
    every = every or R.SEEK_EVERY
    f = _filters("ability", pool)
    params = {"min_races": DEFAULT_FLOOR}
    where = R._whereClauses(f, params, with_dates=False)
    fam_rows = f"""
            SELECT {_fid('sp', 'y', 'NULL', 'fl')} AS fid, k, id
            FROM   base
            CROSS  JOIN LATERAL (VALUES (base.spc), (0)) a(sp)
            CROSS  JOIN LATERAL (VALUES (base.year, TRUE), (NULL::int, FALSE)) b(y, pin)
            CROSS  JOIN LATERAL (VALUES (0, base.ok_d), (1, base.ok_e)) c(fl, ok)
            -- a season family only for a row with a season
            WHERE  c.ok AND a.sp < 3 AND (b.y IS NOT NULL OR NOT b.pin)"""
    sql = f"""
        WITH base AS MATERIALIZED (
            SELECT s.person_id AS id, {_SPC} AS spc, s.year,
                   -(s.mean_rating::float8) AS k,
                   COALESCE(({floorSql(False)}), FALSE) AS ok_d,
                   COALESCE(({floorSql(True)}), FALSE)  AS ok_e
            FROM   athlete_season s
            WHERE  s.mean_rating IS NOT NULL {where}
        )
        {_positions("ability", fam_rows, every, "-k")}"""
    return sql, params


# the biggest pools first, so the last job to start is a small one
_POOL_ORDER = ("hs_m", "hs_f", "college_m", "college_f", "ms_m", "ms_f")
assert set(_POOL_ORDER) == R.POOLS, "every board pool is built"


def _jobs():
    """(board, pool, variant): each pool's performance boards one pinning
    variant at a time (four workers share hs_m), the ability boards a pool
    at a time (a fraction of the work)."""
    return ([("performance", p, v) for p in _POOL_ORDER for v in range(len(VARIANTS))]
            + [("ability", p, None) for p in _POOL_ORDER])


def jobSql(job, every=None):
    """(sql, params) for one job of _jobs(), ready for INSERT INTO."""
    board, pool, v = job
    sql, params = (performanceSql(pool, every, (v,)) if board == "performance"
                   else abilitySql(pool, every))
    params["pool"] = pool
    return sql, params


def build(workers=4, connect=None, every=None, log=print, swap=True):
    """Build board_seek_new from the live tables, stamp it, swap it in.
    `connect` is a context manager yielding a connection (getConn)."""
    if connect is None:
        from database import getConn as connect
    t0 = time.time()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("SELECT 'ranking_results'::regclass::oid, 'athlete_season'::regclass::oid")
            rr_oid, as_oid = (int(x) for x in cur.fetchone())
            cur.execute(f"DROP TABLE IF EXISTS {NEW}")
            cur.execute(f"""CREATE TABLE {NEW} (fam text NOT NULL, pos integer NOT NULL,
                                               k1 double precision, k2 bigint NOT NULL)""")
        conn.commit()

    def one(job):
        sql, params = jobSql(job, every)
        t = time.time()
        with connect() as c:
            with c.cursor() as cur:
                cur.execute("SET statement_timeout = 0")
                cur.execute(f"SET LOCAL work_mem = '{WORK_MEM}'")
                cur.execute(f"INSERT INTO {NEW} (fam, pos, k1, k2) {sql}", params)
                n = cur.rowcount
            c.commit()
        return job, n, time.time() - t

    total = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for (board, pool, v), n, dt in ex.map(one, _jobs()):
            total += n
            log(f"  [{dt:7.1f}s] {board} {pool}"
                + ("" if v is None else " (season, distance pinned: %s, %s)" % VARIANTS[v])
                + f": {n:,} rows")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            for name, kind, cols in INDEXES:
                cur.execute(f"CREATE {kind} INDEX idx_{NEW}_{name} ON {NEW} {cols}")
            cur.execute(f"ANALYZE {NEW}")
            cur.execute(f"COMMENT ON TABLE {NEW} IS %s", (R.seekComment(rr_oid, as_oid),))
            cur.execute("SELECT 'ranking_results'::regclass::oid, 'athlete_season'::regclass::oid")
            now = tuple(int(x) for x in cur.fetchone())
        conn.commit()
        if now != (rr_oid, as_oid):
            log("  ! ranking_results / athlete_season was swapped during the build: "
                "the table is kept, and the boards will not read it (they page "
                "live) until the next build")
        if swap:
            from dbfast import swapTable
            swapTable(conn, TABLE, renames=[(f"idx_{NEW}_{n}", f"idx_{TABLE}_{n}")
                                            for n, _k, _c in INDEXES])
    log(f"  {TABLE}: {total:,} rows [{time.time() - t0:.0f}s]")
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("XCP_BOARD_SEEK_WORKERS", "4")))
    a = ap.parse_args(argv)
    build(workers=a.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
