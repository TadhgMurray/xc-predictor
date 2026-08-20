"""
anchor_check.py -- did the row get normalised on the pool it is rated in?

    python engine/anchor_check.py                 # audit the corpus
    python engine/anchor_check.py --person 29346285
    python engine/anchor_check.py --sport TF --limit 40

Run from the PROJECT ROOT. Reads only; nothing here writes.

★ THE RULE, WHICH IS JUST "BOTH STAGES USED THE SAME POOL".

    normalized_time = normalizeTime(time, distance, POOL_AT_BACKFILL)
    speed_rating    = pool_mean(POOL_AT_SOLVE) / normalized_time * 100

  Those two pools are decided at different times, by different code, from
  facts that can change in between -- a grade_fix verdict, a season_level
  verdict, a school that got a level. Nothing has ever checked they agree.

⚠ AND THEY ARE ON DIFFERENT SCALES WHEN THEY DISAGREE, because the anchor is
  per pool: normalize_distance.targetFor reads pool_targets from the spline
  artifact, and ms spans (800, 3200) so it anchors at 3200 while hs anchors
  at 5000. A 3200m-anchored ability divided by a 5000m-anchored pool mean is
  inflated by about 1.64x for free.

  Measured on person 29346285, an eighth grader with no recorded grade:

      800m  2:10.53   norm_factor 4.7154 -> exponent 1.119 at a 3200m anchor
      1500m 5:04.42   norm_factor 2.3142 -> exponent 1.107 at a 3200m anchor

  Both land inside the fitted 1.06-1.22 for track at 3200m, and at 5000m
  they give 0.85 and 0.70 -- exponents under 1, which would mean the longer
  race is proportionally easier. So the rows were normalised as ms. The
  athlete-season is hs_m, mean 1236.4s, and he rates 187. On one anchor he
  rates about 112.

★ THE TEST IS A RECOMPUTATION, NOT AN INFERENCE. Given the row's own time
  and distance, normalizeTime is deterministic -- so run it with the pool
  the row is RATED in and compare. Agreement means both stages used the same
  scale, whatever that scale was. No exponent has to be recovered and no
  anchor has to be guessed.

⚠ THE TOLERANCE IS NOT ZERO, and not because of floating point. The stored
  value may predate a refit of the spline, in which case every row in the
  corpus differs slightly and none of them is a pool mismatch. A pool
  mismatch is not slight: hs against ms is 64%. Anything under
  TOLERANCE is a stale artifact, not a seam.
"""

import sys
import argparse

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from normalize_distance import normalizeTime, targetFor

# A refit moves every row a little; a pool mismatch moves one row a lot. The
# smallest real mismatch in this corpus is ms(3200) against hs(5000), which
# is 39% at the smallest exponent in the fitted range -- so 10% separates the
# two cases with an order of magnitude to spare.
TOLERANCE = 0.10


def mismatch(time_seconds, distance, stored_nt, pool, sport=None):
    """(is_mismatch, expected_nt, ratio). Pure -- no database, no globals.

    Returns (False, None, None) when the row cannot be checked at all: no
    time, no distance, no stored value. An unanswerable question is not a
    finding, and treating it as one would condemn every row the backfill
    has not reached yet.
    """
    try:
        t = float(time_seconds)
        d = float(distance)
        nt = float(stored_nt)
    except (TypeError, ValueError):
        return False, None, None
    if t <= 0 or d <= 0 or nt <= 0:
        return False, None, None

    expected = normalizeTime(t, d, pool, sport)
    if not expected or expected <= 0:
        return False, None, None
    ratio = nt / expected
    return abs(ratio - 1.0) > TOLERANCE, expected, ratio


def whichPool(time_seconds, distance, stored_nt, pools, sport=None):
    """Which of `pools` the row was ACTUALLY normalised on, if any.

    ! FOR THE REPORT, NOT FOR THE GATE. Naming the pool a row came from turns
      "this row is wrong" into "this row was normalised as ms and rated as
      hs", which is the difference between a count and a cause.
    """
    best, best_off = None, None
    for p in pools:
        _, expected, ratio = mismatch(time_seconds, distance, stored_nt, p,
                                      sport)
        if ratio is None:
            continue
        off = abs(ratio - 1.0)
        if best_off is None or off < best_off:
            best, best_off = p, off
    return best, best_off


# ------------------------------------------------------------------ #
#  THE AUDIT
# ------------------------------------------------------------------ #

_SQL = """
    SELECT k.pool, k.sport, r.result_id, r.person_id,
           r.time_seconds, {dist} AS distance, r.normalized_time,
           r.speed_rating
    FROM   {table} r
    JOIN   ranking_results k ON k.result_id = r.result_id
                            AND k.sport = %(sport)s
    WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
      AND  r.time_seconds > 0
      {person}
    LIMIT  %(scan)s
"""

_POOLS = ("elem_m", "elem_f", "ms_m", "ms_f", "hs_m", "hs_f",
          "college_m", "college_f")


def main():
    ap = argparse.ArgumentParser(
        description="Check that each row was normalised on the pool it is "
                    "rated in.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="TF")
    ap.add_argument("--person", type=int)
    ap.add_argument("--scan", type=int, default=400_000)
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    print("\nANCHOR PER POOL (normalize_distance.targetFor)")
    for p in _POOLS:
        print(f"    {p:<12}{targetFor(p, args.sport):>8.0f} m")

    table = "results_tf" if args.sport == "TF" else "results"
    dist = "k.distance" if args.sport == "TF" else "k.distance"

    from database import getConn
    import psycopg2.extras

    seen = 0
    bad = []
    by_pool = {}
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL.format(
                table=table, dist=dist,
                person=("AND r.person_id = %(person)s" if args.person else "")),
                {"sport": args.sport, "scan": args.scan,
                 **({"person": args.person} if args.person else {})})
            for r in cur.fetchall():
                is_bad, expected, ratio = mismatch(
                    r["time_seconds"], r["distance"], r["normalized_time"],
                    r["pool"], r["sport"])
                if ratio is None:
                    continue
                seen += 1
                slot = by_pool.setdefault(r["pool"], [0, 0])
                slot[0] += 1
                if is_bad:
                    slot[1] += 1
                    was, _ = whichPool(r["time_seconds"], r["distance"],
                                       r["normalized_time"], _POOLS, r["sport"])
                    bad.append((abs(ratio - 1.0), r, expected, ratio, was))

    if not seen:
        print("\nNo checkable rows. Does ranking_results carry a distance?")
        return

    print(f"\n\nROWS WHOSE STORED normalized_time DOES NOT MATCH THEIR POOL")
    print(f"  ({args.sport}, {seen:,} rows checked, tolerance "
          f"{TOLERANCE:.0%})\n")
    print(f"  {'pool':<12}{'checked':>10}{'mismatched':>12}{'%':>8}")
    print("  " + "-" * 42)
    for pool, (n, n_bad) in sorted(by_pool.items()):
        print(f"  {pool:<12}{n:>10,}{n_bad:>12,}{100.0 * n_bad / n:>7.2f}%")
    total_bad = sum(v[1] for v in by_pool.values())
    print(f"\n  {total_bad:,} of {seen:,} rows "
          f"({100.0 * total_bad / seen:.2f}%)")

    if not bad:
        print("\n  Nothing to nuke: every row was normalised on the pool it "
              "is rated in.")
        return

    bad.sort(reverse=True, key=lambda x: x[0])
    print(f"\n\nTHE WORST OF THEM")
    print("  `was` is the pool whose anchor actually reproduces the stored "
          "value.\n")
    print(f"  {'result_id':>12}{'person':>11}{'rated in':>11}{'was':>11}"
          f"{'stored nt':>11}{'expected':>10}{'off':>8}{'rating':>8}")
    print("  " + "-" * 82)
    for _off, r, expected, ratio, was in bad[:args.limit]:
        print(f"  {r['result_id']:>12}{r['person_id']:>11}{r['pool']:>11}"
              f"{str(was):>11}{r['normalized_time']:>11.1f}{expected:>10.1f}"
              f"{ratio - 1:>+7.1%}"
              f"{(r['speed_rating'] or 0):>8.1f}")

    print(f"\n  ⚠ These are RATED ON THE WRONG SCALE. Every one of them is "
          f"inflated\n    or deflated by the ratio above, and the athlete "
          f"did nothing unusual.")


if __name__ == "__main__":
    main()
