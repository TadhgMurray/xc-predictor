#!/usr/bin/env python3
"""
curve_window_gap.py -- what the engine's OWN form curve says the XC/TF
within-year difference is.

    scripts/curve_window_gap.py logs/run18.out
    scripts/curve_window_gap.py logs/*.out --compare -0.02795

★ WHY THIS IS THE SECOND OPINION. measure_sport_gap infers the gap from
  athletes who cross between the sports. The joint solve independently fits a
  per-pool FORM CURVE over the academic year (knots every CURVE_KNOT_DAYS,
  one pinned at 1 October) and prints, every outer pass:

      curve TF-XC window gap [ ... ]

  That is the same quantity measured a completely different way: not from
  crossers, but from the shape of the season for everybody.

⚠⚠ AND ON THE FIRST REAL RUN IT WAS PINNED, WHICH MAKES IT NO OPINION AT
   ALL (2026-09-09). logs/run18.out returned -0.02000 for eleven windows and
   nan for three:

       pass 4: [-0.02000 -0.02000 ... -0.02000 +nan +nan +nan]

   joint_solve: `gap_target = -float(winter_gain or 0.0)` and
   CURVE_GAP_WEIGHT = 100.0. The run carried XCP_WINTER_GAIN=0.02, so the
   curve's TF-XC gap was CONSTRAINED to -0.02 by a very heavy penalty. It
   agreeing with a sandwich D of -0.02795 means nothing whatever: it was
   told to be -0.02 and it obeyed.

   So this is a second opinion ONLY when the pin is off. Re-run the solve
   with --winter-gain 0 (gap_target 0) and the curve is free to find its own
   TF-XC gap; then the comparison below is worth reading. This script now
   refuses to draw a conclusion when the windows are all the same number.

! nan IS A SENTINEL, NOT A FAILURE. curveWindowGaps returns nan for a pool
  with no dual-sport balance to measure -- three of fourteen on that run.
  They are counted and skipped; the first version averaged them and printed
  a mean of nan, then drew a conclusion from it anyway.

! READS A LOG, WRITES NOTHING, TOUCHES NO DATABASE. The number is already
  printed by every verbose run; it just scrolls past at 3am.
"""

import argparse
import glob
import re
import sys

# "curve TF-XC window gap [ 0.0123 -0.0045]" -- numpy's array repr, so the
# separator is whitespace and the brackets are the delimiter.
_GAP = re.compile(r"curve TF-XC window gap\s*\[([^\]]*)\]")
# the pass line also carries the sport recentring, which is the other half
_BBAR = re.compile(r"recentred by\s*([+-]?\d*\.?\d+)")


def gapsIn(text):
    """[(pass_index, [gap, ...]), ...] in file order."""
    out = []
    for i, m in enumerate(_GAP.finditer(text)):
        vals = []
        for tok in m.group(1).replace(",", " ").split():
            try:
                vals.append(float(tok))
            except ValueError:
                pass
        out.append((i, vals))
    return out


def main():
    ap = argparse.ArgumentParser(
        description="The fitted form curve's own XC/TF within-year gap.")
    ap.add_argument("logs", nargs="+", help="run log(s); globs are expanded")
    ap.add_argument("--compare", type=float, default=None, metavar="D",
                    help="the sandwich estimate to hold it against "
                         "(e.g. -0.02795)")
    args = ap.parse_args()

    paths = [p for pat in args.logs for p in sorted(glob.glob(pat)) or [pat]]
    found = False
    for path in paths:
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            print(f"  {path}: {e}")
            continue
        gaps = gapsIn(text)
        bbars = [float(x) for x in _BBAR.findall(text)]
        if not gaps:
            print(f"\n  {path}: no 'curve TF-XC window gap' line.")
            print("    The run needs verbose=True and a fitted curve "
                  "(--no-curve turns it off).")
            continue
        found = True
        print(f"\n  {path}")
        print(f"    {len(gaps)} outer pass(es) with a curve gap")
        for i, vals in gaps[-3:]:
            shown = " ".join(f"{v:+.5f}" for v in vals)
            print(f"      pass {i}: [{shown}]")
        last = gaps[-1][1]
        if not last:
            continue
        # ! SKIP THE SENTINELS, AND SAY HOW MANY. A pool with no dual-sport
        #   balance has nothing to measure and comes back nan.
        finite = [v for v in last if v == v]
        n_nan = len(last) - len(finite)
        if n_nan:
            print(f"    {n_nan} of {len(last)} windows are nan (a pool with "
                  f"no dual-sport balance) -- skipped")
        if not finite:
            print("    every window is nan; nothing to compare.")
            continue
        # ! THE MEAN OVER WINDOWS, and it is a summary rather than the
        #   quantity: the windows are separate parts of the year and a
        #   season that is high early and low late averages to nothing while
        #   being very much not flat. The per-window line above is the data.
        mean = sum(finite) / len(finite)
        span = max(finite) - min(finite)
        print(f"    last pass: mean {mean:+.5f} over {len(finite)} finite "
              f"window(s), spread {span:.5f}")
        if bbars:
            print(f"    and the sport recentring that pass: {bbars[-1]:+.5f}")

        # ⚠⚠ THE PIN. CURVE_GAP_WEIGHT is 100.0 and gap_target is
        #    -winter_gain, so with a winter gain set the curve is TOLD what
        #    its TF-XC gap must be. Every window landing on the same number
        #    is that penalty winning, and a pinned curve cannot corroborate
        #    anything.
        pinned = span < 0.002 and abs(mean) > 1e-6
        if pinned:
            print(f"\n    ⚠ PINNED, NOT FITTED. All {len(finite)} windows "
                  f"agree to {span:.5f}, which is the\n      "
                  f"CURVE_GAP_WEIGHT=100 penalty holding the gap at "
                  f"gap_target = -winter_gain.\n      This run looks like "
                  f"XCP_WINTER_GAIN={-mean:g}. The curve is not an "
                  f"independent\n      estimate here -- re-run the solve "
                  f"with --winter-gain 0 to free it.")
        if args.compare is not None and pinned:
            print(f"\n    (not comparing against {args.compare:+.5f}: a "
                  f"pinned curve agreeing with\n     a measurement is not "
                  f"evidence, it is the pin.)")
        elif args.compare is not None:
            d = args.compare
            print(f"\n    sandwich D          {d:+.5f}")
            print(f"    curve window mean   {mean:+.5f}")
            print(f"    difference          {d - mean:+.5f}")
            if abs(mean) < 0.2 * abs(d):
                print("      -> the curve sees almost none of it. D is more "
                      "likely a real\n         scale error; "
                      "--sport-gap-delta is the right lever.")
            elif abs(mean - d) < 0.3 * abs(d):
                print("      -> the curve has already absorbed most of it. "
                      "That is FORM,\n         not sport scale, and "
                      "correcting the level would double-count.")
            else:
                print("      -> partly seen. The difference is the part the "
                      "curve does not\n         explain, and is the most "
                      "defensible thing to correct.")
    if not found:
        print("\n  Nothing read. Point this at a verbose run log "
              "(e.g. logs/run18.out).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
