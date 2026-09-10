# Project: xc-predictor / tests
# File:    test_prior_collapse.py
# Purpose: A sport's course difficulty must never be silently flattened by
#          a race-day floor.
#
# ★★★ THE FAILURE THIS EXISTS FOR, and it SHIPPED (2026-09-10, run22).
#
#     Within a race, delta and u are exactly collinear, so the split is
#     decided by tau2 against sigma_u2 and by nothing else. A stated
#     race-day floor of 0.045 -- twice the 0.0228 the data fitted -- won
#     every track split:
#
#       TF: race-day sd 0.04500 (fitted 0.02279, floor BINDING),
#           course prior 0.00147 -- a one-race course keeps 0.00
#
#     tau[TF] = 0.147% against a spread of 1.22% that
#     difficulty_reliability.py measured as REAL by splitting each venue's
#     races in half on different days, which a race-day effect cannot
#     fake. Every track was published as the average track. The solve
#     converged, wrote a board, and said nothing.
#
#     What makes it a repeat offence: the comment block directly above
#     SIGMA_U_FLOOR diagnoses a 0.045 floor destroying XC difficulty, and
#     in the same breath keeps a 0.045 floor on TF.
import io
import os
import sys
import unittest

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js


class NoBindingFloorByDefault(unittest.TestCase):
    def test_neither_sport_ships_a_race_day_floor(self):
        """⚠ THE ONE THAT MATTERS. identified_priors estimates tau and
        sigma_u on 2+ race cells alone, where the data separate them. A
        floor on top of that overrides a measurement with a guess."""
        for g, name in ((0, "XC"), (1, "TF")):
            self.assertEqual(
                js.SIGMA_U_FLOOR.get(g, 0.0), 0.0,
                f"{name} ships a race-day floor again -- that is what "
                f"collapsed the TF course prior to 0.00147")

    def test_a_floor_would_have_to_beat_the_measured_spread_to_be_safe(self):
        """Any floor at all is only harmless while it stays well under the
        sport's measured true course spread. 0.045 against TF's 0.0122 was
        not close."""
        for g in (0, 1):
            floor = js.SIGMA_U_FLOOR.get(g, 0.0)
            if floor:
                self.assertLess(floor, js.MEASURED_TRUE_SD[g],
                                "a race-day floor above the measured course "
                                "spread hands the day every split")


class TheAlarm(unittest.TestCase):
    def test_it_fires_on_the_run_that_shipped(self):
        """The exact numbers out of run22's log."""
        c = js.checkPriors(np.array([0.0364, 0.00147]) ** 2,
                           np.array([0.02931, 0.045]) ** 2)
        self.assertEqual(len(c), 1, c)
        self.assertIn("TF", c[0])
        self.assertIn("COLLAPSED", c[0])
        self.assertNotIn("XC:", c[0])

    def test_it_is_quiet_on_a_healthy_solve(self):
        self.assertEqual(
            js.checkPriors(np.array([0.0364, 0.0161]) ** 2,
                           np.array([0.0293, 0.0228]) ** 2), [])

    def test_it_fires_on_xc_too(self):
        """Not a track-specific check -- XC collapsed the same way under
        the shared floor, and that is what the owner spotted on the
        boards."""
        c = js.checkPriors(np.array([0.001, 0.0161]) ** 2,
                           np.array([0.045, 0.0228]) ** 2)
        self.assertTrue(any(s.startswith("XC:") for s in c), c)

    def test_a_third_of_the_measured_spread_is_the_line(self):
        truth = js.MEASURED_TRUE_SD[1]
        healthy = js.checkPriors(np.array([0.0364, truth / 2.0]) ** 2,
                                 np.array([0.0293, 0.0228]) ** 2)
        self.assertFalse(any("COLLAPSED" in s for s in healthy), healthy)
        broken = js.checkPriors(np.array([0.0364, truth / 4.0]) ** 2,
                                np.array([0.0293, 0.0228]) ** 2)
        self.assertTrue(any("COLLAPSED" in s for s in broken), broken)

    def test_it_survives_scalars(self):
        self.assertIsInstance(js.checkPriors(0.0364 ** 2, 0.0293 ** 2), list)

    def test_it_names_the_share(self):
        """The complaint has to say what a one-race course actually keeps
        -- that number is the whole diagnosis."""
        c = js.checkPriors(np.array([0.0364, 0.00147]) ** 2,
                           np.array([0.02931, 0.045]) ** 2)
        self.assertIn("0.001", c[0])


class ItIsWiredIn(unittest.TestCase):
    """A guard nobody calls is a comment."""

    def test_the_solve_calls_it(self):
        src = io.open(os.path.join(_ROOT, "engine", "joint_solve.py"),
                      encoding="utf-8").read()
        self.assertIn("for _c in checkPriors(tau2, sigma_u2):", src)

    def test_the_run_summary_calls_it(self):
        src = io.open(os.path.join(_ROOT, "engine", "run_joint.py"),
                      encoding="utf-8").read()
        self.assertIn("js.checkPriors(out[\"tau2\"], out[\"sigma_u2\"])", src)


if __name__ == "__main__":
    unittest.main()
