"""
diag_event_pairs.py -- what athletes who run X for one event run for
another, in the same season. The empirical exchange rate, straight from
ranking_results, no model: the check on any "a 4:13 should beat a 9:01"
argument (issue 190).

    python scripts/diag_event_pairs.py --pool hs_m --a 3200 --b 1600
    python scripts/diag_event_pairs.py --pool hs_m --a 1600 --b 3200 --lo 250 --hi 262

Buckets athletes by their season-best time at event A (seconds, --lo to
--hi in --step buckets), and prints the median (and quartiles) of their
season-best at event B, with the count. Read: "a 9:00-9:05 3200 runner
runs a 4:1x 1600 in the same season" -- the same relation the solve's
event offsets encode, but visible.
"""
import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

_SQL = """
    WITH best AS (
        SELECT person_id, year, distance, min(time_seconds) AS t
        FROM   ranking_results
        WHERE  sport = 'TF' AND pool = %(pool)s
          AND  time_seconds > 0 AND time_seconds < 999999
          AND  (distance BETWEEN %(a_lo)s AND %(a_hi)s
                OR distance BETWEEN %(b_lo)s AND %(b_hi)s)
        GROUP  BY person_id, year, distance
    ),
    a AS (SELECT person_id, year, min(t) AS ta FROM best
          WHERE distance BETWEEN %(a_lo)s AND %(a_hi)s GROUP BY 1, 2),
    b AS (SELECT person_id, year, min(t) AS tb FROM best
          WHERE distance BETWEEN %(b_lo)s AND %(b_hi)s GROUP BY 1, 2)
    SELECT floor((a.ta - %(lo)s) / %(step)s) AS bucket,
           count(*),
           percentile_cont(0.25) WITHIN GROUP (ORDER BY b.tb),
           percentile_cont(0.5)  WITHIN GROUP (ORDER BY b.tb),
           percentile_cont(0.75) WITHIN GROUP (ORDER BY b.tb)
    FROM   a JOIN b USING (person_id, year)
    WHERE  a.ta >= %(lo)s AND a.ta < %(hi)s
    GROUP  BY 1 ORDER BY 1
"""


def _clock(s):
    return f"{int(s // 60)}:{s % 60:04.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="hs_m")
    ap.add_argument("--a", type=float, default=3200, help="event A, metres")
    ap.add_argument("--b", type=float, default=1600, help="event B, metres")
    # ! SECONDS, AND THE DEFAULTS ARE A 3200's. Named in the help because
    #   the pair (520, 640) is silently wrong for any other event, and the
    #   symptom used to be an empty table with no explanation.
    ap.add_argument("--lo", type=float, default=520,
                    help="event A's season-best time to bucket FROM, in "
                         "SECONDS (default %(default)s -- a 3200. A 10,000 m "
                         "is ~1700-2200, an 800 ~105-130)")
    ap.add_argument("--hi", type=float, default=640,
                    help="event A's season-best time to bucket TO, in SECONDS "
                         "(default %(default)s)")
    ap.add_argument("--step", type=float, default=10,
                    help="bucket width in SECONDS (default %(default)s)")
    args = ap.parse_args()
    p = {"pool": args.pool, "lo": args.lo, "hi": args.hi, "step": args.step,
         "a_lo": args.a * 0.99, "a_hi": args.a * 1.01,
         "b_lo": args.b * 0.99, "b_hi": args.b * 1.01}
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, p)
        rows = cur.fetchall()
        # ! AN EMPTY TABLE MUST SAY WHY (2026-09-22). --lo/--hi are event A's
        #   TIME IN SECONDS and they default to 520-640, a 3200. Ask for a
        #   10,000 m (~1,700-2,200 s) or an 800 (~105-130 s) and every athlete
        #   falls outside the buckets: the script printed a header, no rows,
        #   and no hint that the window was the problem rather than the data.
        #   So when nothing lands, go back and ask what the A times actually
        #   ARE -- the answer is the flags to use.
        if not rows:
            cur.execute("""
                SELECT count(*),
                       percentile_cont(0.05) WITHIN GROUP (ORDER BY t),
                       percentile_cont(0.50) WITHIN GROUP (ORDER BY t),
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY t)
                FROM (SELECT person_id, year, min(time_seconds) AS t
                      FROM   ranking_results
                      WHERE  sport = 'TF' AND pool = %(pool)s
                        AND  time_seconds > 0 AND time_seconds < 999999
                        AND  distance BETWEEN %(a_lo)s AND %(a_hi)s
                      GROUP  BY person_id, year) a
            """, p)
            n, p05, p50, p95 = cur.fetchone()
    if not rows:
        print(f"{args.pool}: nothing landed in the {args.lo:.0f}-{args.hi:.0f}s "
              f"buckets for {args.a:.0f} m.")
        if not n:
            print(f"  and no {args.a:.0f} m season-best exists for this pool at "
                  f"all -- check --pool and the distance.")
        else:
            print(f"  {n:,} season-bests exist; they run "
                  f"{_clock(p05)} (p05) .. {_clock(p50)} (median) .. "
                  f"{_clock(p95)} (p95).")
            step = max(round((p95 - p05) / 12.0), 1)
            print(f"  try:  --lo {p05:.0f} --hi {p95:.0f} --step {step:.0f}")
        return
    print(f"{args.pool}: season-best {args.a:.0f} m -> the same season's best "
          f"{args.b:.0f} m (athletes with both)")
    print(f"  {'A best':>13} {'n':>7} {'B 25%':>8} {'B median':>9} {'B 75%':>8}")
    for bucket, n, q1, med, q3 in rows:
        lo = args.lo + float(bucket) * args.step
        print(f"  {_clock(lo):>6}-{_clock(lo + args.step):<6} {n:>7,} "
              f"{_clock(q1):>8} {_clock(med):>9} {_clock(q3):>8}")


if __name__ == "__main__":
    main()
