"""diag_weather_credit: the own-other-races comparison and the credit split."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import diag_weather_credit as W  # noqa: E402


def test_own_other_races_excludes_same_race_and_far_days():
    # person 1: races A (day 0), B (day 10), C (day 50); two rows at A
    pk = np.array([1, 1, 1, 1, 2, 2])
    day = np.array([0, 0, 10, 50, 5, 5])
    race = np.array([0, 0, 1, 2, 3, 4])
    lr = np.log(np.array([100.0, 100.0, 110.0, 120.0, 90.0, 95.0]))
    s, c = W.ownOtherRaces(pk, day, race, lr, window=30)
    # rows at A see only B (C is 50 days out, the other A row is the same race)
    assert list(c) == [1, 1, 2, 0, 1, 1]
    assert math.isclose(s[0], math.log(110.0))
    assert math.isclose(s[2] / c[2], math.log(100.0))
    # a different race on the same day still counts
    assert math.isclose(s[4], math.log(95.0))


def test_credit_splits_into_terms():
    art = W.nd._weatherArtifactFor("XC")
    if art is None:
        return
    ref = dict(art["reference"])
    wx = {**ref, "precip": 15.0, "doy": 280}
    got = W.credit(wx, None, "XC", 5000.0)
    assert got is not None
    tot, rain, mud, temp = got
    # a wet race is credited, and all of that is the rain term here
    assert rain > 0
    assert math.isclose(tot, rain, rel_tol=1e-6, abs_tol=1e-9)
