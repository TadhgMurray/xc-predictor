#!/usr/bin/env python3
"""
anchor_repair.py -- put each row back on the scale of the pool it is RATED in.

    python engine/anchor_repair.py --sport TF              # dry run, counts only
    python engine/anchor_repair.py --sport TF --apply      # rewrite
    python engine/anchor_repair.py --sport XC --apply
    python engine/anchor_repair.py --sport TF --person 23965611 --apply

Run from the PROJECT ROOT. anchor_check.py finds them; this fixes them.

★ THE SEAM, IN ONE SENTENCE: the backfill picks the pool that decides the
  SCALE, the solve picks the pool that decides the ANCHOR, and for an athlete
  who raced across levels in one season those two disagree.

  backfill_normalize resolves a season's level from season_level, which only
  speaks when the verdict is UNANIMOUS. An athlete who ran mostly non-HS
  races and a few HS ones has no unanimous verdict, so the backfill falls
  back to poolFor(grade=12) -> hs_m and writes normalized_time at the 5000m
  anchor. The engine's own resolver takes a majority and calls the season
  college_m, whose pool mean is on the 8000m anchor. The rating is then

      100 x pool_mean(8000m scale) / normalized_time(5000m scale)

  and comes out about 1.61x too big, for free. Measured on the 2023 HS mile
  final: six seniors at 141-144 and the two Youngs at 230-232, on the same
  times in the same race.

★ WHY (a), RE-NORMALISE ON THE RATED POOL, AND NOT (b), PIN THE BACKFILL'S.
  Owner's call, 2026-09-09. The rated pool is the one whose mean DEFINES
  100 and the one the athlete is ranked in, so the scale has to follow it.
  Pinning the backfill's pool freezes a scale that goes stale the moment a
  season legitimately changes level, and leaves the same mismatch pinned
  instead of drifting.

⚠ IT RESCALES, IT DOES NOT RECOMPUTE, AND THAT IS THE WHOLE CARE OF THIS
  FILE. anchor_check's `expected` is a bare distance factor -- normalizeTime
  with no season, no weather, no track geometry, no course. The stored value
  has all of those baked in. Writing `expected` back would fix the anchor
  and silently strip every correction the pipeline spent hours computing.

  So the pool-scale component is divided out and the right one multiplied
  in:

      new = stored x factor(d, rated_pool) / factor(d, pool_it_was_on)

  Everything that is not the pool factor survives untouched, exactly.

! AND IT ONLY TOUCHES ROWS WHOSE ORIGINAL SCALE IS IDENTIFIED. If no pool
  reproduces the stored value to within IDENTIFY_TOL, the stored number did
  not come from any pool scale this code knows and rescaling it would be a
  guess dressed as a repair. Those are counted and left alone.

⚠ IT TAKES A RUN TO SHOW UP. The rating is computed FROM normalized_time
  during the solve, so repairing the column fixes the NEXT engine run, not
  the ratings sitting in the table now. Run it after the go-live that wrote
  rating_pool, then re-run the engine.
"""

import argparse
import heapq
import math
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

import anchor_check as ac                                       # noqa: E402
from anchor_check import _FACTOR, _expected, mismatch, whichPool  # noqa: E402,F401
from event_parse import distanceFromEventShort                  # noqa: E402

# How close a candidate pool must reproduce the stored value before this will
# call it the scale the row was written on. anchor_check's TOLERANCE (10%)
# decides "is this wrong"; identifying WHICH scale it came from has to be much
# tighter, because the repair divides by that pool's factor. The gaps between
# real pool scales are 30-60%, so 3% names one without ambiguity and abstains
# rather than picking a neighbour.
IDENTIFY_TOL = 0.03


def rescale(stored_nt, distance, from_pool, to_pool, sport):
    """The stored value moved from one pool's scale to another's, or None.

    Pure: no database, no globals beyond normalize_distance's own factor
    cache. Every correction inside `stored_nt` other than the pool factor is
    preserved by construction, because only the ratio of the two factors is
    applied.
    """
    f_from = _expected(1.0, distance, from_pool, sport)
    f_to = _expected(1.0, distance, to_pool, sport)
    if not f_from or not f_to or f_from <= 0:
        return None
    return stored_nt * f_to / f_from


def repairRow(time_seconds, distance, stored_nt, rated_pool, sport, pools):
    """(new_nt, was) for a row that needs moving, else (None, reason)."""
    is_bad, _expect, _ratio = mismatch(time_seconds, distance, stored_nt,
                                       rated_pool, sport)
    if _ratio is None:
        return None, "uncheckable"
    if not is_bad:
        return None, "already right"
    was, off = whichPool(time_seconds, distance, stored_nt, pools, sport)
    if was is None or off is None or off > IDENTIFY_TOL:
        # ⚠ NOT IDENTIFIED IS NOT NOTHING. These rows ARE wrong -- they
        #   failed the check -- but nothing here knows what scale they are
        #   on, so there is no arithmetic that returns them to a known one.
        return None, "scale not identified"
    from_pool = was.split("|")[0]
    from_sport = was.split("|")[1]
    if from_sport == "none":
        from_sport = None
    new = rescale(stored_nt, distance, from_pool, rated_pool, sport)
    if new is None or new <= 0:
        return None, "no factor"
    return new, was


def rowDistance(distance, event_short):
    """The distance the check reads: the column, else the event name's."""
    d = distance
    if d is None and event_short:
        # ! (distance, gender) -- only the first is wanted here. Cached in
        #   event_parse, so a name costs one parse however many rows wear it.
        got = distanceFromEventShort(event_short)
        d = got[0] if isinstance(got, (tuple, list)) else got
    return d


# ⚠⚠ A PYTHON LOOP OVER EVERY ROW, AND ALMOST EVERY ROW IS RIGHT (2026-09-26).
#    The walk was keyset batches of 200,000 -- ~300 of them on TF -- each
#    fetched into RealDictCursor dicts and run through repairRow one row at a
#    time. ~21 min per sport on the owner's server, to find that nearly all
#    of 61M rows were already on their pool's scale and write a handful.
#
# ★ SO THE QUESTION "IS THIS ROW MORE THAN TOLERANCE OFF" GOES TO SQL, and
#   Python sees only the rows that could move. The expected factor depends on
#   the row only through (distance, rated pool, sport), so it is computed in
#   Python once per DISTINCT (distance key, pool) -- pass 1 -- and loaded
#   beside those keys in a temp table; pass 2 joins it and streams back the
#   rows outside the band, in result_id order.
#
# ⚠ THE SQL TEST IS A SUPERSET, AND repairRow STILL DECIDES. Two reasons the
#   SQL arithmetic cannot simply BE the check:
#
#     * the columns are REAL. Postgres widens the float4 exactly; Python gets
#       the shortest decimal that names it and parses THAT as a double. They
#       differ by up to ~6e-8 (5e-6 under extra_float_digits=0), so a row
#       sitting on the 10% line could fall on different sides.
#     * a metre holds more than one factor (see anchor_check._factorOf):
#       1609 and 1609.344 share a _FACTOR slot 2.4e-4 apart, and the one
#       that reaches it first is the one the row is judged with.
#
#   So a row is left out only when it is inside TOLERANCE - _SQL_SLACK for
#   EVERY factor its metre holds (lo..hi). Anything nearer the line, and
#   every row of a group whose factors are not all usable, goes to Python
#   and through the unchanged repairRow. _SQL_SLACK is 1e-4 against an error
#   of ~1e-5 at worst: a row SQL calls right is one repairRow calls right.
#
# ! AND THE FIRST-TO-THE-SLOT ORDER IS REPLAYED, NOT ASSUMED. In the old walk
#   the first row of each (distance, pool) in result_id order filled the
#   slot; pass 1 records that row's result_id (first_id) for every group, and
#   the candidate loop calls _expected for each group before the first
#   candidate after it. Rows SQL leaves out are not bad, so filling their
#   pool's slot was all they ever did to the cache -- the same calls, in the
#   same order, as the row-by-row walk made.
#
# ⚠ THE KEYSET NOTE (2026-09-22) STILL HOLDS: nothing here pages a table
#   with OFFSET. Each pass reads it once; --scan finds its cut-off row with
#   one ordered read of the rows before it.
_SQL_SLACK = 1e-4

_FROM = """
    FROM   {table} r
    LEFT   JOIN ranking_results k ON k.result_id = r.result_id
                                 AND k.sport = %(sport)s
    {joins}
    {group_join}
    WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
      AND  r.time_seconds > 0
      AND  {pool_expr} IS NOT NULL
      {upto}
      {person}
"""

# ! A SURROGATE gid, SO THE FACTORS GO BACK BY ID. The key is REAL on XC; a
#   float sent to Python and back is a round trip this does not need to
#   trust. Pass 2 joins on the key server-side, same expression, same type.
_GROUPS = """
    CREATE TEMP TABLE ar_group AS
    SELECT row_number() OVER ()                 AS gid, g.*,
           NULL::text AS cls, NULL::float8 AS lo, NULL::float8 AS hi
    FROM  (SELECT {key}                         AS key,
                  {pool_expr}                   AS pool,
                  count(*)                      AS n,
                  min(r.result_id)              AS first_id
           {src}
           GROUP  BY 1, 2) g
"""

_CANDIDATES = """
    SELECT {pool_expr}                          AS pool,
           r.result_id, r.person_id, r.time_seconds,
           {dist}                               AS distance,
           {event}                              AS event_short,
           r.normalized_time
    {src}
      AND (g.cls = 'python'
           OR (g.cls = 'sql'
               AND NOT (r.normalized_time / (r.time_seconds * g.hi)
                            >= %(lo_ok)s
                        AND r.normalized_time / (r.time_seconds * g.lo)
                            <= %(hi_ok)s)))
    ORDER  BY r.result_id
"""

# ! ONE STATEMENT PER BATCH, KEYED BY result_id. A per-row UPDATE over a
#   corpus this size is hours; VALUES-joined it is seconds.
_UPDATE = """
    UPDATE {table} AS r
    SET    normalized_time = v.nt
    FROM  (VALUES %s) AS v(result_id, nt)
    WHERE  r.result_id = v.result_id
"""


def _usable(d):
    """mismatch()'s own gate on the distance: a float, above zero."""
    try:
        return float(d) > 0
    except (TypeError, ValueError):
        return False


def classifyGroups(groups, sport, is_tf):
    """({gid: (cls, lo, hi)}, {gid: float distance or None}). Pure.

    'uncheckable'  every row is: no usable distance, or no factor at all
    'sql'          every factor the metre holds is finite and positive, so
                   SQL may clear the rows inside the band
    'python'       anything else -- every row goes to repairRow
    """
    dist, variants = {}, {}
    for gid, key, _pool, _n, _first in groups:
        d = rowDistance(None, key) if is_tf else rowDistance(key, None)
        dist[gid] = float(d) if _usable(d) else None
        if dist[gid] is not None:
            variants.setdefault(round(dist[gid]), set()).add(dist[gid])
    memo, out = {}, {}
    for gid, _key, pool, _n, _first in groups:
        d = dist[gid]
        if d is None:
            out[gid] = ("uncheckable", None, None)
            continue
        fs = []
        for v in sorted(variants[round(d)]):
            if (v, pool) not in memo:
                memo[(v, pool)] = ac._factorOf(v, pool, sport)
            fs.append(memo[(v, pool)])
        if all(f is None for f in fs):
            out[gid] = ("uncheckable", None, None)
        elif all(f is not None and math.isfinite(f) and f > 0 for f in fs):
            out[gid] = ("sql", min(fs), max(fs))
        else:
            out[gid] = ("python", None, None)
    return out, dist


def walk(conn, wconn, sport, person=None, batch=200_000, scan=0):
    """Repair one sport. Writes through `wconn` when it is given (--apply),
    committing every `batch` rows. Returns (n_seen, moved, skipped,
    biggest) for the report, or None when there is no rating_pool."""
    import psycopg2.extras

    table = "results_tf" if sport == "TF" else "results"
    is_tf = sport == "TF"
    fmt = dict(table=table,
               joins="" if is_tf else ac._XC_JOINS,
               pool_expr="COALESCE(r.rating_pool, k.pool)",
               person="AND r.person_id = %(person)s" if person else "",
               upto="", group_join="")
    key = "r.event_short" if is_tf else "COALESCE(dov.distance, m.distance)"
    params = {"sport": sport, **({"person": person} if person else {})}

    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = %s
                         AND column_name = 'rating_pool'""", (table,))
        if not cur.fetchone():
            return None

        if scan:
            # ! THE SAME ROWS --scan ALWAYS READ: whole batches, in
            #   result_id order, until at least N had been seen.
            cur.execute("SELECT r.result_id" + _FROM.format(**fmt)
                        + "ORDER BY r.result_id OFFSET %(off)s LIMIT 1",
                        {**params, "off": -(-scan // batch) * batch - 1})
            got = cur.fetchone()
            if got:
                fmt["upto"] = "AND r.result_id <= %(upto)s"
                params["upto"] = got[0]

        # ---- pass 1: every (distance key, rated pool), counted ---------- #
        cur.execute("DROP TABLE IF EXISTS ar_group")
        cur.execute(_GROUPS.format(key=key, pool_expr=fmt["pool_expr"],
                                   src=_FROM.format(**fmt)), params)
        cur.execute("SELECT gid, key, pool, n, first_id FROM ar_group")
        groups = cur.fetchall()
        cls, dist = classifyGroups(groups, sport, is_tf)
        if groups:
            psycopg2.extras.execute_values(
                cur, """UPDATE ar_group g SET cls = v.cls, lo = v.lo,
                                              hi = v.hi
                        FROM (VALUES %s) AS v(gid, cls, lo, hi)
                        WHERE g.gid = v.gid""",
                [(gid, *cls[gid]) for gid, *_ in groups],
                template="(%s, %s, %s::float8, %s::float8)", page_size=10_000)
        cur.execute("ANALYZE ar_group")

    n_seen = sum(n for _g, _k, _p, n, _f in groups)
    n_unchecked = sum(n for gid, _k, _p, n, _f in groups
                      if cls[gid][0] == "uncheckable")
    by_cls = {}
    for gid, *_ in groups:
        by_cls[cls[gid][0]] = by_cls.get(cls[gid][0], 0) + 1
    print(f"  {n_seen:,} rows in {len(groups):,} (distance, pool) groups: "
          + ", ".join(f"{v:,} {k}" for k, v in sorted(by_cls.items())),
          flush=True)

    # the slot-filling calls the left-out rows made, in result_id order
    events = sorted((first, dist[gid], pool)
                    for gid, _k, pool, _n, first in groups
                    if dist[gid] is not None)

    moved, skipped = {}, {}
    top, seq, n_cand, n_written = [], 0, 0, 0
    writes = []

    def flush():
        nonlocal n_written
        if writes and wconn is not None:
            with wconn.cursor() as wcur:
                psycopg2.extras.execute_values(
                    wcur, _UPDATE.format(table=table), writes,
                    page_size=10_000)
            wconn.commit()
            n_written += len(writes)
        writes.clear()

    # ---- pass 2: only the rows that could move, streamed ---------------- #
    fmt["group_join"] = ("JOIN   ar_group g ON g.key = " + key
                         + " AND g.pool = " + fmt["pool_expr"])
    ei = 0
    with conn.cursor(name="anchor_repair_candidates") as cur:
        cur.itersize = 10_000
        cur.execute(_CANDIDATES.format(
            pool_expr=fmt["pool_expr"], src=_FROM.format(**fmt),
            dist="NULL::float" if is_tf else key,
            event="r.event_short" if is_tf else "NULL::text"),
            {**params, "lo_ok": 1.0 - ac.TOLERANCE + _SQL_SLACK,
             "hi_ok": 1.0 + ac.TOLERANCE - _SQL_SLACK})
        for pool, result_id, person_id, t, distance, ev, nt in cur:
            while ei < len(events) and events[ei][0] <= result_id:
                _first, d, p = events[ei]
                _expected(1.0, d, p, sport)
                ei += 1
            n_cand += 1
            new, why = repairRow(t, rowDistance(distance, ev), nt, pool,
                                 sport, ac._POOLS)
            if new is None:
                skipped[why] = skipped.get(why, 0) + 1
                continue
            writes.append((result_id, float(new)))
            moved[pool] = moved.get(pool, 0) + 1
            # ! THE REPORT PRINTS FIFTEEN, SO FIFTEEN ARE KEPT. Ties go to
            #   the earlier row, as the old stable sort of every move did.
            seq += 1
            heapq.heappush(top, (abs(new / nt - 1.0), -seq,
                                 {"result_id": result_id,
                                  "person_id": person_id, "pool": pool,
                                  "normalized_time": nt}, new, why))
            if len(top) > 15:
                heapq.heappop(top)
            if len(writes) >= batch:
                flush()
    flush()

    n_right = n_seen - n_unchecked - n_cand
    for why, n in (("uncheckable", n_unchecked), ("already right", n_right)):
        if n:
            skipped[why] = skipped.get(why, 0) + n
    print(f"  {n_cand:,} rows outside the band went to repairRow, "
          f"{sum(moved.values()):,} to move"
          f"{f' ({n_written:,} written)' if wconn is not None else ''}",
          flush=True)
    biggest = [(o, r, new, was)
               for o, _s, r, new, was in sorted(top, reverse=True)]
    return n_seen, moved, skipped, biggest


def main():
    ap = argparse.ArgumentParser(
        description="Re-normalise rows onto the pool they are rated in.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="TF")
    ap.add_argument("--person", type=int)
    ap.add_argument("--apply", action="store_true",
                    help="write. Without it nothing is changed.")
    ap.add_argument("--batch", type=int, default=200_000)
    ap.add_argument("--scan", type=int, default=0, metavar="N",
                    help="stop after N rows (0 = the whole table)")
    args = ap.parse_args()

    from database import getConn

    table = "results_tf" if args.sport == "TF" else "results"
    kw = dict(person=args.person, batch=args.batch, scan=args.scan)
    with getConn() as conn:
        # ! THE WRITES ON THEIR OWN CONNECTION. The candidates come off a
        #   named cursor, which lives in the reading transaction; a commit
        #   there would close it mid-stream.
        if args.apply:
            with getConn() as wconn:
                got = walk(conn, wconn, args.sport, **kw)
        else:
            got = walk(conn, None, args.sport, **kw)
        # a dry run wrote nothing, and the temp table goes either way
        conn.rollback()

    if got is None:
        print(f"{table} has no rating_pool column -- run a pack "
              f"first; there is no rated pool to move rows onto.")
        return
    n_seen, moved, skipped, biggest = got

    total = sum(moved.values())
    print(f"\n{'WROTE' if args.apply else 'WOULD WRITE'} {total:,} rows "
          f"of {n_seen:,} scanned ({args.sport})")
    for pool, n in sorted(moved.items(), key=lambda kv: -kv[1]):
        print(f"    {pool:<12}{n:>10,}")
    if skipped:
        print("\n  left alone:")
        for why, n in sorted(skipped.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>12,}  {why}")

    if biggest:
        print(f"\n  the biggest moves:")
        print(f"  {'result_id':>21}{'person':>10}{'rated in':>11}  "
              f"{'was on':<13}{'stored':>10}{'->':>12}")
        for _o, r, new, was in biggest:
            print(f"  {r['result_id']:>21}{r['person_id']:>10}"
                  f"{r['pool']:>11}  {str(was):<13}"
                  f"{r['normalized_time']:>10.1f}{new:>12.1f}")

    if not args.apply:
        print("\n  DRY RUN -- nothing written. Pass --apply.")
    else:
        print("\n  ⚠ The ratings in the table are unchanged: they were "
              "computed FROM\n    normalized_time. Re-run the engine for "
              "this to reach them.")


if __name__ == "__main__":
    main()
