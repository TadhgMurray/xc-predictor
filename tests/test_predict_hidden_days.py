"""What the predictor tells the model about the gap to the target
(owner, 2026-10-08): only (target - today) can hide races, and inside a
week the race is a plain next race, not a forecast.

    python -m pytest -q tests/test_predict_hidden_days.py
"""
import datetime
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import predict as P                                          # noqa: E402

D = datetime.date


def test_inside_a_week_is_not_a_forecast():
    assert P._hiddenDays(D(2026, 10, 14), D(2026, 10, 8)) == (False, 0.0)
    assert P._hiddenDays(D(2026, 10, 8), D(2026, 10, 8)) == (False, 0.0)
    # a past target (an as-it-ran re-run) hides nothing either
    assert P._hiddenDays(D(2025, 11, 22), D(2026, 10, 8)) == (False, 0.0)


def test_three_weeks_out_hides_three_weeks_not_the_idle_year():
    assert P._hiddenDays(D(2026, 10, 29), D(2026, 10, 8)) == (True, 21.0)
    assert P._hiddenDays(D(2026, 10, 15), D(2026, 10, 8)) == (True, 7.0)


class _Fx:
    """feature_extraction's surface _forecastExample touches."""
    MAX_SEQ_LEN = 512
    CONTEXT_YEAR_INDEX = 23

    def __init__(self):
        self.calls = []

    def _baseVectors(self, hist, enc):
        return [[0.0] * 23 for _ in hist]

    def _parseDate(self, s):
        return D.fromisoformat(s[:10])

    def _buildContextVector(self, row, seq, prior, enc, is_forecast=False,
                            hidden_days=0.0):
        self.calls.append((is_forecast, hidden_days))
        return [1.0 if is_forecast else 0.0] + [0.0] * 23 + [hidden_days]


def test_the_example_carries_the_hidden_span_and_old_checkpoints_get_24(monkeypatch):
    fx = _Fx()
    hist = [{"date": "2025-10-01"}]                  # idle a year
    row = {"date": "2026-10-29"}
    monkeypatch.setattr(P, "_artifacts", {"context_features": 25})
    _seq, ctx = P._forecastExample(fx, hist, row, {}, as_of=D(2026, 10, 8))
    assert fx.calls[-1] == (True, 21.0) and len(ctx) == 25 and ctx[-1] == 21.0
    # a checkpoint trained before hidden_days is fed the 24 it knows
    monkeypatch.setattr(P, "_artifacts", {"context_features": 24})
    _seq, ctx = P._forecastExample(fx, hist, row, {}, as_of=D(2026, 10, 8))
    assert len(ctx) == 24
    # within a week of the race: a plain next race
    _seq, ctx = P._forecastExample(fx, hist, {"date": "2026-10-12"}, {},
                                   as_of=D(2026, 10, 8))
    assert fx.calls[-1] == (False, 0.0)


def test_an_old_checkpoint_gets_its_21_wide_sequence(monkeypatch):
    fx = _Fx()
    monkeypatch.setattr(P, "_artifacts", {"context_features": 24,
                                          "sequence_features": 21})
    seq, ctx = P._forecastExample(fx, [{"date": "2026-09-01"}],
                                  {"date": "2026-10-29"}, {}, as_of=D(2026, 10, 8))
    assert all(len(v) == 21 for v in seq) and len(ctx) == 24
