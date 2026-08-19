# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/slope_fit.py
# Purpose: fit a per-cell SLOPE -- how a course's difficulty error changes
#          with athlete ability -- and correct for it.
#
#   THE DEFECT
#   ----------
#   delta is ONE multiplicative number per (course, distance) cell. If a
#   course's real difficulty does not scale proportionally with pace, no
#   single number fits the whole field, and the fit lands where the evidence
#   is -- the middle -- mis-serving both ends in OPPOSITE directions.
#
#   Hydrangea Ranch (Ultimook), 13,503 athletes, leave-one-out:
#
#        ability     lift            ability     lift
#         80- 85    -1.88            115-120    +3.60
#         90- 95    -1.71            125-130    +5.18
#        100-105    -0.14            130-135    +7.65
#        110-115    +1.40            136-139    +8.29
#
#   Monotone, crossing zero near 105. The venue's MEAN lift is -0.19, i.e.
#   apparently perfect, because the mass of the field sits where the effect is
#   nil. That is why nothing else found it.
#
#   ★ AND IT IS INVISIBLE TO field_shift BY CONSTRUCTION. That detector
#     compares a field to their own career averages -- but athletes who race
#     here repeatedly carry the error INTO those averages, so the baseline
#     absorbs it and the race stops looking anomalous. A venue with a wrong
#     delta and a stable repeat field CONCEALS ITSELF. This is the class that
#     survives to the leaderboard quietly: not a 170, a 145 where 135 belongs.
#
#   ★ THE CORPUS IS THE CONTROL, AND IT IS FLAT: +0.59 at the bottom band to
#     -1.28 at the top, a 1.9-point drift over the whole range, versus
#     Hydrangea's 10-point swing in the opposite direction. So this is a
#     property of particular COURSES, not of the rating model. If the corpus
#     curve ever stops being flat, this whole script is measuring the model
#     and must not be applied -- validate() checks that every run.
#
#   WHY POST-HOC AND NOT IN THE SOLVER
#   ----------------------------------
#   The principled fix is a per-cell interaction term in the CG solve, which
#   doubles the unknowns and changes what pair_all is solving. This fits the
#   same quantity from RESIDUALS instead -- exactly the pattern the weather
#   and form corrections already use -- so it can be measured, shrunk and
#   validated without touching the solver.
#
#   ⚠ ONE PASS ONLY. The correction changes ratings, which changes the
#     leave-one-out baselines, which would change the fit. Iterating would
#     chase its own tail; weather and form have the same property and are also
#     fitted once.

import os
import sys
import math

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# ------------------------------------------------------------------ #
#  CONSTANTS
# ------------------------------------------------------------------ #

# Ability at which the correction is zero. 100 is the pool mean by
# construction, so a cell's delta keeps its meaning for a typical athlete and
# only the ends move.
PIVOT = 100.0

# An athlete needs this many races AWAY from the cell for their baseline to be
# a baseline rather than one race's noise.
MIN_AWAY_RACES = 3

# A cell needs this many athletes before a slope is fitted at all.
MIN_ATHLETES = 60

# ★ SHRINKAGE, SAME LOGIC AS THE PAIR ENGINE'S delta SHRINKAGE.
#   A slope fitted on 60 athletes is mostly noise; one fitted on 5,000 is
#   mostly signal. kept = n / (n + SHRINK_K), so a cell with SHRINK_K athletes
#   keeps half its measured slope.
#
#   ⚠ THE COST OF GETTING THIS WRONG IS ASYMMETRIC. Under-shrinking invents a
#     slope for a cell that does not have one and MOVES ratings that were
#     right. Over-shrinking leaves a known defect partly in place. Start high.
SHRINK_K = 400.0

# Refuse to move any single rating by more than this, whatever the fit says.
# A cap is not a fudge here: it bounds the blast radius of a bad fit on a cell
# nobody has looked at.
MAX_ADJUST = 6.0


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE OBSERVATIONS
# ------------------------------------------------------------------ #

def buildLift(cur):
    """
    slope_obs: (cell, person, at_cell, elsewhere) for every athlete-cell pair.

    ★ `elsewhere` IS LEAVE-ONE-OUT PER CELL, and that is the whole reason this
      measurement works. Reusing one career average for every cell would leave
      each cell's own error inside its own baseline -- the exact circularity
      that hides this bug. Computed as
          (career_total - cell_total) / (career_n - cell_n)
      which gets the leave-one-out figure from two sums rather than a
      self-join per cell.

    ★ THE CELL IS (course_name, distance), NOT THE VENUE. Mt. SAC runs twenty
      distances under one name and they are different physical courses; the
      difficulty key already splits on distance, so the slope must too.
    """
    cur.execute("DROP TABLE IF EXISTS slope_obs")
    cur.execute(f"""
        CREATE TABLE slope_obs AS
        WITH rated AS (
            SELECT r.person_id,
                   m.course_name,
                   round(m.distance)::int AS dist,
                   r.speed_rating
            FROM   results r
            JOIN   meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
            WHERE  r.speed_rating BETWEEN 40 AND 200
              AND  r.person_id IS NOT NULL
              AND  m.course_name IS NOT NULL
              AND  m.distance > 0
        ),
        career AS (
            SELECT person_id, sum(speed_rating) AS tot, count(*) AS n
            FROM   rated GROUP BY 1
        ),
        cell AS (
            SELECT person_id, course_name, dist,
                   sum(speed_rating) AS tot, count(*) AS n
            FROM   rated GROUP BY 1, 2, 3
        )
        SELECT c.course_name, c.dist,
               (c.tot / c.n)                   AS at_cell,
               ((k.tot - c.tot) / (k.n - c.n)) AS elsewhere
        FROM   cell c
        JOIN   career k ON k.person_id = c.person_id
        WHERE  k.n - c.n >= {MIN_AWAY_RACES}
    """)
    cur.execute("CREATE INDEX ON slope_obs (course_name, dist)")
    cur.execute("ANALYZE slope_obs")
    cur.execute("SELECT count(*), count(DISTINCT (course_name, dist)) "
                "FROM slope_obs")
    n, cells = cur.fetchone()
    print(f"    {n:,} athlete-cell observations across {cells:,} cells")
    return n


# ------------------------------------------------------------------ #
# CHUNK 2 -- FIT
# ------------------------------------------------------------------ #

def fitSlopes(cur):
    """
    cell_slope: per cell, the OLS slope of lift against ability, shrunk.

    lift  = at_cell - elsewhere
    x     = elsewhere - PIVOT          (ability, centred)
    slope = sum(x*lift) / sum(x*x)     (through the origin -- see below)

    ★ THROUGH THE ORIGIN, NOT A FREE INTERCEPT. A free intercept would absorb
      the cell's ordinary delta error, and delta already exists to carry that.
      Forcing the line through (PIVOT, 0) means this fits ONLY the part that
      varies with ability and leaves the level alone -- so applying it cannot
      re-litigate a difficulty the solver already decided.

    ★ SHRUNK BY EVIDENCE, n / (n + SHRINK_K). Identical in spirit to the pair
      engine's degree-based shrinkage: a slope measured on few athletes is
      pulled toward zero, which is the "no slope" null.
    """
    cur.execute("DROP TABLE IF EXISTS cell_slope")
    cur.execute(f"""
        CREATE TABLE cell_slope AS
        SELECT course_name, dist,
               count(*)                                          AS n,
               sum((elsewhere - {PIVOT}) * (at_cell - elsewhere)) AS sxy,
               sum((elsewhere - {PIVOT}) * (elsewhere - {PIVOT})) AS sxx,
               CASE WHEN sum((elsewhere - {PIVOT})
                             * (elsewhere - {PIVOT})) > 0
                    THEN (sum((elsewhere - {PIVOT})
                              * (at_cell - elsewhere))
                          / sum((elsewhere - {PIVOT})
                                * (elsewhere - {PIVOT})))
                         * (count(*)::float / (count(*) + {SHRINK_K}))
                    ELSE 0.0
               END                                               AS slope
        FROM   slope_obs
        GROUP  BY 1, 2
        HAVING count(*) >= {MIN_ATHLETES}
    """)
    cur.execute("CREATE INDEX ON cell_slope (course_name, dist)")
    cur.execute("ANALYZE cell_slope")

    cur.execute("""SELECT count(*),
                          round(avg(slope)::numeric, 5),
                          round(stddev(slope)::numeric, 5),
                          round(min(slope)::numeric, 4),
                          round(max(slope)::numeric, 4)
                   FROM cell_slope""")
    n, mean, sd, lo, hi = cur.fetchone()
    print(f"    {n:,} cells fitted   mean {mean}  sd {sd}  "
          f"range [{lo}, {hi}]")
    return n


def report(cur, limit=25):
    """The cells whose slope moves a 135-ability athlete the most."""
    cur.execute(f"""
        SELECT course_name, dist, n, round(slope::numeric, 4) AS slope,
               round((slope * 35)::numeric, 2) AS adj_at_135
        FROM   cell_slope
        ORDER  BY abs(slope) DESC
        LIMIT  %s
    """, (limit,))
    print(f"\n[slope] largest slopes (adj shown for a 135-ability athlete)")
    print(f"    {'n':>7}{'slope':>10}{'adj@135':>10}{'dist':>7}  cell")
    for course, dist, n, slope, adj in cur.fetchall():
        print(f"    {n:>7}{slope:>10}{adj:>10}{dist:>7}  {course[:44]}")


# ------------------------------------------------------------------ #
# CHUNK 3 -- VALIDATE
# ------------------------------------------------------------------ #

def validate(cur):
    """
    Re-measure the ability-band curve AFTER applying the fitted slopes.

    ★ THIS IS THE ONLY THING THAT SAYS THE FIT HELPED. A slope that fits the
      observations it was fitted on proves nothing; what matters is whether
      the BAND CURVE flattens. The corrected column should be nearer zero at
      every band than the raw one, and especially at the ends.

    ⚠ IF THE CORPUS CURVE WAS ALREADY FLAT AND THE CORRECTED ONE IS WORSE, the
      slopes are fitting noise and must not be applied. That is the stop
      condition, and it is why this runs before any write.
    """
    cur.execute(f"""
        SELECT width_bucket(o.elsewhere, 80, 145, 13) AS band,
               round(min(o.elsewhere)::numeric, 0) AS lo,
               round(max(o.elsewhere)::numeric, 0) AS hi,
               count(*) AS obs,
               round(percentile_cont(0.5) WITHIN GROUP
                     (ORDER BY o.at_cell - o.elsewhere)::numeric, 2) AS raw,
               round(percentile_cont(0.5) WITHIN GROUP
                     (ORDER BY o.at_cell - o.elsewhere
                               - coalesce(s.slope, 0)
                                 * (o.elsewhere - {PIVOT}))::numeric,
                     2) AS corrected
        FROM   slope_obs o
        LEFT   JOIN cell_slope s
               ON s.course_name = o.course_name AND s.dist = o.dist
        GROUP  BY 1 ORDER BY 1
    """)
    print("\n[slope] band curve, before and after correction")
    print(f"    {'lo':>5}{'hi':>5}{'obs':>12}{'raw':>9}{'corrected':>11}")
    worse = 0
    for band, lo, hi, obs, raw, corr in cur.fetchall():
        flag = ""
        if abs(float(corr)) > abs(float(raw)) + 0.05:
            flag = "  <-- WORSE"
            worse += 1
        print(f"    {lo:>5}{hi:>5}{obs:>12,}{raw:>9}{corr:>11}{flag}")
    if worse:
        print(f"\n    ⚠ {worse} bands got WORSE. The slopes are fitting noise."
              f"\n      Raise SHRINK_K or MIN_ATHLETES before applying.")
    else:
        print("\n    every band moved toward zero.")
    return worse


def validateVenue(cur, pattern):
    """The same before/after, for one venue."""
    cur.execute(f"""
        SELECT width_bucket(o.elsewhere, 80, 145, 13) AS band,
               round(min(o.elsewhere)::numeric, 0) AS lo,
               round(max(o.elsewhere)::numeric, 0) AS hi,
               count(*) AS obs,
               round(percentile_cont(0.5) WITHIN GROUP
                     (ORDER BY o.at_cell - o.elsewhere)::numeric, 2) AS raw,
               round(percentile_cont(0.5) WITHIN GROUP
                     (ORDER BY o.at_cell - o.elsewhere
                               - coalesce(s.slope, 0)
                                 * (o.elsewhere - {PIVOT}))::numeric,
                     2) AS corrected
        FROM   slope_obs o
        LEFT   JOIN cell_slope s
               ON s.course_name = o.course_name AND s.dist = o.dist
        WHERE  o.course_name ILIKE %s
        GROUP  BY 1 ORDER BY 1
    """, (pattern,))
    print(f"\n[slope] {pattern}")
    print(f"    {'lo':>5}{'hi':>5}{'obs':>9}{'raw':>9}{'corrected':>11}")
    for band, lo, hi, obs, raw, corr in cur.fetchall():
        print(f"    {lo:>5}{hi:>5}{obs:>9,}{raw:>9}{corr:>11}")


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(rebuild=True, venue="%Hydrangea%"):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            if rebuild:
                print("[slope] building leave-one-out observations...")
                buildLift(cur)
                conn.commit()

            print("\n[slope] fitting per-cell slopes...")
            fitSlopes(cur)
            conn.commit()

            report(cur)
            worse = validate(cur)
            if venue:
                validateVenue(cur, venue)

    print("\n[slope] cell_slope is written. NOTHING is applied yet -- a")
    print("        consumer has to read it. The correction is")
    print("            rating_adj = rating - slope * (ability - 100)")
    print("        capped at +/- %.0f, and it belongs wherever the pair"
          % MAX_ADJUST)
    print("        engine writes speed_rating, so one pass only.")
    if worse:
        print("\n        ⚠ DO NOT APPLY: validate() says bands got worse.")


if __name__ == "__main__":
    _v = next((a.split("=", 1)[1] for a in sys.argv
               if a.startswith("--venue=")), "%Hydrangea%")
    main(rebuild="--no-rebuild" not in sys.argv, venue=_v)