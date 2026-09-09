"""Track is the zero, the curve gives its level back, and thin courses shrink.

Owner, 2026-09-09: "how do we make average track difficulty default 0.0?"

★ THE ZERO WAS THE CORPUS, NOT A SURFACE. joint_golive anchored difficulty on
  the results-weighted mean over BOTH sports at once, so the reference sat
  wherever the mixture of XC and TF happened to put it -- and TF has more
  results per cell, which dragged it toward the track and left every XC
  course reading positive against nothing in particular. Anchored on the TF
  cells alone a track is 0.0 by construction and the scale means "what you
  would run on a track".

★ WHICH MAKES IT FALSIFIABLE. Coaching practice puts the same distance on
  grass at x1.06 of a track (x1.03 firm, x1.08 hilly, x1.10 muddy), so the XC
  mean has a predicted value that does not come from this corpus. The run
  PRINTS it and compares, instead of the number being imposed.

    python tests/test_track_anchor.py
"""
import io
import math
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
GL = io.open(os.path.join(ROOT, "engine", "joint_golive.py"),
             encoding="utf-8").read()
JS = io.open(os.path.join(ROOT, "engine", "joint_solve.py"),
             encoding="utf-8").read()
RJ = io.open(os.path.join(ROOT, "engine", "run_joint.py"),
             encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 1. the difficulty zero is a track --------------------------------- #
blk = GL[GL.index("the difficulty, display-anchored"):
         GL.index("difficulty = np.where(solved")]
ok("ref = solved & is_tf" in blk,
   "the anchor must be the TF cells, not every solved cell")
ok("np.average(raw[ref], weights=w[ref])" in blk,
   "and it must still be results-weighted within them")
# ⚠ A CELL BELONGS TO ONE SPORT, but it has to be derived from the rows that
#   raced there -- there is no sport column on a cell.
ok("tf_rows" in blk and "rows_per_cell * 0.5" in blk,
   "which cells are track must come from the rows, by majority")

# ! AN XC-ONLY PACK HAS NO TRACK TO ANCHOR ON. Dividing by nothing there
#   would take down go-live on a diagnostic run.
ok("no track cells in this pack" in blk,
   "an XC-only pack must fall back, not crash")
ok("np.average(raw[solved], weights=w[solved])" in blk,
   "...to the old both-sports anchor")

# ---- 2. and the scale announces when it is wrong ----------------------- #
ok("XC_TRACK_GAP" in blk,
   "the expected grass cost must be printed beside what XC actually came out "
   "as -- imposing it would make the check circular")
ok("expm1" in blk and "expected about" in blk,
   "printed as a percentage, which is the unit the ladder is quoted in")
ok(re.search(r"abs\(off\) > 0\.0[123]", blk),
   "and a loud line past a few points -- 3 is a third of the whole "
   "firm-to-muddy ladder")


# ---- 3. the curve gives its level back, at the right grain ------------- #
import joint_solve as js                                       # noqa: E402


class _D:
    n = 6
    n_ath = 2
    has_curve = True
    athlete = np.array([0, 0, 0, 1, 1, 1])
    w0 = np.array([1., .5, 0., 1., .5, 0.])
    w1 = 1 - w0
    k0 = np.array([0, 0, 1, 2, 2, 3])
    k1 = k0 + 1


d = _D()
b = {"c": np.array([0.10, 0.20, -0.30, -0.10, 0.00])}
f = d.w0 * b["c"][d.k0] + d.w1 * b["c"][d.k1]
lvl = js.curveLevelPerAthlete(b, d, np.ones(6))
ok(np.allclose(lvl, [f[:3].mean(), f[3:].mean()]),
   f"each athlete-season's OWN mean, got {lvl}")

# ⚠ amp SCALES THE CURVE PER ATHLETE-SEASON and must be carried, or a
#   high-amplitude athlete gets the wrong level handed back.
lvl2 = js.curveLevelPerAthlete(b, d, np.array([2., 2., 2., 1., 1., 1.]))
ok(np.allclose(lvl2, [2 * f[:3].mean(), f[3:].mean()]),
   f"amp must be honoured, got {lvl2}")
ok(np.allclose(js.curveLevelPerAthlete({}, d, 1.0), 0.0),
   "a design with no curve returns zeros rather than raising")

# ! PER ATHLETE-SEASON REPLACES THE POOL-WIDE FOLD, it does not stack on it.
#   Doing both would count the level twice.
tail = JS[JS.index('out["ability_raw"]'):JS.index('out["curve"] = c')]
ok('out["ability"] = b["a"] + lvl' in tail,
   "centre_curve must REPLACE the pool-mean fold, not add to it")
ok("if centre_curve:" in tail, "and only when asked")


# ---- 4. shrinkage reaches both sports ---------------------------------- #
ok('"--tau-max"' in RJ, "the cap must be settable per sport, not TF only")
ok("args.tau_tf_max = None" in RJ,
   "the general form must supersede --tau-tf-max, or two mechanisms fight")
ok('ap.error("--tau-max wants XC,TF' in RJ,
   "a malformed cap must be refused, not silently half-applied")


# ---- 5. the anchor audit runs every time ------------------------------- #
PIPE = io.open(os.path.join(ROOT, "deploy", "run_pipeline.sh"),
               encoding="utf-8").read()
ok("08c_anchor_check" in PIPE, "the audit must be a pipeline step")
ok("--pct 1" in PIPE,
   "and sampled -- the full scan is a per-row recomputation over 191M rows")
# ⚠ A DIAGNOSTIC MUST NEVER FAIL THE RUN.
i = PIPE.index("08c_anchor_check")
ok("|| true" in PIPE[i:i + 260],
   "a report that can abort the pipeline is a gate, and this is not one")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
