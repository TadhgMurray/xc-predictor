"""diag_course_credit.withinFit: recovers a planted course credit inside
runner-seasons, and finds none when there is none.

    python -m pytest -q tests/test_course_credit.py
"""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import diag_course_credit as D                                   # noqa: E402


def _data(b_course, n=400, races=6, seed=1):
    rng = np.random.default_rng(seed)
    g = np.repeat(np.arange(n), races)
    level = rng.normal(4.7, 0.15, n)[g]
    course = rng.uniform(0.0, 0.12, g.size)
    day = rng.normal(0.0, 0.02, g.size)
    t = rng.uniform(0, 1, g.size)
    y = level + b_course * course + 0.0 * day + 0.02 * t + rng.normal(0, 0.01, g.size)
    return g, y, np.column_stack([course, day, t])


def test_recovers_a_planted_credit():
    g, y, X = _data(0.3)
    b, se, n = D.withinFit(g, y, X)
    assert abs(b[0] - 0.3) < 4 * se[0] and abs(b[1]) < 4 * se[1] and n == len(y)


def test_none_when_charged_right():
    g, y, X = _data(0.0, seed=2)
    b, se, _n = D.withinFit(g, y, X)
    assert abs(b[0]) < 4 * se[0]
