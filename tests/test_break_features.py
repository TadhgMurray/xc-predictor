# Project: xc-predictor / tests
# File:    test_break_features.py
# Purpose: XCPredictor.breakFeatures tells an off-season break from an
#          absence through a stretch the athlete usually races (owner,
#          2026-10-10: "figure out how to tell them apart -- should be easy
#          with dates"), and does not count a forecast's hidden stretch as
#          time away.
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "model"))

try:
    import torch
    _HAVE_TORCH = True
except ImportError:
    _HAVE_TORCH = False


def _example(days_ago, gap, hidden=0.0):
    """One athlete: prior races `days_ago` before the target."""
    from transformer import (XCPredictor, SEQUENCE_FEATURES, CONTEXT_FEATURES,
                             SEQ_DAYS_AGO, CONTEXT_GAP_INDEX, CONTEXT_HIDDEN_INDEX)
    seq = torch.zeros(1, len(days_ago), SEQUENCE_FEATURES)
    seq[0, :, SEQ_DAYS_AGO] = torch.tensor(days_ago, dtype=torch.float32)
    mask = torch.ones(1, len(days_ago), dtype=torch.bool)
    ctx = torch.zeros(1, CONTEXT_FEATURES)
    ctx[0, CONTEXT_GAP_INDEX] = gap
    ctx[0, CONTEXT_HIDDEN_INDEX] = hidden
    m = XCPredictor(derived_features=3)
    return m.breakFeatures(seq, mask, ctx)[0].tolist()


@unittest.skipUnless(_HAVE_TORCH, "torch not installed")
class BreakFeatures(unittest.TestCase):
    def test_missing_a_stretch_they_always_race_reads_as_unusual(self):
        # raced weekly through the same 8 weeks in each of the two prior
        # years; this year nothing for those 8 weeks (56 days)
        prior = [365.25 + d for d in range(7, 57, 7)] + \
                [730.5 + d for d in range(7, 57, 7)]
        away, usual, known = _example(prior, gap=56)
        self.assertGreater(usual, 1.0)       # ~7 races a year missed
        self.assertEqual(known, 1.0)

    def test_an_off_season_break_reads_as_usual_rest(self):
        # same length of break, but in prior years they never raced in it
        prior = [365.25 + d for d in range(100, 160, 7)] + \
                [730.5 + d for d in range(100, 160, 7)]
        away, usual, known = _example(prior, gap=56)
        self.assertEqual(usual, 0.0)
        self.assertEqual(known, 1.0)

    def test_a_first_year_runner_is_unknown_not_off_season(self):
        away, usual, known = _example([60.0, 80.0, 100.0], gap=60)
        self.assertEqual(usual, 0.0)
        self.assertEqual(known, 0.0)

    def test_a_hidden_stretch_is_not_time_away(self):
        real = _example([200.0], gap=200)[0]
        twin = _example([200.0], gap=200, hidden=190)[0]   # 10 real days
        self.assertLess(twin, real)
        import math
        self.assertAlmostEqual(twin, math.log1p(10) / math.log(366), places=5)

    def test_a_year_away_covers_the_whole_calendar(self):
        prior = [400.0 + d for d in range(0, 300, 30)]
        away, usual, known = _example(prior, gap=400)
        self.assertGreater(usual, 0.0)


if __name__ == "__main__":
    unittest.main()
