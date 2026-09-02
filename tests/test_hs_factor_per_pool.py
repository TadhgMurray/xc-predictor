"""One HS-equivalent factor per pool (owner, 2026-09-02): the HS view must
never reorder rows inside a pool, whatever the sport or distance.

    python -m pytest -q tests/test_hs_factor_per_pool.py
"""
import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
for name in ("database", "psycopg2", "psycopg2.extras", "psycopg2.errors"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["database"].getConn = lambda: None
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2"].errors = sys.modules["psycopg2.errors"]

import pool_view as PV                                           # noqa: E402


def _constants(values):
    PV._FACTOR_CACHE.clear()
    PV._CONST_CACHE.clear()
    PV._FAILED.clear()
    PV._poolConstant = lambda pool, sport: values.get(pool)


def test_same_factor_for_every_sport_and_distance():
    _constants({"college_m": 1100.0, "hs_m": 1237.0, "ms_m": 1400.0, "hs_f": 1466.0,
                "college_f": 1300.0})
    f = PV.hsFactor("college_m", "XC", 5000)
    assert abs(f - 1237.0 / 1100.0) < 1e-9
    assert PV.hsFactor("college_m", "TF", 1600) == f
    assert PV.hsFactor("college_m", "XC", 8000) == f
    assert PV.hsFactor("college_m|TF", None, None) == f, "the sport suffix and a missing distance are fine"
    assert PV.hsFactor("ms_m", "TF", 1600) == PV.hsFactor("ms_m", "XC", 3000)
    assert PV.repFactor("college_f", "XC") == PV.repFactor("college_f", "TF")


def test_hs_pools_and_unknown_gender():
    _constants({"hs_m": 1237.0})
    assert PV.hsFactor("hs_m", "XC", 5000) == 1.0
    assert PV.hsFactor("hs_f", "TF", 1600) == 1.0
    assert PV.hsFactor("hs_unknown_gender", "XC", 5000) is None
    assert PV.hsFactor(None, "XC", 5000) is None


def test_the_rail_and_a_missing_constant():
    _constants({"college_m": 100.0, "hs_m": 1237.0})
    assert PV.hsFactor("college_m", "XC", 5000) is None, "12x is outside the rail"
    _constants({"hs_m": 1237.0})
    assert PV.hsFactor("college_m", "XC", 5000) is None, "no college constant"


def test_the_source_no_longer_prices_by_distance():
    src = io.open(os.path.join(ROOT, "racecast", "pool_view.py"), encoding="utf-8").read()
    body = src[src.index("def hsFactor("):src.index("def _raceDistance(")]
    assert "_forward_factor(" not in body
    assert "_poolConstant(pool, None)" in body
