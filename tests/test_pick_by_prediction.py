"""Each team's top N by predicted time (owner, 2026-10-11: "predict best
athletes for every team, but you only present/show the top x").
predict.byPrediction shortlists by recent form and its band, runs the model
on the shortlist only ("takes way too long" with the whole squad; "not as
accurate" with form alone), and orders each team by the model's time.

    python -m pytest -q tests/test_pick_by_prediction.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import predict as P                                             # noqa: E402

# team A runs 2. Form (seconds, lo, hi): 1 and 2 are in, 3's fast end
# reaches 2's slow end, 4 cannot; 5 has no form.
FORM = {1: (900, 880, 920), 2: (910, 890, 930), 3: (940, 925, 960),
        4: (1000, 980, 1020)}
SQUADS = {"A": [{"person_id": i} for i in (1, 2, 3, 4, 5)]}


def run(model, model_raises=False, form=FORM, no_clock=(), sigma=None):
    sent = []

    def rating_times(cur, ids, spec, cut):
        return {i: {"seconds": f[0], "lo": f[1], "hi": f[2]}
                for i, f in form.items() if i in ids}

    def predict_times(cur, ids, target, spec=None, history=None):
        sent.extend(ids)
        if model_raises:
            raise RuntimeError("no model")
        return [{"seconds": model.get(i), "is_race_time": i not in no_clock,
                 "time_basis": "own", "sigma_pct": (sigma or {}).get(i)} for i in ids]
    real = (P._ratingTimes, P._predictTimes, P._targetSpec)
    P._ratingTimes, P._predictTimes = rating_times, predict_times
    P._targetSpec = lambda cur, target: {"date": "2026-10-17"}
    try:
        out, note = P.byPrediction(
            None, {k: [dict(e) for e in v] for k, v in SQUADS.items()},
            {"A": 2}, {"mode": "rerun"})
    finally:
        P._ratingTimes, P._predictTimes, P._targetSpec = real
    return [e["person_id"] for e in out["A"]], sorted(sent), note


def test_model_only_for_contenders_and_it_decides(monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_BASIS", "model")      # the raw network
    order, sent, note = run({1: 905, 2: 930, 3: 915, 5: 920})
    assert sent == [1, 2, 3, 5]          # 4 is out of reach, never modelled
    assert order[:2] == [1, 3] and note is None


def test_model_failure_falls_back_to_form_order():
    order, _sent, note = run({}, model_raises=True)
    assert order == [1, 2, 3, 4, 5] and "recent form" in note


def test_no_form_and_no_model_leaves_the_squad():
    order, _sent, note = run({}, form={})
    assert order == [1, 2, 3, 4, 5] and "season rating" in note


def test_a_time_off_the_race_clock_never_orders_the_lineup():
    """owner, 2026-10-18 (WPI: 894 s beside 1367 s): a model time that could
    not be put on this race's clock is a 5K-equivalent, and sorted against
    real race times it jumped the queue. That runner goes by form instead."""
    # 3's model "time" is a 5K-equivalent (600) -- by it, 3 would lead
    order, _sent, note = run({1: 905, 2: 912, 3: 600, 5: 920}, no_clock=(3,))
    assert order[:2] == [1, 2] and note is None


def test_by_default_the_order_is_what_the_prediction_serves(monkeypatch):
    """owner, 2026-10-18 (WPI): the lineup is ordered by the times the
    prediction would serve -- basis "rating", the default: a rated runner
    by their rating's time, the model only for the unrated, and only when
    it is on the race clock and sure (_GUARD_MAX_SIGMA)."""
    monkeypatch.delenv("XCP_PREDICT_BASIS", raising=False)
    # the model would put 3 first (850) -- but 3 is rated, so 3 goes by form
    order, sent, note = run({1: 905, 2: 930, 3: 850, 5: 890}, sigma={5: 4.0})
    assert sent == [5]                   # only the unrated runner is modelled
    assert order[:3] == [5, 1, 2] and note is None   # 5's sure 890 leads

    # an unrated runner whose model band is not a prediction goes after
    order, sent, _ = run({5: 890}, sigma={5: 15.0})
    assert order[:2] == [1, 2] and order.index(5) > order.index(2)


def test_guard_keeps_the_model_only_where_it_agrees(monkeypatch):
    monkeypatch.setenv("XCP_PREDICT_BASIS", "guard")
    # 1: model 905 vs form 900 agrees; 2: model 700 vs form 910, 23% off
    order, sent, _ = run({1: 905, 2: 700, 3: 915, 5: 920},
                         sigma={1: 4.0, 2: 4.0, 3: 4.0, 5: 4.0})
    assert order[:2] == [1, 2]            # 2 by its form (910), not the 700
