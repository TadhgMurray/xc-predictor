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
    (z,) = C.zScores(fc, fin, [{"from": "2026-11-02", "k": 0.5}])
    assert z == pytest.approx(1.0, rel=1e-3)
    assert C.zScores(fc, [], []) == []


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
