"""
pair_sportsplit.py -- should XC and TF share ONE ability?

THE UNTESTED ASSUMPTION
    The merged solve fits alpha[athlete, season] -- one ability covering both
    sports. A 4:05 miler who is mediocre on grass and a grass specialist with no
    kick get the SAME number, and whatever the model cannot explain goes into
    whichever venue they happened to race.

    Nothing has ever measured whether that costs accuracy. Everything else we
    checked came back defensible -- XC distance agrees with an independent
    estimator to 1.2%, altitude is monotone and absorbed, banked tracks read
    0.8% faster than flat, TF event conversion is off by 0.4% at 800m -- so this
    is the largest remaining structural choice with no evidence behind it.

★ WHAT SPLITTING COSTS, AND WHY THE TEST IS NOT FREE-WINS-ONLY
    Splitting doubles the ability parameters for every dual-sport athlete, so
    each is estimated from fewer races. More flexibility always fits the
    training rows better; held-out is the only way to know whether it pays.

★ AND IT SEVERS THE GRAPH. Shared athletes are the ONLY thing linking XC venues
  to TF venues. Split the ability and the bipartite graph falls into two
  components with two independent gauges -- Stage 0 measured 99.95% of rows in
  one component precisely because ability is shared. So the split model cannot
  compare difficulty ACROSS sports at all, even if it predicts better within
  them. That is a real loss, not a technicality: TF sits 0.043 below XC in the
  current solve, and that number would cease to exist.

Held-out error, same rows, same split, both models. Measures only.
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


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE TWO GROUPINGS
# ------------------------------------------------------------------ #

def sportSplitCodes(athlete, year, sport):
    """
    A dense id per (athlete, season, SPORT).

    Same construction as athleteSeasonCodes with one more factor. The multiplier
    must exceed any season value, and sport is 0/1, so athlete*20000 + year*2 +
    sport is unique without building a 2D array over 59M rows.
    """
    key = (athlete.astype(np.int64) * 20000
           + year.astype(np.int64) * 2
           + sport.astype(np.int64))
    _, codes = np.unique(key, return_inverse=True)
    return codes.astype(np.int64), int(codes.max()) + 1


# ------------------------------------------------------------------ #
# CHUNK 2 -- ONE ARM OF THE TEST
# ------------------------------------------------------------------ #

def evaluate(course, group, y, n_cells, n_groups, te, sport, label):
    """
    Fit on the training rows, predict the held-out ones.

    ★ COVERAGE IS REPORTED BESIDE THE ERROR. A held-out row is unpredictable if
      its cell or its ability node has no training rows -- and splitting makes
      that WORSE, because each node has fewer races to survive on. Comparing
      error without comparing coverage would flatter whichever model quietly
      predicted fewer rows.
    """
    tr = ~te
    t0 = time.time()
    T = pv.solveSubset(course[tr], group[tr], y[tr], n_cells, n_groups,
                       quiet=True)
    alpha, gcount = pv.refitAlpha(y[tr], T["delta"], course[tr], group[tr],
                                  n_groups)
    solved = T["degree"] >= 2

    cov = solved[course[te]] & (gcount[group[te]] > 0)
    err = y[te][cov] - alpha[group[te][cov]] - T["delta"][course[te][cov]]

    print(f"  {label:<22} error sd {err.std():.6f}   "
          f"covered {int(cov.sum()):,} ({100.0 * cov.mean():.1f}%)   "
          f"[{time.time() - t0:.0f}s]")

    # per sport, since the split could help one and hurt the other
    out = {}
    for code, name in ((0, "XC"), (1, "TF")):
        m = cov & (sport[te] == code)
        if m.sum() > 1000:
            e = y[te][m] - alpha[group[te][m]] - T["delta"][course[te][m]]
            out[name] = (float(e.std()), int(m.sum()))
            print(f"      {name}: {e.std():.6f}  ({int(m.sum()):,} rows)")
    return float(err.std()), float(cov.mean()), out


# ------------------------------------------------------------------ #
# CHUNK 3 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(pack_path, frac=0.10):
    import pair_all as pa

    D = pa.prepare(pack_path)
    if "sport" not in D:
        print("[split] pack has no 'sport' column -- cannot separate")
        return

    course, y, sport = D["course"], D["y"], D["sport"]
    te = pv.splitByRow(y.size, frac=frac, seed=1)
    print(f"[split] {int(te.sum()):,} held-out rows "
          f"({100.0 * (sport == 0).mean():.0f}% XC / "
          f"{100.0 * (sport == 1).mean():.0f}% TF)")

    print("\n[split] MERGED -- one ability per (athlete, season)")
    m_sd, m_cov, m_by = evaluate(course, D["group"], y, D["n_cells"],
                                 D["n_groups"], te, sport, "merged")

    print("\n[split] SPLIT -- one ability per (athlete, season, sport)")
    g2, n2 = sportSplitCodes(D["athlete"], D["year"], sport)
    print(f"        {n2:,} ability nodes vs {D['n_groups']:,} merged "
          f"(+{100.0 * (n2 / D['n_groups'] - 1):.1f}%)")
    s_sd, s_cov, s_by = evaluate(course, g2, y, D["n_cells"], n2, te, sport,
                                 "split")

    print("\n[split] ---- verdict ----")
    print(f"    merged  {m_sd:.6f}   covered {100.0 * m_cov:.1f}%")
    print(f"    split   {s_sd:.6f}   covered {100.0 * s_cov:.1f}%   "
          f"{100.0 * (s_sd - m_sd) / m_sd:+.2f}%")
    for name in ("XC", "TF"):
        if name in m_by and name in s_by:
            a, b = m_by[name][0], s_by[name][0]
            print(f"    {name:<7} {a:.6f} -> {b:.6f}   "
                  f"{100.0 * (b - a) / a:+.2f}%")

    print("\n    A split that predicts better still severs XC from TF: the two")
    print("    would become separate components with independent gauges, and")
    print("    the 0.043 offset between them would cease to be measurable.")


if __name__ == "__main__":
    here = os.path.join(_HERE, "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else os.path.join(here, "packed_XC_TF.npz"))