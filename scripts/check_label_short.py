# Project: xc-predictor / scripts
# File:    check_label_short.py
# Purpose: Find courses whose distance LABELS are short -- the mirror image
#          of the Ox Bow class, invisible to every existing guard.
#
#     python scripts/check_label_short.py                  # corpus scan
#     python scripts/check_label_short.py --venue 7141     # one venue
#
# ★ THE MECHANISM (venue 7141 / Cabell Midland, 2026-08-27). A too-long
#   label mints impossible times and trips every guard. A too-SHORT label
#   makes everyone mildly slow, trips nothing, and -- because downward-only
#   forbids raising a label -- can never be overridden. The difficulty
#   solve then absorbs the shortfall as terrain: 1 + d = (true/label)^K on
#   top of the real terrain. Ratings mostly come out right (the solve
#   launders the error), but the label, the displayed difficulty, the
#   distance-scoped PR flags, and any correctly-measured race that shares
#   the cell are all wrong.
#
# ★ THE EVIDENCE IS CONVERGENCE. One inflated cell is just a hard course.
#   But when a venue's SEVERAL short cells, labeled different distances,
#   all imply the SAME longer true distance --
#
#       true = label * ((1+d) / (1+d_ref))^(1/K)
#
#   with d_ref the venue's longest solved cell (the label most likely
#   honest, carrying the shared terrain) -- terrain cannot explain it,
#   because terrain is shared and cancels in the ratio. Measured at 7141:
#   labels 3000/3200/3300 at +0.155/+0.092/+0.066 imply 3350/3390/3410 m.
#   One course, three wrong names.
#
# Read-only; needs only pair_difficulty.npz. Report-first: what to DO with
# the class is a policy question (downward-only forbids the obvious fix).

import argparse
import os
import re
import sys
from collections import defaultdict

import numpy as np

_DIFF = os.path.join("engine", "data", "pair_difficulty.npz")
K = 1.06                    # the normaliser's distance exponent

MIN_IMPLIED_EXCESS = 0.05   # implied true must exceed the label by 5%+
CONVERGE_TOL = 0.04         # and the implied distances must agree within 4%
MIN_ROWS_NOTE = "degree"    # npz carries degree per cell for a weight-ish n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", default=_DIFF)
    ap.add_argument("--venue", default=None,
                    help="print one venue base (id or name fragment)")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    npz = np.load(args.diff, allow_pickle=True)
    keys = [str(k) for k in npz["course_keys"]]
    # ! difficulty_raw, NOT difficulty. The linkage step now NUKES the
    #   convergence-proven cells to their sport default before the shipped
    #   difficulty is written -- reading the shipped column would blind this
    #   detector to exactly the cells it exists to find. The raw column is
    #   the pre-shrink solve and keeps the evidence.
    diff = np.asarray(npz.get("difficulty_raw", npz["difficulty"]),
                      dtype=np.float64)
    solved = np.asarray(npz["solved"], dtype=bool)
    degree = np.asarray(npz["degree"])

    # XC cells with a parseable distance, grouped by venue base
    venues = defaultdict(list)          # base -> [(label_m, cell_index)]
    for i, k in enumerate(keys):
        if not k.startswith("XC:") or not solved[i]:
            continue
        m = re.search(r"^(XC:.+):d(\d+)$", k)
        if not m:
            continue
        venues[m.group(1)].append((float(m.group(2)), i))

    findings = []
    for base, cells in venues.items():
        if len(cells) < 2:
            continue
        cells.sort()
        ref_label, ref_i = cells[-1]            # longest = the honest label
        d_ref = diff[ref_i]
        if not np.isfinite(d_ref) or d_ref <= -1.0:
            continue
        implied = []
        for label, i in cells[:-1]:
            if label <= 0 or not np.isfinite(diff[i]) or diff[i] <= -1.0:
                continue
            t = label * (((1.0 + diff[i]) / (1.0 + d_ref)) ** (1.0 / K))
            implied.append((label, i, t))
        # the short cells whose implied true exceeds their label
        hot = [(lb, i, t) for lb, i, t in implied
               if t / lb - 1.0 >= MIN_IMPLIED_EXCESS]
        if len(hot) < 2:
            continue
        ts = np.array([t for _, _, t in hot])
        center = float(np.median(ts))
        if not np.all(np.abs(ts / center - 1.0) <= CONVERGE_TOL):
            continue
        findings.append({"base": base, "center": center, "hot": hot,
                         "ref": (ref_label, d_ref),
                         "n": int(sum(degree[i] for _, i, _ in hot))})

    if args.venue:
        pat = args.venue.lower()
        print(f"\n  cells at venues matching {pat!r} "
              "(implied = label-corrected by the difficulty ratio):")
        for base, cells in sorted(venues.items()):
            if pat not in base.lower():
                continue
            cells.sort()
            ref_label, ref_i = cells[-1]
            print(f"\n    {base}   (reference: d{ref_label:.0f} at "
                  f"{diff[ref_i]:+.3f})")
            for label, i in cells:
                t = label * (((1.0 + diff[i]) / (1.0 + diff[ref_i]))
                             ** (1.0 / K))
                print(f"      d{label:<7.0f} diff {diff[i]:+.3f}   "
                      f"implied true ~ {t:,.0f} m"
                      f"{'   <- label reads SHORT' if t / label > 1.05 else ''}")
        return

    findings.sort(key=lambda f: -f["n"])
    print(f"\n  {len(findings)} venues where 2+ short cells' implied true "
          f"distances converge\n  (excess >= {MIN_IMPLIED_EXCESS:.0%}, "
          f"agreement within {CONVERGE_TOL:.0%}). One course, several "
          "wrong labels.\n")
    print(f"    {'venue base':<34}{'~true m':>9}  labeled as")
    print("    " + "-" * 70)
    for f in findings[:args.limit]:
        labels = ", ".join(f"{lb:.0f}(+{diff[i]:.2f})"
                           for lb, i, _ in f["hot"])
        print(f"    {f['base'][:34]:<34}{f['center']:>9,.0f}  {labels}"
              f"   [ref d{f['ref'][0]:.0f} {f['ref'][1]:+.2f}]")
    if len(findings) > args.limit:
        print(f"    ... and {len(findings) - args.limit} more")
    print("\n  These labels cannot be fixed by overrides (downward-only "
          "forbids raising).\n  The difficulty solve absorbs them, so "
          "ratings survive -- the labels, the\n  displayed difficulty and "
          "the distance-scoped PRs do not. Policy decision\n  needed before "
          "any fix.")


if __name__ == "__main__":
    main()
