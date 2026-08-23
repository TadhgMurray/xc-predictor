"""One race, one cell, one pool -- does the rating sort by time?

THE BUG THIS PINS DOWN. resultRatings used to fold the athlete-season sport
offset beta into the effective difficulty:

    eff = delta[cell] * h  +  beta[athlete_season] * sc

delta and h are properties of the CELL, so they are identical for everyone in
one race. beta is not. Two runners crossing the same finish line seconds apart
were therefore rated against different courses, and the published rating stopped
being a decreasing function of time -- observed on meet 271870 / div 1083175,
where 234 rows shared one pool, one cell and a norm that matched the database
exactly, yet rating*norm spanned 4.53%. exp(+-0.045) is 4.6%. That was beta.

This fixture reproduces the inversion with the old formula and asserts the
current resultRatings has none. Run it after any change to the rating chain:

    python scripts/check_rating_monotone.py

Exit status is the test result, so it drops straight into CI.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "engine"))

import linkage_check as lc                       # noqa: E402
from pair_write_results import poolMeanPerGroup  # noqa: E402

N = 6
# Times strictly increasing: row 0 wins, row 5 is last.
NORM = np.array([1000.0, 1005.0, 1010.0, 1015.0, 1020.0, 1025.0])
# A mixed field -- track specialists and XC specialists in the same race. The
# magnitudes are real: bbar came out at -0.05319 on the live solve.
BETA = np.array([-0.045, 0.040, -0.030, 0.045, -0.010, 0.020])
SC = np.full(N, -0.5)          # every row is an XC row
DELTA = 0.011                  # the cell's difficulty
TILT_K = -0.031                # _TILT_K
CELL_RATING = 130.0            # a fast college field


def fixture():
    """Six rows, one cell, one pool, one season."""
    return {
        "delta": np.array([DELTA]),
        "course": np.zeros(N, dtype=np.int64),
        "solved": np.array([True]),
        "norm": NORM.copy(),
        "group": np.arange(N),
        # abilityTilt is per CELL, so h is one number repeated.
        "h": np.full(N, 1.0 + TILT_K * (CELL_RATING - 100.0) / 10.0),
        "beta": BETA.copy(),
        "sc": SC.copy(),
        "rat": {"ability": np.full(N, CELL_RATING),
                "valid": np.ones(N, bool), "anchor": None},
        "attrs": {"pool": np.zeros(N, dtype=np.int64),
                  "pool_names": ["college_f"],
                  "season": np.full(N, 2025, dtype=np.int64)},
    }


def report(tag, rating):
    print(f"\n{tag}")
    for i in range(N):
        print(f"  row {i}  norm={NORM[i]:8.1f}  beta={BETA[i]:+.3f}  "
              f"rating={rating[i]:8.4f}")
    inversions = int((np.diff(rating) > 0).sum())
    prod = rating * NORM
    spread = (prod.max() - prod.min()) / prod.mean() * 100.0
    print(f"  monotone decreasing in time: {inversions == 0}"
          f"   (inversions {inversions}/{N - 1})")
    print(f"  spread of rating*norm: {spread:.3f}%")
    return inversions


def main():
    # The old formula, written out, so the fixture still fails if someone puts
    # beta back.
    D = fixture()
    eff = (lc.ratingDelta(D)[D["course"]] * D["h"]
           + D["beta"][D["group"]] * D["sc"])
    pm_c, _ = poolMeanPerGroup(D["rat"]["ability"], D["attrs"],
                               D["rat"]["valid"])
    old = 100.0 * pm_c[D["group"]] / (D["norm"] / np.exp(eff))
    bad_old = report("BEFORE -- beta folded into eff", old)

    new = lc.resultRatings(fixture())["r_career"]
    bad_new = report("AFTER -- resultRatings as it stands", new)

    print()
    if bad_old == 0:
        print("FAIL: the fixture no longer reproduces the bug it guards.")
        return 1
    if bad_new:
        print(f"FAIL: resultRatings inverts {bad_new} adjacent finishers in a "
              f"single race. Something athlete-specific is back in eff.")
        return 1
    print("PASS: beta was inverting finishers; it no longer does.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
