"""Is the XC-vs-TF difficulty gap the right SIZE? READ ONLY.

    python scripts/measure_sport_gap.py
    python scripts/measure_sport_gap.py --pool hs_m --min-races 3
    python scripts/measure_sport_gap.py --by-pool

Run from the PROJECT ROOT.

★ THE PROBLEM, AND IT IS AN IDENTIFICATION PROBLEM RATHER THAN A BUG.

  recenterSport moves the mean per-athlete sport offset `bbar` out of beta and
  into the difficulties: delta_c += bbar * s_c, s_c = +-0.5 by sport. That is
  a reparameterisation and every prediction is unchanged -- but it means the
  whole XC/TF level rests on whatever `bbar` measures.

  `bbar` is a mean of beta over athlete-SEASONS. season_year.py opens the
  academic year in August, so ONE group holds XC in the fall of year Y and
  track in the spring of year Y+1 -- and the XC races always come first.
  alpha is a single ability per group; beta is the only freedom that can move
  within it.

  So inside a group the sport contrast is PERFECTLY COLLINEAR WITH TIME. A
  high schooler who is fitter in spring than they were the previous fall --
  the normal trajectory -- produces a signal identical to "track is easier".
  beta absorbs it, bbar averages it over 2.07M dual-sport athlete-seasons, and
  the recentre deposits a year of growing up into the TF difficulties.

  pair_recenter's header says the assumption is unproven and frames the risk
  as SPECIALISATION. Time is the larger confound and is not mentioned there.

★ THE FIX FOR THE MEASUREMENT IS TO STOP USING THE SOLVE'S GROUPING. The
  academic year constrains beta; it does not constrain a script. Put the XC
  season BETWEEN two TF seasons and interpolate the athlete's own level across
  it by date:

      w    = (t2 - t1) / (t3 - t1)
      lhat = ln r1 + w * (ln r3 - ln r1)
      d    = ln r2 - lhat

  Any monotone improvement cancels -- that is what a sandwich buys and what a
  single within-year XC->TF contrast can never do.

★ AND IT READS FINAL speed_rating ON PURPOSE. If the difficulty in the
  database were already right, d would be zero by construction. So d IS the
  error in the current gap, in log-rating units, needing no engine internals
  and no re-solve. (Per-race ratings no longer carry beta -- it was removed
  from resultRatings -- so what is left is the cell difficulty being tested.)

★ BOTH DIRECTIONS, ALWAYS, AND NOT MERELY AS A CROSS-CHECK. Writing
  ln r_S(t) = A(t) + (eff_S - T_S) + const, a TF-XC-TF sandwich returns C + D
  and an XC-TF-XC sandwich returns C - D, where C is how far the athlete's
  true curve sits off the straight line at the middle date and D is the error
  in the sport gap. So the MEAN of the two directions is D with the curvature
  cancelled exactly, and half their difference IS the curvature.

  Running both does not just check the linear assumption -- it removes the
  need for it. Measured: C is about +0.0024 for high school and middle
  school and about ZERO for college, which is what a real developmental
  curve looks like. Twelve-year-olds decelerate over a year; twenty-year-olds
  do not.

⚠ WHAT THIS CANNOT SEPARATE, AND NOTHING ELSE CAN EITHER. Interpolating
  between two SPRINGS and evaluating at the intervening FALL still carries the
  seasonal cycle: "track is easier" and "athletes are fitter in spring" are
  the same contrast, observationally. There is a fair argument that some of
  that BELONGS in the sport difficulty -- a track PR is run in peak condition
  on a fast surface. What does not belong is a freshman's eight months of
  growth, and that is what the sandwich removes.

! THE BLOCK STATISTIC IS THE MEAN, NOT THE 80th PERCENTILE the boards use.
  Deliberately: the mean is unbiased in n. A quantile is not -- measured, its
  expectation drifts 2.31 -> 3.64 points from n=3 to n=30 -- and track
  athletes race more often than cross country athletes, so an n-dependent
  statistic would put a race-count difference straight into the sport gap.
"""
import argparse
import math
import os
import sys

from psycopg2.extras import RealDictCursor

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from database import getConn                                    # noqa: E402

# Ratings outside this are not performances, they are wreckage.
RATING_LO, RATING_HI = 20.0, 250.0

# How far apart the two ENDS of a sandwich may sit. Under a year is two blocks
# in one academic year (impossible for a same-sport pair); much over two years
# and a straight line through the athlete's development stops being credible.
SPAN_LO, SPAN_HI = 200, 900

# ! ONE ROW PER COMPETITION BLOCK, and (sport, year) already IS the block:
#   season_year puts fall XC and the following spring's track in one `year`,
#   and the sport tells them apart. So no date bucketing is needed or wanted.
_BLOCK_SQL = """
DROP TABLE IF EXISTS sg_block;
CREATE TEMP TABLE sg_block AS
SELECT person_id, pool, sport, year,
       avg(speed_rating)::float8                                   AS rating,
       -- midpoint of the block, as a real date: (date - date) is an int and
       -- (date + int) is a date, so this needs no epoch arithmetic.
       (min(race_date) + ((max(race_date) - min(race_date)) / 2))   AS mid_date,
       count(*)                                                    AS n
FROM   ranking_results
WHERE  person_id IS NOT NULL
  AND  race_date IS NOT NULL
  AND  speed_rating BETWEEN %(lo)s AND %(hi)s
  AND  (%(pool)s IS NULL OR pool = %(pool)s)
GROUP  BY person_id, pool, sport, year
HAVING count(*) >= %(min_races)s;
CREATE INDEX ON sg_block (person_id, pool, mid_date);
ANALYZE sg_block;
"""

# ⚠ PARTITIONED BY (person_id, pool), NOT person_id ALONE. An athlete moving
#   from hs_m to college_m changes the population their rating is scaled
#   against; a sandwich spanning that seam would measure the move, not the
#   sport.
_TRIPLE_SQL = """
DROP TABLE IF EXISTS sg_tri;
CREATE TEMP TABLE sg_tri AS
SELECT * FROM (
    SELECT person_id, pool, sport, mid_date, rating, n,
           lag(sport)     OVER w AS a_sport,
           lag(mid_date)  OVER w AS a_date,
           lag(rating)    OVER w AS a_rating,
           lead(sport)    OVER w AS c_sport,
           lead(mid_date) OVER w AS c_date,
           lead(rating)   OVER w AS c_rating
    FROM   sg_block
    WINDOW w AS (PARTITION BY person_id, pool ORDER BY mid_date)
) t
WHERE  a_sport IS NOT NULL AND c_sport IS NOT NULL
  -- the two ends share a sport and the middle is the other one
  AND  a_sport = c_sport
  AND  sport <> a_sport
  -- strictly between, so the interpolation weight is inside (0, 1)
  AND  mid_date > a_date AND mid_date < c_date
  AND  (c_date - a_date) BETWEEN %(span_lo)s AND %(span_hi)s
  AND  a_rating > 0 AND c_rating > 0 AND rating > 0;
"""


def _stats(values):
    """(n, mean, median, sd of the mean)."""
    if not values:
        return 0, 0.0, 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    s = sorted(values)
    med = (s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2]))
    if n < 2:
        return n, mean, med, 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return n, mean, med, math.sqrt(var / n)


def _collect(rows):
    """{'TF': [d...], 'XC': [d...]} keyed by the sport at the ENDS."""
    out = {"TF": [], "XC": []}
    for r in rows:
        span = (r["c_date"] - r["a_date"]).days
        if span <= 0:
            continue
        w = (r["mid_date"] - r["a_date"]).days / span
        # ★ INTERPOLATE IN LOG SPACE. rating is proportional to exp(eff), so
        #   the model is multiplicative and a log-linear line is the one whose
        #   residual means what we want it to mean.
        la, lc = math.log(r["a_rating"]), math.log(r["c_rating"])
        d = math.log(r["rating"]) - (la + w * (lc - la))
        out[r["a_sport"]].append(d)
    return out


def _report(label, by_end):
    """Print one population. Returns the combined estimate D, or None."""
    # d for a TF-ended sandwich is (XC minus interpolated TF): D itself.
    # d for an XC-ended sandwich is (TF minus interpolated XC): -D.
    n_tf, m_tf, md_tf, se_tf = _stats(by_end["TF"])
    n_xc, m_xc, md_xc, se_xc = _stats(by_end["XC"])
    print(f"\n  {label}")
    print(f"    {'sandwich':<12} {'n':>9} {'D (log)':>10} {'median':>10} "
          f"{'SE':>8}")
    print(f"    {'-' * 52}")
    if n_tf:
        print(f"    {'TF-XC-TF':<12} {n_tf:>9,} {m_tf:>10.5f} "
              f"{md_tf:>10.5f} {se_tf:>8.5f}")
    if n_xc:
        print(f"    {'XC-TF-XC':<12} {n_xc:>9,} {-m_xc:>10.5f} "
              f"{-md_xc:>10.5f} {se_xc:>8.5f}")
    if not (n_tf and n_xc):
        print("    only one direction available -- no cross-check.")
        return (m_tf if n_tf else -m_xc) if (n_tf or n_xc) else None

    # ★ THE TWO DIRECTIONS DIFFERING IS A MEASUREMENT, NOT A FAILURE, AND
    #   THE FIRST VERSION OF THIS CALLED IT A FAILURE.
    #
    #     ln r_S(t) = A(t) + (eff_S - T_S) + const
    #
    #   so with C the deviation of A at the middle date from the straight line
    #   through the two ends, and D = (eff_XC - T_XC) - (eff_TF - T_TF):
    #
    #     TF-XC-TF  ->  C + D          (printed as-is)
    #     XC-TF-XC  ->  C - D          (printed negated, so D - C)
    #
    #   The MEAN of the two is D and the curvature cancels EXACTLY. Half their
    #   difference is C. So a sandwich does not merely assume development is
    #   linear -- running it both ways measures the non-linearity and removes
    #   it, and the disagreement is the estimate of how curved a year is.
    #
    # ! WHAT WOULD ACTUALLY SPOIL D IS IMBALANCE, NOT CURVATURE. The two
    #   directions are averaged by count, so C leaks in proportional to
    #   (n_tf - n_xc) / (n_tf + n_xc). With the counts near equal that is
    #   nothing; it is only worth a warning when both the imbalance and the
    #   curvature are large.
    d = (m_tf * n_tf + (-m_xc) * n_xc) / (n_tf + n_xc)
    curve = 0.5 * (m_tf + m_xc)
    imbalance = (n_tf - n_xc) / float(n_tf + n_xc)
    leak = curve * imbalance
    pooled_se = math.sqrt(se_tf ** 2 + se_xc ** 2)
    print(f"    {'combined':<12} {n_tf + n_xc:>9,} {d:>10.5f}   <- D, the gap "
          f"error")
    print(f"\n    curvature of a year   {curve:+.5f}"
          + (f"  ({abs(m_tf + m_xc) / pooled_se:.0f} SE)" if pooled_se else ""))
    print(f"    it cancels in D except through the count imbalance "
          f"({imbalance:+.1%}),")
    print(f"    which leaves {leak:+.5f} of it in the number above.")
    if abs(leak) > 0.1 * max(abs(d), 1e-9):
        print("    ⚠ THAT LEAK IS OVER A TENTH OF D. The two directions are "
              "unbalanced enough\n      that the curvature is no longer "
              "cancelling. Read the two rows, not D.")
    return d


def _implications(d, bbar=-0.03924):
    """What d means for bbar and the difficulties."""
    print("\n  WHAT THIS MEANS FOR THE ENGINE\n")
    print(f"    D = {d:+.5f} in log-rating. rating is proportional to "
          f"exp(eff), so a")
    print(f"    positive D says XC rates ABOVE the athlete's own interpolated "
          f"TF level --")
    print(f"    that is, TF is currently over-penalised by that much.\n")
    # ! D IS THE ERROR IN THE GAP, SO THE CELLS EACH MOVE HALF OF IT.
    #     ln rating = A(t) + (eff_S - T_S) + const, so a sandwich returns
    #         D = (eff_XC - eff_TF) - (T_XC - T_TF)
    #     the CURRENT gap minus the TRUE one. Closing it means eff_TF += D/2
    #     and eff_XC -= D/2, which holds the overall level and narrows the gap
    #     by exactly D. Since the recentre moves eff by bbar * s_c with
    #     s = +-0.5, that is Delta_bbar = +D -- NOT 2D, which would overshoot
    #     by a factor of two and swap the sign of the error.
    new_bbar = bbar + d
    print(f"    recenterSport moves eff by bbar * s_c (s = +-0.5), and D is "
          f"the error in the")
    print(f"    GAP, so each sport carries half of it: eff_TF += D/2, "
          f"eff_XC -= D/2.\n")
    print(f"        bbar {bbar:+.5f}  ->  {new_bbar:+.5f}")
    print(f"        TF cells shift {d / 2:+.5f}, XC cells shift {-d / 2:+.5f}, "
          f"gap narrows by {d:+.5f}\n")
    print(f"    ⚠ THIS IS A MEASUREMENT, NOT A PATCH. Nothing here writes. To "
          f"use it,\n      recenterSport has to TAKE a bbar rather than "
          f"compute one -- the\n      reparameterisation arithmetic is "
          f"unchanged, you are only replacing a\n      confounded estimate "
          f"with a measured constant.")


# ------------------------------------------------------------------ #
#  --self-test -- DOES THE ESTIMATOR RECOVER A GAP IT WAS GIVEN?
# ------------------------------------------------------------------ #
#
# ★ THE ARITHMETIC IS THE WHOLE CLAIM, so it is checkable without a database.
#   Synthesise athletes with a KNOWN sport-gap error and a KNOWN improvement
#   rate, run them through the same _collect the real path uses, and see
#   whether the growth comes back out.
#
# ! IT ALSO RUNS THE NAIVE CONTRAST FOR CONTRAST. A single within-academic-year
#   XC->TF difference -- which is what beta sees and what bbar averages -- is
#   shown beside it. On the fixture the growth very nearly cancels the error
#   and the naive number lands near zero, which is exactly how a gap this
#   wrong stays invisible.
def selfTest():
    import datetime as _dt
    import random as _r

    EFF_XC, EFF_TF = 0.01746, -0.031549     # what the database holds today
    TRUE_XC, TRUE_TF = 0.006, -0.019        # a hypothetical truth
    true_d = (EFF_XC - EFF_TF) - (TRUE_XC - TRUE_TF)
    GROWTH, NOISE, N = 0.05, 0.02, 40_000

    _r.seed(3)

    def rating(a, sport):
        eff = EFF_XC if sport == "XC" else EFF_TF
        t = TRUE_XC if sport == "XC" else TRUE_TF
        return math.exp(math.log(110.0) + a + (eff - t) + _r.gauss(0, NOISE))

    t1 = _dt.date(2020, 4, 1)
    t2 = t1 + _dt.timedelta(days=185)       # the following fall
    t3 = t1 + _dt.timedelta(days=365)       # the next spring
    rows, naive = [], []
    for _ in range(N):
        a0 = _r.gauss(0, 0.15)
        a = {t: a0 + GROWTH * ((t - t1).days / 365.0) for t in (t1, t2, t3)}
        for ends, mid in (("TF", "XC"), ("XC", "TF")):
            rows.append({
                "a_sport": ends, "c_sport": ends, "sport": mid, "pool": "test",
                "a_date": t1, "mid_date": t2, "c_date": t3,
                "a_rating": rating(a[t1], ends),
                "rating":   rating(a[t2], mid),
                "c_rating": rating(a[t3], ends)})
        # the within-year contrast beta actually sees: XC in the fall, then
        # track in the spring, one academic year, XC always first.
        naive.append(math.log(rating(a[t3], "TF")) -
                     math.log(rating(a[t2], "XC")))

    print(f"\n  SELF TEST -- {N:,} synthetic athletes, "
          f"{GROWTH:.0%}/yr improvement, sigma {NOISE}\n")
    d = _report("SYNTHETIC", _collect(rows))
    nv = sum(naive) / len(naive)
    print(f"\n    planted gap error       {true_d:+.5f}")
    print(f"    recovered               {d:+.5f}   "
          f"(error {d - true_d:+.5f})")
    print(f"    naive within-year XC->TF   {nv:+.5f}   "
          f"<- off by {nv - true_d:+.5f}, which is the growth")
    ok = abs(d - true_d) < 0.002
    print(f"\n    {'PASS' if ok else 'FAIL'}: the sandwich removed the "
          f"improvement, the naive contrast did not.\n")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description="Measure the XC/TF gap free of within-year improvement.")
    ap.add_argument("--pool", default=None,
                    help="restrict to one pool (default: all)")
    ap.add_argument("--by-pool", action="store_true", dest="by_pool",
                    help="also break the estimate down per pool")
    ap.add_argument("--min-races", type=int, default=2, dest="min_races",
                    help="races a season block needs to count (default 2)")
    ap.add_argument("--span", type=int, nargs=2, default=(SPAN_LO, SPAN_HI),
                    metavar=("LO", "HI"),
                    help="days allowed between the two ends of a sandwich")
    ap.add_argument("--bbar", type=float, default=-0.03924,
                    help="the bbar the engine currently uses, for the "
                         "implications block")
    ap.add_argument("--self-test", action="store_true", dest="self_test",
                    help="check the estimator against synthetic athletes with "
                         "a known gap and known improvement. No database.")
    args = ap.parse_args()

    if args.self_test:
        return selfTest()

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # ! ONE BIG AGGREGATE. Give it room rather than letting it spill;
            #   SET LOCAL dies with the transaction, so nothing leaks.
            cur.execute("SET LOCAL work_mem = '1GB'")
            print("\n  building season blocks...")
            cur.execute(_BLOCK_SQL, {"lo": RATING_LO, "hi": RATING_HI,
                                     "pool": args.pool,
                                     "min_races": args.min_races})
            cur.execute("SELECT count(*) AS n, count(DISTINCT person_id) AS a "
                        "FROM sg_block")
            b = cur.fetchone()
            print(f"    {b['n']:,} blocks over {b['a']:,} athletes "
                  f"({args.min_races}+ races each)")

            print("  finding sandwiches...")
            cur.execute(_TRIPLE_SQL, {"span_lo": args.span[0],
                                      "span_hi": args.span[1]})
            cur.execute("SELECT * FROM sg_tri")
            rows = cur.fetchall()
            print(f"    {len(rows):,} sandwiches "
                  f"({args.span[0]}-{args.span[1]} days end to end)")

    if not rows:
        print("\n  NOTHING TO MEASURE. Widen --span or lower --min-races.\n")
        return 1

    print("\n" + "=" * 68)
    print("  XC MINUS INTERPOLATED TF, IN LOG-RATING")
    print("=" * 68)
    d = _report("ALL POOLS", _collect(rows))

    if args.by_pool:
        pools = {}
        for r in rows:
            pools.setdefault(r["pool"], []).append(r)
        for pool in sorted(pools, key=lambda p: -len(pools[p])):
            if len(pools[pool]) < 200:
                continue
            _report(pool, _collect(pools[pool]))

    if d is not None:
        _implications(d, args.bbar)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
