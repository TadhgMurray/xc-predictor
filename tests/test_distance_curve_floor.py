# Project: xc-predictor / tests
# File:    test_distance_curve_floor.py
# Purpose: the fitted distance curve can never say a longer race is run at
#          a faster pace: the sampled potential is held to a floor on its
#          local exponent, the health line says so, and the check script
#          reads the exponents back.
#
#   python -m pytest -q tests/test_distance_curve_floor.py
import math
import os
import sys

import numpy as np

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import types                                                   # noqa: E402
if "corrections" not in sys.modules:                            # the fitter imports it at load
    _corr = types.ModuleType("corrections")
    _corr.distanceOverrideSQL = lambda *a, **k: ("", "")
    _corr.distanceDropSQL = lambda *a, **k: ""
    sys.modules["corrections"] = _corr
import fit_distance_exponent as fde                            # noqa: E402
import distance_curve_check as dcc                             # noqa: E402
import normalize_distance as nd                                # noqa: E402


def _curve(exps, d0=800.0, n=8):
    """knots at equal log steps; each segment's exponent from `exps`."""
    knots = [math.log(d0) + 0.25 * i for i in range(n)]
    vals = [0.0]
    for i in range(1, n):
        vals.append(vals[-1] + exps[(i - 1) % len(exps)] * 0.25)
    return knots, vals


def test_the_floor_raises_only_the_segments_below_it():
    knots, vals = _curve([1.10, 1.08, 0.92, 1.05, 0.98, 1.12, 1.07])
    out, raised = fde._floorLocalExponent(knots, vals, 1.04)
    assert raised == 2
    slopes = [(out[i + 1] - out[i]) / (knots[i + 1] - knots[i]) for i in range(len(out) - 1)]
    assert min(slopes) >= 1.04 - 1e-9
    # the segments that were fine keep their own slope
    assert abs(slopes[0] - 1.10) < 1e-9 and abs(slopes[5] - 1.12) < 1e-9
    same, n0 = fde._floorLocalExponent(knots, vals, 0)
    assert n0 == 0 and same == [float(v) for v in vals]


def test_the_health_line_names_the_floor():
    knots, vals = _curve([1.08] * 7)
    entry = {"knots": knots, "values": vals, "floored_segments": 2, "min_local_exp": 1.04}
    assert "2 segments held to the 1.04 floor" in fde._healthNote(entry)
    entry["floored_segments"] = 0
    assert "floor" not in fde._healthNote(entry)


def test_the_check_reads_the_artifacts_exponents_back(monkeypatch):
    knots, vals = _curve([1.15, 1.12, 1.10, 1.08, 0.92, 1.07, 1.07], d0=700.0, n=13)
    art = {"kind": "distance_potential", "target": 5000.0,
           "pool_targets": {"college_m": 8000.0},
           "pools": {"college_m|XC": {"knots": knots, "values": vals}},
           "global": {"knots": knots, "values": vals}, "global_by_sport": {}}
    monkeypatch.setattr(nd, "_SPLINES", art)
    lines = []
    n_bad = dcc.report(pools=("college_m",), sports=("XC",), out=lines.append)
    text = "\n".join(lines)
    assert "college_m|XC" in text and "normalised to 8000 m" in text
    assert n_bad >= 1 and "!" in text
    n_after = dcc.report(pools=("college_m",), sports=("XC",), floor=1.04, out=lambda _s: None)
    assert n_after < n_bad


def test_the_solves_offsets_lay_on_the_spline_per_band(monkeypatch):
    knots, vals = _curve([1.12, 1.10, 1.09, 1.08, 1.08, 1.07, 1.07], d0=700.0, n=13)
    art = {"kind": "distance_potential", "target": 5000.0, "pool_targets": {"hs_m": 5000.0},
           "pools": {"hs_m|TF": {"knots": knots, "values": vals}},
           "global": {"knots": knots, "values": vals}, "global_by_sport": {}}
    monkeypatch.setattr(nd, "_SPLINES", art)
    npz = {"dist_offset": np.array([0.02, -0.01, 0.0, 0.0]),
           "dist_labels": np.array(["hs_m:800:b0", "hs_m:800:b1", "hs_m:3200:b0", "hs_m:3200:b1"])}
    offsets, n_band = dcc.loadOffsets(npz)
    assert n_band == 2 and offsets[("hs_m", 800, 0)] == 0.02
    f, _t = dcc.factors("hs_m", "TF")
    fe = dcc.effective(f, "hs_m", offsets, 0)
    # a positive offset at 800 makes the 800 read faster: its multiplier falls
    assert fe[800] < f[800] and fe[3200] == f[3200] and fe[1600] == f[1600]
    lines = []
    dcc.report(pools=("hs_m",), sports=("TF",), out=lines.append, offsets=offsets, n_band=n_band)
    text = "\n".join(lines)
    assert "spline" in text and "band 0" in text and "band 1" in text
    assert dcc.loadOffsets(None) == ({}, 0)


def test_the_exponent_is_made_non_increasing_and_nothing_else():
    knots, vals = _curve([1.16, 1.12, 1.15, 1.09, 1.10, 1.07, 1.07])    # two bumps
    out, change = fde._monotoneLocalExponent(knots, vals)
    slopes = [(out[i + 1] - out[i]) / (knots[i + 1] - knots[i]) for i in range(len(out) - 1)]
    assert all(a >= b - 1e-12 for a, b in zip(slopes, slopes[1:]))
    assert abs(slopes[0] - 1.16) < 1e-9 and abs(slopes[-1] - 1.07) < 1e-9
    assert abs(slopes[1] - 1.135) < 1e-9 and abs(slopes[2] - 1.135) < 1e-9   # the pooled pair
    assert abs(change - 0.015) < 1e-9
    knots2, vals2 = _curve([1.15, 1.12, 1.10, 1.08, 1.08, 1.07, 1.07])   # already monotone
    same, c2 = fde._monotoneLocalExponent(knots2, vals2)
    assert c2 < 1e-12 and all(abs(a - b) < 1e-12 for a, b in zip(same, vals2))
    e = {"knots": knots, "values": vals}
    assert fde._applyMonotone(e, "ROAD") is e                            # not a monotone sport
    for sport in ("TF", "XC"):
        got = fde._applyMonotone(e, sport)
        assert got["monotone"] and got["monotone_change"] > 0
        assert e.get("monotone") is None                                 # a copy, not in place
        assert "non-increasing" in fde._healthNote(got)


def test_both_sports_get_one_shape():
    """★ THE OWNER'S QUESTION (2026-09-16): "at extremes it's going faster
    ... how can we best fit a clean line?" XC was the sport left out, and
    every unphysical segment in the shipped artifact was XC's: college_m
    8000->10000 at 0.920, elem past 3200 at 0.98, hs_m RISING after 5000.
    A fade is a fact about the runner; the surface is what difficulty is
    for."""
    assert set(fde.MONOTONE_SPORTS) == {"TF", "XC"}


def test_the_extension_may_not_choose_a_slope_the_floor_would_refuse():
    """⚠ THE 0.920 ESCAPE HATCH. _extensionSlopeHigh accepted a measured
    beyond-span slope anywhere in the health band, which reached down to
    0.85, and the floor then overwrote it -- two rules disagreeing about
    one number."""
    assert fde.EXT_SLOPE_BAND[0] >= fde.MIN_LOCAL_EXP
    src = open(os.path.join(_ROOT, "engine", "fit_distance_exponent.py")).read()
    body = src[src.index("def _extensionSlopeHigh("):src.index("# _fitOnePotential")]
    assert "max(EXT_SLOPE_BAND[0], MIN_LOCAL_EXP" in body, \
        "the floor must be read at call time so --min-exponent raises the band too"


def test_the_floor_survives_the_smoother_so_the_order_is_safe():
    """The fitter floors inside _fitOnePotential and smooths where the
    artifact is assembled. A pooled block's mean is never below its own
    minimum, so smoothing cannot reopen the floor -- but only in that
    order."""
    knots, vals = _curve([1.04, 1.30, 1.04, 1.15, 1.04, 1.04, 1.04])
    floored, _n = fde._floorLocalExponent(knots, vals, 1.04)
    out, _c = fde._monotoneLocalExponent(knots, floored)
    slopes = [(out[i + 1] - out[i]) / (knots[i + 1] - knots[i]) for i in range(len(out) - 1)]
    assert min(slopes) >= 1.04 - 1e-9
    assert all(a >= b - 1e-12 for a, b in zip(slopes, slopes[1:]))
