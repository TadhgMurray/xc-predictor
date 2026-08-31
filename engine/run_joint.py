"""
run_joint.py -- drive joint_solve over the packed cache.

    python engine/run_joint.py                      # solve, write, compare
    python engine/run_joint.py --outer 8 --probes 200
    python engine/run_joint.py --no-robust          # plain least squares

★ NON-DESTRUCTIVE BY CONSTRUCTION. Writes engine/data/joint_difficulty.npz and
  touches NOTHING else -- not results.speed_rating, not course_difficulties,
  not pair_difficulty.npz. Run it beside the existing solve and compare; it
  cannot change what the site serves.

THE RESPONSE IS THE EXISTING ONE. buildResponse from pair_engine is reused
unchanged, so this and 08_golive see identical y. Any difference in the answer
is the MODEL, not the input.

WHAT IS AND IS NOT WIRED YET
  ON   ability (athlete-season), difficulty, race-day effect, asymmetric
       robust weights, hierarchical tau2 by sport, posterior variance.
  OFF  the tilt. h must be evaluated at a RATING, and rating needs the pool
       mean per row from pair_ratings' pool machinery. One call to wire, but
       it is not verified here, and a wrong pool mean would silently tilt
       every rating the wrong way. Pass --pool-means <npy> to enable it.
  OFF  the joint spline terms (distance / era / weather). Those need the
       fitters' design rows folded in; the operator already accepts a
       covariate block, this driver does not build one yet.
"""

import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import joint_solve as js                                        # noqa: E402
import pair_engine as pe                                        # noqa: E402


# Purpose:   a race is one running of one cell on one day.
# ★ THE GRAIN IS (cell, date), NOT (meet, division). Weather, mud and how the
#   pace went out are shared by everyone on the course that day, across
#   divisions; and (cell, date) is available in the pack as it stands, where a
#   meet id is not. Coarser than a division, right for conditions.
def raceCodes(course, date):
    key = np.stack([course.astype(np.int64), date.astype(np.int64)], axis=1)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.astype(np.int64), int(inv.max()) + 1


# Purpose:   one shrinkage group per CELL, so tau2 is not a single global
#            constant over middle-school miles and NCAA 8Ks alike.
def cellGroups(course, sport, n_cells):
    if sport is None:
        return np.zeros(n_cells, dtype=np.int64), 1
    s = np.where(np.asarray(sport) > 0, 1, 0).astype(np.int64)
    tot = np.bincount(course, weights=s, minlength=n_cells)
    cnt = np.maximum(np.bincount(course, minlength=n_cells), 1)
    return (tot / cnt >= 0.5).astype(np.int64), 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default=os.path.join(_HERE, "data",
                                                   "packed_XC_TF.npz"))
    ap.add_argument("--out", default=os.path.join(_HERE, "data",
                                                  "joint_difficulty.npz"))
    ap.add_argument("--outer", type=int, default=6)
    ap.add_argument("--probes", type=int, default=64)
    ap.add_argument("--pool-means", default=None,
                    help=".npy of pool mean per row; enables the tilt")
    ap.add_argument("--no-robust", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.pack):
        sys.exit(f"[joint] no pack at {args.pack} -- run 07_pack first")

    print(f"[joint] loading {args.pack}")
    cols = pe.loadPack(args.pack)

    y_all, _form = pe.buildResponse(cols)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    y = y_all[keep]
    course = cols["course"][keep].astype(np.int64)
    athlete, n_groups = pe.athleteSeasonCodes(cols["athlete"][keep],
                                              cols["year"][keep])
    race, n_race = raceCodes(course, cols["date"][keep])
    n_cells = len(cols["course_keys"])
    group, n_grp = cellGroups(course, cols.get("sport"), n_cells)

    print(f"[joint] {y.size:,} rows | {n_groups:,} athlete-seasons | "
          f"{n_cells:,} cells | {n_race:,} races | {n_grp} shrinkage groups")

    pool_means = None
    if args.pool_means:
        pool_means = np.load(args.pool_means)[keep]
        print("[joint] tilt ON (pool means supplied)")

    out = js.solveJoint(
        y, athlete, course, race, group=group, pool_mean_row=pool_means,
        n_outer=args.outer, robust=not args.no_robust,
        tilt=pool_means is not None, n_probe=args.probes, verbose=True)

    delta = out["delta"]
    print(f"\n[joint] sigma {np.sqrt(out['sigma2']):.5f} | race-day sigma_u "
          f"{np.sqrt(out['sigma_u2']):.5f} | tau {np.sqrt(out['tau2'])} ")
    print(f"[joint] {out['n_downweighted']:,} rows down-weighted, 0 dropped")
    print(f"[joint] cell SE: median {np.median(out['cell_se']):.4f}, "
          f"p95 {np.percentile(out['cell_se'], 95):.4f}")

    np.savez(args.out, delta=delta, cell_se=out["cell_se"],
             cell_var=out["cell_var"], race_effect=out["race_effect"],
             sigma2=out["sigma2"], sigma_u2=out["sigma_u2"], tau2=out["tau2"],
             course_keys=np.array([str(k) for k in cols["course_keys"]]))
    print(f"[joint] wrote {args.out}")

    # ★ THE COMPARISON IS THE POINT. Same response, same cells -- so a large
    #   move is the race-day term and the robust weights, and it should be
    #   biggest exactly where the old engine's SE was least trustworthy.
    old_path = os.path.join(os.path.dirname(args.out), "pair_difficulty.npz")
    if os.path.exists(old_path):
        with np.load(old_path, allow_pickle=False) as old:
            if "difficulty_raw" in old.files:
                a = np.log1p(old["difficulty_raw"])
                m = np.isfinite(a) & np.isfinite(delta) & (a != 0)
                if m.sum() > 100:
                    d = delta[m] - a[m]
                    print(f"\n[joint] vs pair_difficulty over {int(m.sum()):,} "
                          f"cells: median move {np.median(np.abs(d)):+.4f}, "
                          f"p95 {np.percentile(np.abs(d), 95):.4f}, "
                          f"corr {np.corrcoef(delta[m], a[m])[0, 1]:.4f}")


if __name__ == "__main__":
    main()
