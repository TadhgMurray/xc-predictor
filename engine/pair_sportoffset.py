"""
pair_sportoffset.py -- how much should a per-athlete sport offset be shrunk?

THE MODEL
    y = alpha[athlete, season] + beta[athlete, season] * s + delta[cell]

    s is +-0.5 for TF/XC, centred within the athlete-season, and beta is
    penalised by a ridge K.

        K -> infinity   beta = 0            one shared ability (today)
        K = 0           beta unpenalised    a full per-sport split

    Measured on the real corpus 2026-08-31, over 5,947,709 held-out rows:

        K = inf (shared)  0.047039   XC 0.049975  TF 0.042993      --
        K = 20            0.046444   XC 0.049435  TF 0.042313   -1.27%
        K = 5             0.045503   XC 0.048594  TF 0.041215   -3.27%
        K = 2             0.044810   XC 0.047994  TF 0.040375   -4.74%
        K = 1             0.044464   XC 0.047710  TF 0.039932   -5.48%
        K = 0.5           0.044325   XC 0.047611  TF 0.039729   -5.77%  <- best
        K = 0.2           0.044349   XC 0.047657  TF 0.039721   -5.72%
        K = 0 (full)      0.045670   XC 0.048557  TF 0.041636   -2.91%

    ★ AN INTERMEDIATE K WINS, WHICH IS THE OUTCOME THIS FILE HOPED FOR AND DID
      NOT GET ON SYNTHETIC DATA. K = 0.5 beats both ends: 5.77% better than one
      shared ability, and 3% better than a full split. 23% of athlete-seasons
      end up with a non-zero beta -- the specialists -- and the rest are shrunk
      to zero, which is what keeps the graph connected.

    ⚠ AND IT SUPERSEDES THE EARLIER NOTE HERE, which recorded the full split at
      -5.56% and TF alone at -10.01%. On the current corpus the full split is
      only -2.91%: at K = 0 every thin athlete-season gets an unpenalised
      offset it has no evidence for, and that costs more than the specialists
      gain. Splitting still severs the XC-TF edges and still makes the level
      unidentifiable -- see pair_recenter -- which is why recentring is
      required at any K.

★ THIS SWEEP FINDS WHERE THE TRADE SITS. Partial pooling keeps alpha spanning
  both sports, so the graph stays connected, while letting a genuine specialist
  differ. The right K is not a matter of taste: it is whichever value predicts
  held-out rows best.

★ WHY AN INTERMEDIATE K SHOULD WIN HERE AND DID NOT ON SYNTHETIC DATA. Shrinkage
  only helps when beta is poorly identified -- an athlete with twenty races in
  each sport needs no help, one with a single TF race needs a great deal. A
  synthetic corpus where everyone races six balanced times has no such athletes,
  so K = 0 wins there. The real corpus is full of them.

Measures only. Nothing is written.
"""

import os
import sys
import time

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
import pair_validate as pv

_SWEEP = (1e9, 20.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.0)


def heldOutSc(sport, group_tr, group_te, n_groups, tr, te):
    """
    Centred sport for the held-out rows, using the TRAIN group means.

    ★ TRAIN MEANS, NOT ALL-DATA MEANS. Centring held-out rows on their own group
      mean would let the test set inform its own prediction -- the offset would
      be measured partly from the rows being predicted, and every K would look
      better than it is.
    """
    s = sport.astype(np.float64) - 0.5
    total = np.bincount(group_tr, weights=s[tr], minlength=n_groups)
    count = np.maximum(np.bincount(group_tr, minlength=n_groups), 1)
    return s[te] - (total / count)[group_te]


def evaluate(D, te, ridge):
    """One K: fit on train, predict held-out, report overall and per sport."""
    tr = ~te
    course, group, y, sport = D["course"], D["group"], D["y"], D["sport"]
    n_cells, n_groups = D["n_cells"], D["n_groups"]

    t0 = time.time()
    shared = ridge > 1e8
    sc_tr = None if shared else pe.sportCentered(sport[tr], group[tr], n_groups)

    T = pv.solveSubset(course[tr], group[tr], y[tr], n_cells, n_groups,
                       quiet=True, sc=sc_tr, ridge=ridge)
    solved = T["degree"] >= 2

    if shared:
        alpha, count = pv.refitAlpha(y[tr], T["delta"], course[tr], group[tr],
                                     n_groups)
        beta = np.zeros(n_groups)
        sc_te = np.zeros(int(te.sum()))
    else:
        alpha, beta, count = pv.refitAlphaBeta(y[tr], T["delta"], course[tr],
                                               group[tr], n_groups, sc_tr,
                                               ridge)
        sc_te = heldOutSc(sport, group[tr], group[te], n_groups, tr, te)

    ok = solved[course[te]] & (count[group[te]] > 0)
    pred = (alpha[group[te][ok]] + beta[group[te][ok]] * sc_te[ok]
            + T["delta"][course[te][ok]])
    err = y[te][ok] - pred

    per = {}
    for code, name in ((0, "XC"), (1, "TF")):
        m = ok & (sport[te] == code)
        if m.sum() > 1000:
            p = (alpha[group[te][m]] + beta[group[te][m]] * sc_te[m]
                 + T["delta"][course[te][m]])
            per[name] = float((y[te][m] - p).std())

    nz = float(np.mean(np.abs(beta[count > 0]) > 1e-6)) if not shared else 0.0
    tag = "  (shared)" if shared else ("  (full split)" if ridge == 0 else "")
    print(f"  {ridge:>10g}  {err.std():.6f}   "
          f"XC {per.get('XC', float('nan')):.6f}  "
          f"TF {per.get('TF', float('nan')):.6f}   "
          f"cov {100.0 * ok.mean():.1f}%  beta!=0 {100.0 * nz:.0f}%  "
          f"[{time.time() - t0:.0f}s]{tag}")
    return err.std(), per


def main(pack_path, frac=0.10):
    import pair_all as pa

    D = pa.prepare(pack_path)
    if "sport" not in D:
        print("[offset] pack has no 'sport' column")
        return

    te = pv.splitByRow(D["y"].size, frac=frac, seed=1)
    print(f"[offset] {int(te.sum()):,} held-out rows\n")
    print("           K   held-out sd        by sport            coverage")

    best, results = None, []
    for K in _SWEEP:
        sd, per = evaluate(D, te, K)
        results.append((K, sd, per))
        if best is None or sd < best[1]:
            best = (K, sd)

    shared = next(s for k, s, _ in results if k > 1e8)
    print(f"\n[offset] best K = {best[0]:g}   {best[1]:.6f}   "
          f"{100.0 * (best[1] - shared) / shared:+.2f}% vs the shared ability")
    if best[0] == 0.0:
        print("    K = 0 winning means shrinkage buys nothing and a full split")
        print("    is genuinely better -- at the cost of severing the sports.")
    elif best[0] > 1e8:
        print("    the shared ability wins; leave the model alone.")
    else:
        print("    an intermediate K wins: specialists get their offset, thin")
        print("    athlete-seasons do not, and the graph stays connected.")


if __name__ == "__main__":
    here = os.path.join(_HERE, "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else os.path.join(here, "packed_XC_TF.npz"))