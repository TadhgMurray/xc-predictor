"""
diag_annual_gain.py -- how much the SAME athlete improves in a year,
within one sport, by ability (issue 194). Fall XC best to the next
fall's XC best (or spring track best to the next spring's), in rating
points, per band of the first year's best. Within a sport the ratings
need no cross-sport level, so this is the one gain the data can
measure cleanly; the winter gain the engine pins is a SHARE of it.

    python scripts/diag_annual_gain.py                 # hs_m XC
    python scripts/diag_annual_gain.py --sport TF      # spring to spring
    python scripts/diag_annual_gain.py --pool hs_f

Read: a 130 athlete who gains 3 points a year, half of it over the
winter, should read +1.5 from fall best to spring best; the engine's
stated winter gain for that band is that share, and
diag_cross_sport_pairs shows what it reads today.
"""
import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

_SQL = """
    WITH best AS (
        SELECT person_id, year, max(speed_rating) AS r, count(*) AS n
        FROM   ranking_results
        WHERE  sport = %(sport)s AND pool = %(pool)s
          AND  speed_rating IS NOT NULL
          AND  {season}
        GROUP  BY person_id, year
        HAVING count(*) >= %(min_races)s
    ),
    pair AS (
        SELECT a.r AS r0, b.r - a.r AS gain,
               floor((a.r - 60) / %(step)s) AS bucket
        FROM   best a JOIN best b ON b.person_id = a.person_id
                                 AND b.year = a.year + 1
    )
    SELECT bucket, count(*),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gain),
           percentile_cont(0.25) WITHIN GROUP (ORDER BY gain),
           percentile_cont(0.75) WITHIN GROUP (ORDER BY gain),
           avg(gain)
    FROM   pair GROUP BY 1 HAVING count(*) >= 100 ORDER BY 1
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="XC", choices=("XC", "TF"))
    ap.add_argument("--pool", default="hs_m")
    ap.add_argument("--step", type=float, default=5)
    ap.add_argument("--min-races", type=int, default=3)
    args = ap.parse_args()
    season = ("EXTRACT(month FROM race_date) >= 8" if args.sport == "XC"
              else "EXTRACT(month FROM race_date) <= 6")
    p = {"sport": args.sport, "pool": args.pool, "step": args.step,
         "min_races": args.min_races}
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL.format(season=season), p)
        rows = cur.fetchall()
    print(f"{args.pool} {args.sport}: season-best rating one year to the "
          f"next, same athlete, by the first year's best "
          f"({args.min_races}+ races each year)")
    print(f"  {'first year':>12} {'n':>8} {'median':>7} {'25%':>6} {'75%':>6} {'mean':>6}")
    for bucket, n, med, q1, q3, mean in rows:
        lo = 60 + float(bucket) * args.step
        print(f"  {lo:>5.0f}-{lo + args.step:<6.0f} {n:>8,} {med:>+7.1f} "
              f"{q1:>+6.1f} {q3:>+6.1f} {mean:>+6.1f}")


if __name__ == "__main__":
    main()
