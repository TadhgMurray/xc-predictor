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
import speed_ratings as sr                         # noqa: E402
import normalize_distance as nd                    # noqa: E402



# ★ IS THIS RACE SPECIAL, OR IS THE IDENTITY FALSE EVERYWHERE? The engine
#   computes rating = 100 * pool_mean * (1 + difficulty) / normalized_time, and
#   both factors are constant within a division, so
#
#       speed_rating * normalized_time
#
#   must be constant across every division in the corpus. At Gans Creek it
#   spans 4.53% with one pool and one venue key -- which the code says is
#   impossible. Either that division is special, or the formula is not what
#   actually wrote the column. One sample answers it.
#
# ⚠ THIS IS NOT A HUNT FOR BAD MEETS. It is a check on whether the identity
#   holds at all. A high failure rate does not mean thousands of broken races;
#   it means the thing I have been reasoning from is wrong.
_SCAN = """
    WITH d AS (
        SELECT meet_id, div_id
        FROM   results
        WHERE  speed_rating > 0 AND normalized_time > 0
        GROUP  BY 1, 2
        HAVING count(*) >= 10
        ORDER  BY random()
        LIMIT  %(n)s
    )
    SELECT r.meet_id, r.div_id, count(*) AS n,
           round((stddev_pop(r.speed_rating * r.normalized_time)
                  / nullif(avg(r.speed_rating * r.normalized_time), 0)
                  * 100)::numeric, 3) AS pct_spread
    FROM   results r JOIN d USING (meet_id, div_id)
    WHERE  r.speed_rating > 0 AND r.normalized_time > 0
    GROUP  BY 1, 2
    ORDER  BY pct_spread DESC
"""


def scan(n):
    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_SCAN, {"n": n})
            rows = cur.fetchall()
    if not rows:
        print("  no divisions sampled.")
        return 0
    holds = [r for r in rows if float(r["pct_spread"] or 0) < 0.01]
    print(f"\n  IDENTITY CHECK on {len(rows)} random divisions "
          f"(>= 10 rated rows each)\n")
    print(f"    speed_rating * normalized_time must be CONSTANT within a "
          f"division.\n")
    print(f"    holds (< 0.01% spread):  {len(holds):>5} / {len(rows)}")
    print(f"    fails:                   {len(rows) - len(holds):>5} / "
          f"{len(rows)}\n")
    print(f"    {'spread%':>9} {'n':>5}  meet/div")
    for r in rows[:15]:
        print(f"    {r['pct_spread']:>9} {r['n']:>5}  "
              f"{r['meet_id']}/{r['div_id']}")
    print()
    if len(holds) == len(rows):
        print("  -> the identity holds everywhere sampled. Gans Creek is "
              "special, and the\n     question is what is different about "
              "that division's rows.")
    elif not holds:
        print("  -> the identity holds NOWHERE. rating is not "
              "100*pool_mean*(1+d)/norm on\n     stored data, so one of the "
              "two columns was written by a different run\n     than the "
              "other -- and the engine's formula is not the thing to debug.")
    else:
        print("  -> mixed. The identity holds for some divisions and not "
              "others, so it IS\n     the formula in use and something "
              "per-division breaks it.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Replay the engine's per-row rating inputs for one race.")
    ap.add_argument("--meet", type=int)
    ap.add_argument("--div", type=int)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--sample", type=int, default=0,
                    help="instead of one race, check the identity on N random "
                         "divisions and report how many hold")
    args = ap.parse_args()

    if args.sample:
        return scan(args.sample)
    if args.meet is None or args.div is None:
        ap.error("give --meet and --div, or --sample N")

    # The engine's own query, unmodified, wrapped so we can filter to one race.
    # ! tw="" TURNS OFF THE CROSS-SOURCE TWIN DEDUP the real run uses. That
    #   can show a row here the engine would have dropped, so a duplicated
    #   result_id in the output is expected and is not the fault being hunted.
    inner = srdb._xcQuery(1.0, 1e9)
    sql = f"""
        WITH eng AS ({inner})
        SELECT e.*,
               r.grade, r.school, r.source, r.person_id, r.distance AS raw_dist,
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
    # ★ AND THE POOL, WHICH IS THE STEP THIS TOOL WAS MISSING. The venue key
    #   is built in SQL and was already visible; the pool is decided in PYTHON,
    #   per row, by poolOf -- so a replay that stops at the SQL cannot see it.
    #   pool_mean is looked up by exactly this string, so if two runners in one
    #   race get different pools, they are divided by different constants and
    #   the race stops sorting by time.
    #
    # ⚠ CALLED, NOT REIMPLEMENTED, and with the same arguments packResults
    #   passes: grade, gender, source, school, sport, merge, person_id, season
    #   and race_date. poolOf reads module-level dicts (season levels, grade
    #   fixes, pro flags); those load on demand, so the first call is slow.
    print(f"    {'time':>8} {'rating':>7} {'norm':>9} {'RE-NORM':>9} "
          f"{'re/st':>7} {'pm*(1+d)':>9} {'pool':>16}")
    seen, pools, recomps = {}, {}, []
    for r in rows:
        v = r.get("venue")
        seen[v] = seen.get(v, 0) + 1
        try:
            # ! _asDate FIRST, EXACTLY AS packResults DOES. r.date comes out
            #   of the query as TEXT, and poolOf feeds it to seasonYearFor,
            #   which asserts on a real date object. Passing the raw string
            #   made every row report the same error and look unanimous.
            d = sr._asDate(r["date"])
            pool = sr.poolOf(r.get("grade"), r.get("gender"), r.get("source"),
                             r.get("school"), r.get("sport"), False,
                             person_id=r.get("person_id"),
                             season=d.year if hasattr(d, "year") else None,
                             race_date=d)
        except Exception as exc:                       # noqa: BLE001
            pool = f"<error {exc}>"
        pools[pool] = pools.get(pool, 0) + 1

        # ★ RUN THE ENGINE'S OWN normalizeTime ON THE RAW TIME. The stored
        #   normalized_time is an output of a PREVIOUS step; recomputing it
        #   here from time + distance + pool + season says whether the column
        #   still agrees with the code that wrote it. If it does not, the
        #   rating was computed against a norm the column no longer holds, and
        #   no amount of reading buildResultRatings will show that.
        recomp = None
        try:
            dist = r.get("distance") or r.get("raw_dist")
            if dist:
                bare = (pool or "").split("|")[0]
                recomp = nd.normalizeTime(
                    float(r["time_seconds"]), float(dist), bare,
                    season=d.year if d else None, sport=r.get("sport"))
        except Exception:                                  # noqa: BLE001
            recomp = None
        ratio = (recomp / float(r["normalized_time"])
                 if recomp and r["normalized_time"] else None)
        recomps.append(ratio)
        rs = f"{recomp:9.2f}" if recomp else "        -"
        rr = f"{ratio:7.5f}" if ratio else "      -"
        print(f"    {r['time_seconds']:>8.1f} {r['speed_rating']:>7.2f} "
              f"{r['normalized_time']:>9.2f} {rs} {rr} "
              f"{r['pm_times_1plus_d']:>9.1f} {str(pool):>16}")

    got = [x for x in recomps if x]
    if got:
        print(f"\n  RECOMPUTED / STORED normalized_time: "
              f"min {min(got):.5f}  max {max(got):.5f}")
        if max(got) - min(got) > 1e-4:
            print("  -> the engine's own normalizeTime does NOT reproduce the "
                  "stored column\n     by the same factor for every row. The "
                  "stored normalized_time is not\n     what the rating was "
                  "computed against.")
        else:
            print("  -> one constant factor for every row: the stored column "
                  "agrees with the\n     code that wrote it (any offset is "
                  "weather/geometry this call omits).")

    print(f"\n  DISTINCT POOLS IN THIS RACE: {len(pools)}")
    for pl, n in sorted(pools.items(), key=lambda kv: -kv[1]):
        print(f"    x{n:<5} {pl}")

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
        if len(pools) > 1:
            print("  -> MORE THAN ONE POOL in a single race. pool_mean is "
                  "looked up by that\n     string, so these runners are "
                  "divided by different constants. That is\n     the fault, "
                  "and it is in poolOf, not in the distance or the venue.")
        elif len(seen) > 1:
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
