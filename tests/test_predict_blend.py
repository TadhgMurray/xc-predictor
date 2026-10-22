"""XCP_PREDICT_BASIS=blend: the rating's order at the model's level
(2026-10-22). No model, no database: both sides are stubbed.

    python -m pytest -q tests/test_predict_blend.py
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
import predict as P                                          # noqa: E402

# the model runs every rated runner 4% slower than the rating; runner 7
# has no rating; runner 6's model time is far off (it only nudges the median)
RATED = {1: 900.0, 2: 950.0, 3: 1000.0, 4: 1050.0, 5: 1100.0, 6: 1150.0}
MODEL = {p: {"seconds": s * 1.04, "sigma_pct": 3.0, "is_race_time": True}
         for p, s in RATED.items()}
MODEL[6] = {"seconds": 1150.0 * 1.30, "sigma_pct": 3.0, "is_race_time": True}
MODEL[7] = {"seconds": 1200.0, "sigma_pct": 3.0, "is_race_time": True}


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(P, "_predictTimes",
                        lambda cur, ids, target, spec=None: [dict(MODEL[i]) for i in ids])
    monkeypatch.setattr(P, "_targetSpec", lambda cur, target: {"date": "2026-10-24"})
    monkeypatch.setattr(P, "_ratingTimes", lambda cur, ids, spec, cut: {
        p: {"seconds": s, "lo": s * 0.97, "hi": s * 1.03, "sigma_pct": 3.0,
            "is_race_time": True, "basis": "rating"} for p, s in RATED.items()})
    monkeypatch.setenv("XCP_PREDICT_BASIS", "blend")


def test_blend_factor_is_the_median_ratio_clamped():
    assert P.blendFactor([(104.0, 100.0)] * 5) == pytest.approx(1.04)
    assert P.blendFactor([(104.0, 100.0)] * 4) == 1.0          # too few to say
    assert P.blendFactor([(150.0, 100.0)] * 9) == pytest.approx(math.exp(0.12))
    assert P.blendFactor([(104.0, 100.0)] * 5 + [(300.0, 100.0)]) == pytest.approx(1.04)


def test_blend_keeps_the_ratings_order_at_the_models_level(stubbed):
    out = P._servedTimes(None, [1, 2, 3, 4, 5, 6, 7], {"mode": "rerun"})
    for i, p in enumerate([1, 2, 3, 4, 5, 6]):
        assert out[i]["basis"] == "rating"
        assert out[i]["seconds"] == pytest.approx(RATED[p] * 1.04, abs=0.06)
        assert out[i]["level_shift_pct"] == pytest.approx(4.0)
    # the rating's order, not the model's (runner 6 stays behind 5 by the
    # rating's gap, not the model's 30%)
    assert out[5]["seconds"] / out[4]["seconds"] == pytest.approx(1150 / 1100, rel=1e-3)
    assert out[6]["seconds"] == 1200.0                         # no rating: the model
