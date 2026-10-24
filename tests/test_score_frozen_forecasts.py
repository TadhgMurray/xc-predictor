"""scripts/score_frozen_forecasts.py's pure parts: one stored race against
its result. No database.

    python -m pytest -q tests/test_score_frozen_forecasts.py
"""
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
import score_frozen_forecasts as S                           # noqa: E402


def _race(slow=1.02, n=12):
    """We said 1000 + 10 i; they ran it `slow` x faster than that, in order;
    our bands are +-1.5%."""
    runners = [{"person_id": i, "seconds": 1000.0 + 10 * i,
                "lo": (1000.0 + 10 * i) * 0.985, "hi": (1000.0 + 10 * i) * 1.015,
                "place": i + 1} for i in range(n)]
    fin = [{"person_id": i, "school": "A", "time_seconds": (1000.0 + 10 * i) / slow}
           for i in range(n)]
    fin.append({"person_id": 99, "school": "B", "time_seconds": 5000.0})   # not listed
    teams = [{"team": "A", "state": "MA", "score": 15}, {"team": "B", "state": "MA", "score": 40}]
    actual = [{"team": "A", "state": "MA", "points": 15}, {"team": "B", "state": None, "points": 45}]
    return {"runners": runners, "teams": teams}, fin, actual


def test_a_uniformly_slow_forecast():
    fc, fin, act = _race(slow=1.02)
    m = S.frozenMetrics(fc, fin, act)
    assert all(e == pytest.approx(100 * math.log(1.02)) for e in m["errs"])
    assert all(abs(w) < 1e-9 for w in m["within"])
    assert m["rho"] == pytest.approx(1.0)
    assert m["place_err"] == [0] * 12
    assert m["team_err"] == [0.0, 5.0]          # a state on one side only still matches
    assert m["winner"] is True
    assert m["in_band"] == 0 and m["n_band"] == 12     # 2% off, 1.5% bands
    assert m["listed"] == pytest.approx(12 / 13)


def test_too_few_matched_say_nothing():
    fc, fin, act = _race(n=5)
    assert S.frozenMetrics(fc, fin, act) is None


def test_the_summary():
    fc, fin, act = _race(slow=1.0)
    s = S.summarize([S.frozenMetrics(fc, fin, act)] * 3)
    assert s["races"] == 3 and s["runners"] == 36
    assert s["mae"] == pytest.approx(0.0) and s["cov68"] == 1.0
    assert s["team_err"] == pytest.approx(2.5) and s["winner"] == 1.0
