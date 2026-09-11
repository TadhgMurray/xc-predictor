# Project: xc-predictor / tests
# File:    test_track_is_zero.py
# Purpose: The average track is 0.0, and NOTHING downstream moves it.
#
# ★★ THE OWNER ASKED THREE TIMES why track difficulty was not 0.0, and the
#    engine was innocent every time. joint_golive anchored the track cells
#    at zero; racecast/difficulty_view then subtracted the CORPUS mean
#    before displaying. Cross country dominates the corpus, so that mean
#    was about +6%, and a typical track displayed near -6%.
#
#    The file even said why it was safe: "the engine anchors its
#    difficulties to this same row-weighted mean, so the value is ~0 by
#    construction". True when it was written. False from the moment the
#    engine started anchoring on track -- and nothing checked.
#
#    So the zero is defined in ONE place (engine/joint_golive.py) and the
#    display is now the identity. These tests pin both halves.
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class DisplayDoesNotMoveIt(unittest.TestCase):
    def setUp(self):
        import difficulty_view as dv
        self.dv = dv
        # a stored anchor that is NOT zero, to prove nothing is subtracted
        dv._state["at"] = float("inf")
        dv._state["mean"] = 0.0583

    def test_the_display_zero_is_zero(self):
        self.assertEqual(self.dv.sportMeanLog(), 0.0)
        self.assertEqual(self.dv.sportMeanLog("TF"), 0.0)
        self.assertEqual(self.dv.sportMeanLog("XC"), 0.0)

    def test_a_stored_zero_displays_as_zero(self):
        """⚠ THE BUG, DIRECTLY. A track stored at 0.0 must show 0.0, even
        when the corpus mean is far from it."""
        self.assertAlmostEqual(self.dv.relativePct(0.0), 0.0, places=6)

    def test_a_stored_value_displays_unchanged(self):
        for d in (0.069, -0.02, 0.15):
            self.assertAlmostEqual(self.dv.relativePct(d), 100.0 * d,
                                   places=6, msg=f"{d} was moved")

    def test_the_sport_argument_changes_nothing(self):
        """Two sports, one line -- the same stored number reads the same
        whichever sport it is labelled."""
        self.assertEqual(self.dv.relativePct(0.069, "XC"),
                         self.dv.relativePct(0.069, "TF"))

    def test_the_guard_still_reads_the_stored_anchor(self):
        """It must remain VISIBLE, just not applied: a table written by an
        engine with a different anchor should be noticeable."""
        self.assertAlmostEqual(self.dv.trackDriftLog(), 0.0583, places=6)

    def test_the_guard_reads_track_cells(self):
        self.assertIn("TF:", self.dv._SQL)
        self.assertNotIn("n_results", self.dv._SQL,
                         "the guard should be the average COURSE, not the "
                         "average result")

    def test_nonsense_is_still_rejected(self):
        self.assertIsNone(self.dv.relativePct(None))
        self.assertIsNone(self.dv.relativePct("fast"))
        self.assertIsNone(self.dv.relativePct(-0.95))


class EngineAnchorsOnTheAverageTrack(unittest.TestCase):
    def test_golive_anchors_on_the_mean_of_track_cells(self):
        src = open(os.path.join(_ROOT, "engine", "joint_golive.py")).read()
        self.assertIn("anchored = raw - float(np.mean(raw[ref]))", src,
                      "the go-live anchor is no longer the mean track")
        # ! UNWEIGHTED on purpose: weighting by results lets a handful of
        #   enormous championship ovals define the zero, and those are the
        #   least typical tracks there are.
        self.assertNotIn("np.average(raw[ref], weights=w[ref])", src)

    def test_the_tau_caps_are_the_observed_spreads(self):
        """⚠ tau = TRUE spread publishes tau*sqrt(r), which is NARROWER than
        the truth -- the sqrt(r) under-dispersion this repo has now hit
        twice. The cap that reproduces the true spread on a board is the
        OBSERVED spread."""
        import joint_solve as js
        self.assertAlmostEqual(js.TAU_MAX_DEFAULT[0], 0.0364, places=4)
        self.assertAlmostEqual(js.TAU_MAX_DEFAULT[1], 0.0161, places=4)
        for g, rel, true_sd in ((0, 0.928, 0.0351), (1, 0.574, 0.0122)):
            published = js.TAU_MAX_DEFAULT[g] * rel ** 0.5
            self.assertAlmostEqual(published, true_sd, delta=0.0005,
                                   msg=f"group {g} publishes {published:.4f}, "
                                       f"true spread is {true_sd:.4f}")


if __name__ == "__main__":
    unittest.main()
