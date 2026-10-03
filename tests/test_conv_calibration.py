"""The cross-sport leg of a conversion, measured on dual-sport runners
(owner, 2026-10-03: "a 25:30 8k at Keene State is not a 9:28 3200m").
Pure: synthetic pairs, no database."""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))

import numpy as np                                             # noqa: E402
import conv_calibration as CC                                  # noqa: E402


def _pairs(rng, n, xb, tb, cell_gap, tilt_per_pt=0.0, noise=0.01):
    ability = rng.normal(110, 10, n)
    g = cell_gap + tilt_per_pt * (ability - 110) + rng.normal(0, noise, n)
    xc = ability / np.exp(g / 2)
    tf = ability * np.exp(g / 2)
    return [xb] * n, [tb] * n, xc, tf


def test_cells_and_tilt_are_recovered():
    rng = np.random.default_rng(1)
    a = _pairs(rng, 20000, 5000, 3200, -0.02, tilt_per_pt=-0.001)
    b = _pairs(rng, 20000, 5000, 800, +0.01, tilt_per_pt=-0.001)
    fit = CC.fitPool(*[np.r_[x, y] for x, y in zip(a, b)])
    assert abs(fit["cells"]["5000>3200"]["g"] - (-0.02)) < 0.002
    assert abs(fit["cells"]["5000>800"]["g"] - 0.01) < 0.002
    t = fit["tilt"]
    # better runners: a more negative gap
    assert t[0][1] > t[-1][1]
    # the tilt's slope is what was put in (per rating point between quartiles)
    slope = (t[-1][1] - t[0][1]) / (t[-1][0] - t[0][0])
    assert abs(slope - (-0.001)) < 0.0003


def test_a_thin_cell_borrows_from_its_pool():
    rng = np.random.default_rng(2)
    big = _pairs(rng, 30000, 5000, 3200, -0.01, noise=0.03)
    thin = _pairs(rng, 5, 8000, 3200, +0.05, noise=0.03)
    fit = CC.fitPool(*[np.r_[x, y] for x, y in zip(big, thin)])
    cell = fit["cells"]["8000>3200"]
    assert abs(cell["g"] - fit["level"]) < abs(cell["raw"] - fit["level"])


def test_lookup_and_direction():
    table = {"pools": {"college_m": {
        "n": 1, "level": -0.01, "tilt": [[100, 0.0], [110, 0.0]],
        "tf": {"3200": {"g": -0.012, "n": 9}},
        "cells": {"8000>3200": {"g": -0.015, "n": 9}}}}}
    # XC -> track: a track rating under XC is a SLOWER track time
    f = CC.timeFactor("college_m|x", "XC", 8000, "TF", 3218.7, 105, table)
    assert math.isclose(f, math.exp(0.015))
    # back again is the inverse
    b = CC.timeFactor("college_m", "TF", 3200, "XC", 8000, 105, table)
    assert math.isclose(f * b, 1.0)
    # an XC distance with no cell: the track event's own term; else the pool
    assert math.isclose(CC.timeFactor("college_m", "XC", 7000, "TF", 3200, 105, table),
                        math.exp(0.012))
    assert math.isclose(CC.timeFactor("college_m", "XC", 7000, "TF", 2000, 105, table),
                        math.exp(0.01))
    # within a sport, or an unmeasured pool: untouched
    assert CC.timeFactor("college_m", "TF", 1600, "TF", 3200, 105, table) == 1.0
    assert CC.timeFactor("ms_f", "XC", 4000, "TF", 1600, 105, table) == 1.0


def test_no_file_no_change(tmp_path):
    assert CC.load(str(tmp_path / "missing.json")) == {}
