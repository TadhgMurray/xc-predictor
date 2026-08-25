# Project: xc-predictor
# File:    engine/diag_exponent_season_best.py
# Purpose: The distance-fade exponent measured on SEASON-BESTS. READ ONLY.
#
#     python engine/diag_exponent_season_best.py
#     python engine/diag_exponent_season_best.py --pool hs_m --sport TF
#     python engine/diag_exponent_season_best.py --min-per-rung 2
#
# Run from the PROJECT ROOT. Needs the database (reads ranking_results).
#
# ★ WHY A SECOND ESTIMAND. diag_exponent_by_ability measured the fitter's
#   own within-21-day pairs and found fast HS boys fading at k~1.156 in TF
#   -- while VDOT's equal-quality tables put the same conversion at ~1.083.
#   Both can be honest measurements of DIFFERENT things: a within-window
#   pair records what the athlete actually ran that month, effort asymmetry
#   included (the longer event raced below its equal-quality mark in duals
#   and doubles); an equivalence table records what equal-QUALITY
#   performances look like. A rating engine needs the second: equal quality
#   should map to equal rating, or every short-event athlete is deflated.
#
# ★ THE ESTIMAND HERE: best-vs-best. Per (athlete, pool, sport, season),
#   take the fastest time at each distance rung, pair the bests across
#   rungs, and measure k = log(t2/t1)/log(d2/d1). A sandbagged dual-meet
#   3200 no longer defines the athlete's 3200 -- their season's best does.
#   If this lands near VDOT (~1.08 for the fast deciles) the within-window
#   fit was inflated by effort bias and the spline should be refit on
#   season-best pairs; if it stays ~1.15, the corpus genuinely disagrees
#   with the tables and that is a finding about the tables' population.
#
# ⚠ KNOWN RESIDUAL BIAS, ACCEPTED FOR A DIAGNOSTIC: a rung raced ten times
#   yields a better best than a rung raced once (sampling), which flatters
#   whichever distance the athlete races more -- usually the SHORTER one in
#   TF, so the bias direction INFLATES k slightly. Season-best k is
#   therefore an upper bound on the equal-quality k, which makes a low
#   reading more convincing, not less. --min-per-rung 2 tightens it.
#
# ! ranking_results, NOT the raw tables. It already carries (person, pool,
#   sport, year, distance, time) filtered to rated rows, with indexes --
#   one GROUP BY replaces the fitter's hour-long stream. Rated-only is a
#   selection, but a diagnostic about the fast deciles lives entirely
#   inside it anyway.

import argparse
import sys

sys.path.insert(0, "engine")
sys.path.insert(0, "scripts")

from diag_exponent_by_ability import (decileTable, printPool,   # noqa: E402
                                      PROVISIONAL_K)

MIN_DIST = 800.0
MAX_DIST = 12_000.0
# One generous physical band, seconds per mile: under 3:50/mi nobody, over
# 15:00/mi nothing this law covers. Corruption control only -- the medians
# do the real robustness.
PACE_MIN_S_PER_MILE = 230.0
PACE_MAX_S_PER_MILE = 900.0
METERS_PER_MILE = 1609.344

_SQL = """
    SELECT person_id, pool, sport, year,
           round(distance)::int   AS dist,
           min(time_seconds)      AS best,
           count(*)               AS n_races
    FROM   ranking_results
    WHERE  time_seconds > 0
      AND  distance BETWEEN %(dmin)s AND %(dmax)s
    GROUP  BY person_id, pool, sport, year, round(distance)::int
    ORDER  BY person_id, pool, sport, year
"""


def _paceOk(dist, t):
    pace = t / (dist / METERS_PER_MILE)
    return PACE_MIN_S_PER_MILE <= pace <= PACE_MAX_S_PER_MILE


def pairsFromRungs(rungs, min_per_rung=1):
    """One athlete-season's {dist: (best, n)} -> season-best pair dicts.

    Every rung combination with a real distance gap; the ratio floor is
    decileTable's own MIN_RATIO, applied there, so this only skips the
    degenerate same-rung case and the pace-corrupt legs."""
    usable = sorted((d, b) for d, (b, n) in rungs.items()
                    if n >= min_per_rung and _paceOk(d, b))
    out = []
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            d1, t1 = usable[i]
            d2, t2 = usable[j]
            out.append({"distance1": d1, "time1": t1,
                        "distance2": d2, "time2": t2})
    return out


def collectPairs(cur, min_per_rung=1):
    """{(pool, sport): [pair dicts]} over the whole table, one streamed pass.

    Rows arrive grouped by (person, pool, sport, year); a key change means
    the previous athlete-season is complete and pairs off its rungs."""
    by_group = {}
    key, rungs = None, {}

    def flush():
        if key is None:
            return
        pl, sp = key[1], key[2]
        got = pairsFromRungs(rungs, min_per_rung)
        if got:
            by_group.setdefault((pl, sp), []).extend(got)

    for pid, pool, sport, year, dist, best, n in cur:
        k = (pid, pool, sport, year)
        if k != key:
            flush()
            key, rungs = k, {}
        rungs[float(dist)] = (float(best), int(n))
    flush()
    return by_group


def main():
    ap = argparse.ArgumentParser(
        description="Season-best distance exponent by ability decile. "
                    "Read only.")
    ap.add_argument("--deciles", type=int, default=10)
    ap.add_argument("--pool", help="limit to one pool (e.g. hs_m)")
    ap.add_argument("--sport", choices=["XC", "TF"])
    ap.add_argument("--min-per-rung", type=int, default=1,
                    help="races required at a distance before its best "
                         "counts (2 tightens the sampling bias)")
    ap.add_argument("--provisional-k", type=float, default=PROVISIONAL_K)
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn:
        with conn.cursor("season_best_diag") as cur:   # server-side stream
            cur.itersize = 50_000
            cur.execute(_SQL, {"dmin": MIN_DIST, "dmax": MAX_DIST})
            by_group = collectPairs(cur, args.min_per_rung)

    spreads = []
    for (pool, sport) in sorted(by_group, key=lambda g: (g[1], g[0])):
        if args.pool and pool != args.pool:
            continue
        if args.sport and sport != args.sport:
            continue
        got = printPool(f"{pool}|{sport} season-best", by_group[(pool, sport)],
                        args.deciles, args.provisional_k)
        if got:
            spreads.append((f"{pool}|{sport}", *got))

    print("\n  READ AGAINST THE FIRST DIAGNOSTIC: within-21-day pairs put "
          "fast hs_m|TF\n  at k~1.156. If the season-best fast deciles land "
          "near VDOT (~1.08),\n  the gap was effort bias and the spline "
          "should refit on season-best\n  pairs; if they hold ~1.15, the "
          "corpus genuinely scales steeper than\n  the tables.")


if __name__ == "__main__":
    main()
