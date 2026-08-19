"""
ability_slope.py -- is a course's penalty the SAME for everyone?

THE MODEL ASSUMES IT IS
    ln(norm) = alpha[athlete, season] + delta[cell] + eps

    delta multiplies every runner's time by the same factor. Measured at two
    courses, that is false, and the failure is monotone:

        Steens Mountain   rating  74-80  ratio 1.609   rating_diff  -8.48
                          rating 140+    ratio 1.361   rating_diff  +7.87
        Hydrangea Ranch   rating  80-90  ratio 1.116   rating_diff  -1.13
                          rating 130-139 ratio 1.034   rating_diff  +6.78

    A single scalar fits the middle and misses both ends. Elite runners rate ~7-8
    points too HIGH at a hard course; slow runners ~8 points too LOW.

★ THE QUESTION THIS ANSWERS. Is that per-course, or does the tilt scale with
  DIFFICULTY? Steens spans ~16 rating points and has delta 0.45; Hydrangea spans
  ~8 and has delta 0.12 -- roughly proportional. If that holds corpus-wide then

      delta_effective(cell, athlete) = delta[cell] * h(rating)

  is ONE global function h, not 81,112 per-cell parameters. That is a small,
  fittable correction rather than a rebuild.

★ WHY THIS ALSO ANSWERS "WHICH TOP X% IS BEST". If h is flat, the population you
  fit on does not matter and any x works. If h slopes, then NO single x is
  right -- Slaney's top 25% measures h at the fast end, all-comers measures its
  average, and the two differ BY CONSTRUCTION rather than by error. Picking x
  then means choosing which runner the difficulty is "for".

Reads the pack and pair_difficulty.npz. Measures only.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, "scripts")
for _p in (_HERE, _ROOT,
           os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

import pair_engine as pe
import pair_ratings as pr
import pair_validate as pv


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE RESIDUAL
# ------------------------------------------------------------------ #

def residuals(D):
    """
    Per row: what the model failed to explain.

        resid = y - alpha[group] - h * delta[cell]

    ★ h MUST BE INCLUDED OR THIS MEASURES THE WRONG MODEL. An earlier version
      omitted it and re-measured the ORIGINAL tilt on a tilted solve -- it
      reported -0.0262 against the fitted -0.031 and looked like a failure to
      correct, when in fact it had never read the corrected fit.

    ★ IF THE SCALAR MODEL WERE RIGHT, E[resid | anything] = 0. It is zero by
      construction when averaged over a whole cell or a whole athlete-season --
      that is what least squares guarantees. It is NOT constrained to be zero
      within a (cell-difficulty, athlete-ability) slice, which is exactly why
      that slice can expose the assumption.
    """
    alpha, _ = pv.refitAlpha(D["y"], D["delta"], D["course"], D["group"],
                             D["n_groups"], h=D.get("h"))
    eff = D["delta"][D["course"]]
    if D.get("h") is not None:
        eff = D["h"] * eff
    return D["y"] - alpha[D["group"]] - eff, alpha


def ratingPerRow(D, alpha):
    """
    Each row's athlete rating, on the 100-centred scale.

    Derived from alpha rather than read from athlete_ratings so it matches the
    solve exactly -- a rating from a previous run would be measuring a different
    ability estimate than the residual was computed against.
    """
    attrs = pr.groupAttributes(D["group"], D["athlete"], D["year"],
                               D["n_groups"],
                               pr.poolPerAthlete(D["cols"]["athlete_keys"]))
    rat = pr.buildRatings(alpha, attrs)
    return rat["career"][D["group"]], attrs, rat


# ------------------------------------------------------------------ #
# CHUNK 2 -- THE TWO-WAY TABLE
# ------------------------------------------------------------------ #

def table(resid, rating, delta_row, weights_ok, n_dbuckets=5):
    """
    ★ THE MEASUREMENT. Mean residual by (cell difficulty, athlete rating).

    Rows are difficulty quintiles, columns are rating bands. Read ACROSS a row:
    if the residual tilts with rating, the penalty is ability-dependent. Then
    compare rows: if the tilt STEEPENS with difficulty, it scales with delta and
    one global h fixes every course.
    """
    ok = weights_ok & np.isfinite(rating) & np.isfinite(resid)
    d, r, e = delta_row[ok], rating[ok], resid[ok]

    edges = np.percentile(d, np.linspace(0, 100, n_dbuckets + 1))
    bands = [(0, 90), (90, 100), (100, 110), (110, 120), (120, 130), (130, 200)]

    print("\n[slope] mean residual by cell difficulty x athlete rating")
    print("        (positive = ran SLOWER than the model expected)")
    header = "    difficulty      n    " + "".join(
        f"{lo}-{hi}".rjust(10) for lo, hi in bands)
    print(header)

    slopes = []
    for i in range(n_dbuckets):
        lo_d, hi_d = edges[i], edges[i + 1]
        m = (d >= lo_d) & (d < hi_d if i < n_dbuckets - 1 else d <= hi_d)
        if m.sum() < 1000:
            continue
        cells = []
        xs, ys = [], []
        for lo, hi in bands:
            mm = m & (r >= lo) & (r < hi)
            if mm.sum() < 200:
                cells.append("       ---")
                continue
            v = float(e[mm].mean())
            cells.append(f"{v:+10.5f}")
            xs.append((lo + hi) / 2.0)
            ys.append(v)
        # slope per 10 rating points, so the number is readable
        slope = (np.polyfit(xs, ys, 1)[0] * 10.0) if len(xs) >= 3 else np.nan
        slopes.append((float(np.median(d[m])), slope, int(m.sum())))
        print(f"    {lo_d:+.3f}..{hi_d:+.3f} {int(m.sum()):>8,}"
              + "".join(cells) + f"   slope {slope:+.5f}")

    print("\n[slope] tilt vs difficulty  (does the tilt scale with delta?)")
    print("    median_delta   rows        slope/10pts")
    for md, sl, n in slopes:
        print(f"    {md:>+12.4f} {n:>10,} {sl:>+18.5f}")

    if len(slopes) >= 3:
        md = np.array([s[0] for s in slopes])
        sl = np.array([s[1] for s in slopes])
        good = np.isfinite(sl)
        if good.sum() >= 3:
            r_ = float(np.corrcoef(md[good], sl[good])[0, 1])
            print(f"\n    corr(difficulty, tilt) = {r_:+.3f}")
            if abs(r_) > 0.8:
                k = float(np.polyfit(md[good], sl[good], 1)[0])
                print(f"    ★ tilt SCALES with difficulty: {k:+.5f} per unit "
                      f"delta per 10 rating points.")
                print(f"      One global h would fix every course:")
                print(f"          delta_eff = delta * (1 {k:+.5f}/1 * "
                      f"(rating - 100)/10)")
            else:
                print("    tilt does NOT track difficulty -- it is per-course, "
                      "not a global\n      function of delta, and no single "
                      "correction fixes it.")
    return slopes


# ------------------------------------------------------------------ #
# CHUNK 3 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(pack_path, npz_path, tilt=False):
    import pair_all as pa

    D = pa.prepare(pack_path)
    pa.solve(D)                          # cached; free on a re-run

    # ★ TO CHECK A TILTED SOLVE, BUILD h AND RE-SOLVE. pa.solve() with no h
    #   loads the untilted cache entry no matter what pair_all --tilt wrote,
    #   because the two are separate cache keys. Reading pair_difficulty.npz
    #   does not help either -- that file holds delta, not the h it was fitted
    #   with.
    if tilt:
        pa.shrink(D)
        pa.ratings(D, quiet=True)
        D["h"] = pa.abilityTilt(D)
        print(f"[slope] measuring the TILTED solve (h median "
              f"{float(np.median(D['h'])):.3f})")
        pa.solve(D, h=D["h"])

    with np.load(npz_path, allow_pickle=False) as f:
        solved = f["solved"].astype(bool)
        degree = f["degree"]

    resid, alpha = residuals(D)
    rating, attrs, rat = ratingPerRow(D, alpha)

    # Only cells with enough athlete-seasons to have a trustworthy delta --
    # a noisy delta produces a fake tilt purely from its own error.
    ok_cell = solved & (degree >= 25)
    weights_ok = ok_cell[D["course"]]
    print(f"[slope] {int(weights_ok.sum()):,} rows in cells with degree >= 25")

    table(resid, rating, D["delta"][D["course"]], weights_ok)


if __name__ == "__main__":
    here = os.path.join(_HERE, "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else os.path.join(here, "packed_XC_TF.npz"),
         args[1] if len(args) > 1
         else os.path.join(here, "pair_difficulty.npz"),
         tilt="--tilt" in sys.argv)