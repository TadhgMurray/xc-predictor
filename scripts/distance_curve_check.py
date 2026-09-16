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

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DISTANCES = (600, 800, 1000, 1500, 1600, 2000, 3000, 3200, 5000, 6000, 8000, 10000)
POOLS = ("elem_m", "elem_f", "ms_m", "ms_f", "hs_m", "hs_f", "college_m", "college_f")
SANE = (1.04, 1.20)


def factors(pool, sport, distances=DISTANCES, floor=None, monotone=False):
    """{distance: multiplier} for a pool and sport from the artifact, the
    curve's local exponent optionally held to `floor` and made non-
    increasing (`monotone`) first, as the fitter now does."""
    import normalize_distance as nd
    entry = nd._distancePotentialEntry(pool, sport)
    if floor:
        from distance_shape import floorLocalExponent
        vals, _n = floorLocalExponent(entry["knots"], entry["values"], floor)
        entry = dict(entry, values=vals)
    if monotone:
        from distance_shape import monotoneLocalExponent
        vals, _c = monotoneLocalExponent(entry["knots"], entry["values"])
        entry = dict(entry, values=vals)
    base = (pool or "").split("|")[0]
    target = (nd._SPLINES.get("pool_targets", {}).get(base) or entry.get("target")
              or nd._SPLINES["target"])
    g_t = nd._evalDistancePotential(entry, math.log(target))
    return {d: math.exp(g_t - nd._evalDistancePotential(entry, math.log(d)))
            for d in distances}, float(target)


# ★ AND THE ONE CURVE NOBODY CAN ARGUE WITH (owner, 2026-09-16: "how can
#   we best fit a clean line?"). The world records ARE a time-vs-distance
#   curve for one (imaginary) athlete, so their implied local exponent is a
#   reference our fit can be read against -- measured, external, and with
#   no pairs, no calendar and no courses in it. Read off record_pace:
#
#       1500->3000  1.088      3000->5000  1.069      5000->10000  1.056
#
#   monotonically falling, which is the shape the fitter's smoother
#   imposes. It is NOT a target to fit to: a record holder fades less than
#   a ninth grader, so a real pool's exponent should sit a little ABOVE
#   this line, not on it. What it does say is which way is up -- a pool
#   whose exponent falls BELOW the record curve at the long end, or rises
#   where this falls, is describing the calendar and the course rather than
#   the runner.
def recordSegments(gender, distances=DISTANCES):
    """[(d1, d2, exponent)] implied by the world records for one sex."""
    from record_pace import recordPace
    sex = "F" if str(gender or "").upper().startswith("F") else "M"

    def t(d):
        return recordPace(d, sex) * d / 1000.0

    return [(a, b, math.log(t(b) / t(a)) / math.log(b / a))
            for a, b in zip(distances, distances[1:])]


def loadOffsets(npz):
    """{(pool, distance bucket, band): log offset} from a solve file's
    dist_offset / dist_labels ('hs_m:3200:b1'; an unbanded label has no
    band and lands in band 0), and the number of bands. Empty without the
    block. The RATING subtracts the offset from log normalized time
    (joint_golive: adjusted = norm / exp(eff)), so the effective
    multiplier at a distance is the spline's times exp(-offset)."""
    if npz is None or "dist_offset" not in npz or "dist_labels" not in npz:
        return {}, 0
    e = [float(x) for x in np.asarray(npz["dist_offset"]).reshape(-1)]
    labels = [str(x) for x in np.asarray(npz["dist_labels"]).reshape(-1)]
    out, n_band = {}, 1
    for lab, val in zip(labels, e):
        parts = lab.split(":")
        pool, dist = parts[0], int(float(parts[1]))
        band = int(parts[2][1:]) if len(parts) > 2 and parts[2].startswith("b") else 0
        n_band = max(n_band, band + 1)
        out[(pool, dist, band)] = val
    return out, n_band


def effective(f, pool, offsets, band, distances=DISTANCES):
    """The spline's multipliers with the solve's offsets applied for one
    pool and band: a distance the solve keyed (its 100 m bucket) gets
    exp(-offset); the reference event and unkeyed distances keep the
    spline's own value."""
    out = {}
    for d in distances:
        bucket = int(round(d / 100.0) * 100)
        off = offsets.get((pool, bucket, band), 0.0)
        out[d] = f[d] * math.exp(-off)
    return out


def segments(f, distances=DISTANCES):
    """[(d1, d2, exponent)] between consecutive distances: t2/t1 = (d2/d1)^k."""
    out = []
    for a, b in zip(distances, distances[1:]):
        out.append((a, b, math.log(f[a] / f[b]) / math.log(b / a)))
    return out


BAND_ANCHORS = (90, 112, 127, 145)          # joint_solve.DIST_BAND_ANCHORS


def _ok(k, sane):
    return sane[0] - 1e-6 <= k <= sane[1] + 1e-6


def _segLine(segs, sane):
    return "  ".join(f"{a}->{b}: {k:.3f}{'!' if not _ok(k, sane) else ' '}"
                     for a, b, k in segs)


def report(pools=POOLS, sports=("TF", "XC"), floor=None, sane=SANE, out=print,
           offsets=None, n_band=0, monotone=(), records=False):
    """The spline's exponents per pool and sport; with `offsets` (loadOffsets)
    also the EFFECTIVE exponents per rating band on the track -- the
    spline and the solve's fitted event offsets together, which is what a
    rating actually applies. Returns the number of flagged segments."""
    flagged = 0
    offsets = offsets or {}
    for pool in pools:
        for sport in sports:
            try:
                f, target = factors(pool, sport, floor=floor, monotone=sport in monotone)
            except Exception as exc:                          # noqa: BLE001
                out(f"{pool}|{sport}: no curve ({type(exc).__name__}: {exc})")
                continue
            segs = segments(f)
            bad = [s for s in segs if not _ok(s[2], sane)]
            flagged += len(bad)
            out(f"\n{pool}|{sport}  (normalised to {target:.0f} m)"
                + (f"  [floor {floor:g} applied]" if floor else "")
                + ("  [non-increasing exponent applied]" if sport in monotone else ""))
            out("   spline    " + _segLine(segs, sane))
            if records:
                out("   records   "
                    + _segLine(recordSegments(pool.rsplit("_", 1)[-1]), sane)
                    + "   <- the world records' own exponent (recordSegments)")
            if sport == "TF" and offsets and any(k[0] == pool for k in offsets):
                for b in range(max(n_band, 1)):
                    fe = effective(f, pool, offsets, b)
                    segs_b = segments(fe)
                    flagged += sum(1 for s_ in segs_b if not _ok(s_[2], sane))
                    tag = (f"band {b} (~{BAND_ANCHORS[b]})" if b < len(BAND_ANCHORS)
                           else f"band {b}")
                    out(f"   {tag:<10}" + _segLine(segs_b, sane)
                        + "   <- spline + the solve's offsets: what a rating applies")
    out(f"\n{flagged} segment(s) outside [{sane[0]:.2f}, {sane[1]:.2f}] "
        "(! = the curve says something no runner does; see the header)")
    return flagged


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", action="append", default=[])
    ap.add_argument("--floor", type=float, default=None,
                    help="show the curve with its local exponent held to this floor")
    ap.add_argument("--monotone", default="",
                    help="sports (e.g. TF) shown with the local exponent made non-increasing, "
                         "as the fitter now stores them")
    ap.add_argument("--records", action="store_true",
                    help="print the world records' own implied exponent beside each curve "
                         "(recordSegments): the external reference, not a target")
    ap.add_argument("--npz", default=None,
                    help="a solve file (engine/data/joint_difficulty.npz by default when "
                         "it exists): its fitted event offsets are laid on the spline and "
                         "the EFFECTIVE exponent per rating band is printed too")
    args = ap.parse_args()
    offsets, n_band = {}, 0
    path = args.npz
    if path is None:
        import run_joint as rj
        path = rj.buildParser().get_default("out")
    if path and os.path.exists(path):
        with np.load(path, allow_pickle=False) as z:
            offsets, n_band = loadOffsets({k: z[k] for k in ("dist_offset", "dist_labels")
                                           if k in z.files})
        print(f"(event offsets from {path}: {len(offsets):,} (pool, distance, band) "
              f"cells, {n_band} bands)" if offsets else
              f"({path}: no event offsets in the file; spline only)")
    report(pools=tuple(args.pool) or POOLS, floor=args.floor, offsets=offsets, n_band=n_band,
           monotone=tuple(x.strip() for x in args.monotone.split(",") if x.strip()),
           records=args.records)


if __name__ == "__main__":
    main()
