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


def test_a_rebuild_asks_the_frozen_basis_with_history_cut_at_the_freeze(monkeypatch):
    import score_frozen_forecasts as S
    seen = {}

    def predict(cur, ids, target):
        seen["target"], seen["basis"] = target, os.environ.get("XCP_PREDICT_BASIS")
        return [{"seconds": 1000.0 + p, "lo": 990.0, "hi": 1010.0} if p != 3 else {}
                for p in ids]
    monkeypatch.setenv("XCP_PREDICT_BASIS", "race")
    fc = {"source": "anet", "meet_id": 5, "div_id": 2, "made_on": "2026-10-20",
          "model_basis": "blend", "runners": [{"person_id": p, "seconds": 900.0}
                                              for p in (1, 2, 3)]}
    got = S.rebuildRace(None, fc, [1, 2, 3], predict)
    assert seen["basis"] == "blend" and os.environ["XCP_PREDICT_BASIS"] == "race"
    assert seen["target"]["history_before"] == "2026-10-20"
    assert [r["person_id"] for r in got["runners"]] == [1, 2]       # 3 had no time
    a, b = S.sharedRunners(fc, got)
    assert [r["person_id"] for r in a["runners"]] == [1, 2] == [r["person_id"] for r in b["runners"]]


def test_the_rating_order_baseline():
    """owner, 2026-11-04: "the model basically just regurgitates ratings" --
    the same runners ordered by rating alone, to score beside the model."""
    import score_frozen_forecasts as S
    fc = {"runners": [{"person_id": 1, "seconds": 900, "rating": 140.0},
                      {"person_id": 2, "seconds": 910, "rating": 145.0},
                      {"person_id": 3, "seconds": 920, "rating": None}]}
    model, by_rating = S.ratingOrder(fc)
    assert [r["person_id"] for r in model["runners"]] == [1, 2]          # rated only
    assert sorted(by_rating["runners"], key=lambda r: r["seconds"])[0]["person_id"] == 2


def test_the_two_archives_averaged_runner_by_runner():
    import score_frozen_forecasts as S
    a = {"runners": [{"person_id": 1, "seconds": 900.0, "lo": 880.0, "hi": 920.0},
                     {"person_id": 2, "seconds": 1000.0}]}
    b = {"runners": [{"person_id": 1, "seconds": 1000.0, "lo": 980.0, "hi": 1020.0}]}
    m = S.mixRunners(a, b)
    assert [r["person_id"] for r in m["runners"]] == [1]
    assert abs(m["runners"][0]["seconds"] - (900.0 * 1000.0) ** 0.5) < 1e-9
    assert m["teams"] == []


def test_the_mean_miss_is_kailos_statistic():
    import math
    import score_frozen_forecasts as S
    errs = [100.0 * math.log(1.02), 100.0 * math.log(0.98)]
    s = S.summarize([{"errs": errs, "within": [0.0], "rho": None, "place_err": [0],
                      "team_err": [], "team_rho": None, "winner": None, "in_band": 0,
                      "n_band": 0, "listed": 1.0}])
    assert abs(s["mean"] - 2.0) < 1e-9
