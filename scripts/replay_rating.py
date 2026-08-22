# Project: xc-predictor / scripts
# File:    replay_rating.py
# Purpose: READ ONLY. Replay the ENGINE'S OWN rating chain, row by row, for one
#          race, and show which factor differs between two runners in it.
#
#     python scripts/replay_rating.py --meet 271870 --div 1083175
#
# ★ WHY A REPLAY AND NOT A DETECTOR. Within one race the engine computes
#
#       rating = 100 * pool_mean * (1 + difficulty) / normalized_time
#
#   and both pool_mean and difficulty are supposed to be properties of the
#   race, not the runner -- so rating must fall strictly with time. It does
#   not: at Gans Creek 2025, 18:38.9 rated 130.6 and 18:52.6 rated 134.8.
#
#   Reading the code says that is impossible, so one of the readings is wrong.
#   This does not argue about it: it runs the engine's OWN query, prints every
#   intermediate the engine would use, and lets the row that differs identify
#   itself.
#
# ⚠ IT IMPORTS THE ENGINE'S SQL, IT DOES NOT REIMPLEMENT IT. _xcQuery is the
#   thing under suspicion; a second copy of it here would agree with whatever I
#   assumed and prove nothing.

import argparse
import sys

from psycopg2.extras import RealDictCursor

sys.path.insert(0, "engine")
sys.path.insert(0, "scripts")

from database import getConn                       # noqa: E402
import speed_ratings_db as srdb                    # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="Replay the engine's per-row rating inputs for one race.")
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--div", type=int, required=True)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    # The engine's own query, unmodified, wrapped so we can filter to one race.
    # ! tw="" TURNS OFF THE CROSS-SOURCE TWIN DEDUP the real run uses. That
    #   can show a row here the engine would have dropped, so a duplicated
    #   result_id in the output is expected and is not the fault being hunted.
    inner = srdb._xcQuery(1.0, 1e9)
    sql = f"""
        WITH eng AS ({inner})
        SELECT e.*,
               r.speed_rating,
               r.time_seconds,
               round((r.speed_rating * e.normalized_time / 100.0)::numeric, 2)
                   AS pm_times_1plus_d
        FROM   eng e
        JOIN   results r ON r.result_id = e.result_id
        WHERE  r.meet_id = %(meet)s AND r.div_id = %(div)s
        ORDER  BY r.time_seconds
        LIMIT  %(lim)s
    """

    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, {"meet": args.meet, "div": args.div,
                              "lim": args.limit})
            rows = cur.fetchall()

    if not rows:
        print("  no rows -- the engine's own query returns nothing for this "
              "race, which is\n  itself the answer: it never rated them.")
        return 0

    print(f"\n  ENGINE INPUTS, ROW BY ROW -- meet {args.meet} div {args.div}"
          f"  ({len(rows)} rows)\n")
    print(f"    {'time':>8} {'rating':>7} {'norm':>9} {'pm*(1+d)':>9} "
          f"{'gender':>6}  venue key")
    seen = {}
    for r in rows:
        v = r.get("venue")
        seen.setdefault(v, 0)
        seen[v] += 1
        print(f"    {r['time_seconds']:>8.1f} {r['speed_rating']:>7.2f} "
              f"{r['normalized_time']:>9.2f} {r['pm_times_1plus_d']:>9.1f} "
              f"{str(r.get('gender')):>6}  {v}")

    print(f"\n  DISTINCT VENUE KEYS IN THIS RACE: {len(seen)}")
    for v, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"    x{n:<5} {v}")

    # ★ THE VERDICT, STATED BY THE DATA. pm*(1+d) is the only thing the two
    #   unknowns can hide in; if the venue key is one value and that product
    #   is not, the difficulty is not coming from the venue key.
    terms = [float(r["pm_times_1plus_d"]) for r in rows
             if r["pm_times_1plus_d"] is not None]
    if terms:
        lo, hi = min(terms), max(terms)
        print(f"\n  pm*(1+d):  min {lo:.1f}  max {hi:.1f}  "
              f"spread {100 * (hi - lo) / lo:.2f}%")
        if len(seen) > 1:
            print("  -> MORE THAN ONE VENUE KEY in a single race. The rows are "
                  "landing in\n     different cells, so they get different "
                  "difficulties. That is the fault.")
        elif hi - lo > 0.05:
            print("  -> ONE venue key, but pm*(1+d) still varies. Difficulty "
                  "cannot be the\n     source, so pool_mean differs per "
                  "athlete -- the pool is resolved per\n     ROW, not per "
                  "race, and two runners in one race are being measured\n"
                  "     against different pools.")
        else:
            print("  -> constant, as the formula requires. This race is clean.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
