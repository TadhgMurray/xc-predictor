"""A runner with no other race says nothing about the day (owner,
2026-11-07: the 1991 Kentucky state meet's +6.9% day, on runners with almost
no other races)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import joint_solve as js  # noqa: E402


def test_weight_by_other_races():
    # athlete 0: one row; 1: two rows; 2: three rows
    ath = np.array([0, 1, 1, 2, 2, 2])
    got = js.dayEvidenceWeight(ath, 3, full=2)
    assert got.tolist() == [0.0, 0.5, 0.5, 1.0, 1.0, 1.0]


def test_off_is_the_old_behaviour():
    ath = np.array([0, 1, 1])
    assert js.dayEvidenceWeight(ath, 2, full=0).tolist() == [1.0, 1.0, 1.0]


def test_a_day_of_one_race_runners_shrinks_to_nothing():
    # every runner 8% slow, none with another race: no day at all
    r = np.full(10, 0.08)
    w = np.ones(10)
    ath = np.arange(10)
    wd = w * js.dayEvidenceWeight(ath, 10, full=2)
    out = js.raceDayDivisions(r, wd, 1.0, np.zeros(10, dtype=np.int64), None, 1, 1.0)
    assert abs(float(out["u"][0])) < 1e-12
    # the same field, each with two other races: the day is read
    out = js.raceDayDivisions(r, w, 1.0, np.zeros(10, dtype=np.int64), None, 1, 1.0)
    assert float(out["u"][0]) > 0.07
