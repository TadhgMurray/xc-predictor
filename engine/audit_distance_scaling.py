"""
audit_distance_scaling.py -- does one athlete's rating depend on the distance?

    python engine/audit_distance_scaling.py
    python engine/audit_distance_scaling.py --person 29346285
    python engine/audit_distance_scaling.py --pool ms_m --limit 40

Run from the PROJECT ROOT. Reads results/results_tf. Writes nothing.

★ THE TEST NEEDS NO GROUND TRUTH, WHICH IS WHY IT IS WORTH RUNNING. Take an
  athlete who raced two DIFFERENT DISTANCES ON THE SAME DAY. Whatever their
  true ability is, it did not change between the two races -- so if the
  ratings disagree, the disagreement is in the distance normalisation and
  nowhere else. No opinion about how fast a 13-year-old should be is
  required, and none is offered.

  The case this was written for -- person 29346285, 2025-07-17:

      800m   2:10.53  ->  rating 192.3
      1500m  5:04.42  ->  rating 168.0

  Twenty-four rating points, one afternoon. Something is wrong, and it is
  not the athlete.

★ AND THE LIKELY CAUSE IS A MISSING ARTIFACT, WHICH THIS ALSO CHECKS.
  normalize_distance prefers a FITTED curve, distance_spline.pkl, and falls
  back to DISTANCE_EXPONENT_BY_POOL -- a flat 1.06 its own header calls "the
  placeholder". The fallback is announced once at import, to stderr, where
  nobody is looking:

      [normalize_distance] Distance splines not found ... no-op until the
      pickle exists.

  Flat 1.06 is roughly right near the anchor and badly wrong far from it, and
  the pools anchor LONG: ms at 3200m, hs at 5000m. So an 800m is a fourfold
  extrapolation in ms and a sixfold one in hs, in the direction that
  over-credits the short race. Worked on the case above:

      exponent 1.06   800m -> 9:27 at 3200m,  1500m -> 11:20   19.8% apart
      exponent 1.22   800m -> 11:48,          1500m -> 12:47    8.3% apart

  A 9:27 3200m by a 13-year-old would be a national record; that is where a
  192 comes from. The 1500m, only a twofold extrapolation, is much less
  distorted -- which is exactly the signature this report measures.

⚠ SO READ THE ARTIFACT LINE FIRST. If the fitted curve is missing, the gaps
  below are measuring the placeholder and the fix is to build the curve
  (fit_distance_exponent.py), not to adjust anything downstream.
"""

import os
import sys
import math
import argparse
import collections

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

# ! IMPORTED FOR ITS STATE, NOT ONLY ITS FUNCTIONS. Whether the fitted curve
#   loaded is a fact about this process, and the point of the report is to say
#   so out loud rather than let it stay a startup warning.
import normalize_distance as ND

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

_ARTIFACTS = (
    ("distance_spline.pkl", "distance scaling",
     "flat 1.06 per pool -- short events badly over-credited"),
    ("geometry_spline.pkl", "track length + banking", "no correction"),
    ("era_curve.pkl", "era", "no correction"),
    ("weather_correction_XC.pkl", "weather (XC)", "no correction"),
    ("weather_correction_TF.pkl", "weather (TF)", "no correction"),
    ("school_levels.pkl", "school -> level", "level from grade alone"),
)

# ! SAME DAY, SAME ATHLETE, DIFFERENT DISTANCE. The date is a string in this
#   schema, so the join is on its first ten characters rather than a cast --
#   the same shape the rest of the codebase uses.
_PAIRS_SQL = """
    WITH rated AS (
        SELECT person_id, substring(date, 1, 10) AS d, event, distance,
               speed_rating, result
        FROM   {table}
        WHERE  speed_rating IS NOT NULL AND speed_rating > 0
          AND  person_id IS NOT NULL
          AND  distance IS NOT NULL AND distance > 0
          {person}
    )
    SELECT a.person_id, a.d,
           a.distance AS d1, a.speed_rating AS r1, a.result AS t1,
           b.distance AS d2, b.speed_rating AS r2, b.result AS t2
    FROM   rated a
    JOIN   rated b ON b.person_id = a.person_id AND b.d = a.d
                  AND b.distance > a.distance
    LIMIT  %(scan)s
"""


def artifactReport():
    print("\nCORRECTION ARTIFACTS")
    missing = []
    for name, what, consequence in _ARTIFACTS:
        path = os.path.join(_DATA, name)
        there = os.path.exists(path)
        if not there:
            missing.append((name, what, consequence))
        size = f"{os.path.getsize(path):,} bytes" if there else "MISSING"
        print(f"  {'ok ' if there else '!! '} {name:<28} {what:<26} {size}")
    if missing:
        print("\n  ⚠ Every missing artifact is a correction that is silently "
              "a no-op.")
        for name, what, consequence in missing:
            print(f"    {name}: falls back to {consequence}")
    # The loaded object is the authority; a file can exist and still not load.
    fitted = bool(getattr(ND, "_SPLINES", None))
    print(f"\n  distance curve actually loaded in this process: "
          f"{'FITTED' if fitted else 'NO -- using the flat placeholder'}")
    return fitted


def worked(t1, d1, t2, d2, anchor):
    """What each race normalises to, at a range of exponents."""
    print(f"\n  the same two races at different scaling exponents "
          f"(anchor {anchor:.0f}m):")
    for e in (1.06, 1.12, 1.18, 1.22):
        n1 = t1 * (anchor / d1) ** e
        n2 = t2 * (anchor / d2) ** e
        gap = 100 * abs(n1 - n2) / min(n1, n2)
        tag = "   <- the placeholder" if e == 1.06 else ""
        print(f"    {e:.2f}   {int(d1)}m -> {int(n1)//60}:{n1 % 60:05.2f}"
              f"   {int(d2)}m -> {int(n2)//60}:{n2 % 60:05.2f}"
              f"   {gap:5.1f}% apart{tag}")


def main():
    ap = argparse.ArgumentParser(
        description="Measure whether a rating depends on the race distance, "
                    "using athletes who raced two distances on one day.")
    ap.add_argument("--person", type=int, help="explain this athlete only")
    ap.add_argument("--sport", choices=["XC", "TF", "both"], default="TF",
                    help="TF is where the distance range is widest")
    ap.add_argument("--scan", type=int, default=200_000,
                    help="same-day pairs to sample")
    ap.add_argument("--limit", type=int, default=25,
                    help="distance pairs to report")
    args = ap.parse_args()

    fitted = artifactReport()

    from database import getConn
    import psycopg2.extras

    tables = ({"TF": ["results_tf"], "XC": ["results"]}
              .get(args.sport, ["results", "results_tf"]))

    # gap -> the spread between two ratings the same athlete earned in one day
    buckets = collections.defaultdict(list)
    worst = []
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for table in tables:
                sql = _PAIRS_SQL.format(
                    table=table,
                    person=("AND person_id = %(person)s" if args.person else ""))
                params = {"scan": args.scan}
                if args.person:
                    params["person"] = args.person
                cur.execute(sql, params)
                for r in cur.fetchall():
                    d1, d2 = float(r["d1"]), float(r["d2"])
                    r1, r2 = float(r["r1"]), float(r["r2"])
                    ratio = d2 / d1
                    key = (table, round(d1), round(d2))
                    buckets[key].append(r1 - r2)
                    worst.append((abs(r1 - r2), ratio, r, table))

    if not buckets:
        print("\nNo same-day pairs found at different distances.")
        return

    print("\n\nSAME ATHLETE, SAME DAY, TWO DISTANCES")
    print("  ★ The athlete's ability did not change between the two races, so "
          "any\n    gap here is the distance normalisation and nothing else.\n")
    print(f"  {'table':<12}{'shorter':>9}{'longer':>9}{'pairs':>9}"
          f"{'median gap':>13}{'ratio':>8}")
    print("  " + "-" * 60)
    rows = sorted(buckets.items(), key=lambda kv: -len(kv[1]))[:args.limit]
    for (table, d1, d2), gaps in rows:
        gaps.sort()
        med = gaps[len(gaps) // 2]
        print(f"  {table:<12}{d1:>9}{d2:>9}{len(gaps):>9,}"
              f"{med:>+13.1f}{d2 / d1:>8.2f}x")
    print("\n  A gap that GROWS with the distance ratio is the scaling curve "
          "failing;\n  a flat gap near zero means it holds.")

    # ---- and one case, worked out --------------------------------------
    worst.sort(reverse=True)
    if worst:
        gap, ratio, r, table = worst[0]
        anchor = 3200.0 if table == "results_tf" else 5000.0
        print(f"\n\nTHE WIDEST PAIR IN THE SAMPLE")
        print(f"  person {r['person_id']}  {r['d']}  ({table})")
        print(f"    {int(float(r['d1']))}m  {r['t1']}  ->  rating {r['r1']}")
        print(f"    {int(float(r['d2']))}m  {r['t2']}  ->  rating {r['r2']}")
        print(f"    {gap:.1f} rating points, one afternoon")
        try:
            worked(_seconds(r["t1"]), float(r["d1"]),
                   _seconds(r["t2"]), float(r["d2"]), anchor)
        except (TypeError, ValueError):
            pass

    if not fitted:
        print("\n\n⚠ THE FITTED DISTANCE CURVE IS NOT LOADED, so everything "
              "above was\n  normalised with the flat 1.06 placeholder. Build "
              "it first --\n  engine/fit_distance_exponent.py -- then rerun "
              "this before changing\n  anything downstream.")


def _seconds(text):
    """'2:10.53' -> 130.53. Also accepts plain seconds."""
    parts = str(text).strip().split(":")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


if __name__ == "__main__":
    main()
