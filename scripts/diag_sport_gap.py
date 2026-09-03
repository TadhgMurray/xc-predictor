"""
diag_sport_gap.py -- the XC-versus-track gap the ratings actually carry.

    python scripts/diag_sport_gap.py            # fall 2024 XC vs spring 2025 TF
    python scripts/diag_sport_gap.py --year 2023

For every person rated in both the fall XC season and the following spring
track season: mean TF rating minus mean XC rating. Under the sequential
engine the mean of this is about zero by construction (bbar recentred).
Under the joint solve it is amp x (f_spring - f_fall) plus whatever the
level got wrong (issue 143). Read-only.
"""
import argparse
import sys

sys.path.insert(0, "racecast")

from database import getConn                                    # noqa: E402

_SQL = """
WITH x AS (
    SELECT person_id, avg(speed_rating) AS xc, count(*) AS n
    FROM   results
    WHERE  date BETWEEN %(xc0)s AND %(xc1)s
      AND  speed_rating IS NOT NULL AND person_id IS NOT NULL
    GROUP  BY person_id),
t AS (
    SELECT person_id, avg(speed_rating) AS tf, count(*) AS n
    FROM   results_tf
    WHERE  date BETWEEN %(tf0)s AND %(tf1)s
      AND  speed_rating IS NOT NULL AND person_id IS NOT NULL
    GROUP  BY person_id)
SELECT count(*)                                            AS people,
       round(avg(t.tf - x.xc)::numeric, 2)                 AS mean_tf_minus_xc,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY t.tf - x.xc))::numeric, 2)
                                                           AS median,
       round(avg(x.xc)::numeric, 2)                        AS mean_xc,
       round(avg(t.tf)::numeric, 2)                        AS mean_tf
FROM   x JOIN t USING (person_id)
WHERE  x.n >= 2 AND t.n >= 2
  AND  x.xc >= %(min_xc)s
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024,
                    help="the XC year; track is the following spring")
    ap.add_argument("--min-xc", type=float, default=0.0,
                    help="only people whose fall XC mean rating is at least "
                         "this: the corpus mean is ~105, so --min-xc 125 "
                         "asks whether the gap differs at the top (the "
                         "amplitude tilt says it may)")
    a = ap.parse_args()
    p = {"xc0": f"{a.year}-08-01", "xc1": f"{a.year}-12-31",
         "tf0": f"{a.year + 1}-01-01", "tf1": f"{a.year + 1}-06-30",
         "min_xc": a.min_xc}
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, p)
        row = cur.fetchone()
    if isinstance(row, dict):
        row = list(row.values())
    people, mean_gap, median, mxc, mtf = row
    print(f"XC {a.year} fall vs TF {a.year + 1} spring, people rated 2+ in both"
          f"{f' with XC mean >= {a.min_xc:g}' if a.min_xc else ''}: "
          f"{people:,}")
    print(f"  mean  TF - XC rating: {mean_gap:+}")
    print(f"  median              : {median:+}")
    print(f"  mean XC {mxc}   mean TF {mtf}")
    print("  A sequential-engine run reads about 0 here. +10 or more is the "
          "level or the curve on every XC row (issue 143).")


if __name__ == "__main__":
    main()
