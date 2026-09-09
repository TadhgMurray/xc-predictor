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


_SELECT = """
    SELECT {pool_expr}                          AS pool,
           r.result_id, r.person_id, r.time_seconds,
           {dist}                               AS distance,
           {event}                              AS event_short,
           r.normalized_time
    FROM   {table} r
    LEFT   JOIN ranking_results k ON k.result_id = r.result_id
                                 AND k.sport = %(sport)s
    {joins}
    WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
      AND  r.time_seconds > 0
      AND  {pool_expr} IS NOT NULL
      {person}
    ORDER  BY r.result_id
    LIMIT  %(scan)s OFFSET %(off)s
"""

# ! ONE STATEMENT PER BATCH, KEYED BY result_id. A per-row UPDATE over a
#   corpus this size is hours; VALUES-joined it is seconds.
_UPDATE = """
    UPDATE {table} AS r
    SET    normalized_time = v.nt
    FROM  (VALUES %s) AS v(result_id, nt)
    WHERE  r.result_id = v.result_id
"""


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
    import psycopg2.extras

    table = "results_tf" if args.sport == "TF" else "results"
    is_tf = args.sport == "TF"
    dist = "NULL::float" if is_tf else "COALESCE(dov.distance, m.distance)"
    event = "r.event_short" if is_tf else "NULL::text"
    joins = "" if is_tf else ac._XC_JOINS

    moved = {}
    skipped = {}
    n_seen = 0
    biggest = []

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT 1 FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = %s
                             AND column_name = 'rating_pool'""", (table,))
            if not cur.fetchone():
                print(f"{table} has no rating_pool column -- run a pack "
                      f"first; there is no rated pool to move rows onto.")
                return
            sql = _SELECT.format(
                table=table, dist=dist, event=event, joins=joins,
                pool_expr="COALESCE(r.rating_pool, k.pool)",
                person="AND r.person_id = %(person)s" if args.person else "")

            off = 0
            while True:
                cur.execute(sql, {"sport": args.sport, "off": off,
                                  "scan": args.batch,
                                  **({"person": args.person}
                                     if args.person else {})})
                rows = cur.fetchall()
                if not rows:
                    break
                off += len(rows)
                n_seen += len(rows)
                writes = []
                for r in rows:
                    d = r["distance"]
                    if d is None and r["event_short"]:
                        got = distanceFromEventShort(r["event_short"])
                        d = got[0] if isinstance(got, (tuple, list)) else got
                    new, why = repairRow(r["time_seconds"], d,
                                         r["normalized_time"], r["pool"],
                                         args.sport, ac._POOLS)
                    if new is None:
                        skipped[why] = skipped.get(why, 0) + 1
                        continue
                    writes.append((r["result_id"], float(new)))
                    moved[r["pool"]] = moved.get(r["pool"], 0) + 1
                    biggest.append((abs(new / r["normalized_time"] - 1.0), r,
                                    new, why))

                if writes and args.apply:
                    with conn.cursor() as wcur:
                        psycopg2.extras.execute_values(
                            wcur, _UPDATE.format(table=table), writes,
                            page_size=10_000)
                    conn.commit()
                print(f"  {n_seen:,} scanned, {sum(moved.values()):,} to move"
                      f"{' (written)' if args.apply else ''}", flush=True)
                if args.scan and n_seen >= args.scan:
                    break
                if len(rows) < args.batch:
                    break

        if not args.apply:
            conn.rollback()

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
        biggest.sort(reverse=True, key=lambda x: x[0])
        print(f"\n  the biggest moves:")
        print(f"  {'result_id':>21}{'person':>10}{'rated in':>11}  "
              f"{'was on':<13}{'stored':>10}{'->':>12}")
        for _o, r, new, was in biggest[:15]:
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
