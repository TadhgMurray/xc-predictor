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
