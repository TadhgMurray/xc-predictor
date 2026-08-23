"""Does a race's ratings sort by time? Corpus-wide.

    python scripts/check_race_monotone.py                    # sample 5,000 XC races
    python scripts/check_race_monotone.py --sport TF
    python scripts/check_race_monotone.py --meet 271870 --div 1083175
    python scripts/check_race_monotone.py --races 50000 --since 2020-01-01
    python scripts/check_race_monotone.py --worst 40

Run from the PROJECT ROOT. READ ONLY -- nothing here writes.

★ THE INVARIANT, AND WHY IT IS THE ONE WORTH CHECKING.

      rating = 100 * pool_mean * exp(eff) / normalized_time

  Everything on the right except normalized_time is a property of the CELL and
  the POOL, not of the athlete. So inside one race -- one venue, one distance,
  one day -- two things must hold:

      1. rating falls as time rises. Always. No exceptions.
      2. rating * normalized_time is the SAME NUMBER for every row in the race
         that shares a pool.

  (2) is the sharper test. (1) can survive a small contamination by luck when
  the field is spread out; (2) cannot survive any of it.

⚠ THIS IS THE TEST THAT WOULD HAVE CAUGHT THE beta BUG IN ONE COMMAND.
  resultRatings used to add beta[athlete_season] * sc -- a per-ATHLETE sport
  offset -- into eff. On meet 271870 / div 1083175 that left 234 rows sharing
  one pool, one cell, and a normalized_time identical to the database's, with
  rating * norm spanning 4.53%. exp(+-0.045) is 4.6%. Five wrong hypotheses
  died before anyone thought to check the product.

⚠ AND A LEGITIMATE SPREAD EXISTS: a division holding more than one POOL. The
  pool mean differs between them, so rating * norm differs by exactly that
  ratio and it is not a bug. Those divisions are reported separately, counted
  under `mixed pool`, and kept out of the failure rate. The pool comes from
  ranking_results, which is the only table that stores it.

★ TF IS GRAINED ON THE EVENT, NOT THE DIVISION. The 800 and the 3200 at one
  meet are one division and two races. Grouping TF by (meet, div) alone would
  report every meeting as non-monotone and mean nothing.
"""
import os
import sys
import argparse
import collections

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from database import getConn                          # noqa: E402

# A race with fewer rows than this says nothing either way -- two runners are
# monotone half the time by chance.
MIN_FIELD = 8

# How far apart rating*norm may be inside one race before it is a finding.
# Float32 storage of speed_rating plus the two-decimal rounding of
# normalized_time is worth a few parts in 10^5; 0.1% is three orders of
# magnitude above that and three orders below the 4.5% beta left behind.
SPREAD_TOL = 0.001


def raceKey(sport):
    """The columns that identify ONE race. See the header on TF."""
    return ("meet_id", "div_id") if sport == "XC" else ("meet_id", "div_id",
                                                        "event_id")


def _sql(sport, one_race, races):
    table = "results" if sport == "XC" else "results_tf"
    keys = raceKey(sport)
    cols = ", ".join(f"r.{k}" for k in keys)
    grp = ", ".join(str(i + 1) for i in range(len(keys)))

    # ! THE FILTER IS THE SAME ONE THE ENGINE RATES UNDER. A row with no
    #   rating, no time or no normalized_time cannot take part in either test,
    #   and including it would make an unanswerable question look like a
    #   finding -- the mistake anchor_check's header warns about.
    where = f"""
        WHERE r.speed_rating IS NOT NULL
          AND r.time_seconds > 0
          AND r.normalized_time > 0
          AND r.date >= %(since)s
    """
    if sport == "TF":
        where += """
          AND COALESCE(r.is_relay, 0) = 0
          AND COALESCE(r.is_field, 0) = 0
        """
    if one_race:
        where += "".join(f"  AND r.{k} = %({k})s\n" for k in keys[:2])

    # Two statements: pick the races, then fetch their rows. One statement
    # would either scan the corpus or need a window function over it.
    pick = f"""
        SELECT {cols}, count(*) AS n
        FROM   {table} r
        {where}
        GROUP  BY {grp}
        HAVING count(*) >= %(min_field)s
        {"" if one_race else f"LIMIT {int(races)}"}
    """
    on = " AND ".join(f"r.{k} = s.{k}" for k in keys)
    rows = f"""
        SELECT {cols}, r.result_id, r.person_id, r.time_seconds,
               r.speed_rating, r.normalized_time, k.pool
        FROM   {table} r
        JOIN   _races s ON {on}
        LEFT JOIN ranking_results k
               ON k.result_id = r.result_id AND k.sport = %(sport)s
        {where}
        ORDER  BY {grp}, r.time_seconds, r.speed_rating DESC
    """
    return pick, rows


def inversions(rows):
    """Adjacent pairs, sorted by time, where the SLOWER runner rates higher.

    ! TIES ARE NOT INVERSIONS. Two runners on the same time must rate the
      same; only a strictly slower time that rates strictly higher counts.
    """
    bad = 0
    worst = 0.0
    for (t0, r0), (t1, r1) in zip(rows, rows[1:]):
        if t1 > t0 and r1 > r0:
            bad += 1
            worst = max(worst, r1 - r0)
    return bad, worst


def spread(products):
    lo, hi = min(products), max(products)
    return (hi - lo) / lo if lo > 0 else 0.0


def main():
    ap = argparse.ArgumentParser(
        description="Do ratings fall with time inside a race? Corpus-wide.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--since", default="1990-01-01")
    ap.add_argument("--races", type=int, default=5_000,
                    help="how many races to check (default 5,000). NOT a "
                         "random sample -- it is whatever the GROUP BY emits "
                         "first, which is cheap; raise it rather than trusting "
                         "a small one.")
    ap.add_argument("--min-field", type=int, default=MIN_FIELD, dest="min_field")
    ap.add_argument("--tol", type=float, default=SPREAD_TOL,
                    help=f"rating*norm spread that counts as a finding "
                         f"(default {SPREAD_TOL:.4f})")
    ap.add_argument("--meet", type=int)
    ap.add_argument("--div", type=int)
    ap.add_argument("--worst", type=int, default=20,
                    help="how many offending races to name")
    args = ap.parse_args()

    one = args.meet is not None and args.div is not None
    keys = raceKey(args.sport)
    pick, fetch = _sql(args.sport, one, args.races)
    params = {"since": args.since, "min_field": args.min_field,
              "sport": args.sport}
    if one:
        params.update({"meet_id": args.meet, "div_id": args.div})

    print(f"\nWITHIN-RACE RATING ORDER -- {args.sport}, "
          f"{'one race' if one else f'{args.races:,} races'}, "
          f"fields of {args.min_field}+, since {args.since}")

    races = collections.OrderedDict()
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE _races ON COMMIT DROP AS " + pick,
                        params)
            cur.execute("SELECT count(*) FROM _races")
            n_races = cur.fetchone()[0]
            if not n_races:
                print("\n  No race matched. Check --meet/--div, --since, "
                      "or lower --min-field.")
                return 0
            cur.execute(fetch, params)
            nk = len(keys)
            for row in cur:
                races.setdefault(row[:nk], []).append(row[nk:])

    n_bad_order = n_bad_spread = n_mixed = 0
    offenders = []
    for key, rows in races.items():
        pools = {r[5] for r in rows if r[5]}
        if len(pools) > 1:
            n_mixed += 1
            continue
        pairs = [(float(r[2]), float(r[3])) for r in rows]
        n_inv, worst_gap = inversions(pairs)
        sp = spread([float(r[3]) * float(r[4]) for r in rows])
        if n_inv:
            n_bad_order += 1
        if sp > args.tol:
            n_bad_spread += 1
        if n_inv or sp > args.tol:
            offenders.append((sp, n_inv, worst_gap, len(rows), key))

    checked = len(races) - n_mixed
    if not checked:
        print(f"\n  Every one of the {len(races):,} races held more than one "
              f"pool; nothing comparable.")
        return 0

    print(f"\n  {'races checked':<34}{checked:>12,}")
    print(f"  {'mixed pool (not comparable)':<34}{n_mixed:>12,}")
    print(f"  {'with a slower runner rating higher':<34}{n_bad_order:>12,}"
          f"  {100.0 * n_bad_order / checked:>7.2f}%")
    print(f"  {f'with rating*norm spread > {args.tol:.2%}':<34}"
          f"{n_bad_spread:>12,}  {100.0 * n_bad_spread / checked:>7.2f}%")

    if not offenders:
        print(f"\n  CLEAN. Every race sorts by time, and rating * "
              f"normalized_time is one number inside each of them --\n"
              f"  which is what it means for the effective difficulty to be a "
              f"property of the course rather than\n  of the athlete.")
        return 0

    offenders.sort(reverse=True)
    print(f"\n\n  THE WORST OF THEM  (spread is max/min of rating * "
          f"normalized_time inside the race)\n")
    head = "".join(f"{k:>12}" for k in keys)
    print(f"  {head}{'field':>8}{'inversions':>12}{'worst':>8}{'spread':>10}")
    print("  " + "-" * (12 * len(keys) + 38))
    for sp, n_inv, worst_gap, n, key in offenders[:args.worst]:
        print("  " + "".join(f"{k!s:>12}" for k in key)
              + f"{n:>8}{n_inv:>12}{worst_gap:>8.1f}{sp:>9.2%}")

    print(f"\n  ⚠ A spread here means something ATHLETE-SPECIFIC is in the "
          f"effective difficulty.\n"
          f"    That is what beta was -- see resultRatings in "
          f"engine/linkage_check.py, and\n"
          f"    scripts/check_rating_monotone.py, which reproduces it without "
          f"a database.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
