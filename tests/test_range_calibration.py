"""scripts/range_calibration.py and race_model's range factor: the factor
that makes +-1 sigma hold 68%, read against the factor in force when each
range was made. No database.

    python -m pytest -q tests/test_range_calibration.py
"""
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
import race_model as M                                       # noqa: E402
import range_calibration as C                                # noqa: E402


def test_the_factor_in_force_on_a_day():
    h = [{"from": "2026-11-09", "k": 0.9}, {"from": "2026-11-02", "k": 0.8}]
    assert M.scaleOn(h, "2026-11-01") == 1.0
    assert M.scaleOn(h, "2026-11-05") == 0.8 and M.scaleOn(h, "2026-11-09") == 0.9


def test_z_is_read_against_the_unscaled_range():
    # a range made under k = 0.5 is half its unscaled width
    w = 0.02
    fc = {"made_on": "2026-11-03", "runners": [
        {"person_id": 1, "seconds": 1000.0, "hi": 1000.0 * math.exp(0.5 * w)}]}
    fin = [{"person_id": 1, "time_seconds": 1000.0 * math.exp(-w)}]
    (z,), place = C.zScores(fc, fin, [{"from": "2026-11-02", "k": 0.5}])
    assert z == pytest.approx(1.0, rel=1e-3) and place == []      # no sigma_pct stored
    assert C.zScores(fc, [], []) == ([], [])


def test_the_place_spread_is_read_without_the_race_level_and_on_its_own_factor():
    # the whole race ran 5% slow; each runner is off the race's median by 1%
    fc = {"made_on": "2026-11-10", "runners": [
        {"person_id": i, "seconds": 1000.0, "hi": 1100.0, "sigma_pct": 1.0} for i in range(3)]}
    fin = [{"person_id": i, "time_seconds": 1000.0 * math.exp(0.05 + d)}
           for i, d in enumerate((-0.01, 0.0, 0.01))]
    h = [{"from": "2026-11-02", "k": 0.5}, {"from": "2026-11-09", "k_place": 0.5}]
    _t, place = C.zScores(fc, fin, h)
    assert sorted(place) == pytest.approx([0.0, 0.5, 0.5])        # 1% over an unscaled 2%
    assert M.scaleOn(h, "2026-11-10") == 0.5 and M.scaleOn(h, "2026-11-10", "k_place") == 0.5
    assert M.scaleOn(h, "2026-11-05", "k_place") == 1.0


def test_the_factor_makes_68_percent_true():
    import random
    rng = random.Random(0)
    zs = [abs(rng.gauss(0, 0.8)) for _ in range(20000)]       # ranges 25% too wide
    assert C.factor(zs) == pytest.approx(0.8, abs=0.03)
    assert C.factor([9.0] * 10) == C.K_MAX and C.factor([0.0] * 10) == C.K_MIN


def test_the_page_scales_its_ranges(monkeypatch, tmp_path):
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({"history": [{"from": "2000-01-01", "k": 0.5}]}))
    monkeypatch.setattr(M, "CAL_PATH", str(p))
    M._CAL["mtime"] = None
    assert M.rangeScale() == 0.5
    monkeypatch.setattr(M, "CAL_PATH", str(tmp_path / "none.json"))
    assert M.rangeScale() == 1.0


def test_the_level_shift_in_force_by_pool():
    h = [{"from": "2026-11-02", "k": 0.8},
         {"from": "2026-11-09", "k": 0.8, "level": {"college_f": -0.01}}]
    assert M.levelOn(h, "2026-11-05", "college_f") == 0.0
    assert M.levelOn(h, "2026-11-10", "college_f") == -0.01
    assert M.levelOn(h, "2026-11-10", "hs_f") == 0.0


def test_a_race_pool_is_its_runners_pool_in_that_race():
    # a high-school form in a college (tfrrs) race reads as a first college season
    assert M.fieldPool(["hs_f|x", "college_f|y", "hs_f"], "tfrrs") == "college_f"
    assert M.fieldPool(["hs_f|x", "hs_f", "ms_f"], "anet") == "hs_f"
    assert M.fieldPool([None, ""], "anet") is None


def test_the_level_miss_is_read_on_the_unshifted_trees():
    # predicted under a -1% shift, ran 1% slower than predicted: unshifted, level
    fc = {"made_on": "2026-11-10", "source": "anet",
          "runners": [{"person_id": i, "seconds": 1000.0, "pool": "ms_f"} for i in range(9)]}
    fin = [{"person_id": i, "time_seconds": 1000.0 * math.exp(0.01)} for i in range(9)]
    h = [{"from": "2026-11-09", "level": {"ms_f": -0.01}}]
    pool, d = C.raceLevel(fc, fin, h)
    assert pool == "ms_f" and d == pytest.approx(0.0, abs=1e-9)
    assert C.raceLevel(fc, fin[:5], h) is None


def test_the_level_shift_is_shrunk_by_races_and_capped():
    got = C.levelShifts([("college_f", -0.03)] * 10 + [("hs_f", 0.0)] * 300
                        + [("ms_m", -0.02)] * 4 + [("ms_f", -0.5)] * 80)
    assert got["college_f"][2] == pytest.approx(-0.03 * 10 / 30)
    assert got["hs_f"][2] == 0.0
    assert "ms_m" not in got                         # too few races
    assert got["ms_f"][2] == -C.LEVEL_MAX


def test_the_page_moves_a_race_by_its_pool(monkeypatch, tmp_path):
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({"history": [{"from": "2000-01-01", "level": {"ms_f": -0.02}}]}))
    monkeypatch.setattr(M, "CAL_PATH", str(p))
    M._CAL["mtime"] = None
    assert M.levelShift("ms_f") == -0.02 and M.levelShift("hs_f") == 0.0
    monkeypatch.setenv("XCP_RACE_LEVEL_SHIFT", "0")
    assert M.levelShift("ms_f") == 0.0
