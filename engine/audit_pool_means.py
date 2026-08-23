"""
audit_pool_means.py -- what is the "100" in each pool actually made of?

    python engine/audit_pool_means.py
    python engine/audit_pool_means.py --pool ms_m --names 30

Run from the PROJECT ROOT. Reads pair_athlete_season. Writes nothing.

★ EVERY RATING IN A POOL IS MULTIPLIED BY ONE NUMBER, so if that number is
  wrong every rating in the pool is wrong by the same percentage. From
  speed_ratings:

      poolMeans          pool_mean = ARITHMETIC MEAN of ability, in seconds
      buildAthleteRatings  rating  = pool_mean / ability * 100

  A shared inflation across two different races by one athlete on one day
  cannot come from the distance curve, the course, or the athlete. It comes
  from the only term the two ratings share -- pool_mean.

★ AND THE RATING INVERTS, WITH NOTHING ASSUMED. rating/100 is exactly how
  many times faster the athlete is than their pool's mean, so a rating names
  the mean it was measured against:

      person 29346285, ms_m, 2025 TF
        1500m  5:04.42  rated 168.0  ->  the average ms_m athlete runs
                                         1500m in 8:31
        800m   2:10.53  rated 192.3  ->  ... and 800m in 4:11

  8:31 for 1500m is a 9:08 mile. If that is really the mean of ms_m, then
  "100" in that pool means something entirely different from "100" in hs_m,
  and no ceiling downstream can reconcile them.

⚠ A MEAN OF SECONDS HAS NO UPPER BOUND, WHICH IS THE THING TO CHECK FIRST.
  Ability is seconds, so one athlete solved to a pathological value pulls the
  pool mean up and every rating in the pool with it -- while a median would
  not move at all. This report puts mean and median side by side for exactly
  that reason: if mean/median is close to 1 the mean is honest and the pool
  really does contain everybody, and if it is not, the tail is doing it.

  The two have different fixes, so distinguishing them is the whole job:

    mean ≈ median   the pool is genuinely that wide -- middle school track
                    is a whole PE class, not a squad -- and the answer is
                    upstream, in who is admitted to the pool.
    mean >> median  a handful of solved abilities are dragging the anchor,
                    and the answer is a robust statistic here.
"""

import sys
import argparse

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

_DIST_SQL = """
    SELECT pool,
           count(*)                                              AS n,
           avg(ability)                                          AS mean,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY ability) AS p50,
           percentile_cont(0.90) WITHIN GROUP (ORDER BY ability) AS p90,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY ability) AS p99,
           max(ability)                                          AS worst,
           -- ! THE MEAN WITHOUT THE TAIL. If this lands on the median, the
           --   tail is what moved the anchor; if it lands on the mean, the
           --   pool really is that wide.
           avg(ability) FILTER (
               WHERE ability <= (SELECT percentile_cont(0.99)
                                 WITHIN GROUP (ORDER BY a2.ability)
                                 FROM pair_athlete_season a2
                                 WHERE a2.pool = a.pool
                                   AND a2.ability IS NOT NULL))  AS trimmed
    FROM   pair_athlete_season a
    WHERE  ability IS NOT NULL AND ability > 0
      AND  (%(pool)s IS NULL OR pool = %(pool)s)
    GROUP  BY pool
    ORDER  BY pool
"""

# ★ THE SLOWEST ABILITIES IN THE POOL, BY NAME. These are what the mean is
#   being dragged by, if it is being dragged. A real slow twelve-year-old is
#   a pool that is genuinely wide; forty seasons of the same club, or an
#   ability of 40,000 seconds, is a solve that failed.
_TAIL_SQL = """
    SELECT a.person_id, a.season, a.races, a.ability, a.rating_seasonal
    FROM   pair_athlete_season a
    WHERE  a.pool = %(pool)s AND a.ability IS NOT NULL
    ORDER  BY a.ability DESC
    LIMIT  %(lim)s
"""


def _fmt(seconds):
    """Ability is seconds at the pool's own anchor. Show both."""
    if seconds is None:
        return "-"
    return f"{seconds:>9,.0f}s = {int(seconds) // 60}:{seconds % 60:04.1f}"


def main():
    ap = argparse.ArgumentParser(
        description="Report the pool mean every rating is divided by, and "
                    "whether its tail is dragging it.")
    ap.add_argument("--pool", help="one pool, e.g. ms_m")
    ap.add_argument("--names", type=int, default=15,
                    help="slowest athlete-seasons to name per pool")
    args = ap.parse_args()

    from database import getConn
    import psycopg2.extras

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_DIST_SQL, {"pool": args.pool})
            rows = cur.fetchall()
            if not rows:
                print("No rows in pair_athlete_season. Run the pair engine "
                      "first.")
                return

            print("\nABILITY PER POOL -- the seconds that '100' is anchored to")
            print(f"\n  {'pool':<20}{'athletes':>10}{'mean':>11}{'median':>11}"
                  f"{'p99':>11}{'worst':>13}{'mean/med':>10}")
            print("  " + "-" * 86)
            suspect = []
            for r in rows:
                mean, p50 = float(r["mean"]), float(r["p50"])
                ratio = mean / p50 if p50 else 0.0
                if ratio > 1.10:
                    suspect.append((r["pool"], ratio))
                print(f"  {r['pool']:<20}{r['n']:>10,}{mean:>11,.0f}"
                      f"{p50:>11,.0f}{float(r['p99']):>11,.0f}"
                      f"{float(r['worst']):>13,.0f}{ratio:>10.2f}")

            print("\n  ★ mean/med is the whole diagnosis. 1.00 means the mean "
                  "is honest and\n    the pool really is that wide. Above it, "
                  "the tail is moving the anchor\n    and every rating in the "
                  "pool is inflated by that factor.")

            # ---- what a robust anchor would do to every rating ----------
            print("\n\nIF THE ANCHOR WERE THE MEDIAN INSTEAD OF THE MEAN")
            print("  Ratings scale linearly with the anchor, so this is the "
                  "factor EVERY\n  rating in the pool would be multiplied by.\n")
            print(f"  {'pool':<20}{'now':>10}{'trimmed p99':>14}"
                  f"{'median':>10}   example: a 180 becomes")
            print("  " + "-" * 76)
            for r in rows:
                mean, p50 = float(r["mean"]), float(r["p50"])
                trimmed = float(r["trimmed"]) if r["trimmed"] else mean
                print(f"  {r['pool']:<20}{1.0:>10.2f}{trimmed / mean:>14.2f}"
                      f"{p50 / mean:>10.2f}   "
                      f"{180 * trimmed / mean:>6.0f} trimmed, "
                      f"{180 * p50 / mean:>3.0f} on the median")

            # ---- and who the tail actually is --------------------------
            print("\n\nTHE SLOWEST ABILITIES IN EACH POOL")
            print("  ⚠ Read these. A genuinely slow twelve-year-old means the "
                  "pool is wide\n    and the fix is upstream, in who is "
                  "admitted. An ability of 40,000\n    seconds means a solve "
                  "that failed, and the fix is here.\n")
            for r in rows:
                pool = r["pool"]
                cur.execute(_TAIL_SQL, {"pool": pool, "lim": args.names})
                tail = cur.fetchall()
                if not tail:
                    continue
                print(f"  {pool}  (mean {float(r['mean']):,.0f}s, "
                      f"median {float(r['p50']):,.0f}s)")
                for t in tail:
                    print(f"    ability {_fmt(float(t['ability']))}   "
                          f"rating {float(t['rating_seasonal'] or 0):>6.1f}   "
                          f"{t['races']:>3} races   season {t['season']}   "
                          f"person {t['person_id']}")
                print()

    if suspect:
        print("⚠ Pools whose mean sits more than 10% above their median:")
        for pool, ratio in sorted(suspect, key=lambda x: -x[1]):
            print(f"    {pool}: every rating inflated by about "
                  f"{100 * (ratio - 1):.0f}%")
    else:
        print("No pool's mean is dragged far from its median -- the anchors "
              "are\nhonest, and a pool that still looks wrong is wrong in WHO "
              "IS IN IT,\nnot in how its middle was computed.")


if __name__ == "__main__":
    main()
