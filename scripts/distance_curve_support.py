#!/usr/bin/env python3
"""
distance_curve_support.py -- where the distance curve is EXTRAPOLATING, per pool.

    scripts/distance_curve_support.py
    scripts/distance_curve_support.py --show-exponents

Run from the PROJECT ROOT. Reads engine/data/distance_spline.pkl only.
NO DATABASE, no network -- safe to run while a scrape is going.

★ WHY (owner, 2026-09-18): "It seems that running a diff distance than usual
  just rlly fucks up the model or something (like a 9:00 3200m runner turning
  into a 31:00 8k runner for first collegiate race? or a 5k for college
  athletes being way off?) ... we need to have a session going over the
  distance curve fitter."

★ AND THE FITTER ALREADY KNOWS. engine/fit_distance_exponent.py:110 records its
  own blind spot: "a linear extrapolation across log(5000/3200) = 0.446. Not a
  subtle tail effect: 100% of ms rows, extrapolated." scripts/
  distance_curve_check.py found college_m|XC 8000->10000 at a local exponent of
  0.92 -- under 1.0, which means the curve says a runner's PACE improves as the
  race gets longer. No runner does that.

  So this is not another curve printer. Every fitted entry carries `span`, the
  metre range its PAIRS actually covered, and `n_matched`, how many pairs fed
  it. Outside that span the curve is the boundary slope continued -- a choice,
  not a measurement. This lists, per pool:

      span          where the data was
      target        the distance every row in the pool is converted TO
      target in?    whether that target is even inside the support
      n_matched     how many same-athlete pairs the curve rests on
      exponent      the local exponent at the span's edges and at the target

⚠⚠ AND THE HEADLINE IS THE ENDS OF A POOL'S OWN RANGE, NOT EXTRAPOLATION
   PAST IT (owner, 2026-09-18, correcting me): "the issue isn't that the
   extrapolation is fine the issue is for rows with actual things like college
   tf distances the extremes (800/600/10k) are off."

   Right, and it is a sharper complaint. 600m, 800m and 10,000m are ordinary
   college track distances with plenty of rows -- they are INSIDE the support.
   The curve is wrong there anyway, and the reason is structural: an end
   segment's slope is set by the boundary, has data on one side only, and is
   where the knot grid is thinnest. So a spline that is well behaved through
   the middle can bend hard at both ends and nothing in the middle notices.

   That is why the FIRST and LAST segments are printed first, per pool, with
   their exponents -- those are the segments doing the damage the owner sees.

! AN EXPONENT UNDER 1.0 IS IMPOSSIBLE and over ~1.20 is a fit artefact, per
  distance_curve_check's reasoning (Riegel is 1.06-1.10 for a trained runner).
  Both are flagged.
"""
import argparse
import math
import os
import pickle
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ARTIFACT = os.path.join(_ROOT, "engine", "data", "distance_spline.pkl")

# The physical bounds, from scripts/distance_curve_check.py's reasoning.
EXP_MIN, EXP_MAX = 1.0, 1.20


def localExponent(entry, metres):
    """The curve's local exponent at a distance: the slope of g against
    log-distance, which IS the exponent k in t2/t1 = (d2/d1)^k. None when the
    entry has no knots there."""
    k, v = entry.get("knots") or [], entry.get("values") or []
    if len(k) < 2:
        return None
    ld = math.log(max(metres, 1.0))
    # the segment containing ld, clamped to the ends (which is exactly what
    # the consumer does, and exactly why the ends matter)
    for i in range(len(k) - 1):
        if k[i] <= ld <= k[i + 1] or (i == 0 and ld < k[0]) \
                or (i == len(k) - 2 and ld > k[-1]):
            dk = k[i + 1] - k[i]
            if dk <= 0:
                return None
            return (v[i + 1] - v[i]) / dk
    return None


def flag(exp):
    if exp is None:
        return "  ?"
    if exp < EXP_MIN:
        return "  IMPOSSIBLE (<1.0: pace improving with distance)"
    if exp > EXP_MAX:
        return f"  suspect (>{EXP_MAX})"
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifact", default=ARTIFACT)
    ap.add_argument("--show-exponents", action="store_true",
                    help="also print the local exponent at every knot")
    args = ap.parse_args()

    if not os.path.exists(args.artifact):
        raise SystemExit(f"no artifact at {args.artifact} -- run "
                         f"engine/fit_distance_exponent.py first")
    with open(args.artifact, "rb") as fh:
        art = pickle.load(fh)

    targets = art.get("pool_targets") or {}
    pools = art.get("pools") or {}
    print(f"\n  artifact: {args.artifact}")
    print(f"  {len(pools)} pool curves + "
          f"{'a' if art.get('global') else 'NO'} global curve\n")

    # ★★ THE END SEGMENTS FIRST. An end segment has data on one side only and
    #    the thinnest knot spacing, so it bends where the middle cannot see --
    #    which is the owner's 600/800 and 10k complaint, on distances that are
    #    well inside the support and heavily raced.
    print(f"  === the END segments, per pool: where the curve bends unseen ===")
    print(f"  {'pool|sport':<22} {'shortest segment':>26} {'k':>7}"
          f"   {'longest segment':>26} {'k':>7}")
    print(f"  {'-' * 22} {'-' * 26} {'-' * 7}   {'-' * 26} {'-' * 7}")
    ends_bad = []
    for key in sorted(pools):
        e = pools[key] or {}
        k = e.get("knots") or []
        v = e.get("values") or []
        if len(k) < 2:
            print(f"  {key:<22} {'(no knots)':>26}")
            continue
        def seg(i):
            d0, d1 = math.exp(k[i]), math.exp(k[i + 1])
            x = (v[i + 1] - v[i]) / (k[i + 1] - k[i])
            return f"{d0:,.0f} -> {d1:,.0f}", x
        lab_lo, x_lo = seg(0)
        lab_hi, x_hi = seg(len(k) - 2)
        print(f"  {key:<22} {lab_lo:>26} {x_lo:7.3f}   "
              f"{lab_hi:>26} {x_hi:7.3f}")
        for lab, x, where in ((lab_lo, x_lo, "short end"),
                              (lab_hi, x_hi, "long end")):
            if x < EXP_MIN or x > EXP_MAX:
                ends_bad.append((key, where, lab, x))
    if ends_bad:
        print(f"\n  ⚠ {len(ends_bad)} END segment(s) with an unphysical "
              f"exponent -- these are the 600/800/10k cases:")
        for key, where, lab, x in ends_bad:
            print(f"      {key:<22} {where:<10} {lab:>22}  {x:6.3f}{flag(x)}")
    else:
        print(f"\n  every pool's end segments are within "
              f"[{EXP_MIN}, {EXP_MAX}].")

    print(f"\n  === support and target, per pool ===")
    print(f"  {'pool|sport':<22} {'span (m)':>17} {'target':>8} "
          f"{'in?':>4} {'pairs':>8}  exponent at target")

    print(f"  {'-' * 22} {'-' * 17} {'-' * 8} {'-' * 4} {'-' * 8}  {'-' * 30}")
    outside = []
    for key in sorted(pools):
        e = pools[key] or {}
        span = e.get("span") or (None, None)
        pool = key.split("|")[0]
        tgt = targets.get(pool)
        lo, hi = span
        in_span = (lo is not None and tgt is not None and lo <= tgt <= hi)
        exp_t = localExponent(e, tgt) if tgt else None
        span_s = (f"{lo:,.0f}..{hi:,.0f}" if lo is not None else "?")
        print(f"  {key:<22} {span_s:>17} "
              f"{(f'{tgt:,.0f}' if tgt else '?'):>8} "
              f"{('yes' if in_span else 'NO'):>4} "
              f"{e.get('n_matched', 0):>8,}  "
              f"{(f'{exp_t:.3f}' if exp_t is not None else '?')}{flag(exp_t)}")
        if not in_span:
            outside.append((key, span_s, tgt))

    # ★★ THE HEADLINE, SEPARATED OUT, because it is the one that explains a
    #    9:00 3200 becoming a 31:00 8k: not a tail, the whole pool.
    if outside:
        print(f"\n  ⚠ {len(outside)} pool curve(s) are asked to convert TO a "
              f"distance outside their own support.\n"
              f"    Every row in those pools is normalised by extrapolation, "
              f"not just the tails:")
        for key, span_s, tgt in outside:
            print(f"      {key:<22} support {span_s:>17} m, "
                  f"target {tgt:,.0f} m" if tgt else f"      {key}")
    else:
        print(f"\n  every pool's target is inside its own support.")

    bad = [(k, localExponent(pools[k], (pools[k].get('span') or (0, 0))[1]))
           for k in sorted(pools)]
    bad = [(k, x) for k, x in bad if x is not None and (x < EXP_MIN or x > EXP_MAX)]
    if bad:
        print(f"\n  ⚠ unphysical local exponent at the TOP of the support "
              f"(where every long race lands):")
        for k, x in bad:
            print(f"      {k:<22} {x:.3f}{flag(x)}")

    if args.show_exponents:
        for key in sorted(pools):
            e = pools[key] or {}
            k = e.get("knots") or []
            print(f"\n  {key}: local exponent by segment")
            for i in range(len(k) - 1):
                d0, d1 = math.exp(k[i]), math.exp(k[i + 1])
                x = (e["values"][i + 1] - e["values"][i]) / (k[i + 1] - k[i])
                print(f"    {d0:>8,.0f} -> {d1:>8,.0f}  {x:6.3f}{flag(x)}")

    print(f"\n  READ IT THIS WAY. The END SEGMENTS are the owner's complaint: "
          f"600m, 800m and\n  10,000m are ordinary college track distances "
          f"with plenty of rows, well inside the\n  support, and the curve "
          f"still bends at the ends because an end segment has data\n  on one "
          f"side only and the thinnest knots. An exponent under 1.0 is "
          f"impossible --\n  it says a runner's pace improves as the race "
          f"lengthens -- so it is the fit, not\n  the runners. `in? = NO` is a "
          f"second, separate fault: that pool's every row is\n  extrapolated, "
          f"not only its tails.")


if __name__ == "__main__":
    main()
