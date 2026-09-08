#!/usr/bin/env python3
"""verdict16.py -- the numbers behind issues 306 (top seasons all XC, the
track shift), 304 (a shared college name's units), in under sixty lines,
so they fit a phone screen. Read-only.

    /srv/venv/bin/python scripts/verdict16.py [--school Georgetown]
"""
import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn                        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--school", default="Georgetown")
    ap.add_argument("--pool", default="hs_m")
    args = ap.parse_args()
    with getConn() as conn, conn.cursor() as cur:
        print("== sport_gain (the shift on track rows), per band")
        cur.execute("SELECT to_regclass('public.sport_gain')")
        if cur.fetchone()[0] is None:
            print("  (no sport_gain table)")
        else:
            cur.execute("""SELECT pool, sport, band, anchor_rating, target_gain, realised_gap, log_shift
                           FROM sport_gain ORDER BY pool, sport, anchor_rating""")
            for r in cur.fetchall():
                print(f"  {r[0]:10} {r[1]:3} band {r[2]!s:6} anchor {float(r[3]):6.1f} "
                      f"target {float(r[4]):+.4f} realised {float(r[5]) if r[5] is not None else float('nan'):+.4f} "
                      f"log_shift {float(r[6]):+.4f} ({100 * (2.718281828 ** float(r[6]) - 1):+.1f}% of time)")
        for top in (50, 200, 1000):
            cur.execute("""SELECT sport, count(*), min(mean_rating), max(mean_rating)
                           FROM (SELECT sport, mean_rating FROM athlete_season
                                 WHERE pool = %s AND n_races >= 3 AND mean_rating IS NOT NULL
                                 ORDER BY mean_rating DESC LIMIT %s) t GROUP BY 1 ORDER BY 1""",
                        (args.pool, top))
            rows = cur.fetchall()
            print(f"== top {top} {args.pool} seasons by sport: "
                  + "; ".join(f"{r[0]} {r[1]} ({float(r[2]):.1f} to {float(r[3]):.1f})" for r in rows))
        print("== same athlete, XC then next TF season, median gain (the real winter gain), by XC rating band")
        cur.execute("""
            SELECT width_bucket(x.mean_rating, 90, 150, 6) AS band, count(*),
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY t.mean_rating - x.mean_rating)
            FROM athlete_season x JOIN athlete_season t
              ON t.person_id = x.person_id AND t.pool = x.pool AND t.sport = 'TF' AND t.year = x.year
            WHERE x.pool = %s AND x.sport = 'XC' AND x.n_races >= 3 AND t.n_races >= 3
              AND x.year >= 2015
            GROUP BY 1 ORDER BY 1""", (args.pool,))
        for band, n, med in cur.fetchall():
            lo = 90 + (band - 1) * 10
            print(f"  XC {lo}-{lo + 10}: n {n:,} median TF-XC {float(med):+.2f}")
        print(f"== {args.school}: school_unit rows")
        cur.execute("""SELECT state, sport, division, votes FROM school_unit
                       WHERE school = %s ORDER BY sport, state""", (args.school,))
        for r in cur.fetchall():
            print(f"  {r[0] or '--':3} {r[1]:3} {r[2] or '-':12} votes {r[3]}")
        print(f"== {args.school}: athlete_season rows since 2024 (pool, sport, year, state, division)")
        cur.execute("""SELECT pool, sport, year, state, division, count(*) FROM athlete_season
                       WHERE school = %s AND year >= 2024 GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4""",
                    (args.school,))
        for r in cur.fetchall():
            print(f"  {r[0]:10} {r[1]:3} {r[2]} {r[3] or '--':3} {r[4] or '-':12} x{r[5]}")


if __name__ == "__main__":
    main()
