# Project: xc-predictor / tests
# File:    test_prior_target.py
# Purpose: the athlete prior's shrinkage target, and the one channel by which a
#          pool's composition can reach a course's difficulty.
#
# ★ THE QUESTION (owner, 2026-09-19: "I do wondr if the fact that each pool has
#   diff abilities messes with the difficulty calculation? Maybe we should just
#   be going of of normalized times rather than abiltiies?").
#
#   Difficulty is already pool-free: a race's reading is r_i = (z - a) / h, a
#   WITHIN-ATHLETE residual, so who raced there cancels. `a` is not a rating --
#   it is the athlete's own mean normalized time with difficulty removed, in z's
#   units. The pool-relative number (points = pool_mean / ability * 100) is
#   computed downstream and never feeds back.
#
#   The one channel is the athlete prior: levels() shrinks `a` toward the pool's
#   centre, and that shrunk level sets r_i. So the centre has to be an estimator
#   a mixed pool cannot drag -- `pro` holds 218,231 athletes and 11,980 of its
#   12,310 teams are there by the <15-athlete rule, not by a professional.
#
#   python -m unittest tests.test_prior_target
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                               # noqa: E402
import bracket_engine as be                                      # noqa: E402


class WeightedPoolMedian(unittest.TestCase):

    def test_a_bimodal_pool_keeps_the_mass_not_the_midpoint(self):
        """The case this exists for: three tiny-school levels and one
        professional in one pool. The mean belongs to nobody in it."""
        vals = np.array([1.0, 1.0, 1.0, 9.0])
        wts = np.array([1.0, 1.0, 1.0, 1.0])
        pools = np.zeros(4, dtype=np.int64)
        med = be._weightedPoolMedian(vals, wts, pools, 1)
        mean = float((vals * wts).sum() / wts.sum())
        self.assertEqual(med[0], 1.0)
        self.assertAlmostEqual(mean, 3.0)
        self.assertLess(abs(med[0] - 1.0), abs(mean - 1.0))

    def test_a_single_distribution_is_unharmed(self):
        """Where a pool really is one population the two agree, so switching
        the default costs nothing there."""
        vals = np.array([4.0, 5.0, 6.0])
        wts = np.array([2.0, 2.0, 2.0])
        pools = np.zeros(3, dtype=np.int64)
        self.assertEqual(be._weightedPoolMedian(vals, wts, pools, 1)[0], 5.0)

    def test_pools_do_not_leak_into_each_other(self):
        vals = np.array([1.0, 1.0, 50.0, 50.0])
        wts = np.array([1.0, 1.0, 1.0, 1.0])
        pools = np.array([0, 0, 1, 1], dtype=np.int64)
        got = be._weightedPoolMedian(vals, wts, pools, 2)
        self.assertEqual(list(got), [1.0, 50.0])

    def test_the_weights_are_respected(self):
        """A level worth 100 rows outvotes three worth one each -- that is the
        whole point of weighting by row count."""
        vals = np.array([1.0, 2.0, 3.0, 9.0])
        wts = np.array([1.0, 1.0, 1.0, 100.0])
        pools = np.zeros(4, dtype=np.int64)
        self.assertEqual(be._weightedPoolMedian(vals, wts, pools, 1)[0], 9.0)

    def test_a_pool_with_nothing_in_it_is_zero_like_the_mean_path(self):
        vals = np.array([1.0, 2.0])
        wts = np.array([1.0, 1.0])
        pools = np.zeros(2, dtype=np.int64)
        got = be._weightedPoolMedian(vals, wts, pools, 3)
        self.assertEqual(list(got[1:]), [0.0, 0.0])

    def test_empty_input(self):
        got = be._weightedPoolMedian(np.array([]), np.array([]),
                                     np.array([], dtype=np.int64), 2)
        self.assertEqual(list(got), [0.0, 0.0])


class TheWiring(unittest.TestCase):

    def test_the_default_is_the_robust_one(self):
        self.assertEqual(be.PRIOR_TARGET, "median")
        self.assertIn("mean", be.PRIOR_TARGETS)

    def test_fit_takes_the_target_and_refuses_a_typo(self):
        """! VALIDATED AT fit(), not at use: a typo would otherwise mean
        'mean' silently for a whole solve."""
        import inspect
        self.assertIn("prior_target", inspect.signature(be.fit).parameters)
        src = inspect.getsource(be.fit)
        self.assertIn("prior_target must be one of", src)

    def test_levels_shrinks_toward_the_chosen_centre(self):
        """The mean path must still be the historic arithmetic, or the
        comparison is not a comparison."""
        import inspect
        src = inspect.getsource(be.fit)
        body = src[src.index("def levels("):]
        self.assertIn('if target == "median":', body)
        self.assertIn("_weightedPoolMedian(", body)
        # the else branch is the bincount mean, unchanged
        self.assertIn("num / np.maximum(den, 1e-12)", body)

    def test_the_holdout_can_sweep_it_on_the_same_rows(self):
        src = open(os.path.join(_ROOT, "scripts", "bracket_holdout.py")).read()
        self.assertIn('"--sweep-shrinkage"', src)
        self.assertIn("prior_target=target", src)
        # the sample is drawn from one pct/seed for every setting, and coverage
        # is checked rather than assumed -- the trap --window falls into
        self.assertIn("COVERAGE MOVED between settings", src)

    def test_production_cannot_turn_the_prior_on_by_accident(self):
        """It is off in the pipeline, which is why today's answer to the
        owner's question is 'pools do not touch difficulty'."""
        self.assertEqual(be.PRIOR_ATHLETE, 0.0)
        joint = open(os.path.join(_ROOT, "engine", "run_joint.py")).read()
        self.assertNotIn("prior_athlete", joint)


if __name__ == "__main__":
    unittest.main()
