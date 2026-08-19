# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/venue_slope.py
# Purpose: find venues whose difficulty fits the MIDDLE of the field but not
#          the ends -- courses where fast runners come out too high and slow
#          runners too low, or the reverse.
#
#   THE BUG THIS FINDS, AND WHY NOTHING ELSE FINDS IT
#   -------------------------------------------------
#   Hydrangea Ranch (Ultimook), banded by ability, 13,503 athletes:
#
#        elsewhere    athletes    lift at this venue
#         80- 85          945        -1.88
#         90- 95        1,791        -1.71
#        100-105        1,937        -0.15
#        110-115        1,160        +1.40
#        115-120          720        +3.60
#        125-130          133        +5.18
#        130-135           34        +7.65
#        136-139            7        +8.29
#
#   Monotone, crossing zero near 105. The venue's MEAN lift is -0.19 -- i.e.
#   perfect -- because the mass of the field sits where the effect is nil.
#
#   ★ delta IS ONE MULTIPLICATIVE NUMBER PER CELL, so the fit lands where the
#     evidence is and mis-serves both ends in OPPOSITE directions. Any course
#     whose difficulty does not scale proportionally with pace does this: a
#     flat fast course costs a slow runner proportionally more time than a
#     fast one, mud and hills the reverse. One number cannot express that.
#
#   ★ AND IT IS INVISIBLE TO field_shift BY CONSTRUCTION. That detector
#     compares a field to their own career averages -- but the athletes who
#     race here repeatedly carry the error INTO those averages, so the
#     baseline absorbs it and the race stops looking anomalous. A venue with a
#     wrong delta and a stable repeat field conceals itself. This is the class
#     that survives to the leaderboard quietly: not a 170, a 145 where 135
#     belongs.
#
#   THE MEASUREMENT
#   ---------------
#   For every athlete: their mean rating AT the venue, minus their mean rating
#   EVERYWHERE ELSE. The "elsewhere" figure excludes the venue entirely, so
#   the baseline cannot contain the error being measured -- and it is also
#   what the athlete is BANDED by, so the bands are uncontaminated too.
#
#   Then fit a slope of lift against ability. A venue whose delta is right for
#   everyone has slope ~0 at every ability. A venue with a pace-dependent
#   course has a slope, and its SIGN says which end is being mistreated.

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# ------------------------------------------------------------------ #
#  CONSTANTS
# ------------------------------------------------------------------ #

# An athlete needs this many races AWAY from the venue for their "elsewhere"
# figure to be a baseline rather than a single race's noise.
MIN_AWAY_RACES = 3

# A venue needs this many qualifying athletes before its slope means anything.
MIN_ATHLETES = 200

# Ability bands: compare the top of the field against the middle. The bands
# are on `elsewhere`, never on the venue's own ratings.
#
# ★ 'TOP' IS 115+, NOT THE TOP DECILE. A percentile band moves with the
#   venue's own population -- a middle-school venue's 90th percentile is a
#   different athlete from a college venue's. An absolute rating band asks the
#   same question of every course.
BAND_MID_LO, BAND_MID_HI = 95.0, 110.0
BAND_TOP_LO = 115.0

# Report venues whose top-band lift differs from their mid-band lift by more
# than this. 3 points is roughly the per-race noise floor (ah measures the
# same athlete at the same venue in the same season varying 3.3-4.5), so a
# systematic gap larger than that is not noise.
MIN_SPREAD = 3.0


# ------------------------------------------------------------------ #
# CHUNK 1 -- PER-ATHLETE, PER-VENUE LIFT
# ------------------------------------------------------------------ #

def buildLift(cur):
    """
    venue_lift: (course_name, person_id, at_venue, elsewhere).

    ★ `elsewhere` EXCLUDES THE VENUE, PER VENUE. It is not one career average
      reused for every course -- that would leave each venue's own error in
      its own baseline, which is precisely the circularity that hides this
      bug. Computed as (career total - this venue's total) / (career n - this
      venue's n), which gets the leave-one-out figure from two sums instead of
      a self-join per venue.
    """
    cur.execute("DROP TABLE IF EXISTS venue_lift")
    cur.execute(f"""
        CREATE TABLE venue_lift AS
        WITH rated AS (
            SELECT r.person_id, m.course_name, r.speed_rating
            FROM   results r
            JOIN   meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
            WHERE  r.speed_rating BETWEEN 40 AND 200
              AND  r.person_id IS NOT NULL
              AND  m.course_name IS NOT NULL
        ),
        career AS (
            SELECT person_id, sum(speed_rating) AS tot, count(*) AS n
            FROM   rated GROUP BY 1
        ),
        here AS (
            SELECT person_id, course_name,
                   sum(speed_rating) AS tot, count(*) AS n
            FROM   rated GROUP BY 1, 2
        )
        SELECT h.course_name,
               h.person_id,
               (h.tot / h.n)                             AS at_venue,
               ((c.tot - h.tot) / (c.n - h.n))           AS elsewhere,
               (c.n - h.n)                               AS n_away
        FROM   here h
        JOIN   career c ON c.person_id = h.person_id
        WHERE  c.n - h.n >= {MIN_AWAY_RACES}
    """)
    cur.execute("CREATE INDEX ON venue_lift (course_name)")
    cur.execute("ANALYZE venue_lift")
    cur.execute("SELECT count(*), count(DISTINCT course_name) FROM venue_lift")
    n, v = cur.fetchone()
    print(f"    {n:,} athlete-venue pairs across {v:,} venues")
    return n


# ------------------------------------------------------------------ #
# CHUNK 2 -- THE CORPUS BASELINE
# ------------------------------------------------------------------ #

def corpusBands(cur):
    """
    The whole-corpus lift by ability band.

    ★ RUN THIS FIRST AND READ IT BEFORE THE VENUE LIST. If EVERY venue shows
      +5 at the top, the slope is a global property of the rating model and no
      individual course is at fault -- the venue ranking below would then be
      ranking noise and acting on it would be wrong. The corpus curve is the
      null hypothesis this whole script is tested against.
    """
    cur.execute("""
        SELECT width_bucket(elsewhere, 80, 145, 13) AS band,
               round(min(elsewhere)::numeric, 0) AS lo,
               round(max(elsewhere)::numeric, 0) AS hi,
               count(*) AS athlete_venues,
               round(percentile_cont(0.5) WITHIN GROUP
                     (ORDER BY at_venue - elsewhere)::numeric, 2) AS med_lift
        FROM   venue_lift
        GROUP  BY 1 ORDER BY 1
    """)
    print("\n[slope] CORPUS baseline -- lift by ability band")
    print(f"    {'lo':>5}{'hi':>5}{'pairs':>12}{'med_lift':>10}")
    for band, lo, hi, n, lift in cur.fetchall():
        print(f"    {lo:>5}{hi:>5}{n:>12,}{lift:>10}")


# ------------------------------------------------------------------ #
# CHUNK 3 -- PER-VENUE SLOPE
# ------------------------------------------------------------------ #

def venueSlopes(cur, limit=40):
    """
    Per venue: median lift for mid-ability athletes vs top-ability ones.

    `spread` = top_lift - mid_lift. Positive means the venue over-rates its
    fast runners relative to its slow ones.

    ★ MEDIAN, NOT MEAN. One athlete with a corrupted career average would move
      a mean by several points in a 200-athlete band.

    ⚠ THE SPREAD IS RELATIVE TO THE VENUE'S OWN MIDDLE, not to zero. A course
      that is uniformly 5 points generous has a delta error and field_shift
      would find it; this looks for the courses where delta CANNOT be right
      for everyone at once, which is a different defect with a different fix.
    """
    cur.execute(f"""
        WITH banded AS (
            SELECT course_name,
                   CASE WHEN elsewhere BETWEEN {BAND_MID_LO} AND {BAND_MID_HI}
                        THEN 'mid'
                        WHEN elsewhere >= {BAND_TOP_LO} THEN 'top' END AS band,
                   at_venue - elsewhere AS lift
            FROM   venue_lift
        ),
        per AS (
            SELECT course_name,
                   count(*) FILTER (WHERE band = 'mid') AS n_mid,
                   count(*) FILTER (WHERE band = 'top') AS n_top,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY lift)
                       FILTER (WHERE band = 'mid') AS mid_lift,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY lift)
                       FILTER (WHERE band = 'top') AS top_lift
            FROM   banded WHERE band IS NOT NULL
            GROUP  BY 1
        )
        SELECT course_name, n_mid, n_top,
               round(mid_lift::numeric, 2),
               round(top_lift::numeric, 2),
               round((top_lift - mid_lift)::numeric, 2) AS spread
        FROM   per
        WHERE  n_mid >= {MIN_ATHLETES} AND n_top >= 30
          AND  abs(top_lift - mid_lift) >= {MIN_SPREAD}
        ORDER  BY abs(top_lift - mid_lift) DESC
        LIMIT  %s
    """, (limit,))
    rows = cur.fetchall()
    print(f"\n[slope] venues whose top and mid bands disagree by "
          f">= {MIN_SPREAD} points")
    print(f"    {'n_mid':>7}{'n_top':>7}{'mid':>8}{'top':>8}{'spread':>9}"
          f"  venue")
    for course, n_mid, n_top, mid, top, spread in rows:
        print(f"    {n_mid:>7}{n_top:>7}{mid:>8}{top:>8}{spread:>9}"
              f"  {course[:46]}")
    print(f"\n    {len(rows)} venues shown.")
    return rows


def oneVenue(cur, pattern):
    """Full band table for a single venue -- the Ultimook view."""
    cur.execute("""
        SELECT width_bucket(elsewhere, 80, 145, 13) AS band,
               round(min(elsewhere)::numeric, 0) AS lo,
               round(max(elsewhere)::numeric, 0) AS hi,
               count(*) AS athletes,
               round(percentile_cont(0.5) WITHIN GROUP
                     (ORDER BY at_venue - elsewhere)::numeric, 2) AS med_lift
        FROM   venue_lift
        WHERE  course_name ILIKE %s
        GROUP  BY 1 ORDER BY 1
    """, (pattern,))
    print(f"\n[slope] {pattern}")
    print(f"    {'lo':>5}{'hi':>5}{'athletes':>10}{'med_lift':>10}")
    for band, lo, hi, n, lift in cur.fetchall():
        print(f"    {lo:>5}{hi:>5}{n:>10,}{lift:>10}")


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(rebuild=True, venue=None):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            if rebuild:
                print("[slope] building per-athlete venue lift...")
                buildLift(cur)
                conn.commit()

            corpusBands(cur)
            if venue:
                oneVenue(cur, venue)
            else:
                oneVenue(cur, "%Hydrangea%")
            venueSlopes(cur)

    print("\n[slope] Read the CORPUS table first. If it already slopes, the")
    print("        venue list is ranking noise -- the model, not the course.")


if __name__ == "__main__":
    _v = next((a.split("=", 1)[1] for a in sys.argv
               if a.startswith("--venue=")), None)
    main(rebuild="--no-rebuild" not in sys.argv, venue=_v)