# Project: xc-predictor / tests
# File:    test_holdout_ladder.py
# Purpose: A held-out set that shares a race with the training set is not a
#          held-out set.
#
# ★★ THE BUG. run_joint.holdout used pv.splitByRow -- 10 per cent of ROWS at
#    random. Athletes and races are CROSSED random effects, so the same
#    athlete and the same race sat in train AND test: the model had already
#    fitted that race's day effect and that athlete's ability from the other
#    90 per cent of the same race. The "prediction" was barely more than
#    interpolation, and the number it printed was the number every modelling
#    argument was supposed to be settled by.
#
# ★ AND THE SECOND HALF OF THE BUG: holdout() passed a hand-picked seven
#   keyword arguments to solveJoint while the real solve passed eighteen, so
#   it scored a DIFFERENT MODEL from the one being shipped. Both now go
#   through solveKwargs, and test_solve_kwargs_are_shared pins that.
import os
import sys
import unittest

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pair_validate as pv                                     # noqa: E402


def _world(n_race=200, per_race=25, n_ath=400, seed=0):
    """Rows carrying a race id, an athlete id and a cell id, shaped like the
    real design: several races per cell, athletes appearing in many races."""
    rng = np.random.default_rng(seed)
    race, ath, cell = [], [], []
    for r in range(n_race):
        c = r // 4                                  # four races per cell
        for a in rng.choice(n_ath, per_race, replace=False):
            race.append(r); ath.append(int(a)); cell.append(c)
    return np.array(race), np.array(ath), np.array(cell)


class Splitters(unittest.TestCase):
    def test_row_split_leaks_every_race(self):
        """⚠ THE CONTROL. This is what we were doing, and it must be SHOWN
        to leak or the fix has nothing to fix."""
        race, ath, cell = _world()
        te = pv.splitFor("row", race.size, race=race, athlete=ath, cell=cell)
        shared = np.intersect1d(np.unique(race[te]), np.unique(race[~te]))
        self.assertGreater(len(shared), 100,
                           "a row split should share almost every race "
                           "between train and test")

    def test_race_split_shares_no_race(self):
        race, ath, cell = _world()
        te = pv.splitFor("race", race.size, race=race, athlete=ath, cell=cell)
        self.assertTrue(te.any() and not te.all(), "degenerate split")
        shared = np.intersect1d(np.unique(race[te]), np.unique(race[~te]))
        self.assertEqual(len(shared), 0, f"races leaked: {shared[:5]}")

    def test_athlete_split_shares_no_athlete(self):
        race, ath, cell = _world()
        te = pv.splitFor("athlete", race.size, race=race, athlete=ath,
                         cell=cell)
        shared = np.intersect1d(np.unique(ath[te]), np.unique(ath[~te]))
        self.assertEqual(len(shared), 0, f"athletes leaked: {shared[:5]}")

    def test_course_split_shares_no_cell(self):
        race, ath, cell = _world()
        te = pv.splitFor("course", race.size, race=race, athlete=ath,
                         cell=cell)
        shared = np.intersect1d(np.unique(cell[te]), np.unique(cell[~te]))
        self.assertEqual(len(shared), 0, f"cells leaked: {shared[:5]}")

    def test_the_fraction_is_about_right(self):
        """Groups vary in size, so the ROW fraction drifts from the group
        fraction -- but not by much, or the score is measured on a sliver."""
        race, ath, cell = _world()
        for kind in ("race", "athlete", "course"):
            te = pv.splitFor(kind, race.size, race=race, athlete=ath,
                             cell=cell, frac=0.10)
            self.assertGreater(te.mean(), 0.03, f"{kind}: {te.mean():.3f}")
            self.assertLess(te.mean(), 0.25, f"{kind}: {te.mean():.3f}")

    def test_a_missing_group_array_is_an_error_not_a_silent_row_split(self):
        """⚠ Falling back to a row split when the caller forgets the group
        array would silently restore the exact bug this file exists for."""
        with self.assertRaises(ValueError):
            pv.splitFor("race", 100)
        with self.assertRaises(ValueError):
            pv.splitFor("nonsense", 100, race=np.zeros(100))

    def test_the_split_is_deterministic(self):
        race, ath, cell = _world()
        a = pv.splitFor("race", race.size, race=race, seed=7)
        b = pv.splitFor("race", race.size, race=race, seed=7)
        c = pv.splitFor("race", race.size, race=race, seed=8)
        self.assertTrue(np.array_equal(a, b))
        self.assertFalse(np.array_equal(a, c))


class SolveKwargsShared(unittest.TestCase):
    def test_solve_kwargs_are_shared(self):
        """★ Both solveJoint call sites in run_joint must go through
        solveKwargs, so a new flag reaches the shipped model AND the score,
        or neither. Checked on the source: a call with its own long keyword
        list is how the two drifted apart last time."""
        import ast
        src = open(os.path.join(_ROOT, "engine", "run_joint.py")).read()
        calls = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "solveJoint"]
        self.assertEqual(len(calls), 2, "expected the real solve and the "
                                        "holdout, found %d" % len(calls))
        for c in calls:
            names = [k.arg for k in c.keywords]
            self.assertIn(None, names,
                          f"solveJoint at line {c.lineno} does not use "
                          f"**solveKwargs -- it will drift from the other")
            explicit = [n for n in names if n is not None]
            self.assertLessEqual(
                len(explicit), 4,
                f"solveJoint at line {c.lineno} passes {explicit} on its "
                f"own; put them in solveKwargs so both callers get them")


if __name__ == "__main__":
    unittest.main()
