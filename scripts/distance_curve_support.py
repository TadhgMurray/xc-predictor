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
  metre range its PAIRS actually covered, and `n_edges`, how many distinct
  distance transitions fed
  it. Outside that span the curve is the boundary slope continued -- a choice,
  not a measurement. This lists, per pool:

      span          where the data was
      target        the distance every row in the pool is converted TO
      target in?    whether that target is even inside the support
      n_edges       distinct distance transitions the curve rests on. THIS
                    IS NOT THE PAIR COUNT AND n_matched IS NOT EITHER.
                    ! n_matched = len(_matchedContrasts(edges)) -- the stage-1
                      evidence for eps, the CALENDAR OFFSET, gated at
                      MIN_EPS_MATCHED_TRANSITIONS = 10. It is a small subset
                      of the transitions by construction, so reading it as
                      "how much data does this pool have" understates a fat
                      pool by orders of magnitude. It was printed under the
                      heading `pairs` here and led to exactly that misreading
                      (2026-09-19). Sample size is n_edges; eps confidence is
                      n_matched; they answer different questions.
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

⚠⚠ AND IT ALSO REPORTS WHETHER THE ARTIFACT IS STALE, which on 2026-09-18 was
   the whole answer. The fitter grew a floor on the local exponent
   (MIN_LOCAL_EXP = 1.04) and a monotone pass precisely to forbid a k under
   1.0, and it records both on every entry it writes: `min_local_exp`,
   `floored_segments`, `monotone`. The shipped pkl has NONE of those keys --
   its entries are coeffs, degree, eps, knots, n_matched, span, target and
   friends. So it was built by a fitter that predates the fix, and the
   impossible tails are not a live bug in the code: they are a file nobody
   rebuilt.

   That is the third instance of the same shape in one day:
   database.backfillMeetsTFVenueNames ("written, committed, wired to nothing"),
   school_team_link (built and read by nothing until 2026-09-17), and now this.
   So the staleness check is printed FIRST, before any exponent, because
   reading the numbers without it sends you to debug the fitter instead of
   re-running it.
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


# _supportedSegments
# Purpose:   The indices of the knot segments whose MIDPOINT lies inside the
#            entry's own fitted span -- the only segments that describe a
#            measurement rather than the extrapolation policy.
# Arguments: entry -- a pool curve with "knots" and "span".
# Output:    an ascending list of segment indices (possibly empty).
# ! MIDPOINT, NOT EITHER ENDPOINT. A segment straddling the span boundary is
#   half policy; judging it by its inner endpoint would admit it and by its
#   outer one would reject the last real segment of every pool.
def _supportedSegments(entry):
    k = entry.get("knots") or []
    span = entry.get("span") or (None, None)
    if len(k) < 2:
        return []
    lo, hi = span
    if lo is None or hi is None:
        return list(range(len(k) - 1))
    llo, lhi = math.log(lo), math.log(hi)
    out = [i for i in range(len(k) - 1)
           if llo <= 0.5 * (k[i] + k[i + 1]) <= lhi]
    # A span narrower than one knot pitch admits nothing; fall back to the
    # single segment containing the span's own midpoint rather than print
    # "(no segment)" for a pool that did fit.
    if not out:
        mid = 0.5 * (llo + lhi)
        best = min(range(len(k) - 1),
                   key=lambda i: abs(0.5 * (k[i] + k[i + 1]) - mid))
        out = [best]
    return out


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

    # ★★ IS THIS FILE EVEN FROM THE CURRENT FITTER? Checked before anything
    #    else: every entry the present fitter writes carries min_local_exp and
    #    floored_segments (the exponent floor) and monotone (the smoother). An
    #    artifact without them predates both, and its impossible tails are a
    #    stale file rather than a live bug.
    STAMPS = ("min_local_exp", "floored_segments", "monotone")
    entries = [e for e in (art.get("pools") or {}).values() if e]
    have = {k for e in entries for k in e.keys()}
    missing = [k for k in STAMPS if k not in have]
    if missing:
        print(f"\n  ⚠⚠ THIS ARTIFACT IS STALE. It carries none of "
              f"{missing} on any entry,\n"
              f"     so it was built BEFORE the fitter grew its exponent floor "
              f"(MIN_LOCAL_EXP)\n"
              f"     and its monotone pass -- the two things that exist to "
              f"forbid a k under 1.0.\n"
              f"     Any impossible exponent below is a file nobody rebuilt, "
              f"NOT a live bug:\n"
              f"         python engine/fit_distance_exponent.py\n"
              f"     Everything after this point describes the OLD curve.")
    else:
        print(f"\n  artifact carries the floor and monotone stamps "
              f"(current fitter).")

    targets = art.get("pool_targets") or {}
    pools = art.get("pools") or {}
    print(f"\n  artifact: {args.artifact}")
    print(f"  {len(pools)} pool curves + "
          f"{'a' if art.get('global') else 'NO'} global curve\n")

    # ★★ THE END SEGMENTS FIRST. An end segment has data on one side only and
    #    the thinnest knot spacing, so it bends where the middle cannot see --
    #    which is the owner's 600/800 complaint, on distances that are well
    #    inside the support and heavily raced.
    #
    # ⚠ AND THE ENDS MEAN THE ENDS OF THE SUPPORT, NOT THE ENDS OF THE KNOT
    #   GRID (2026-09-19). _knotGrid deliberately runs past the fitted span
    #   and _sampleClamped fills everything beyond it by LINEAR EXTENSION at
    #   the boundary slope -- that is the extrapolation POLICY, baked into the
    #   artifact on purpose. Reading the outermost knots therefore measured
    #   the policy and reported it as a fit, in a distance range where
    #   NOBODY RACES: hs_f|XC's grid runs to 11,843m and no high-school girl
    #   has ever run one. The segments below are the outermost ones whose
    #   MIDPOINT falls inside the entry's own span, so every number here is
    #   somewhere the pool actually competed.
    print(f"  === the END segments of the SUPPORT, per pool ===")
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
        inside = _supportedSegments(e)
        if not inside:
            print(f"  {key:<22} {'(no segment inside its own span)':>26}")
            continue
        lab_lo, x_lo = seg(inside[0])
        lab_hi, x_hi = seg(inside[-1])
        print(f"  {key:<22} {lab_lo:>26} {x_lo:7.3f}   "
              f"{lab_hi:>26} {x_hi:7.3f}")
        for lab, x, where in ((lab_lo, x_lo, "short end"),
                              (lab_hi, x_hi, "long end")):
            if x < EXP_MIN or x > EXP_MAX:
                ends_bad.append((key, where, lab, x))
    if ends_bad:
        print(f"\n  ⚠ {len(ends_bad)} END segment(s) with an unphysical "
              f"exponent, inside the support:")
        for key, where, lab, x in ends_bad:
            print(f"      {key:<22} {where:<10} {lab:>22}  {x:6.3f}{flag(x)}")
    else:
        print(f"\n  every pool's end segments are within "
              f"[{EXP_MIN}, {EXP_MAX}].")

    print(f"\n  === support and target, per pool ===")
    print(f"  {'pool|sport':<22} {'span (m)':>17} {'target':>8} "
          f"{'in?':>4} {'edges':>8} {'eps n':>6}  exponent at target")

    print(f"  {'-' * 22} {'-' * 17} {'-' * 8} {'-' * 4} {'-' * 8} {'-' * 6}"
          f"  {'-' * 30}")
    outside = []
    for key in sorted(pools):
        e = pools[key] or {}
        span = e.get("span") or (None, None)
        pool = key.split("|")[0]
        tgt = targets.get(pool)
        lo, hi = span
        # ! A TARGET EXACTLY AT THE EDGE IS INSIDE (2026-09-18). The span is
        #   stored as a float from exp(log(d)), so a 5,000m target against a
        #   5,000m span boundary compares as 5000.0 > 4999.9996 and was
        #   reported as extrapolated. Three of the nine "NO"s in the first real
        #   run were that -- college_f|XC, hs_f|XC and hs_m|XC all have their
        #   target ON the boundary. A metre of tolerance, which is far below
        #   any real distance difference.
        EDGE_M = 1.0
        in_span = (lo is not None and tgt is not None
                   and (lo - EDGE_M) <= tgt <= (hi + EDGE_M))
        exp_t = localExponent(e, tgt) if tgt else None
        span_s = (f"{lo:,.0f}..{hi:,.0f}" if lo is not None else "?")
        print(f"  {key:<22} {span_s:>17} "
              f"{(f'{tgt:,.0f}' if tgt else '?'):>8} "
              f"{('yes' if in_span else 'NO'):>4} "
              f"{e.get('n_edges', 0):>8,} {e.get('n_matched', 0):>6,}  "
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

    # ★★ AFTER A RE-FIT, THE FLOOR IS THE THING TO WATCH. MIN_LOCAL_EXP can
    #    make every end segment legal while the PAIRS still say otherwise: a
    #    curve pinned at the floor across most of its range is the floor
    #    talking, not the data. Measured on the 2026-09-18 re-fit:
    #    college_f|XC came back flat at 1.040 over 38 segments after two
    #    stability demotions to degree 1 -- a straight line at the floor is not
    #    a measurement.
    floor_reach = []
    for key in sorted(pools):
        e = pools[key] or {}
        kk, vv = e.get("knots") or [], e.get("values") or []
        fl = e.get("min_local_exp")
        if len(kk) < 2 or not fl:
            continue
        segs = [(vv[i + 1] - vv[i]) / (kk[i + 1] - kk[i])
                for i in range(len(kk) - 1)]
        at = sum(1 for x in segs if abs(x - fl) < 1e-6)
        if at:
            floor_reach.append((key, at, len(segs), fl))
    if floor_reach:
        print(f"\n  === how much of each curve is the FLOOR rather than the "
              f"data ===")
        print(f"  {'pool|sport':<22} {'at floor':>9} {'segments':>9} "
              f"{'share':>7}  floor")
        for key, at, n, fl in sorted(floor_reach, key=lambda r: -r[1] / r[2]):
            share = 100.0 * at / n
            note = ("   <-- the curve IS the floor; the pairs still say "
                    "pace improves with distance" if share > 60 else "")
            print(f"  {key:<22} {at:>9} {n:>9} {share:>6.0f}%  "
                  f"{fl:.3f}{note}")
        print(f"    ! the floor makes a curve LEGAL, not RIGHT. A pool mostly "
              f"at the floor means the\n      underlying pairs are still "
              f"confounded -- for XC that is the course and the\n      calendar "
              f"(a November championship 10k against an October 8k), which a\n"
              f"      same-athlete pair does not cancel.")

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

    print(f"\n  READ IT THIS WAY, AND ONLY WHERE THEY RACE. The END SEGMENTS "
          f"are the complaint:\n  600m and 800m are ordinary college track "
          f"distances with plenty of rows, well\n  inside the support, and "
          f"the curve still bends there because an end segment has\n  data on "
          f"one side only and the thinnest knots. An exponent under 1.0 is\n"
          f"  impossible -- it says a runner's pace improves as the race "
          f"lengthens -- so it is\n  the fit, not the runners.\n"
          f"\n  ⚠ A NUMBER FROM A DISTANCE NOBODY RUNS IS NOT A FINDING. "
          f"Every segment above\n    is inside its pool's own fitted span, "
          f"because reading the knot grid's ends\n    instead reported "
          f"elementary-school exponents at 8,410m and high-school girls'\n"
          f"    at 11,843m. Cross-country's 10k is the same trap: college men "
          f"barely race\n    it, so its end segment is real arithmetic about "
          f"an empty distance. Judge a\n    pool at its TARGET and across the "
          f"distances its athletes actually contest.\n"
          f"\n  `in? = NO` is a second, separate fault: that pool's every row "
          f"is extrapolated,\n  not only its tails. All six are TF, and they "
          f"are what --merge-sports exists to\n  fix -- a merged pool spans "
          f"800..10,000 and contains every anchor.")


if __name__ == "__main__":
    main()
