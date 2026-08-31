"""
global_solve.py -- PROTOTYPE. One sparse least-squares system for abilities
and course difficulties at once, instead of alternating between them.

    python engine/global_solve.py --pool hs_m --since 2019-08-01 --out hs_m.npz
    python engine/holdout_eval.py --cut 2024-08-01 --difficulty hs_m.npz

⚠ THIS DOES NOT REPLACE speed_ratings.py AND IS NOT WIRED INTO ANYTHING. It
  exists to answer one question with a number: does solving globally beat
  alternating, on races neither has seen? Score it with holdout_eval.py.

★ WHAT IT ACTUALLY CHANGES, AND WHAT IT DOES NOT.
    ln(norm) = ln(ability_a) + ln(1 + d_c) + noise
  is the SAME model speed_ratings fits. Coordinate descent and a Krylov
  solver are two ways to minimise the same objective; where they differ is
  path, conditioning and stopping, not the answer they are aiming at. So a
  large difference in score means one of them is not reaching the optimum,
  and a small one means the estimator was never the binding constraint.

⚠ LSQR/LSMR ARE ITERATIVE TOO. This is not "stop iterating, solve directly":
  direct factorisation of a 60M x 5M sparse system is not feasible and no
  library will do it. LSMR is Golub-Kahan bidiagonalization -- a Krylov
  method. The real claim is that a Krylov iteration on the whole system has
  better-understood convergence than damped alternation with a patience
  counter, which is true, and worth measuring rather than asserting.

⚠ AND IT DOES NOT FIX DISCONNECTED COMPONENTS. If two blocks of the race
  graph never meet, the system is RANK-DEFICIENT and their relative level is
  unidentifiable. LSMR returns the minimum-norm solution -- it does not
  balance them, it silently picks one of infinitely many and reports success.
  The claim that a global solver "ensures regional bubbles are balanced" is
  false, and believing it is more dangerous than the alternation it replaces,
  because alternation at least leaves the drift visible. Connectivity is a
  property of the DATA. engine/diag_connectivity.py measures it and
  linkage_check.shrinkByLinkage prices it; neither becomes unnecessary here.

  ⚠⚠ AND THE RIDGE HIDES IT. Measured on a severed synthetic graph: with no
     penalty the two blocks come back 0.156 apart, which is the error
     announcing itself. With the per-cell ridge they come back 0.011 apart --
     not because anything was balanced, but because both were pulled to the
     same prior. The reassuring number is the dangerous one. Read
     diag_connectivity BEFORE trusting any difficulty from a sparse region,
     whichever estimator produced it.

! THE RIDGE IS EXPLICIT PENALTY ROWS ON THE COURSE BLOCK, NOT LSMR's `damp`,
  AND THE DIFFERENCE IS NOT COSMETIC. `damp` adds damp^2 * ||x||^2 over EVERY
  parameter -- all five million athletes included -- penalising a 10,000-row
  course exactly as hard as a 30-row one. speed_ratings' RIDGE_LAMBDA lives
  in a denominator, `sums / (wsum + lambda)`, so it shrinks each cell in
  proportion to how little data that cell has, and a well-measured cell is
  untouched.

  Its own comment says what it is: "arithmetically identical to `lambda`
  extra observations pinned at zero". So that is what this appends -- one row
  per course cell, weight sqrt(RIDGE_LAMBDA), target zero -- which reproduces
  the denominator form exactly and leaves the athlete block unpenalised.

  ⚠ MEASURED, BECAUSE THE FIRST VERSION OF THIS FILE GOT IT WRONG. With
    damp=5.0 on a synthetic world whose blocks were fully connected, the
    recovered difficulties were off by a block-to-block gap of 0.1292 --
    as bad as severing the graph entirely (0.1268). With damp=0 and the
    penalty rows, the same connected world comes back at 0.0013. A uniform
    ridge does not shrink a solution, it bends it.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

# The SAME constant speed_ratings uses, applied the same way -- see the
# docstring. Kept as a default rather than imported so the prototype can be
# swept without touching the engine.
RIDGE_LAMBDA = 25.0

# LSMR's own damp stays OFF. It penalises every parameter uniformly, which is
# a different estimator, and a wrong one here.
DEFAULT_DAMP = 0.0

MAX_ITER = 400


_SQL = """
    SELECT r.person_id, r.meet_id, r.div_id, r.normalized_time
    FROM   {table} r
    WHERE  r.normalized_time IS NOT NULL
      AND  r.normalized_time > 0
      AND  r.person_id IS NOT NULL
      {since}
"""


def _codes(values):
    lookup, out = {}, np.empty(len(values), dtype=np.int64)
    for i, v in enumerate(values):
        c = lookup.get(v)
        if c is None:
            c = lookup[v] = len(lookup)
        out[i] = c
    return out, lookup


def main():
    ap = argparse.ArgumentParser(description="Global sparse solve (prototype).")
    ap.add_argument("--sport", default="XC", choices=["XC", "TF"])
    ap.add_argument("--since", default=None, help="YYYY-MM-DD")
    ap.add_argument("--damp", type=float, default=DEFAULT_DAMP,
                    help="LSMR uniform damp. Leave at 0; use --ridge.")
    ap.add_argument("--ridge", type=float, default=RIDGE_LAMBDA,
                    help=f"per-cell ridge, as speed_ratings' RIDGE_LAMBDA "
                         f"(default {RIDGE_LAMBDA})")
    ap.add_argument("--iters", type=int, default=MAX_ITER)
    ap.add_argument("--out", required=True, help="output .npz")
    args = ap.parse_args()

    try:
        import scipy.sparse as sp
        from scipy.sparse.linalg import lsmr
    except ImportError:
        sys.exit("needs scipy: /srv/venv/bin/pip install scipy")

    from database import getConn

    table = "results" if args.sport == "XC" else "results_tf"
    since = "AND r.date >= %(since)s" if args.since else ""
    with getConn() as conn, conn.cursor(name="gsolve") as cur:
        cur.itersize = 200_000
        cur.execute(_SQL.format(table=table, since=since),
                    {"since": args.since})
        person_raw, course_raw, nt = [], [], []
        for pid, meet, div, t in cur:
            person_raw.append(pid)
            course_raw.append((int(meet), int(div)))
            nt.append(float(t))

    nt = np.asarray(nt)
    print(f"[global] {nt.size:,} rows")
    person, pmap = _codes(person_raw)
    course, cmap = _codes(course_raw)
    na, nc = len(pmap), len(cmap)
    print(f"[global] {na:,} athletes, {nc:,} course cells, "
          f"{na + nc:,} parameters")

    # ★ THE DESIGN MATRIX IS TWO ONES PER ROW. Athlete column, course column.
    #   That is the entire model: ln(t) = alpha_a + beta_c. 2 nonzeros per row
    #   means the matrix is ~24 bytes/row, so 60M rows is ~1.4GB -- large but
    #   not the obstacle people expect.
    rows = np.repeat(np.arange(nt.size), 2)
    cols = np.empty(nt.size * 2, dtype=np.int64)
    cols[0::2] = person
    cols[1::2] = na + course
    data = np.ones(nt.size * 2, dtype=np.float64)
    y = np.log(nt)

    # ★ THE RIDGE, AS PSEUDO-OBSERVATIONS ON THE COURSE BLOCK ONLY. One row
    #   per cell, sqrt(lambda) in that cell's column, target zero. In the
    #   normal equations this adds lambda to that cell's diagonal and nothing
    #   else -- which IS `sums / (wsum + lambda)`. The athlete block is left
    #   alone, exactly as in speed_ratings.
    if args.ridge > 0:
        pen_rows = nt.size + np.arange(nc)
        rows = np.concatenate([rows, pen_rows])
        cols = np.concatenate([cols, na + np.arange(nc)])
        data = np.concatenate([data, np.full(nc, np.sqrt(args.ridge))])
        y = np.concatenate([y, np.zeros(nc)])
        n_eq = nt.size + nc
    else:
        n_eq = nt.size
    A = sp.csr_matrix((data, (rows, cols)), shape=(n_eq, na + nc))

    print(f"[global] solving (damp={args.damp}, max {args.iters} iters)...")
    res = lsmr(A, y, damp=args.damp, maxiter=args.iters, show=False)
    x, istop, itn, normr = res[0], res[1], res[2], res[3]
    print(f"[global] istop={istop} after {itn} iterations, "
          f"residual norm {normr:.4f}")

    ability_log = x[:na]
    diff_log = x[na:]

    # ★ THE GAUGE, PINNED THE SAME WAY speed_ratings PINS IT. `damp` alone
    #   fixes a solution only by shrinking both blocks toward zero, which is
    #   an arbitrary gauge, not the engine's. Recentring difficulty to mean
    #   zero and pushing the offset into ability makes "difficulty" mean
    #   "harder than an average course" in BOTH estimators -- without this the
    #   two are not on the same scale and holdout_eval would be comparing
    #   units, not quality.
    counts = np.bincount(course, minlength=nc).astype(float)
    shift = float(np.average(diff_log, weights=counts))
    diff_log -= shift
    ability_log += shift

    difficulty = np.expm1(diff_log)
    course_key = np.array([k for k, _ in
                           sorted(cmap.items(), key=lambda kv: kv[1])],
                          dtype=np.int64)

    np.savez_compressed(args.out, course_key=course_key,
                        difficulty=difficulty, n_rows=nt.size,
                        counts=counts)
    print(f"[global] wrote {args.out}: {nc:,} cells, "
          f"difficulty range [{difficulty.min():+.3f}, "
          f"{difficulty.max():+.3f}]")
    print("[global] score it:  python engine/holdout_eval.py "
          f"--cut YYYY-MM-DD --difficulty {args.out}")


if __name__ == "__main__":
    main()
