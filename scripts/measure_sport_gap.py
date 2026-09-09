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
  the normal trajectory -- produces a signal identical to "track is easier",
  and nothing inside a group can separate them. That was the reason to build
  this: bbar might be booking a year of growing up as difficulty.

★ MEASURED, IT IS NOT. That hypothesis is dead and the number that killed it
  is worth keeping. This estimator removes the growth trend by construction;
  bbar does not. If growth were in bbar the two would disagree:

      bbar in the engine    -0.03924
      --from norm measured  -0.03878      1.2 percent apart

  beta really is measuring the raw XC-vs-TF discrepancy in normalised time,
  and a year of development is not detectably in it. The tool still earns its
  place -- it is what PROVED that, and --from rating/--from norm is what
  splits the gap into its two halves -- but do not read the paragraph above
  as a diagnosis.

★ WHAT THE SPLIT ACTUALLY FOUND. Across eight pools:

      D(cell)   mean +0.0538,  sd 0.0024      <- one global constant
      D(norm)   mean -0.0362,  sd 0.0151      <- all of the pool spread

  The cell half is flat, which is exactly what one global bbar deposited in
  every cell should look like -- the difficulties are behaving. Every bit of
  the per-pool variation lives in normalized_time. And the applied cell gap
  (+0.0538) overshoots the raw discrepancy it cancels (-0.0388) by +0.0134,
  of which bbar contributes only 0.0392 -- so roughly +0.015 is added to the
  XC-TF delta somewhere before the recentre runs. shrinkByLinkage assigning
  unidentified cells a per-sport default is the first thing to check; that is
  the `[link] sport defaults` line in 08_golive.log.

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
# ★ TWO SOURCES, AND THE DIFFERENCE BETWEEN THEM IS THE ANSWER.
#
#     ln rating = const(pool) + h * delta[cell] - ln(normalized_time)
#
#   alpha is not in a per-result rating at all, and pm_c comes from
#   poolPerAthlete, which strips the sport suffix (pair_ratings.py:57) -- so
#   the pool mean is one number shared by both sports and cancels inside a
#   sandwich. That leaves exactly two places a sport gap can live: the CELLS
#   (h * delta) and the NORMALISATION (distance, geometry, era, weather).
#
#   --from rating measures both together. --from norm uses 1/normalized_time
#   in place of the rating, which drops the cell term entirely. So
#
#       D(norm)              is the normalisation half
#       D(rating) - D(norm)  is the cell half
#
#   and one subtraction says which of the two to go and fix.
#
# ! GEOMETRIC MEAN, NOT ARITHMETIC. The estimator interpolates in logs, so the
#   block statistic must be the mean of the logs or the line being drawn is
#   not the line being sampled. exp(avg(ln x)) is that, written so the column
#   stays a rating.
#
# ! AND 1/normalized_time, NOT normalized_time. A rating rises when a run is
#   better and a normalised time falls, so inverting keeps every sign
#   downstream identical and makes the two runs directly comparable.
#
# ! norm COSTS A JOIN PER SPORT, which is why it is opt-in and why it is a
#   UNION rather than a CASE. ranking_results does not carry normalized_time
#   (see _COLUMNS in build_ranking_results), and XC and TF keep theirs in
#   different tables. A lateral picking between them would run 56 million
#   times; two aggregates the planner can hash-join separately do not.
# ★ THE ARM IS A PARAMETER, NOT ALWAYS THE SPORT (owner, 2026-09-09: "Could
#   track fitness gain be messing up on indoor to outdoor switch?").
#
#   The sandwich cancels a LINEAR trend exactly and a quadratic between its
#   two bread types, but it does NOT cancel a phase-locked seasonal effect:
#   both arrangements interpolate between two same-phase seasons and compare
#   against the opposite phase, so a real "sharper every spring than every
#   autumn" enters both with the same sign and sits inside D.
#
# ★ INDOOR AGAINST OUTDOOR IS THE CONTROL THAT SEPARATES THEM, and it is the
#   owner's question turned into an experiment. Indoor and outdoor are the
#   SAME sport to the engine -- one `sport` indicator, one mu, one pool
#   anchor -- so no sport-level scale error can exist between them. Whatever
#   D comes back from an indoor/outdoor sandwich is therefore phase and
#   calibration, not sport level. Then:
#
#       D(indoor vs outdoor)   = pure phase (+ any indoor geometry error)
#       D(XC vs outdoor)       = sport scale + a half-year of phase
#       D(XC vs indoor)        = sport scale + a season of phase
#
#   If D(in/out) is ~0 and the two XC arms agree, the gap is a scale error
#   and the whole of it should be corrected. If D(in/out) is large, or the
#   XC-indoor and XC-outdoor arms disagree by about it, that much of D is
#   fitness the rating is SUPPOSED to carry and correcting it would flatten
#   a real effect.
_ARM_SPORT = "k.sport"

# ! LATERAL, AND ONLY FOR TF. meets_tf is one row per EVENT, so a plain join
#   fans a result out across its meet; LIMIT 1 is exactly one. The ON k.sport
#   = 'TF' keeps it off every XC row rather than looking up a table that
#   cannot contain them.
_ARM_INOUT = """CASE WHEN k.sport <> 'TF' THEN k.sport
                     WHEN io.is_indoor = 1 THEN 'TF-in'
                     ELSE 'TF-out' END"""
_ARM_INOUT_JOIN = """
LEFT   JOIN LATERAL (
    SELECT m.is_indoor
    FROM   results_tf rt
    JOIN   meets_tf m ON m.meet_id = rt.meet_id AND m.source = rt.source
    WHERE  rt.result_id = k.result_id
    LIMIT  1
) io ON k.sport = 'TF'
"""

_BLOCK_SELECT = """
SELECT k.person_id, k.pool, {arm} AS sport, k.year,
       {stat}                                                        AS rating,
       -- ! CARRIED FOR THE COVARIATE REPORT, NOT FOR THE ESTIMATE. D is
       --   computed from `rating` alone; these only answer "what else is
       --   different about this pool".
       avg(k.distance) FILTER (WHERE k.distance > 0)::float8         AS dist,
       -- midpoint of the block, as a real date: (date - date) is an int and
       -- (date + int) is a date, so this needs no epoch arithmetic.
       (min(k.race_date) + ((max(k.race_date) - min(k.race_date)) / 2))
                                                                     AS mid_date,
       count(*)                                                      AS n
FROM   ranking_results k
{join}
WHERE  k.person_id IS NOT NULL
  AND  k.race_date IS NOT NULL
  AND  k.speed_rating BETWEEN %(lo)s AND %(hi)s
  AND  (%(pool)s IS NULL OR k.pool = %(pool)s)
  {extra}
GROUP  BY k.person_id, k.pool, {arm}, k.year
HAVING count(*) >= %(min_races)s
"""

_SOURCES = {
    "rating": [{"stat": "exp(avg(ln(k.speed_rating)))::float8",
                "join": "", "extra": ""}],
    "norm": [
        {"stat": "exp(-avg(ln(r.normalized_time)))::float8",
         "join": "JOIN   results r ON r.result_id = k.result_id",
         "extra": "AND  k.sport = 'XC' AND r.normalized_time > 0"},
        {"stat": "exp(-avg(ln(r.normalized_time)))::float8",
         "join": "JOIN   results_tf r ON r.result_id = k.result_id",
         "extra": "AND  k.sport = 'TF' AND r.normalized_time > 0"},
    ],
}


def blockSql(source, split_indoor=False):
    """The CREATE TEMP TABLE for one source, one arm per sport table."""
    arm = _ARM_INOUT if split_indoor else _ARM_SPORT
    extra_join = _ARM_INOUT_JOIN if split_indoor else ""
    arms = "\nUNION ALL\n".join(
        _BLOCK_SELECT.format(arm=arm, stat=v["stat"],
                             join=v["join"] + extra_join, extra=v["extra"])
        for v in _SOURCES[source])
    return ("DROP TABLE IF EXISTS sg_block;\n"
            "CREATE TEMP TABLE sg_block AS\n" + arms + ";\n"
            "CREATE INDEX ON sg_block (person_id, pool, mid_date);\n"
            "ANALYZE sg_block;\n")


# ⚠ PARTITIONED BY (person_id, pool), NOT person_id ALONE. An athlete moving
#   from hs_m to college_m changes the population their rating is scaled
#   against; a sandwich spanning that seam would measure the move, not the
#   sport.
_TRIPLE_SQL = """
DROP TABLE IF EXISTS sg_tri;
CREATE TEMP TABLE sg_tri AS
SELECT * FROM (
    SELECT person_id, pool, sport, mid_date, rating, n, dist,
           lag(sport)     OVER w AS a_sport,
           lag(mid_date)  OVER w AS a_date,
           lag(rating)    OVER w AS a_rating,
           lag(dist)      OVER w AS a_dist,
           lag(n)         OVER w AS a_n,
           lead(sport)    OVER w AS c_sport,
           lead(mid_date) OVER w AS c_date,
           lead(rating)   OVER w AS c_rating,
           lead(dist)     OVER w AS c_dist,
           lead(n)        OVER w AS c_n
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


# ★ THE PAIRWISE REPORT, FOR --split-indoor. With three arms a sandwich can
#   be any (ends, middle) pair, so the estimate is per PAIR rather than one
#   number. Same arithmetic as _report; only the bookkeeping differs.
#
# ! ARM ORDER IS PINNED, NOT ALPHABETICAL, so every D printed reads the same
#   way round as the headline one: XC first where XC is in the pair, and
#   indoor before outdoor otherwise. A sign convention that flips between
#   rows of the same table is a trap, not a table.
_ARM_RANK = {"XC": 0, "TF-in": 1, "TF-out": 2, "TF": 1}


def _collectPairs(rows):
    """{(first, second): {ends_arm: [d, ...]}} over every arm pair present."""
    out = {}
    for r in rows:
        span = (r["c_date"] - r["a_date"]).days
        if span <= 0:
            continue
        w = (r["mid_date"] - r["a_date"]).days / span
        la, lc = math.log(r["a_rating"]), math.log(r["c_rating"])
        d = math.log(r["rating"]) - (la + w * (lc - la))
        pair = tuple(sorted((r["a_sport"], r["sport"]),
                            key=lambda a: (_ARM_RANK.get(a, 9), a)))
        out.setdefault(pair, {}).setdefault(r["a_sport"], []).append(d)
    return out


def _reportPairs(pairs):
    """Print D for each arm pair. Returns {pair: D}."""
    got = {}
    print(f"\n  {'pair (first minus interpolated second)':<40} {'n':>9} "
          f"{'D (log)':>10} {'SE':>8}")
    print("  " + "-" * 70)
    for pair in sorted(pairs, key=lambda p: (_ARM_RANK.get(p[0], 9), p)):
        first, second = pair
        by_end = pairs[pair]
        # ends = second  -> middle is first  -> d is (first - interp second) = +D
        # ends = first   -> middle is second -> d is (second - interp first) = -D
        n_p, m_p, _md, se_p = _stats(by_end.get(second, []))
        n_m, m_m, _md2, se_m = _stats(by_end.get(first, []))
        vals = []
        if n_p:
            vals.append((n_p, m_p, se_p))
        if n_m:
            vals.append((n_m, -m_m, se_m))
        if not vals:
            continue
        n = sum(v[0] for v in vals)
        D = sum(v[0] * v[1] for v in vals) / n
        se = math.sqrt(sum((v[0] / n) ** 2 * v[2] ** 2 for v in vals))
        got[pair] = D
        print(f"  {first + ' - ' + second:<40} {n:>9,} {D:>10.5f} "
              f"{se:>8.5f}")
        if n_p and n_m:
            # ⚠ THE TWO DIRECTIONS, PRINTED. They differ by twice the
            #   curvature of the year; agreement is the cross-check that the
            #   interpolation is doing its job.
            print(f"    {'ends ' + second:<38} {n_p:>9,} {m_p:>10.5f}")
            print(f"    {'ends ' + first:<38} {n_m:>9,} {-m_m:>10.5f}")
    return got


# ★ WHAT THE THREE ARMS MEAN TOGETHER. This is the whole point of the mode:
#   indoor and outdoor are the SAME sport to the engine -- one indicator, one
#   mu, one pool anchor -- so no sport-LEVEL scale error can sit between
#   them. Whatever D they show is phase (real fitness) plus any indoor
#   geometry miscalibration, and that is the part of the XC/TF gap that must
#   NOT be corrected away.
def _phaseVerdict(D):
    io_pair = ("TF-in", "TF-out")
    xin = ("XC", "TF-in")
    xout = ("XC", "TF-out")
    print("\n" + "=" * 68)
    print("  HOW MUCH OF THE XC/TF GAP IS FITNESS RATHER THAN SCALE")
    print("=" * 68)
    if io_pair not in D:
        print("\n  No indoor/outdoor sandwiches -- cannot separate them.")
        return
    d_io = D[io_pair]
    print(f"\n    indoor minus outdoor            {d_io:+.5f}")
    print("      Same sport, same mu, same pool anchor: NO sport-level scale")
    print("      error can live here. This is phase (and any indoor geometry")
    print("      error), and it is the size of the effect the rating is")
    print("      SUPPOSED to carry.")
    if xin in D and xout in D:
        print(f"\n    XC minus indoor                 {D[xin]:+.5f}")
        print(f"    XC minus outdoor                {D[xout]:+.5f}")
        # ! (XC-out) - (XC-in) = in - out, NOT the other way round. The
        #   first version printed (XC-in) - (XC-out) and reported a ratio of
        #   -1.00 on a fixture built to be exactly phase -- the right answer
        #   with the sign inverted, which reads as "nothing is explained".
        gap = D[xout] - D[xin]
        print(f"    outdoor arm minus indoor arm    {gap:+.5f}")
        print("      If the XC/TF gap were pure scale, the two XC arms would")
        print("      be EQUAL -- one sport level, measured twice -- and this")
        print("      would be zero. It is instead the indoor/outdoor phase,")
        print(f"      so it should come out near the {d_io:+.5f} above.")
        if abs(d_io) < 1e-9:
            print("        indoor/outdoor is zero -- the whole gap is scale.")
        else:
            print(f"        ratio = {gap / d_io:+.2f}"
                  f"   (+1.00 = the two XC arms differ by exactly the "
                  f"indoor/outdoor phase)")
        scale = (D[xin] + D[xout]) / 2.0
        print(f"\n    the two XC arms average         {scale:+.5f}")
        print("      ⚠ THAT AVERAGE IS STILL NOT THE SCALE ERROR. It is the")
        print("        scale error plus whatever phase separates the TF")
        print("        season as a whole from the XC season, which this")
        print("        design cannot see -- indoor/outdoor only bounds the")
        print("        phase WITHIN track. Treat it as an upper bound, and")
        print("        subtract at most the indoor/outdoor figure from it")
        print("        before passing anything to --sport-gap-delta.")


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


def _implications(d, bbar=-0.03924, source="rating"):
    """What d means for bbar and the difficulties."""
    # ⚠ ONLY --from rating IS AN ERROR. --from norm measures the raw XC-vs-TF
    #   discrepancy in normalised time -- the thing the difficulty EXISTS to
    #   correct -- so feeding it through this arithmetic reads a correct
    #   measurement as a correction to itself and doubles the gap. It printed
    #   "bbar -0.03924 -> -0.07802" once; that number means nothing.
    if source != "rating":
        print("\n  WHAT THIS MEANS\n")
        print(f"    D = {d:+.5f} is the RAW XC-vs-TF discrepancy in normalised")
        print(f"    time, with no difficulty applied. It is what the sport gap")
        print(f"    exists to cancel, NOT an error in it.\n")
        print(f"    The engine's bbar is {bbar:+.5f}. These should be close --")
        print(f"    beta measures this same quantity -- so a large difference")
        print(f"    would mean bbar is picking up something else.")
        print(f"        difference {bbar - d:+.5f}\n")
        print(f"    ★ SUBTRACT THIS RUN FROM THE --from rating RUN for the CELL")
        print(f"      half: D(cell) = D(rating) - D(norm). That is the")
        print(f"      correction the engine actually applies; comparing it to")
        print(f"      the number above is what says whether it overshoots.\n")
        return
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
#  WHAT ELSE IS DIFFERENT ABOUT A POOL?
# ------------------------------------------------------------------ #
#
# ★ D IS NOT ONE NUMBER, AND THAT IS THE ACTUAL FINDING. It runs from -0.004
#   in college_f to +0.045 in elem_m, ordered by age. A single bbar applied to
#   every cell cannot be right for all of them, so before changing bbar it is
#   worth asking what the pools differ in.
#
# ★ THE HYPOTHESIS WITH A SHARP TEST. normalized_time is
#
#       T_anchor = T_d * exp(g(ln target) - g(ln d))
#
#   and targetFor is PER POOL. hs_m races XC at 5000 against a 5000 anchor --
#   no correction at all -- while its track races at 800-3200 carry the whole
#   of it. College track sits much nearer its anchor. Elementary track is
#   furthest away and on the steepest part of the curve.
#
#   So if the distance curve is even slightly too steep, the error appears as
#   an apparent SPORT gap, sized by how much further the track races had to
#   travel than the cross country ones:
#
#       span = ln(target / d_TF) - ln(target / d_XC) = ln(d_XC / d_TF)
#
#   and D should be span times a CONSTANT -- the error in the local exponent.
#   If D/span is roughly equal across pools, the fault is the distance curve
#   and bbar is a symptom. If it scatters, it is not, and this hypothesis is
#   dead rather than merely unproven.
#
# ! AND SOME COVARIATES THAT ARE NOT THE HYPOTHESIS, on purpose. Race volume,
#   season spacing and rating level are printed beside it so the table can
#   disagree with the story it was built to test.
def _covariates(rows):
    """Per pool: D, the distance span, and a few things that are not it."""
    by_pool = {}
    for r in rows:
        by_pool.setdefault(r["pool"], []).append(r)

    print("\n" + "=" * 78)
    print("  WHAT VARIES WITH D")
    print("=" * 78)
    print(f"\n    {'pool':<10} {'D':>9} {'d_XC':>7} {'d_TF':>7} {'span':>7} "
          f"{'D/span':>8} {'races':>11} {'gap_d':>6} {'level':>7}")
    print(f"    {'-' * 76}")
    out = []
    for pool in sorted(by_pool, key=lambda p: -len(by_pool[p])):
        rs = by_pool[pool]
        if len(rs) < 200:
            continue
        d = _report_quiet(rs)
        if d is None:
            continue
        # distances, taken from whichever leg of the sandwich is that sport
        # ! THE MIDDLE IS ONE SPORT AND BOTH ENDS ARE THE OTHER, so a
        #   sandwich contributes one block to each list -- never mixed.
        xc, tf, vol_xc, vol_tf, spans, lvl = [], [], [], [], [], []
        for r in rs:
            mid = xc if r["sport"] == "XC" else tf
            end = tf if r["sport"] == "XC" else xc
            mid_vol = vol_xc if r["sport"] == "XC" else vol_tf
            end_vol = vol_tf if r["sport"] == "XC" else vol_xc
            if r["dist"] is not None:
                mid.append(r["dist"])
                mid_vol.append(r["n"] or 0)
            for dist, n in ((r["a_dist"], r["a_n"]), (r["c_dist"], r["c_n"])):
                if dist is not None:
                    end.append(dist)
                    end_vol.append(n or 0)
            spans.append((r["c_date"] - r["a_date"]).days)
            lvl.append(r["rating"])
        if not (xc and tf):
            continue
        d_xc = sum(xc) / len(xc)
        d_tf = sum(tf) / len(tf)
        span = math.log(d_xc / d_tf) if d_tf > 0 else 0.0
        ratio = d / span if abs(span) > 1e-6 else float("nan")
        races = (f"{sum(vol_xc) / max(len(vol_xc), 1):.1f}"
                 f"/{sum(vol_tf) / max(len(vol_tf), 1):.1f}")
        print(f"    {pool:<10} {d:>9.5f} {d_xc:>7.0f} {d_tf:>7.0f} "
              f"{span:>7.3f} {ratio:>8.4f} {races:>11} "
              f"{sum(spans) / len(spans):>6.0f} "
              # ! MEANINGLESS UNDER --from norm, where `rating` is
              #   1/normalized_time, and it read 0.0. A zero somebody has to
              #   work out is not a measurement.
              f"{(f'{sum(lvl) / len(lvl):.1f}' if max(lvl) > 1.0 else '--'):>7}")
        out.append((pool, d, span, ratio, len(rs)))

    print(f"\n    d_XC / d_TF   mean race distance in each sport, metres")
    print(f"    span          ln(d_XC / d_TF) -- how much further the track")
    print(f"                  races had to be normalised than the XC ones")
    print(f"    D/span        the implied error in the local exponent. THE")
    print(f"                  TEST: if the distance curve is the fault, this")
    print(f"                  column is roughly CONSTANT down the table.")
    print(f"    races         mean races per season block, XC/TF")
    print(f"    gap_d         mean days between the two ends of a sandwich")
    print(f"    level         mean rating of the middle season\n")

    good = [o for o in out if o[3] == o[3] and abs(o[2]) > 0.05]
    if len(good) >= 3:
        vals = [o[3] for o in good]
        m = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))
        print(f"    D/span across {len(good)} pools: mean {m:+.4f}, "
              f"sd {sd:.4f}  (spread {sd / abs(m):.0%} of the mean)")
        if sd < 0.35 * abs(m):
            print("    ★ THAT IS TIGHT. D is tracking the distance span, so "
                  "what is being\n      measured as a sport gap is mostly a "
                  "DISTANCE CURVE error, and bbar\n      is the wrong knob.")
        else:
            print("    ⚠ THAT IS NOT CONSTANT. D does not track the distance "
                  "span, so the\n      distance-curve hypothesis does not "
                  "explain the pool spread. Look at\n      the columns that "
                  "are not it.")
    print()


def _report_quiet(rows):
    """D for one population, printing nothing."""
    by = _collect(rows)
    n_tf, m_tf, _md, _se = _stats(by["TF"])
    n_xc, m_xc, _md2, _se2 = _stats(by["XC"])
    if not (n_tf or n_xc):
        return None
    if not (n_tf and n_xc):
        return m_tf if n_tf else -m_xc
    return (m_tf * n_tf + (-m_xc) * n_xc) / (n_tf + n_xc)


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
    ap.add_argument("--from", dest="source", default="rating",
                    choices=sorted(_SOURCES),
                    help="rating: the published speed_rating, which carries "
                         "both the cell difficulty and the normalisation. "
                         "norm: 1/normalized_time, which carries only the "
                         "normalisation. Subtract them for the cell half.")
    # ★ THE OWNER'S QUESTION AS A FLAG (2026-09-09): "Could track fitness
    #   gain be messing up on indoor to outdoor switch?"
    ap.add_argument("--split-indoor", action="store_true", dest="split_indoor",
                    help="treat indoor and outdoor track as separate arms. "
                         "They share one sport indicator, one mu and one pool "
                         "anchor, so any gap between them is phase rather "
                         "than sport scale -- which is what bounds how much "
                         "of the XC/TF gap may honestly be corrected.")
    ap.add_argument("--self-test", action="store_true", dest="self_test",
                    help="check the estimator against synthetic athletes with "
                         "a known gap and known improvement. No database.")
    ap.add_argument("--emit", action="store_true",
                    help="close the loop: write measured_bbar = applied + D "
                         "to engine/data/sport_gap_bbar.json for the next "
                         "solve to apply (requires --from rating and a "
                         "recorded applied_bbar, or --applied)")
    ap.add_argument("--applied", type=float, default=None,
                    help="override the applied bbar for --emit (first seed, "
                         "read from the last 08 log's '[all] sport recentre: "
                         "bbar X' line)")
    args = ap.parse_args()

    if args.self_test:
        return selfTest()

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # ! ONE BIG AGGREGATE. Give it room rather than letting it spill;
            #   SET LOCAL dies with the transaction, so nothing leaks.
            cur.execute("SET LOCAL work_mem = '1GB'")
            print(f"\n  building season blocks from {args.source}...")
            cur.execute(blockSql(args.source, args.split_indoor),
                        {"lo": RATING_LO, "hi": RATING_HI,
                         "pool": args.pool, "min_races": args.min_races})
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

    if args.split_indoor:
        print("\n" + "=" * 68)
        print("  THREE ARMS: XC, INDOOR TRACK, OUTDOOR TRACK")
        print("=" * 68)
        _phaseVerdict(_reportPairs(_collectPairs(rows)))
        print()
        return 0

    print("\n" + "=" * 68)
    print("  XC MINUS INTERPOLATED TF, IN LOG-RATING")
    print("=" * 68)
    d = _report("ALL POOLS", _collect(rows))

    _covariates(rows)

    if args.by_pool:
        pools = {}
        for r in rows:
            pools.setdefault(r["pool"], []).append(r)
        for pool in sorted(pools, key=lambda p: -len(pools[p])):
            if len(pools[pool]) < 200:
                continue
            _report(pool, _collect(pools[pool]))

    if d is not None:
        _implications(d, args.bbar, args.source)
    if args.emit:
        return _emit(d, args)
    print()
    return 0


# ---- the loop-closing write. measured_bbar = applied + D goes to the json
#      pair_recenter loads at the NEXT solve. Unattended-safe: every refusal
#      is a printed reason and exit 0, never a stop.
def _emit(d, args):
    import datetime
    import json
    path = os.path.join("engine", "data", "sport_gap_bbar.json")
    if args.source != "rating":
        print(f"\n  --emit REFUSED: only --from rating measures the gap "
              "ERROR\n  (--from norm is the raw discrepancy the difficulty "
              "exists to correct).\n")
        return 0
    if d is None:
        print("\n  --emit SKIPPED: no D measured.\n")
        return 0
    doc = {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        pass
    applied = (args.applied if args.applied is not None
               else doc.get("applied_bbar"))
    if applied is None:
        print("\n  --emit SKIPPED: no applied bbar on record. The 08 golive "
              "records it\n  after its recentre; for a first seed pass "
              "--applied X (X from the last\n  08 log's '[all] sport "
              "recentre: bbar X' line).\n")
        return 0
    doc.update({"measured_bbar": float(applied) + float(d),
                "applied_bbar": float(applied),
                "D": float(d),
                "measured_date": datetime.date.today().isoformat()})
    # ! THE RIDGE TRAVELS WITH THE CONSTANT. applied_ridge is what the 08
    #   golive recorded for the recentring D was measured against, so it
    #   is the ridge this measured_bbar is valid at. pair_recenter.measuredFor
    #   applies the constant only to a solve at that ridge (issue #74).
    if doc.get("applied_ridge") is not None:
        doc["measured_ridge"] = float(doc["applied_ridge"])

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    # ★ THE NUDGE, IN POINTS. Owner asked for prominence (2026-08-27):
    #   each sport carries half of D, so translate that half into rating
    #   points at the 130 level, where the eye actually lives.
    half = float(d) / 2.0
    pts = 130.0 * (math.exp(abs(half)) - 1.0)
    xc_dir, tf_dir = ("-", "+") if d > 0 else ("+", "-")
    print("\n  " + "=" * 66)
    print(f"  SPORT GAP NUDGE   bbar {float(applied):+.5f}  ->  "
          f"{doc['measured_bbar']:+.5f}   (D {float(d):+.5f})")
    print(f"  next run: XC {xc_dir}{abs(half):.3%}, TF {tf_dir}"
          f"{abs(half):.3%}  ~=  {pts:.1f} rating points each way at 130")
    print("  D toward 0 across nights = the loop converging")
    print("  " + "=" * 66 + f"\n  written to {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
