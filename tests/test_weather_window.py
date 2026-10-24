# diag_weather_window.py: the pure parts (owner, 2026-10-23: let the races
# choose the weather window and the heat credit's size).
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _env  # noqa: F401,E402
import diag_weather_window as WW                                 # noqa: E402


def test_the_fit_recovers_a_scale_and_prefers_the_true_window():
    rng = np.random.default_rng(1)
    x_true = rng.uniform(0, 0.08, 400)                  # the heat the race really had
    x_wrong = x_true * 0.5 + rng.uniform(0, 0.04, 400)  # a window that only half sees it
    y = 0.6 * x_true + rng.normal(0, 0.003, 400)        # the curve credits heat 40% too much
    w = np.ones(400)
    a, b, r2, n = WW.fit(y, x_true, w)
    assert abs(b - 0.6) < 0.03 and r2 > 0.9 and n == 400
    assert WW.fit(y, x_wrong, w)[2] < r2


def test_within_course_takes_each_course_against_its_own_days():
    got = WW.withinCourse([1.0, 3.0, 5.0], ["a", "a", "b"], [1, 1, 1])
    assert got[0] == -1.0 and got[1] == 1.0 and np.isnan(got[2])
