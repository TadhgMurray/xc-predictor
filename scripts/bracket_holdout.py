#!/usr/bin/env python3
"""
bracket_holdout.py -- score the bracket engine on the SAME held-out races
the joint model's ladder scores itself on, and fit it on everything.

    scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2
    scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2 --top 0.25
    scripts/bracket_holdout.py --full --era-years 2      # fit on all rows, write the file

The athlete sample (--pct, --seed) and the race split (10%, seed 1) are
run_joint's own, so `error sd` here and the ladder's base rung
(engine/data/ladder_logs/base.log, "error sd ... covered ...") are the
same question asked of two engines: a whole race the model never saw, at
a course it knows, from athletes it knows.
"""
import argparse
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket_engine as be                                     # noqa: E402
import pair_engine as pe                                        # noqa: E402
import pair_validate as pv                                      # noqa: E402
import run_joint as rj                                          # noqa: E402


def sampleAndSplit(cols, pct, seed, frac=0.10, split_seed=1):
    """run_joint's keep (an athlete sample) and holdout()'s race split."""
    keep = (np.asarray(cols["course"]) >= 0) & (np.asarray(cols["norm"]) > 0)
    if pct and pct < 100:
        ath_all = np.asarray(cols["athlete"])
        uniq = np.unique(ath_all[keep])
        rng = np.random.default_rng(seed)
        picked = uniq[rng.random(uniq.size) < pct / 100.0]
        keep = keep & np.isin(ath_all, picked)
    idx = np.flatnonzero(keep)
    race_all, _ = rj.raceCodes(cols["course"], cols["days"])
    te_local = pv.splitFor("race", idx.size, race=race_all[idx],
                           athlete=np.asarray(cols["athlete"])[idx],
                           cell=np.asarray(cols["course"])[idx],
                           frac=frac, seed=split_seed)
    train = np.zeros(keep.size, dtype=bool); train[idx[~te_local]] = True
    test = np.zeros(keep.size, dtype=bool); test[idx[te_local]] = True
    return train, test


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    d = rj.buildParser()
    ap.add_argument("--pack", default=d.get_default("pack"))
    ap.add_argument("--npz", default=d.get_default("out"),
                    help="the joint solve's file, for the form curve and the ratings")
    ap.add_argument("--out", default=os.path.join(_ROOT, "engine", "data",
                                                  "bracket_difficulty.npz"))
    ap.add_argument("--pct", type=float, default=15.0)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--era-years", type=int, default=0)
    ap.add_argument("--window", type=float, default=21.0)
    ap.add_argument("--top", type=float, default=0.5)
    ap.add_argument("--prior-rows", type=float, default=20.0)
    ap.add_argument("--iters", type=int, default=30)
    ap.add_argument("--no-tilt", action="store_true")
    ap.add_argument("--no-curve", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="fit on every row and write --out instead of scoring")
    args = ap.parse_args()
    cols = pe.loadPack(args.pack)
    npz = dict(np.load(args.npz, allow_pickle=False)) if os.path.exists(args.npz) else None
    if npz is None:
        print(f"(no joint file at {args.npz}: no curve, no ratings, no tilt)")
    t0 = time.time()
    if args.full:
        f = be.fit(cols, npz, train=None, window=args.window, top=args.top,
                   era_years=args.era_years, n_iter=args.iters,
                   prior_rows=args.prior_rows, tilt=not args.no_tilt,
                   use_curve=not args.no_curve, verbose=True)
        np.savez(args.out, D=f["D"], votes=f["votes"], course_keys=np.array(f["cell_keys"]),
                 D_race=f["D_race"], votes_race=f["votes_race"],
                 window=np.array([args.window]), top=np.array([args.top]),
                 era_years=np.array([args.era_years]))
        print(f"[bracket] wrote {args.out}: {int((f['votes'] > 0).sum()):,} cells with votes "
              f"in {time.time() - t0:.0f}s")
        return
    train, test = sampleAndSplit(cols, args.pct, args.seed)
    print(f"[bracket] {int(train.sum()):,} training rows, {int(test.sum()):,} held-out rows "
          f"({args.pct}% of athletes, 10% of their races)")
    f = be.fit(cols, npz, train=train, window=args.window, top=args.top,
               era_years=args.era_years, n_iter=args.iters,
               prior_rows=args.prior_rows, tilt=not args.no_tilt,
               use_curve=not args.no_curve, verbose=True)
    pred, cov = be.predict(f)
    y = np.log(np.asarray(cols["norm"], dtype=np.float64))
    m = test & cov
    err = y[m] - pred[m]
    print(f"\n[bracket] HELD OUT: 10% of RACES -- a whole new race at a known course")
    print(f"[bracket] error sd {err.std():.6f}   covered {cov[test].mean():.1%}   "
          f"[{time.time() - t0:.0f}s]   window {args.window:g} top {args.top:g} "
          f"era {args.era_years} tilt {'off' if args.no_tilt else 'on'} "
          f"curve {'off' if args.no_curve else 'on'}")
    if "sport" in cols:
        sport = np.asarray(cols["sport"])
        for code, name in ((0, "XC"), (1, "TF")):
            mm = m & (sport == code)
            if mm.sum() > 1000:
                e = y[mm] - pred[mm]
                print(f"        {name}: {e.std():.6f}  ({int(mm.sum()):,} rows)")
    print("        compare: the ladder's base rung in engine/data/ladder_logs/base.log, "
          "same sample, same split, same question")


if __name__ == "__main__":
    main()
