"""diag_altitude_credit.homeKm: the median venue km of each runner-season.

    python -m pytest -q tests/test_altitude_credit.py
"""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import diag_altitude_credit as D                                 # noqa: E402


def test_home_km_is_the_seasons_median():
    grp = np.array([0, 0, 0, 1, 1])
    km = np.array([1.6, 1.6, 0.0, 0.0, 0.9])
    assert list(D.homeKm(grp, km)) == [1.6, 1.6, 1.6, 0.45, 0.45]
