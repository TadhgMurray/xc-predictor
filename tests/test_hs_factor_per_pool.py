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
    """values: {pool: C} on one scale; the stub serves the same number for
    both sports so the geometric mean is the plain ratio. A second dict
    keyed (pool, sport) exercises the two-scale case."""
    PV._FACTOR_CACHE.clear()
    PV._CONST_CACHE.clear()
    PV._FAILED.clear()
    # the engine's distance factor is stubbed to 1 (same reference distance
    # on both sides); the unit-conversion case below sets it explicitly
    PV._forward_factor = lambda d, pool, *a: 1.0
    if values and isinstance(next(iter(values)), tuple):
        PV._poolConstant = lambda pool, sport: values.get((pool, sport))
    else:
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


def test_two_scales_do_not_mix():
    # track constants near 300, cross country near 1300: the ratio inside
    # each sport is 0.9, and so is the factor -- never a number in the gap
    _constants({("ms_m", "XC"): 1400.0, ("hs_m", "XC"): 1260.0,
                ("ms_m", "TF"): 330.0, ("hs_m", "TF"): 297.0})
    assert abs(PV.hsFactor("ms_m", "XC", 5000) - 0.9) < 1e-9
    # one sport missing: the other's ratio stands alone
    _constants({("college_f", "XC"): 1200.0, ("hs_f", "XC"): 1440.0})
    assert abs(PV.hsFactor("college_f", "TF", 1600) - 1.2) < 1e-9


def test_reference_distances_are_converted():
    # middle school normalises to 3000 m, high school to 5000: the bare
    # constant ratio is 1.43 (the live box, 2026-09-03), the engine's
    # factor from 5000 to 3000 is about 0.58, and the honest factor is
    # their product, about 0.83 -- below 1, as a younger pool must be
    _constants({("ms_m", "XC"): 860.0, ("hs_m", "XC"): 1229.0})
    PV._forward_factor = lambda d, pool, *a: 0.58 if pool == "ms_m" else 1.0
    f = PV.hsFactor("ms_m", "XC", 5000)
    assert abs(f - (1229.0 / 860.0) * 0.58) < 1e-9 and f < 1.0


def test_the_source_no_longer_prices_by_distance():
    src = io.open(os.path.join(ROOT, "racecast", "pool_view.py"), encoding="utf-8").read()
    body = src[src.index("def hsFactor("):src.index("def _raceDistance(")]
    assert "_REP_DIST[sp]" in body and "np.log(ratios)" in body
    assert "distance_m" not in body.split('"""')[2], "the distance argument is not read"
