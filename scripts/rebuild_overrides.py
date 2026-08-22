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
_GAP_BODY = """
DROP TABLE IF EXISTS reb_gap;
CREATE TEMP TABLE reb_gap AS
WITH rated AS (
    SELECT r.meet_id, r.div_id, r.result_id, r.speed_rating,
           r.time_seconds,
           COALESCE(r.person_id, r.athlete_id) AS ident
    FROM   {table} r
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


def buildGap(cur, sport, min_own):
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
    SELECT course_name, distance, division
    FROM   meets WHERE meets.meet_id = g.meet_id AND meets.div_id = g.div_id
    LIMIT  1
) m ON TRUE
GROUP  BY g.meet_id, g.div_id, m.course_name, m.distance, m.division
HAVING count(*) >= %(min_field)s
"""


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


def pass1(rows, sigma, t1, unanimity):
    """(condemned, routed_to_2, skipped) for the whole-division pass."""
    condemned, routed, skipped = [], [], []
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
        snapped, err = snapToLadder(implied)
        if abs(err) > SNAP_TOL:
            # A field that is wrong for a reason OTHER than distance implies
            # a value between the rungs. That is not a distance fault and
            # must not be written as one.
            skipped.append((r, f"implied {implied:.0f} snaps poorly "
                               f"({err:+.1%})"))
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
                          "snap_err": err, "gap": gap})
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
    ap.add_argument("--out", default=None)
    ap.add_argument("--explain", default=None,
                    help="MEET/DIV -- print every gate that division met or "
                         "failed, in order, and stop")
    args = ap.parse_args()
    SIGMA = args.sigma

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            buildGap(cur, args.sport, args.min_own_races)

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
                                             args.unanimity)
                report1(got, routed, skipped, args)
                if args.out:
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
                                           args.unanimity)
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
                                          args.unanimity)
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
    if got:
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
