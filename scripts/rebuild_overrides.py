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
           COALESCE(r.person_id, r.athlete_id) AS ident
    FROM   {table} r
    LEFT   JOIN reb_unovr u
           ON u.meet_id = r.meet_id AND u.div_id = r.div_id
    WHERE  r.speed_rating IS NOT NULL AND r.speed_rating > 0
      AND  COALESCE(r.person_id, r.athlete_id) IS NOT NULL
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


def buildGap(cur, sport, min_own, as_if_wiped=False, tol=0.02):
    _noBarePercent("_PASS1_SQL", _PASS1_SQL)
    _noBarePercent("_GAP_BODY", _GAP_BODY)
    # ! ALWAYS, NOT ONLY UNDER --as-if-wiped. _PASS1_SQL now falls back to
    #   this for a division's label distance, so it has to exist in both
    #   modes or every tfrrs division loses its label again. Same reader
    #   build_ranking_results uses -- imported, not copied.
    sys.path.insert(0, "racecast")
    from build_ranking_results import _XC_TFRRS_DIST_SQL
    cur.execute(_XC_TFRRS_DIST_SQL)
    cur.execute(_COURSE_DIST_SQL, {"min_n": COURSE_MIN_N})
    kept, raw = loadLadder(cur)
    print(f"  ladder: {len(kept):,} distances the corpus actually races "
          f"({LADDER_MIN_N:,}+ finishers each; {len(raw) - len(kept):,} "
          f"comb teeth merged away)")
    if as_if_wiped:
        cur.execute(_UNOVERRIDE_SQL, {"k": K, "tol": tol})
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
    cur.execute(_GAP_BODY.format(table=_TABLE[sport]), {"min_own": min_own})
    # RealDictCursor, so name the aggregates rather than unpacking a tuple.
    cur.execute("SELECT count(*) AS n, count(gender) AS n_sexed FROM reb_gap")
    row = cur.fetchone()
    n, n_sexed = row["n"], row["n_sexed"]
    print(f"  gap table: {n:,} rated rows judged against their athlete's own "
          f"median ({n_sexed:,} with a gender)")
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

_PASS1_SQL = """
SELECT g.meet_id, g.div_id,
       count(*)                                                AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY g.gap)      AS med_gap,
       avg(g.med)                                              AS base,
       greatest(
           count(*) FILTER (WHERE g.gap > 0),
           count(*) FILTER (WHERE g.gap < 0)
       )::float / count(*)                                     AS same_side,
       m.course_name, m.distance, m.division
FROM   reb_gap g
LEFT   JOIN LATERAL (
    -- ⚠ `meets` IS ANET-ONLY, AND THAT BLINDED THIS PASS TO EVERY TFRRS
    --   DIVISION. impliedDistance needs a label to scale from; with no label
    --   it returns None and pass1 skips the row as "no usable label
    --   distance" -- AFTER the division has already passed the bar and the
    --   unanimity gate. The fault is found and then discarded.
    --
    --   Measured on 26359/0, Ox Bow Park, the JV Minutemen Classic: 22 rated
    --   rows, field median gap +58.6, the whole field on one side, bar
    --   11.2. It passes
    --   everything and dies here, because meet 26359 has 568 tfrrs rows and
    --   no `meets` row at all.
    --
    -- ★ SO THE TFRRS BLOB IS THE FALLBACK, same source and same reader as
    --   build_ranking_results.prepareXcTfrrsDistTemp -- a per-division
    --   distance inside meets_tfrrs.division_distances, keyed by div_id as a
    --   string. COALESCE, not UNION: anet wins where both have one, which is
    --   the precedence every other reader uses.
    SELECT COALESCE(m.course_name, x.course_name)  AS course_name,
           COALESCE(m.distance, x.distance)        AS distance,
           m.division                              AS division
    FROM  (SELECT course_name, distance, division
             FROM meets
            WHERE meets.meet_id = g.meet_id AND meets.div_id = g.div_id
            LIMIT 1) m
    FULL  OUTER JOIN
          (SELECT NULL::text AS course_name, t.distance
             FROM tmp_xc_tfrrs_dist t
            WHERE t.meet_id = g.meet_id AND t.div_id = g.div_id
            LIMIT 1) x ON TRUE
    LIMIT 1
) m ON TRUE
GROUP  BY g.meet_id, g.div_id, m.course_name, m.distance, m.division
HAVING count(*) >= %(min_field)s
"""


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




def courseDistances(cur, course_name):
    """[(distance, n), ...] for one venue, commonest first. Memoised."""
    if course_name in _COURSE_DIST:
        return _COURSE_DIST[course_name]
    cur.execute("SELECT distance, n FROM reb_course_dist "
                "WHERE course_name = %(c)s ORDER BY n DESC",
                {"c": course_name})
    got = [(float(r["distance"]), int(r["n"])) for r in cur.fetchall()]
    _COURSE_DIST[course_name] = got
    return got


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
    print(f"\n    It clears every gate -- it should be in the output.")


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
    for r in rows:
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
        condemned.append({**r, "implied": implied, "snapped": snapped,
                          "snap_err": err, "gap": gap, "source": source,
                          "tier": tierFor(source, err, gap, r["n"], sigma)})
    return condemned, routed, skipped


# ------------------------------------------------------------------ #
#  PASS 2 -- the division holds two races, split by sex
# ------------------------------------------------------------------ #

_PASS2_SQL = """
SELECT g.meet_id, g.div_id, g.gender,
       count(*)                                              AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY g.gap)    AS med_gap,
       avg(g.med)                                            AS base,
       array_agg(g.result_id)                                AS result_ids,
       m.distance
FROM   reb_gap g
LEFT   JOIN LATERAL (
    SELECT distance FROM meets
    WHERE meets.meet_id = g.meet_id AND meets.div_id = g.div_id LIMIT 1
) m ON TRUE
WHERE  g.gender IN ('M', 'F')
  AND  (g.meet_id, g.div_id) IN (SELECT meet_id, div_id FROM reb_pass2)
GROUP  BY g.meet_id, g.div_id, g.gender, m.distance
"""


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
       m.distance, m.course_name
FROM   adj a
JOIN   div d ON d.meet_id = a.meet_id AND d.div_id = a.div_id
LEFT   JOIN LATERAL (
    SELECT distance, course_name FROM meets
    WHERE meets.meet_id = a.meet_id AND meets.div_id = a.div_id LIMIT 1
) m ON TRUE
WHERE  abs(a.adj_gap) > %(bar)s
  AND  abs(a.adj_gap - d.div_gap) > %(vs_field)s
ORDER  BY abs(a.adj_gap) DESC
LIMIT  %(limit)s
"""


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
          max_frac=0.05, max_per_ath=2, max_per_meet=4):
    cur.execute(_PASS3_SQL, {"bar": t3 * sigma,
                             "vs_field": vs_field * sigma, "limit": limit})
    rows = cur.fetchall()
    div_n = {(r["meet_id"], r["div_id"]): r["div_n"] for r in rows}
    # Which distances are raced elsewhere at the same meet -- the witness that
    # separates "recorded in the wrong race" from "corrupt".
    meets = tuple({r["meet_id"] for r in rows}) or (0,)
    cur.execute("""
        SELECT meet_id, array_agg(DISTINCT distance) AS dists
        FROM   meets WHERE meet_id = ANY(%s) AND distance > 0
        GROUP  BY meet_id
    """, (list(meets),))
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
    ap.add_argument("--out", default=None)
    ap.add_argument("--explain", default=None,
                    help="MEET/DIV -- print every gate that division met or "
                         "failed, in order, and stop")
    args = ap.parse_args()
    SIGMA = args.sigma

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            buildGap(cur, args.sport, args.min_own_races,
                     as_if_wiped=args.as_if_wiped, tol=args.same_tol)

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
                      f"rated rows")
                if args.sweep:
                    sweep1(rows, args.sigma, args.unanimity, args.min_field)
                    return 0
                got, routed, skipped = pass1(rows, args.sigma, args.t1,
                                             args.unanimity, cur)
                report1(got, routed, skipped, args)
                if args.out:
                    got = [r for r in got if r.get("tier") in ("A", "B")]
                    lines = [f"({r['meet_id']}, {r['div_id']}): "
                             f"{r['snapped']:.0f},  # was "
                             f"{r['distance']:.0f}, field {r['gap']:+.1f} over "
                             f"its own heads, n={r['n']}, snap "
                             f"{r['snap_err']:+.1%}"
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
                got1, routed, _sk = pass1(rows, args.sigma, args.t1,
                                          args.unanimity, cur)
                cur.execute("DROP TABLE IF EXISTS reb_pass2")
                cur.execute("CREATE TEMP TABLE reb_pass2 "
                            "(meet_id bigint, div_id bigint)")
                if routed:
                    from psycopg2.extras import execute_values
                    execute_values(cur, "INSERT INTO reb_pass2 VALUES %s",
                                   [(r["meet_id"], r["div_id"])
                                    for r in routed])
                cur.execute(_PASS2_SQL)
                got2, _sk2 = pass2(cur.fetchall(), args.sigma, args.t2,
                                   args.min_minority)
                # rating' = rating * (d_new / d_old) ** K
                fixed = [(r["meet_id"], r["div_id"],
                          (r["snapped"] / float(r["distance"])) ** K)
                         for r in got1 if r["distance"]]
                pinned = [rid for side in got2 for rid in side["result_ids"]]
                stagePriorPasses(cur, fixed, pinned)
                drops, saves, groups, medians = pass3(
                    cur, args.sigma, args.t3, args.vs_field, args.limit,
                    args.max_per_div, args.max_drop_frac, args.max_per_ath,
                    args.max_per_meet)
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
                  f"{(r['course_name'] or '?')[:28]}")
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
                  f"{(r['course_name'] or '?')[:26]}")
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
