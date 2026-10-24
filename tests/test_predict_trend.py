"""XCP_PREDICT_BASIS=trend: the rating basis moved by a table fitted on
races before a date (review 2026-10). No model, no database: both sides are
stubbed, as in test_predict_blend.py.

    python -m pytest -q tests/test_predict_trend.py
"""
import datetime
import json
import math
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402
import predict as P                                          # noqa: E402

CUT = datetime.date(2026, 10, 24)


def _rows(*pairs, pool="hs_m", sport="XC"):
    """(days before the cut, rating) -> _ratingRows' rows, newest first."""
    return [((CUT - datetime.timedelta(days=d)).isoformat(), r, pool, sport, None, None)
            for d, r in sorted(pairs)]


def test_the_shape_of_a_rising_form():
    # 100 -> 103 -> 106 over 42 days: ln-slope ~ +2% per 30 days
    s = P._formShape(_rows((7, 106.0), (28, 103.0), (49, 100.0)), "XC", CUT)
    assert s["last_days"] == 7
    assert 7 < s["form_age_days"] < 49
    assert s["season_slope"] == pytest.approx(30 * math.log(1.06) / 42, rel=0.05)


def test_two_races_or_three_in_a_fortnight_are_not_a_trend():
    assert P._formShape(_rows((7, 106.0), (28, 100.0)), "XC", CUT)["season_slope"] == 0.0
    assert P._formShape(_rows((7, 106.0), (10, 103.0), (14, 100.0)), "XC",
                        CUT)["season_slope"] == 0.0


def test_the_slope_reads_only_the_pool_raced_last():
    # last spring's middle-school races do not make a high-school trend
    rows = (_rows((7, 106.0), (28, 106.0), (49, 106.0))
            + _rows((200, 70.0), (220, 60.0), (240, 50.0), pool="ms_m"))
    assert P._formShape(rows, "XC", CUT)["season_slope"] == pytest.approx(0.0, abs=1e-9)


def test_the_table_cell_its_level_then_the_overall():
    table = {"cells": {"hs_m|1|15-35": 0.02}, "levels": {"hs_m": 0.01},
             "overall": 0.005}
    e = {"form_pool": "hs_m|x", "form_age_days": 20, "season_slope": 0.0}
    assert P.trendShift(e, table, True) == pytest.approx(0.02)
    assert P.trendShift(e, table, False) == pytest.approx(0.01)       # no such cell
    assert P.trendShift(dict(e, form_pool="college_f"), table, True) == pytest.approx(0.005)
    assert P.trendShift(e, {}, True) == 0.0                           # no table, no change


def test_the_age_bands():
    assert P.trendKey("hs_m", True, 0) == "hs_m|1|0-14"
    assert P.trendKey("hs_m", False, 15) == "hs_m|0|15-35"
    assert P.trendKey("hs_m", True, 181) == "hs_m|1|181+"
    assert P.trendKey("hs_m", True, None) == "hs_m|1|0-14"


def test_a_rising_runner_is_carried_forward_and_the_shift_is_clamped():
    table = {"overall": 0.0, "slope_beta": 0.5}
    e = {"form_pool": "hs_m", "form_age_days": 30, "season_slope": 0.02}
    assert P.trendShift(e, table, True) == pytest.approx(-0.01)        # faster
    e["season_slope"] = 0.5                                            # clamped slope
    assert P.trendShift(e, table, True) == pytest.approx(-0.015)
    assert P.trendShift(e, {"overall": 0.5}, True) == pytest.approx(P.TREND_MAX_SHIFT)


def test_apply_moves_the_time_and_widens_only_the_time_band():
    table = {"overall": 0.02, "day_sd": 0.03, "place_k": 1.2}
    e = {"seconds": 1000.0, "lo": 970.0, "hi": 1030.0, "sigma_pct": 3.0,
         "form_pool": "hs_m", "form_age_days": 20, "season_slope": 0.0}
    P.trendApply(e, table, True)
    assert e["seconds"] == pytest.approx(1000.0 * math.exp(0.02), abs=0.06)
    assert e["sigma_pct"] == pytest.approx(3.6)                        # race_sim's spread
    want = math.sqrt(0.036 ** 2 + 0.03 ** 2)
    assert e["time_sigma_pct"] == pytest.approx(100 * want, abs=0.01)
    assert e["hi"] == pytest.approx(e["seconds"] * math.exp(want), abs=0.1)


RATED = {1: 900.0, 2: 950.0, 3: 1000.0}


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "_predictTimes", lambda cur, ids, target, spec=None: [
        {"seconds": 1200.0, "sigma_pct": 3.0, "is_race_time": True} for _i in ids])
    monkeypatch.setattr(P, "_targetSpec", lambda cur, target: {
        "date": "2026-10-24", "venue_cell": 0.04, "sport": "XC"})
    monkeypatch.setattr(P, "_ratingTimes", lambda cur, ids, spec, cut: {
        p: {"seconds": s, "lo": s * 0.97, "hi": s * 1.03, "sigma_pct": 3.0,
            "is_race_time": True, "basis": "rating", "form_pool": "hs_m",
            "form_age_days": 20, "season_slope": 0.0} for p, s in RATED.items()})
    path = tmp_path / "rating_trend.json"
    path.write_text(json.dumps({"cells": {"hs_m|1|15-35": -0.02}, "day_sd": 0.03}))
    monkeypatch.setattr(P, "TREND_PATH", str(path))
    monkeypatch.setenv("XCP_PREDICT_BASIS", "trend")


def test_trend_serves_the_rating_moved_and_the_model_only_for_the_unrated(stubbed):
    out = P._servedTimes(None, [1, 2, 3, 4], {"mode": "rerun"})
    for i, p in enumerate([1, 2, 3]):
        assert out[i]["basis"] == "rating"
        assert out[i]["seconds"] == pytest.approx(RATED[p] * math.exp(-0.02), abs=0.06)
        assert out[i]["trend_shift_pct"] == pytest.approx(-2.0)
    assert out[3]["seconds"] == 1200.0                       # no rating: the model


def test_trend_with_no_table_is_the_rating_basis(stubbed, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "TREND_PATH", str(tmp_path / "missing.json"))
    out = P._servedTimes(None, [1, 2, 3], {"mode": "rerun"})
    assert [e["seconds"] for e in out] == [900.0, 950.0, 1000.0]
