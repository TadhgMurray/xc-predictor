#!/usr/bin/env python3
"""
distance_curve_check.py -- what the shipped distance curve says, in the
one number a coach can argue with: the local exponent between events.

    scripts/distance_curve_check.py
    scripts/distance_curve_check.py --pool hs_m --pool college_m
    scripts/distance_curve_check.py --floor 1.04     # what the fitter's floor would change

Run from the PROJECT ROOT. Reads engine/data/distance_spline.pkl through
normalize_distance; no database.

★ WHY (owner, 2026-09-13: "the distance normalization might be off").
  normalized_time is t x exp(g(target) - g(d)); between two distances the
  curve's slope is the exponent k in t2/t1 = (d2/d1)^k. Riegel's k for a
  trained runner is 1.06-1.10 and no runner has k under 1.0 (their pace
  would speed up as the race got longer), so a segment under about 1.04
  or over about 1.20 is the curve, not the runner: a same-athlete pair
  that did not cancel the calendar or the course. Read 2026-09-13 from
  the shipped artifact: college_m|XC 8000->10000 at 0.92 (the "10k too
  low" symptom on the college boards), elem_m|XC past 3200 at 0.98, and
  every TF pool at 1.15-1.18 between 800 and 1000. The fitter now holds
  a curve to MIN_LOCAL_EXP (engine/fit_distance_exponent.py); this prints
  the artifact as it is, and with --floor what that floor would do.
"""
import argparse
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DISTANCES = (800, 1000, 1500, 1600, 2000, 3000, 3200, 5000, 6000, 8000, 10000)
POOLS = ("elem_m", "elem_f", "ms_m", "ms_f", "hs_m", "hs_f", "college_m", "college_f")
SANE = (1.04, 1.20)


def factors(pool, sport, distances=DISTANCES, floor=None):
    """{distance: multiplier} for a pool and sport from the artifact, the
    curve's local exponent optionally held to `floor` first."""
    import normalize_distance as nd
    entry = nd._distancePotentialEntry(pool, sport)
    if floor:
        from fit_distance_exponent import _floorLocalExponent
        vals, _n = _floorLocalExponent(entry["knots"], entry["values"], floor)
        entry = dict(entry, values=vals)
    base = (pool or "").split("|")[0]
    target = (nd._SPLINES.get("pool_targets", {}).get(base) or entry.get("target")
              or nd._SPLINES["target"])
    g_t = nd._evalDistancePotential(entry, math.log(target))
    return {d: math.exp(g_t - nd._evalDistancePotential(entry, math.log(d)))
            for d in distances}, float(target)


def segments(f, distances=DISTANCES):
    """[(d1, d2, exponent)] between consecutive distances: t2/t1 = (d2/d1)^k."""
    out = []
    for a, b in zip(distances, distances[1:]):
        out.append((a, b, math.log(f[a] / f[b]) / math.log(b / a)))
    return out


def report(pools=POOLS, sports=("TF", "XC"), floor=None, sane=SANE, out=print):
    flagged = 0
    for pool in pools:
        for sport in sports:
            try:
                f, target = factors(pool, sport, floor=floor)
            except Exception as exc:                          # noqa: BLE001
                out(f"{pool}|{sport}: no curve ({type(exc).__name__}: {exc})")
                continue
            segs = segments(f)
            bad = [s for s in segs if not (sane[0] <= s[2] <= sane[1])]
            flagged += len(bad)
            out(f"\n{pool}|{sport}  (normalised to {target:.0f} m)"
                + (f"  [floor {floor:g} applied]" if floor else ""))
            out("   " + "  ".join(f"{a}->{b}: {k:.3f}{'!' if not (sane[0] <= k <= sane[1]) else ' '}"
                                for a, b, k in segs))
    out(f"\n{flagged} segment(s) outside [{sane[0]:.2f}, {sane[1]:.2f}] "
        "(! = the curve says something no runner does; see the header)")
    return flagged


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", action="append", default=[])
    ap.add_argument("--floor", type=float, default=None,
                    help="show the curve with its local exponent held to this floor")
    args = ap.parse_args()
    report(pools=tuple(args.pool) or POOLS, floor=args.floor)


if __name__ == "__main__":
    main()
