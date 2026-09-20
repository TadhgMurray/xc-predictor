#!/usr/bin/env python3
"""
diag_joint_vs_bracket.py -- the two difficulty estimators on ONE gauge.

    python engine/diag_joint_vs_bracket.py
    python engine/diag_joint_vs_bracket.py --show 25

★ WHY (owner, 2026-09-20: "I want as close to a slaney solve with the extra
  things we've decided are good ... DO (b) first, include (a) and measure").
  Option (a) is "publish the joint solve's own course difficulties instead of
  the bracket engine's" -- and the joint solve IS the Slaney model: same
  equation (ability x difficulty x calendar), generalised in every term, fitted
  by penalised least squares because his PyMC sampler does 70,696 rows and this
  corpus has 62,805,298.

⚠⚠ THE COMPARISON IS MEANINGLESS ON TWO DIFFERENT ZEROS, which is the whole
   reason this script exists rather than a correlation printed in a log. The
   joint solve's d is centred by recentreLevels (the group's mean at zero); the
   bracket engine's D under gauge=flat400 is centred on the flat outdoor 400s.
   Subtracting one from the other then measures mostly the gauge. So BOTH are
   re-gauged here onto the same reference class before anything is compared, and
   the shift each one needed is itself reported -- for the joint solve that
   shift is the answer to "how far is its zero from a fixed reference", which is
   the §2 complaint ("every course outside California went negative") stated as
   a number.

! IT COMPARES THE PUBLISHED NUMBERS, NOT THE PREDICTIONS. Which model predicts
  a held-out race better is a different question and already answered by
  scripts/bracket_holdout.py, which scores the joint model on the same sample
  when a fresh ladder dump exists (bracket 0.0419 against joint 0.0515 on
  2026-09-19, though on different coverage: 63% against 89%).

Read-only. Reads the solve file and the pack; writes nothing.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                               # noqa: E402


def _regauge(d, ref, group):
    """d with the reference cells' mean at zero, per group. Returns (d, shift).

    ! THE MEAN, NOT EACH CELL AT ZERO. A post-hoc re-gauge is all that is
      available for the joint solve's d -- holding each reference cell at 0.0
      exactly is a CONSTRAINT and belongs inside its objective, not after it.
      So this is the fairest comparison that does not require re-solving, and it
      is deliberately the weaker of the two pins."""
    d = np.asarray(d, dtype=np.float64).copy()
    shift = np.zeros(int(group.max()) + 1 if group.size else 1)
    for g in range(shift.size):
        m = (group == g) & ref
        if not m.any():
            m = group == g
            if not m.any():
                continue
        shift[g] = float(np.mean(d[m]))
        d[group == g] -= shift[g]
    return d, shift


def main():
    import bracket as bk
    import run_joint as rj
    import track_geometry as tg
    import bracket_engine as be

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", default=rj.buildParser().get_default("pack"))
    ap.add_argument("--npz", default=rj.buildParser().get_default("out"))
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()

    if not os.path.exists(args.npz):
        raise SystemExit(f"no solve file at {args.npz}")
    z = np.load(args.npz, allow_pickle=False)
    have = set(z.files)
    for need in ("delta", "delta_joint", "course_keys"):
        if need not in have:
            raise SystemExit(
                f"{args.npz} has no {need}. It must be a --difficulty bracket "
                f"solve: the joint delta is kept as delta_joint only there.")
    d_br = np.asarray(z["delta"], dtype=np.float64)
    d_jt = np.asarray(z["delta_joint"], dtype=np.float64)
    keys = [str(k) for k in z["course_keys"]]
    votes = (np.asarray(z["bracket_votes"], dtype=np.float64)
             if "bracket_votes" in have else np.ones(len(keys)))

    cols, _npz2 = bk.loadInputs(args.pack, None)
    pack_keys = {str(k): i for i, k in enumerate(cols["course_keys"])}
    if "track_length" not in cols:
        raise SystemExit("the pack carries no track geometry; rebuild it "
                         "(speed_ratings.attachCourseGeometry)")
    ref_base = np.asarray(tg.flatOutdoor400Mask(
        cols["track_length"], cols.get("track_type"), cols["track_indoor"]),
        dtype=bool)
    # the solve's cells carry an era suffix; the geometry is per base key
    ref = np.zeros(len(keys), dtype=bool)
    for i, k in enumerate(keys):
        base = k.rpartition("@e")[0] if "@e" in k else k
        j = pack_keys.get(base)
        if j is not None:
            ref[i] = ref_base[j]
    group = np.array([be.priorGroupOfKeys([k])[0] for k in keys],
                     dtype=np.int64)

    live = votes > 0
    print(f"\n  {len(keys):,} cells, {int(live.sum()):,} with votes, "
          f"{int((ref & live).sum()):,} in the reference class "
          f"(flat outdoor 400)")
    if not (ref & live).any():
        raise SystemExit("no reference cell has a vote: nothing to gauge on")

    br, sh_br = _regauge(d_br, ref & live, group)
    jt, sh_jt = _regauge(d_jt, ref & live, group)
    print("\n  the shift each one needed to sit on the reference class "
          "(per group):")
    print(f"    {'group':<8}{'bracket':>10}{'joint':>10}")
    for g, name in enumerate(be.PRIOR_GROUP_NAMES):
        if (group == g).any():
            print(f"    {name:<8}{100 * sh_br[g]:>+9.2f}%{100 * sh_jt[g]:>+9.2f}%")
    print("    ! the bracket shift is ~0 by construction under gauge=flat400. "
          "The JOINT shift is\n      how far its own zero sits from a fixed "
          "reference -- the §2 complaint, as a number.")

    m = live
    diff = br[m] - jt[m]
    print(f"\n  on the same gauge, over {int(m.sum()):,} voted cells:")
    print(f"    correlation            {np.corrcoef(br[m], jt[m])[0, 1]: .4f}")
    print(f"    median |bracket-joint| {100 * float(np.median(np.abs(diff))):.2f}%")
    print(f"    p95    |bracket-joint| "
          f"{100 * float(np.percentile(np.abs(diff), 95)):.2f}%")
    for label, arr in (("bracket", br[m]), ("joint", jt[m])):
        neg = float((arr < 0).mean())
        print(f"    {label:<8} median {100 * float(np.median(arr)):+.2f}%   "
              f"negative {100 * neg:.1f}%   sd {100 * float(arr.std()):.2f}%")
    print("    ! 'negative' is the owner's own test. On a fixed reference a "
          "course EASIER than a\n      flat outdoor 400 should be rare, so a "
          "large share negative is the symptom\n      that started this, and "
          "it is now measurable per estimator.")

    idx = np.argsort(-np.abs(br - jt) * live)
    print(f"\n  the {args.show} cells the two most disagree about:")
    print(f"    {'cell':<34}{'bracket':>10}{'joint':>10}{'diff':>9}{'votes':>9}")
    for i in idx[:args.show]:
        print(f"    {keys[i][:34]:<34}{100 * br[i]:>+9.2f}%"
              f"{100 * jt[i]:>+9.2f}%{100 * (br[i] - jt[i]):>+8.2f}%"
              f"{votes[i]:>9.0f}")
    print("\n  ! nothing was written. Which estimator PREDICTS better is a "
          "different question:\n    scripts/bracket_holdout.py answers it, and "
          "needs a ladder dump built against\n    this same pack to do the "
          "same-rows comparison.")


if __name__ == "__main__":
    main()
