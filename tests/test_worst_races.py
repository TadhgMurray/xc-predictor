"""scripts/worst_races.py: the whole-field misses. No database."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import worst_races as W                                     # noqa: E402


def test_a_race_level_is_its_fields_median_miss():
    keys = ["(1, 2, 'tfrrs', '2026-10-01')"] * 5 + ["(1, 2, 'tfrrs', '2026-10-01', 28)"] \
        + ["(3, 4, 'anet', '2026-10-01')"] * 5
    pred = [1250.0] * 5 + [9.0] + [900.0] * 5
    act = [1000.0] * 5 + [1.0] + [1000.0] * 5
    got = W.raceLevels(keys, pred, act, "tfrrs")
    assert list(got) == [(1, 2, "tfrrs", "2026-10-01")]
    lv, n = got[(1, 2, "tfrrs", "2026-10-01")]
    assert n == 5 and abs(lv - math.log(1.25)) < 1e-12


def test_the_distance_our_times_would_fit():
    # predicted 25% slow for an 8K: the race was about a 6.4K
    assert abs(W.impliedDistance(8000, math.log(1.25)) - 8000 * 1.25 ** (-1 / 1.06)) < 1e-6
    assert W.impliedDistance(None, 0.1) is None
