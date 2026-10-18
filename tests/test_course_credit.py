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


class _Cur:
    """Answers the five reads load() makes, in order."""
    def __init__(self):
        from datetime import date
        d = date(2024, 10, 26)
        self.answers = {
            "ranking_results": [(1, 7, "hs_m", 2024, 4.9, d, 86), (2, 7, "hs_m", 2024, 4.88, d, 86)],
            "FROM results": [(1, 100, 5), (2, 100, 6)],
            "FROM meets": [(100, 5, "Mt. San Antonio College", 34.05, -117.8, 4828.0),
                           (100, 6, "Somewhere Else", 1.0, 2.0, 5000.0)],
            "course_canonical": [("Mt. San Antonio College", 34.05, -117.8, 13433)],
            "race_day_effect": [(13433, 4800, d, 0.089, 0.034)],
        }
        self.rows = []

    def execute(self, sql, params=None):
        for k, v in self.answers.items():
            if k in sql:
                self.rows = v
                return
        self.rows = []

    def fetchall(self):
        return self.rows


def test_load_joins_in_python():
    got = D.load(_Cur(), 2023, 1, None, say=lambda m: None)
    assert got == [(7, "hs_m", 2024, 4.9, 0.089, 0.034, 86.0)]   # row 2's course is unknown
