# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/distance_repair.py
# Purpose: for a race flagged by field_shift, INFER the distance that would
#          explain the rating inflation, and if the venue actually uses that
#          distance, propose it as the correction.
#
#   THE IDEA
#   --------
#   A whole field beating its own careers by a constant amount is what a
#   mislabeled distance looks like. The inflation is not arbitrary -- it is a
#   deterministic function of how wrong the distance is, so it can be INVERTED.
#
#       normalized_time = raw_time * (5000 / d) ** k
#       rating          = 100 * pool_mean / ability,   ability ~ normalized_time
#
#   so if the claimed distance is rho times the true one:
#
#       rating_claimed / rating_true = rho ** k
#       =>  rho = (1 + med_gap / base) ** (1 / k)
#       =>  d_true = d_claimed / rho
#
#   ★ AND THEN THE PART THAT MAKES IT SAFE: SNAP TO A DISTANCE THE VENUE
#     ACTUALLY USES. The inferred number is approximate -- k varies by pool,
#     the baseline is contaminated (see below), and course difficulty absorbs
#     some of the error. On AIAL the inference lands near 2576 m while the
#     truth is 2253 m, which is 14% out. Far too loose to write directly.
#
#     But 2253 is a distance that venue DEMONSTRABLY RAN, in 2024, at the same
#     meet. The set of distances a course has actually used is small and
#     discrete, so snapping converts a fuzzy estimate into an exact answer --
#     and a race whose inference matches NO existing venue distance is simply
#     not proposed. The venue's own history is the ruler; the inference only
#     has to be good enough to pick the right entry from a short list.
#
#   ⚠ THIS PROPOSES, IT DOES NOT DECIDE. Default is a dry run that prints and
#     stores proposals. --write takes a backup first and the rollback is one
#     statement. Every proposal carries the evidence that produced it.

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
#  CONSTANTS
# ------------------------------------------------------------------ #

# The distance exponent, t ~ d**k. ah/faah record ~1.0707 for the fitted
# whole-corpus value; the real model is a per-pool spline, not one exponent.
#
# ★ THE APPROXIMATION IS TOLERABLE ONLY BECAUSE OF THE SNAP. k enters through
#   a 1/k power, so a 5% error in k moves the inferred distance by well under
#   5%. The snap tolerance below absorbs that. If this ever wrote distances
#   directly, k would have to come from the pool's own spline.
DISTANCE_EXPONENT = 1.0707

# How close the inferred distance must be to an existing venue distance.
# 0.15 is deliberately generous: the inference is the fuzzy part and the
# candidate list is short and discrete, so a wide window still lands on one
# answer. AMBIGUITY IS HANDLED BY REFUSING, not by picking the nearer one --
# see _snap.
# ⚠ 0.10, TIGHTENED FROM 0.15. At 15% the snap could land on a candidate that
#   moves the distance only a fraction of what the inference asked for, and
#   the resulting correction cannot explain the gap it was derived from:
#
#     Weston HS   claimed 5000, inferred 4214 (-15.7%), snapped 4828 (-3.4%)
#     Reid Park   claimed 5000, inferred 4481 (-10.4%), snapped 4828 (-3.4%)
#
#   Both would have been "corrected" by 3.4% to fix a +20 rating gap. If the
#   nearest real venue distance is that far from the inference, the venue has
#   no candidate that explains the anomaly and the honest answer is to propose
#   NOTHING -- the cause is something other than distance.
SNAP_TOLERANCE = 0.10

# A candidate distance must have been used this many times at the venue.
# One row is a typo; a distance a course ran 40 times is a real configuration.
MIN_CANDIDATE_ROWS = 20

# ★ A DISTANCE ERROR IS A PROPERTY OF ONE RACE DAY, NOT OF A CELL.
#   If most of a venue's days at the claimed distance are ALSO flagged, the
#   label is not what is wrong -- the cell's difficulty is, and re-labelling
#   the distance would move the ratings for a reason that is not true.
#
#     Sehmel Homestead Park, 2000 m: 21 race days over 8 years, 4,313 rows,
#     and most of the flagged Sehmel races are at it. The distance is the
#     venue's primary configuration. (Its real defect is a slope: fast runners
#     +8, slow runners -2. See engine/slope_fit.py.)
#
#     Byron P. Steele, 3218 m: eleven race days, ONE of them flagged (AIAL
#     2025-10-11, whose four divisions ran 534-746 s when every other 3218 day
#     at that venue has a winner between 680 and 890). That one day is
#     mislabelled; the other ten are fine.
#
#   So: propose only when the flagged day is a MINORITY of the venue's days at
#   that distance. Same shape as the rain course -- one day behaving
#   differently is a per-race defect, every day behaving the same way is a
#   per-cell one, and they have different fixes.
MAX_FLAGGED_SHARE = 0.5

# ★ ROWS ARE NOT ENOUGH -- THE HISTORY MUST BE SPREAD OUT.
#   A distance can carry thousands of rows and still be a single mislabeled
#   race day at a big meet. Snapping to it moves the error rather than fixing
#   it. A distance the venue uses REPEATEDLY, ACROSS SEASONS, is a real
#   configuration; one that appears on a day or two is itself suspect.
#
#   These killed the 5000 -> 4000 college cluster that survived two rounds of
#   review: Kenyon, Berea, Erskine, Rim Rock Farm and a dozen others were
#   being told they ran 4000 m. Colleges race 5000 and 8000.
# ⚠ 2, NOT 3. At 3 days the guard killed Byron P. Steele 3218 -> 2253 -- the
#   one proposal verified independently (the venue ran 2253 in 2024; the trace
#   reproduced the stored factor at 3218 and the correct one at 2253). A guard
#   that rejects the known-good case is calibrated wrong, whatever it does to
#   the rest. The YEAR span carries the real weight: two race days in two
#   different seasons means two independent scrapes agreed.
MIN_CANDIDATE_DAYS  = 2      # distinct race days at that venue
MIN_CANDIDATE_YEARS = 2      # spanning at least this many calendar years

# ★ A PROPOSAL MUST ACTUALLY CHANGE THE DISTANCE.
#   8000 -> 8046 and 3200 -> 3218 are the SAME distance restated in different
#   units (5 miles, 2 miles). A 0.6% change cannot explain a double-digit
#   rating gap, so a proposal that small is measuring something else.
MIN_CHANGE_FRAC = 0.02

# ★ TIERED CANDIDATES, BEST EVIDENCE FIRST. A hard "must have >= 2 days across
#   >= 2 years" guard rejected AIAL: Byron P. Steele ran 2253 on exactly ONE
#   race day (2024) and 3218 thereafter, so the correct answer had thin
#   history and the tool proposed nothing at all -- leaving a field 50 rating
#   points high rather than proposing the one distance the venue is known to
#   have run.
#
#   The guard was right about its target (it killed the 5000 -> 4000 college
#   cluster) and wrong to be absolute. Tiers keep both: a well-attested venue
#   distance still wins, a thin one is used only when nothing better exists,
#   and the standard list is the last resort rather than a competitor.
#
#     A  venue distance, >= MIN_CANDIDATE_DAYS days across >= MIN_CANDIDATE_YEARS
#     B  venue distance, >= MIN_CANDIDATE_ROWS rows, any history
#     C  standard cross country distances
#
#   ⚠ THE TIER IS REPORTED PER PROPOSAL. A tier-C pin is a guess about the
#     sport; a tier-A one is a fact about the course. They should not read the
#     same in the output or in corrections.py.
_STANDARD = (1609.0, 2000.0, 2253.0, 2414.0, 3000.0, 3218.0, 4000.0,
             4828.0, 5000.0, 6000.0, 6437.0, 8000.0)

# ★ HAND EXCLUSIONS. The inference can only ever produce a DISTANCE, so when a
#   race is anomalous for some OTHER reason it still gets a distance answer --
#   a confident, plausible-looking, wrong one. These are the venues where the
#   claimed distance is known-good and the anomaly is something else.
_EXCLUDE_VENUE = {
    # 4715 m is Mt. SAC's real course: 170,294 rows over 76 race days since
    # 1978. Proposing 4000 against that is the inference over-ruling the best-
    # measured distance in the corpus. Mt. SAC anomalies are the RAIN COURSE,
    # which is a separate cell and a separate fix.
    "Mt. San Antonio College",
    "Mt. San Antonio College (rain course)",
    "Mt. Sac",
}

# Specific (meet_id, div_id) -> distance, verified by hand and NOT reachable
# by the history guard because the venue lacks the required year span.
#
# ★ AIAL IS THE CALIBRATION CASE AND IT FAILS THE GUARD. Byron P. Steele ran
#   2253 in 2024 and 3218 in 2025; 2253 appears on ONE race day, so it can
#   never satisfy ">= 2 days across >= 2 years". Verified independently:
#   normalizeResult at 3218 reproduces the stored factor, at 2253 it gives the
#   correct one, and 8:54 for 3218 is 4:27/mile from a 7th-8th grade winner.
_MANUAL = {
    (271805, 1082943): 2253.0,
    (271805, 1082944): 2253.0,
}

# Gates inherited from field_shift, restated here so this script is readable
# alone. Changing them here does NOT change the detector.
# ★ THE FIELD FLOOR SLIDES WITH THE SIZE OF THE ANOMALY. A flat floor
#   excluded exactly the races that most need finding:
#
#     Byron P. Steele 2025-10-11, four divisions --
#       1082943   14 usable   med  -3.8   (pinned to 2253, now correct)
#       1082944   15 usable   med  +1.2   (pinned, now correct)
#       1082945    9 usable   med +45.2   <- EXCLUDED by a floor of 10
#       1082946    6 usable   med +43.2   <- EXCLUDED
#
#   Those are parochial middle schoolers whose whole racing history is this
#   one meet, so the leave-one-out baseline -- the thing that makes the gap
#   CORRECT -- also strips most of the field out of the usable count. The
#   population the tool is most needed for is the one a flat floor removes.
#
#   ⚠ AND A BIG GAP GENUINELY NEEDS FEWER RUNNERS. ah puts per-athlete
#     week-to-week variation at 3.3-4.5 points, so noise on a median of k is
#     about 4/sqrt(k): at k=6 that is 1.6 points, and a +43 median is ~26
#     sigma. Demanding ten runners for that is not caution, it is a floor
#     doing a job the gate already does better.
MIN_FIELD = 10

# Never below this, whatever the gap. Five runners can share a school, a bus
# and a bad day; below that a "field" is not a field.
ABS_MIN_FIELD = 5

# The gap at which the requirement reaches ABS_MIN_FIELD. Between MIN_P10 and
# here it slides linearly.
BIG_GAP = 25.0
MIN_P10 = 8.0
BASE_GAP = 9.0
REF_N = 20
MIN_BASELINE_RACES = 2


# ------------------------------------------------------------------ #
# CHUNK 1 -- INFERENCE (pure, testable)
# ------------------------------------------------------------------ #

def impliedRatio(base_rating, med_gap, k=DISTANCE_EXPONENT):
    """
    rho = claimed_distance / true_distance, from the rating inflation.

    rho > 1 means the claimed distance is TOO LONG, which is the case that
    inflates ratings -- the normalizer credits the field with ground they did
    not cover.

    base_rating is the field's own typical level (median career average), NOT
    a constant. A +20 gap means something different to a field averaging 90
    than to one averaging 150, because the relationship is multiplicative.
    """
    if not base_rating or base_rating <= 0:
        return None
    inflation = (base_rating + med_gap) / base_rating
    if inflation <= 0:
        return None
    return inflation ** (1.0 / k)


def inferDistance(claimed, base_rating, med_gap, k=DISTANCE_EXPONENT):
    """claimed / rho. None when the inputs cannot support an estimate."""
    rho = impliedRatio(base_rating, med_gap, k)
    if rho is None or rho <= 0 or not claimed or claimed <= 0:
        return None
    return claimed / rho


def _snap(inferred, candidates, tol=SNAP_TOLERANCE):
    """
    The candidate distance NEAREST the inference, within `tol`, or None.

    candidates: [(distance, n_rows), ...]

    ★ NEAREST WINS, NOT REFUSE-ON-TIE. The original rule abstained whenever
      two candidates fell inside the window, on the reasoning that choosing
      between them was guessing. That is right when the two are equally close
      and wrong when they are not -- and it cost the one case we verified by
      hand:

        AIAL, Byron P. Steele, 2025-10-11. Gap +45.1 inverts to 2262 m.
        The venue runs BOTH 2253 (the true distance, raced 2024-10-12) and
        2414. Both sit inside a 10% window, so the tie rule refused and left
        the race at 3218 -- worse than either candidate.

        |2253 - 2262| =   9 m
        |2414 - 2262| = 152 m

      Seventeen times closer. Calling that a tie discards a real measurement
      to avoid a decision that the numbers already make.

    ⚠ THE ABSTENTION STILL EXISTS, just at a different place: nothing inside
      the window still returns None, and MIN_CHANGE_FRAC still rejects a snap
      that barely moves the distance. What is gone is refusing when the
      inference has clearly picked a side.

    Ties are broken by row count -- if two candidates are equidistant, the one
    the venue ran more often is the better bet.
    """
    if inferred is None or inferred <= 0:
        return None
    near = [(abs(d - inferred), -n, d) for d, n in candidates
            if d > 0 and abs(d - inferred) / inferred <= tol]
    if not near:
        return None
    return min(near)[2]


# ------------------------------------------------------------------ #
# CHUNK 2 -- EVIDENCE FROM THE DATABASE
# ------------------------------------------------------------------ #

# ! ONE VIEW OVER BOTH SOURCES, BECAUSE `meets` IS ANET-ONLY. Three separate
#   inner joins to it meant every tfrrs race was dropped before this script
#   ever saw it: the flagged list, the venue history and the candidate list
#   all silently excluded them. Meet 25531 div 1 -- 57 rows, source tfrrs,
#   one athlete rating 153.9 against 85 to 94 in every other race that season
#   -- survived twenty repair rounds because of it, and field_shift had the
#   same bug.
#
# * THE TFRRS DISTANCE IS PER DIVISION, IN JSONB. meets_tfrrs.distance is null
#   on essentially every row; the real value is in division_distances keyed on
#   div_id AS TEXT. Reading the scalar instead once had a women's 5000 and a
#   men's 8000 at one meet sharing a distance.
_MEETS_ALL = """
    SELECT meet_id, div_id, course_name, distance FROM meets
    WHERE  course_name IS NOT NULL AND distance > 0
    UNION ALL
    SELECT mt.meet_id,
           d.key::int                                    AS div_id,
           mt.venue_name                                 AS course_name,
           (d.value ->> 'distance')::real                 AS distance
    FROM   meets_tfrrs mt
    CROSS  JOIN LATERAL jsonb_each(mt.division_distances) d
    WHERE  mt.sport = 'XC'
      AND  mt.venue_name IS NOT NULL
      AND  (d.value ->> 'distance')::real > 0
"""


def _flaggedSql():
    """
    The flagged set, with the field's BASE rating -- leave-one-out.

    ★ THE BASELINE MUST EXCLUDE THIS VENUE, AND THIS IS THE WHOLE BUG.
      person_baseline.career_avg includes an athlete's races AT the venue
      being tested. For a field whose careers are mostly races at that venue,
      the "normal" they are compared against already contains the inflation,
      so the measured gap is a FRACTION of the real one -- and the inversion
      then under-corrects the distance by the same fraction.

      AIAL, Byron P. Steele: measured gap ~30, which inverts to 2568 m. The
      venue ran 2253 in 2024. Reaching 2253 from 3218 needs rho = 1.428, i.e.
      a gap near 46 -- roughly what the leave-one-out figure gives once the
      athlete's own AIAL races stop propping up their baseline.

      Computed as (career_total - venue_total) / (career_n - venue_n): two
      sums, no self-join, same construction venue_slope uses.

    ★ base IS THE POOL PIVOT, 100, NOT THE FIELD'S MEDIAN. rating is
      100 * pool_mean / ability by definition, so the multiplicative
      relationship is anchored at 100. Using the field's own median makes the
      inferred distance depend on who showed up.
    """
    gate = f"{BASE_GAP} * sqrt({REF_N}::float / count(*))"
    return f"""
        WITH rated AS (
            SELECT r.meet_id, r.div_id, r.date, r.speed_rating,
                   -- ★ WINDOW FUNCTIONS, NOT TWO GROUP BYs AND TWO JOINS.
                   --   The leave-one-out baseline needs each athlete's career
                   --   total and their total at this venue. Computing those as
                   --   separate aggregates meant scanning ~34M rows, grouping
                   --   twice, then joining both results BACK to the same 34M
                   --   rows. As window functions they are computed in the pass
                   --   that is already happening -- no join, no second scan,
                   --   and the partitions Postgres needs are the same sort it
                   --   would have done for the GROUP BY anyway.
                   sum(r.speed_rating) OVER w_all  AS car_tot,
                   count(*)            OVER w_all  AS car_n,
                   sum(r.speed_rating) OVER w_ven  AS ven_tot,
                   count(*)            OVER w_ven  AS ven_n
            FROM   results r
            JOIN   ({_MEETS_ALL}) m
                   ON m.meet_id = r.meet_id AND m.div_id = r.div_id
            WHERE  r.speed_rating BETWEEN 40 AND 200
              AND  r.person_id IS NOT NULL
            WINDOW w_all AS (PARTITION BY r.person_id),
                   w_ven AS (PARTITION BY r.person_id, m.course_name)
        ),
        gapped AS (
            SELECT meet_id, div_id, date,
                   speed_rating - (car_tot - ven_tot) / (car_n - ven_n) AS gap
            FROM   rated
            WHERE  car_n - ven_n >= {MIN_BASELINE_RACES}
        )
        SELECT meet_id, div_id, date, count(*) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) AS med_gap,
               percentile_cont(0.1) WITHIN GROUP (ORDER BY gap) AS p10,
               100.0                                            AS base_rating
        FROM   gapped
        GROUP  BY 1, 2, 3
        HAVING count(*) >= greatest(
                   {ABS_MIN_FIELD},
                   least({MIN_FIELD},
                         {MIN_FIELD} - ({MIN_FIELD} - {ABS_MIN_FIELD})
                         * least(1.0, greatest(0.0,
                             (abs(percentile_cont(0.5) WITHIN GROUP
                                  (ORDER BY gap)) - {MIN_P10})
                             / ({BIG_GAP} - {MIN_P10})))))
           AND percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) >= {gate}
           AND percentile_cont(0.1) WITHIN GROUP (ORDER BY gap) >= {MIN_P10}
    """


def loadFlagged(cur):
    """
    One row per flagged race, with its claimed distance, course, and how many
    of the venue's days at that distance are ALSO flagged.

    `flag_share` is the discriminator between a mislabelled day and a
    mis-fitted cell -- see MAX_FLAGGED_SHARE.
    """
    cur.execute(f"""
        WITH f AS ({_flaggedSql()}),
        tagged AS (
            SELECT f.*, m.course_name, m.distance
            FROM   f
            JOIN   ({_MEETS_ALL}) m
                   ON m.meet_id = f.meet_id AND m.div_id = f.div_id
        ),
        alldays AS (
            SELECT m.course_name, m.distance,
                   count(DISTINCT r.date) AS days
            FROM   ({_MEETS_ALL}) m
            JOIN   results r ON r.meet_id = m.meet_id AND r.div_id = m.div_id
            GROUP  BY 1, 2
        ),
        flagdays AS (
            SELECT course_name, distance,
                   count(DISTINCT date) AS days
            FROM   tagged GROUP BY 1, 2
        )
        SELECT t.meet_id, t.div_id, t.date, t.n, t.med_gap, t.p10,
               t.base_rating, t.course_name, t.distance,
               (fd.days::float / a.days) AS flag_share,
               a.days AS venue_days
        FROM   tagged t
        JOIN   alldays  a  ON a.course_name = t.course_name
                          AND a.distance    = t.distance
        JOIN   flagdays fd ON fd.course_name = t.course_name
                          AND fd.distance    = t.distance
        ORDER  BY t.med_gap * t.n DESC
    """)
    rows = cur.fetchall()
    print(f"    {len(rows):,} flagged races with a course and a distance")
    return rows


def loadVenueDistances(cur, flagged):
    """
    {course_name: [(distance, n_rows), ...]} -- what each venue actually runs.

    ★ EXCLUDES THE FLAGGED RACE-DAYS THEMSELVES. A wrong distance must not be
      allowed to vote on what its own venue's distances are; if it did, every
      flagged race would find itself as a candidate and snap to the value we
      are trying to correct. This is the same exclusion that made the Mt. SAC
      year-over-year comparison meaningful.
    """
    bad_days = {(r[7], str(r[2])) for r in flagged}   # (course, date)

    # ★ THE FLAGGED DAY IS EXCLUDED FROM ITS OWN VENUE'S HISTORY. Without
    #   this a wrong distance votes for itself: it would appear as a
    #   candidate, snap to itself, and the proposal would be a no-op that
    #   looks like a confirmation.
    cur.execute("""
        SELECT m.course_name, m.distance,
               count(*)                          AS n,
               count(DISTINCT r.date)            AS days,
               count(DISTINCT substr(r.date,1,4)) AS years
        FROM   (""" + _MEETS_ALL + """) m
        JOIN   results r ON r.meet_id = m.meet_id AND r.div_id = m.div_id
        GROUP  BY 1, 2
        HAVING count(*) >= %s
    """, (MIN_CANDIDATE_ROWS,))

    out = {}
    for course, dist, n, days, years in cur:
        out.setdefault(course, []).append(
            (float(dist), int(n), int(days), int(years)))
    print(f"    {len(out):,} venues with candidate distances "
          f"({len({c for c, _ in bad_days}):,} appear in the flagged set)")
    return out


# ------------------------------------------------------------------ #
# CHUNK 3 -- PROPOSALS
# ------------------------------------------------------------------ #

def buildProposals(flagged, venue, tiers=None, explain=None):
    """
    Zip the inference and the snap together. Returns (proposals, ledger).

    ★ THE LEDGER RECORDS WHY EACH RACE WAS *NOT* PROPOSED. Nothing vanishes
      silently -- a race with no venue history and a race whose inference was
      ambiguous are different problems, and the counts tell you which you have.
    """
    proposals, ledger = [], {}
    tiers = set(tiers) if tiers else None

    def note(reason):
        ledger[reason] = ledger.get(reason, 0) + 1

    for (meet_id, div_id, date, n, med_gap, p10, base, course, claimed,
         flag_share, venue_days) in flagged:
        claimed = float(claimed)

        # ★ MOST OF THE VENUE'S DAYS AT THIS DISTANCE ARE ALSO FLAGGED, so
        #   the label is not the defect -- the cell is. Re-labelling would
        #   move ratings for a reason that is not true.
        if venue_days >= 3 and flag_share > MAX_FLAGGED_SHARE:
            note(f"{flag_share:.0%} of this venue+distance's days are also "
                 f"flagged -- cell defect, not a label error")
            continue
        inferred = inferDistance(claimed, float(base or 0), float(med_gap))
        if inferred is None:
            note("no inference (bad base rating)")
            continue

        if course in _EXCLUDE_VENUE:
            note("venue hand-excluded (claimed distance known-good)")
            continue

        allc = venue.get(course, [])

        if explain is not None and meet_id == explain:
            print(f"\n  [explain] meet {meet_id} div {div_id} {date}")
            print(f"      n={n}  med_gap={float(med_gap):+.1f}  "
                  f"p10={float(p10):+.1f}  claimed={claimed:.0f}  "
                  f"inferred={inferred:.0f}")
            print(f"      snap window "
                  f"{inferred*(1-SNAP_TOLERANCE):.0f}-"
                  f"{inferred*(1+SNAP_TOLERANCE):.0f}")
            print(f"      flag_share={float(flag_share):.0%} of "
                  f"{venue_days} venue days at {claimed:.0f}")
            print(f"      venue history:")
            for d, nn, dd, yy in sorted(allc):
                mark = ("  <-- IN WINDOW"
                        if abs(d - inferred) / inferred <= SNAP_TOLERANCE
                        else "")
                a = "A" if (dd >= MIN_CANDIDATE_DAYS
                            and yy >= MIN_CANDIDATE_YEARS) else "B"
                print(f"        {d:>7.0f}  {nn:>6} rows  {dd:>3}d  {yy:>2}y "
                      f" tier{a}{mark}")

        # TIER A -- venue distances with repeated, multi-season history.
        tier = "A"
        cands = [(d, n) for d, n, days, years in allc
                 if abs(d - claimed) > 1
                 and days  >= MIN_CANDIDATE_DAYS
                 and years >= MIN_CANDIDATE_YEARS]
        snapped = _snap(inferred, cands)

        # TIER B -- any distance the venue has on record. Thin history, but a
        # distance this course DID run beats a guess about the sport.
        if snapped is None:
            tier = "B"
            cands = [(d, n) for d, n, days, years in allc
                     if abs(d - claimed) > 1]
            snapped = _snap(inferred, cands)

        # TIER C -- standard cross country distances. Last resort: the venue
        # cannot tell us, so the sport does.
        if snapped is None:
            tier = "C"
            snapped = _snap(inferred, [(d, 0) for d in _STANDARD
                                       if abs(d - claimed) > 1])

        if False:
            print(f"\n  [explain] meet {meet_id} div {div_id} {date}")
            print(f"      n={n}  med_gap={float(med_gap):+.1f}  "
                  f"p10={float(p10):+.1f}  claimed={claimed:.0f}")
            print(f"      inferred={inferred:.0f}  "
                  f"window {inferred*(1-SNAP_TOLERANCE):.0f}"
                  f"-{inferred*(1+SNAP_TOLERANCE):.0f}")
            print(f"      venue history (dist, rows, days, years):")
            for d, nn, dd, yy in sorted(allc):
                mark = ""
                if abs(d - inferred) / inferred <= SNAP_TOLERANCE:
                    mark = "  <-- IN WINDOW"
                a = "A" if (dd >= MIN_CANDIDATE_DAYS
                            and yy >= MIN_CANDIDATE_YEARS) else "B"
                print(f"        {d:>7.0f}  {nn:>6}  {dd:>3}d  {yy:>2}y "
                      f" tier{a}{mark}")
            print(f"      -> snapped={snapped}  tier={tier}")

        if snapped is None:
            note("no candidate within the snap window at any tier")
            continue

        # ★ TIER FILTER. A is a fact about this course, B is a thin fact about
        #   it, C is a guess about the sport -- and C is where the bad
        #   proposals live (Sehmel 2000->1609 at a venue with 21 days at 2000;
        #   Mount Tahoma 1609->1236, a distance nobody runs). Writing A and B
        #   only keeps every proposal that is grounded in what the venue
        #   actually ran.
        if tiers is not None and tier not in tiers:
            note(f"tier {tier} excluded by --tier")
            continue

        if abs(snapped - claimed) / claimed < MIN_CHANGE_FRAC:
            note(f"proposed change under {MIN_CHANGE_FRAC:.0%} "
                 f"(same distance, different units)")
            continue
        if abs(snapped - claimed) < 1:
            note("snapped back to the claimed distance")
            continue

        proposals.append({
            "meet_id": meet_id, "div_id": div_id, "date": str(date),
            "course": course, "n": n, "med_gap": float(med_gap),
            "p10": float(p10), "base": float(base),
            "claimed": claimed, "inferred": inferred, "proposed": snapped,
            "err_pct": 100.0 * abs(snapped - inferred) / inferred,
            "tier": tier,
        })
    # ★ MANUAL ENTRIES ARE ADDED AFTER, AND OVERWRITE. If the inference also
    #   produced a proposal for one of these, the hand-verified value wins --
    #   a measured, traced answer outranks an inferred one.
    seen = {(p["meet_id"], p["div_id"]) for p in proposals}
    for (mid, did), dist in _MANUAL.items():
        proposals = [p for p in proposals
                     if (p["meet_id"], p["div_id"]) != (mid, did)]
        proposals.append({
            "meet_id": mid, "div_id": did, "date": "hand", "course": "MANUAL",
            "n": 0, "med_gap": 0.0, "p10": 0.0, "base": 0.0,
            "claimed": 0.0, "inferred": dist, "proposed": dist,
            "err_pct": 0.0, "tier": "M"})
    if _MANUAL:
        print(f"    + {len(_MANUAL)} hand-verified entries")

    return proposals, ledger


def report(proposals, ledger):
    print(f"\n[repair] {len(proposals):,} proposals")
    print(f"    {'date':<12}{'n':>5}{'med':>7}{'claimed':>9}"
          f"{'inferred':>10}{'proposed':>10}{'err%':>7}{'t':>3}  course")
    for p in proposals[:80]:
        print(f"    {p['date']:<12}{p['n']:>5}{p['med_gap']:>7.1f}"
              f"{p['claimed']:>9.0f}{p['inferred']:>10.0f}"
              f"{p['proposed']:>10.0f}{p['err_pct']:>7.1f}"
              f"{p.get('tier','?'):>3}  {p['course'][:38]}")
    print("\n[repair] not proposed:")
    for reason, cnt in sorted(ledger.items(), key=lambda kv: -kv[1]):
        print(f"    {cnt:>6,}  {reason}")


# ------------------------------------------------------------------ #
# CHUNK 4 -- WRITE
# ------------------------------------------------------------------ #

_BLOCK_MARK = "# ==== AUTO: distance repairs"


def write(cur, proposals):
    """
    Append the repairs to _DISTANCE_OVERRIDES_XC in corrections.py.

    ★ corrections.py, NOT meets.distance. An UPDATE to meets is undone by the
      next scrape of that meet -- and these are meets whose SOURCE has the
      wrong distance, so re-scraping is exactly what puts it back. The
      override table is read FIRST in _resolveDistanceGender's chain, before
      the meets table, which is what makes a correction stick.

      AIAL MS District is the case that proves it: meets.distance says 3218,
      the venue ran 2253 in 2024, and 8:54 for 3218 is 4:27/mile from a
      7th-8th grade winner. Nothing in the pipeline disputes meets.distance
      except an override.

    ★ APPEND, DO NOT EDIT THE DICT LITERAL. corrections.py is ~1.45M lines and
      declares _DISTANCE_OVERRIDES_XC in two dozen places. A block at the end
      runs after all of them, whatever they are, and touches no existing line.
      Reverting is deleting the block.

    ⚠ ASSIGNMENT, NOT pop(). The retraction block written by
      audit_distance_overrides.py REMOVES keys; this one SETS them. If a meet
      appears in both, ordering decides -- so this block must be appended
      AFTER any retraction block, which it is by construction since it is
      appended last.
    """
    if not proposals:
        print("\n[repair] nothing to write")
        return

    import datetime
    stamp = datetime.date.today().isoformat()
    lines = [
        "",
        "",
        f"{_BLOCK_MARK}, {stamp} ====",
        "# Written by engine/distance_repair.py --write.",
        "#",
        "# Each meet below had a whole field beat their own career averages,",
        "# and the inflation inverts to a distance the VENUE ITSELF has run in",
        "# other years. The inference is only accurate to ~15%; the venue's own",
        "# distance history supplies the precision. A race whose inference",
        "# matched no existing venue distance, or matched two, is not here.",
        "#",
        "#   rating_claimed / rating_true = rho ** k",
        "#   rho = (1 + med_gap / base) ** (1/k),  k = 1.0707",
        "#   d_true = d_claimed / rho, then SNAPPED to a venue distance",
        "#",
        "# Set here rather than in meets.distance: the override is read before",
        "# the meets table, so it survives a re-scrape of the source that",
        "# carries the wrong number.",
        "_DISTANCE_OVERRIDES_XC.update({",
    ]
    for p in sorted(proposals, key=lambda q: -q["n"]):
        lines.append(
            f"    ({p['meet_id']}, {p['div_id']}): {p['proposed']:.0f},".ljust(34)
            + f"# was {p['claimed']:.0f}, inferred {p['inferred']:.0f}"
              f" (err {p['err_pct']:.0f}%, tier {p.get('tier','?')}),"
              f" gap {p['med_gap']:+.1f},"
              f" n={p['n']}  {p['date']}  {p['course'][:28]}")
    lines += [
        "})",
        f"# ==== END AUTO block {stamp} ====",
        "",
    ]
    block = "\n".join(lines)

    import corrections
    import inspect
    path = inspect.getfile(corrections)
    bak = path + ".bak"
    if not os.path.exists(bak):
        import shutil
        shutil.copy2(path, bak)
        print(f"\n[repair] backup: {bak}")
    else:
        print(f"\n[repair] backup already exists, kept: {bak}")

    with open(path, "a", encoding="utf-8") as fh:
        fh.write(block)
    print(f"[repair] appended {len(proposals)} overrides to {path}")

    # ★ VERIFY BY RE-IMPORTING. "wrote N lines" is not evidence that N
    #   overrides are live -- a syntax error or a later reassignment would
    #   swallow them silently.
    import importlib
    before = len(corrections._DISTANCE_OVERRIDES_XC)
    importlib.reload(corrections)
    after = len(corrections._DISTANCE_OVERRIDES_XC)
    live = sum(1 for p in proposals
               if corrections._DISTANCE_OVERRIDES_XC.get(
                   (p["meet_id"], p["div_id"])) == p["proposed"])
    print(f"[repair] _DISTANCE_OVERRIDES_XC: {before:,} -> {after:,}; "
          f"{live}/{len(proposals)} of the new values are live")
    if live != len(proposals):
        print("    ⚠ some values did not take -- a later block may override "
              "them, or a key was already present with a different value.")


def revert():
    """Delete the most recent repair block. One statement, no DB."""
    import corrections
    import inspect
    path = inspect.getfile(corrections)
    src = open(path, encoding="utf-8").read()
    if _BLOCK_MARK not in src:
        print("[repair] no repair block found")
        return
    cut = src.rindex(_BLOCK_MARK)
    open(path, "w", encoding="utf-8").write(src[:cut])
    print(f"[repair] removed the last repair block from {path}")


# ------------------------------------------------------------------ #
# CHUNK 5 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(live=False, tiers=None, explain=None):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            print("[repair] loading flagged races...")
            flagged = loadFlagged(cur)
            print("[repair] loading venue distance history...")
            venue = loadVenueDistances(cur, flagged)

            # ! tiers AND explain WERE ACCEPTED AND THEN DROPPED. main took
            #   both and passed neither, so --tier=AB silently did nothing and
            #   every run wrote tier C. That is how (183600, 748575): 1609
            #   reached corrections.py -- a tier C snap at 4% error that made
            #   550 seconds rate 218, and it was appended six times because
            #   nothing ever filtered it out.
            proposals, ledger = buildProposals(flagged, venue,
                                               tiers=tiers, explain=explain)
            report(proposals, ledger)

            if live and proposals:
                write(cur, proposals)
                conn.rollback()          # nothing to commit: the fix is in
                                         # corrections.py, not the database
                print("\n[repair] WRITTEN to corrections.py. "
                      "Re-normalize and re-solve.")
            else:
                conn.rollback()
                print("\n[repair] DRY RUN -- pass --write to apply")


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert()
    else:
        _t = next((a.split("=",1)[1] for a in sys.argv
                   if a.startswith("--tier=")), None)
        _e = next((int(a.split("=",1)[1]) for a in sys.argv
                   if a.startswith("--explain=")), None)
        main(live="--write" in sys.argv,
             tiers=list(_t.upper()) if _t else None,
             explain=_e)