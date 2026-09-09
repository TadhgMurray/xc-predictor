"""One scale that means fitness: the sport level is refused, not estimated.

Owner, 2026-09-09, after "no one does this" killed every cross-sport
identification: "so how do we get one scale that actually means fitness".

★ SPORT IS SEASON. Cross country is autumn, track is spring, and nobody
  races both close enough together for fitness to be held constant. So "track
  courses are easier" and "athletes are fitter in spring" are the SAME
  SENTENCE in this corpus. No estimator can split them -- which is why two
  months of trying to measure a sport gap produced a closure error of
  +0.01440 instead of a number.

★ BUT IT IS EXACTLY ONE SCALAR. Relative difficulties inside autumn are
  pinned (athletes race several grass courses each fall), relative
  difficulties inside spring are pinned, and the curve's shape inside each
  window is pinned. The only thing the data cannot see is the mean offset
  between the two sets -- and recentreLevels is where that number already
  lives, banked in mu.

★ --merge-sports ASSERTS IT IS ZERO. The per-sport means of difficulty AND
  of the race effect are dropped rather than banked, so the two sports'
  average course is equal by construction on the shared ruler (targetFor
  already ignores the sport: hs_m is 5000m in both). Then autumn-to-spring
  movement has nowhere to go but the form curve -- which is fitness.

⚠ AN ASSUMPTION REPLACING FOUR IMPLICIT ONES, not a discovery. Today the
  same scalar is set by XCP_WINTER_GAIN=0.02 pinning the curve at weight
  100, entangled with beta, the ridge and mu.

    python tests/test_merge_sports.py
"""
import io
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
JS = io.open(os.path.join(ROOT, "engine", "joint_solve.py"),
             encoding="utf-8").read()
RJ = io.open(os.path.join(ROOT, "engine", "run_joint.py"),
             encoding="utf-8").read()

import joint_solve as js                                       # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


class _D:
    n_group = 2
    group_of_cell = np.array([0, 0, 0, 1, 1, 1])
    n_cell, n_race = 6, 4
    race = np.array([0, 1, 2, 3])
    group_row = np.array([0, 0, 1, 1])


def _fresh():
    return {"d": np.array([1., 2., 3., 10., 11., 12.]),
            "mu": np.zeros(2),
            "u": np.array([0.5, -0.5, 2., 1.]),
            "a": np.zeros(3)}


d = _D()
normal = js.recentreLevels(_fresh(), d, merge=False)
merged = js.recentreLevels(_fresh(), d, merge=True)

# ---- 1. what MUST be identical ----------------------------------------- #
#   The whole claim is that only the between-sport level changes. If merging
#   also moved the relative difficulties, it would be re-ranking courses
#   rather than declining to rank sports.
ok(np.allclose(normal["d"], merged["d"]),
   f"relative course difficulties must be untouched: {normal['d']} vs "
   f"{merged['d']}")
ok(np.allclose(normal["u"], merged["u"]),
   "per-race effects must be untouched too")

# ---- 2. and what must not ---------------------------------------------- #
ok(np.allclose(merged["mu"], 0.0),
   f"merged must keep NO sport level, got {merged['mu']}")
ok(not np.allclose(normal["mu"], 0.0),
   f"the fixture is pointless if the normal path finds no level: "
   f"{normal['mu']}")

# ⚠ BOTH HALVES. u is the per-race effect and its per-sport mean is a sport
#   level by another name; the first cut guarded only the difficulty half and
#   left mu at [0, 1.5] on a fixture built to come out [0, 0].
i_u = JS.index('b["u"] = np.where(seen')
tail = JS[i_u:i_u + 900]
ok("if not merge:" in tail,
   "the race-effect mean must be guarded by merge as well, or the sport "
   "level comes back through the back door")

# ---- 3. it is off unless asked, and it brings its implications ---------- #
ok('"--merge-sports"' in RJ, "the flag must exist")
ok("merge_sports=False" in JS,
   "solveJoint must default to the old behaviour exactly")
ok("merge=merge_sports" in JS, "and thread it to recentreLevels")

# ! ALL FOUR TOGETHER OR NONE. A run carrying three of them is measuring
#   something nobody can name.
i = RJ.index("if args.merge_sports:")
block = RJ[i:i + 1200]
for implied in ("no_sport_offset = True", "winter_gain = 0.0",
                "curve_gap = 0.0"):
    ok(implied in block,
       f"--merge-sports must also set {implied}: beta, the winter-gain pin "
       f"and the curve-gap penalty are all the same assumption")
ok("sport_gap_delta = 0.0" in block,
   "--sport-gap-delta is meaningless with no sport level and must be "
   "refused, not silently applied")
ok("MERGED SPORTS" in RJ,
   "a run that carries this must say so -- it produces a different board "
   "from the same data")

# ---- 4. the pipeline does not turn it on by itself --------------------- #
PIPE = io.open(os.path.join(ROOT, "deploy", "run_pipeline.sh"),
               encoding="utf-8").read()
ok("--merge-sports" not in PIPE,
   "run_pipeline must not acquire this silently -- it changes what a rating "
   "means")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
