# Project: xc-predictor / scripts
# File:    diag_rating_jumps.py
# Purpose: READ ONLY. Divisions where the field rated far above what those
#          same athletes rate in their OWN other races. That is all.
#
#     python scripts/diag_rating_jumps.py
#     python scripts/diag_rating_jumps.py --gap 50 --min-athletes 3
#     python scripts/diag_rating_jumps.py --name minutemen
#     python scripts/diag_rating_jumps.py --meet 26359
#
# ★ THE WHOLE TEST, IN ONE SENTENCE: for each rated row, compare its rating to
#   the MEDIAN of that athlete's OTHER rated races -- any date, any season, any
#   distance -- and count how many rows in a division are more than `gap`
#   points above it. A division where three or more athletes each rate 50
#   points over their own career median is wrong, and it does not matter why.
#
# ⚠ WHAT THIS DELIBERATELY DOES NOT DO, because every other tool in this
#   repository does it and every one of them missed 26359/0:
#
#     - no distance-class baselines, so no sample to bias
#     - no minimum RATED field size, so a division where 102 of 108 rows were
#       dropped upstream still shows up on the six that survived
#     - no join to dist_override, so a division with no override is visible
#     - no normalization, no pool, no season mean, no shift table
#
#   A rating 50 points above an athlete's own median is self-evidently wrong.
#   It needs no reference class to be judged against, and building one is what
#   kept letting these through.

import argparse

from psycopg2.extras import RealDictCursor

from database import getConn


_TABLE = {"XC": "results", "TF": "results_tf"}

_SQL = """
WITH rated AS (
    SELECT r.meet_id, r.div_id, r.result_id, r.speed_rating,
           COALESCE(r.person_id, r.athlete_id) AS ident
    FROM   {table} r
    WHERE  r.speed_rating IS NOT NULL
      AND  r.speed_rating > 0
      AND  COALESCE(r.person_id, r.athlete_id) IS NOT NULL
),
-- Each athlete's own median over ALL their rated races.
--
-- ! THE ROW BEING JUDGED IS IN HERE, AND THAT IS FINE BECAUSE IT IS A
--   MEDIAN. Four races at 95 and one at 144.9 still medians to 95, so the
--   bad row does not lift the baseline it is measured against. A MEAN
--   would: it would read 105 and shrink a 50-point jump to 40. Excluding
--   the row per-row would need a lateral over 34M rows for a number the
--   median already gives for free.
--
-- ⚠ IT DOES SHRINK when a large FRACTION of an athlete's races are in bad
--   divisions -- if half their season is misrated the median follows. That
--   is the case --gap is for: lower it to 30 and the same divisions surface
--   with more of the field over the bar.
own AS (
    SELECT ident,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating) AS med,
           count(*) AS n_races
    FROM   rated
    GROUP  BY ident
    HAVING count(*) >= %(min_races)s
),
jumps AS (
    SELECT x.meet_id, x.div_id, x.speed_rating, o.med,
           x.speed_rating - o.med AS gap
    FROM   rated x
    JOIN   own o ON o.ident = x.ident
)
SELECT j.meet_id, j.div_id,
       count(*)                                          AS n_rated,
       count(*) FILTER (WHERE j.gap > %(gap)s)            AS n_jump,
       round(max(j.gap)::numeric, 1)                      AS worst,
       round(avg(j.gap) FILTER (WHERE j.gap > %(gap)s)::numeric, 1) AS avg_jump,
       round(max(j.speed_rating)::numeric, 1)             AS top_rating,
       m.course_name, m.distance, m.division, m.date
FROM   jumps j
LEFT   JOIN LATERAL (
    SELECT course_name, distance, division, date
    FROM   meets
    WHERE  meets.meet_id = j.meet_id AND meets.div_id = j.div_id
    LIMIT  1
) m ON TRUE
WHERE  (%(name)s IS NULL OR m.course_name ILIKE %(name)s)
  AND  (%(meet)s IS NULL OR j.meet_id = %(meet)s)
GROUP  BY j.meet_id, j.div_id, m.course_name, m.distance, m.division, m.date
HAVING count(*) FILTER (WHERE j.gap > %(gap)s) >= %(min_ath)s
ORDER  BY count(*) FILTER (WHERE j.gap > %(gap)s) DESC, max(j.gap) DESC
LIMIT  %(limit)s
"""


def main():
    ap = argparse.ArgumentParser(
        description="Divisions whose athletes rated far above their own "
                    "other races. Read only.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--gap", type=float, default=50.0,
                    help="points above the athlete's own median that counts "
                         "as a jump (default 50)")
    ap.add_argument("--min-athletes", type=int, default=3, dest="min_ath",
                    help="jumping athletes needed to list a division "
                         "(default 3 -- NOT a rated-field-size gate)")
    ap.add_argument("--min-races", type=int, default=3,
                    help="races an athlete needs elsewhere for their median "
                         "to mean anything (default 3)")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--name", default=None)
    ap.add_argument("--meet", type=int, default=None)
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_SQL.format(table=_TABLE[args.sport]),
                        {"gap": args.gap, "min_ath": args.min_ath,
                         "min_races": args.min_races, "limit": args.limit,
                         "name": f"%{args.name}%" if args.name else None,
                         "meet": args.meet})
            rows = cur.fetchall()

    print(f"\n  DIVISIONS RATING ABOVE THEIR OWN ATHLETES ({args.sport})")
    print(f"  {args.min_ath}+ athletes more than {args.gap:.0f} points above "
          f"their own median across their other races\n")
    if not rows:
        print("    nothing matched.\n")
        return 0

    print(f"    {'jump':>5}/{'rated':<5} {'worst':>6} {'avg':>6} {'top':>6} "
          f"{'label':>6} {'date':>10}  {'meet/div':>16}  course")
    for r in rows:
        d = f"{r['distance']:.0f}" if r["distance"] else "-"
        print(f"    {r['n_jump']:>5}/{r['n_rated']:<5} {r['worst']:>6} "
              f"{r['avg_jump']:>6} {r['top_rating']:>6} {d:>6} "
              f"{str(r['date'])[:10]:>10}  "
              f"{str(r['meet_id']) + '/' + str(r['div_id']):>16}  "
              f"{(r['course_name'] or '?')[:30]}")

    print(f"\n    jump/rated = athletes over the bar / athletes rated at all.")
    print(f"    A LOW `rated` next to a high `jump` is the worst case: most of")
    print(f"    the field was dropped upstream and what survived is nonsense.")
    print(f"    worst/avg  = points above the athlete's own median.")
    print(f"    top        = highest rating the division produced.\n")
    print("  Nothing was written. This script is read only.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
