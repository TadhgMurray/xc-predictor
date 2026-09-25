"""A professional is normalised at the college anchor (owner, 2026-09-25).

Pros are rated against the college pool's mean (pair_write_results.
_proScaleMap); with no anchor of their own their times were 5000 m
equivalents divided into a college_m mean in 8000 m seconds, and Nuguse's
3:29 1500 rated ~208 beside 147.5 for his last college season.

    python -m pytest -q tests/test_pro_anchor.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import normalize_distance as nd                                 # noqa: E402

ART = {"kind": "distance_potential", "target": 5000.0,
       "pool_targets": {"college_m": 8000.0, "college_f": 6000.0,
                        "hs_m": 5000.0}}


def test_the_pro_pools_read_their_college_twins_anchor(monkeypatch):
    monkeypatch.setattr(nd, "_SPLINES", ART)
    assert nd.targetFor("pro_m") == nd.targetFor("college_m") == 8000.0
    assert nd.targetFor("pro_f", "TF") == nd.targetFor("college_f") == 6000.0
    assert nd.targetFor("hs_m") == 5000.0
    assert nd.poolBandFor("pro_m") == nd.poolBandFor("college_m")


def test_the_normaliser_evaluates_the_pro_curve_at_the_college_anchor(monkeypatch):
    monkeypatch.setattr(nd, "_SPLINES", ART)
    seen = []
    monkeypatch.setattr(nd, "_distancePotentialEntry",
                        lambda pool, sport: {"target": 5000.0})
    monkeypatch.setattr(nd, "_evalDistancePotential",
                        lambda entry, x: seen.append(round(__import__("math").exp(x))) or 0.0)
    nd._normalizeWithPotential(209.36, 1500.0, "pro_m")
    assert seen == [1500, 8000]


def test_a_refit_stamps_the_same_anchors():
    """Read off the fitter's source: it imports the 165 MB corrections."""
    import re
    src = open(os.path.join(_ROOT, "engine", "fit_distance_exponent.py")).read()
    table = src[src.index("POOL_TARGET_METERS = {"):]
    table = table[:table.index("\n}\n")]
    got = dict(re.findall(r'"(\w+)":\s*([\d.]+)', table))
    for pro, col in (("pro_m", "college_m"), ("pro_f", "college_f")):
        assert float(got[pro]) == float(got[col])
