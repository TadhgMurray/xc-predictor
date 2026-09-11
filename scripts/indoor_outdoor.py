# Project: xc-predictor / scripts
# File:    indoor_outdoor.py
# Purpose: Does per-cell difficulty actually FIND the indoor/outdoor gap?
#
# ★★ THE OWNER'S QUESTION (2026-09-11): "indoor -> outdoor is fine if
#    difficulty finds it (does it?)". There is no geometry correction
#    anywhere in the engine. Indoor and outdoor at one facility are two
#    separate cells -- 'TF:loc:<id>:in' and 'TF:loc:<id>:out'
#    (conversions.py:557) -- and the ONLY thing that can separate them is
#    their fitted difficulty.
#
# ⚠ AND THERE IS A PRIOR PROBLEM WAITING. A banked 200m oval is slower than
#   a 400m outdoor track for a distance race: more turns, tighter radius,
#   more time fighting the curve. The published literature puts that at
#   roughly 1-2% for 3000m and the mile. The fitted TF course prior is
#   tau = 0.00892. If the true systematic gap is 1-2%, it is LARGER THAN
#   THE ENTIRE SPREAD the prior allows any track to have -- so the same
#   shrinkage that correctly pulls a thin track toward its sport's level
#   is also pulling every indoor track away from a real physical effect
#   that all of them share.
#
#   A shared effect belongs in a SHARED parameter. Altitude already works
#   that way (one k per sport, ALT_PRIOR_MEAN, not per-venue). If the gap
#   below is real and the model is shrinking it, indoor should be an
#   indicator term beside altitude rather than 6,000 independent cells
#   each rediscovering the same physics from its own thin evidence.
#
#   python scripts/indoor_outdoor.py
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn


def _pct(x):
    return 100.0 * x


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-races", type=int, default=0,
                    help="only locations where BOTH sides have this many races")
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            # ! split_part, not LIKE with a wildcard: the key is
            #   'TF:loc:<id>:<in|out>' and the id is numeric, so field 3 is
            #   the location and field 4 the surface. A LIKE '%%:in' also
            #   matches nothing useful when the suffix is missing.
            cur.execute("""
                SELECT split_part(course_name, ':', 3)  AS loc,
                       split_part(course_name, ':', 4)  AS surface,
                       difficulty,
                       COALESCE(n_races, 0)
                FROM   course_difficulties
                WHERE  course_name LIKE 'TF:loc:%%'
                  AND  split_part(course_name, ':', 4) IN ('in', 'out')
            """)
            rows = cur.fetchall()

    by_loc = {}
    for loc, surface, diff, n in rows:
        by_loc.setdefault(loc, {})[surface] = (float(diff), int(n))

    pairs = []
    for loc, d in by_loc.items():
        if "in" in d and "out" in d:
            (di, ni), (do, no) = d["in"], d["out"]
            if min(ni, no) >= args.min_races:
                pairs.append((loc, di, ni, do, no, di - do))

    n_in = sum(1 for _l, s in ((l, s) for l, dd in by_loc.items()
                               for s in dd) if s == "in")
    n_out = sum(1 for _l, s in ((l, s) for l, dd in by_loc.items()
                                for s in dd) if s == "out")
    print(f"\n  TF cells: {n_in:,} indoor, {n_out:,} outdoor, "
          f"{len(by_loc):,} locations")
    print(f"  locations with BOTH surfaces"
          + (f" and >= {args.min_races} races each" if args.min_races else "")
          + f": {len(pairs):,}")
    if not pairs:
        print("\n  ! No location has both. The gap cannot be measured this "
              "way,\n    and per-cell difficulty has no path to find it "
              "either: nothing\n    ties an indoor cell to its own outdoor "
              "cell.")
        return

    gaps = sorted(p[5] for p in pairs)
    mean = sum(gaps) / len(gaps)
    med = gaps[len(gaps) // 2]
    print(f"\n  indoor minus outdoor, same facility")
    print(f"    mean    {_pct(mean):+.3f}%")
    print(f"    median  {_pct(med):+.3f}%")
    print(f"    p10     {_pct(gaps[len(gaps) // 10]):+.3f}%")
    print(f"    p90     {_pct(gaps[9 * len(gaps) // 10]):+.3f}%")
    pos = sum(1 for g in gaps if g > 0)
    print(f"    indoor harder at {pos:,} of {len(gaps):,} "
          f"({100.0 * pos / len(gaps):.0f}%)")

    print(f"""
  HOW TO READ IT. A banked 200m oval should be SLOWER than a 400m outdoor
  track for a distance race -- more turns, tighter radius -- so the mean
  should be clearly POSITIVE, somewhere around +1 to +2%.

    mean near zero     the model is NOT finding it. Every indoor track is
                       being told it is an average track, and the
                       shrinkage that does the right thing to a thin
                       venue is doing the wrong thing to a real effect
                       that every indoor track shares.
    mean well positive and the coin-flip column near 100% -- it is
                       finding it, and per-cell difficulty is enough.

  ⚠ COMPARE THE MEAN AGAINST tau. The fitted TF course prior is about
    0.9%. A systematic gap near or above that cannot be carried by a
    prior that size: the prior is the standard deviation of ALL track
    difficulty, and one physical effect would be eating most of it.
    That is the case for making indoor a shared term beside altitude.""")


if __name__ == "__main__":
    main()
