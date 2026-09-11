# Project: xc-predictor / scripts
# File:    indoor_outdoor.py
# Purpose: Is the indoor/outdoor switch being paid for by the FORM CURVE,
#          and is indoor difficulty being shrunk toward an outdoor zero?
#
# ★★ THE OWNER'S QUESTION, CORRECTLY PUT (2026-09-11). A first version of
#    this script compared 'TF:loc:<id>:in' against ':out' at the same
#    facility. That was the wrong measurement, and the run proved it:
#
#      TF cells: 1,574 indoor, 25,629 outdoor, 27,203 locations
#      locations with BOTH surfaces: 0
#
#    NOT ONE location has both. A location_id is indoor or outdoor and
#    never both, so the ':in'/':out' suffix carries no information the
#    location did not already carry -- and there is no within-venue
#    contrast anywhere in the corpus for per-cell difficulty to learn the
#    surface effect from. It can only come from athletes who race both.
#
#    So the two things that can actually go wrong are the two the owner
#    named:
#
#      1. THE FORM CURVE EATS THE SWITCH. Indoor runs roughly December to
#         March and outdoor March to June. The curve is a smooth function
#         of DATE applied to everyone. If indoor is systematically slower
#         -- a banked 200m oval is more turns and a tighter radius -- then
#         athletes appear to "get fitter" in March when what actually
#         happened is that they moved to a 400m track. The curve cannot
#         tell those apart, so it books a surface effect as spring
#         fitness, and every indoor mark is then judged against a curve
#         that expects them to be slow.
#
#      2. INDOOR IS SHRUNK TOWARD AN OUTDOOR ZERO. Indoor is 1,574 of
#         27,203 TF cells -- 6 per cent. tau[TF] = 0.00892 pulls every
#         thin cell toward the sport's mean, and that mean is defined by
#         the 94 per cent that are outdoor. A systematic indoor penalty
#         is therefore being shrunk away toward outdoor's level, by a
#         prior whose whole spread (0.9%) is smaller than the effect.
#
# ⚠ BOTH FAILURES LOOK LIKE SUCCESS FROM INSIDE THE SOLVE. The residuals
#   are small either way; the surface effect has simply been paid for by
#   the wrong parameter. The test is a WITHIN-ATHLETE contrast, which no
#   amount of curve or difficulty misallocation can fake: the same runner,
#   the same season, indoor against outdoor.
#
#   python scripts/indoor_outdoor.py
#   python scripts/indoor_outdoor.py --min-each 2
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn


def _pct(x):
    return 100.0 * x


# ! results_tf.date is TEXT, so EXTRACT raises -- substr, with a guard that
#   the text really is an ISO date. See season_reliability._ISO_DATE.
_ISO = "r.date::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"
# The TRACK season, not the calendar year: indoor December belongs with the
# following spring, so an athlete's indoor and outdoor marks land together.
_SEASON = ("(substr(r.date::text,1,4)::int"
           " + CASE WHEN substr(r.date::text,6,2)::int >= 8 THEN 1 ELSE 0 END)")


def withinAthlete(cur, min_each):
    """Same athlete, same track season, indoor mean rating vs outdoor.

    ★ WHY WITHIN-ATHLETE. Ability is the largest term in any rating and it
      cancels exactly when both sides come from one person in one season.
      What is left is the surface, plus whatever the model already removed.
      If the engine has priced indoor correctly this is ZERO.
    """
    cur.execute(f"""
        WITH r AS (
            SELECT r.person_id,
                   {_SEASON}                    AS season,
                   COALESCE(m.is_indoor, 0)     AS indoor,
                   r.speed_rating               AS rating
            FROM   results_tf r
            JOIN   meets_tf m ON m.meet_id = r.meet_id
            WHERE  r.speed_rating IS NOT NULL
              AND  r.speed_rating > 0
              AND  {_ISO}
        ), per AS (
            SELECT person_id, season,
                   avg(rating) FILTER (WHERE indoor = 1) AS ind,
                   avg(rating) FILTER (WHERE indoor = 0) AS out,
                   count(*)    FILTER (WHERE indoor = 1) AS n_ind,
                   count(*)    FILTER (WHERE indoor = 0) AS n_out
            FROM   r GROUP BY person_id, season
        )
        SELECT count(*),
               avg(ind - out),
               stddev_samp(ind - out),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY ind - out),
               avg(out)
        FROM   per
        WHERE  ind IS NOT NULL AND out IS NOT NULL
          AND  n_ind >= %(k)s AND n_out >= %(k)s
    """, {"k": min_each})
    return cur.fetchone()


def byMonth(cur):
    """Mean TF rating and the indoor share, month by month.

    ⚠ THE CURVE'S FINGERPRINT. If the form curve is paying for the
      surface switch, the months where indoor dominates read SLOW and the
      months where it does not read FAST, and the two move together.
      A curve that had nothing to do with surface would show the rating
      drifting smoothly while the indoor share collapses under it.
    """
    cur.execute(f"""
        SELECT substr(r.date::text, 6, 2)                       AS mon,
               count(*)                                          AS n,
               avg(r.speed_rating)                               AS mean_rating,
               avg(COALESCE(m.is_indoor, 0)::float)              AS indoor_share
        FROM   results_tf r
        JOIN   meets_tf m ON m.meet_id = r.meet_id
        WHERE  r.speed_rating IS NOT NULL AND r.speed_rating > 0
          AND  {_ISO}
        GROUP  BY 1 ORDER BY 1
    """)
    return cur.fetchall()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-each", type=int, default=1,
                    help="rated races required on EACH surface, per "
                         "athlete-season (default 1)")
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET work_mem = '512MB'")
            n, mean, sd, med, out_mean = withinAthlete(cur, args.min_each)
            months = byMonth(cur)

    print("\n  1. SAME ATHLETE, SAME SEASON: indoor rating minus outdoor")
    if not n:
        print("     no athlete-season has both surfaces -- nothing to say")
        return
    se = (float(sd) / (float(n) ** 0.5)) if sd else float("nan")
    print(f"     athlete-seasons with both   {int(n):,}")
    print(f"     mean indoor - outdoor       {float(mean):+.3f} rating points"
          f"  (se {se:.3f})")
    print(f"     median                      {float(med):+.3f}")
    print(f"     outdoor mean rating         {float(out_mean):.2f}")
    if out_mean:
        print(f"     as a share of level         "
              f"{_pct(float(mean) / float(out_mean)):+.2f}%")
    print(f"""
     ZERO is what a correct model gives: the same runner, the same
     season, judged the same on both surfaces. A NEGATIVE number means
     indoor marks are rated WORSE than the same athlete's outdoor marks
     -- the engine is not paying for the banked 200m and the athlete
     wears it. Compare the size against tau[TF] = 0.9%: anything near or
     above that cannot be carried by per-cell difficulty at all.""")

    print("\n  2. MONTH BY MONTH: does the curve move with the surface?")
    print(f"     {'mon':>3}  {'results':>10}  {'mean rating':>11}  "
          f"{'indoor share':>12}")
    for mon, cnt, mr, share in months:
        print(f"     {mon:>3}  {int(cnt):>10,}  {float(mr):>11.2f}  "
              f"{_pct(float(share)):>11.1f}%")
    print("""
     ⚠ READ THE TWO COLUMNS TOGETHER. If mean rating climbs as the indoor
       share falls -- slow in January and February, fast from April --
       that rise is the form curve being paid to explain a surface
       change. A real fitness curve would keep climbing through the
       outdoor months on its own; a surface artefact jumps where the
       share does, and then flattens.""")


if __name__ == "__main__":
    main()
