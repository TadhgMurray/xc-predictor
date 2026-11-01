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
    monkeypatch.setenv("XCP_EQUAL_TIME_SCALE", "0")
    assert PV.hsMap("college_f") == PV.powerKnots(1.2)
    assert PV.toHs(149.2, "college_f") == pytest.approx(179.04)
    assert PV.fromHs(179.04, "college_f") == pytest.approx(149.2)
    assert PV.toHs(149.3, "hs_f") == pytest.approx(149.3)
    assert PV.toHs(120.0, "hs_unknown") is None and PV.hsMap("hs_unknown") is None
    assert not PV.mapMoves("hs_f") and PV.mapMoves("college_f")


def test_power_fit_recovers_a_power_curve():
    pairs = [(r, 1.15 * r ** 1.03) for r in range(40, 200, 4)]
    a, b, err = PV.fitPowerMap(pairs)
    assert a == pytest.approx(1.15, rel=1e-9) and b == pytest.approx(1.03, rel=1e-9)
    assert err < 1e-9


def test_knots_carry_a_power_curve_exactly():
    k = PV.powerKnots(1.15, 1.03)
    for r in (0.5, 40.0, 149.2, 900.0, 5000.0):
        assert PV.mapAt(r, k) == pytest.approx(1.15 * r ** 1.03, rel=1e-12)
    segs = PV.mapSegments(((50.0, 60.0), (100.0, 125.0), (150.0, 180.0)))
    assert segs[0][0] == 100.0 and segs[-1][0] is None
    for hi, a, b in segs:
        x = 75.0 if hi == 100.0 else 120.0
        assert a * x ** b == pytest.approx(PV.mapAt(x, ((50.0, 60.0), (100.0, 125.0), (150.0, 180.0))))


def _clocks(monkeypatch, college):
    """HS t = 3000 * 100 / r; the college clock is `college(r)`."""
    def clock(r, pool, distance=5000.0, sport="XC"):
        r = float(r)
        return 3000.0 * 100.0 / r if pool == "hs_f" else college(r)
    monkeypatch.setattr(PV, "_trackSeconds", clock)
    import normalize_distance
    monkeypatch.setattr(normalize_distance, "ratedScalePool", lambda p: p)
    return clock


def test_equal_time_matches_the_clocks(monkeypatch):
    """Two clocks that are NOT proportional. The map must give the HS
    rating with the same time, and the inverse must undo it."""
    monkeypatch.setenv("XCP_EQUAL_TIME_SCALE", "1")
    clock = _clocks(monkeypatch, lambda r: 2600.0 * (100.0 / r) ** 1.05)
    for own in (100.0, 140.0, 149.2, 35.0, 260.0):
        hs = PV.toHs(own, "college_f")
        assert clock(hs, "hs_f") == pytest.approx(clock(own, "college_f"), rel=1e-6)
        assert PV.fromHs(hs, "college_f") == pytest.approx(own, rel=1e-9)
    # monotone: a pool's own order is its public order
    assert PV.toHs(120.0, "college_f") < PV.toHs(121.0, "college_f")
    assert PV.mapMoves("college_f") and not PV.mapMoves("hs_f")


def test_a_clock_one_curve_cannot_fit_is_still_matched(monkeypatch):
    """2026-11-01: a college clock with a bend (a calibration that starts at
    115 and stops at 125) is what one power curve missed by 2%. The knots
    match it at every knot and within 0.1% between."""
    import math
    monkeypatch.setenv("XCP_EQUAL_TIME_SCALE", "1")

    def cal(r):
        return 0.0 if r <= 115 else -0.03 if r >= 125 else -0.03 * (r - 115) / 10

    clock = _clocks(monkeypatch, lambda r: 2600.0 * (100.0 / r) ** 1.05 * math.exp(cal(r)))
    for own in [float(x) for x in range(60, 200, 3)] + [118.7, 149.2]:
        hs = PV.toHs(own, "college_f")
        assert clock(hs, "hs_f") == pytest.approx(clock(own, "college_f"), rel=1e-3)
    a, b, err = PV.fitPowerMap([(r, PV.toHs(r, "college_f")) for r in range(40, 202, 4)])
    assert err > 1.0                       # one curve could not have done it


def test_no_clock_keeps_the_factor(monkeypatch):
    monkeypatch.setenv("XCP_EQUAL_TIME_SCALE", "1")
    monkeypatch.setattr(PV, "_equalTimeHs", lambda *a, **k: None)
    import normalize_distance
    monkeypatch.setattr(normalize_distance, "ratedScalePool", lambda p: p)
    assert PV.hsMap("college_f") == PV.powerKnots(1.2)


def test_the_board_sql_is_the_same_map(monkeypatch):
    """rankings._scaleExpr evaluates to toHs for a pool=all board."""
    import math
    import re
    monkeypatch.setenv("XCP_EQUAL_TIME_SCALE", "1")
    _clocks(monkeypatch, lambda r: 2600.0 * (100.0 / r) ** 1.05 * math.exp(-0.02 if r > 120 else 0.0))
    import rankings
    monkeypatch.setattr(rankings, "_SCALE_POOLS", ("college_f",))
    sql = rankings._scaleExpr({"scale": "hs", "pool": "all"}, "s.mean_rating")
    assert "s.pool = 'college_f'" in sql

    def run(v):
        e = sql.replace("s.mean_rating", repr(v))
        e = re.sub(r"power\(([^,]+), ([^)]+)\)", r"((\1) ** (\2))", e)
        # CASE WHEN c THEN x ... ELSE y END -> python, one level at a time
        for _ in range(4):
            e = re.sub(r"CASE ((?:WHEN (?:(?!CASE|END).)+? THEN (?:(?!CASE|WHEN|END).)+? )+)ELSE ((?:(?!CASE|WHEN|END).)+?) END",
                       lambda m: _py(m), e)
        return eval(e.replace("s.pool = 'college_f'", "True").replace("IS NULL", "== None")
                     .replace(" OR ", " or "))

    def _py(m):
        arms = re.findall(r"WHEN ((?:(?!WHEN).)+?) THEN ((?:(?!WHEN).)+?) (?=WHEN|$)", m.group(1) + " ")
        out = m.group(2)
        for cond, val in reversed(arms):
            out = f"(({val}) if ({cond}) else ({out}))"
        return out
    for v in (95.0, 118.0, 133.3, 160.0):
        assert run(v) == pytest.approx(PV.toHs(v, "college_f", "XC"), rel=1e-5)


def test_equal_time_is_the_default(monkeypatch):
    monkeypatch.delenv("XCP_EQUAL_TIME_SCALE", raising=False)
    assert PV.equalTimeOn()
    monkeypatch.setenv("XCP_EQUAL_TIME_SCALE", "0")
    assert not PV.equalTimeOn()
