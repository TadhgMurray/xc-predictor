"""diag_trajectory.py -- what do athletes who ran X at one XC course actually
run the following spring? READ ONLY.

    python scripts/diag_trajectory.py
    python scripts/diag_trajectory.py --focus 930 950 --case-xc 940 --case-tf 541.1
    python scripts/diag_trajectory.py --course '%woodward%' --dist 5000
    python scripts/diag_trajectory.py --sql            # print the SQL, run none

Run from the PROJECT ROOT. Needs the database. Writes nothing.

THE QUESTION (issue #107). The engine rates a 15:40 at Mt. SAC and a 9:01
3200 the following April as the same performance. That is a claim about a
population: "an athlete who runs 15:40 at Mt. SAC in October runs 9:01 in
April." This script reads what that population actually ran, so the claim
can be checked against data rather than argued.

★ CONDITION ON THE FALL SIDE, NEVER ON THE SPRING MEET. Starting from the
  athletes who ran Arcadia would select for the ones who hit its qualifying
  standard, and the spring median would be biased fast by construction.
  Starting from the Mt. SAC field and following EVERYONE into the spring,
  whatever meet they ran, selects on nothing the spring outcome can see.

★ THE REMAINING SELECTION IS PRINTED, NOT HIDDEN. Some fall athletes run no
  spring 3200 at all. They are counted per band as "censored". A band with a
  high censoring rate has a spring median drawn from its survivors, and the
  reader is told so rather than handed a clean-looking number.

★ BOTH DIRECTIONS. The forward table conditions on the Mt. SAC time and is
  biased toward improvers (survivors). The reverse table conditions on the
  spring 3200 and is biased the other way, by regression to the mean. The
  truth for the pair sits between them, exactly as the two directions of the
  sandwich in measure_sport_gap bracket the sport gap.

★ MEANS AS WELL AS BESTS. A season best is a max over more races for athletes
  who race more, which flatters them. The engine's cell deltas are
  least-squares MEANS, so the fair test of the cells is the athlete's fall
  mean against their spring mean, in log normalised time, compared with what
  the two cells' difficulties imply. That residual, positive when the spring
  outran the engine's expectation, is the number that says whether the cells
  are mis-set for this pair. Best-against-best answers the question as the
  owner phrases it, and both are printed.

! THE ARCADIA TEST (issue 62's design, applied to a track). For every athlete
  with a 3200 at Arcadia, the lift at Arcadia against their own season is
  compared with their lift at OTHER invitationals and at ordinary meets. A
  lift that appears at every invitational is peaking and field quality. A
  lift at Arcadia alone is the track. Beside each group is the mean cell
  difficulty the engine charges it, so the two can be read together.

⚠ WHAT THIS READS FROM. ranking_results, so only rows the boards accepted;
  #104's truncation of slow track rows barely reaches a 15:40 athlete's
  spring, but it is a selection and it is named here. Difficulties come from
  course_difficulties as stored, i.e. the recentred, shrunk, PRE-TILT deltas.
  The rain-course Mt. SAC meets that panels.py excludes are excluded here too,
  since they are a different course under the same name.
"""

import argparse
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

# Mt. SAC meets that ran the flat rain course under the normal course's name.
# Mirrors racecast/panels.py EXCLUDED_MEET_IDS; a different course is not
# evidence about this one.
RAIN_COURSE_MEETS = (31741, 226604, 256823)

# ------------------------------------------------------------------ #
#  SQL
# ------------------------------------------------------------------ #

# The fall side: every rated row at the named course and distance. One meets
# row per (meet_id, div_id) via LATERAL LIMIT 1 -- meets is not unique on that
# key (apply_tilt.py found duplicates) and the course filter sits inside the
# lateral so a non-matching meet simply yields no row.
_XC_SQL = """
DROP TABLE IF EXISTS tj_xc;
CREATE TEMP TABLE tj_xc AS
SELECT k.person_id, k.year, k.race_date, k.meet_id, k.div_id,
       k.time_seconds::float8                    AS t,
       ln(r.normalized_time)                     AS lnorm,
       m.course_name, m.distance,
       ln(1.0 + cd.difficulty)                   AS ldiff
FROM   ranking_results k
JOIN   results r ON r.result_id = k.result_id
JOIN   LATERAL (
           SELECT course_name, distance
           FROM   meets
           WHERE  meet_id = k.meet_id AND div_id = k.div_id
             AND  course_name ILIKE %(course)s
             AND  abs(distance - %(dist)s) <= %(tol)s
           LIMIT  1
       ) m ON TRUE
LEFT   JOIN course_difficulties cd
         ON cd.course_name = 'XC:' || m.course_name
        AND cd.distance_m  = (round(m.distance / 100.0) * 100)::int
WHERE  k.sport = 'XC'
  AND  k.pool  = %(pool)s
  AND  k.time_seconds > 0
  AND  r.normalized_time > 0
  AND  k.race_date IS NOT NULL
  AND  NOT (k.meet_id = ANY(%(excl)s));
CREATE INDEX ON tj_xc (person_id, year);
ANALYZE tj_xc;
"""

# The spring side, for the pool at large: every rated track row at the target
# event in the spring months. Pool-wide rather than restricted to the fall
# athletes because the reverse direction and the Arcadia test both need the
# whole population. The cell key mirrors speed_ratings_db._tfQuery.
_TF_SQL = """
DROP TABLE IF EXISTS tj_tf;
CREATE TEMP TABLE tj_tf AS
SELECT k.person_id, k.year, k.race_date, k.meet_id, k.div_id,
       k.time_seconds::float8                    AS t,
       k.distance::float8                        AS distance,
       ln(r.normalized_time)                     AS lnorm,
       mt.meet_name,
       k.state                                   AS state,
       (mt.meet_name ILIKE %(venue)s)            AS at_venue,
       (mt.meet_name ILIKE '%%invit%%')          AS invitational,
       ln(1.0 + cd.difficulty)                   AS ldiff
FROM   ranking_results k
JOIN   results_tf r ON r.result_id = k.result_id
LEFT   JOIN LATERAL (
           SELECT meet_name, location_id, is_indoor
           FROM   meets_tf
           WHERE  meet_id = k.meet_id AND div_id = k.div_id
             AND  event_id = k.event_id
           LIMIT  1
       ) mt ON TRUE
LEFT   JOIN course_difficulties cd
         ON cd.course_name = 'TF:loc:' || mt.location_id::text
            || CASE WHEN COALESCE(mt.is_indoor, 0) = 1 THEN ':in' ELSE ':out' END
WHERE  k.sport = 'TF'
  AND  k.pool  = %(pool)s
  AND  k.time_seconds > 0
  AND  r.normalized_time > 0
  AND  k.race_date IS NOT NULL
  AND  EXTRACT(month FROM k.race_date) BETWEEN %(mlo)s AND %(mhi)s
  AND  k.distance BETWEEN %(dlo)s AND %(dhi)s;
CREATE INDEX ON tj_tf (person_id, year);
ANALYZE tj_tf;
"""

# One row per athlete-year on each side. The fall side keeps its best time
# and its mean log normalised time at THIS course; the spring side the same
# over all its target-event rows, plus whether the best was at the venue.
_PAIR_SQL = """
WITH fall AS (
    SELECT person_id, year,
           min(t)        AS best_xc,
           avg(lnorm)    AS mean_lnorm_xc,
           avg(ldiff)    AS ldiff_xc,
           count(*)      AS n_xc
    FROM   tj_xc
    GROUP  BY person_id, year
),
spring AS (
    SELECT person_id, year,
           min(t)        AS best_tf,
           avg(lnorm)    AS mean_lnorm_tf,
           avg(ldiff)    AS ldiff_tf,
           count(*)      AS n_tf,
           bool_or(at_venue) AS any_venue,
           (array_agg(at_venue ORDER BY t))[1] AS best_at_venue,
           -- ! THE MEET'S STATE, NOT THE ATHLETE'S. ranking_results.state is
           --   where the race was; the mode over an athlete's spring rows is
           --   where they live, near enough, and it is what separates the
           --   track side of two athletes who ran the SAME fall cell.
           mode() WITHIN GROUP (ORDER BY state) AS tf_state
    FROM   tj_tf
    GROUP  BY person_id, year
)
SELECT f.person_id, f.year, f.best_xc, f.mean_lnorm_xc, f.ldiff_xc, f.n_xc,
       s.best_tf, s.mean_lnorm_tf, s.ldiff_tf, s.n_tf, s.any_venue,
       s.best_at_venue, s.tf_state
FROM   fall f
LEFT   JOIN spring s ON s.person_id = f.person_id AND s.year = f.year
"""

# The reverse direction: athlete-years whose spring best sits in the window,
# and whether they raced this course the previous fall at all.
_REVERSE_SQL = """
WITH spring AS (
    SELECT person_id, year, min(t) AS best_tf
    FROM   tj_tf
    GROUP  BY person_id, year
    HAVING min(t) BETWEEN %(tlo)s AND %(thi)s
),
fall AS (
    SELECT person_id, year, min(t) AS best_xc
    FROM   tj_xc
    GROUP  BY person_id, year
)
SELECT s.person_id, s.year, s.best_tf, f.best_xc
FROM   spring s
LEFT   JOIN fall f ON f.person_id = s.person_id AND f.year = s.year
"""

# The Arcadia test. Per row: the athlete's mean log normalised time over
# their OTHER rows that spring, minus this row's. Positive = ran faster here
# than their own season. Needs two other rows so the baseline is not one race.
_LIFT_SQL = """
WITH ay AS (
    SELECT person_id, year
    FROM   tj_tf
    GROUP  BY person_id, year
    HAVING bool_or(at_venue) AND count(*) >= 3
),
rows_ AS (
    SELECT t.*, sum(t.lnorm) OVER w AS s_all, count(*) OVER w AS n_all
    FROM   tj_tf t
    JOIN   ay USING (person_id, year)
    WINDOW w AS (PARTITION BY t.person_id, t.year)
)
SELECT CASE WHEN at_venue THEN 'venue'
            WHEN invitational THEN 'other invitational'
            ELSE 'other meet' END                          AS kind,
       ((s_all - lnorm) / (n_all - 1)) - lnorm             AS lift,
       ldiff
FROM   rows_
"""

_VENUE_DIFF_SQL = """
SELECT cd.course_name, cd.difficulty, cd.n_results,
       ln(1.0 + cd.difficulty) AS ldiff
FROM   course_difficulties cd
WHERE  cd.course_name LIKE 'TF:loc:%%'
  AND  cd.course_name IN (
           SELECT DISTINCT 'TF:loc:' || mt.location_id::text
                  || CASE WHEN COALESCE(mt.is_indoor, 0) = 1
                          THEN ':in' ELSE ':out' END
           FROM   meets_tf mt
           WHERE  mt.meet_name ILIKE %(venue)s
             AND  mt.location_id IS NOT NULL)
ORDER  BY cd.n_results DESC
LIMIT  8
"""


# ------------------------------------------------------------------ #
#  SMALL HELPERS
# ------------------------------------------------------------------ #

def _pct(sorted_vals, q):
    """Linear-interpolated percentile on a pre-sorted list, q in [0, 1]."""
    n = len(sorted_vals)
    if n == 0:
        return None
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _rank(sorted_vals, x):
    """Fraction of values <= x, i.e. x's percentile in the sample."""
    if not sorted_vals:
        return None
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(sorted_vals)


def _mmss(seconds):
    if seconds is None:
        return "   --"
    m, s = divmod(float(seconds), 60.0)
    return f"{int(m)}:{s:04.1f}"


def _pts(log_gap, at=130.0):
    """A log gap expressed as rating points at a given rating level."""
    return at * (math.exp(log_gap) - 1.0)


# ------------------------------------------------------------------ #
#  THE TABLES
# ------------------------------------------------------------------ #

def forwardTable(pairs, args):
    """Fall band -> what the spring looked like, with censoring."""
    bw = args.band
    lo, hi = args.range
    case_r = math.log(args.case_tf / args.case_xc)
    f_lo, f_hi = args.focus

    print("\n" + "=" * 78)
    print(f"  FORWARD: {args.label} best in the fall -> spring "
          f"{int(args.tf_dist)}m best, same academic year")
    print("=" * 78)
    print("  r = ln(spring best / fall best). More negative = more improvement.")
    print(f"  The engine's implied r for the case is "
          f"ln({args.case_tf} / {args.case_xc}) = {case_r:+.4f}.\n")
    print(f"    {'fall band':<13}{'n':>7}{'spring':>8}{'cens%':>7}"
          f"{'sp p10':>8}{'sp p25':>8}{'sp med':>8}{'sp p75':>8}{'sp p90':>8}"
          f"{'r med':>9}{'case%':>7}")
    print("    " + "-" * 92)

    focus_rows = []
    b = lo
    while b < hi:
        rows = [p for p in pairs if b <= p["best_xc"] < b + bw]
        if rows:
            have = [p for p in rows if p["best_tf"] is not None]
            cens = 100.0 * (1 - len(have) / len(rows))
            tf = sorted(float(p["best_tf"]) for p in have)
            r = sorted(math.log(float(p["best_tf"]) / float(p["best_xc"]))
                       for p in have)
            case_pct = _rank(r, case_r)
            print(f"    {_mmss(b)}-{_mmss(b + bw):<7}{len(rows):>7}"
                  f"{len(have):>8}{cens:>7.1f}"
                  f"{_mmss(_pct(tf, .10)):>8}{_mmss(_pct(tf, .25)):>8}"
                  f"{_mmss(_pct(tf, .50)):>8}{_mmss(_pct(tf, .75)):>8}"
                  f"{_mmss(_pct(tf, .90)):>8}"
                  f"{(_pct(r, .5) if r else float('nan')):>+9.4f}"
                  f"{(100 * case_pct if case_pct is not None else float('nan')):>7.1f}")
            if b < f_hi and b + bw > f_lo:
                focus_rows.extend(have)
        b += bw

    print("\n    cens%  share of the band's fall athletes with no spring row at "
          "this event")
    print("    case%  share of the band's springs that improved at LEAST as "
          "much as the case;")
    print("           small = the case is an unusually good spring, large = "
          "an ordinary one\n")

    if not focus_rows:
        print(f"  focus band {_mmss(f_lo)}-{_mmss(f_hi)}: no athletes")
        return
    r = sorted(math.log(float(p["best_tf"]) / float(p["best_xc"]))
               for p in focus_rows)
    tf = sorted(float(p["best_tf"]) for p in focus_rows)
    case_pct = _rank(r, case_r)
    med_tf_from_case = args.case_xc * math.exp(_pct(r, .5))
    print(f"  FOCUS BAND {_mmss(f_lo)}-{_mmss(f_hi)}  ({len(focus_rows):,} "
          f"athlete-years with a spring row)")
    print(f"    median spring best         {_mmss(_pct(tf, .5))}")
    print(f"    median r                   {_pct(r, .5):+.4f}   -> a "
          f"{_mmss(args.case_xc)} runner's typical spring: "
          f"{_mmss(med_tf_from_case)}")
    print(f"    the case's r               {case_r:+.4f}   sits at the "
          f"{100 * case_pct:.1f}th percentile of springs")
    gap = case_r - _pct(r, .5)
    print(f"    case minus median          {gap:+.4f} log = "
          f"{_pts(-gap):+.1f} rating points at 130")
    print("    READ: if the case sits near the 50th percentile the engine's "
          "cells agree with the population\n          and the disagreement is "
          "definitional; far below it, the cells expect more improvement\n"
          "          than the population delivers, by the points shown.")

    # The venue's own share, for the selection the docstring warns about.
    at_v = [p for p in focus_rows if p["best_at_venue"]]
    if at_v:
        rv = sorted(math.log(float(p["best_tf"]) / float(p["best_xc"]))
                    for p in at_v)
        print(f"    of which best was at {args.venue_label}: {len(at_v):,}, "
              f"median r {_pct(rv, .5):+.4f}  (the SELECTED subset -- shown "
              f"only so its bias is visible)")


def meansTable(pairs, args):
    """Fall mean vs spring mean against what the cells imply."""
    bw = args.band
    lo, hi = args.range

    print("\n" + "=" * 78)
    print("  MEANS AGAINST THE CELLS: observed fall-minus-spring gap in log "
          "normalised time,\n  minus the gap the two cells' difficulties imply")
    print("=" * 78)
    print("  resid = (mean ln norm_XC - mean ln norm_TF) - (mean ln(1+d_XC) - "
          "mean ln(1+d_TF))")
    print("  positive = the spring outran what the engine expected from the "
          "cells -> TF under-rated vs XC\n")
    print(f"    {'fall band':<13}{'n':>7}{'obs gap':>10}{'implied':>10}"
          f"{'resid p25':>11}{'resid med':>11}{'resid p75':>11}{'pts@130':>9}")
    print("    " + "-" * 82)
    b = lo
    while b < hi:
        rows = [p for p in pairs
                if b <= p["best_xc"] < b + bw and p["mean_lnorm_tf"] is not None
                and p["ldiff_xc"] is not None and p["ldiff_tf"] is not None]
        if len(rows) >= 20:
            obs = [float(p["mean_lnorm_xc"]) - float(p["mean_lnorm_tf"])
                   for p in rows]
            imp = [float(p["ldiff_xc"]) - float(p["ldiff_tf"]) for p in rows]
            res = sorted(o - i for o, i in zip(obs, imp))
            med = _pct(res, .5)
            print(f"    {_mmss(b)}-{_mmss(b + bw):<7}{len(rows):>7}"
                  f"{sum(obs) / len(obs):>+10.4f}{sum(imp) / len(imp):>+10.4f}"
                  f"{_pct(res, .25):>+11.4f}{med:>+11.4f}{_pct(res, .75):>+11.4f}"
                  f"{_pts(med):>+9.1f}")
        b += bw
    print("\n    A residual that grows with the fall time is the tilt: the "
          "engine's one gap fits the middle\n    of the field and misses the "
          "ends. A residual flat and non-zero is a level error in the pair.\n")


def stateTable(pairs, args):
    """The focus band, split by where the athlete raced in spring.

    ★ SAME FALL CELL, DIFFERENT TRACK SIDES. Everyone here ran the same
      course, so a residual that differs by spring state cannot be the
      course. It is the track cells of that state against the fall cell,
      i.e. a regional XC-versus-track level.
    """
    lo, hi = args.focus
    rows = [p for p in pairs
            if lo <= p["best_xc"] < hi and p["mean_lnorm_tf"] is not None
            and p["ldiff_xc"] is not None and p["ldiff_tf"] is not None]
    by = {}
    for p in rows:
        by.setdefault(p.get("tf_state") or "??", []).append(p)
    print("\n" + "=" * 78)
    print(f"  BY SPRING STATE, focus band {_mmss(lo)}-{_mmss(hi)}: same fall "
          f"cell, each state's own track cells")
    print("=" * 78)
    print(f"    {'state':<7}{'n':>7}{'obs gap':>10}{'implied':>10}"
          f"{'resid med':>11}{'pts@130':>9}{'r med':>9}{'sp med':>9}")
    print("    " + "-" * 72)
    for st, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len(rs) < 30:
            continue
        obs = [float(p["mean_lnorm_xc"]) - float(p["mean_lnorm_tf"]) for p in rs]
        imp = [float(p["ldiff_xc"]) - float(p["ldiff_tf"]) for p in rs]
        res = sorted(o - i for o, i in zip(obs, imp))
        r = sorted(math.log(float(p["best_tf"]) / float(p["best_xc"]))
                   for p in rs if p["best_tf"] is not None)
        tf = sorted(float(p["best_tf"]) for p in rs if p["best_tf"] is not None)
        med = _pct(res, .5)
        print(f"    {st:<7}{len(rs):>7}{sum(obs) / len(obs):>+10.4f}"
              f"{sum(imp) / len(imp):>+10.4f}{med:>+11.4f}{_pts(med):>+9.1f}"
              f"{(_pct(r, .5) if r else float('nan')):>+9.4f}"
              f"{_mmss(_pct(tf, .5)) if tf else '--':>9}")
    print("\n    READ: a state whose residual is near zero has its track cells "
          "level with this fall cell;\n          one far below has track cells "
          "the engine credits too much relative to it, or the\n          reverse. "
          "Same course for all of them, so the course is not the difference.\n")


def reverseTable(rows, args):
    """Spring window -> what the previous fall at this course looked like."""
    tlo, thi = args.reverse
    have = [r for r in rows if r["best_xc"] is not None]
    print("\n" + "=" * 78)
    print(f"  REVERSE: spring {int(args.tf_dist)}m best in "
          f"{_mmss(tlo)}-{_mmss(thi)} -> that fall's best at {args.label}")
    print("=" * 78)
    if not rows:
        print("  no athlete-years in the window\n")
        return
    cens = 100.0 * (1 - len(have) / len(rows))
    print(f"    athlete-years in the window   {len(rows):,}")
    print(f"    of which raced {args.label:<16}{len(have):,}   "
          f"(censored {cens:.1f}% -- never ran this course that fall)")
    if not have:
        print()
        return
    xc = sorted(float(r["best_xc"]) for r in have)
    pct = _rank(xc, args.case_xc)
    print(f"    fall best  p10 {_mmss(_pct(xc, .1))}   p25 {_mmss(_pct(xc, .25))}"
          f"   med {_mmss(_pct(xc, .5))}   p75 {_mmss(_pct(xc, .75))}"
          f"   p90 {_mmss(_pct(xc, .9))}")
    print(f"    the case's {_mmss(args.case_xc)} sits at the {100 * pct:.1f}th "
          f"percentile (slow end = the case improved more than most)")
    print("    READ WITH THE FORWARD TABLE: forward is biased toward improvers, "
          "reverse toward regression\n    to the mean. The pair's truth sits "
          "between the two medians.\n")


def liftTable(rows, venue_rows, args):
    """The Arcadia test: lift by meet kind, beside the cell charged."""
    print("\n" + "=" * 78)
    print(f"  THE {args.venue_label.upper()} TEST: how much faster than their "
          f"own spring do athletes run there,\n  against other invitationals "
          f"and ordinary meets")
    print("=" * 78)
    print("  lift  = athlete's mean ln norm over their OTHER spring rows, minus "
          "this row's. Positive = faster here.")
    print("  cell  = mean ln(1 + difficulty) of the cells in the group, i.e. "
          "what the engine credits.\n")
    print(f"    {'kind':<20}{'rows':>8}{'lift p25':>10}{'lift med':>10}"
          f"{'lift p75':>10}{'cell':>9}{'lift-cell':>11}")
    print("    " + "-" * 78)
    by = {}
    for r in rows:
        by.setdefault(r["kind"], []).append(r)
    for kind in ("venue", "other invitational", "other meet"):
        rs = by.get(kind, [])
        if len(rs) < 20:
            print(f"    {kind:<20}{len(rs):>8}   (too few)")
            continue
        lift = sorted(float(r["lift"]) for r in rs)
        cells = [float(r["ldiff"]) for r in rs if r["ldiff"] is not None]
        cell = sum(cells) / len(cells) if cells else float("nan")
        med = _pct(lift, .5)
        # A cell that is EASIER than average has ldiff < 0; the engine then
        # takes -ldiff off the row. lift - (-ldiff) is what the athlete gained
        # beyond what the cell already removes.
        print(f"    {kind:<20}{len(rs):>8}{_pct(lift, .25):>+10.4f}{med:>+10.4f}"
              f"{_pct(lift, .75):>+10.4f}{cell:>+9.4f}{med + cell:>+11.4f}")
    print("\n    READ: venue lift ~ other-invitational lift  -> peaking and "
          "field quality, not the track;\n          the venue's cell is then "
          "charging a taper as easiness.\n          venue lift >> other "
          "invitationals              -> the track really is fast.\n")
    if venue_rows:
        print(f"  {args.venue_label} cells in course_difficulties:")
        for v in venue_rows:
            print(f"    {v['course_name']:<24} difficulty {v['difficulty']:+.4f}"
                  f"  ln {v['ldiff']:+.4f}   n_results {v['n_results']:,}")
        print()


# ------------------------------------------------------------------ #
#  ENTRY POINT
# ------------------------------------------------------------------ #

def main():
    ap = argparse.ArgumentParser(
        description="Fall course time -> spring track time, both directions, "
                    "with the selection printed. Read only.")
    ap.add_argument("--pool", default="hs_m")
    ap.add_argument("--course", default="%san antonio%",
                    help="ILIKE pattern on meets.course_name (default Mt. SAC)")
    ap.add_argument("--label", default="Mt. SAC",
                    help="how to name the course in the output")
    ap.add_argument("--dist", type=float, default=4828.0,
                    help="course distance in metres")
    ap.add_argument("--tol", type=float, default=150.0,
                    help="metres of slack on the stored distance")
    ap.add_argument("--tf-dist", type=float, default=3200.0, dest="tf_dist")
    ap.add_argument("--tf-tol", type=float, default=25.0, dest="tf_tol")
    ap.add_argument("--tf-months", type=int, nargs=2, default=(3, 7),
                    dest="tf_months", metavar=("LO", "HI"),
                    help="calendar months that count as spring (default 3 7)")
    ap.add_argument("--venue", default="%arcadia%",
                    help="ILIKE pattern on meets_tf.meet_name for the venue "
                         "test (default Arcadia)")
    ap.add_argument("--venue-label", default="Arcadia", dest="venue_label")
    ap.add_argument("--band", type=float, default=20.0,
                    help="fall band width, seconds")
    ap.add_argument("--range", type=float, nargs=2, default=(870.0, 1110.0),
                    metavar=("LO", "HI"), help="fall times tabulated")
    ap.add_argument("--focus", type=float, nargs=2, default=(930.0, 950.0),
                    metavar=("LO", "HI"), help="the band the case lives in")
    ap.add_argument("--case-xc", type=float, default=940.0, dest="case_xc",
                    help="the case's fall time, seconds (15:40)")
    ap.add_argument("--case-tf", type=float, default=541.1, dest="case_tf",
                    help="the case's spring time, seconds (9:01.1)")
    ap.add_argument("--reverse", type=float, nargs=2, default=(538.0, 545.0),
                    metavar=("LO", "HI"),
                    help="spring window for the reverse direction")
    ap.add_argument("--exclude-meets", type=int, nargs="*",
                    default=list(RAIN_COURSE_MEETS), dest="exclude_meets")
    ap.add_argument("--sql", action="store_true",
                    help="print the SQL and run nothing")
    args = ap.parse_args()

    params = {"pool": args.pool, "course": args.course, "dist": args.dist,
              "tol": args.tol, "excl": list(args.exclude_meets) or [-1],
              "venue": args.venue,
              "mlo": args.tf_months[0], "mhi": args.tf_months[1],
              "dlo": args.tf_dist - args.tf_tol,
              "dhi": args.tf_dist + args.tf_tol,
              "tlo": args.reverse[0], "thi": args.reverse[1]}

    if args.sql:
        for name, sql in (("XC", _XC_SQL), ("TF", _TF_SQL), ("PAIR", _PAIR_SQL),
                          ("REVERSE", _REVERSE_SQL), ("LIFT", _LIFT_SQL),
                          ("VENUE", _VENUE_DIFF_SQL)):
            print(f"\n-- {name}\n{sql.strip()}")
        print(f"\n-- params {params}")
        return 0

    from database import getConn
    from psycopg2.extras import RealDictCursor

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL work_mem = '512MB'")

            print(f"\n  building the fall side ({args.label}, {args.pool}, "
                  f"{args.dist:.0f} +- {args.tol:.0f} m)...")
            cur.execute(_XC_SQL, params)
            cur.execute("""
                SELECT course_name, distance, count(*) AS n,
                       min(race_date) AS first, max(race_date) AS last,
                       round(avg(exp(ldiff))::numeric - 1, 4) AS difficulty
                FROM   tj_xc GROUP BY 1, 2 ORDER BY n DESC LIMIT 12""")
            matched = cur.fetchall()
            if not matched:
                print("  NOTHING MATCHED the course pattern. Try --course "
                      "with a looser pattern, or --sql to see the query.\n")
                conn.rollback()
                return 1
            print("  matched course rows (check these are ONE course):")
            for m in matched:
                print(f"    {m['course_name'][:44]:<44} {m['distance']:>7.0f}m"
                      f"  {m['n']:>7,} rows  {m['first']}..{m['last']}"
                      f"  difficulty {m['difficulty']}")

            print(f"\n  building the spring side ({int(args.tf_dist)}m, months "
                  f"{args.tf_months[0]}-{args.tf_months[1]}, pool-wide)...")
            cur.execute(_TF_SQL, params)
            cur.execute("SELECT count(*) AS n, count(DISTINCT person_id) AS a, "
                        "count(*) FILTER (WHERE at_venue) AS v FROM tj_tf")
            t = cur.fetchone()
            print(f"    {t['n']:,} rows over {t['a']:,} athletes, "
                  f"{t['v']:,} at {args.venue_label}")

            cur.execute(_PAIR_SQL)
            pairs = cur.fetchall()
            cur.execute(_REVERSE_SQL, params)
            rev = cur.fetchall()
            cur.execute(_LIFT_SQL)
            lift = cur.fetchall()
            cur.execute(_VENUE_DIFF_SQL, params)
            venue = cur.fetchall()
            conn.rollback()

    pairs = [dict(p, best_xc=float(p["best_xc"])) for p in pairs]
    print(f"\n  {len(pairs):,} fall athlete-years at {args.label}; "
          f"{sum(1 for p in pairs if p['best_tf'] is not None):,} with a "
          f"spring {int(args.tf_dist)}m")

    forwardTable(pairs, args)
    meansTable(pairs, args)
    stateTable(pairs, args)
    reverseTable(rev, args)
    liftTable(lift, venue, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
