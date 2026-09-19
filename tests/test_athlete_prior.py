# Project: xc-predictor / tests
# File:    test_athlete_prior.py
# Purpose: the athlete-side shrinkage the engine never had, and the two things
#          that must be true of it.
#
#   ⚠ THE ASYMMETRY IT FIXES. bracket_engine shrinks the COURSE side three
#     times over -- a race into its course by race_sat, a course into its
#     group by a prior FITTED per group, an era into its course by
#     prior_races -- and the athlete side not at all: levels() returned
#     s_ / max(n_other, 1), a plain mean, so an athlete-season with one other
#     row in the window was a single noisy reading carrying the authority of
#     one with thirty. Twice over: as a VOTER whose noise lands in the
#     course's difficulty, and as the RATING itself.
#
#   ! IT SHIPS OFF. So the first thing pinned here is that off means
#     UNCHANGED, not approximately unchanged.
#
#   python -m unittest tests.test_athlete_prior
import os
import sys
import unittest

import numpy as np

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket_engine as be                                    # noqa: E402


class TheShippedDefaultIsOff(unittest.TestCase):

    def test_the_constant_is_zero(self):
        # ! Every other change in bracket_engine.py was argued from a
        #   measurement on the corpus. This one is argued from a code
        #   reading, and it moves EVERY rating, so it stays off until
        #   bracket_holdout scores it.
        self.assertEqual(be.PRIOR_ATHLETE, 0.0)

    def test_fit_is_spelled_the_same_as_the_group_priors(self):
        self.assertEqual(be.PRIOR_FIT, "fit")

    def test_the_range_refuses_an_unbelievable_estimate(self):
        lo, hi = be.PRIOR_ATHLETE_RANGE
        self.assertGreater(lo, 0.0)
        self.assertLess(lo, hi)
        self.assertGreaterEqual(be.PRIOR_ATHLETE_MIN_ATHLETES, 1)


class TheShrinkageArithmetic(unittest.TestCase):
    """The formula, checked against hand arithmetic rather than itself.

    A level is (sum_of_other_rows + k * pool_mean) / (n_other + k). So:
      k = 0            -> the plain mean, exactly
      n_other -> large -> the plain mean, whatever k is
      n_other = 1      -> half way to the pool mean when k = 1
    """

    @staticmethod
    def _shrunk(total, n_other, pool_mean, k):
        return (total + k * pool_mean) / (n_other + k)

    def test_k_zero_is_the_plain_mean(self):
        for total, n in ((3.0, 1), (12.0, 4), (300.0, 30)):
            self.assertAlmostEqual(self._shrunk(total, n, 99.0, 0.0),
                                   total / n, places=12,
                                   msg="a zero prior must not consult the "
                                       "pool mean at all")

    def test_one_row_with_k_one_lands_half_way(self):
        got = self._shrunk(total=2.0, n_other=1, pool_mean=4.0, k=1.0)
        self.assertAlmostEqual(got, 3.0, places=12)

    def test_a_well_raced_athlete_keeps_their_own_number(self):
        own = 2.0
        got = self._shrunk(total=own * 30, n_other=30, pool_mean=4.0, k=1.0)
        self.assertAlmostEqual(got, (60.0 + 4.0) / 31.0, places=12)
        self.assertLess(abs(got - own), 0.07,
                        "thirty rows must barely move; if they move a lot the "
                        "prior is over-shrinking and the holdout's fat "
                        "buckets will say so")

    def test_shrinkage_is_monotone_in_the_prior(self):
        pulls = [self._shrunk(2.0, 1, 4.0, k) for k in (0.0, 0.5, 1.0, 4.0)]
        self.assertEqual(pulls, sorted(pulls),
                         "more prior must mean more pull, or the knob cannot "
                         "be tuned")
        self.assertLess(pulls[-1], 4.0, "and never past the target")


class TheHoldoutCanActuallyMeasureIt(unittest.TestCase):
    """A prior with no scoreboard is how the course/athlete asymmetry
    survived in the first place: the course side has been bucketed by its
    thinness since the prior existed, the athlete side had no table."""

    def test_the_athlete_axis_exists_and_counts_training_rows_only(self):
        import bracket_holdout as bh
        cols = {"athlete": np.array([1, 1, 1, 2, 2, 3]),
                "year": np.array([2024, 2024, 2024, 2024, 2024, 2024])}
        train = np.array([True, True, False, True, False, False])
        got = bh._rowsPerAthleteSeason(cols, train)
        # athlete 1 has two TRAINING rows, athlete 2 one, athlete 3 none
        self.assertEqual(list(got), [2, 2, 2, 1, 1, 0])

    def test_an_athlete_season_is_the_unit_not_the_athlete(self):
        import bracket_holdout as bh
        cols = {"athlete": np.array([1, 1, 1]),
                "year": np.array([2023, 2024, 2024])}
        train = np.array([True, True, True])
        got = bh._rowsPerAthleteSeason(cols, train)
        self.assertEqual(list(got), [1, 2, 2],
                         "levels() keys on athlete-SEASON, so the scoreboard "
                         "must too or the buckets describe nothing")

    def test_fit_survives_the_round_trip_through_the_cli_parser(self):
        import bracket_holdout as bh
        self.assertEqual(bh._priorAthlete("fit"), be.PRIOR_FIT)
        self.assertEqual(bh._priorAthlete("FIT"), be.PRIOR_FIT)
        self.assertEqual(bh._priorAthlete("2.5"), 2.5)
        self.assertEqual(bh._priorAthlete("0"), 0.0)
        self.assertEqual(bh._priorAthlete(""), 0.0)
        self.assertEqual(bh._priorAthlete(None), 0.0)


class TheEngineStillAcceptsItsOwnSignature(unittest.TestCase):

    def test_fit_takes_prior_athlete(self):
        import inspect
        sig = inspect.signature(be.fit)
        self.assertIn("prior_athlete", sig.parameters)
        self.assertEqual(sig.parameters["prior_athlete"].default,
                         be.PRIOR_ATHLETE)

    def test_the_off_path_skips_the_pool_machinery_entirely(self):
        # ⚠ NOT "produces the same answer" -- the same CODE. levels() returns
        #   before the two bincounts when no prior is positive, so the
        #   historic path is what runs rather than what is reproduced.
        import inspect
        text = inspect.getsource(be.fit)
        i = text.index("def levels(")
        body = text[i:text.index("def cellStep(")]
        self.assertIn("if not np.any(kk > 0):", body)
        self.assertLess(body.index("if not np.any(kk > 0):"),
                        body.index("np.bincount"),
                        "the early return must come BEFORE any pool work")


if __name__ == "__main__":
    unittest.main()


class TheHoldoutCliActuallyReachesTheFunction(unittest.TestCase):
    """⚠ TWICE NOW a --dump/--compare edit landed in argparse and NOT in
    score(), and `--help` looked perfect while the run died on
    `score() got an unexpected keyword argument 'dump'`. Checking the help
    text proves the parser accepts a flag, not that anything consumes it.
    These assert the CALLABLE."""

    def test_score_accepts_every_flag_main_forwards(self):
        import inspect
        import bracket_holdout as bh
        params = inspect.signature(bh.score).parameters
        src = inspect.getsource(bh.main)
        # every `name=args.x` in main's score(...) call must be a parameter
        call = src[src.index("    score("):]
        for name in ("dump", "compare", "prior_athlete", "gauge", "window"):
            self.assertIn(name, params, f"score() is missing {name}")
            self.assertIn(f"{name}=", call, f"main() does not pass {name}")

    def test_the_dump_and_compare_helpers_exist(self):
        import bracket_holdout as bh
        self.assertTrue(callable(bh._dumpRun))
        self.assertTrue(callable(bh._compareRuns))

    def test_compare_refuses_a_mismatched_sample(self):
        import os
        import tempfile
        import bracket_holdout as bh
        n = 40
        y = np.linspace(0, 1, n)
        cov = np.ones(n, bool)
        test = np.ones(n, bool)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "a.npz")
            bh._dumpRun(path, cov, y, y, test, 15, 11, 2, 21)
            # same sample -> a verdict
            got = bh._compareRuns(path, cov, y, y, test, 15, 11, 2, 45)
            self.assertIsNotNone(got)
            self.assertEqual(got["n"], n)
            # different row count -> refused, not silently misaligned
            half = slice(0, n // 2)
            self.assertIsNone(
                bh._compareRuns(path, cov[half], y[half], y[half], test[half],
                                15, 11, 2, 45))
            # different seed -> refused
            self.assertIsNone(
                bh._compareRuns(path, cov, y, y, test, 15, 99, 2, 45))

    def test_compare_on_a_missing_dump_is_a_message_not_a_crash(self):
        import bracket_holdout as bh
        n = 10
        self.assertIsNone(
            bh._compareRuns("/nonexistent/nope.npz", np.ones(n, bool),
                            np.zeros(n), np.zeros(n), np.ones(n, bool),
                            15, 11, 2, 45))
