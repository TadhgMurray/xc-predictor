"""Each team's top N by predicted time (owner, 2026-10-11: "predict best
athletes for every team, but you only present/show the top x").
predict.byPrediction reorders every squad fastest-predicted first; an
athlete with no prediction keeps rating order behind those who have one,
and a model that cannot run leaves the squads as they came.

    python -m pytest -q tests/test_pick_by_prediction.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import predict as P                                             # noqa: E402

SQUADS = {"A": [{"person_id": 1}, {"person_id": 2}, {"person_id": 3}],
          "B": [{"person_id": 4}, {"person_id": 5}]}


def run(times, raises=False):
    def fake(cur, ids, spec, cut):
        if raises:
            raise RuntimeError("no ratings")
        return {i: {"seconds": times[i]} for i in ids if i in times}
    real = (P._ratingTimes, P._targetSpec)
    P._ratingTimes = fake
    P._targetSpec = lambda cur, target: {"date": "2026-10-17"}
    try:
        return P.byPrediction(None, {k: [dict(e) for e in v] for k, v in SQUADS.items()},
                              ["A", "B"], {"mode": "rerun"})
    finally:
        P._ratingTimes, P._targetSpec = real


def ids(sq):
    return [e["person_id"] for e in sq]


def test_fastest_predicted_first():
    out, note = run({1: 900, 2: 880, 3: 870, 4: 1000, 5: 990})
    assert note is None
    assert ids(out["A"]) == [3, 2, 1] and ids(out["B"]) == [5, 4]
    assert out["A"][0]["pred_seconds"] == 870


def test_unpredicted_keep_rating_order_at_the_back():
    out, _ = run({2: 880, 4: 1000})
    assert ids(out["A"]) == [2, 1, 3] and ids(out["B"]) == [4, 5]


def test_no_model_leaves_the_squads():
    out, note = run({}, raises=True)
    assert note and ids(out["A"]) == [1, 2, 3]
    out, note = run({})
    assert note and ids(out["B"]) == [4, 5]
