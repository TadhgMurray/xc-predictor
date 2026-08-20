"""
audit_pool_ceilings.py -- measure what each pool actually produces.

    python racecast/audit_pool_ceilings.py
    python racecast/audit_pool_ceilings.py --sport XC --since 2015

Run from the PROJECT ROOT. Reads athlete_season. Writes nothing.

★ THE POINT IS TO REPLACE A GUESS WITH A MEASUREMENT. pool_ceiling.py ships
  starting values -- 180 for middle school, 150 for high school, 130 for
  college -- and they are exactly that: starting values, chosen because a
  professional club was topping the high school board on 180 while the best
  real squad reached about 140. Nobody has yet looked at the distribution
  they are supposed to cut.

  Set one too HIGH and the club stays. Set one too LOW and the rail quietly
  deletes the greatest seasons in the corpus -- the ones this site exists to
  show -- and the board looks fine, because what is missing is missing.

⚠ SO READ THE NAMES, NOT ONLY THE COUNTS. A ceiling that drops 0.01% of a
  pool is not automatically right: the question is whether the athletes it
  drops are mistakes. The report prints who sits just under and just over
  each proposed line, because "Katelyn Tuohy" above the line means the line
  is wrong and no percentage will tell you that.
"""

import sys
import argparse

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")
from database import getConn
from pool_ceiling import POOL_CEILING, ceilingFor
from rankings import POOLS, US_STATES

# The candidates to price out beside the shipped one. Wide enough to show the
# shape of the tail rather than only the neighbourhood of today's guess.
_CANDIDATES = (120, 130, 140, 150, 160, 170, 180, 190)

_DIST_SQL = """
    SELECT pool,
           count(*)                                                   AS n,
           round(avg(mean_rating)::numeric, 1)                        AS mean,
           round(percentile_cont(0.50) WITHIN GROUP
                 (ORDER BY mean_rating)::numeric, 1)                  AS p50,
           round(percentile_cont(0.99) WITHIN GROUP
                 (ORDER BY mean_rating)::numeric, 1)                  AS p99,
           round(percentile_cont(0.999) WITHIN GROUP
                 (ORDER BY mean_rating)::numeric, 1)                  AS p999,
           round(percentile_cont(0.9999) WITHIN GROUP
                 (ORDER BY mean_rating)::numeric, 1)                  AS p9999,
           round(max(mean_rating)::numeric, 1)                        AS top
    FROM   athlete_season
    WHERE  mean_rating IS NOT NULL
      AND  n_races >= %(min_races)s
      AND  pool = ANY(%(pools)s)
      AND  state = ANY(%(states)s)
      AND  year >= %(since)s
      AND  (%(sport)s = 'both' OR sport = %(sport)s)
    GROUP  BY pool
    ORDER  BY pool
"""

# ! THE TOP OF EACH POOL, BY NAME. A count cannot tell you whether a ceiling
#   is cutting mistakes or records; a list of names can, in about four
#   seconds of reading.
_TOP_SQL = """
    SELECT school, state, year, sport,
           round(mean_rating::numeric, 1) AS rating, n_races
    FROM   athlete_season
    WHERE  pool = %(pool)s
      AND  mean_rating IS NOT NULL
      AND  n_races >= %(min_races)s
      AND  state = ANY(%(states)s)
      AND  year >= %(since)s
      AND  (%(sport)s = 'both' OR sport = %(sport)s)
    ORDER  BY mean_rating DESC
    LIMIT  %(lim)s
"""

# ★ WHICH SCHOOLS THE TAIL IS MADE OF. One mis-pooled club contributes dozens
#   of athlete-seasons, so the tail of a broken pool is not a spread of
#   individuals -- it is the same few names over and over. That signature is
#   the fastest way to find the next Oregon Track Club.
_TAIL_SCHOOLS_SQL = """
    SELECT btrim(school) AS school, state, count(*) AS n,
           round(max(mean_rating)::numeric, 1) AS best
    FROM   athlete_season
    WHERE  pool = %(pool)s
      AND  mean_rating > %(floor)s
      AND  n_races >= %(min_races)s
      AND  state = ANY(%(states)s)
      AND  year >= %(since)s
      AND  (%(sport)s = 'both' OR sport = %(sport)s)
    GROUP  BY 1, 2
    ORDER  BY n DESC, best DESC
    LIMIT  12
"""


def main():
    ap = argparse.ArgumentParser(
        description="Measure each pool's rating distribution, so "
                    "pool_ceiling.py can be set from data rather than guessed.")
    ap.add_argument("--sport", choices=["XC", "TF", "both"], default="both")
    ap.add_argument("--since", type=int, default=1990)
    ap.add_argument("--min-races", type=int, default=2,
                    help="match build_team_season.MIN_RACES (default 2)")
    ap.add_argument("--names", type=int, default=12,
                    help="how many top athlete-seasons to name per pool")
    args = ap.parse_args()

    base = {"min_races": args.min_races, "pools": sorted(POOLS),
            "states": list(US_STATES), "since": args.since,
            "sport": args.sport}

    import psycopg2.extras
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_DIST_SQL, base)
            dist = cur.fetchall()

            print(f"\nathlete_season, sport={args.sport}, since={args.since}, "
                  f"n_races >= {args.min_races}\n")
            print(f"  {'pool':<12}{'n':>10}{'mean':>7}{'p50':>7}{'p99':>7}"
                  f"{'p99.9':>8}{'p99.99':>8}{'max':>8}{'ceiling':>9}")
            print("  " + "-" * 76)
            for r in dist:
                print(f"  {r['pool']:<12}{r['n']:>10,}{r['mean']:>7}"
                      f"{r['p50']:>7}{r['p99']:>7}{r['p999']:>8}"
                      f"{r['p9999']:>8}{r['top']:>8}"
                      f"{ceilingFor(r['pool']):>9.0f}")

            # ---- what each candidate line would cost --------------------
            print("\n\nHOW MANY ATHLETE-SEASONS EACH CANDIDATE CEILING DROPS")
            print("  (the shipped ceiling is marked *)\n")
            header = "".join(f"{c:>12}" for c in _CANDIDATES)
            print(f"  {'pool':<12}{header}")
            print("  " + "-" * (12 + 12 * len(_CANDIDATES)))
            for r in dist:
                pool = r["pool"]
                shipped = ceilingFor(pool)
                cells = []
                for c in _CANDIDATES:
                    cur.execute(
                        "SELECT count(*) AS n FROM athlete_season "
                        "WHERE pool = %(pool)s AND mean_rating > %(c)s "
                        "  AND n_races >= %(min_races)s "
                        "  AND state = ANY(%(states)s) AND year >= %(since)s "
                        "  AND (%(sport)s = 'both' OR sport = %(sport)s)",
                        {**base, "pool": pool, "c": c})
                    n = cur.fetchone()["n"]
                    pct = 100.0 * n / r["n"] if r["n"] else 0.0
                    mark = "*" if abs(c - shipped) < 0.5 else " "
                    # ! ONE FIXED WIDTH, NOT A TRUNCATION. Cutting the cell to
                    #   fit ate the % sign, so "17%" read as "17" -- a column
                    #   of counts and a column of percentages look identical
                    #   and mean very different things.
                    cells.append(f"{n:,}{mark}{pct:>5.2f}%".rjust(12))
                print(f"  {pool:<12}" + "".join(cells))

            # ---- and WHO those are -------------------------------------
            print("\n\nTHE TOP OF EACH POOL, BY NAME")
            print("  ⚠ A real athlete above the line means the line is wrong. "
                  "Counts cannot tell you this.\n")
            for r in dist:
                pool = r["pool"]
                ceiling = ceilingFor(pool)
                cur.execute(_TOP_SQL, {**base, "pool": pool,
                                       "lim": args.names})
                print(f"  {pool}  (ceiling {ceiling:.0f})")
                for row in cur.fetchall():
                    flag = "DROPPED" if row["rating"] > ceiling else "  kept "
                    print(f"    {flag}  {row['rating']:>6}  "
                          f"{(row['school'] or '?')[:34]:<34} "
                          f"{row['state'] or '--':<3} {row['year']} "
                          f"{row['sport']}  {row['n_races']} races")

                # The schools that make up the tail, which is where a
                # mis-pooled club shows itself: many seasons, one name.
                floor = min(ceiling, float(r["p999"] or ceiling))
                cur.execute(_TAIL_SCHOOLS_SQL,
                            {**base, "pool": pool, "floor": floor})
                tail = cur.fetchall()
                if tail:
                    print(f"    schools above {floor:.0f} "
                          f"(many seasons from one name = a mis-pooled club):")
                    for t in tail:
                        print(f"      {t['n']:>4} seasons  best {t['best']:>6}"
                              f"  {(t['school'] or '?')[:40]} "
                              f"({t['state'] or '--'})")
                print()

    print("Set POOL_CEILING in racecast/pool_ceiling.py from the above, then "
          "rerun racecast/build_team_season.py.")


if __name__ == "__main__":
    main()
