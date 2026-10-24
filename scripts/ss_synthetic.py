"""
ss_synthetic.py -- the planted-world tables in docs/REVIEW-ENGINE-2026-10.md
§5: the state-space prototype (engine/statespace.py) against the same code
held to the current engine's shape, on held-out races, a forward month and
the spring track, plus what each does to the ratings of a mis-measured
division.

    python scripts/ss_synthetic.py            # seeds 0 and 1, ~1 minute
    python scripts/ss_synthetic.py --seeds 0 1 2 3

★ THE RUNGS, cumulative:
    A    athlete-season ability, one race-day per venue-day, Gaussian days,
         the XC gap asserted -- the current engine's skeleton (no bracket,
         field check or island check: those are fixes for what A gets wrong)
    +walk         ability a random walk with a personal season trend
    +nested       venue-day > race, so a division has its own day
    B    +heavy   Student-t days: a short course is absorbed, not shrunk
⚠ PLANTED, NOT REAL. Every gain here is a gain on a world built to have the
  things the model models. The pack decides (scripts/ss_eval.py).
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import statespace as ss                                         # noqa: E402
import statespace_world as sw                                   # noqa: E402

GAP = sw.TRUE["mu_xc"]
LADDER = (
    ("A (current shape)", dict(dynamic=False, nested=False, heavy=False)),
    ("+walk", dict(dynamic=True, nested=False, heavy=False)),
    ("+nested", dict(dynamic=True, nested=True, heavy=False)),
    ("B (+heavy)", dict(dynamic=True, nested=True, heavy=True)),
)


def figures(D, R, fast_only=False):
    """The rating a model would publish, in log time: the row less its
    tilted course and day. fast_only=True credits XC days only when fast
    and track days never (production's XCP_RACE_EFFECT_SPORTS=XC:fast)."""
    h, cell = R["h"], D["cell"]
    course = R["mu"][D["group"]] + np.where(cell >= 0, R["cell"][np.maximum(cell, 0)], 0.0)
    day = R["day"][D["day"]] + R["race"][D["race"]]
    if fast_only:
        day = np.where(D["xc"] == 1, np.minimum(day, 0.0), 0.0)
    return D["y"] - h * (course + day)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1])
    ap.add_argument("--outer", type=int, default=25)
    a = ap.parse_args()
    for seed in a.seeds:
        D = sw.world(seed=seed)
        S = sw.splits(D)
        print(f"\n### seed {seed}: {D['y'].size:,} rows, {D['starts'].size - 1:,} people")
        for split in ("race", "forward", "sport"):
            tr, sc = S[split]
            print(f"\n{split}: train {tr.sum():,}, scored {sc.sum():,}")
            print(f"  {'rung':20s} {'bias':>7s} {'med|e|':>7s} {'spread':>7s} {'h2h':>7s} {'kappa':>6s}")
            for name, kw in LADDER:
                t0 = time.time()
                R = ss.fit(D, tr, outer=a.outer, fix_mu={0: GAP}, **kw)
                r = ss.score(D["y"], R["pred"], D["race"], sc)
                print(f"  {name:20s} {100 * r['bias']:+6.2f}% {100 * r['med_abs']:6.3f}% "
                      f"{100 * r['spread']:6.3f}% {100 * r['h2h']:6.2f}% {R['kappa']:6.2f}"
                      f"  ({time.time() - t0:.0f}s)")
        # the ratings themselves, fitted on everything
        allrows = np.ones(D["y"].size, dtype=bool)
        bad = D["bad_race"]
        print(f"\nratings vs the planted performance, |figure - truth| median, all rows fitted"
              f" ({bad.mean():.1%} of rows in a mis-measured division)")
        print(f"  {'rung':28s} {'ordinary':>9s} {'mis-measured':>13s}")
        for name, kw, fast in (("A, every day credited", LADDER[0][1], False),
                               ("A, XC:fast", LADDER[0][1], True),
                               ("B, every day credited", LADDER[3][1], False)):
            R = ss.fit(D, allrows, outer=a.outer, fix_mu={0: GAP}, **kw)
            err = figures(D, R, fast) - D["truth_perf"]
            err = err - np.median(err[~bad])          # the scale's level is free
            print(f"  {name:28s} {100 * np.median(np.abs(err[~bad])):8.3f}% "
                  f"{100 * np.median(np.abs(err[bad])):12.3f}%")
        R = ss.fit(D, allrows, outer=a.outer, **LADDER[3][1], fix_mu={0: GAP})
        print(f"\nB recovers: kappa {R['kappa']:.3f} (planted {sw.TRUE['kappa']}), "
              f"sigma {np.sqrt(R['sigma2']):.4f} ({sw.TRUE['sigma']} + bad days), "
              f"season trend sd {np.sqrt(R['s_slope2']):.4f}/yr ({sw.TRUE['slope_sd']}), "
              f"tau XC {np.sqrt(R['tau2'][0]):.4f} ({sw.TRUE['tau'][0]}), "
              f"day sd XC {np.sqrt(R['s_w2'][0]):.4f} ({sw.TRUE['s_w'][0]}, t3)")
        print("\nthe XC gap, profiled (train sigma; spring-track holdout med|e| and h2h):")
        tr, sc = S["sport"]
        for g in (0.0, 0.03, 0.06, 0.09):
            R = ss.fit(D, tr, outer=a.outer, fix_mu={0: g}, **LADDER[3][1])
            r = ss.score(D["y"], R["pred"], D["race"], sc)
            print(f"  gap {g:.2f}: sigma {np.sqrt(R['sigma2']):.5f}  med|e| "
                  f"{100 * r['med_abs']:.3f}%  h2h {100 * r['h2h']:.2f}%  bias {100 * r['bias']:+.2f}%")


if __name__ == "__main__":
    main()
