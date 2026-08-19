# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/field_shift.py
# Purpose: find races where the WHOLE FIELD ran far better than its own
#          athletes normally do -- the signature of a corrupted ruler
#          (mislabeled distance, wrong course, bad conversion) rather than a
#          fast day.
#
#   WHY THE EXISTING DETECTORS MISSED THESE
#   ---------------------------------------
#   AIAL MS District, Byron P. Steele, 2025-10-11: every runner ~30 points
#   above their own career average. The winner rated 175.2 (8:54 for a claimed
#   3218m = 4:27/mile, from a 7th-8th grade field). The same meet at the same
#   venue in 2024 was 2253m and he rated 122.6.
#
#   It escaped every prior scan for two reasons, both of them about the
#   BASELINE rather than the statistic:
#
#     1. SEASON-SCOPED. Those athletes have exactly ONE race that season, so a
#        same-season baseline is the flagged race itself -> gap 0.
#     2. XC-ONLY. Their track races were never counted, halving everyone's
#        race count and starving the evidence gate.
#
#   Fixing both -- career-wide, both sports -- takes the corpus from ~1 usable
#   race per MS athlete-season to 9.29 per person. AIAL then ranks 25th.
#
#   ★ AND WHY A POOL-RELATIVE DETECTOR CANNOT WORK HERE. Earlier scans compared
#     a race against its POOL's baseline. A field that shifts UNIFORMLY looks
#     normal against a pool baseline that is polluted by the same kind of race.
#     Comparing each athlete to THEMSELVES is what makes a uniform shift
#     visible -- the confound cancels exactly rather than averaging out.

import os
import sys

# ★ scripts/ ON THE PATH AT IMPORT TIME, NOT INSIDE __main__. `database` and
#   `corrections` live there, and a module imported BY another script never
#   runs its own __main__ block -- so a path fix that only happens there works
#   when the file is run directly and fails when it is imported.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# ------------------------------------------------------------------ #
#  THRESHOLDS -- derived from the observed distribution, not chosen
# ------------------------------------------------------------------ #
#
# The median-gap histogram over 470k races decays geometrically (x0.52 per
# point) out to about +8, then FLATTENS into a shelf:
#
#        gap    races    ratio to previous
#       6->7    3,374    x0.52     smooth decay = normal racing
#       7->8    1,732    x0.51
#       8->9      958    x0.55
#       9->10     616    x0.64     <- decay breaking
#      12->13     162    x0.71
#      13->14     149    x0.92     <- flat
#      17->18      47    x1.02     <- flat
#
# ★ A FLAT SHELF SITTING ON A DECAYING MOUND IS A SECOND POPULATION. The
#   shoulder is where the ruler-corruption class begins, and it is ~+9.
#   This number was READ OFF THE DATA. The previous +15 was picked to make the
#   output a readable length, which is not a reason.
BASE_GAP = 9.0

# The gap threshold must SCALE WITH FIELD SIZE, and this is the real lesson
# rather than a refinement.
#
#   ah measures per-athlete week-to-week variation at 3.3-4.5 rating points
#   (same athlete, same venue, same season). So the noise on a MEDIAN of n
#   runners is about NOISE_PER_RUNNER / sqrt(n):
#
#        n= 10   noise ~1.3    a flat +9 is ~7 sigma
#        n=100   noise ~0.4    a flat +9 is ~22 sigma
#
#   A fixed cut is therefore simultaneously TOO LOOSE for small fields and TOO
#   STRICT for large ones. That is not a tuning problem -- it is the wrong
#   statistic, the same failure the c-threshold had.
NOISE_PER_RUNNER = 4.0
REF_N = 20          # field size at which the gate equals BASE_GAP exactly

# p10 -- the 10th percentile of the gap -- separates the two things a high
# median can mean:
#
#   whole field shifted   p10 tracks the median   -> RULER BUG
#   only the front moved  p10 collapses to ~0     -> strong field, or merged
#                                                    identities. NOT a ruler bug.
#
# Observed live: Jackson Hole HS med +36.2 / p10 +4.0, and Belmont Plateau
# med +24.8 / p10 -5.5, are both top-only and must not reach a correction list.
MIN_P10 = 8.0

# An athlete with one race has no baseline distinct from the race being tested.
MIN_BASELINE_RACES = 2

# Below this a median is one or two people's noise.
MIN_FIELD = 10

# ------------------------------------------------------------------ #
#  SMALL FIELDS
# ------------------------------------------------------------------ #
#
# * MIN_FIELD = 10 WAS THE ONLY THING HIDING SMALL MISLABELLED RACES. The sqrt
#   gate already handles them correctly on its own: it holds SIGNIFICANCE
#   constant, so it demands 12.7 at n=10, about 23 at n=3 and about 40 at n=1.
#   A tiny field is not a weaker test, it is a higher bar.
#
#   Luke Morelli's 2025-09-13 race is the case this exists for. His own season
#   reads 827.7s normalising to 1429 and 836.3s to 1442, both ratio 1.73, a
#   3000 scaled to 5K. The same meet's 808.0s normalised to 799, ratio 0.99,
#   and rated 153.9 against 85 to 94 everywhere else. A fifty point outlier,
#   invisible because almost nobody else in that division carried a rating.
#
# ! WHAT A SMALL FIELD LACKS IS CORROBORATION. Ten runners disagreeing with
#   their own careers is ten witnesses; three is three, and one thin baseline
#   can carry the median alone. So below MIN_FIELD every contributing athlete
#   must have a real career behind them, not the two races
#   MIN_BASELINE_RACES allows.
MIN_FIELD_SMALL = 3
MIN_BASELINE_SMALL = 5


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE BASELINE
# ------------------------------------------------------------------ #

def buildBaseline(cur):
    """
    person_baseline: one row per person, over BOTH sports, WHOLE career.

    ★ BOTH SPORTS, ONE UNION ALL OF PRE-AGGREGATED SCANS. Each table is read
      once and grouped; joining the raw tables would be 39M x 27M. The
      zero-free shape works because we only need per-person aggregates, not
      per-sport ones.

    ★ avg, NOT percentile_cont. A median is an ordered-set aggregate: Postgres
      must materialise and SORT every group. Measured on this corpus that plan
      was a blocking GroupAggregate whose Sort node more than doubled the total
      cost (1.49M -> 3.30M) with 45% of the work before the first row. avg
      hash-aggregates in one streaming pass.

      The robustness loss is acceptable HERE because this is the BASELINE, not
      the measurement: it is a central value over ~9 races per person, so one
      blow-up moves it slightly and in a direction uncorrelated with which race
      is being tested. The statistic that carries the finding is the per-race
      median in CHUNK 2, and that one stays a true median.

    ⚠ THE BASELINE CONTAINS THE RACE BEING TESTED. A runner whose 9 races
      include an inflated one has their average pulled UP, which SHRINKS the
      measured gap. Every number this script prints is therefore an
      UNDERSTATEMENT, never an invention -- conservative in the right
      direction. Leave-one-out would need a window function over 39M rows and
      buys accuracy we do not need to rank a worklist.
    """
    cur.execute("DROP TABLE IF EXISTS person_baseline")
    cur.execute("""
        CREATE TABLE person_baseline AS
        SELECT person_id,
               avg(speed_rating) AS career_avg,
               count(*)          AS n_races
        FROM (
            SELECT person_id, speed_rating FROM results
            WHERE person_id IS NOT NULL AND speed_rating BETWEEN 40 AND 200
            UNION ALL
            SELECT person_id, speed_rating FROM results_tf
            WHERE person_id IS NOT NULL AND speed_rating BETWEEN 40 AND 200
        ) x
        GROUP BY person_id
    """)
    cur.execute("CREATE INDEX ON person_baseline (person_id)")
    cur.execute("ANALYZE person_baseline")
    cur.execute("SELECT count(*), round(avg(n_races)::numeric,2) "
                "FROM person_baseline")
    people, avg_races = cur.fetchone()
    print(f"    {people:,} people, {avg_races} races each")
    return people


# ------------------------------------------------------------------ #
# CHUNK 2 -- THE GATE
# ------------------------------------------------------------------ #

def _gateSql(alias="n"):
    """
    The field-size-scaled threshold, as SQL.

        required_gap = BASE_GAP * sqrt(REF_N / n)

    Holds SIGNIFICANCE constant instead of holding the raw gap constant:
        n= 10  -> 12.7      n= 20 ->  9.0
        n= 50  ->  5.7      n=100 ->  4.0

    Extracted as a helper so the SELECT list and the HAVING clause use ONE
    expression. Two copies of a threshold is how a report ends up printing a
    number the filter did not actually apply.
    """
    return f"{BASE_GAP} * sqrt({REF_N}::float / {alias})"


def findShifts(cur, limit=None):
    """
    One row per race whose whole field beat its own athletes' careers.

    The gap is computed per ROW (athlete-relative), then aggregated per race.
    Doing it the other way round -- race mean vs pool mean -- is exactly the
    comparison that hides a uniform shift.
    """
    lim = f"LIMIT {int(limit)}" if limit else ""
    cur.execute(f"""
        WITH gapped AS (
            SELECT r.meet_id, r.div_id, r.date,
                   r.speed_rating - p.career_avg AS gap,
                   p.n_races                     AS baseline_races
            FROM   results r
            JOIN   person_baseline p ON p.person_id = r.person_id
            WHERE  r.speed_rating BETWEEN 40 AND 200
              AND  p.n_races >= {MIN_BASELINE_RACES}
        ),
        per_race AS (
            SELECT meet_id, div_id, date,
                   count(*) AS n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) AS med_gap,
                   percentile_cont(0.1) WITHIN GROUP (ORDER BY gap) AS p10,
                   min(baseline_races) AS min_baseline
            FROM   gapped
            GROUP  BY 1, 2, 3
            -- A small field is allowed through, but only when every athlete
            -- in it has a real career behind them. See MIN_FIELD_SMALL.
            HAVING (count(*) >= {MIN_FIELD}
                    OR (count(*) >= {MIN_FIELD_SMALL}
                        AND min(baseline_races) >= {MIN_BASELINE_SMALL}))
               AND percentile_cont(0.5) WITHIN GROUP (ORDER BY gap)
                     >= {_gateSql('count(*)')}
               AND percentile_cont(0.1) WITHIN GROUP (ORDER BY gap) >= {MIN_P10}
        )
        SELECT pr.meet_id, pr.div_id, pr.date,
               COALESCE(m.course_name, mt.venue_name)  AS course_name,
               COALESCE(
                   m.distance,
                   (mt.division_distances -> pr.div_id::text ->> 'distance')::real
               )                                       AS distance,
               pr.n,
               round(pr.med_gap::numeric, 1) AS med_gap,
               round(pr.p10::numeric, 1)     AS p10,
               round({_gateSql('pr.n')}::numeric, 1) AS required,
               -- ★ RANK BY ROWS AFFECTED, NOT BY SEVERITY. A +20 shift over
               --   100 runners corrupts ten times more of the corpus than a
               --   +40 over 10. A worklist is ordered by damage.
               round((pr.med_gap * pr.n)::numeric, 0) AS impact
        FROM   per_race pr
        -- ! LEFT JOIN, AND meets_tfrrs AS WELL. `meets` is ANET-ONLY, so an
        --   inner join here flagged a race by the aggregate and then threw it
        --   away because no metadata row could ever exist for it. Every tfrrs
        --   XC race was invisible to this scan and therefore to
        --   distance_repair, which is why meet 25531 div 1 -- 57 rows, source
        --   tfrrs, one athlete rating 153.9 against 85 to 94 in every other
        --   race that season -- survived twenty repair rounds untouched.
        --
        -- * AND THE TFRRS DISTANCE IS PER DIVISION, IN JSONB.
        --   meets_tfrrs.distance is null on essentially every row; the real
        --   value is in division_distances keyed on div_id AS TEXT. Reading
        --   the scalar instead is what once had a women's 5000 and a men's
        --   8000 at one meet sharing a distance.
        LEFT   JOIN meets m ON m.meet_id = pr.meet_id
                           AND m.div_id  = pr.div_id
        LEFT   JOIN meets_tfrrs mt ON mt.meet_id = pr.meet_id
                                  AND mt.sport   = 'XC'
        ORDER  BY impact DESC
        {lim}
    """)
    rows = cur.fetchall()
    print(f"\n[shift] {len(rows):,} races flagged")
    print(f"    {'date':<12}{'n':>5}{'med':>7}{'p10':>7}{'req':>7}"
          f"{'impact':>9}  {'dist':>7}  course")
    for meet, div, date, course, dist, n, med, p10, req, impact in rows[:60]:
        d = f"{dist:.0f}" if dist else "-"
        print(f"    {str(date):<12}{n:>5}{med:>7}{p10:>7}{req:>7}"
              f"{impact:>9}  {d:>7}  {(course or '?')[:44]}")
    return rows


# ------------------------------------------------------------------ #
# CHUNK 3 -- CALIBRATION
# ------------------------------------------------------------------ #

def shoulder(cur):
    """
    Print the median-gap histogram, so the threshold can be RE-DERIVED rather
    than inherited.

    ★ RUN THIS AFTER ANY ENGINE CHANGE. BASE_GAP is a property of the current
      rating distribution. Re-solve the engine and the shoulder moves; a
      constant that was measured once and then trusted forever is a constant
      that is now wrong.

    The NEGATIVE tail is the control. Racing is roughly symmetric, so a
    threshold catching far more races on the positive side than its mirror
    catches on the negative side is finding real anomalies rather than noise.
    """
    cur.execute(f"""
        WITH gapped AS (
            SELECT r.meet_id, r.div_id, r.date,
                   r.speed_rating - p.career_avg AS gap
            FROM   results r
            JOIN   person_baseline p ON p.person_id = r.person_id
            WHERE  r.speed_rating BETWEEN 40 AND 200
              AND  p.n_races >= {MIN_BASELINE_RACES}
        ),
        per_race AS (
            SELECT count(*) AS n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) AS med_gap
            FROM   gapped GROUP BY meet_id, div_id, date
            HAVING count(*) >= {MIN_FIELD}
        )
        SELECT floor(med_gap)::int AS g, count(*)
        FROM   per_race
        WHERE  med_gap BETWEEN -20 AND 25
        GROUP  BY 1 ORDER BY 1
    """)
    rows = cur.fetchall()
    print("\n[shift] median-gap histogram (decay ratio breaks at the shoulder)")
    prev = None
    for g, cnt in rows:
        ratio = f"x{cnt / prev:.2f}" if prev else ""
        mark = "  <-- shelf" if prev and cnt / prev > 0.75 and g > 0 else ""
        print(f"    {g:>4}  {cnt:>8,}  {ratio:>6}{mark}")
        prev = cnt


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(rebuild=True, calibrate=False, limit=None):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            if rebuild:
                print("[shift] building career baseline (both sports)...")
                buildBaseline(cur)
                conn.commit()
            if calibrate:
                shoulder(cur)
            findShifts(cur, limit=limit)


if __name__ == "__main__":

    _lim = next((int(a.split("=", 1)[1]) for a in sys.argv
                 if a.startswith("--limit=")), None)
    main(rebuild="--no-rebuild" not in sys.argv,
         calibrate="--calibrate" in sys.argv,
         limit=_lim)