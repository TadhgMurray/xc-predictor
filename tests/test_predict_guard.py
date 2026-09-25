"""The page's predictions are checked against the athletes' own ratings
(owner, 2026-09-25: "why does a 15 min 5ker get predicted at 30 mins").
No model, no database: the model's output and the rating times are stubbed.

    python -m pytest -q tests/test_predict_guard.py
"""
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

MODEL = [
    {"seconds": 1905.6, "lo": 1215.5, "hi": 2987.5, "sigma_pct": 45.0,
     "is_race_time": True},                     # Clark Gregory: 31:45, 20-50 min
    {"seconds": 1001.0, "lo": 975.0, "hi": 1027.0, "sigma_pct": 2.6,
     "is_race_time": True},                     # sane, agrees with his ratings
    {"seconds": 1180.0, "lo": 1150.0, "hi": 1210.0, "sigma_pct": 2.5,
     "is_race_time": True},                     # tight but 18% slow
    {"seconds": None, "reason": "No rated races in the corpus."},
    {"seconds": 990.0, "sigma_pct": 3.0, "is_race_time": True},  # no ratings
]
RATED = {1: 960.0, 2: 995.0, 3: 1000.0, 4: 1010.0}


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(P, "_predictTimes",
                        lambda cur, ids, target: [dict(m) for m in MODEL])
    monkeypatch.setattr(P, "_targetSpec", lambda cur, target: {"date": "2024-11-23"})
    monkeypatch.setattr(P, "_ratingTimes", lambda cur, ids, spec, cut: {
        pid: {"seconds": s, "lo": s * 0.97, "hi": s * 1.03, "sigma_pct": 3.0,
              "is_race_time": True, "basis": "rating"}
        for pid, s in RATED.items()})
    monkeypatch.delenv("XCP_PREDICT_BASIS", raising=False)


def test_the_guard_serves_ratings_where_the_model_is_out_of_line(stubbed):
    out = P._servedTimes(None, [1, 2, 3, 4, 5], {"mode": "rerun"})
    assert out[0]["basis"] == "rating" and out[0]["seconds"] == 960.0
    assert out[0]["model_seconds"] == 1905.6            # kept for the hover
    assert out[1]["basis"] == "model" and out[1]["seconds"] == 1001.0
    assert out[2]["basis"] == "rating"                  # tight but far off
    assert out[3]["seconds"] == 1010.0 and "reason" not in out[3]
    assert out[4]["seconds"] == 990.0                   # nothing to check against


def test_basis_model_is_the_raw_network(stubbed, monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_BASIS", "model")
    out = P._servedTimes(None, [1, 2, 3, 4, 5], {"mode": "rerun"})
    assert out[0]["seconds"] == 1905.6


def test_basis_rating_serves_ratings_for_everyone_rated(stubbed, monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_BASIS", "rating")
    out = P._servedTimes(None, [1, 2, 3, 4, 5], {"mode": "rerun"})
    assert [o.get("basis") for o in out[:4]] == ["rating"] * 4


def test_form_rating_uses_the_latest_pool_and_drops_a_fall():
    rows = [("2024-11-09", 130.0, "hs_m"), ("2024-10-26", 131.0, "hs_m"),
            ("2024-10-12", 95.0, "hs_m"),               # a fall: dropped
            ("2024-09-28", 128.0, "hs_m"),
            ("2024-06-01", 140.0, "ms_m")]              # another pool: ignored
    rating, sig, pool, n = P._formRating(rows)
    assert pool == "hs_m" and n == 3
    assert 128.0 < rating < 131.0
    assert P._RATING_SIGMA[0] <= sig <= P._RATING_SIGMA[1]


def test_only_the_page_is_guarded():
    src = open(os.path.join(_ROOT, "racecast", "predict.py")).read()
    assert src.count("_servedTimes(cur, [") == 3 and "_servedTimes(cur, [person_id]" in src
    for f in ("racecast/build_recruit_projection.py", "scripts/diag_model_quality.py"):
        assert "_servedTimes" not in open(os.path.join(_ROOT, f)).read()
