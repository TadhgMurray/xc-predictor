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

# ! THE sys.path LINE MUST COME FIRST. _env lives in tests/, so it is not
#   importable until this directory is on the path -- and it must be
#   imported before anything that reaches scripts/config.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD; see tests/_env.py
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


class TheJointDumpCannotCrashTheRun(unittest.TestCase):
    """⚠ A dump written against a DIFFERENT pack raised
    "index 62805404 is out of bounds for axis 0 with size 62805298" -- the
    pack had been rebuilt 107 rows smaller between the two runs. sameRows
    already had the guard for it (`inb`), one line TOO LATE: the lexsort
    undo indexed with the raw ids first.

    ! AND IT ONLY SURFACED ONCE AN EARLIER BUG WAS FIXED. The previous crash
      was in _rowsPerAthleteSeason, evaluated as an ARGUMENT to sameRows, so
      execution never got inside. One bug was masking the other."""

    @staticmethod
    def _pack(n):
        return dict(
            full_ath=np.arange(n) // 3,
            full_year=np.full(n, 2024),
            full_norm=np.exp(np.linspace(0.1, 0.9, n)))

    def _run(self, rows, n=1000):
        import os
        import tempfile
        import bracket_holdout as bh
        pk = self._pack(n)
        y = np.log(pk["full_norm"])
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base_holdout.npz")
            idx = np.clip(rows, 0, n - 1)
            np.savez(path, row=rows, pred=y[idx],
                     covered=np.ones(rows.size, bool), y=y[idx])
            return bh.sameRows({"sport": np.zeros(n)}, np.ones(n, bool),
                               np.ones(n, bool), np.ones(n, bool), y, y,
                               path, **pk)

    def test_an_id_past_the_end_is_reported_not_raised(self):
        # the server's case: a pack rebuilt smaller than the dump's
        got = self._run(np.array([0, 5, 10, 1106]))
        self.assertIsNone(got, "it must decline, not crash")

    def test_every_id_past_the_end_is_also_survivable(self):
        self.assertIsNone(self._run(np.arange(0, 1000, 10) + 1000))

    def test_negative_ids_are_treated_the_same_way(self):
        # ! -1 is how this module already spells "no row", so it must not be
        #   read as "the last row" by numpy's wrap-around.
        self.assertIsNone(self._run(np.array([-1, -5, 0, 3])))

    def test_a_dump_that_does_match_is_not_rejected_by_the_clamp(self):
        # It may still decline for OTHER reasons (too little overlap); what
        # this pins is that the clamp itself raises nothing and lets it past.
        try:
            self._run(np.arange(0, 1000, 2))
        except Exception as exc:                                 # noqa: BLE001
            self.fail(f"a valid dump must not raise: {exc!r}")


# ===================================================================== #
#  THE FITTED BRANCH, WHICH NO TEST HAD EVER ENTERED                    #
# ===================================================================== #

class TheFitActuallyRuns(unittest.TestCase):
    """⚠⚠ WHY THIS CLASS EXISTS. _fitAthletePrior computed

            tau2 - sigma2 / cnt[multi].mean()

       where cnt is per athlete-SEASON and multi is per ROW. On the corpus that
       is a 505,986-long array indexed by a 3,287,804-long mask, and it raised
       IndexError in the owner's first real shrinkage sweep -- after the `off`
       rung had already spent its time.

       It survived every test because a pool needs
       PRIOR_ATHLETE_MIN_ATHLETES (200) multi-row athlete-seasons before it is
       fitted at all, and no fixture had anywhere near that, so every test took
       the `continue` above the bug and the arithmetic never ran once. A prior
       that cannot be exercised at fixture scale needs a fixture at its scale,
       so these build one: 250 athletes x 4 rows.
    """

    @staticmethod
    def _world(n_ath=250, per=4, seed=5, tau=0.15, sigma=0.03):
        """One pool, `n_ath` athlete-seasons of `per` rows each, planted with a
        known between-athlete spread (tau) and within-athlete noise (sigma).
        Two courses so the levels are identifiable."""
        rng = np.random.default_rng(seed)
        a_true = rng.normal(0, tau, n_ath)
        ath = np.repeat(np.arange(n_ath), per)
        course = np.tile(np.arange(per) % 2, n_ath)
        doy = 100 + np.tile(np.arange(per) * 7, n_ath)
        y = a_true[ath] + rng.normal(0, sigma, ath.size)
        return dict(
            athlete=ath, year=np.full(ath.size, 2025), course=course,
            days=(366 - doy).astype(np.float64), doy=doy,
            sport=np.ones(ath.size, dtype=np.int64), norm=np.exp(y),
            dist_m=np.full(ath.size, 5000.0),
            athlete_keys=[(i, "hs_m") for i in range(n_ath)],
            course_keys=["XC:1:d5000", "XC:2:d5000"])

    def _fit(self, **kw):
        import contextlib
        import io
        cols = self._world()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            f = be.fit(cols, None, window=60, top=1.0, era_years=0,
                       n_iter=20, prior_athlete="fit", verbose=True, **kw)
        return f, buf.getvalue()

    def test_the_fit_runs_and_does_not_raise(self):
        """The regression itself. ! THE ASSERTION IS THAT THE POOL WAS FITTED,
        not merely that fit() returned: "0 of 1 pools fitted" is exactly the
        `continue` that hid the bug for a week, and it would pass a weaker
        check."""
        f, out = self._fit()
        self.assertTrue(np.isfinite(np.asarray(f["D"])).all())
        self.assertIn("1 of 1 pools fitted", out,
                      "the fitted branch was skipped, so this test would not "
                      "have caught the IndexError either:\n" + out)

    def test_the_estimate_lands_inside_the_believable_range(self):
        """k = sigma^2/tau^2, and the world is planted at sigma 0.03 against
        tau 0.15, so the raw estimate (~0.04) is BELOW the range floor and must
        come back clamped to it rather than as a number nobody believes. Little
        shrinkage is the right answer here: the athletes really are different."""
        _f, out = self._fit()
        lo, _hi = be.PRIOR_ATHLETE_RANGE
        line = [l for l in out.splitlines() if "athlete prior" in l][0]
        self.assertIn(f"by {lo:g} rows' worth", line, line)

    def test_both_shrinkage_targets_survive_the_fitted_branch(self):
        """The sweep runs the fitted k against each target in turn, so both
        paths have to reach the end."""
        for target in be.PRIOR_TARGETS:
            f, _out = self._fit(prior_target=target)
            self.assertTrue(np.isfinite(np.asarray(f["D"])).all(), target)
