"""
distance_bake.py -- fold the measured per-pool distance correction into
distance_spline.pkl.

THE MEASUREMENT THIS APPLIES
    distance_fix_by_pool.py compared the SAME venue raced at two distances by
    the SAME pool, so terrain cancels exactly and only that pool's distance
    normalization error is left. The curves validated by cross-pool agreement,
    not by variance explained:

        hs_m vs hs_f   r = 0.970  (9 shared distances)
        ms_m vs ms_f   r = 0.931  (7 shared distances)

    Independent populations, independent solves, same curve.

THE ARITHMETIC
    The artifact normalizes by  T_5k = T_d * exp(g(log target) - g(log d)).
    We want the corrected time to be the old one times exp(-phi(d)). Setting
    g'(x) = g(x) + phi(x) gives

        g'(target) - g'(d) = g(target) + phi(target) - g(d) - phi(d)

    and phi(5000) = 0 by choice of anchor while target IS 5000, so the target
    term cancels and the result is exactly old * exp(-phi(d)).

★ WHAT THIS DOES AND DOES NOT CHANGE. Cells are keyed (canonical_id, distance),
  so every row in a cell shares one distance and a distance shift is absorbed by
  that cell's difficulty. Measured directly: applying the correction moved
  held-out prediction error by +0.01%. So RATINGS DO NOT MOVE. What moves is the
  meaning of the difficulty number -- it stops reading "terrain plus distance
  artifact" and starts reading "terrain". Woodbridge goes from -0.0295 to about
  -0.039.

★ XC ONLY, AND ONLY THE FOUR MEASURED POOLS. phi was measured on XC cells, so
  only "<pool>|XC" entries are touched. college_m vs college_f came back at
  r = -0.975 on four shared distances with every value inside +-0.018 -- noise
  finding a spurious anti-correlation -- and elem produced no identifiable pairs
  at all. Those keep the existing potential.

★ NO EXTRAPOLATION. phi is held flat outside the measured distances rather than
  extended. Extrapolating a distance curve past its data is exactly what
  produced the d1600 cells at +1.6 in the first place.

Backs up the original before writing. Dry run by default.
"""

import math
import os
import pickle
import shutil
import sys

import numpy as np

_TRUSTED = ("hs_m", "hs_f", "ms_m", "ms_f")

# ⚠ DISTANCES DROPPED FROM THE CURVE BEFORE BAKING.
#   4700 is Mt. SAC's distance and essentially nothing else -- 417 cells, and it
#   appeared in five of the top six worst-fitting pairs in the pooled solve.
#   Keeping it puts a 1-2% spike between 4100 m and 4790 m that is one venue's
#   TERRAIN leaking into the distance curve: phi runs +0.0079 at 4000, +0.0208
#   at 4700, then back to +0.0093 at 4800. A real distance effect does not do
#   that. Dropped, so 4000 -> 4800 interpolates smoothly.
#
#   Override with --keep=4700 or extend with --drop=6000,4700.
_DROP_DEFAULT = (4700,)


# ------------------------------------------------------------------ #
# CHUNK 1 -- INPUTS
# ------------------------------------------------------------------ #

def loadCurves(path, trusted=_TRUSTED, drop=()):
    """
    {pool: {distance: phi}} from distance_fix_by_pool.npz, minus `drop`.

    A dropped distance is removed BEFORE interpolation, so the curve bridges
    across it rather than through it -- which is the point: a bad knot pulls
    every interpolated value on both sides toward itself.
    """
    curves = {}
    drop = set(int(d) for d in drop)
    with np.load(path, allow_pickle=False) as f:
        for pool in trusted:
            dk, pk = f"{pool}__distances", f"{pool}__phi"
            if dk in f.files and pk in f.files:
                curves[pool] = {int(d): float(p)
                                for d, p in zip(f[dk], f[pk])
                                if int(d) not in drop}
    if drop:
        print(f"[bake] dropped distances: {sorted(drop)}")
    return curves


def loadArtifact(path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


# ------------------------------------------------------------------ #
# CHUNK 2 -- PHI AT AN ARBITRARY DISTANCE
# ------------------------------------------------------------------ #

def phiAt(curve, distance):
    """
    phi at any distance: linear in LOG distance between measured points, held
    FLAT outside them.

    ★ Log-space because the potential itself is a function of log-distance, so
      interpolating linearly in metres would put the correction in a different
      space from the curve it is being added to.

    ★ Flat outside, never extrapolated. The measured distances are the race
      conventions (mile, 1.5mi, 2mi, 3000, 4000, 3mi, 5k, 6k, 8k, 10k). Beyond
      them there is no evidence, and a distance curve extended past its data is
      what produced the d1600 cells at +1.6.
    """
    ds = sorted(curve)
    if not ds:
        return 0.0
    if distance <= ds[0]:
        return curve[ds[0]]
    if distance >= ds[-1]:
        return curve[ds[-1]]

    ld = math.log(distance)
    for lo, hi in zip(ds, ds[1:]):
        if lo <= distance <= hi:
            t = (ld - math.log(lo)) / (math.log(hi) - math.log(lo))
            return curve[lo] * (1 - t) + curve[hi] * t
    return 0.0


# ------------------------------------------------------------------ #
# CHUNK 3 -- BAKE
# ------------------------------------------------------------------ #

def bakeEntry(entry, curve):
    """
    Add phi to a potential entry, INSERTING A KNOT AT EACH MEASURED DISTANCE.

    ★ THE KNOT INSERTION IS NOT OPTIONAL. phi has structure BETWEEN the
      artifact's existing knots -- phi(2400) = +0.0194 sits between knots at
      2000 and 3000 where phi is +0.0144 and +0.0042. Adding phi only at the
      existing knots loses that peak entirely: an end-to-end check gave a ratio
      of 0.99025 where exp(-phi) demanded 0.98079. Inserting a knot at every
      measured distance makes the artifact able to represent the curve exactly,
      and the linear interpolation between knots then matches phiAt by
      construction.

    A new knot's base value is the ORIGINAL g evaluated there, so inserting it
    changes nothing on its own -- the curve through the new point is identical
    until phi is added.
    """
    knots = list(entry["knots"])
    values = list(entry["values"])

    for d in sorted(curve):
        ld = math.log(d)
        if any(abs(ld - k) < 1e-9 for k in knots):
            continue
        g = _eval(entry, ld)                 # original curve, unchanged there
        pos = 0
        while pos < len(knots) and knots[pos] < ld:
            pos += 1
        knots.insert(pos, ld)
        values.insert(pos, g)

    applied = []
    out = []
    for k, v in zip(knots, values):
        d = math.exp(k)
        p = phiAt(curve, d)
        out.append(v + p)
        applied.append((d, p))
    return {"knots": knots, "values": out}, applied


def bakeArtifact(art, curves, sport="XC"):
    """
    Apply every trusted curve to its "<pool>|SPORT" entry.

    A pool with no matching entry is reported and skipped rather than created:
    inventing a curve for a pool the artifact does not carry would change which
    fallback that pool resolves to, which is a different change from correcting
    an existing curve.
    """
    pools = art.get("pools", {})
    report = []
    for pool, curve in curves.items():
        key = f"{pool}|{sport}"
        entry = pools.get(key)
        if entry is None:
            report.append((key, None, "no entry in artifact -- skipped"))
            continue
        new_entry, applied = bakeEntry(entry, curve)
        pools[key] = new_entry
        report.append((key, applied, None))
    art["pools"] = pools
    return art, report


# ------------------------------------------------------------------ #
# CHUNK 4 -- REPORT
# ------------------------------------------------------------------ #

def reportBake(report, curves):
    print("\n[bake] ---- correction applied per pool ----")
    for key, applied, note in report:
        if note:
            print(f"    {key:<14} {note}")
            continue
        nz = [(d, p) for d, p in applied if abs(p) > 1e-9]
        print(f"    {key:<14} {len(applied)} knots, {len(nz)} moved")
        for d, p in applied:
            mult = math.exp(-p)
            print(f"      {d:>8.0f} m   phi {p:+.4f}   "
                  f"time x {mult:.4f}")
    print("[bake] ------------------------------------")

    print("\n[bake] measured curves, for reference:")
    for pool in sorted(curves):
        pairs = "  ".join(f"{d}:{p:+.4f}" for d, p in sorted(curves[pool].items()))
        print(f"    {pool:<8} {pairs}")


def sanityCheck(baked, original, curves, sport="XC"):
    """
    ★ THE ARITHMETIC CHECK, against the ORIGINAL artifact.

    ⚠ An earlier version compared the baked entry against ITSELF with phi
      subtracted back off. That is circular -- it verifies that subtracting phi
      undoes adding phi, which is true by construction, and it passed while the
      real normalization at 2400 m was wrong by 1%. The comparison has to be
      against the untouched curve.
    """
    print("\n[bake] verifying against the ORIGINAL artifact:")
    target = baked.get("target", 5000)
    ok = True
    for pool, curve in curves.items():
        key = f"{pool}|{sport}"
        new_e = baked["pools"].get(key)
        old_e = original["pools"].get(key)
        if new_e is None or old_e is None:
            continue
        worst = 0.0
        for d, p in sorted(curve.items()):
            new = math.exp(_eval(new_e, math.log(target))
                           - _eval(new_e, math.log(d)))
            old = math.exp(_eval(old_e, math.log(target))
                           - _eval(old_e, math.log(d)))
            ratio, want = new / old, math.exp(-p)
            worst = max(worst, abs(ratio - want))
            if abs(ratio - want) > 1e-9:
                print(f"    MISMATCH {key} {d}m: {ratio:.6f} vs {want:.6f}")
                ok = False
        print(f"    {key:<14} max error {worst:.2e}")
    print("    all distances match" if ok else "    ⚠ MISMATCH -- do not write")
    return ok


def _eval(entry, ld):
    """Mirror of normalize_distance._evalDistancePotential."""
    k, v = entry["knots"], entry["values"]
    if ld <= k[0]:
        slope = (v[1] - v[0]) / (k[1] - k[0])
        return v[0] + slope * (ld - k[0])
    if ld >= k[-1]:
        slope = (v[-1] - v[-2]) / (k[-1] - k[-2])
        return v[-1] + slope * (ld - k[-1])
    lo, hi = 0, len(k) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if k[mid] <= ld:
            lo = mid
        else:
            hi = mid
    t = (ld - k[lo]) / (k[hi] - k[lo])
    return v[lo] * (1 - t) + v[hi] * t


# ------------------------------------------------------------------ #
# CHUNK 5 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(spline_path, curve_path, write=False, drop=_DROP_DEFAULT):
    art = loadArtifact(spline_path)
    if art.get("kind") != "distance_potential":
        print(f"[bake] {spline_path} is not a distance_potential artifact "
              f"(kind={art.get('kind')!r}) -- refusing to touch it")
        return
    curves = loadCurves(curve_path, drop=drop)
    if not curves:
        print(f"[bake] no trusted curves in {curve_path}")
        return
    print(f"[bake] loaded curves for {', '.join(sorted(curves))}")
    print(f"[bake] artifact target = {art.get('target')} m, "
          f"{len(art.get('pools', {})):,} pool entries")

    original = loadArtifact(spline_path)     # a second, untouched copy
    art, report = bakeArtifact(art, curves)
    reportBake(report, curves)
    if not sanityCheck(art, original, curves):
        return

    if not write:
        print("\n[bake] DRY RUN -- pass --write to save. Backup is taken then.")
        return

    backup = spline_path + ".prebake"
    if not os.path.exists(backup):
        shutil.copy2(spline_path, backup)
        print(f"\n[bake] backed up original to {backup}")
    else:
        print(f"\n[bake] {backup} already exists -- kept (it predates this run)")

    with open(spline_path, "wb") as fh:
        pickle.dump(art, fh)
    print(f"[bake] wrote {spline_path}")
    print("\n[bake] NEXT: re-normalize, then repack, then re-run the engine.")
    print("       Ratings will NOT move -- difficulty absorbs a per-cell shift.")
    print("       The difficulty TABLE is what changes.")


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    drop = set(_DROP_DEFAULT)
    for a in sys.argv[1:]:
        if a.startswith("--drop="):
            drop |= {int(x) for x in a.split("=", 1)[1].split(",") if x}
        if a.startswith("--keep="):
            drop -= {int(x) for x in a.split("=", 1)[1].split(",") if x}

    main(args[0] if args else os.path.join(here, "distance_spline.pkl"),
         args[1] if len(args) > 1
         else os.path.join(here, "distance_fix_by_pool.npz"),
         write="--write" in sys.argv, drop=sorted(drop))