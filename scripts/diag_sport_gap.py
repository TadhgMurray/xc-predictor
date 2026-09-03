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
      -- --tf-event: one track event class against the XC season, so the
      -- distance law (issue 109) can be told from the calendar
      AND  (%(tf_event)s = '' OR event_short ~* %(tf_event)s)
    GROUP  BY person_id),
-- --pool: the person's pool from athlete_ratings (bare pool names on a
-- merged run), so the band table is read within one pool and not across
-- middle school, high school and college at once
pp AS (
    SELECT DISTINCT athlete_id AS person_id FROM athlete_ratings
    WHERE  %(pool)s = '' OR pool = %(pool)s)
SELECT count(*)                                            AS people,
       round(avg(t.tf - x.xc)::numeric, 2)                 AS mean_tf_minus_xc,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY t.tf - x.xc))::numeric, 2)
                                                           AS median,
       round(avg(x.xc)::numeric, 2)                        AS mean_xc,
       round(avg(t.tf)::numeric, 2)                        AS mean_tf
FROM   x JOIN t USING (person_id) JOIN pp USING (person_id)
WHERE  x.n >= 2 AND t.n >= 2
  AND  x.xc >= %(min_xc)s
"""

# ★ BANDED ON THE AVERAGE OF THE TWO SEASONS, NOT ON ONE OF THEM. Selecting
#   people on their XC mean alone picks the ones whose fall was unusually
#   good (regression to the mean: the spring comes back down) AND the XC
#   specialists (beta > 0), and both read as "track is under XC at the top"
#   whether or not it is. --min-xc 125 read -4.5 on 2026-09-03 for exactly
#   that reason. (xc + tf) / 2 is symmetric in the two, so a band of it
#   shows how the gap moves with ability and nothing else.
_BANDS = _SQL.replace(
    "SELECT count(*)                                            AS people,",
    "SELECT (floor(((x.xc + t.tf) / 2.0) / 10.0) * 10)::int    AS band,\n"
    "       count(*)                                            AS people,"
) + "GROUP BY 1 ORDER BY 1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024,
                    help="the XC year; track is the following spring")
    ap.add_argument("--min-xc", type=float, default=0.0,
                    help="only people whose fall XC mean rating is at least "
                         "this: the corpus mean is ~105, so --min-xc 125 "
                         "asks whether the gap differs at the top (the "
                         "amplitude tilt says it may)")
    ap.add_argument("--pool", default="",
                    help="one pool only (hs_m, hs_f, college_m, ms_f ...), "
                         "from athlete_ratings")
    ap.add_argument("--tf-event", default="",
                    help="regex on results_tf.event_short for the track "
                         "side, e.g. '3200|2 mile', '1600|mile', '^800'. "
                         "A gap that moves with the event at one ability "
                         "band is the distance law (issue 109), not fitness")
    a = ap.parse_args()
    p = {"xc0": f"{a.year}-08-01", "xc1": f"{a.year}-12-31",
         "tf0": f"{a.year + 1}-01-01", "tf1": f"{a.year + 1}-06-30",
         "min_xc": a.min_xc, "pool": a.pool, "tf_event": a.tf_event}
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, p)
        row = cur.fetchone()
        cur.execute(_BANDS, p)
        bands = [list(r.values()) if isinstance(r, dict) else list(r)
                 for r in cur.fetchall()]
    if isinstance(row, dict):
        row = list(row.values())
    people, mean_gap, median, mxc, mtf = row
    print(f"XC {a.year} fall vs TF {a.year + 1} spring, people rated 2+ in both"
          f"{f' with XC mean >= {a.min_xc:g}' if a.min_xc else ''}"
          f"{f', pool {a.pool}' if a.pool else ''}"
          f"{f', track events ~ {a.tf_event!r}' if a.tf_event else ''}: "
          f"{people:,}")
    print(f"  mean  TF - XC rating: {mean_gap:+}")
    print(f"  median              : {median:+}")
    print(f"  mean XC {mxc}   mean TF {mtf}")
    print("  A sequential-engine run reads about 0 here. +10 or more is the "
          "level or the curve on every XC row (issue 143).")
    print("\n  by band of (XC + TF) / 2, the symmetric cut:")
    print(f"  {'band':>6} {'people':>9} {'mean':>7} {'median':>7} "
          f"{'xc':>7} {'tf':>7}")
    for band, people, mean_gap, median, mxc, mtf in bands:
        if people < 200:
            continue
        print(f"  {band:>4d}+ {people:>9,} {mean_gap:>+7} {median:>+7} "
              f"{mxc:>7} {mtf:>7}")
    print("  A slope here is ability-shaped structure (the amplitude tilt, "
          "issue 109's distance curve), not the winter gain, which is one "
          "number for everyone.")


if __name__ == "__main__":
    main()
