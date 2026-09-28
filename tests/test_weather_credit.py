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


def test_within_course_compares_a_course_with_its_own_years():
    # Glendoveer: a mild year rated 3% high, a wet year 1% low, runner-weighted
    wx = {"precip": 0.0, "rain_before": 0.0, "soil": 0.2, "apparent_temp": 10.0, "wind": 1.0}
    races = [
        dict(ck="Glendoveer", course="Glendoveer", iso="2023-10-01", meet=1, src="anet", n=100,
             gap=0.03, once=False, wx=wx, credit=(0.0, 0.0, 0.0, 0.0)),
        dict(ck="Glendoveer", course="Glendoveer", iso="2024-10-01", meet=2, src="anet", n=300,
             gap=-0.01, once=False, wx={**wx, "precip": 9.0}, credit=(0.01, 0.01, 0.0, 0.0)),
        dict(ck="Lone", course="Lone", iso="2024-10-02", meet=3, src="anet", n=50,
             gap=0.05, once=True, wx=wx, credit=(0.0, 0.0, 0.0, 0.0)),
    ]
    W.withinCourse(races)
    # course mean = (0.03*100 - 0.01*300) / 400 = 0
    assert math.isclose(races[0]["within"], 0.03)
    assert math.isclose(races[1]["within"], -0.01)
    assert races[2]["within"] is None, "a course raced once has nothing to compare with"
    W.courseDays(races, ["glendoveer"])          # prints; must not raise
