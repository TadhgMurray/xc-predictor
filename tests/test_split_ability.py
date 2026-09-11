"""One ability per sport-season, and the free scalar written down once.

Owner, 2026-09-09: "fitness should have a mean of 0 in season but fitness
needs to apply to course difficulty" -- then, on the scalar that is left:
"how do we measure the free scalar?"

★ IT CANNOT BE MEASURED. Sport is season: XC is autumn, track is spring, and
  nobody races both close enough together for fitness to be held constant. So
  the two halves share no data and their relative level is a DEFINITION.
  XC_TRACK_GAP is that definition, in one place, instead of being spread
  across beta, mu, the winter-gain pin and the go-live band shift.

★ WHY beta COMES OUT. Keyed (athlete, year) a single ability had to serve an
  autumn 5k and a spring 800, and beta -- a per-athlete sport offset -- was
  bolted on to patch the difference it could not represent. Split by sport
  there is nothing left to patch, and the autumn-to-spring gain becomes the
  difference between two abilities, which REACHES THE RATING because the
  rating is the ability. Nothing is deleted at go-live.

    python tests/test_split_ability.py
"""
import io
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
RJ = io.open(os.path.join(ROOT, "engine", "run_joint.py"),
             encoding="utf-8").read()
JS = io.open(os.path.join(ROOT, "engine", "joint_solve.py"),
             encoding="utf-8").read()

import pair_engine as pe                                       # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 1. the key ------------------------------------------------------- #
ath = np.array([1, 1, 1, 1, 2, 2])
yr = np.array([2024, 2024, 2024, 2025, 2024, 2024])
sp = np.array([0, 0, 1, 0, 0, 1])                    # 0 = XC, 1 = TF

old_c, old_n = pe.athleteSeasonCodes(ath, yr)
new_c, new_n = pe.athleteSeasonCodes(ath, yr, sp)

# ! THE OLD KEY, BIT FOR BIT, when no sport is passed. Every other caller in
#   the tree relies on it.
ok(old_n == 3 and list(old_c) == [0, 0, 0, 1, 2, 2],
   f"sport=None must reproduce the old key exactly: {old_c}, {old_n}")
ok(new_n == 5, f"split by sport gives 5 abilities here, got {new_n}")

# ⚠ THE COLLISION. year occupies the low four digits of the key, so folding
#   sport in by ADDITION would make athlete 1's TF 2024 the same code as
#   their XC 2025 -- one athlete's track season silently merged with the
#   next autumn.
i_tf24, i_xc25 = 2, 3
ok(new_c[i_tf24] != new_c[i_xc25],
   f"TF 2024 collided with XC 2025: both are code {new_c[i_tf24]}")
ok(len(set(new_c[:4])) == 3,
   "athlete 1 has XC24, TF24 and XC25 -- three distinct abilities")


# ---- 2. beta comes out with it ---------------------------------------- #
i = RJ.index("if args.split_ability:")
block = RJ[i:i + 700]
ok("args.no_sport_offset = True" in block,
   "beta patched a SHARED ability; with none left to share it would be "
   "fitting the sport level twice")
ok("SPLIT ABILITY" in RJ, "a run that carries it must say so")

# ! EVERY DESIGN, INCLUDING THE HOLDOUT'S TWO. A holdout scored on a
#   differently-keyed ability is scoring a different model from the one that
#   goes live.
ok(RJ.count("split_ability=args.split_ability") == 3,
   f"all three buildDesign call sites must pass it, found "
   f"{RJ.count('split_ability=args.split_ability')}")
_sig = RJ[RJ.index("def buildDesign("):RJ.index('"""', RJ.index("def buildDesign("))]
ok("split_ability=False" in _sig,
   "buildDesign must take it as a parameter -- args is not in scope inside "
   "it, and reaching for args there is a NameError at run time")


# ---- 3. the scalar is one number, and it is the researched one --------- #
ok("XC_TRACK_GAP" in JS, "the free scalar must exist, named, in one place")
val = float(RJ.join([]) or 0) if False else None
import re                                                      # noqa: E402
val = float(re.search(r"XC_TRACK_GAP = ([\d.]+)", JS).group(1))
ok(abs(val - math.log(1.06)) < 5e-5,
   f"XC_TRACK_GAP is {val}, expected ln(1.06) = {math.log(1.06):.4f} -- the "
   f"long-standing coaching conversion for grass at the same distance")

# ⚠ A CONSTANT, NOT A FUNCTION OF ABILITY, AND THAT WAS CHECKED. The surface
#   cost is a per-step energy loss and stays a roughly constant FRACTION of
#   running economy across speeds. Course-to-course variation (3% firm to 10%
#   muddy) already lives in course_difficulties; putting it here too would
#   count it twice.
# ! COMMENT MARKERS AND LINE WRAPS STRIPPED FIRST. The sentence is split
#   across "vary with\n#   ability", so a raw substring search fails on prose
#   that is present and correct.
_head = JS[JS.index("XC_TRACK_GAP") - 2200:JS.index("XC_TRACK_GAP")]
head = " ".join(_head.replace("#", " ").split())
ok("does not vary with ability" in head,
   "the constant-shape decision must be written down, not left implicit")
ok("course_difficulties" in head,
   "...along with where the course-to-course variation does live")
ok("difficulty_spread" in head,
   "and how to falsify it -- the XC difficulty distribution must come out on "
   "the 3/6/8/10 ladder, which one command prints")


# ---- 4. off by default ------------------------------------------------- #
ok('"--split-ability"' in RJ, "the flag must exist")
PIPE = io.open(os.path.join(ROOT, "deploy", "run_pipeline.sh"),
               encoding="utf-8").read()
cmd = "\n".join(ln.split("#")[0] for ln in PIPE.splitlines())
# an env-gated, off-by-default switch is not silent (the merge-sports test
# makes the same allowance): strip it before searching for a bare flag
cmd = cmd.replace("${XCP_SPLIT_ABILITY:+--split-ability}", "")
ok("--split-ability" not in cmd,
   "the pipeline must not acquire it silently -- it changes what a rating is")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
