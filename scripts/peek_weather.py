# scripts/peek_weather.py
#
# Purpose : print what weather_correction_<sport>.pkl actually holds -- betas,
#           spline knots, and the per-course soil sensitivity DISTRIBUTION.
# Why     : with this pickle live, the XC fastest floor read 6.81s; with it
#           renamed .OFF, 714.86s. Same 39,288,362 rows. It divides by ~143,
#           i.e. exp(4.96). 3.4 says beta_soil ~= 0.16 and that raw per-course
#           s_c ran p05/p95 = -24/+31. 0.16 * 31 = 4.96. This tells us whether
#           the [0,1] clip ("allow MUTING, forbid AMPLIFYING") is in this file.
# Output  : a census. Read-only, no DB, no numpy needed.

import argparse
import pickle


# ---------------------------------------------------------------- loading
def _load(path):
    # Purpose   : read the artifact.
    # Note      : "rb" -- pickles are binary. No DB, no engine import: this must
    #             be runnable against a .OFF file that nothing else will touch.
    with open(path, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------- stats
def _percentile(sorted_vals, q):
    # Purpose   : the q-th percentile of an ALREADY-SORTED list.
    # Arguments : sorted_vals -- ascending floats. q -- 0.0..1.0.
    # Output    : float, or None if empty.
    # Note      : nearest-rank, no numpy. int(q * (n-1)) indexes directly; we
    #             are sizing a blow-up, not doing statistics.
    if not sorted_vals:
        return None
    return sorted_vals[int(q * (len(sorted_vals) - 1))]


def _soilValues(art):
    # Purpose   : pull every course's s_c out of the artifact.
    # Output    : a sorted list of floats.
    # Note      : soil_sensitivity is {course: {"s": float, "n": int}}. We want
    #             the s values only; n is the evidence count behind each.
    smap = art.get("soil_sensitivity") or {}
    return sorted(float(v["s"]) for v in smap.values() if "s" in v)


def _outsideUnit(sorted_vals):
    # Purpose   : how many s_c values fall outside the [0,1] clip 3.4 prescribes.
    # Output    : (n_below, n_above).
    # Why       : "A course can reliably show it DOESN'T respond to mud... but
    #             'which grass course is muddiest' IS NOISE. Allow MUTING,
    #             forbid AMPLIFYING." Anything above 1.0 is amplification.
    below = sum(1 for v in sorted_vals if v < 0.0)
    above = sum(1 for v in sorted_vals if v > 1.0)
    return below, above


# ---------------------------------------------------------------- reporting
def _reportScalars(art):
    print("REFERENCE (the no-op point):", art.get("reference"))
    print("linear features used       :", art.get("linear_features_used"))
    print("dist_ref                   :", art.get("dist_ref"))
    print("\nBETAS (linear terms):")
    for feat, b in sorted((art.get("betas") or {}).items()):
        print(f"  {feat:<16} {b:>12.6f}")
    print("\nDIST_BETAS (distance interaction slopes):")
    for feat, b in sorted((art.get("dist_betas") or {}).items()):
        print(f"  {feat:<16} {b:>12.6f}")


def _reportSplines(art):
    print("\nSPLINES:")
    for feat, sp in sorted((art.get("splines") or {}).items()):
        knots = sp.get("knots")
        coef = sp.get("coef")
        print(f"  {feat}: ref={sp.get('ref')}")
        print(f"    knots {knots}")
        print(f"    coef  {coef}")


def _reportSoil(art):
    # THE MAIN EVENT. If p95 is ~31 rather than ~1, the [0,1] clip is not in
    # this artifact, and beta_soil * s_c is the divide-by-143.
    vals = _soilValues(art)
    print(f"\nSOIL SENSITIVITY s_c  ({len(vals):,} courses)")
    if not vals:
        print("  (none -- artifact carries no per-course map)")
        return
    for label, q in (("min", 0.0), ("p05", 0.05), ("p50", 0.50),
                     ("p95", 0.95), ("max", 1.0)):
        print(f"  {label:<4} {_percentile(vals, q):>12.4f}")

    below, above = _outsideUnit(vals)
    print(f"\n  outside the [0,1] clip: {below:,} below 0   {above:,} above 1")
    if above:
        beta = (art.get("betas") or {}).get("soil")
        worst = vals[-1]
        if beta:
            term = beta * worst
            print(f"  !! worst term = beta_soil({beta:.4f}) * s_c({worst:.2f})"
                  f" = {term:.3f}  -> wmult = exp(term) = {2.718281828 ** term:,.1f}x")
        print("  !! 3.4: clip s_c to [0,1]. ALLOW MUTING, FORBID AMPLIFYING.")


def main():
    ap = argparse.ArgumentParser(description="Census a weather artifact.")
    ap.add_argument("--path",
                    default="engine/data/weather_correction_XC.pkl.OFF")
    args = ap.parse_args()

    art = _load(args.path)
    print(f"=== {args.path} ===")
    print("note:", art.get("note"), "\n")
    _reportScalars(art)
    _reportSplines(art)
    _reportSoil(art)


if __name__ == "__main__":
    main()