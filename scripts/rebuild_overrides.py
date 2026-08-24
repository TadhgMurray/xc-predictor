# ⚠ NEAREST IS THE WRONG RULE WHEN THE RUNGS ARE NOT EQUALLY REAL.
#
#   An implied 5180 sits 2.4% from 5310 and 3.6% from 5000. Nearest picks
#   5310 -- which the corpus raced 5,345 times, against 5000's 19,505,087. A
#   race is not a 5310 because a number landed slightly closer to it.
#
# ★ SO THE SNAP WEIGHS FREQUENCY AGAINST ERROR. Score each rung by how often
#   it is raced, discounted by how far the implied value sits from it:
#
#       score = log10(n) * exp(-(err / SNAP_SIGMA)^2 / 2)
#
#   The Gaussian is what stops this becoming "always answer 5000": at 20%
#   away the discount is e^-22, so a rung has to be genuinely close before
#   its popularity counts for anything.
#
# ! log10(n), NOT n. Weighting by the raw count is too strong -- an implied
#   4850 would snap to 5000 over 4828 (three miles) even though 4828 is
#   nearly exact, because 5000 is five times commoner. log10 lets popularity
#   break a tie without overruling a good fit:
#
#       implied 5180  ->  5000  (5310 loses despite being nearer)
#       implied 4850  ->  4828  (5000 loses despite being commoner)
#       implied 4000  ->  4000  (nothing else is close enough to matter)
#   SNAP_SIGMA is 0.045 and that was set from cases, not taste. At 0.03 the
#   error term is so sharp that popularity cannot reach across 5%: an implied
#   5250 went to 5310 (5,345 finishers, 1.1% away) over 5000 (19.5M, 5%
#   away). 0.045 is the widest value that still lets an almost-exact fit win
#   -- 4850 stays on 4828 rather than sliding to the commoner 5000.
SNAP_SIGMA = 0.045

# Past this the implied value is not near anything and no weighting saves it.
SNAP_MAX_ERR = 0.12


def snapToCorpus(implied):
    """(distance, fractional error), weighing how often each rung is raced.

    Still a TEST rather than a rounding: nothing within SNAP_MAX_ERR means no
    answer, and the error it returns is what the caller judges.
    """
    import math
    if not implied or not _CORPUS_LADDER:
        return None
    best, best_score = None, None
    for d, n in _CORPUS_LADDER:
        err = (implied - d) / d
        if abs(err) > SNAP_MAX_ERR:
            continue
        score = math.log10(max(n, 10)) * math.exp(
            -(err / SNAP_SIGMA) ** 2 / 2.0)
        if best_score is None or score > best_score:
            best, best_score = (float(d), err), score
    return best


# Project: xc-predictor / scripts
# File:    rebuild_overrides.py
# Purpose: Rebuild the distance and result overrides from nothing, in three
#          passes, judging everything against ONE measurement: how far a rating
#          sits from that athlete's own median.
#
#     python scripts/rebuild_overrides.py --measure          # DO THIS FIRST
#     python scripts/rebuild_overrides.py --pass 1 --sweep
#     python scripts/rebuild_overrides.py --pass 1 --out pass1.py
#     python scripts/rebuild_overrides.py --pass 2 --out pass2.py
#     python scripts/rebuild_overrides.py --pass 3 --out pass3.py
#
# ============================================================================
# THE ONE MEASUREMENT
# ============================================================================
#   gap = this row's speed_rating - the median speed_rating of that athlete's
#         other rated races, any season, any distance, any venue.
#
# Nothing else. No distance-class baseline, no pool, no normalization, no
# minimum rated field size, no join to an existing override. Those are what
# every previous tool used, and every one of them walked past 26359/0 -- a
# division with 108 finishers, 6 ratings, and a field 50 points over its own
# heads -- because each one required something the division did not have.
#
# ⚠ THE ROW BEING JUDGED IS IN ITS OWN MEDIAN, DELIBERATELY. Four races at 95
#   and one at 145 still medians to 95. A MEAN would read 105 and shrink a
#   50-point gap to 40. Excluding it per-row costs a lateral over 34M rows to
#   buy what the median gives free.
#
# ============================================================================
# WHY THE THREE PASSES RUN IN THIS ORDER
# ============================================================================
#   1  the whole division is wrong      -> one distance for the division
#   2  the division holds two races     -> per-row distance + gender
#   3  one row is corrupt               -> drop that row
#
# 1 BEFORE 2, because pass 1's unanimity gate is what routes work to pass 2.
#   A division holding boys 8K and girls 5K under one label has HALF its field
#   wrong, so pass 1 declines by construction and hands it down. Run 2 first
#   and it splits divisions that are uniformly wrong -- both halves shifted
#   identically -- writing per-row pins where one number would do.
#
# 3 LAST, and this is the load-bearing one. A division-level fault makes EVERY
#   row look individually corrupt. Run 3 first on Ox Bow Park and it pins 108
#   rows instead of writing (26359,0): 5000. Pass 3 only means anything once
#   everything explicable at the group level has been explained.
#
# ⚠ 2 AND 3 JUDGE POST-PASS-1 RATINGS, WITHOUT A RE-SOLVE. An override
#   multiplies the field's normalized time by (d_old/d_new)^K, and rating is
#   inversely proportional to it, so the predicted rating is
#       rating * (d_new / d_old) ** K
#   which is the same arithmetic audit_overrides uses for `after`. Chain the
#   passes on predicted ratings, run the engine once, then run this again to
#   convergence -- the second run should find far less than the first.
#
# ============================================================================
# THE THRESHOLDS, AND WHY THEY DIFFER PER PASS
# ============================================================================
# The floor is measured, not chosen. 08_golive prints the spread of one
# athlete's ratings within one season, same venue: mean_sd 3.44 to 4.59 across
# every rating band, over 5.6M athlete-seasons. So SIGMA ~ 4.0 points is
# irreducible noise. --measure recomputes it from the live corpus.
#
# One flat bar for all three passes is wrong, because the passes hold wildly
# different amounts of evidence:
#
#   pass 1   N athletes agreeing.  The standard error of a field median is
#            1.25*sigma/sqrt(N) -- at N=20 that is 1.1 points, so a 10-point
#            field median is NINE standard errors. A whole field does not
#            drift 10 points by chance. Bar: 2.5 sigma.
#
#   pass 2   the same, on each half. Bar: 2.5 sigma BETWEEN the halves.
#
#   pass 3   ONE observation, which must survive sigma alone. 30 points is
#            7.5 sigma; below that, real breakthroughs start being condemned.
#            Bar: 7.5 sigma, AND the row must be 5 sigma from its own
#            division's median gap -- otherwise it is the division that is
#            wrong, not the row, and pass 1 or 2 should have taken it.
#
# ! A FLAT 50 WOULD BE 12 SIGMA AND IS TOO HIGH. The Ox Bow Park field sits at
#   about 49.9 over its own heads: a 50-point bar misses it by a tenth of a
#   point. That is what a threshold set by feel rather than by sigma does.
#
# ============================================================================
# PASS 2'S MINORITY GATE IS ABSOLUTE, NOT A PERCENTAGE
# ============================================================================
# A percentage scales the wrong way. 10% of a 200-runner division is 20 rows
# (plenty); 10% of a 20-runner division is 2 (useless). What decides whether a
# minority's median means anything is its COUNT: the standard error of a
# median is 1.25*sigma/sqrt(n), so
#
#       n=3 -> SE 2.9      n=5 -> SE 2.2      n=10 -> SE 1.6
#
# and a 10-point difference between halves is 4.2 SE at n=5 but only 3.3 at
# n=3. Five is where it stops being a coin flip. --min-minority is that floor.
#
# ⚠ AND MIXED GENDER IS NOT ITSELF THE SIGNAL. Divisions legitimately hold
#   both sexes racing the same distance. The signal is the two halves
#   DISAGREEING by more than the bar; the count gate only decides whether the
#   disagreement is measurable.

import argparse
import io
import os
import sys

from psycopg2.extras import RealDictCursor

sys.path.insert(0, "engine")
sys.path.insert(0, "scripts")

from database import getConn                    # noqa: E402
from audit_overrides import snapToLadder, K   # noqa: E402


_TABLE = {"XC": "results", "TF": "results_tf"}

SIGMA = 4.0             # measured; --measure recomputes it
T1_SIGMA = 2.5          # pass 1, on the field median gap
T2_SIGMA = 2.5          # pass 2, between the two halves
T3_SIGMA = 7.5          # pass 3, on one row
T3_VS_FIELD_SIGMA = 5.0  # pass 3, that row against its own division
UNANIMITY = 0.75        # pass 1: share of the field on the same side
# ⚠ 4, NOT 8, AND THE BAR MOVES INSTEAD. A flat minimum field is the same
#   class of gate that hid 26359/0 from every earlier tool: that division has
#   108 finishers and SIX ratings, because the other 102 were dropped upstream
#   for being impossible. Requiring 8 RATED rows re-created the exact blind
#   spot this rebuild exists to close.
#
#   The fix is to make the threshold depend on the evidence rather than
#   refusing to look. SE of a field median is 1.25*sigma/sqrt(N), so
#
#       bar(N) = max(absolute_bar, SE_K * 1.25 * sigma / sqrt(N))
#
#   holds the false positive count near-constant across every field size --
#   at SE_K=4 it is about 31 divisions out of 493,029 whether N is 4 or 100.
#   Small fields need a bigger gap to clear it, which is exactly right: they
#   carry less evidence.
MIN_FIELD = 4           # pass 1/2: rated rows needed to judge a division
SE_K = 4.0              # ...and the gap must also clear this many SEs
MIN_MINORITY = 5        # pass 2: rated rows needed in the smaller half
MIN_OWN_RACES = 3       # races an athlete needs for their median to mean this

# ⚠ ONE SIGMA DOES NOT FIT EVERY ATHLETE. The pooled sigma (~4.5) is the
#   AVERAGE athlete's race-to-race scatter; an elite runs within a point or
#   two of himself, so pass 3's t3*sigma bar is ~7.5 sigma of the average kid
#   and ~15 of the elite's -- leaving the pass blind to corruption on exactly
#   the rows the boards display. Pass 3's FAST side therefore uses the
#   athlete's own scatter (1.4826 * MAD of their rating history, >= 6 races),
#   clamped: floored here so three identical early ratings cannot collapse
#   the bar to zero, and capped at the pooled sigma so a personal bar only
#   ever TIGHTENS. The slow side keeps the pooled bar on purpose -- elites
#   jog easy races deliberately (pacing a teammate, a workout race is -20
#   "points" and real), nobody accidentally runs FASTER than their fitness.
#
# ! AND ONLY PASS 3. Passes 1 and 2 judge FIELD MEDIANS, where n already
#   does the noise reduction (barFor takes 4 SEs of the median), and their
#   absolute floor exists for SYSTEMATIC day effects -- mud, heat, a
#   re-routed course move every athlete the same ~5-10 points however
#   consistent each one is. A field of four metronomic elites has a razor
#   median and a muddy day still shifts it 8 points; tightening their bar
#   would catch weather, not distance. Per-athlete sigma helps exactly
#   where n = 1 and nothing averages out.
SIG_FLOOR = 1.5         # min per-athlete sigma; fast bar bottoms at t3*this
SPREAD_MIN_RACES = 6    # races needed before the personal sigma is trusted
MED_SANE_LO = 40.0      # an own-median outside this range is ITSELF the
MED_SANE_HI = 150.0     # corrupt thing; its rows are never drop candidates
# ! HI IS SET FROM THE CEILING, NOT FROM COMFORT. The engine's elite ceiling
#   is ~146 for a single career-best PERFORMANCE, so a career MEDIAN above
#   150 is not an athlete -- it is a person-merge. 200 let that class
#   through: the 2026-08-24 20k run's slow-side drop tail was full of rows
#   like "97.2 vs own median 175.1", deleting what is probably the merged-in
#   real kid's real race. Those belong in the broken-median unlink queue.
# ! 0.03, NOT 0.06, BECAUSE THE LADDER IS DENSER THAN THAT. 1931 / 2000 /
#   2011 sit inside 4% of each other, and 2400/2414, 3200/3218, 4800/4828,
#   8000/8047 are all under 1% apart. At 6% an implied 2131 -- which is
#   BETWEEN the 2011 and 2400 rungs, i.e. not a race distance at all --
#   "snaps" to 2011 and gets written. A field landing between rungs is
#   telling you it is wrong for a reason other than distance, and the snap
#   error is the only thing that says so.
#
#   The precision argues for it too: the SE of a field median is about 2% of
#   a 100-rating base, and distance error is that divided by K, so a
#   correctly-labelled field lands within about 2% of its rung. 3% is a
#   little over one SE; 6% is three, wide enough to catch anything.
SNAP_TOL = 0.03         # implied distance must land near a real rung
# ⚠ AND A CAP ON THE MOVE. 1609 -> 3200 is a doubling; it happens (a mile
#   label on a two-mile race) but it is also what a badly-broken field looks
#   like, and the two are indistinguishable from the gap alone. Anything past
#   this is reported rather than written.
MAX_CHANGE = 2.0


# ------------------------------------------------------------------ #
#  THE GAP TABLE -- every pass reads this and nothing else
# ------------------------------------------------------------------ #
#
# ! ONE TEMP TABLE, NOT A CTE REPEATED THREE TIMES. The per-athlete median is
#   the expensive half and all three passes need the identical one; computing
#   it per pass would triple the cost and, worse, let the passes drift apart.
# ------------------------------------------------------------------ #
#  --as-if-wiped -- PROPOSE AGAINST THE SLATE THE WIPE WOULD LEAVE
# ------------------------------------------------------------------ #
#
# ⚠ THE HOLE IN "RESET, THEN REBUILD", AND IT IS NOT SMALL. The ratings in the
#   database were solved WITH the current overrides. So a division whose
#   override is CORRECT sits perfectly on its athletes' own heads -- gap ~ 0 --
#   and pass 1 declines it, correctly, because there is nothing wrong with it.
#
#   Wipe afterwards and that override is gone, with nothing proposed to replace
#   it, and the division silently reverts to the scraped distance the override
#   existed to correct. Every override that was RIGHT is lost, and only the
#   wrong ones get rebuilt. That is the exact opposite of the intent.
#
# ★ SO THE PASSES JUDGE THE RATINGS THE WIPE WOULD PRODUCE, NOT THE ONES ON
#   DISK. Reverting a division from d_ovr to d_scraped multiplies its ratings
#   by (d_scraped / d_ovr) ** K -- the same first-order arithmetic the passes
#   already use to chain 2 and 3 onto pass 1, and the same one audit_overrides
#   uses for `after`. Under it, a correct override becomes a large gap and
#   pass 1 re-proposes it from the same evidence that justified it originally.
#
# ! SOLE SOURCES ARE LEFT ALONE, because wipe_overrides --keep-sole-source
#   leaves them alone. Nothing else carries a distance for those divisions, so
#   there is no scraped value to revert to and their ratings do not move. Run
#   scripts/census_override_sources.py to see both counts.
#
# ⚠ IT IS FIRST ORDER. Reverting the distances would also move the pool means
#   and every cell delta a little, and this does not model that. It is the
#   approximation this whole tool is built on; the header's advice stands --
#   run the engine once, then run this again, and the second pass should find
#   far less than the first.
_UNOVERRIDE_SQL = """
DROP TABLE IF EXISTS reb_unovr;
CREATE TEMP TABLE reb_unovr AS
SELECT o.meet_id, o.div_id,
       (base.d / o.distance) ^ (%(k)s)::double precision AS scale
FROM   dist_override o
CROSS  JOIN LATERAL (
    SELECT COALESCE(
        (SELECT min(m.distance) FROM meets m
          WHERE m.meet_id = o.meet_id AND m.div_id = o.div_id
            AND m.distance IS NOT NULL AND m.distance > 0),
        (SELECT min(x.distance) FROM tmp_xc_tfrrs_dist x
          WHERE x.meet_id = o.meet_id AND x.div_id = o.div_id)
    ) AS d
) base
WHERE  base.d IS NOT NULL AND base.d > 0 AND o.distance > 0
  -- A scraped value inside SAME_TOL of the override is not a correction and
  -- reverting to it moves nothing; excluding it keeps the table small and the
  -- report honest about how many divisions actually move.
  AND  abs(base.d - o.distance) / base.d > (%(tol)s)::double precision;
CREATE INDEX ON reb_unovr (meet_id, div_id);
ANALYZE reb_unovr;
"""

# The empty stand-in, so _GAP_BODY has ONE shape whether or not the flag is on.
_UNOVERRIDE_NONE = """
DROP TABLE IF EXISTS reb_unovr;
CREATE TEMP TABLE reb_unovr (meet_id bigint, div_id bigint,
                             scale double precision);
CREATE INDEX ON reb_unovr (meet_id, div_id);
"""

_GAP_BODY = """
DROP TABLE IF EXISTS reb_gap;
CREATE TEMP TABLE reb_gap AS
WITH rated AS (
    SELECT r.meet_id, r.div_id, r.result_id,
           -- ! THE ONLY PLACE --as-if-wiped CHANGES ANYTHING. reb_unovr is
           --   empty without it, so COALESCE makes this the identity.
           r.speed_rating * COALESCE(u.scale, 1.0) AS speed_rating,
           r.time_seconds,
           -- ident through the person map, same as the times body.
           COALESCE(r.person_id, rp.person_id, r.athlete_id) AS ident
    FROM   {table} r
    LEFT   JOIN reb_unovr u
           ON u.meet_id = r.meet_id AND u.div_id = r.div_id
    LEFT   JOIN reb_person rp ON rp.athlete_id = r.athlete_id
    WHERE  r.speed_rating IS NOT NULL AND r.speed_rating > 0
      AND  COALESCE(r.person_id, rp.person_id, r.athlete_id) IS NOT NULL
), own AS (
    SELECT ident,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating) AS med
    FROM   rated
    GROUP  BY ident
    HAVING count(*) >= %(min_own)s
), sex AS (
    -- One gender per person. DISTINCT ON with a stable ORDER BY, matching
    -- build_ranking_results._GENDER_TEMP_SQL exactly: a different tie-break
    -- would silently re-sex athletes between tools.
    SELECT DISTINCT ON (athlete_id) athlete_id, gender
    FROM   athletes
    WHERE  gender IN ('M', 'F')
    ORDER  BY athlete_id, school
)
SELECT x.meet_id, x.div_id, x.result_id, x.ident,
       x.speed_rating, x.time_seconds, o.med,
       x.speed_rating - o.med AS gap,
       s.gender
FROM   rated x
JOIN   own o ON o.ident = x.ident
LEFT   JOIN sex s ON s.athlete_id = x.ident;

CREATE INDEX ON reb_gap (meet_id, div_id);
ANALYZE reb_gap;
"""



# ------------------------------------------------------------------ #
#  THE GAP FROM TIMES -- EVERY FINISHER, NOT THE ONES THE GUARD KEPT
# ------------------------------------------------------------------ #
#
# ⚠ THE OLD BODY READ `WHERE r.speed_rating IS NOT NULL`, AND THAT IS
#   BACKWARDS FOR A DISTANCE DETECTOR.
#
#   A wrong distance makes times look impossible, the pace guard drops those
#   rows, and a dropped row has no speed_rating. So the worse the distance
#   error, the fewer rows the detector gets: Ox Bow Park, 145 finishers, 2
#   rated. MIN_FIELD was never the problem -- a division with zero rated rows
#   is zero at any threshold.
#
#   And the half that is quieter: the survivors of a broken division are not
#   a sample of the field, they are the rows whose times HAPPENED to stay
#   plausible at the wrong distance. Taking their median under-states the
#   error even where the division does clear the gate.
#
# ★ SO THE RATING IS COMPUTED HERE, FOR EVERY ROW, FROM THE ENGINE'S OWN
#   FORMULA. speed_rating = 100 * pool_mean * exp(delta_cell) /
#   normalized_time, and normalized_time is written by the BACKFILL -- a
#   different stage from the one that drops rows, so it survives exactly what
#   speed_rating loses.
#
# ! AND NOT COALESCE(speed_rating, computed). Mixing the engine's number with
#   a computed one makes a single field heterogeneous: the engine's rows
#   carry apply_tilt (which is XC-only), the guard's clamping, and the
#   selection above. A field median wants one formula applied to everybody.
#
# ★ THE POOL MEAN DOES NOT AFFECT THE ANSWER -- AS LONG AS IT IS ON THE RIGHT
#   AXIS. Any constant shared by a row and that athlete's median cancels in a
#   RATIO, but the gates measure gaps in ABSOLUTE POINTS, so the constant has
#   to put ratings near 100 or the gaps land on the wrong scale entirely: a
#   pool-less athlete who fell to a bare 100.0 (pm is ~1e5) had ratings of
#   ~0.1 and gaps of milli-points that every gate read as +0.0 -- their
#   evidence silently deleted (see the '__all__' row in _PM_SQL). It is set
#   so each pool's median rating is 100 -- readability, and comparability for
#   an athlete whose career crosses pools, since targetFor anchors normalized
#   time PER POOL and the raw numbers are not comparable across that seam.
# ★ ONE PERSON PER ATHLETE ID, EVEN WHERE A ROW FORGOT. results.person_id is
#   filled per ROW by the linkage backfill, so a meet scraped after the last
#   linkage run carries NULL person_ids while the same athletes' CAREER rows
#   are person-keyed. COALESCE(person_id, athlete_id) then splits one human
#   into two idents: the fresh division orphans from its own careers, its
#   athletes read as having no history, and the passes go blind exactly
#   where new mislabels arrive first. Measured: 264234/1050225, 78 athletes,
#   1 judgeable row -- while the site showed the careers plainly. The map
#   recovers the person from the athlete's other rows.
_IDENT_SQL = """
DROP TABLE IF EXISTS reb_person;
CREATE TEMP TABLE reb_person AS
SELECT athlete_id, min(person_id) AS person_id
FROM   {table}
WHERE  person_id IS NOT NULL AND athlete_id IS NOT NULL
GROUP  BY athlete_id;
CREATE INDEX ON reb_person (athlete_id);
ANALYZE reb_person;
"""

_POOL_SQL = """
-- ! min(pool), NOT mode(). mode() IS AN ORDERED-SET AGGREGATE, so Postgres
--   can only plan it as GroupAggregate -- a full sort of all 56M rows of
--   ranking_results, with no parallel plan available, for a value that is
--   constant for almost every athlete anyway. min() is a plain aggregate:
--   HashAggregate, parallel, and it picks deterministically. The only
--   athletes it decides anything for are the ones who changed pool, and for
--   them it only sets which scale constant they share.
DROP TABLE IF EXISTS reb_pool;
CREATE TEMP TABLE reb_pool AS
SELECT person_id AS ident, min(pool) AS pool
FROM   ranking_results
WHERE  person_id IS NOT NULL AND pool IS NOT NULL
  AND  sport = %(sport)s
GROUP  BY person_id;
CREATE INDEX ON reb_pool (ident);
ANALYZE reb_pool;
"""

# ! THE SAME CELL KEY apply_tilt USES, copied rather than re-derived: XC
#   difficulty is stored as 'XC:' || course_name with the distance snapped to
#   the nearest 100 m. min() because `meets` is not unique on
#   (meet_id, div_id) -- see the note in apply_tilt.
#
# ⚠ A DIVISION WITH NO CELL GETS delta = 0, NOT DROPPED. Course difficulty is
#   a few percent; a missing one is worth far less than losing the division.
_CELL_SQL = """
DROP TABLE IF EXISTS reb_cell;
CREATE TEMP TABLE reb_cell AS
SELECT m.meet_id, m.div_id, min(c.difficulty) AS difficulty
FROM   meets m
JOIN   course_difficulties c
       ON c.course_name = 'XC:' || m.course_name
      AND c.distance_m  = (round(m.distance / 100.0) * 100)::int
WHERE  c.difficulty IS NOT NULL
  AND  m.distance IS NOT NULL AND m.distance > 0
GROUP  BY 1, 2;
CREATE INDEX ON reb_cell (meet_id, div_id);
ANALYZE reb_cell;
"""

# ★ pm FROM A ONE PERCENT SAMPLE, AND IT DOES NOT MATTER THAT IT IS ONE.
#   Every gate uses own_med / rating, so pm cancels; it exists to put each
#   pool's median near 100 for readability and to keep an athlete comparable
#   across a pool change. A scale constant does not deserve a full pass over
#   60M rows, and TABLESAMPLE SYSTEM reads blocks rather than rows.
#
# ! GEOMETRIC MEAN, NOT MEDIAN, for the same reason min() replaced mode():
#   percentile_cont would force a sort where avg(ln x) is a HashAggregate.
_PM_SQL = """
DROP TABLE IF EXISTS reb_pm;
CREATE TEMP TABLE reb_pm AS
SELECT p.pool,
       100.0 / exp(avg(ln(exp(COALESCE(cd.difficulty, 0.0))
                          / r.normalized_time))) AS pm
FROM   {table} r TABLESAMPLE SYSTEM (1)
JOIN   reb_pool p  ON p.ident = COALESCE(r.person_id, r.athlete_id)
LEFT   JOIN reb_cell cd ON cd.meet_id = r.meet_id AND cd.div_id = r.div_id
WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
GROUP  BY p.pool
-- ⚠ THE GLOBAL ROW IS WHAT KEEPS A POOL-LESS ATHLETE'S EVIDENCE ALIVE.
--   An athlete absent from reb_pool (thin or unlinked -- not in
--   ranking_results) used to fall through to COALESCE(pm, 100.0), which is
--   a scale ~1000x off: pm is ~100/mean(1/nt) ~ 1e5, so their ratings came
--   out ~0.1 and their GAPS -- which every gate measures in absolute points
--   -- compressed to milli-points and read as +0.0. "pm cancels" is true
--   of ratios and FALSE of point differences. Measured on 26359/2, Ox Bow:
--   8 of the division's 10 judgeable rows were self-neutralised this way
--   and a wrong-race field printed a median gap of +0.0. The pool-less now
--   share one corpus-wide scale; approximate, but on the right axis.
UNION ALL
SELECT '__all__',
       100.0 / exp(avg(ln(exp(COALESCE(cd.difficulty, 0.0))
                          / r.normalized_time)))
FROM   {table} r TABLESAMPLE SYSTEM (1)
LEFT   JOIN reb_cell cd ON cd.meet_id = r.meet_id AND cd.div_id = r.div_id
WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0;
CREATE INDEX ON reb_pm (pool);
ANALYZE reb_pm;
"""

# ⚠ SAME SHAPE AS _GAP_BODY, DELIBERATELY, so the cost is "the old one on
#   more rows" rather than a new plan nobody has watched. The only additions
#   are three hash joins against small temp tables.
#
#   There is no intermediate 60M-row table and no index on one: reb_q was
#   both, and neither was ever read by anything that needed an index. The
#   joins here are hash joins against reb_pool / reb_pm / reb_cell, which are
#   small enough to hash.
#
# ! percentile_cont STAYS. It is an ordered-set aggregate and it does force a
#   sort -- but the athlete's own median is the one place robustness is not
#   optional, precisely because this table now INCLUDES the rows the pace
#   guard threw out, and those are wild by construction. A geometric mean
#   there would let one impossible row move an athlete's whole history.
#   buildGap raises work_mem before this runs so the sort stays in memory.
_GAP_BODY_TIMES = """
DROP TABLE IF EXISTS reb_gap;
CREATE TEMP TABLE reb_gap AS
WITH rated AS (
    SELECT r.meet_id, r.div_id, r.result_id,
           -- ident THROUGH the person map: a row linkage has not reached yet
           -- still joins its athlete's person-keyed career. See _IDENT_SQL.
           COALESCE(r.person_id, rp.person_id, r.athlete_id)    AS ident,
           r.time_seconds,
           -- the engine's own formula, applied to EVERY row.
           -- ! reb_unovr is empty without --as-if-wiped, so COALESCE is the
           --   identity in the normal path -- same as the old body.
           COALESCE(pm.pm, 100.0)
               * exp(COALESCE(cd.difficulty, 0.0)) / r.normalized_time
               * COALESCE(u.scale, 1.0)                          AS speed_rating
    FROM   {table} r
    LEFT   JOIN reb_unovr u  ON u.meet_id = r.meet_id AND u.div_id = r.div_id
    LEFT   JOIN reb_cell  cd ON cd.meet_id = r.meet_id AND cd.div_id = r.div_id
    LEFT   JOIN reb_person rp ON rp.athlete_id = r.athlete_id
    LEFT   JOIN reb_pool  p  ON p.ident =
                                COALESCE(r.person_id, rp.person_id,
                                         r.athlete_id)
    -- '__all__': the global scale for pool-less athletes -- see _PM_SQL.
    LEFT   JOIN reb_pm    pm ON pm.pool = COALESCE(p.pool, '__all__')
    WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
      AND  COALESCE(r.person_id, rp.person_id, r.athlete_id) IS NOT NULL
), own AS (
    SELECT ident,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating) AS med
    FROM   rated
    GROUP  BY ident
    HAVING count(*) >= %(min_own)s
), sex AS (
    SELECT DISTINCT ON (athlete_id) athlete_id, gender
    FROM   athletes
    WHERE  gender IN ('M', 'F')
    ORDER  BY athlete_id, school
)
SELECT x.meet_id, x.div_id, x.result_id, x.ident,
       x.speed_rating, x.time_seconds, o.med,
       x.speed_rating - o.med AS gap,
       s.gender
FROM   rated x
JOIN   own o ON o.ident = x.ident
LEFT   JOIN sex s ON s.athlete_id = x.ident;

CREATE INDEX ON reb_gap (meet_id, div_id);
ANALYZE reb_gap;
"""

# ------------------------------------------------------------------ #
#  THE FORM CURVE -- FITNESS BY RACE-OF-SEASON, MEASURED AND REMOVED
# ------------------------------------------------------------------ #
#
# ⚠ AN ATHLETE'S OWN MEDIAN IS A MID-SEASON NUMBER, so every early-season
#   race reads slow against it and the slow direction fills with phantom
#   relabels. Measured on the 2026-08-23 run: 3,147 divisions failed the
#   direction gate as openers, and of the 732 negative proposals that
#   survived, the file mixed three populations only fitness separates --
#   real mislabels at -33..-40 (Rocklin's 3000-actually-5000), time trials
#   and openers at -12..-18 (LELAND TIME TRIALS, EHS Time Trial, the
#   Wyoming altitude opener), and odd-but-correct local distances being
#   "fixed" off a -12 (Crystal Springs' famous 4747).
#
# ★ SO FITNESS IS MEASURED AND SUBTRACTED, NOT GUESSED. Number each
#   athlete's races within their academic season; the median gap at race
#   #1, #2, ... IS the form curve, printed every run. A wrong distance
#   keeps its deficit after the curve comes off (-40 stays ~-30); an
#   opener collapses into the bar. Race-of-season self-aligns regions:
#   Texas opens in early August and New York in September, but race #1 is
#   race #1 everywhere.
#
# ⚠ PER POOL, BECAUSE THE GLOBAL CURVE MEASURED FLAT AND THE FAULT DID NOT
#   MOVE. First measurement: #1 -1.0, #10+ +1.6 across 32M finishers --
#   corpus-wide, race #1 is barely slow, yet the McIver college opener
#   still read ~-13. The corpus is overwhelmingly high schoolers; a
#   population-specific offset (college medians inflated by the hs->college
#   anchor seam, or a genuinely later college peak) is invisible in a
#   global median. Per (pool, k) the curve is both the correction and the
#   verdict: a college_f row that is flat-negative at every k is a
#   CONSTANT accounting bias, not fitness -- read the printout.
#   Cells under 500 rows are dropped (their median is noise) and fall
#   through as form 0.
#
# ! ONE COPY, AFTER EITHER BODY. This rewrites reb_gap in place (keeping
#   gap_raw), so both --gap-from modes get it without a second copy of the
#   rule -- the label lateral already taught us what three copies cost.
#   Rows with an unparseable date get form 0 and pass through unchanged.
#   Pass 3 recomputes its per-row gap from speed_rating and med directly
#   and is deliberately untouched: a single row's impossibility is not a
#   fitness question.
_FORM_SQL = """
DROP TABLE IF EXISTS reb_gap_raw;
ALTER TABLE reb_gap RENAME TO reb_gap_raw;

DROP TABLE IF EXISTS reb_seq;
CREATE TEMP TABLE reb_seq AS
SELECT g.result_id,
       LEAST(row_number() OVER (
           PARTITION BY g.ident, {season}
           ORDER BY r.date, g.result_id), 10) AS k
FROM   reb_gap_raw g
JOIN   {table} r ON r.result_id = g.result_id
WHERE  r.date ~ '^(19|20)[0-9][0-9]-[0-9][0-9]-[0-9][0-9]';
CREATE INDEX ON reb_seq (result_id);
ANALYZE reb_seq;

DROP TABLE IF EXISTS reb_form;
CREATE TEMP TABLE reb_form AS
SELECT {poolexpr} AS pool, s.k,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY g.gap) AS form,
       count(*) AS n
FROM   reb_gap_raw g
JOIN   reb_seq s ON s.result_id = g.result_id
{pooljoin}
GROUP  BY {poolexpr}, s.k
HAVING count(*) >= 500;

CREATE TEMP TABLE reb_gap AS
SELECT g.meet_id, g.div_id, g.result_id, g.ident,
       g.speed_rating, g.time_seconds, g.med, g.gender,
       g.gap                                 AS gap_raw,
       g.gap - COALESCE(f.form, 0.0)         AS gap
FROM   reb_gap_raw g
LEFT   JOIN reb_seq  s ON s.result_id = g.result_id
{pooljoin}
LEFT   JOIN reb_form f ON f.pool = {poolexpr} AND f.k = s.k;

CREATE INDEX ON reb_gap (meet_id, div_id);
ANALYZE reb_gap;
DROP TABLE reb_gap_raw;
DROP TABLE reb_seq;
"""


# ⚠ A LITERAL % IN ANY OF THESE STRINGS IS A RUNTIME ERROR, NOT A TYPO.
#   psycopg2 reads % as the start of a placeholder, so a SQL COMMENT saying
#   "100% on one side" makes execute() raise "dict is not a sequence" -- an
#   error message that names neither the string nor the character. Write
#   percentages as words inside these, or double them.
def _noBarePercent(name, sql):
    import re
    for m in re.finditer(r"%(?!\(|%)", sql):
        raise AssertionError(
            f"{name} contains a bare '%' at offset {m.start()}: "
            f"{sql[max(0, m.start() - 40):m.start() + 10]!r}")


# ------------------------------------------------------------------ #
#  IS THE LABEL STILL THE ONE THE RATINGS WERE BUILT AT?
# ------------------------------------------------------------------ #
#
# ⚠ THE PASSES READ dist_override FOR THE LABEL, AND THAT IS ONLY RIGHT WHILE
#   THE TABLE STILL DESCRIBES WHAT THE BACKFILL USED.
#
#   impliedDistance scales from the label, and the gap it scales was measured
#   against ratings computed from normalized_time -- which the backfill wrote
#   using whatever dist_override held AT THAT TIME. Rebuild dist_override
#   without re-running 05_backfill and the two fall out of step: the label
#   says one thing, the ratings were built on another, and every proposal is
#   wrong by the ratio between them. That is the bug that put 3,085 wrong 6 km
#   divisions into corrections.py across twenty daily blocks.
#
#   The ordering rule -- wipe corrections.py, do NOT re-dump until after the
#   pipeline -- is easy to state and easy to break at one in the morning.
#
# ★ SO IT IS CHECKED, NOT TRUSTED. normalizeTime is deterministic given time,
#   distance, pool and sport, so running it at the override distance and
#   comparing against the stored normalized_time says whether the backfill
#   used that distance. Same recomputation anchor_check makes, and the same
#   one census_override_sources --verify makes from the other side.
#
# ! IT REFUSES RATHER THAN WARNS. A warning above a hundred-line report is a
#   warning nobody reads, and the cost of missing this one is a whole corpus
#   of overrides wrong by a constant ratio.
LABEL_MIN_AGREE = 0.90
LABEL_SAMPLE = 20_000

_LABEL_SQL = """
SELECT r.time_seconds, r.normalized_time, o.distance, k.pool
FROM   dist_override o
JOIN   {table} r ON r.meet_id = o.meet_id AND r.div_id = o.div_id
JOIN   ranking_results k ON k.result_id = r.result_id AND k.sport = %(sport)s
WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
  AND  r.time_seconds > 0 AND o.distance > 0
LIMIT  %(n)s
"""


def assertLabelTrustworthy(cur, sport, min_agree=LABEL_MIN_AGREE):
    """Refuse to run if dist_override no longer describes the backfill."""
    from anchor_check import mismatch

    cur.execute(_LABEL_SQL.format(table=_TABLE[sport]),
                {"sport": sport, "n": LABEL_SAMPLE})
    rows = cur.fetchall()
    if not rows:
        print("  label check: nothing to check -- dist_override is empty, or "
              "ranking_results\n               has not been rebuilt. "
              "Proceeding.")
        return True

    agree = 0
    for r in rows:
        _bad, _exp, ratio = mismatch(r["time_seconds"], float(r["distance"]),
                                     r["normalized_time"], r["pool"], sport)
        if ratio is not None and abs(ratio - 1.0) <= 0.05:
            agree += 1
    share = agree / len(rows)
    print(f"  label check: {share:.1%} of {len(rows):,} overridden rows were "
          f"normalised at their\n               own override distance "
          f"(need {min_agree:.0%})")
    if share >= min_agree:
        return True
    print("\n  ⚠ REFUSING TO RUN. dist_override no longer describes what the "
          "backfill used, so\n    the label the passes scale from does not "
          "match the ratings they measure.\n"
          "    Every proposal would be wrong by the ratio between them.\n\n"
          "    Most likely engine/dump_overrides.py was run after editing "
          "corrections.py\n    but before 05_backfill. Re-run the pipeline, "
          "or put dist_override back to\n    what the backfill last saw.\n")
    return False


# ------------------------------------------------------------------ #
#  IS THE LABEL THE DISTANCE reb_gap's RATINGS WERE COMPUTED AT?
# ------------------------------------------------------------------ #
#
# ★ THE ONE INVARIANT THIS WHOLE TOOL RESTS ON, SO IT IS MEASURED RATHER THAN
#   ASSERTED IN A COMMENT. Every proposal is
#
#       d_true = label * (base / (base + gap)) ** (1 / K)
#
#   and `gap` is measured against reb_gap.speed_rating. If the label is not
#   the distance THAT rating was computed at, every proposal is wrong by the
#   ratio between the two -- silently, plausibly, and in the same direction
#   for the whole corpus. It is the bug that wrote 3,085 wrong 6 km
#   divisions, and then its mirror image under --as-if-wiped.
#
# ★ AND IT IS CHECKABLE END TO END, because rating is inversely proportional
#   to normalized_time and reverting a division multiplies the rating by
#   `scale`:
#
#       normalizeTime(t, label)  ==  normalized_time_stored / scale
#
#   Both sides are already on disk. normalizeTime is deterministic given
#   time, distance, pool and sport, so this is arithmetic, not a heuristic.
#
# ! IT COVERS EVERY ROW, NOT JUST THE OVERRIDDEN ONES, which is what
#   assertLabelTrustworthy cannot do -- that one compares dist_override
#   against the backfill and is silent about the 90-odd percent of divisions
#   that have no override, and blind by construction under --as-if-wiped,
#   where the label is SUPPOSED to differ from dist_override.
#
# ! THE SAMPLE IS DRAWN FIRST, INTO ITS OWN TABLE, AND THE FIRST VERSION OF
#   THIS TOOK PASS 1 FROM 6 MINUTES TO FOREVER.
#
#   It read `FROM reb_gap g JOIN results ... JOIN ranking_results ...
#   WHERE mod(g.result_id, 997) = 0` in one statement. Postgres has no
#   statistics for mod() on a temp table, so it falls back to a default
#   selectivity and estimates the filter returns a hundred thousand-odd rows
#   rather than the 31,000 it does -- and on that estimate a hash join against
#   all 56M rows of ranking_results looks cheaper than 31,000 index lookups.
#   The plan builds hash tables over two large tables to answer a question
#   about 20,000 rows.
#
# ★ SO THE SAMPLE IS MATERIALISED, ANALYZED, AND THEN JOINED. Once it is a
#   real 20,000-row table with real statistics, no planner will choose a hash
#   join over a nested loop, and the whole check costs a handful of index
#   lookups.
#
# ! TABLESAMPLE SYSTEM, NOT mod(). The reason for mod() was that a bare LIMIT
#   reads one end of the corpus -- reb_gap is physically in `results` order,
#   so the first 20,000 rows are one span of dates. But mod() has to walk all
#   31.6M rows to find its hits, and it does that on every pass. SYSTEM picks
#   random BLOCKS across the whole table and reads only those, which is the
#   same spread for a thousandth of the IO.
#
# ⚠ BLOCK SAMPLING IS CLUSTERED, and that is a real cost: rows in one block
#   are adjacent results, usually the same division, so the effective sample
#   is smaller than the row count suggests. It is the right trade here because
#   the fault being caught is a CORPUS-WIDE CONSTANT RATIO -- every label
#   wrong by the same factor -- and clustering barely blunts that. It would be
#   the wrong trade for estimating a rare per-row property.
GAP_LABEL_MIN_AGREE = 0.90
GAP_LABEL_SAMPLE = 20_000
GAP_LABEL_TOL = 0.05

_GAP_SAMPLE_SQL = """
DROP TABLE IF EXISTS gl_sample;
CREATE TEMP TABLE gl_sample AS
SELECT result_id, meet_id, div_id
FROM   reb_gap TABLESAMPLE SYSTEM ({pct})
LIMIT  {cap};
CREATE INDEX ON gl_sample (result_id);
CREATE INDEX ON gl_sample (meet_id, div_id);
ANALYZE gl_sample;
"""

_GAP_LABEL_SQL = """
SELECT r.time_seconds, r.normalized_time, lbl.distance AS label,
       k.pool, COALESCE(u.scale, 1.0) AS scale
FROM   gl_sample g
JOIN   {table} r ON r.result_id = g.result_id
JOIN   ranking_results k ON k.result_id = g.result_id AND k.sport = %(sport)s
LEFT   JOIN reb_unovr u ON u.meet_id = g.meet_id AND u.div_id = g.div_id
__LABEL__
WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
  AND  r.time_seconds > 0 AND lbl.distance > 0
"""


def assertGapLabelMatchesRatings(cur, sport, as_if_wiped, n_rows,
                                 min_agree=GAP_LABEL_MIN_AGREE):
    """The label must be the distance reb_gap's ratings were computed at."""
    from anchor_check import mismatch

    # Ask for three times the cap in blocks, then take the cap: SYSTEM returns
    # whole blocks, so the yield varies, and coming up short is worse than
    # reading a few thousand rows nobody uses.
    pct = min(100.0, max(0.01, 100.0 * 3.0 * GAP_LABEL_SAMPLE
                         / max(int(n_rows), 1)))
    cur.execute(_GAP_SAMPLE_SQL.format(pct=f"{pct:.4f}",
                                       cap=GAP_LABEL_SAMPLE))

    sql = _GAP_LABEL_SQL.format(table=_TABLE[sport])
    sql = sql.replace("__LABEL__", _LABEL_LATERAL.format(g="g"))
    _noBarePercent("_GAP_LABEL_SQL", sql.replace("%(sport)s", ""))
    cur.execute(sql, {"sport": sport})
    rows = cur.fetchall()
    if not rows:
        print("  label/rating check: no sampled row carried both a label and "
              "a normalised time.")
        return True

    agree, ratios = 0, []
    for r in rows:
        # rating ~ 1 / normalized_time, and --as-if-wiped multiplied the
        # rating by `scale`, so the normalised time it implies is /scale.
        want = float(r["normalized_time"]) / float(r["scale"])
        _bad, _exp, ratio = mismatch(r["time_seconds"], float(r["label"]),
                                     want, r["pool"], sport)
        if ratio is None:
            continue
        ratios.append(ratio)
        if abs(ratio - 1.0) <= GAP_LABEL_TOL:
            agree += 1
    if not ratios:
        print("  label/rating check: nothing comparable in the sample.")
        return True

    share = agree / len(ratios)
    ratios.sort()
    med = ratios[len(ratios) // 2]
    print(f"  label/rating check: {share:.1%} of {len(ratios):,} sampled rows "
          f"were normalised at the\n                      label the passes "
          f"will scale from (median ratio {med:.4f}, "
          f"need {min_agree:.0%})")
    if share >= min_agree:
        return True

    # ⚠ THE MEDIAN RATIO IS THE DIAGNOSIS, NOT DECORATION. A label error is
    #   systematic, so the ratio clusters: 1.33 is the Lehigh 8000-vs-6000
    #   shape, 0.5 or 2.0 is a mile/two-mile confusion, and a ratio spread
    #   flat around 1 is something else entirely and not this bug.
    lo = ratios[len(ratios) // 20]
    hi = ratios[-max(1, len(ratios) // 20)]
    print("\n  ⚠ REFUSING TO RUN. The label does not describe the distance "
          "these ratings were\n    computed at, so every proposal would be "
          f"wrong by the ratio between them.\n"
          f"    ratio 5th/50th/95th: {lo:.4f} / {med:.4f} / {hi:.4f}\n")
    if as_if_wiped:
        print("    --as-if-wiped is ON, so reb_gap's ratings were reverted to "
              "the scraped\n    distance and the label had to revert with "
              "them. Check that the CASE on\n    reb_unovr in _LABEL_LATERAL "
              "still fires for every reverted division.\n")
    else:
        print("    Most likely engine/dump_overrides.py was run after editing "
              "corrections.py\n    but before 05_backfill, so dist_override "
              "describes a rebuild that has not\n    happened yet.\n")
    print("    --skip-label-check runs anyway. It is there for diagnosing "
          "this message,\n    not for getting past it: the proposals will be "
          "wrong.\n")
    return False


# ! A CLOCK, BECAUSE "IT IS SLOW" WAS DIAGNOSED TWICE BY GUESSING. buildGap
#   is six statements and any one of them can be the whole runtime; without
#   per-step elapsed the only evidence is a running query in pg_stat_activity
#   and a hypothesis. Costs one time.time() per step.
def _step(t0, label):
    import time
    now = time.time()
    print(f"    [{now - t0:6.1f}s] {label}")
    return now


def buildGap(cur, sport, min_own, as_if_wiped=False, tol=0.02,
             skip_label_check=False, gap_from="times", coverage=False,
             form=True):
    _noBarePercent("_PASS1_SQL", _PASS1_SQL)
    _noBarePercent("_GAP_BODY", _GAP_BODY)
    # ! ALL THREE, not just the one that broke. They share _LABEL_LATERAL now,
    #   so a bare % written into it would take out every pass at once.
    _noBarePercent("_PASS2_SQL", _PASS2_SQL)
    _noBarePercent("_PASS3_SQL", _PASS3_SQL)
    # ! ALWAYS, NOT ONLY UNDER --as-if-wiped. _PASS1_SQL now falls back to
    #   this for a division's label distance, so it has to exist in both
    #   modes or every tfrrs division loses its label again. Same reader
    #   build_ranking_results uses -- imported, not copied.
    sys.path.insert(0, "racecast")
    from build_ranking_results import _XC_TFRRS_DIST_SQL
    import time
    _t = time.time()
    cur.execute(_XC_TFRRS_DIST_SQL)
    _t = _step(_t, "tfrrs distances")
    cur.execute(_COURSE_DIST_SQL, {"min_n": COURSE_MIN_N})
    n_courses = loadCourseDistances(cur)
    _t = _step(_t, f"course distances ({n_courses:,} venues cached)")
    kept, raw = loadLadder(cur)
    _t = _step(_t, "corpus ladder")
    print(f"  ladder: {len(kept):,} distances the corpus actually races "
          f"({LADDER_MIN_N:,}+ finishers each; {len(raw) - len(kept):,} "
          f"comb teeth merged away)")
    if as_if_wiped:
        cur.execute(_UNOVERRIDE_SQL, {"k": K, "tol": tol})
        _t = _step(_t, "--as-if-wiped revert")
        cur.execute("SELECT count(*) AS n FROM reb_unovr")
        n_ovr = cur.fetchone()["n"]
        print(f"  --as-if-wiped: {n_ovr:,} divisions reverted to their scraped "
              f"distance before judging.\n"
              f"                 Sole sources are NOT reverted -- see "
              f"scripts/census_override_sources.py.")
        if not n_ovr and sport == "TF":
            # ⚠ EXPECTED ON TRACK, AND WORTH SAYING SO. TF keeps its distance
            #   in the EVENT NAME -- `meets_tf` cannot carry one, because the
            #   800 and the 3200 at one meeting are one row and two distances.
            #   So a TF distance override has no scraped value underneath it
            #   by construction: every one is a sole source, nothing reverts,
            #   and --as-if-wiped is correctly a no-op here.
            print("  (TF: nothing to revert. Track's distance lives in the "
                  "event name, so every\n   TF distance override is a sole "
                  "source -- see census_override_sources.py.)")
        elif not n_ovr:
            print("  ⚠ NOTHING REVERTED. Either dist_override is empty or it "
                  "is stale --\n    engine/dump_overrides.py is NOT a "
                  "pipeline step, so run it first.")
    else:
        cur.execute(_UNOVERRIDE_NONE)
    if gap_from == "times":
        _noBarePercent("_GAP_BODY_TIMES", _GAP_BODY_TIMES)
        # ! ROOM FOR THE ONE SORT THIS CANNOT AVOID. The athlete-median is an
        #   ordered-set aggregate over every finisher, not just the rated
        #   ones, so it is the biggest sort in the run. Spilling it to disk is
        #   the difference between minutes and hours. SET LOCAL dies with the
        #   transaction, so nothing leaks.
        cur.execute("SET LOCAL work_mem = '2GB'")
        cur.execute(_IDENT_SQL.format(table=_TABLE[sport]))
        _t = _step(_t, "person map (rows linkage has not reached yet)")
        cur.execute(_POOL_SQL, {"sport": sport})
        _t = _step(_t, "pool per athlete")
        cur.execute(_CELL_SQL)
        cur.execute(_PM_SQL.format(table=_TABLE[sport]))
        _t = _step(_t, "cells and pool scale")
        # The two corpus constants --explain needs at start-up, cached so
        # its fast path skips them. See _writeExplainCache.
        _writeExplainCache(cur)
        cur.execute(_GAP_BODY_TIMES.format(table=_TABLE[sport]),
                    {"min_own": min_own})
    else:
        cur.execute(_IDENT_SQL.format(table=_TABLE[sport]))
        cur.execute(_GAP_BODY.format(table=_TABLE[sport]),
                    {"min_own": min_own})
    _t = _step(_t, f"gap table from {gap_from} (the expensive one)")
    if form:
        from season_year import seasonYearSqlInt
        # reb_pool only exists on the times path; the ratings branch gets
        # the global curve rather than a second copy of the pool logic.
        if gap_from == "times":
            poolexpr = "COALESCE(p.pool, 'unknown')"
            pooljoin = "LEFT JOIN reb_pool p ON p.ident = g.ident"
        else:
            poolexpr, pooljoin = "'all'", ""
        sql = _FORM_SQL.format(table=_TABLE[sport],
                               season=seasonYearSqlInt(None, "r.date"),
                               poolexpr=poolexpr, pooljoin=pooljoin)
        _noBarePercent("_FORM_SQL", sql)
        # The window sort over the whole gap table is the same size as the
        # own-median sort; give it the same room in the ratings branch too.
        cur.execute("SET LOCAL work_mem = '2GB'")
        cur.execute(sql)
        _t = _step(_t, "form curve (fitness by race-of-season, per pool)")
        cur.execute("SELECT pool, k, form, n FROM reb_form ORDER BY pool, k")
        by_pool = {}
        for r in cur.fetchall():
            by_pool.setdefault(r["pool"], []).append(r)
        print("  [form] median gap by race-of-season, per pool, subtracted "
              "from every gap (--no-form reverts):\n"
              "         a row that is flat-negative at every k is a constant "
              "bias in that pool's\n         medians, not fitness -- that is "
              "a finding, not a correction to shrug at")
        for pool, rows in sorted(by_pool.items(),
                                 key=lambda kv: -sum(r["n"] for r in kv[1])):
            curve = "  ".join(f"#{r['k']}{'+' if r['k'] == 10 else ''} "
                              f"{r['form']:+.1f}" for r in rows)
            print(f"         {pool:<10} {curve}")
    # RealDictCursor, so name the aggregates rather than unpacking a tuple.
    cur.execute("SELECT count(*) AS n, count(gender) AS n_sexed FROM reb_gap")
    row = cur.fetchone()
    n, n_sexed = row["n"], row["n_sexed"]
    # ! IT SAYS WHICH ROWS THESE ARE. Both modes printed "rated rows", so the
    #   times table -- whose entire point is that it does NOT gate on a rating
    #   -- looked exactly like the thing it replaced.
    what = "finishers" if gap_from == "times" else "ENGINE-RATED rows only"
    print(f"  gap table: {n:,} {what} judged against their athlete's own "
          f"median ({n_sexed:,} with a gender)")
    if gap_from != "times":
        print("             ⚠ --gap-from ratings: rows the pace guard dropped "
              "are NOT here, so a\n               division whose distance is "
              "badly wrong is mostly invisible. That is\n               the "
              "old gate; --gap-from times is the default.")
    if gap_from == "times":
        # ★ THE NUMBER THAT SAYS WHETHER THIS WAS WORTH DOING. Rows the engine
        #   never rated are exactly the ones a wrong distance produces, so the
        #   share of the gap table they make up is the blind spot being closed.
        cur.execute("""
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE r.speed_rating IS NULL) AS recovered,
                   count(DISTINCT (g.meet_id, g.div_id))
                       FILTER (WHERE r.speed_rating IS NULL)      AS divs
            FROM   reb_gap g
            JOIN   {table} r ON r.result_id = g.result_id
        """.format(table=_TABLE[sport]))
        c = cur.fetchone()
        if c and c["n"] and coverage:
            # ★ THE ROWS THAT ARE STILL NOT HERE, AND WHY. Recovering the
            #   pace-guard drops is only half the blind spot: normalized_time
            #   survives the RATING stage, but nothing dropped at or before
            #   the BACKFILL ever gets one. _RESULT_DROP_XC alone is 569,352
            #   rows, _DISTANCE_DROP kills whole divisions, and a division
            #   with no usable distance cannot be normalised at all.
            #
            # ! ONE LEFT JOIN OVER THE WHOLE RESULT TABLE, which is why it is
            #   behind a flag. It is the only way to count what is absent.
            cur.execute("""
                SELECT count(*) AS total,
                       count(*) FILTER (
                           WHERE r.normalized_time IS NULL
                              OR r.normalized_time <= 0)          AS no_norm,
                       count(*) FILTER (
                           WHERE COALESCE(r.person_id, r.athlete_id)
                                 IS NULL)                          AS no_ident,
                       count(*) FILTER (
                           WHERE g.result_id IS NULL
                             AND r.normalized_time > 0
                             AND COALESCE(r.person_id, r.athlete_id)
                                 IS NOT NULL)                      AS no_own
                FROM   {table} r
                LEFT   JOIN reb_gap g ON g.result_id = r.result_id
            """.format(table=_TABLE[sport]))
            v = cur.fetchone()
            t = max(v["total"], 1)
            print(f"\n  COVERAGE -- {v['total']:,} rows in {_TABLE[sport]}, "
                  f"{n:,} of them judgeable ({n / t:.1%})\n")
            print(f"    {v['no_norm']:>12,}  ({v['no_norm'] / t:>5.1%})  no "
                  f"normalized_time -- dropped at or before the BACKFILL,")
            print(f"    {'':>12}           so no distance can be implied from "
                  f"them at all")
            print(f"    {v['no_ident']:>12,}  ({v['no_ident'] / t:>5.1%})  no "
                  f"person_id or athlete_id -- nobody to compare against")
            print(f"    {v['no_own']:>12,}  ({v['no_own'] / t:>5.1%})  usable, "
                  f"but their athlete has fewer than")
            print(f"    {'':>12}           --min-own-races rated races "
                  f"elsewhere\n")
        if c and c["n"]:
            print(f"             {c['recovered']:,} of them "
                  f"({c['recovered'] / c['n']:.1%}) have NO speed_rating in "
                  f"the database\n             -- rows the pace guard dropped, "
                  f"across {c['divs']:,} divisions. Those are\n             "
                  f"the ones a wrong distance produces, and the old gap table "
                  f"could not see them.")
    if not skip_label_check and not assertGapLabelMatchesRatings(
            cur, sport, as_if_wiped, n):
        return None
    _step(_t, "label/rating check")
    return n


# ------------------------------------------------------------------ #
#  MEASURE -- what sigma actually is on this corpus
# ------------------------------------------------------------------ #

_MEASURE_SQL = """
WITH per_ath AS (
    SELECT ident,
           stddev_samp(speed_rating) AS sd,
           avg(speed_rating)         AS mean,
           count(*)                  AS n
    FROM   reb_gap
    GROUP  BY ident
    HAVING count(*) >= 4
)
SELECT width_bucket(mean, 60, 160, 10) AS band,
       min(mean)::int  AS lo,
       max(mean)::int  AS hi,
       count(*)        AS athletes,
       round(avg(sd)::numeric, 2)                                AS mean_sd,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY sd))::numeric, 2)
                                                                 AS med_sd
FROM   per_ath
WHERE  sd IS NOT NULL
GROUP  BY band
ORDER  BY band
"""


def measure(cur):
    """Recompute the noise floor from the live corpus.

    ⚠ THIS IS AN UPPER BOUND ON SIGMA, NOT SIGMA. It spreads over an
      athlete's whole career -- different venues, different seasons, real
      improvement -- so it contains genuine signal as well as noise.
      08_golive's number is tighter because it holds venue and season fixed.
      If this lands ABOVE that, the difference is improvement, not error, and
      the tighter figure is the one the thresholds should use.
    """
    cur.execute(_MEASURE_SQL)
    rows = cur.fetchall()
    print("\n  PER-ATHLETE RATING SPREAD (4+ rated races)\n")
    print(f"    {'band':>12} {'athletes':>10} {'mean sd':>9} {'median sd':>10}")
    sds = []
    for r in rows:
        if not r["athletes"]:
            continue
        sds.append((float(r["med_sd"] or 0), r["athletes"]))
        print(f"    {str(r['lo']) + '-' + str(r['hi']):>12} "
              f"{r['athletes']:>10,} {r['mean_sd']:>9} {r['med_sd']:>10}")
    if sds:
        tot = sum(n for _s, n in sds)
        pooled = sum(s * n for s, n in sds) / tot if tot else 0.0
        print(f"\n    athlete-weighted median sd: {pooled:.2f}")
        print(f"    08_golive, venue and season held fixed: 3.44 - 4.59")
        print(f"    thresholds below use --sigma (currently {SIGMA:.2f})")
        print(f"\n    bars at that sigma:  pass1 {T1_SIGMA * SIGMA:.0f}  "
              f"pass2 {T2_SIGMA * SIGMA:.0f}  pass3 {T3_SIGMA * SIGMA:.0f} "
              f"points")
    print()


# ------------------------------------------------------------------ #
#  PASS 1 -- the whole division is wrong
# ------------------------------------------------------------------ #

# ------------------------------------------------------------------ #
#  THE LABEL -- ONE DEFINITION, BECAUSE THREE COPIES DRIFTED
# ------------------------------------------------------------------ #
#
# ★ EVERY PASS SCALES FROM THIS AND NOTHING ELSE:
#
#       d_true = label * (base / (base + gap)) ** (1 / K)
#
#   so the label MUST be the distance the ratings being measured were
#   computed at. Not the scraped one, not the current one -- that one.
#
# ⚠ IT LIVED IN THREE PLACES AND ONLY ONE OF THEM GOT FIXED. Pass 1 was
#   given dist_override precedence, then the tfrrs fallback, then the
#   --as-if-wiped revert. Passes 2 and 3 kept a two-line
#   COALESCE(o.distance, m.distance) through all three, so under
#   --as-if-wiped they reverted the RATINGS (reb_gap rescales every row) and
#   left the LABEL on the override -- the exact mirror-image bug that made
#   Swedetown propose 2400 -> 1200 instead of 5000 -> 2500. They were also
#   still blind to every tfrrs division, which is what hid 26359/0.
#
#   Three copies of a rule is three chances to fix two of them. So there is
#   one, spliced into all three queries below, and `{g}` is the alias of the
#   gap row it hangs off.
# ------------------------------------------------------------------ #
#  WHAT TO CALL A DIVISION IN THE REPORT
# ------------------------------------------------------------------ #
#
# ⚠ LOOKED UP AFTER THE PASS, NOT JOINED INSIDE IT, AND THE FIRST VERSION GOT
#   THAT WRONG. As a LATERAL beside _LABEL_LATERAL this ran once per ROW of
#   reb_gap -- 31.6 MILLION rows, not the 543,260 divisions they group into,
#   because the join happens before the aggregate. Four correlated subqueries
#   on top of that took pass 1 from 6 minutes to past 21 and still climbing,
#   on IO wait.
#
# ★ AND THE NAMES ARE ONLY EVER NEEDED FOR THE FINDINGS. Pass 1 condemns
#   about 5,500 divisions of 543,260 and prints a couple of hundred; pass 3
#   drops a few dozen. So the lookup takes the keys it actually has to name --
#   four orders of magnitude fewer -- and runs once, at the end.
#
# ! SEPARATE FROM THE LABEL, DELIBERATELY. It is tempting to have the label
#   lateral return the tfrrs venue as `course_name` and be done, but
#   course_name is not decoration there: it is the KEY snapToCourse looks up
#   in reb_course_dist, and that table is built from `meets` alone. Feeding it
#   a tfrrs venue string would find no candidates for some divisions and,
#   worse, real candidates for others whose venue happens to collide. The snap
#   would change as a side effect of improving a printout.
#
# ⚠ VENUE AND MEET NAME BOTH, because they are different strings and people
#   search for both. "Ox Bow Park" is the venue; "JV Minutemen Classic" is the
#   meet. Every name in the last round of complaints -- Yellow Jacket,
#   Gunstock, RGNS, Wyoming Invitational -- is a MEET name, so a venue-only
#   report could not have matched one of them, and searching the proposals for
#   any of them would have come back empty while the division sat in the file.
_NAMES_SQL = """
SELECT k.meet_id, k.div_id,
       COALESCE(
           (SELECT m.course_name FROM meets m
             WHERE m.meet_id = k.meet_id AND m.div_id = k.div_id
               AND m.course_name IS NOT NULL LIMIT 1),
           (SELECT t.venue_name FROM meets_tfrrs t
             WHERE t.meet_id = k.meet_id
               AND t.venue_name IS NOT NULL LIMIT 1)
       ) AS display_name,
       COALESCE(
           (SELECT m.meet_name FROM meets m
             WHERE m.meet_id = k.meet_id
               AND m.meet_name IS NOT NULL LIMIT 1),
           (SELECT t.meet_name FROM meets_tfrrs t
             WHERE t.meet_id = k.meet_id
               AND t.meet_name IS NOT NULL LIMIT 1)
       ) AS meet_display
FROM   (VALUES %s) AS k(meet_id, div_id)
"""


def applyNames(cur, *rowsets):
    """Fill display_name / meet_display on these rows, in place.

    Takes several lists so one round trip covers the whole report -- the
    condemned set and the skipped set share most of their meets.
    """
    from psycopg2.extras import execute_values

    keys, rows = set(), []
    for rs in rowsets:
        for r in rs or ():
            # skipped rows arrive as (row, reason) pairs
            r = r[0] if isinstance(r, tuple) else r
            if r.get("meet_id") is None:
                continue
            keys.add((int(r["meet_id"]), int(r["div_id"])))
            rows.append(r)
    if not keys:
        return
    # ! fetch=True, NOT cur.fetchall(). execute_values sends one statement
    #   per page, and the cursor only holds the LAST page's rows -- so over
    #   1,000 keys, fetchall() named ~keys%1000 divisions and silently
    #   dropped the rest. The console table hid it behind its course_name
    #   fallback; pass1.py's comments have no fallback and came out bare.
    fetched = execute_values(cur, _NAMES_SQL, sorted(keys), page_size=1000,
                             fetch=True)
    got = {(int(r["meet_id"]), int(r["div_id"])):
           (r["display_name"], r["meet_display"]) for r in fetched}
    for r in rows:
        name = got.get((int(r["meet_id"]), int(r["div_id"])))
        if name:
            r["display_name"], r["meet_display"] = name


_LABEL_LATERAL = r"""
LEFT   JOIN LATERAL (
    -- ⚠ `meets` IS ANET-ONLY, AND THAT BLINDED THESE PASSES TO EVERY TFRRS
    --   DIVISION. impliedDistance needs a label to scale from; with no label
    --   it returns None and the row is skipped as "no usable label distance"
    --   -- AFTER the division has already passed the bar and the unanimity
    --   gate. The fault is found and then discarded.
    --
    --   Measured on 26359/0, Ox Bow Park, the JV Minutemen Classic: 22 rated
    --   rows, field median gap +58.6, the whole field on one side, bar 11.2.
    --   It passes everything and dies here, because meet 26359 has 568 tfrrs
    --   rows and no `meets` row at all.
    --
    -- ★ SO THE TFRRS BLOB IS THE FALLBACK, same source and same reader as
    --   build_ranking_results.prepareXcTfrrsDistTemp -- a per-division
    --   distance inside meets_tfrrs.division_distances, keyed by div_id as a
    --   string. COALESCE, not UNION: anet wins where both have one, which is
    --   the precedence every other reader uses.
    --
    -- ⚠ dist_override FIRST, AND ITS ABSENCE HERE WAS THE BUG. The ratings
    --   were computed at the distance the BACKFILL used -- which is
    --   dist_override when one exists, since that is the precedence
    --   backfill_normalize, the engine's _xcQuery and build_ranking_results
    --   all apply.
    --
    --   Reading the scrape instead starts the scaling from the wrong number.
    --   Meet 44518 div 191150, Lehigh: dist_override says 6000, `meets` says
    --   8000, and pass 1 reported the label as 8000 -- so every implied
    --   distance for that division came out 8000/6000 = 1.33x too long, was
    --   written as a fresh override, and the next run repeated the error from
    --   the new wrong base. corrections.py carries twenty daily
    --   distance_override_xc.py blocks from 2026-07-13 to 07-18, several with
    --   identical entry counts, re-proposing the same divisions. That is this
    --   loop, running once a day.
    --
    -- ⚠ AND --as-if-wiped MOVES WHAT THE RATINGS WERE BUILT AT, SO IT MOVES
    --   THIS TOO. The flag reverts a division's ratings to the SCRAPED
    --   distance; the label has to follow, or the two disagree again in the
    --   mirror image of the bug above.
    --
    --   Measured on Swedetown 199300/798809: with the override as the label
    --   the run proposed 2400 -> 1200, against 5000 -> 2500 before. Same
    --   division, same field, two answers, because the label and the ratings
    --   were describing different distances.
    --
    -- ! reb_unovr HOLDS EXACTLY THE REVERTED DIVISIONS and is EMPTY when the
    --   flag is off, so this CASE is the identity in the normal path.
    SELECT COALESCE(m.course_name, x.course_name)  AS course_name,
           CASE WHEN u.meet_id IS NOT NULL
                THEN COALESCE(m.distance, x.distance)
                ELSE COALESCE(o.distance, m.distance, x.distance)
           END                                     AS distance,
           m.division                              AS division
    FROM  (SELECT course_name, distance, division
             FROM meets
            WHERE meets.meet_id = {g}.meet_id AND meets.div_id = {g}.div_id
            LIMIT 1) m
    LEFT  JOIN LATERAL
          (SELECT distance FROM dist_override d
            WHERE d.meet_id = {g}.meet_id AND d.div_id = {g}.div_id
            LIMIT 1) o ON TRUE
    LEFT  JOIN LATERAL
          (SELECT meet_id FROM reb_unovr r
            WHERE r.meet_id = {g}.meet_id AND r.div_id = {g}.div_id
            LIMIT 1) u ON TRUE
    FULL  OUTER JOIN
          (SELECT NULL::text AS course_name, t.distance
             FROM tmp_xc_tfrrs_dist t
            WHERE t.meet_id = {g}.meet_id AND t.div_id = {g}.div_id
            LIMIT 1) x ON TRUE
    LIMIT 1
) lbl ON TRUE
"""


# ⚠ THE AGGREGATE FIRST, THE LABEL SECOND, AND HAVING IT THE OTHER WAY ROUND
#   WAS THE WHOLE RUNTIME.
#
#   With the lateral in the FROM beside reb_gap it is evaluated once per ROW
#   -- 31,667,670 of them -- each running up to four correlated subqueries,
#   so roughly 126 million subquery evaluations to answer a question about
#   543,260 divisions.
#
#   The label is a property of (meet_id, div_id). Grouping first and hanging
#   the lateral off the 543,260-row aggregate is the same answer for one
#   fifty-eighth of the work. Same mistake as _NAME_LATERAL, one function
#   over, found the same way.
_PASS1_SQL = """
WITH agg AS (
    SELECT g.meet_id, g.div_id,
           count(*)                                            AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY g.gap)  AS med_gap,
           avg(g.med)                                          AS base,
           greatest(
               count(*) FILTER (WHERE g.gap > 0),
               count(*) FILTER (WHERE g.gap < 0)
           )::float / count(*)                                 AS same_side
    FROM   reb_gap g
    GROUP  BY g.meet_id, g.div_id
    HAVING count(*) >= %(min_field)s
)
SELECT a.meet_id, a.div_id, a.n, a.med_gap, a.base, a.same_side,
       lbl.course_name, lbl.distance, lbl.division
FROM   agg a
__LABEL__
""".replace("__LABEL__", _LABEL_LATERAL.format(g="a"))


# ------------------------------------------------------------------ #
#  WHAT DISTANCE IS THIS COURSE ACTUALLY RUN AT?
# ------------------------------------------------------------------ #
#
# ★ THE LADDER IS A GUESS ABOUT THE SPORT; THE COURSE IS A FACT ABOUT THE
#   COURSE. snapToLadder picks the nearest entry from a global list of race
#   distances, which is right in general and needlessly weak here: a venue
#   that has hosted 40,000 results at 5000 m and none at 5149 is telling you
#   which of those two an implied 5124 means.
#
#   26359 is the case. Its divisions imply 5220, 5164 and 5259; the ladder
#   snaps all of them to 5149 (3.2 miles) because 5149 is nearer. The venue
#   itself runs 5000 -- and the hand-written override on div 0, written by
#   someone who knew Indiana high school cross country is 5000 m, says 5000.
#   The ladder cannot see either fact.
#
# ! COUNTED BY RESULTS, NOT BY DIVISIONS. One mislabelled division at a venue
#   should not make its own wrong distance a candidate; forty thousand
#   finishers at 5000 should.
#
# ⚠ AND THE BROKEN DIVISION'S OWN LABEL IS EXCLUDED. Otherwise a venue whose
#   only rows are the mislabelled ones "corroborates" the mislabel and the
#   snap returns the number we are trying to replace.
_COURSE_DIST_SQL = """
DROP TABLE IF EXISTS reb_course_dist;
CREATE TEMP TABLE reb_course_dist AS
SELECT m.course_name, m.distance, count(*) AS n
FROM   meets m
JOIN   results r ON r.meet_id = m.meet_id AND r.div_id = m.div_id
                AND r.source = m.source
WHERE  m.course_name IS NOT NULL
  AND  m.distance IS NOT NULL AND m.distance > 0
GROUP  BY 1, 2
HAVING count(*) >= %(min_n)s;
CREATE INDEX ON reb_course_dist (course_name);
ANALYZE reb_course_dist;
"""

# A distance needs this many finishers at a venue before it counts as one the
# venue runs. Below it, one mislabelled division is a "candidate".
COURSE_MIN_N = 200

# How near the implied distance must land to one the course runs. Wider than
# SNAP_TOL because this is corroborated evidence rather than a guess: the
# venue has actually been raced at that distance, thousands of times.
COURSE_TOL = 0.06

_COURSE_DIST = {}

# ------------------------------------------------------------------ #
#  THE LADDER THE CORPUS ACTUALLY RUNS
# ------------------------------------------------------------------ #
#
# ⚠ THE HAND-WRITTEN LADDER IS WHY PASS 1 FINDS NOTHING. audit_overrides.LADDER
#   is 28 rungs, and the gaps between them are enormous:
#
#       3218 -> 4000   24.3%      1200 -> 1500   25.0%
#       8047 -> 10000  24.3%      1609 -> 1931   20.0%
#
#   A division implying 3600 m sits 12.2% from both neighbours -- FOUR TIMES
#   SNAP_TOL. It cannot pass however obviously broken it is. Measured on this
#   corpus: 2,598 divisions cleared the bar and died at the snap, against 1 at
#   the change cap and 4 at the label. The snap is not a gate, it is the wall.
#
# ★ SO THE RUNGS COME FROM THE CORPUS. A distance that thousands of results
#   were actually raced at is a real race distance, by definition and without
#   anyone having to have thought of it. 3500, 4400, 5600, 9000 -- none of
#   them are in the hand list and all of them are real somewhere.
#
# ! MISLABELLED ROWS CONTRIBUTE, AND THAT IS FINE. A race wrongly recorded as
#   8046 still makes 8046 a rung -- and 8046 IS a real distance, so the rung
#   is correct even when the row is not. What this cannot do is invent a rung
#   nobody ever raced.
_LADDER_SQL = """
DROP TABLE IF EXISTS reb_ladder;
CREATE TEMP TABLE reb_ladder AS
SELECT round(m.distance)::int AS distance, count(*) AS n
FROM   meets m
JOIN   results r ON r.meet_id = m.meet_id AND r.div_id = m.div_id
                AND r.source = m.source
WHERE  m.distance BETWEEN 800 AND 20000
GROUP  BY 1
HAVING count(*) >= %(min_n)s;
"""

# How many finishers a distance needs corpus-wide to count as a rung. High
# enough that a handful of mislabelled divisions cannot mint one; low enough
# that a regionally common distance survives. --ladder prints what it yields.
LADDER_MIN_N = 5_000

_CORPUS_LADDER = []


# ⚠ THE RAW CORPUS LADDER IS A COMB, NOT A LADDER. Measured on this corpus:
#
#       4667  4683  4699  4715  4731  4747  4763  4779  4795
#
#   Nine "distances" sixteen metres apart. 16.09 m is one hundredth of a mile,
#   so something upstream records distances in hundredths of a mile and the
#   conversion to metres produces teeth. Same at 4409/4417/4425 (8 m = 0.005
#   mi) and 3089/3100/3106.
#
#   Those teeth are not race distances anybody chose, and they destroy the
#   snap as a test: with the comb present, 74.9% of PURE NOISE passes at 3%,
#   because anything landing between 4600 and 4950 hits a tooth.
#
# ★ SO A RUNG HAS TO BE A LOCAL PEAK. Keep a distance only when no distance
#   within MERGE_TOL of it was raced more often -- which collapses each comb
#   to its most popular tooth and leaves the real rungs untouched, because a
#   real rung IS the popular one in its neighbourhood. That is what "commonly
#   run" has to mean: a distance with 5,001 finishers is not a peer of 5000 m
#   with millions.
# ! LOCAL PEAK WAS THE WRONG RULE AND THE TESTS CAUGHT IT. Keeping whichever
#   distance is tallest in its neighbourhood keeps the tallest COMB TOOTH
#   (4699, with 7,000 finishers, beats its eight neighbours and survives) and
#   deletes genuine distances that happen to sit beside a bigger one (3218
#   loses to 3200; 4800 loses to 4828 -- all four are real).
#
# ★ THE DISTINCTION IS MAGNITUDE, NOT RANK. A real race distance holds a
#   meaningful share of the traffic around it; a comb tooth holds a
#   thousandth. 3200 and 3218 are within 0.6% of each other and BOTH have
#   millions of finishers, so both are real and both stay. 4699 has 7,000
#   against 4828's four million, so it is rounding noise and goes.
MERGE_WINDOW = 0.05     # how far to look for a rung's peers
MERGE_SHARE = 0.05      # ...and the share of the biggest peer it must hold


def decomb(rungs, window=MERGE_WINDOW, share=MERGE_SHARE):
    """Drop distances that are a rounding artifact of a bigger neighbour.

    rungs is [(distance, n)]. A distance survives when its finisher count is
    at least `share` of the largest count within `window` of it.
    """
    keep = []
    for d, n in rungs:
        peak = max((m for e, m in rungs if abs(e - d) / d <= window),
                   default=n)
        if peak <= 0 or n / peak >= share:
            keep.append((d, n))
    # ! SORTED, BECAUSE TWO READERS ASSUME IT. --ladder's gap report zips
    #   consecutive pairs, and snapToCorpus's min() does not care but the
    #   report would print negative gaps from an unsorted list.
    return sorted(keep)


def loadLadder(cur, min_n=LADDER_MIN_N):
    """The distances this corpus actually races, de-combed."""
    global _CORPUS_LADDER
    cur.execute(_LADDER_SQL, {"min_n": min_n})
    cur.execute("SELECT distance, n FROM reb_ladder ORDER BY distance")
    raw = [(int(r["distance"]), int(r["n"])) for r in cur.fetchall()]
    _CORPUS_LADDER = decomb(raw)
    return _CORPUS_LADDER, raw


def ladderPower(tol=None, lo=1500, hi=12000, n=200_000):
    """How often a RANDOM distance snaps within tol. The gate's false-positive rate.

    ⚠ A DENSER LADDER IS A WEAKER TEST, AND THAT TRADE HAS TO BE MEASURED.
      snapToLadder's contract is that it is a TEST -- a field wrong for a
      reason other than distance implies a value BETWEEN the rungs, and the
      error is what says so. That only works while the rungs are sparse
      relative to the tolerance. Going from 28 rungs to 94 admitted 1,376 more
      divisions; it also made it easier for a value that means nothing to land
      near something.
      
      So: draw distances log-uniformly across the range real races occupy and
      count how many snap. That fraction is what the gate would pass if the
      implied distances were pure noise -- exactly the `noise%` column the
      pass 1 sweep prints, for the snap instead of the bar.

    ! LOG-UNIFORM, NOT UNIFORM. Race distances span 800 m to 20 km and the
      rungs crowd at the short end; sampling uniformly would put most of the
      draws in a range with three rungs in it and understate the pass rate.
    """
    import math
    if tol is None:
        tol = SNAP_TOL
    if not _CORPUS_LADDER:
        return None
    step = (math.log(hi) - math.log(lo)) / n
    hits = 0
    for i in range(n):
        d = math.exp(math.log(lo) + step * i)
        got = snapToCorpus(d)
        if got and abs(got[1]) <= tol:
            hits += 1
    return hits / n




# ⚠ LOADED ONCE, NOT LOOKED UP PER VENUE, AND THE LAZY VERSION IS WHAT HUNG
#   THE RUN AFTER THE DIVISION COUNT PRINTED.
#
#   It memoised per course_name and queried on a miss -- one round trip per
#   distinct venue. That was survivable while about seven thousand divisions
#   cleared the bar. The times gap table includes the rows the pace guard
#   dropped, whose ratings are extreme by construction, so far more divisions
#   clear it -- and the miss rate is one per venue, so the loop turned into
#   hundreds of thousands of serial round trips with nothing printed between
#   them. The query is fast; the latency is the cost, and it cannot be
#   amortised one row at a time.
#
# ★ reb_course_dist IS SMALL BY CONSTRUCTION -- it is already filtered to
#   venues with COURSE_MIN_N finishers at a distance -- so the whole table
#   fits in a dict and one query replaces all of them.
def loadCourseDistances(cur):
    """Fill _COURSE_DIST from reb_course_dist in one pass."""
    _COURSE_DIST.clear()
    cur.execute("SELECT course_name, distance, n FROM reb_course_dist "
                "ORDER BY course_name, n DESC")
    for r in cur.fetchall():
        _COURSE_DIST.setdefault(r["course_name"], []).append(
            (float(r["distance"]), int(r["n"])))
    return len(_COURSE_DIST)


def courseDistances(cur, course_name):
    """[(distance, n), ...] for one venue, commonest first.

    ! NO QUERY HERE. loadCourseDistances fills the map in buildGap; a venue
      that is absent has no distance over the floor, which is a fact, not a
      cache miss to go and resolve.
    """
    return _COURSE_DIST.get(course_name, ())


def snapToCourse(implied, course_name, label, cur, tol=COURSE_TOL):
    """(distance, err, 'course') using what this venue actually runs, or None.

    Returns the venue's COMMONEST distance within tol of the implied value --
    commonest, not nearest, because the tie this exists to break is between
    two rungs that are both close.
    """
    if not implied or not course_name:
        return None
    best = None
    for d, n in courseDistances(cur, course_name):
        if label and abs(d - float(label)) / float(label) <= 0.02:
            continue                       # the label we are replacing
        if abs(implied - d) / d <= tol and (best is None or n > best[1]):
            best = (d, n)
    if best is None:
        return None
    return float(best[0]), (implied - best[0]) / best[0], "course"


def _named(r):
    """`  -- Venue | Meet` for a comment, or nothing. Never raises on a NULL.

    Both, not the better of the two: the whole point is that findstr should
    match whichever one the person happens to know.
    """
    def clean(v):
        # ! ONE LINE PER ENTRY. A newline in a scraped venue string would split
        #   the generated dict and make corrections.py unimportable, which is
        #   found out four hours into a pipeline. Collapse whitespace rather
        #   than trusting the source.
        return " ".join((v or "").split())[:60]

    parts = [p for p in (clean(r.get("display_name")),
                         clean(r.get("meet_display"))) if p]
    # A venue whose meet name repeats it adds nothing to search on.
    if len(parts) == 2 and parts[0].lower() == parts[1].lower():
        parts.pop()
    return ("  -- " + " | ".join(parts)) if parts else ""


def impliedDistance(label, base, gap):
    """The distance that would put this field back on its own heads.

        rating is proportional to 1/nt,  nt is proportional to d^-K
     => rating_label / rating_true = (d_label / d_true) ** K
     => d_true = d_label * (base / (base + gap)) ** (1/K)
    """
    if not label or not base or base + gap <= 0:
        return None
    return float(label) * (base / (base + gap)) ** (1.0 / K)


def barFor(n, sigma, t1):
    """The gap a field of n has to clear: the absolute bar, or SE_K standard
    errors of ITS OWN median, whichever is larger."""
    import math
    se = 1.25 * sigma / math.sqrt(max(n, 1))
    return max(t1 * sigma, SE_K * se)


def explain1(rows, key, sigma, t1, unanimity, cur):
    """Why did THIS division not get an override? Every gate, in order.

    ★ BECAUSE GUESSING AT IT FROM THE SUMMARY DOES NOT WORK. 26359/0 has been
      missing from three consecutive runs and each explanation offered for it
      was a hypothesis about a different gate. The gates are in this file;
      they can just be printed.
    """
    print(f"\n  WHY {key[0]}/{key[1]} IS NOT IN PASS 1\n")
    row = next((r for r in rows
                if (r["meet_id"], r["div_id"]) == key), None)
    if row is None:
        # It never reached the aggregate at all. Say which stage lost it.
        cur.execute("SELECT count(*) AS n FROM reb_gap "
                    "WHERE meet_id = %s AND div_id = %s", key)
        n_gap = cur.fetchone()["n"]
        print(f"    rows in the gap table:            {n_gap}")
        if n_gap == 0:
            cur.execute("SELECT count(*) AS n, count(speed_rating) AS rated "
                        "FROM results WHERE meet_id = %s AND div_id = %s", key)
            r0 = cur.fetchone()
            print(f"    rows in results:                  {r0['n']}")
            print(f"    of those with a speed_rating:     {r0['rated']}")
            print(f"\n    Every rated row was dropped building the gap table. "
                  f"That table needs\n    an athlete to have >= "
                  f"--min-own-races rated races ANYWHERE, so their\n"
                  f"    median means something. Lower it, or this division "
                  f"is made of\n    athletes with no other rated results.")
        else:
            print(f"    ...but under --min-field, so it was never aggregated.")
        return
    # ★ THE ROWS, BECAUSE THE AGGREGATE CAN BE INNOCENT WHILE THE DIVISION
    #   IS NOT. 26359/0 reports 22 rated rows and a field median gap of -2.5
    #   -- clean by this measure -- for a race whose winner ran 18:02.9
    #   against a five-mile label. Both can be true only if the athletes'
    #   OWN MEDIANS are built from the bad races: a first-year runner with
    #   three rated results, two of them at this venue, has a median that IS
    #   the corrupted value, so their gap collapses to zero.
    #
    #   `meets` is the count of DISTINCT OTHER meets behind that athlete's
    #   median. At 0 or 1 the median is largely self-referential and the gap
    #   measured against it means little; the division's median gap is then
    #   an average over rows that cannot see their own fault.
    cur.execute("""
        WITH here AS (
            SELECT ident, speed_rating, med, gap
            FROM   reb_gap WHERE meet_id = %s AND div_id = %s
        )
        SELECT h.*,
               (SELECT count(DISTINCT g2.meet_id) FROM reb_gap g2
                WHERE g2.ident = h.ident AND g2.meet_id <> %s) AS other_meets
        FROM   here h ORDER BY h.gap
    """, (key[0], key[1], key[0]))
    detail = cur.fetchall()
    print(f"    {'rating':>7} {'own med':>8} {'gap':>7} {'other meets':>12}")
    for d in detail:
        print(f"    {d['speed_rating']:>7.1f} {d['med']:>8.1f} "
              f"{d['gap']:>+7.1f} {d['other_meets']:>12}")
    thin = sum(1 for d in detail if d["other_meets"] <= 1)
    if thin:
        print(f"\n    {thin} of {len(detail)} rows belong to athletes with "
              f"<= 1 other meet behind\n    their median. For those the gap "
              f"is measured against a number this\n    same division helped "
              f"produce, and cannot show the fault.")
    print()

    n, gap = row["n"], float(row["med_gap"])
    bar = barFor(n, sigma, t1)
    print(f"    rated rows (n):                   {n}")
    print(f"    field median gap:                 {gap:+.1f}")
    print(f"    bar for a field this size:        {bar:.1f}"
          f"   {'PASS' if abs(gap) > bar else 'FAILS HERE'}")
    if abs(gap) <= bar:
        return
    side = float(row["same_side"])
    print(f"    share of the field on one side:   {side:.0%}"
          f"   {'PASS' if side >= unanimity else 'FAILS HERE -> pass 2'}")
    if side < unanimity:
        return
    implied = impliedDistance(row["distance"], float(row["base"]), gap)
    print(f"    label distance:                   {row['distance']}")
    if implied is None:
        print(f"    implied distance:                 unusable label")
        return
    snapped, err = snapToLadder(implied)
    print(f"    implied distance:                 {implied:.0f}")
    print(f"    nearest rung:                     {snapped:.0f} "
          f"({err:+.1%})   "
          f"{'PASS' if abs(err) <= SNAP_TOL else f'FAILS HERE (tol {SNAP_TOL:.0%})'}")
    if abs(err) > SNAP_TOL:
        return
    ratio = snapped / float(row["distance"])
    print(f"    change:                           {ratio:.2f}x   "
          f"{'FAILS HERE' if ratio > MAX_CHANGE or ratio < 1 / MAX_CHANGE else 'PASS'}")
    if ratio > MAX_CHANGE or ratio < 1 / MAX_CHANGE:
        return
    if gap < 0:
        # Mirror of pass1's direction gate. The run also requires the COURSE
        # to corroborate a slow-field relabel; this ladder-only view cannot
        # check that half, so a PASS here is necessary, not sufficient.
        side_ok = side >= NEG_UNANIMITY
        mag_ok = abs(gap) >= NEG_BAR_MULT * bar
        print(f"    slow-field gate (gap < 0):        "
              f"side {side:.0%} vs {NEG_UNANIMITY:.0%} "
              f"{'PASS' if side_ok else 'FAILS HERE'};  "
              f"|gap| {abs(gap):.1f} vs {NEG_BAR_MULT:.0f}x bar "
              f"= {NEG_BAR_MULT * bar:.1f} "
              f"{'PASS' if mag_ok else 'FAILS HERE'};  "
              f"course corroboration also required (not visible here)")
        if not (side_ok and mag_ok):
            return
    print(f"\n    It clears every gate -- it should be in the output.")


# ------------------------------------------------------------------ #
#  --explain IN SECONDS: the corpus build, restricted to one division
# ------------------------------------------------------------------ #
#
# ★ THE FULL PATH PAID ~12 MINUTES TO ANSWER A QUESTION ABOUT ~100 ROWS.
#   --explain used to run the whole pipeline -- 32M-row gap table, 4-minute
#   form curve, course cache -- and then read one division out of it. But a
#   division's verdict only needs its OWN athletes' careers: their medians,
#   the cell difficulties, the revert state and the label. All of that is
#   the same SQL, restricted to those athletes -- a few thousand rows.
#
# ⚠ TWO KNOWN DIFFERENCES FROM THE FULL RUN, BOTH PRINTED AT RUN TIME:
#     form   the race-of-season correction needs the full corpus and is
#            skipped; it is worth at most ~3 points. A verdict within 3 of
#            a boundary deserves --explain-full.
#     pm     the pool scale constant comes from the cache the last full run
#            wrote (it cancels inside every gap; it only sets the printed
#            scale). No cache yet -> recomputed live, a one-time ~12s cost.

_EXPLAIN_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "engine", "data",
                              "rebuild_explain_cache.pkl")


def _writeExplainCache(cur):
    """pm-per-pool and the raw ladder, saved so --explain skips the two
    corpus aggregates that dominate its start-up. Written by every full
    build, so it is at most one run stale -- and both values move only
    when the corpus does."""
    import pickle
    try:
        cur.execute("SELECT pool, pm FROM reb_pm")
        pm = {r["pool"]: float(r["pm"]) for r in cur.fetchall()}
        cur.execute("SELECT distance, n FROM reb_ladder ORDER BY distance")
        raw = [(int(r["distance"]), int(r["n"])) for r in cur.fetchall()]
        with open(_EXPLAIN_CACHE, "wb") as f:
            pickle.dump({"pm": pm, "ladder_raw": raw}, f)
    except Exception as e:        # a convenience, never a failure mode
        print(f"  (explain cache not written: {e})")


def _loadExplainCache():
    import pickle
    try:
        with open(_EXPLAIN_CACHE, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _restrict(sql, anchor, extra):
    """Anchored injection into a shared SQL string -- LOUD when the anchor
    drifts, instead of silently running the unrestricted corpus query."""
    if anchor not in sql:
        raise AssertionError(f"explain fast-path anchor missing: {anchor!r}")
    return sql.replace(anchor, anchor + extra)


def explainFast(cur, args):
    """explain1, fed by a division-restricted build. Seconds, not minutes."""
    import time
    meet, _, div = args.explain.partition("/")
    key = (int(meet), int(div or 0))
    table = _TABLE[args.sport]
    sys.path.insert(0, "racecast")
    from build_ranking_results import _XC_TFRRS_DIST_SQL

    _t = time.time()
    cur.execute(_XC_TFRRS_DIST_SQL)
    if args.as_if_wiped:
        cur.execute(_UNOVERRIDE_SQL, {"k": K, "tol": args.same_tol})
    else:
        cur.execute(_UNOVERRIDE_NONE)
    cache = _loadExplainCache()
    if cache and cache.get("ladder_raw"):
        global _CORPUS_LADDER
        _CORPUS_LADDER = decomb(cache["ladder_raw"])
    else:
        loadLadder(cur)
    _t = _step(_t, "revert state + ladder")

    cur.execute(f"""
        SELECT DISTINCT athlete_id, person_id
        FROM   {table}
        WHERE  meet_id = %s AND div_id = %s
    """, key)
    seen = cur.fetchall()
    aids = sorted({r["athlete_id"] for r in seen
                   if r["athlete_id"] is not None})
    pids = {r["person_id"] for r in seen if r["person_id"] is not None}
    if not aids and not pids:
        print(f"\n  WHY {key[0]}/{key[1]} IS NOT IN PASS 1\n")
        print(f"    no rows with an athlete id for that meet/div in {table}. "
              f"Check the ids:\n    on a colliding meet page the other "
              f"source's division is a different key,\n    and tfrrs XC "
              f"div_ids are small per-meet indices, not global ones.")
        return
    # The person map, restricted to this division's athletes: rows here that
    # linkage has not reached yet still join their person-keyed careers.
    cur.execute("DROP TABLE IF EXISTS reb_person")
    cur.execute("CREATE TEMP TABLE reb_person "
                "(athlete_id bigint, person_id bigint)")
    if aids:
        cur.execute(f"""
            INSERT INTO reb_person
            SELECT athlete_id, min(person_id)
            FROM   {table}
            WHERE  athlete_id = ANY(%(a)s) AND person_id IS NOT NULL
            GROUP  BY athlete_id
        """, {"a": aids})
        cur.execute("SELECT person_id FROM reb_person")
        pids |= {r["person_id"] for r in cur.fetchall()}
    cur.execute("CREATE INDEX ON reb_person (athlete_id)")
    pids = sorted(pids)
    print(f"  {len(aids):,} athletes in {key[0]}/{key[1]} "
          f"({len(pids):,} resolve to linked persons)")
    # ! [-1] STAND-INS: psycopg2 renders an empty list as '{}', and Postgres
    #   cannot type an empty array literal inside = ANY().
    aids = aids or [-1]
    pids = pids or [-1]

    cur.execute("SET LOCAL work_mem = '512MB'")
    # reb_pool restricted to these athletes. Their pool only routes the pm
    # scale constant, so a missing row degrades the printed level, never the
    # gap.
    cur.execute(_restrict(_POOL_SQL,
                          "  AND  sport = %(sport)s",
                          "\n  AND  person_id = ANY(%(ids)s)"),
                {"sport": args.sport, "ids": pids + aids})
    cur.execute(_CELL_SQL)
    if cache and cache.get("pm"):
        cur.execute("DROP TABLE IF EXISTS reb_pm")
        cur.execute("CREATE TEMP TABLE reb_pm (pool text, pm float8)")
        from psycopg2.extras import execute_values
        execute_values(cur, "INSERT INTO reb_pm VALUES %s",
                       sorted(cache["pm"].items()))
        pm_src = "cached from the last full run"
    else:
        cur.execute(_PM_SQL.format(table=table))
        pm_src = "recomputed live -- a full pass run writes the cache"
    _t = _step(_t, f"pools + cells (pm {pm_src})")

    # The real gap body, restricted. The OR-of-two-indexable-conditions
    # covers both id spaces so an index on either column can serve it: a
    # career row is person-keyed (pids, the map included) or still raw
    # (aids).
    cur.execute(_restrict(
        _GAP_BODY_TIMES,
        "      AND  COALESCE(r.person_id, rp.person_id, r.athlete_id) "
        "IS NOT NULL",
        "\n      AND  (r.person_id = ANY(%(pids)s) "
        "OR r.athlete_id = ANY(%(aids)s))").format(table=table),
        {"min_own": args.min_own_races, "pids": pids, "aids": aids})
    _t = _step(_t, "gap table over these athletes' full careers")
    print("  ⚠ fast mode: the form (race-of-season) correction is skipped -- "
          "it needs the\n    full corpus and is worth at most ~3 points. A "
          "verdict within 3 of a\n    boundary deserves --explain-full. "
          "Ratings print on the cached pm scale;\n    the gap and median "
          "columns are the evidence.")

    # ★ WHEN THE DIVISION IS THIN, SAY WHY IT IS THIN. "1 row in the gap
    #   table" has two very different causes -- rows the backfill has not
    #   normalized yet, and athletes without min_own rated rows IN THIS
    #   SPORT -- and 264234/1050225 burned a round-trip on exactly that
    #   ambiguity. Both are one cheap query.
    cur.execute("SELECT count(*) AS n FROM reb_gap "
                "WHERE meet_id = %s AND div_id = %s", key)
    if cur.fetchone()["n"] < args.min_field:
        cur.execute(f"""
            SELECT count(*) AS n,
                   count(normalized_time) AS with_nt,
                   count(speed_rating)    AS engine_rated
            FROM   {table} WHERE meet_id = %s AND div_id = %s
        """, key)
        d = cur.fetchone()
        print(f"\n  WHY THE DIVISION IS THIN IN THE GAP TABLE\n")
        print(f"    rows in {table}:                  {d['n']}")
        print(f"    with a normalized_time:           {d['with_nt']}")
        print(f"    engine-rated:                     {d['engine_rated']}")
        if d["with_nt"] == 0:
            print(f"\n    NOTHING here is normalized -- the backfill has not "
                  f"reached this meet.\n    Until 05_backfill runs, no pass "
                  f"can judge it: there are no times on a\n    common scale "
                  f"to judge.")
        cur.execute(f"""
            SELECT least(c.career, %(mo)s) AS bucket, count(*) AS athletes
            FROM (
                SELECT COALESCE(r.person_id, rp.person_id, r.athlete_id)
                           AS ident,
                       count(*) FILTER (WHERE r.normalized_time IS NOT NULL
                                          AND r.normalized_time > 0)
                           AS career
                FROM   {table} r
                LEFT   JOIN reb_person rp ON rp.athlete_id = r.athlete_id
                WHERE  (r.person_id = ANY(%(pids)s)
                        OR r.athlete_id = ANY(%(aids)s))
                GROUP  BY 1
            ) c GROUP BY 1 ORDER BY 1
        """, {"pids": pids, "aids": aids, "mo": args.min_own_races})
        hist = {int(r["bucket"]): int(r["athletes"]) for r in cur.fetchall()}
        parts = [f"{b}: {hist.get(b, 0)}" for b in range(args.min_own_races)]
        parts.append(f"{args.min_own_races}+: "
                     f"{hist.get(args.min_own_races, 0)}")
        print(f"\n    career sizes (normalized rows anywhere in {table}, "
              f"this sport only --\n    a TF career does not testify in an "
              f"XC gap table):\n        {'   '.join(parts)}"
              f"    (min_own = {args.min_own_races})")
        print(f"    athletes under min_own contribute no gap row: their "
              f"median would be\n    mostly this division judging itself.")

    anchor = "    FROM   reb_gap g\n    GROUP  BY"
    if anchor not in _PASS1_SQL:
        raise AssertionError("explain fast-path: _PASS1_SQL shape changed")
    one = _PASS1_SQL.replace(
        anchor,
        "    FROM   reb_gap g\n"
        "    WHERE  g.meet_id = %(meet)s AND g.div_id = %(div)s\n"
        "    GROUP  BY")
    cur.execute(one, {"min_field": args.min_field,
                      "meet": key[0], "div": key[1]})
    explain1(cur.fetchall(), key, args.sigma, args.t1, args.unanimity, cur)


# ------------------------------------------------------------------ #
#  HOW MUCH TO TRUST ONE PROPOSAL
# ------------------------------------------------------------------ #
#
# ⚠ THE SNAP CANNOT CARRY THIS DECISION, AND THE MEASUREMENT SAYS SO. On the
#   de-combed 58-rung ladder, 74.2% of PURE NOISE still snaps within 3%. That
#   is not a ladder problem -- the surviving rungs around 2092, 2172, 2253,
#   2400, 2414, 2500, 2574, 2600, 2655, 2700 are 3-4% apart and every one of
#   them is a real race distance. Cross country races something every hundred
#   metres between 2 km and 5 km, so "it landed near a real distance" is worth
#   almost nothing in the range where most races are.
#
#   Removing the comb moved the number from 74.9% to 74.2%. The comb was not
#   the cause. The sport is.
#
# ★ SO THE EVIDENCE THAT ACTUALLY DISCRIMINATES IS RANKED, AND THE OUTPUT IS
#   TIERED RATHER THAN GATED:
#
#     A  the COURSE corroborates it. A venue races one to three distances, not
#        fifty-eight, so landing on one of them is strong -- this is the test
#        the corpus ladder cannot be. These are safe to append.
#
#     B  no course history, but the corpus snap is tight AND the field is big
#        enough that its median is precise. Reviewable in bulk.
#
#     C  everything else that cleared the bar. Something is wrong with these
#        divisions -- they are 11+ points off their own athletes' heads -- but
#        the distance proposed is a guess. Report, do not write.
#
# ! THE BAR AND THE UNANIMITY GATE ARE UNCHANGED AND STILL DO THE REAL WORK.
#   This only decides how much to trust the NUMBER once a division has already
#   been judged wrong.
TIER_B_SNAP = 0.02      # a corpus snap this tight, and...
TIER_B_SE = 2.0         # ...a field whose median is good to this many points

# ⚠ THE TWO DIRECTIONS ARE NOT THE SAME EVIDENCE, so the gate is asymmetric.
#   A field ABOVE its own heads (label too long, propose shorter) has one
#   explanation -- nobody runs 60 points over their own career median, so the
#   distance is wrong. A field BELOW its own heads (label too short, propose
#   longer) has several: a season opener before anyone is fit, August heat, a
#   JV tail jogging it in, first-year athletes measured against medians they
#   have outgrown. Measured on the 2026-08-23 run, the negative population
#   sat at -13..-22 -- exactly the size of an opener's fitness deficit, never
#   the -40 a real 1.2x distance error can produce alone -- and proposed
#   label x1.2 across the board: college 5000s -> 6000 dated late August, and
#   Yellow Jacket's 3218 -> 4800 re-derived from its own slow tail while the
#   14:52 up front pinned the true distance.
#
# ★ SO THE SLOW DIRECTION IS GATED ON UNANIMITY, NOT MAGNITUDE. A wrong
#   distance shifts EVERY finisher by the same ratio; fitness is
#   heterogeneous -- someone in every August field trained all summer and
#   sits above their median. So -15 with everybody on one side is a distance
#   fault, while -15 at 80% is an opener. A magnitude bar cannot make that
#   distinction (a real 1.2x error IS about -15), and was tried and removed.
#   A negative-gap division stays condemnable when the COURSE itself
#   corroborates the longer distance AND the field is near-unanimous.
#
# ! AND THE FAILURES ARE A POPULATION, NOT NOISE. A slow division that fails
#   unanimity is exactly what a MIXED division looks like -- two races, or a
#   race plus a jogging tail -- so they land in skipped with one countable
#   reason, ready to become a later pass's candidate list. Their individually
#   impossible rows remain pass 3's per-result business either way.
NEG_UNANIMITY = 0.95    # slow fields need ~everybody on one side, vs 75% fast
# ⚠ AND A MAGNITUDE FLOOR, BY POLICY: a longer distance RAISES every rating
#   in the division, and the owner's call is that ratings move down freely
#   but up only when it is blatant. 3x the bar (~-33 on a typical field) is
#   Rocklin (-40) and Nevada Union (-35) territory; McIver-class ambiguity
#   (-13) never raises a rating no matter how unanimous.
NEG_BAR_MULT = 3.0      # |adjusted gap| must clear THREE times the bar

# ★ HAND-REJECTED DIVISIONS -- judged by a human, not by a gate, because they
#   are the one fault class no statistic can separate from a wrong label: a
#   course that is SUPPOSED to be slow. Farragut's "Annual Hill Climb" is
#   unanimous, blatant and course-corroborated -- and correct at 2993m: the
#   oddball label is a wheeled course (error processes swap real race
#   distances, they don't invent 2993), the meet name declares the terrain,
#   and the same label recurs every edition. Relabeling it 5000 would mint
#   +40-point ratings. Listed here, not deleted from pass1.py by hand, so
#   every regeneration stays clean. Reviewed 2026-08-24.
HAND_REJECTED = {
    (174759, 717161): "Farragut Annual Hill Climb -- terrain, not distance",
    (174766, 717169): "Farragut Annual Hill Climb -- terrain, not distance",
    (225606, 901254): "Farragut Annual Hill Climb -- terrain, not distance",
}


def tierFor(source, err, gap, n, sigma):
    import math
    if source == "course":
        return "A"
    se = 1.25 * sigma / math.sqrt(max(n, 1))
    if abs(err) <= TIER_B_SNAP and se <= TIER_B_SE:
        return "B"
    return "C"


# Every snap failure's error magnitude, so report1 can show the distribution
# instead of a count -- the difference between "the tolerance is too tight" and
# "these are not distance faults".
_SNAP_MISS = []


def pass1(rows, sigma, t1, unanimity, cur=None):
    """(condemned, routed_to_2, skipped) for the whole-division pass."""
    condemned, routed, skipped = [], [], []
    _SNAP_MISS.clear()
    # ! IT SAYS WHERE IT IS. This loop runs over half a million rows and
    #   printed nothing until it finished, so a slow one was
    #   indistinguishable from a hung one -- which is exactly how the
    #   per-venue round trip above went unnoticed.
    _every = max(1, len(rows) // 10)
    for _i, r in enumerate(rows):
        if _i and _i % _every == 0:
            print(f"    ...{_i:,}/{len(rows):,} divisions judged", flush=True)
        rej = HAND_REJECTED.get((r["meet_id"], r["div_id"]))
        if rej:
            skipped.append((r, f"hand-rejected: {rej}"))
            continue
        gap = float(r["med_gap"])
        if abs(gap) <= barFor(r["n"], sigma, t1):
            skipped.append((r, "within the bar"))
            continue
        # ★ THE UNANIMITY GATE IS THE ROUTER. A division split between two
        #   races has about half its field on each side of zero and fails
        #   here by construction -- which is exactly how pass 2 gets its
        #   candidates without having to re-judge the corpus.
        if float(r["same_side"]) < unanimity:
            routed.append(r)
            continue
        implied = impliedDistance(r["distance"], float(r["base"]), gap)
        if implied is None:
            skipped.append((r, "no usable label distance"))
            continue
        # ★ THE COURSE FIRST, THE LADDER SECOND. What this venue actually
        #   runs beats a global list of what the sport runs -- see
        #   snapToCourse. Falls through to the ladder for a venue with no
        #   history, which is most of the ones that need fixing.
        hit = (snapToCourse(implied, r.get("course_name"), r["distance"], cur)
               if cur is not None else None)
        if hit is not None:
            snapped, err, _src = hit
            source = "course"
        else:
            source = "corpus"
            # ★ THE CORPUS LADDER, NOT THE HAND LIST. See _LADDER_SQL: the
            #   28-rung list has 25% gaps and rejected 2,598 divisions it had
            #   already agreed were wrong.
            hit = snapToCorpus(implied)
            snapped, err = hit if hit else snapToLadder(implied)
            if abs(err) > SNAP_TOL:
                # A field wrong for a reason OTHER than distance implies a
                # value between the rungs. That is not a distance fault and
                # must not be written as one.
                skipped.append((r, f"implied {implied:.0f} snaps poorly "
                                   f"({err:+.1%})"))
                _SNAP_MISS.append(abs(err))
                continue
        ratio = snapped / float(r["distance"])
        if abs(ratio - 1) < 0.02:
            skipped.append((r, "snaps back to the label"))
            continue
        if ratio > MAX_CHANGE or ratio < 1.0 / MAX_CHANGE:
            skipped.append((r, f"would move the distance {ratio:.2f}x, past "
                               f"the {MAX_CHANGE:.1f}x cap"))
            continue
        # The asymmetric direction gate -- see the block above NEG_UNANIMITY.
        if gap < 0 and not (source == "course"
                            and float(r["same_side"]) >= NEG_UNANIMITY
                            and abs(gap) >= NEG_BAR_MULT
                                * barFor(r["n"], sigma, t1)):
            skipped.append((r, "field slow but not blatantly -- a longer "
                               "distance raises ratings, so it must be "
                               "obvious"))
            continue
        condemned.append({**r, "implied": implied, "snapped": snapped,
                          "snap_err": err, "gap": gap, "source": source,
                          "tier": tierFor(source, err, gap, r["n"], sigma)})
    return condemned, routed, skipped


# ------------------------------------------------------------------ #
#  PASS 2 -- the division holds two races, split by sex
# ------------------------------------------------------------------ #

# ! SAME SHAPE AS PASS 1, AND FOR THE SAME REASON. Group, then label.
_PASS2_SQL = """
WITH agg AS (
    SELECT g.meet_id, g.div_id, g.gender,
           count(*)                                           AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY g.gap) AS med_gap,
           avg(g.med)                                         AS base,
           array_agg(g.result_id)                             AS result_ids
    FROM   reb_gap g
    WHERE  g.gender IN ('M', 'F')
      AND  (g.meet_id, g.div_id) IN (SELECT meet_id, div_id FROM reb_pass2)
    GROUP  BY g.meet_id, g.div_id, g.gender
)
SELECT a.meet_id, a.div_id, a.gender, a.n, a.med_gap, a.base, a.result_ids,
       lbl.distance AS distance
FROM   agg a
__LABEL__
""".replace("__LABEL__", _LABEL_LATERAL.format(g="a"))


def pass2(halves, sigma, t2, min_minority):
    """[{result_id: (distance, gender)}] for divisions holding two races."""
    bar = t2 * sigma
    by_div = {}
    for h in halves:
        by_div.setdefault((h["meet_id"], h["div_id"]), {})[h["gender"]] = h

    out, skipped = [], []
    # keys where a half was refused because we could not resolve it, as
    # opposed to refused because it was fine. See below.
    unresolved = set()
    for key, sides in by_div.items():
        if set(sides) != {"M", "F"}:
            skipped.append((key, "only one sex has rated rows"))
            continue
        m, f = sides["M"], sides["F"]
        minority = min(m["n"], f["n"])
        if minority < min_minority:
            skipped.append((key, f"smaller half has {minority} rated rows, "
                                 f"under {min_minority}"))
            continue
        split = abs(float(m["med_gap"]) - float(f["med_gap"]))
        if split <= bar:
            # Both sexes shifted the same way: one race, uniformly wrong.
            # That is pass 1's shape and pass 1 already declined it for some
            # other reason -- do not manufacture a split to explain it.
            skipped.append((key, f"halves agree ({split:.1f} <= {bar:.1f}) "
                                 f"-- one race, not two"))
            continue
        for side in (m, f):
            implied = impliedDistance(side["distance"], float(side["base"]),
                                      float(side["med_gap"]))
            if implied is None:
                continue
            snapped, err = snapToLadder(implied)
            if abs(err) > SNAP_TOL:
                skipped.append((key, f"{side['gender']} implied "
                                     f"{implied:.0f} snaps poorly "
                                     f"({err:+.1%})"))
                unresolved.add(key)      # we could not tell what it ran
                continue
            # ! THE HALF THAT IS ALREADY RIGHT GETS NOTHING. In a split
            #   division one half usually sits at gap ~0 -- it ran the
            #   labelled race -- and implies its own label straight back.
            #   The first run pinned those: 53464/227519 F, 11 rows at gap
            #   +3.5, written as 2400 against a 2414 label. A redundant pin
            #   is not harmless, because it also fixes a GENDER on rows whose
            #   gender was never in question.
            if abs(snapped / float(side["distance"]) - 1) < 0.02:
                skipped.append((key, f"{side['gender']} implies its own "
                                     f"label back -- it ran the labelled "
                                     f"race, nothing to pin"))
                continue
            # ⚠ AND THE HALF ITSELF HAS TO BE OFF, not merely the pair.
            #   The halves differing by more than the bar says ONE of them is
            #   wrong; it does not license moving both. 164403/678570 emitted
            #   M with 121 rows at gap -5.8 -- below the bar a field that size
            #   has to clear to be called wrong at all -- purely because its
            #   5-row partner sat 14.5 away.
            own_bar = barFor(side["n"], sigma, t2)
            if abs(float(side["med_gap"])) <= own_bar:
                skipped.append((key, f"{side['gender']} is {side['med_gap']:+.1f} "
                                     f"from its own median, inside the "
                                     f"{own_bar:.1f} bar for {side['n']} rows "
                                     f"-- it is not the broken half"))
                continue
            out.append({"meet_id": key[0], "div_id": key[1],
                        "gender": side["gender"], "n": side["n"],
                        "distance": snapped, "split": split,
                        "med_gap": float(side["med_gap"]),
                        "result_ids": side["result_ids"]})
    # ⚠ ONE HALF IS THE NORMAL ANSWER; ONE HALF PLUS AN UNRESOLVED PARTNER
    #   IS NOT.
    #
    #   Usually exactly one half is wrong -- the other ran the labelled race
    #   and needs nothing -- so emitting a single side is correct and common.
    #   The dangerous case is narrower: a partner we could not RESOLVE,
    #   because its implied distance landed between the rungs. There the half
    #   still carrying the fault is the one that got dropped, and pinning its
    #   partner moves rows that were already right while leaving the broken
    #   race untouched. That is 44609/191385 (F only) and 47213/201553
    #   (M only) from the second run.
    #
    #   An earlier version keyed this on "fewer than two sides emitted",
    #   which conflated the two and threw away every genuine one-sided split.
    if unresolved:
        out = [o for o in out if (o["meet_id"], o["div_id"]) not in unresolved]
        for k in unresolved:
            skipped.append((k, "its partner half could not be resolved -- "
                               "pinning this one alone would move the "
                               "correct race and leave the broken one"))
    return out, skipped


# ------------------------------------------------------------------ #
#  PASS 3 -- one row is corrupt, and cannot be saved
# ------------------------------------------------------------------ #
#
# ★ A DROP, NOT A FIX, AND THAT IS FORCED. Pass 1 and 2 can repair a row
#   because a GROUP of rows says what the right answer is -- a field agreeing
#   on a distance is evidence. One row disagreeing with its own athlete and
#   its own field carries no such evidence: any distance or time written for
#   it would be invented. So it goes to _RESULT_DROP.
#
# ! THE ONE SAVABLE CASE IS HANDLED SEPARATELY. If the row's implied distance
#   snaps cleanly to a rung AND another division at the SAME MEET races that
#   distance, it is a runner recorded in the wrong race rather than a corrupt
#   row -- there is an external witness. Those emit a _RESULT_OVERRIDE
#   instead, and are reported apart so the two are never confused.

_PASS3_SQL = """
WITH adj AS (
    -- ★ POST-PASS-1 RATINGS, COMPUTED NOT RE-SOLVED. A distance override
    --   multiplies the field's normalized time by (d_old/d_new)^K and rating
    --   is inversely proportional to it, so the rating pass 1 will produce is
    --   exactly rating * (d_new/d_old)^K. Same arithmetic audit_overrides
    --   uses for `after`.
    --
    -- ⚠ THE ATHLETE'S MEDIAN IS LEFT ALONE, and that is a real approximation:
    --   it is taken over that athlete's whole career, so one division moving
    --   shifts it by roughly 1/n_races. With the median over 5+ races and one
    --   race changing, it usually does not move at all. The residue lands on
    --   the NEXT iteration, after a re-solve, which is what convergence is
    --   for.
    SELECT g.*,
           g.speed_rating * COALESCE(f.scale, 1.0)             AS adj_rating,
           g.speed_rating * COALESCE(f.scale, 1.0) - g.med     AS adj_gap
    FROM   reb_gap g
    LEFT   JOIN reb_fix f ON f.meet_id = g.meet_id AND f.div_id = g.div_id
    -- ! PASS 2'S ROWS ARE GONE, NOT ADJUSTED. Pass 2 assigns a row its own
    --   distance AND sex; there is no single scale for the division, and
    --   those rows are already accounted for. Leaving them in is what made
    --   the first version of this pass re-report all 20 girls of a split
    --   division that pass 2 had just handled.
    WHERE  g.result_id NOT IN (SELECT result_id FROM reb_pinned)
), div AS (
    SELECT meet_id, div_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY adj_gap) AS div_gap,
           count(*) AS n
    FROM   adj
    GROUP  BY meet_id, div_id
)
SELECT a.result_id, a.meet_id, a.div_id, a.ident,
       a.adj_rating AS speed_rating, a.med, a.adj_gap AS gap,
       a.time_seconds, d.div_gap, d.n AS div_n,
       lbl.distance AS distance, lbl.course_name
FROM   adj a
JOIN   div d ON d.meet_id = a.meet_id AND d.div_id = a.div_id
LEFT   JOIN reb_spread sp ON sp.ident = a.ident
__LABEL__
-- ⚠ ASYMMETRIC BAR -- see SIG_FLOOR. The fast side is judged against the
--   athlete's OWN scatter (clamped to [SIG_FLOOR, pooled sigma], pooled
--   fallback under SPREAD_MIN_RACES): a metronomic elite's typo trips at
--   ~t3*1.5 points instead of hiding under the average kid's bar. The slow
--   side keeps the pooled bar: deliberately jogged races are real, common,
--   and board-harmless.
WHERE  (CASE WHEN a.adj_gap > 0
             THEN a.adj_gap > %(t3)s *
                  COALESCE(greatest(%(sig_floor)s,
                                    least(%(sigma)s, sp.raw_sig)),
                           %(sigma)s)
             ELSE -a.adj_gap > %(bar)s END)
  AND  abs(a.adj_gap - d.div_gap) > %(vs_field)s
ORDER  BY abs(a.adj_gap) DESC
LIMIT  %(limit)s
""".replace("__LABEL__", _LABEL_LATERAL.format(g="a"))


def stagePriorPasses(cur, fixed, pinned):
    """Put pass 1's scale factors and pass 2's pinned rows where the SQL can
    see them. Both tables always exist, empty if the pass found nothing, so
    pass 3's query has one shape."""
    from psycopg2.extras import execute_values
    cur.execute("DROP TABLE IF EXISTS reb_fix; DROP TABLE IF EXISTS reb_pinned")
    cur.execute("CREATE TEMP TABLE reb_fix "
                "(meet_id bigint, div_id bigint, scale double precision)")
    cur.execute("CREATE TEMP TABLE reb_pinned (result_id bigint)")
    if fixed:
        execute_values(cur, "INSERT INTO reb_fix VALUES %s", fixed)
    if pinned:
        execute_values(cur, "INSERT INTO reb_pinned VALUES %s",
                       [(r,) for r in pinned])
    cur.execute("CREATE INDEX ON reb_pinned (result_id)")
    cur.execute("ANALYZE reb_fix; ANALYZE reb_pinned")
    print(f"  pass 3 sees {len(fixed):,} divisions already re-scaled by pass 1 "
          f"and {len(pinned):,} rows already pinned by pass 2")


def pass3(cur, sigma, t3, vs_field, limit, max_per_div=3,
          max_frac=0.05, max_per_ath=2, max_per_meet=4, form=True):
    # Per-athlete scatter for the fast-side bar (see SIG_FLOOR). Built here,
    # not in buildGap: pass 1 and 2 never read it, and it is one more full
    # sort over the gap table. MAD is taken over the RAW gaps -- the form
    # curve is a population correction and has no business inside one
    # athlete's personal spread.
    gapcol = "gap_raw" if form else "gap"
    cur.execute("SET LOCAL work_mem = '2GB'")
    cur.execute(f"""
        DROP TABLE IF EXISTS reb_spread;
        CREATE TEMP TABLE reb_spread AS
        SELECT ident,
               1.4826 * percentile_cont(0.5)
                   WITHIN GROUP (ORDER BY abs({gapcol})) AS raw_sig
        FROM   reb_gap
        GROUP  BY ident
        HAVING count(*) >= {int(SPREAD_MIN_RACES)};
        CREATE INDEX ON reb_spread (ident);
        ANALYZE reb_spread;
    """)
    cur.execute(_PASS3_SQL, {"bar": t3 * sigma, "t3": t3, "sigma": sigma,
                             "sig_floor": SIG_FLOOR,
                             "vs_field": vs_field * sigma, "limit": limit})
    rows = cur.fetchall()
    div_n = {(r["meet_id"], r["div_id"]): r["div_n"] for r in rows}
    # Which distances are raced elsewhere at the same meet -- the witness that
    # separates "recorded in the wrong race" from "corrupt".
    #
    # ⚠ BOTH SOURCES, NOT JUST `meets`. `meets` is anet-only, so at a
    #   tfrrs-covered meet every other race was invisible, no witness was
    #   found, and a reassignable runner became a deletion -- the same
    #   blindness audit_overrides documented for its stored-distance
    #   fallback. Measured on the 2026-08-24 --limit 5000 run: the +90 drop
    #   band clustered at St. Olaf, Les Bolstadt, Kentucky Horse Park --
    #   multi-race invitational venues -- with 17 savables against hundreds
    #   of wrong-race-shaped drops.
    meets = tuple({r["meet_id"] for r in rows}) or (0,)
    cur.execute("""
        SELECT meet_id, array_agg(DISTINCT distance) AS dists
        FROM (
            SELECT meet_id, distance
            FROM   meets WHERE meet_id = ANY(%(meets)s) AND distance > 0
            UNION ALL
            SELECT mt.meet_id, (d.value ->> 'distance')::real AS distance
            FROM   meets_tfrrs mt,
                   jsonb_each(mt.division_distances::jsonb) d
            WHERE  mt.meet_id = ANY(%(meets)s) AND mt.sport = 'XC'
              AND  (d.value ->> 'distance')::real > 0
        ) x
        GROUP  BY meet_id
    """, {"meets": list(meets)})
    at_meet = {r["meet_id"]: [float(d) for d in r["dists"]]
               for r in cur.fetchall()}

    # ★ A CLUSTER IS NOT CORRUPTION. Culver Military Academy contributed 55
    #   of 384 "corrupt rows" in the first real run and Columbus Grove 50 of
    #   287 in one division -- those runners ran a real race, just not the
    #   one on the label. Dropping them one at a time deletes real results
    #   and hides a group fault the earlier passes should own.
    from collections import Counter
    per_div = Counter((r["meet_id"], r["div_id"]) for r in rows)
    # ! THE FRACTION ONLY APPLIES WHERE A FRACTION MEANS SOMETHING. On a
    #   division of 11, one row is 9% and trips a 5% gate on its own -- which
    #   would route every single genuinely corrupt row in a small field into
    #   the group report and drop nothing, ever. Below 20 rated rows the
    #   count gate alone governs.
    clustered = {k for k, c in per_div.items()
                 if c > max_per_div
                 or (div_n.get(k, 0) >= 20 and c / div_n[k] > max_frac)}

    # ★ AND THE SAME LOGIC ONE LEVEL DOWN: AN ATHLETE, NOT A DIVISION.
    #
    #   The gap is measured against the athlete's own median, so a broken
    #   MEDIAN condemns that athlete's GOOD races. The first run dropped rows
    #   at Luther College, Saratoga Spa, Van Cortlandt, Houghton and
    #   Letchworth -- five different meets -- all against an own-median of
    #   174-178. No cross-country career medians 175. The median was the
    #   broken thing and the races being deleted were the real ones.
    #
    #   One athlete appearing once is a bad race. The same athlete appearing
    #   three times, in three unrelated divisions, is a bad median.
    per_ath = Counter(r["ident"] for r in rows)
    bad_median = {a for a, c in per_ath.items() if c > max_per_ath}
    # ⚠ AND AN ABSOLUTE SANITY RANGE ON THE MEDIAN ITSELF. The count gate
    #   needs three sightings, so an athlete with a broken median and only
    #   one or two flagged rows slipped it: the 2026-08-24 run tried to drop
    #   six rows whose own medians read 255-302, and nobody rates 300 -- the
    #   engine's elite ceiling is ~146. A median outside the sane range is
    #   itself the corrupt thing, the flagged row is likely that athlete's
    #   one REAL race, and it belongs in the broken-median report, never in
    #   the drops.
    bad_median |= {r["ident"] for r in rows
                   if r["med"] is not None
                   and not (MED_SANE_LO <= float(r["med"]) <= MED_SANE_HI)}

    # ★ AND ONE LEVEL UP AGAIN: THE MEET. A meet whose divisions each
    #   contribute two or three rows slips under a per-division cap while
    #   being, plainly, one fault. Chisholm Links produced 5 rows across 4
    #   divisions of meet 260482; Rawhiti Domain 3 across 3; Bellarmine Prep
    #   4 across 3. Each division looked innocent on its own.
    per_meet = Counter(r["meet_id"] for r in rows)
    bad_meet = {m for m, c in per_meet.items() if c > max_per_meet}

    drops, saves, groups, medians = [], [], [], []
    for r in rows:
        if (r["meet_id"], r["div_id"]) in clustered:
            groups.append(r)
            continue
        if r["meet_id"] in bad_meet:
            groups.append(r)
            continue
        if r["ident"] in bad_median:
            medians.append(r)
            continue
        implied = impliedDistance(r["distance"], float(r["med"]),
                                  float(r["gap"]))
        witness = None
        if implied:
            snapped, err = snapToLadder(implied)
            if abs(err) <= SNAP_TOL:
                for d in at_meet.get(r["meet_id"], []):
                    if abs(d - snapped) / snapped < 0.02:
                        witness = snapped
                        break
        if witness:
            saves.append({**r, "distance_fix": witness})
        else:
            drops.append(r)
    return drops, saves, groups, medians


# ------------------------------------------------------------------ #
#  SWEEP -- what each bar would condemn, so it is chosen on evidence
# ------------------------------------------------------------------ #

def sweep1(rows, sigma, unanimity, min_field):
    """The count curve, next to the count NOISE ALONE would produce.

    ★ THE COUNT CURVE ON ITS OWN CANNOT PICK A BAR. A heavy-tailed mixture of
      "mostly fine, with noise" and "genuinely broken" has no break in it, so
      reading the curve for a shoulder finds whatever you were hoping for.
      What decides the bar is how many of those divisions would be there if
      nothing were wrong at all.

      The standard error of a field median is 1.25*sigma/sqrt(N). Taking N at
      MIN_FIELD is the WORST case -- every larger division has a tighter SE
      and contributes fewer false positives -- so `noise` is an upper bound.

    ⚠ AND IT IS A LOOSE ONE, because these counts are already past the snap
      gate. A division that is merely noisy implies a distance between the
      rungs and is thrown out before it reaches this table, so the true false
      positive rate is below what `noise%` says.
    """
    import math
    se = 1.25 * sigma / math.sqrt(max(min_field, 1))
    print(f"\n  PASS 1 -- WHAT EACH BAR CONDEMNS, AGAINST WHAT NOISE WOULD")
    print(f"  sigma {sigma:.2f}, worst-case field of {min_field} -> SE of a "
          f"field median {se:.2f}\n")
    print(f"    {'sigma':>6} {'points':>7} {'divisions':>11} {'% of all':>9} "
          f"{'SE':>6} {'noise':>8} {'noise%':>8}")
    print("    " + "-" * 60)
    for t in (1.0, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0, 12.0):
        got, _routed, _sk = pass1(rows, sigma, t, unanimity)
        bar = t * sigma
        z = bar / se if se else 0.0
        pval = math.erfc(z / math.sqrt(2)) if z else 1.0
        noise = pval * len(rows)
        pct = 100.0 * len(got) / max(len(rows), 1)
        npct = 100.0 * noise / max(len(got), 1)
        mark = " *" if abs(t - T1_SIGMA) < 1e-9 else ""
        print(f"    {t:>6.1f} {bar:>7.1f} {len(got):>11,} {pct:>8.2f}% "
              f"{z:>6.2f} {noise:>8,.0f} {npct:>7.1f}%{mark}")
    print("\n    noise  = divisions this bar would condemn if NOTHING were "
          "wrong.")
    print("             Computed at the SMALLEST field, while the real bar "
          "now scales as")
    print(f"             max(bar, {SE_K:.0f}*SE(N)) per division -- so this "
          "column overstates it.")
    print("    noise% = that as a share of what it does condemn -- the "
          "false positive")
    print("             rate, upper bound. Take the lowest bar whose noise% "
          "you can live")
    print("             with; every point lower is real faults you are "
          "leaving in.\n")


# ------------------------------------------------------------------ #
#  EMIT
# ------------------------------------------------------------------ #

def emit(path, sport, blocks, header):
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(header)
        for name, body in blocks:
            if not body:
                continue
            fh.write(f"\n{name}.update({{\n")
            for line in body:
                fh.write(f"    {line}\n")
            fh.write("})\n")
    print(f"\n  wrote {path}")
    print("  REVIEW IT, then append to engine/corrections.py. This script "
          "never edits\n  corrections.py itself -- that file is the record, "
          "and a generator that\n  rewrites it is how the last one got lost.")


def main():
    global SIGMA
    ap = argparse.ArgumentParser(
        description="Rebuild distance and result overrides in three passes, "
                    "judged only on how far a rating sits from that "
                    "athlete's own median.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--measure", action="store_true",
                    help="report the corpus noise floor and stop")
    ap.add_argument("--pass", dest="which", type=int, choices=(1, 2, 3))
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--sigma", type=float, default=SIGMA)
    ap.add_argument("--t1", type=float, default=T1_SIGMA)
    ap.add_argument("--t2", type=float, default=T2_SIGMA)
    ap.add_argument("--t3", type=float, default=T3_SIGMA)
    ap.add_argument("--vs-field", type=float, default=T3_VS_FIELD_SIGMA)
    ap.add_argument("--unanimity", type=float, default=UNANIMITY)
    ap.add_argument("--min-field", type=int, default=MIN_FIELD)
    ap.add_argument("--min-minority", type=int, default=MIN_MINORITY)
    ap.add_argument("--min-own-races", type=int, default=MIN_OWN_RACES)
    ap.add_argument("--no-form", action="store_true",
                    help="do not subtract the race-of-season form curve "
                         "from the gaps (for diffing against older runs)")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--max-per-div", type=int, default=3, dest="max_per_div",
                    help="pass 3: more rows than this over the bar in one "
                         "division is a GROUP fault, not corruption -- "
                         "reported, not dropped (default 3)")
    ap.add_argument("--max-per-meet", type=int, default=4,
                    dest="max_per_meet",
                    help="pass 3: more rows than this from one MEET, across "
                         "any number of its divisions, is a meet-level fault "
                         "(default 4)")
    ap.add_argument("--max-per-athlete", type=int, default=2,
                    dest="max_per_ath",
                    help="pass 3: more rows than this from ONE athlete means "
                         "their median is what is broken, not their races "
                         "(default 2)")
    ap.add_argument("--max-drop-frac", type=float, default=0.05,
                    dest="max_drop_frac",
                    help="pass 3: or more than this share of the division "
                         "(default 0.05)")
    # ⚠ THE FLAG THAT MAKES A RESET A RESET. See _UNOVERRIDE_SQL: without it
    #   the passes judge ratings solved WITH the current overrides, so every
    #   override that is RIGHT looks fine, is not re-proposed, and is lost by
    #   the wipe. Use it for the clean-slate rebuild; leave it off to audit the
    #   corpus as it actually stands.
    ap.add_argument("--as-if-wiped", action="store_true", dest="as_if_wiped",
                    help="judge the ratings a wipe would produce: revert every "
                         "correcting override to its scraped distance first. "
                         "Sole-source overrides are left alone.")
    ap.add_argument("--same-tol", type=float, default=0.02, dest="same_tol",
                    help="how far a scraped distance must sit from the "
                         "override before reverting counts as a change "
                         "(default 2%%)")
    ap.add_argument("--ladder", action="store_true",
                    help="print the corpus ladder and how much discriminating "
                         "power the snap has left at each tolerance, then stop")
    # ⚠ DEFAULTS TO times. See the header on _GAP_BODY_TIMES: gating the gap
    #   table on speed_rating makes the detector weakest exactly where the
    #   fault is largest. `ratings` is the old behaviour, kept so the two can
    #   be diffed rather than swapped on trust.
    ap.add_argument("--coverage", action="store_true",
                    help="count the rows that are STILL not judgeable and "
                         "why. One left join over the whole result table, so "
                         "it costs a minute or two.")
    ap.add_argument("--gap-from", dest="gap_from", default="times",
                    choices=("times", "ratings"),
                    help="times: compute the rating for EVERY finisher from "
                         "normalized_time, so pace-guard drops still count. "
                         "ratings: only rows the engine rated (the old gate).")
    ap.add_argument("--skip-label-check", action="store_true",
                    dest="skip_label_check",
                    help="run even when the label does not match the distance "
                         "the ratings were computed at. For diagnosing that "
                         "message; the proposals will be wrong.")
    ap.add_argument("--out", default=None)
    ap.add_argument("--explain", default=None,
                    help="MEET/DIV -- print every gate that division met or "
                         "failed, in order, and stop. Runs the FAST path: "
                         "the same SQL restricted to the division's "
                         "athletes, seconds instead of the corpus build")
    ap.add_argument("--explain-full", action="store_true",
                    dest="explain_full",
                    help="run --explain through the full corpus build "
                         "instead of the fast path -- exact form correction, "
                         "~12 minutes. Use when a gap sits within ~3 points "
                         "of a verdict boundary")
    args = ap.parse_args()
    SIGMA = args.sigma

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # ★ THE FAST EXPLAIN, BEFORE ANY CORPUS WORK. The label check and
            #   the full gap build together cost ~12 minutes to answer a
            #   question about one division; the fast path runs the same SQL
            #   restricted to that division's athletes. --explain-full is the
            #   old exact route.
            if args.explain and args.which == 1 and not args.explain_full:
                explainFast(cur, args)
                return 0
            if not assertLabelTrustworthy(cur, args.sport):
                return 2
            if buildGap(cur, args.sport, args.min_own_races,
                        as_if_wiped=args.as_if_wiped, tol=args.same_tol,
                        skip_label_check=args.skip_label_check,
                        gap_from=args.gap_from,
                        coverage=args.coverage,
                        form=not args.no_form) is None:
                return 2

            if args.ladder:
                rungs = _CORPUS_LADDER
                print(f"\n  {len(rungs):,} RUNGS "
                      f"({LADDER_MIN_N:,}+ finishers each, de-combed)\n")
                # ! WITH THE COUNTS. The comb was invisible without them: nine
                #   teeth 16 m apart look like nine distances until you see
                #   that one has 4M finishers and the rest have 6,000.
                for i in range(0, len(rungs), 4):
                    print("    " + "   ".join(
                        f"{d:>6,} ({n:>9,})" for d, n in rungs[i:i + 4]))
                gaps = [(100.0 * (b - a) / a, a, b)
                        for (a, _x), (b, _y) in zip(rungs, rungs[1:])]
                gaps.sort(reverse=True)
                print(f"\n  WIDEST GAPS -- a value mid-gap misses both "
                      f"neighbours by half of this\n")
                for pct, a, b in gaps[:8]:
                    print(f"    {a:>6,} -> {b:>6,}   {pct:>5.1f}%")
                print(f"\n  HOW MUCH TEST IS LEFT IN THE SNAP\n")
                print(f"    {'tolerance':<12}{'random values that pass':>26}")
                print("    " + "-" * 38)
                for t in (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10):
                    p = ladderPower(t)
                    flag = "  <-- current" if abs(t - SNAP_TOL) < 1e-9 else ""
                    print(f"    {t:<12.0%}{p:>25.1%}{flag}")
                print(f"\n    This is the share of PURE NOISE the snap would "
                      f"admit -- the same\n    reading as the sweep's noise% "
                      f"column, for the snap instead of the\n    bar. A "
                      f"tolerance where most random numbers pass is not a "
                      f"test.")
                return 0

            if args.measure:
                measure(cur)
                return 0

            if args.which == 1:
                cur.execute(_PASS1_SQL, {"min_field": args.min_field})
                rows = cur.fetchall()
                if args.explain:
                    meet, _, div = args.explain.partition("/")
                    explain1(rows, (int(meet), int(div or 0)), args.sigma,
                             args.t1, args.unanimity, cur)
                    return 0
                print(f"  {len(rows):,} divisions with >= {args.min_field} "
                      f"{'finishers' if args.gap_from == 'times' else 'rated rows'}"
                      f" in the gap table")
                if args.sweep:
                    sweep1(rows, args.sigma, args.unanimity, args.min_field)
                    return 0
                got, routed, skipped = pass1(rows, args.sigma, args.t1,
                                             args.unanimity, cur)
                # ! ONE ROUND TRIP, AFTER THE PASS. See _NAMES_SQL: joined
                #   inside the query this ran 31.6M times instead of 5,500.
                #
                # ! AND ONLY THE CONDEMNED SET. Passing `skipped` here handed
                #   applyNames the other ~548k divisions -- 550+ paged
                #   statements, four correlated subqueries each -- to fetch
                #   names report1 never prints (it only Counts skipped
                #   reasons). That was the third post-loop stall.
                applyNames(cur, got)
                report1(got, routed, skipped, args)
                if args.out:
                    got = [r for r in got if r.get("tier") in ("A", "B")]
                    # ! THE NAME GOES IN THE COMMENT, so a course can be
                    #   found in the proposals with findstr. Without it
                    #   pass1.py is meet/div ids and nothing else, and
                    #   checking what happened to a meet you can name means
                    #   first looking up its id somewhere else.
                    lines = [f"({r['meet_id']}, {r['div_id']}): "
                             f"{r['snapped']:.0f},  # was "
                             f"{r['distance']:.0f}, field {r['gap']:+.1f} over "
                             f"its own heads, n={r['n']}, snap "
                             f"{r['snap_err']:+.1%}"
                             f"{_named(r)}"
                             for r in got]
                    emit(args.out, args.sport,
                         [(f"_DISTANCE_OVERRIDES_{args.sport}", lines)],
                         _HEADER.format(what="pass 1: whole-division distance",
                                        n=len(lines), bar=args.t1 * args.sigma))
                return 0

            if args.which == 2:
                cur.execute(_PASS1_SQL, {"min_field": args.min_field})
                rows = cur.fetchall()
                got1, _routed, _sk = pass1(rows, args.sigma, args.t1,
                                           args.unanimity, cur)
                # ⚠ EVERY DIVISION PASS 1 DID NOT CONDEMN, not just the ones
                #   that failed its unanimity gate. That gate is a bad router
                #   for this: a genuine two-race division has one half at gap
                #   ~0, which splits about 10/10 by noise, and one half far
                #   off -- putting same_side at almost exactly 75%, right on
                #   the boundary. Routing on it found 2 half-divisions in a
                #   corpus of 493,029. Pass 2's own test (the halves
                #   disagreeing by more than the bar) is the gate, and it is
                #   a much better one, so it judges everything left.
                fixed1 = {(r["meet_id"], r["div_id"]) for r in got1}
                routed = [r for r in rows
                          if (r["meet_id"], r["div_id"]) not in fixed1]
                print(f"  {len(routed):,} divisions pass 1 left alone come "
                      f"here")
                cur.execute("DROP TABLE IF EXISTS reb_pass2")
                cur.execute("CREATE TEMP TABLE reb_pass2 "
                            "(meet_id bigint, div_id bigint)")
                if routed:
                    from psycopg2.extras import execute_values
                    execute_values(cur, "INSERT INTO reb_pass2 VALUES %s",
                                   [(r["meet_id"], r["div_id"])
                                    for r in routed])
                cur.execute(_PASS2_SQL)
                halves = cur.fetchall()
                got, skipped = pass2(halves, args.sigma, args.t2,
                                     args.min_minority)
                report2(got, skipped, args)
                if args.out:
                    lines = []
                    for side in got:
                        lines.append(f"# {side['meet_id']}/{side['div_id']} "
                                     f"{side['gender']}: {side['n']} rows -> "
                                     f"{side['distance']:.0f}, halves "
                                     f"{side['split']:.1f} apart")
                        for rid in side["result_ids"]:
                            lines.append(f"{rid}: ({side['distance']:.1f}, "
                                         f"\"{side['gender']}\"),")
                    emit(args.out, args.sport,
                         [(f"_RESULT_OVERRIDE_{args.sport}", lines)],
                         _HEADER.format(what="pass 2: per-row distance+sex",
                                        n=len(got), bar=args.t2 * args.sigma))
                return 0

            if args.which == 3:
                # ⚠ 1 AND 2 RUN FIRST, ALWAYS. A division-level fault makes
                #   every row in it look individually corrupt; without this,
                #   pass 3 reports the whole field of everything the earlier
                #   passes were about to fix.
                cur.execute(_PASS1_SQL, {"min_field": args.min_field})
                rows = cur.fetchall()
                got1, _routed, _sk = pass1(rows, args.sigma, args.t1,
                                           args.unanimity, cur)
                # ⚠ SAME CANDIDATE SET AS --pass 2, NOT pass 1's unanimity
                #   router. --pass 2 judges every division pass 1 did not
                #   condemn (the router found 2 half-divisions in 493,029);
                #   staging only the routed ones here computed reb_pinned
                #   over ~nothing, so pass 3 re-reported every split
                #   division's moved half as individual corruption -- the
                #   exact failure the reb_pinned exclusion exists for.
                fixed1 = {(r["meet_id"], r["div_id"]) for r in got1}
                cand = [r for r in rows
                        if (r["meet_id"], r["div_id"]) not in fixed1]
                cur.execute("DROP TABLE IF EXISTS reb_pass2")
                cur.execute("CREATE TEMP TABLE reb_pass2 "
                            "(meet_id bigint, div_id bigint)")
                if cand:
                    from psycopg2.extras import execute_values
                    execute_values(cur, "INSERT INTO reb_pass2 VALUES %s",
                                   [(r["meet_id"], r["div_id"])
                                    for r in cand])
                cur.execute(_PASS2_SQL)
                got2, _sk2 = pass2(cur.fetchall(), args.sigma, args.t2,
                                   args.min_minority)
                # rating' = rating * (d_new / d_old) ** K
                # ! A AND B ONLY: pass 3 models the corrections that will
                #   actually be APPLIED, and tier C is report-only. Scaling
                #   by C adjusts rows for a fix nobody writes; unscaled, a C
                #   division's rows are shielded by the vs-field guard (their
                #   division median is off with them).
                fixed = [(r["meet_id"], r["div_id"],
                          (r["snapped"] / float(r["distance"])) ** K)
                         for r in got1
                         if r["distance"] and r.get("tier") in ("A", "B")]
                pinned = [rid for side in got2 for rid in side["result_ids"]]
                stagePriorPasses(cur, fixed, pinned)
                drops, saves, groups, medians = pass3(
                    cur, args.sigma, args.t3, args.vs_field, args.limit,
                    args.max_per_div, args.max_drop_frac, args.max_per_ath,
                    args.max_per_meet, form=not args.no_form)
                applyNames(cur, drops, saves)
                report3(drops, saves, groups, medians, args)
                if args.out:
                    dl = [f"{r['result_id']},  # {r['speed_rating']:.1f} vs "
                          f"own median {r['med']:.1f} ({r['gap']:+.1f}), "
                          f"division median {r['div_gap']:+.1f}"
                          for r in drops]
                    sl = [f"{r['result_id']}: ({r['distance_fix']:.1f}, None), "
                          f" # raced elsewhere at this meet"
                          for r in saves]
                    emit(args.out, args.sport,
                         [(f"_RESULT_DROP_{args.sport}", dl),
                          (f"_RESULT_OVERRIDE_{args.sport}", sl)],
                         _HEADER.format(what="pass 3: corrupt rows",
                                        n=len(dl) + len(sl),
                                        bar=args.t3 * args.sigma))
                return 0

    ap.error("give --measure or --pass 1|2|3")


_HEADER = '''# GENERATED by scripts/rebuild_overrides.py -- {what}
#
# {n} entries, bar {bar:.0f} rating points.
#
# Every entry is here because a rating sat that far from the athlete's own
# median. Review before appending to engine/corrections.py.
'''


def report1(got, routed, skipped, args):
    print(f"\n  PASS 1 -- {len(got):,} divisions condemned "
          f"(bar {args.t1 * args.sigma:.0f} points, "
          f"{args.unanimity:.0%} of the field on one side)")
    print(f"  {len(routed):,} routed to pass 2 (field not one-sided enough)")
    print(f"  {len(skipped):,} left alone\n")

    # ★ WHERE THE FINDINGS DIE, WHICH THIS NEVER SAID. Every rejection has
    #   carried a reason since the first version and report1 printed only the
    #   total, so "the passes aren't working" was unanswerable by design: a
    #   division that cleared the bar and then died at the snap looked exactly
    #   like one that was never wrong.
    #
    # ⚠ READ THE GATES BELOW THE BAR AS SUSPECTS, NOT AS SUCCESSES. Anything
    #   dying at the snap, the label or the change cap has ALREADY been judged
    #   too far from its athletes' own heads to be chance. It is a division
    #   this tool agrees is broken and then declines to fix.
    from collections import Counter
    why = Counter()
    for _row, reason in skipped:
        # collapse the ones that carry numbers in the text
        key = ("implied snaps poorly" if reason.startswith("implied ")
               else "past the change cap" if "cap" in reason
               else reason)
        why[key] += 1
    print("    WHY THE REST WERE LEFT ALONE")
    below, above = [], []
    for reason, n in why.most_common():
        (below if reason == "within the bar" else above).append((reason, n))
    for reason, n in below + above:
        mark = "     " if reason == "within the bar" else "  <-- "
        print(f"    {n:>9,}{mark}{reason}")
    if _SNAP_MISS:
        # ⚠ HOW FAR OUT, NOT JUST HOW MANY. A cluster just past the tolerance
        #   means the tolerance is wrong; a long tail means these really are
        #   fields wrong for some reason other than distance.
        bands = [(0.03, 0.04), (0.04, 0.05), (0.05, 0.075), (0.075, 0.10),
                 (0.10, 0.15), (0.15, 0.25), (0.25, 9.99)]
        print(f"\n    HOW BADLY THE {len(_SNAP_MISS):,} SNAP FAILURES MISSED")
        run = 0
        for lo, hi in bands:
            n = sum(1 for e in _SNAP_MISS if lo <= e < hi)
            if not n:
                continue
            run += n
            print(f"      {lo:.0%} - {hi:.0%}   {n:>8,}   "
                  f"{100.0 * run / len(_SNAP_MISS):>5.1f}% cumulative")
        print(f"      A cluster near the top of the list is a tolerance "
              f"problem.\n      A long tail is not.")

    n_judged = sum(n for r, n in above)
    if n_judged:
        print(f"\n    {n_judged:,} of those cleared the bar -- this tool "
              f"already agrees they are\n    wrong -- and were then refused "
              f"by a later gate. That is the number to\n    argue with, not "
              f"the condemned count.")
    if got:
        from collections import Counter as _C
        tiers = _C(r.get("tier", "C") for r in got)
        print(f"    CONFIDENCE IN THE DISTANCE PROPOSED")
        for t, what in (("A", "the course itself races this distance"),
                        ("B", "corpus snap within 2%, field median precise"),
                        ("C", "wrong division, guessed distance")):
            print(f"      {t}  {tiers.get(t, 0):>7,}   {what}")
        print(f"\n      A and B are what --out writes. C is reported only "
              f"-- see tierFor.\n")
        print(f"    {'gap':>7} {'n':>5} {'side':>5} {'label':>6} "
              f"{'implied':>7} {'snap':>6} {'err':>7}  {'meet/div':>16}  course")
        for r in sorted(got, key=lambda x: -abs(x["gap"]))[:args.limit]:
            print(f"    {r['gap']:>+7.1f} {r['n']:>5} "
                  f"{float(r['same_side']):>5.0%} {r['distance']:>6.0f} "
                  f"{r['implied']:>7.0f} {r['snapped']:>6.0f} "
                  f"{r['snap_err']:>+7.1%}  "
                  f"{str(r['meet_id']) + '/' + str(r['div_id']):>16}  "
                  f"{(r.get('display_name') or r['course_name'] or '?')[:28]}")
    print()


def report2(got, skipped, args):
    print(f"\n  PASS 2 -- {len(got):,} half-divisions to pin "
          f"(halves must differ by {args.t2 * args.sigma:.0f} points, "
          f"smaller half >= {args.min_minority} rows)")
    print(f"  {len(skipped):,} routed divisions left alone\n")
    if got:
        print(f"    {'sex':>4} {'n':>5} {'gap':>7} {'split':>6} "
              f"{'-> dist':>8}  meet/div")
        for r in sorted(got, key=lambda x: (-x["split"], x["meet_id"]))[:args.limit]:
            print(f"    {r['gender']:>4} {r['n']:>5} {r['med_gap']:>+7.1f} "
                  f"{r['split']:>6.1f} {r['distance']:>8.0f}  "
                  f"{r['meet_id']}/{r['div_id']}")
    print()


def report3(drops, saves, groups, medians, args):
    print(f"\n  PASS 3 -- {len(drops):,} rows to DROP, {len(saves):,} savable "
          f"(bar {args.t3 * args.sigma:.0f} points, and "
          f"{args.vs_field * args.sigma:.0f} from the division median)\n")
    if drops:
        print(f"    {'rating':>7} {'own med':>8} {'gap':>7} {'div gap':>8} "
              f"{'div n':>6}  {'meet/div':>16}  course")
        for r in drops[:args.limit]:
            print(f"    {r['speed_rating']:>7.1f} {r['med']:>8.1f} "
                  f"{r['gap']:>+7.1f} {r['div_gap']:>+8.1f} {r['div_n']:>6}  "
                  f"{str(r['meet_id']) + '/' + str(r['div_id']):>16}  "
                  f"{(r.get('display_name') or r['course_name'] or '?')[:26]}")
    if groups:
        from collections import Counter
        per = Counter((r["meet_id"], r["div_id"]) for r in groups)
        print(f"\n    NOT DROPPED -- {len(groups):,} rows across "
              f"{len(per):,} divisions where too many rows are over the bar")
        print(f"    for this to be individual corruption. These are GROUP "
              f"faults: a subgroup")
        print(f"    that ran a different race. Dropping them one at a time "
              f"would delete real")
        print(f"    results. They need a division-level answer, not a row "
              f"one.\n")
        print(f"      {'rows':>6} {'of':>6}  meet/div")
        for (meet, div), c in per.most_common(30):
            n = next((r["div_n"] for r in groups
                      if r["meet_id"] == meet and r["div_id"] == div), 0)
            print(f"      {c:>6} {n:>6}  {meet}/{div}")
        if len(per) > 30:
            print(f"      ... and {len(per) - 30:,} more divisions")

    if medians:
        from collections import Counter
        per = Counter(r["ident"] for r in medians)
        print(f"\n    NOT DROPPED -- {len(medians):,} rows from {len(per):,} "
              f"athletes who are over the bar in")
        print(f"    more than {args.max_per_ath} unrelated divisions. The gap "
              f"is measured against their")
        print(f"    own median, so what is broken is the MEDIAN, and these "
              f"are their good races.\n")
        print(f"      {'rows':>6} {'own med':>9}  athlete")
        for ident, c in per.most_common(20):
            med = next(r["med"] for r in medians if r["ident"] == ident)
            print(f"      {c:>6} {med:>9.1f}  {ident}")

    if saves:
        print(f"\n    SAVABLE -- these snap to a distance raced elsewhere at "
              f"the same meet,\n    so they are a runner in the wrong race, "
              f"not a corrupt row:")
        for r in saves[:args.limit]:
            print(f"      {r['result_id']}  {r['speed_rating']:.1f} "
                  f"({r['gap']:+.1f}) -> {r['distance_fix']:.0f}m")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
