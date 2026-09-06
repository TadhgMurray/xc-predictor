"""
diag_cross_sport_pairs.py -- the SAME athletes, fall XC against spring
track, in the ratings the site shows (issue 194). The direct test of
the level at every ability, on real people, no model argument.

    python scripts/diag_cross_sport_pairs.py                    # hs_m, spring 3200 best
    python scripts/diag_cross_sport_pairs.py --event 1600 --lo 245 --hi 275 --step 5
    python scripts/diag_cross_sport_pairs.py --pool hs_f --lo 600 --hi 720

Buckets athletes by their spring season-best TIME at --event (Jan-Jun
of year Y), and for each bucket prints: the median rating that best
carries, the median of their fall XC season-best rating (Aug-Dec of
Y-1), the difference (--stat median compares each athlete's MEDIAN
rating on both sides instead, which no race count can bias), and the median fall XC time at the fall meet
most of them ran (--meet, a name fragment; default the biggest one
in the bucket).

Read: spring - fall is what the ratings say these athletes gained over
the winter (--leg winter) or lost over the summer (--leg summer, the
same spring against the NEXT fall). The two legs add up to the annual
gain diag_annual_gain measures, so a winter number is a choice of how
to split a measured year: stating more than the year gained makes
every summer read as a loss. The engine pins it at XCP_WINTER_GAIN for the AVERAGE
dual-sport athlete (2% = about 2.6 points at 130); whatever it reads
here at the top is the top's level, and the owner's number to state.
"""
import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

_SQL = """
    WITH tf0 AS (
        SELECT person_id, year, time_seconds, speed_rating
        FROM   ranking_results
        WHERE  sport = 'TF' AND pool = %(pool)s
          AND  distance BETWEEN %(ev_lo)s AND %(ev_hi)s
          AND  time_seconds > 0 AND speed_rating IS NOT NULL
          AND  EXTRACT(month FROM race_date) <= 6
          AND  year >= %(since)s
    ),
    tf AS (
        SELECT person_id, year, min(time_seconds) AS t,
               (array_agg(speed_rating ORDER BY time_seconds))[1] AS r_best,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating) AS r_med
        FROM   tf0 GROUP BY person_id, year
        HAVING min(time_seconds) >= %(lo)s AND min(time_seconds) < %(hi)s
    ),
    xc AS (
        SELECT x.person_id, x.year, max(x.speed_rating) AS r_best,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY x.speed_rating) AS r_med
        FROM   ranking_results x
        JOIN   tf ON tf.person_id = x.person_id AND x.year = tf.year - %(lag)s
        WHERE  x.sport = 'XC' AND x.pool = %(pool)s
          AND  x.speed_rating IS NOT NULL
          AND  EXTRACT(month FROM x.race_date) >= 8
        GROUP  BY x.person_id, x.year
    ),
    pair AS (
        SELECT tf.person_id, tf.year, tf.t AS tt, tf.{rcol} AS tr, xc.{rcol} AS xr,
               floor((tf.t - %(lo)s) / %(step)s) AS bucket
        FROM   tf JOIN xc ON xc.person_id = tf.person_id
                         AND xc.year = tf.year - %(lag)s
    )
    SELECT bucket, count(*),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY tr),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY xr),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY tr - xr),
           percentile_cont(0.25) WITHIN GROUP (ORDER BY tr - xr),
           percentile_cont(0.75) WITHIN GROUP (ORDER BY tr - xr)
    FROM   pair GROUP BY 1 ORDER BY 1
"""

_MEET = """
    WITH tf AS (
        SELECT DISTINCT ON (person_id, year) person_id, year, time_seconds AS t
        FROM   ranking_results
        WHERE  sport = 'TF' AND pool = %(pool)s
          AND  distance BETWEEN %(ev_lo)s AND %(ev_hi)s
          AND  time_seconds > 0 AND speed_rating IS NOT NULL
          AND  EXTRACT(month FROM race_date) <= 6
          AND  time_seconds >= %(lo)s AND time_seconds < %(hi)s
          AND  year >= %(since)s
        ORDER  BY person_id, year, time_seconds
    ),
    x AS (
        SELECT x.person_id, x.year, x.time_seconds AS xt, x.speed_rating AS xr,
               x.meet_id, round(x.distance) AS dist,
               floor((tf.t - %(lo)s) / %(step)s) AS bucket
        FROM   ranking_results x
        JOIN   tf ON tf.person_id = x.person_id AND x.year = tf.year - %(lag)s
        JOIN   meets m ON m.div_id = x.div_id
        WHERE  x.sport = 'XC' AND x.pool = %(pool)s
          AND  x.speed_rating IS NOT NULL AND x.time_seconds > 0
          AND  EXTRACT(month FROM x.race_date) >= 8
          AND  (m.meet_name ILIKE %(meet)s OR m.course_name ILIKE %(meet)s)
    )
    SELECT bucket, dist, count(*),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY xt),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY xr)
    FROM   x GROUP BY 1, 2 ORDER BY 1, 3 DESC
"""


def _clock(s):
    return f"{int(s // 60)}:{s % 60:04.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="hs_m")
    ap.add_argument("--event", type=float, default=3200)
    ap.add_argument("--lo", type=float, default=530)
    ap.add_argument("--hi", type=float, default=650)
    ap.add_argument("--step", type=float, default=10)
    ap.add_argument("--meet", default="%Mt. SAC%",
                    help="ILIKE pattern of a fall meet to show their times at")
    ap.add_argument("--leg", default="winter", choices=("winter", "summer"),
                    help="winter: the fall XC best BEFORE the spring (default);"
                         " summer: the fall XC best AFTER it, the other half"
                         " of the year (winter + summer = the annual gain)")
    ap.add_argument("--stat", default="best", choices=("best", "median"),
                    help="best: the rating of the spring best time against "
                         "the best fall rating (the fall best is the max of "
                         "more races, worth about a point); median: the "
                         "athlete's median rating on each side, count-free")
    ap.add_argument("--since", type=int, default=2000,
                    help="first spring year to include (the fall before is "
                         "the XC side); Mt. SAC's 3-mile course is 2022+, "
                         "so --since 2023 keeps it off the 2.93-mile one")
    args = ap.parse_args()
    p = {"pool": args.pool, "lo": args.lo, "hi": args.hi, "step": args.step,
         "ev_lo": args.event * 0.99, "ev_hi": args.event * 1.01,
         "meet": args.meet, "since": args.since,
         "lag": 1 if args.leg == "winter" else 0}
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL.format(rcol="r_best" if args.stat == "best" else "r_med"), p)
        rows = cur.fetchall()
        cur.execute(_MEET, p)
        at_meet = {}
        for b, dist, n, t, r in cur.fetchall():
            at_meet.setdefault(int(b), []).append((int(dist or 0), n, t, r))
    print(f"{args.pool}: spring season-best {args.event:.0f} m (spring "
          f"{args.since}+), {args.stat} ratings, and the same athletes' "
          f"{args.stat} fall XC rating the "
          f"{'fall before (winter leg)' if args.leg == 'winter' else 'fall after (summer leg)'}")
    print(f"  {'spring best':>13} {'n':>7} {'TF rtg':>7} {'XC rtg':>7} "
          f"{'TF-XC':>6} {'25%':>6} {'75%':>6}   at {args.meet.strip('%')}, "
          f"by distance label: n / median time / rating")
    for bucket, n, tr, xr, d, q1, q3 in rows:
        lo = args.lo + float(bucket) * args.step
        meet = "  ".join(f"{dist}m {mn:,}/{_clock(t)}/{r:.1f}"
                         for dist, mn, t, r in at_meet.get(int(bucket), [])[:2])
        print(f"  {_clock(lo):>6}-{_clock(lo + args.step):<6} {n:>7,} "
              f"{tr:>7.1f} {xr:>7.1f} {d:>+6.1f} {q1:>+6.1f} {q3:>+6.1f}   "
              f"{meet}")


if __name__ == "__main__":
    main()
