"""pool_view's public scale as a map (2026-10-22, XCP_EQUAL_TIME_SCALE).

No database: the clocks and the factor are stubbed.

    python -m pytest -q tests/test_equal_time_scale.py
"""
import math
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "x")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, d))

import pytest                                                    # noqa: E402
import pool_view as PV                                           # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(PV, "_MAP_CACHE", {})
    monkeypatch.setattr(PV, "repFactor",
                        lambda pool, sport=None: {"hs_f": 1.0, "college_f": 1.2}.get(
                            (pool or "").split("|")[0]))


def test_switch_off_is_the_old_multiplier(monkeypatch):
    monkeypatch.delenv("XCP_EQUAL_TIME_SCALE", raising=False)
    assert PV.hsMap("college_f") == (1.2, 1.0)
    assert PV.toHs(149.2, "college_f") == pytest.approx(179.04)
    assert PV.fromHs(179.04, "college_f") == pytest.approx(149.2)
    assert PV.toHs(149.3, "hs_f") == pytest.approx(149.3)
    assert PV.toHs(120.0, "hs_unknown") is None and PV.hsMap("hs_unknown") is None


def test_power_fit_recovers_a_power_curve():
    pairs = [(r, 1.15 * r ** 1.03) for r in range(40, 200, 4)]
    a, b, err = PV.fitPowerMap(pairs)
    assert a == pytest.approx(1.15, rel=1e-9) and b == pytest.approx(1.03, rel=1e-9)
    assert err < 1e-9


def test_equal_time_matches_the_clocks(monkeypatch):
    """Two clocks that are NOT proportional: HS t = 3000 * 100 / r, college
    t = 2600 * (100 / r) ** 1.05. The map must give the HS rating with the
    same time, and the inverse must undo it."""
    monkeypatch.setenv("XCP_EQUAL_TIME_SCALE", "1")

    def clock(r, pool):
        r = float(r)
        if pool == "hs_f":
            return 3000.0 * 100.0 / r
        return 2600.0 * (100.0 / r) ** 1.05
    monkeypatch.setattr(PV, "_trackSeconds", clock)
    import normalize_distance
    monkeypatch.setattr(normalize_distance, "ratedScalePool", lambda p: p)
    a, b = PV.hsMap("college_f")
    assert b == pytest.approx(1.05, rel=1e-4)                 # the clocks' exponent ratio
    for own in (100.0, 140.0, 149.2):
        hs = PV.toHs(own, "college_f")
        assert clock(hs, "hs_f") == pytest.approx(clock(own, "college_f"), rel=1e-4)
        assert PV.fromHs(hs, "college_f") == pytest.approx(own, rel=1e-9)
    # monotone: a pool's own order is its public order
    assert PV.toHs(120.0, "college_f") < PV.toHs(121.0, "college_f")
    assert PV.mapMoves("college_f") and not PV.mapMoves("hs_f")


def test_a_map_that_fits_badly_keeps_the_factor(monkeypatch):
    monkeypatch.setenv("XCP_EQUAL_TIME_SCALE", "1")
    monkeypatch.setattr(PV, "_equalTimeHs", lambda r, p, h: 150.0 if r < 120 else 60.0)
    import normalize_distance
    monkeypatch.setattr(normalize_distance, "ratedScalePool", lambda p: p)
    assert PV.hsMap("college_f") == (1.2, 1.0)
