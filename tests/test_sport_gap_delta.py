"""The measured XC/TF gap reaches the solve, as a delta, and only on request.

Owner, 2026-09-09: "mainly the fact that most of the best seasons of all time
are tf."

scripts/measure_sport_gap.py, on 2.4M sandwiches (SE 0.00004):

    D = -0.02795 in log-rating, XC minus interpolated TF

Negative means XC rates BELOW the athlete's own interpolated TF level -- TF
is over-rewarded by 2.8%. Each sport carries half, so a 150 TF season becomes
147.9 and a 150 XC season 152.1: a 4.2-point swing at the top of an all-time
board, which is where the complaint came from.

★ A DELTA, NOT AN ABSOLUTE, AND THAT IS THE ONE THING THIS FILE GUARDS. The
  tool prints "bbar -0.03924 -> -0.06719", but -0.03924 is the OLD PAIR
  ENGINE's number, quoted out of linkage_check's header. The joint solve
  computes its own bbar from beta on every outer pass and never reads that
  file, so pinning the absolute would import an unrelated engine's estimate
  and move the gap by an unknown amount. Adding D to whatever the pass
  computed moves the gap by exactly D.

    python tests/test_sport_gap_delta.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


JS = _src("engine", "joint_solve.py")
RJ = _src("engine", "run_joint.py")

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 1. it ADDS to the computed bbar, it does not replace it ----------- #
# ! FAIL, DO NOT CRASH, WHEN THE LINE IS GONE. A mutation that drops the
#   np.average call entirely -- delta replacing the computed mean rather than
#   adding to it -- is exactly the bug this checks for, and an AttributeError
#   from .group(0) reports it as a broken test instead of a broken engine.
_m = re.search(r"^\s*bbar = float\(np\.average\(.*$", JS, re.M)
line = _m.group(0) if _m else "<the computed-mean line is gone>"
ok(_m is not None and "+ float(delta)" in line,
   f"bbar must be the computed mean PLUS the measured delta, got: "
   f"{line.strip()}")
ok("delta=0.0" in JS[JS.index("def recentreSportOffset"):
                     JS.index("def recentreSportOffset") + 300],
   "the delta must default to 0.0 -- a run that does not ask for it has to "
   "behave exactly as before")


# ---- 2. it is threaded all the way from the flag ----------------------- #
ok('"--sport-gap-delta"' in RJ, "run_joint must expose the flag")
ok(re.search(r'--sport-gap-delta".*?default=0\.0', RJ, re.S),
   "and it must default to off")
ok("sport_gap_delta=args.sport_gap_delta" in RJ,
   "the flag must reach solveJoint")
ok("sport_gap_delta=0.0" in JS, "solveJoint must accept it, defaulting to off")
ok("delta=sport_gap_delta" in JS,
   "solveJoint must hand it to recentreSportOffset")


# ---- 3. a run that carries it says so ---------------------------------- #
#   Two runs of the same data with and without this produce different
#   all-time boards, and the number is the only difference. A log that does
#   not name it cannot be told apart from one that does.
ok("sport gap: bbar carried a measured" in RJ,
   "a run carrying a gap correction must print it")
ok("recentred by" in JS and "measured gap" in JS,
   "and the per-pass line must separate the base from the delta")


# ---- 4. nothing turns it on behind your back --------------------------- #
#   Same rule as test_race_day_wording: the pipeline must not acquire a
#   silent engine default.
PIPE = _src("deploy", "run_pipeline.sh")
ok("--sport-gap-delta" not in PIPE,
   "run_pipeline must not pass --sport-gap-delta without this test moving "
   "with it -- it decides which sport tops an all-time board")


# ---- 5. the arithmetic, live ------------------------------------------- #
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    import copy                                                # noqa: E402
    import numpy as np                                         # noqa: E402
    import joint_solve as js                                   # noqa: E402
except Exception as e:                                         # noqa: BLE001
    print(f"  (skipping section 5: {e})")
else:
    class _D:
        pass

    d = _D()
    n, na = 200, 20
    d.n, d.n_ath, d.n_group = n, na, 2
    d.athlete = np.repeat(np.arange(na), n // na)
    d.sc = np.tile([0.5, -0.5], n // 2)
    d.group_row = np.tile([0, 1], n // 2)
    b = {"beta": np.random.RandomState(0).normal(0, .1, na),
         "mu": np.zeros(2), "a": np.zeros(na)}

    _, base = js.recentreSportOffset(copy.deepcopy(b), d)
    _, zero = js.recentreSportOffset(copy.deepcopy(b), d, delta=0.0)
    ok(base == zero,
       "delta=0.0 must be bit-identical to not passing one at all")
    for D_meas in (-0.02795, +0.01, -0.5):
        _, got = js.recentreSportOffset(copy.deepcopy(b), d, delta=D_meas)
        ok(abs((got - base) - D_meas) < 1e-12,
           f"delta {D_meas} moved bbar by {got - base}, not by {D_meas}")

    # ⚠ AND THE SIGN. Negative D must LOWER the TF side. The solve moves eff
    #   by bbar * s_c, so a more negative bbar moves the s=+0.5 group down.
    ok(js.recentreSportOffset(copy.deepcopy(b), d, delta=-0.02795)[1] < base,
       "a negative measured gap must make bbar more negative, or the "
       "correction is being applied backwards and TF goes UP")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
