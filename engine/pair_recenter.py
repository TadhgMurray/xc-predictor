"""
pair_recenter.py -- recover the XC-vs-TF gap from a split solve.

THE PROBLEM
    Fitting a per-athlete sport offset predicts better -- held-out 0.045117 vs
    0.046259, TF alone -3.9% -- but it makes the XC-vs-TF difficulty level
    unidentifiable. Measured directly, the operator's eigenvalue along the
    "+1 on every TF cell, -1 on every XC cell" direction:

        K = infinity (shared)   90.81
        K = 5                   60.46
        K = 1                   26.75
        K = 0   (full split)     0.00

    Every unit of freedom given to beta comes straight out of the level. A world
    where every runner is 4% slower on grass and a world where grass courses are
    4% harder produce identical times.

★ SO CONSTRAIN THE MEAN, DO NOT PENALISE IT. The assumption that identifies the
  gap is that specialisation AVERAGES OUT across athletes -- the typical runner
  has no sport preference. Ridge shrinks beta toward zero but never forces its
  MEAN to zero, so a uniform component survives and keeps trading against the
  level. Centring beta imposes exactly the assumption, and nothing more:

        beta_g -> beta_g - bbar
        delta_c -> delta_c + bbar * s_c          (s_c = +-0.5 by the cell's sport)
        alpha_g -> alpha_g - bbar * sbar_g

  Every prediction is unchanged -- this is a reparameterisation, not a refit --
  but the mean offset now sits in the difficulties where it belongs.

★ WHAT IS STILL ASSUMED. Nothing in the data proves the average athlete is
  sport-neutral. But the alternative is worse: minimum-norm conjugate gradient
  leaves delta orthogonal to the sport direction, which silently asserts XC and
  TF have EQUAL mean difficulty. That is certainly false. This assumption is at
  least stated.

Measures and reports. Writes nothing.
"""

import os
import sys

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


def cellSport(course, sport, n_cells):
    """
    +-0.5 per CELL. A cell belongs to exactly one sport -- XC and TF venue keys
    are namespaced -- so this is well defined and a cell never mixes.
    """
    s = sport.astype(np.float64) - 0.5
    total = np.bincount(course, weights=s, minlength=n_cells)
    count = np.maximum(np.bincount(course, minlength=n_cells), 1)
    return total / count


def meanOffset(beta, sc, group, n_groups):
    """
    The population mean sport offset, weighted by how much each athlete-season
    actually identifies its own beta.

    ★ WEIGHTED BY sum(sc^2), NOT UNWEIGHTED. An athlete with ten races in each
      sport pins their offset precisely; one with a single TF race barely
      constrains it and their beta is mostly noise. An unweighted mean would let
      millions of near-arbitrary betas outvote the athletes who actually carry
      the information.
    """
    w = np.bincount(group, weights=sc * sc, minlength=n_groups)
    ok = w > 0
    return float(np.average(beta[ok], weights=w[ok])), int(ok.sum())


# ★ THE MEASURED CONSTANT THAT REPLACES A CONFOUNDED ESTIMATE, when set.
#
#   The solve's own bbar is a weighted mean over athlete-season betas, and it
#   DRIFTS: -0.0392 in the handoff era, -0.053 by 2026-08, while the
#   growth-free sandwich estimator (scripts/measure_sport_gap.py) held at
#   -0.039. Whatever beta soaks up beyond true specialisation -- growth
#   curvature, season composition -- lands in the applied gap. The
#   reparameterisation below is prediction-preserving for ANY constant, so
#   applying a measured one instead is algebraically free.
#
#   THE LOOP IS CLOSED THROUGH A FILE NOW (2026-08-27), not a hand-pinned
#   constant. data/sport_gap_bbar.json carries:
#       measured_bbar   what the NEXT solve should apply (X + D)
#       applied_bbar    what the LAST solve actually applied (recordApplied,
#                       called by the golive after recentring)
#       D               the last measured gap error, for telemetry
#   The nightly sequence: 08 golive applies measured_bbar (or the solve's
#   own estimate when the file is absent) and records applied_bbar; the gap
#   step after rankings runs measure_sport_gap --emit, which reads
#   applied_bbar, measures D on the fresh ratings, and writes
#   measured_bbar = applied + D (delta_bbar = +D, not 2D -- D is the error
#   in the GAP and each sport carries half). D converging toward 0 across
#   nights is the loop working. Delete the file to revert to the solve's
#   own estimate.

_GAP_JSON = os.path.join(_HERE, "data", "sport_gap_bbar.json")


def _loadMeasured():
    import json
    try:
        with open(_GAP_JSON, encoding="utf-8") as f:
            v = json.load(f).get("measured_bbar")
    except (OSError, ValueError):
        return None
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# recordApplied : the golive calls this with the bbar it ACTUALLY applied,
#   so the measure step can compute measured = applied + D without reading
#   logs. Merges into the json; never drops measured_bbar.
def recordApplied(bbar):
    import datetime
    import json
    doc = {}
    try:
        with open(_GAP_JSON, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        pass
    doc["applied_bbar"] = float(bbar)
    doc["applied_date"] = datetime.date.today().isoformat()
    os.makedirs(os.path.dirname(_GAP_JSON), exist_ok=True)
    with open(_GAP_JSON, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)


MEASURED_BBAR = _loadMeasured()


def recenter(delta, alpha, beta, sc, group, sport, course, n_cells, n_groups,
             bbar=None):
    """
    Move the mean offset out of beta and into delta. Predictions unchanged.
    Pass bbar to apply a measured constant instead of the solve's estimate;
    n_ident still reports how many athlete-seasons the estimate had.
    """
    bbar_solve, n_ident = meanOffset(beta, sc, group, n_groups)
    if bbar is None:
        bbar = bbar_solve
    s_cell = cellSport(course, sport, n_cells)

    sbar = (np.bincount(group, weights=sport.astype(np.float64) - 0.5,
                        minlength=n_groups)
            / np.maximum(np.bincount(group, minlength=n_groups), 1))

    return (delta + bbar * s_cell,
            alpha - bbar * sbar,
            beta - bbar,
            bbar, n_ident)


def report(delta_before, delta_after, s_cell, solved, bbar, n_ident):
    xc = solved & (s_cell < 0)
    tf = solved & (s_cell > 0)
    print(f"\n[recentre] mean offset bbar = {bbar:+.5f} "
          f"(from {n_ident:,} athlete-seasons that race both)")
    print("\n    difficulty by sport      before        after")
    for name, m in (("XC", xc), ("TF", tf)):
        print(f"    {name}  {int(m.sum()):>8,} cells  "
              f"{delta_before[m].mean():+11.5f} {delta_after[m].mean():+12.5f}")
    gap_b = delta_before[tf].mean() - delta_before[xc].mean()
    gap_a = delta_after[tf].mean() - delta_after[xc].mean()
    print(f"\n    TF - XC gap:  {gap_b:+.5f}  ->  {gap_a:+.5f}")
    print("    the shared-ability solve put this near -0.043")


def main(pack_path, frac=0.10):
    import pair_all as pa

    D = pa.prepare(pack_path)
    if "sport" not in D:
        print("[recentre] pack has no 'sport' column")
        return

    course, group, y = D["course"], D["group"], D["y"]
    sport, n_cells, n_groups = D["sport"], D["n_cells"], D["n_groups"]

    te = pv.splitByRow(y.size, frac=frac, seed=1)
    tr = ~te
    sc_tr = pe.sportCentered(sport[tr], group[tr], n_groups)

    print("[recentre] solving with per-athlete sport offsets (K = 0)...")
    T = pv.solveSubset(course[tr], group[tr], y[tr], n_cells, n_groups,
                       quiet=False, sc=sc_tr, ridge=0.0)
    alpha, beta, count = pv.refitAlphaBeta(y[tr], T["delta"], course[tr],
                                           group[tr], n_groups, sc_tr, 0.0)

    s_cell = cellSport(course[tr], sport[tr], n_cells)
    solved = T["degree"] >= 2

    d2, a2, b2, bbar, n_ident = recenter(T["delta"], alpha, beta, sc_tr,
                                         group[tr], sport[tr], course[tr],
                                         n_cells, n_groups)
    report(T["delta"], d2, s_cell, solved, bbar, n_ident)

    # ★ THE CHECK THAT MATTERS: predictions must be IDENTICAL. If held-out error
    #   moves at all, the algebra is wrong and the "gap" is an artefact.
    s_all = sport.astype(np.float64) - 0.5
    tot = np.bincount(group[tr], weights=s_all[tr], minlength=n_groups)
    cnt = np.maximum(np.bincount(group[tr], minlength=n_groups), 1)
    sc_te = s_all[te] - (tot / cnt)[group[te]]
    ok = solved[course[te]] & (count[group[te]] > 0)

    for tag, dd, aa, bb in (("before", T["delta"], alpha, beta),
                            ("after ", d2, a2, b2)):
        pred = (aa[group[te][ok]] + bb[group[te][ok]] * sc_te[ok]
                + dd[course[te][ok]])
        print(f"    held-out {tag}: {(y[te][ok] - pred).std():.6f}")


if __name__ == "__main__":
    here = os.path.join(_HERE, "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else os.path.join(here, "packed_XC_TF.npz"))