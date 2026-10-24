"""
ss_synthetic.py -- the planted-world tables in docs/REVIEW-ENGINE-2026-10.md
§6: the state-space engine (engine/statespace.fitGLS) against the same code
held to the current engine's skeleton, on the hard world
(statespace_world.worldHard: the real calendar's overlaps, tapers, December
breaks, only the best racing December XC, short courses, bad days, a tilt,
the XC gap planted at 6% and indoor at 1%).

    python scripts/ss_synthetic.py                 # seeds 0 1 2, ~5 minutes
    python scripts/ss_synthetic.py --seeds 0 --quick

★ THE TABLES
    1  held-out races, a forward month, spring track from the fall:
       A (athlete-season ability, one day per venue-day, Gaussian days, the
       gap ASSERTED at the truth) against the engine (everything fitted)
    2  the ratings against the planted performance: ordinary rows, rows in a
       mis-measured division, and the XC-vs-track level of the ratings
    3  the gap: realised (the planted courses' own average), the engine's,
       the naive 30-day pair difference, and the engine without each kind
       of bridge
    4  the gap's precision against how many venues the bridges touch
⚠ PLANTED, NOT REAL. The pack decides (scripts/ss_eval.py).
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import statespace as ss                                         # noqa: E402
import statespace_world as sw                                   # noqa: E402

TERMS = ("peak", "recovery", "event", "mu_by_level")


def realised(D):
    tc = np.asarray(D["truth_course"]); g = D["group"]
    _, first = np.unique(D["cell"], return_index=True)
    cm = {gg: tc[first[g[first] == gg]].mean() for gg in range(3)}
    T = D["truth"]
    return T["mu_xc"] + cm[0] - cm[1], T["mu_in"] + cm[2] - cm[1]


def rungA(D, tr, outer):
    T = D["truth"]
    return ss.fit(D, tr, outer=outer, dynamic=False, nested=False, heavy=False,
                  fix_mu={ss.GROUP_XC: T["mu_xc"], ss.GROUP_IN: T["mu_in"]})


def perfA(D, R):
    return D["y"] - R["h"] * (R["mu"][D["group"]] + R["cell"][D["cell"]]
                              + R["day"][D["day"]] + R["race"][D["race"]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--outer", type=int, default=20)
    ap.add_argument("--quick", action="store_true", help="skip tables 3b and 4")
    a = ap.parse_args()
    for seed in a.seeds:
        D = sw.worldHard(seed=seed)
        S = sw.splits(D)
        print(f"\n### seed {seed}: {D['y'].size:,} rows, {D['starts'].size - 1:,} runners")
        print("\n1. held out")
        print(f"  {'split':8s} {'rung':10s} {'bias':>7s} {'med|e|':>7s} {'spread':>7s} {'h2h':>7s}")
        for split in ("race", "forward", "sport"):
            tr, sc = S[split]
            for name, fn in (("A", lambda: rungA(D, tr, a.outer)),
                             ("engine", lambda: ss.fitGLS(D, tr, outer=a.outer, terms=TERMS))):
                R = fn()
                r = ss.score(D["y"], R["pred"], D["race"], sc)
                print(f"  {split:8s} {name:10s} {100 * r['bias']:+6.2f}% {100 * r['med_abs']:6.3f}% "
                      f"{100 * r['spread']:6.3f}% {100 * r['h2h']:6.2f}%", flush=True)
        print("\n2. ratings against the planted performance (median |error|)")
        allr = np.ones(D["y"].size, dtype=bool)
        bad = D["bad_race"]; xc = D["group"] == ss.GROUP_XC
        RA = rungA(D, allr, a.outer)
        RB = ss.fitGLS(D, allr, outer=a.outer, terms=TERMS)
        for name, perf in (("A", perfA(D, RA)), ("engine", RB["perf"])):
            err = perf - D["truth_perf"]
            err = err - np.median(err[~bad & ~xc])
            print(f"  {name:8s} ordinary XC {100 * np.median(np.abs(err[~bad & xc])):.2f}%  "
                  f"ordinary track {100 * np.median(np.abs(err[~bad & ~xc])):.2f}%  "
                  f"mis-measured {100 * np.median(np.abs(err[bad])):.2f}%  "
                  f"XC level vs track {100 * np.median(err[~bad & xc]):+.2f}%", flush=True)
        rx, ri = realised(D)
        nv = sw.naiveGaps(D, 30)
        print(f"\n3. the gap (+ = slower than the flat outdoor gauge)")
        print(f"  realised       XC {100 * rx:+.2f}%   indoor {100 * ri:+.2f}%")
        print(f"  engine         XC {100 * RB['gap_ref'][ss.GROUP_XC]:+.2f}%   indoor "
              f"{100 * RB['gap_ref'][ss.GROUP_IN]:+.2f}%   (taper "
              f"{100 * RB['b'][RB['blocks']['peak'][0]]:+.2f}%, planted -1.00%)")
        print(f"  naive 30-day   XC {-100 * nv['outdoor-XC'][0]:+.2f}%   indoor "
              f"{100 * (nv['indoor-XC'][0] - nv['outdoor-XC'][0]):+.2f}%")
        if not a.quick:
            masks = ss.bridgeMasks(D)
            for k in ss.BRIDGE_KINDS:
                E = ss.subsetRows(D, ~masks[k])
                Rk = ss.fitGLS(E, np.ones(E["y"].size, dtype=bool), outer=a.outer, terms=TERMS)
                print(f"  without {k:16s} XC {100 * Rk['gap_ref'][ss.GROUP_XC]:+.2f}%   indoor "
                      f"{100 * Rk['gap_ref'][ss.GROUP_IN]:+.2f}%", flush=True)
    if not a.quick:
        print("\n4. the XC-vs-track level of the ratings against the number of venues")
        for nr, schools in ((10, 20), (40, 10)):
            offs = []
            for seed in a.seeds:
                D = sw.worldHard(seed=seed, n_regions=nr, schools=schools)
                R = ss.fitGLS(D, np.ones(D["y"].size, dtype=bool), outer=15, terms=TERMS)
                bad = D["bad_race"]; xc = D["group"] == ss.GROUP_XC
                err = R["perf"] - D["truth_perf"]
                offs.append(np.median(err[xc & ~bad]) - np.median(err[~xc & ~bad]))
            print(f"  {nr:3d} regions: offsets {np.round(100 * np.array(offs), 2)}%  "
                  f"rms {100 * np.sqrt(np.mean(np.square(offs))):.2f}%", flush=True)


if __name__ == "__main__":
    main()
