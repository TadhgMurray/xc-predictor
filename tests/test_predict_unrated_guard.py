"""A runner with no rated race before the target keeps the model's time only
when its band is a prediction (owner, 2026-10-05: freshmen at 20:25 for a
college 8K at the top of a D3 field)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts", "model"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import pytest  # noqa: E402

predict = pytest.importorskip("predict")


def _run(monkeypatch, preds, rated):
    monkeypatch.setattr(predict, "_targetSpec", lambda cur, t: {"date": "2026-09-13"})
    monkeypatch.setattr(predict, "_predictTimes", lambda cur, ids, t, spec=None: preds)
    monkeypatch.setattr(predict, "_ratingTimes", lambda cur, ids, spec, cut: rated)
    monkeypatch.delenv("XCP_PREDICT_BASIS", raising=False)
    return predict._servedTimes(None, [1, 2, 3], {"mode": "meet"})


def test_an_unrated_runner_on_a_wide_model_band_is_unscored(monkeypatch):
    preds = [{"seconds": 1225.4, "sigma_pct": 10.0, "is_race_time": True},   # the freshman
             {"seconds": 1500.0, "sigma_pct": 4.0, "is_race_time": True},    # confident model
             {"seconds": 1600.0, "sigma_pct": 3.0, "is_race_time": True}]    # rated
    rated = {3: {"seconds": 1550.0, "basis": "rating", "sigma_pct": 3.0}}
    out = _run(monkeypatch, preds, rated)
    assert out[0]["seconds"] is None and out[0]["model_seconds"] == 1225.4
    assert "No rated race" in out[0]["reason"]
    assert out[1]["seconds"] == 1500.0          # a tight model band still stands
    assert out[2]["seconds"] == 1550.0          # the rating serves the rated one


def test_the_model_runs_only_for_unrated_runners(monkeypatch):
    asked = []

    def fake_predict(cur, ids, t, spec=None):
        asked.append(list(ids))
        return [{"seconds": 1500.0, "sigma_pct": 4.0, "is_race_time": True} for _ in ids]

    monkeypatch.setattr(predict, "_targetSpec", lambda cur, t: {"date": "2026-09-13"})
    monkeypatch.setattr(predict, "_predictTimes", fake_predict)
    monkeypatch.setattr(predict, "_ratingTimes",
                        lambda cur, ids, spec, cut: {1: {"seconds": 1550.0, "basis": "rating",
                                                         "sigma_pct": 3.0}})
    monkeypatch.delenv("XCP_PREDICT_BASIS", raising=False)
    out = predict._servedTimes(None, [1, 2], {"mode": "meet"})
    assert asked == [[2]]
    assert out[0]["seconds"] == 1550.0 and "model_seconds" not in out[0]
    assert out[1]["seconds"] == 1500.0


def test_guard_basis_still_runs_the_model_for_everyone(monkeypatch):
    asked = []
    monkeypatch.setattr(predict, "_targetSpec", lambda cur, t: {"date": "2026-09-13"})
    monkeypatch.setattr(predict, "_predictTimes",
                        lambda cur, ids, t, spec=None: asked.append(list(ids)) or
                        [{"seconds": 1540.0, "sigma_pct": 4.0, "is_race_time": True} for _ in ids])
    monkeypatch.setattr(predict, "_ratingTimes",
                        lambda cur, ids, spec, cut: {1: {"seconds": 1550.0, "basis": "rating",
                                                         "sigma_pct": 3.0}})
    monkeypatch.setenv("XCP_PREDICT_BASIS", "guard")
    out = predict._servedTimes(None, [1, 2], {"mode": "meet"})
    assert asked == [[1, 2]]
    assert out[0]["basis"] == "model"
