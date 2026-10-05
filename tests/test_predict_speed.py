# Project: xc-predictor / tests
# File:    test_predict_speed.py
# Purpose: the prediction path does each piece of work once, and doing it
#          once changes no number (owner, 2026-10-05: "speed up the
#          predictions and the loading of the squads. They're pretty slow").
#
# No model, no database: the stages are stubbed and counted. The model's own
# forward pass is not here -- it needs torch -- and it was not touched; the
# simulation's equivalence lives in tests/test_race_sim.py.
#
#   python -m pytest -q tests/test_predict_speed.py
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

SPEC = {"date": "2026-10-25", "distance_meters": 5000.0, "sport": "XC"}


@pytest.fixture
def counted(monkeypatch):
    seen = {"spec": 0, "predict_spec": [], "rating_spec": []}

    def targetSpec(cur, target):
        seen["spec"] += 1
        return dict(SPEC)

    def predictTimes(cur, ids, target, spec=None):
        seen["predict_spec"].append(spec)
        return [{"seconds": 1000.0 + i, "sigma_pct": 3.0, "is_race_time": True,
                 "normalized": 990.0 + i} for i, _ in enumerate(ids)]

    def ratingTimes(cur, ids, spec, cut):
        seen["rating_spec"].append((spec, cut))
        return {ids[0]: {"seconds": 950.0, "lo": 930.0, "hi": 970.0,
                         "sigma_pct": 2.5, "is_race_time": True,
                         "basis": "rating"}}

    monkeypatch.setattr(P, "_targetSpec", targetSpec)
    monkeypatch.setattr(P, "_predictTimes", predictTimes)
    monkeypatch.setattr(P, "_ratingTimes", ratingTimes)
    monkeypatch.delenv("XCP_PREDICT_BASIS", raising=False)
    return seen


def test_the_target_is_resolved_once_and_shared(counted):
    """_predictTimes and the rating guard each ran _targetSpec -- the meet
    row, its course, its difficulty, a min(date) over its results -- for the
    same target in the same request."""
    out = P._servedTimes(None, [7, 8], {"mode": "meet", "meet_id": 1})
    assert counted["spec"] == 1
    assert counted["predict_spec"] == [SPEC]
    assert counted["rating_spec"][0][0] == SPEC
    assert str(counted["rating_spec"][0][1]) == "2026-10-25"
    # and the served numbers are what they were: the rating where there is
    # one, the model elsewhere. Since 2026-10-05 (the model only for who
    # needs it) the model is asked for the unrated runner alone, so the
    # rated one carries no model time beside it.
    assert out[0]["seconds"] == 950.0 and "model_seconds" not in out[0]
    assert out[1]["seconds"] == 1000.0 and "basis" not in out[1]


def test_a_target_that_cannot_resolve_still_fails_where_it_did(monkeypatch):
    """! The first resolve failing must not swallow the error: _predictTimes
    resolves again and raises from there, as it always has."""
    def boom(cur, target):
        raise RuntimeError("no such meet")

    class Cur:
        class connection:
            @staticmethod
            def rollback():
                pass

    monkeypatch.setattr(P, "_targetSpec", boom)

    def predictTimes(cur, ids, target, spec=None):
        assert spec is None
        return P._targetSpec(cur, target)
    monkeypatch.setattr(P, "_predictTimes", predictTimes)
    with pytest.raises(RuntimeError):
        P._servedTimes(Cur(), [1], {"mode": "meet", "meet_id": 1})


def test_the_meets_field_is_read_once_per_prediction(monkeypatch):
    """_teamRosters read the same meet and division twice: once for the
    field, once again as evidence for the teams' states."""
    calls = []
    rows = [{"person_id": i, "school": "Alpha" if i < 6 else "Beta",
             "name": f"R{i}", "grade": "11", "pool": "hs_m", "rating": 100.0}
            for i in range(1, 11)]

    def exactField(cur, meet_id, div, sport, source=None):
        calls.append((meet_id, div, sport))
        return [dict(r) for r in rows]

    states_seen = []

    def teamStates(cur, evidence, meet_state=None):
        states_seen.append([dict(e) for e in evidence])
        return {"Alpha": "OR", "Beta": "WA"}

    monkeypatch.setattr(P, "_exactField", exactField)
    monkeypatch.setattr(P, "_teamStates", teamStates)
    monkeypatch.setattr(P, "_meetState", lambda *a, **k: None)
    monkeypatch.setattr(P, "_stateOf", lambda s: None)
    out = P._teamRosters(None, [], {"mode": "rerun_exact", "meet_id": 5,
                                    "div_id": "9", "sport": "XC"})
    assert calls == [(5, 9, "XC")]
    assert [e["school_state"] for e in out] == ["OR"] * 5 + ["WA"] * 5
    # the evidence is what it was: the entries, then a clean copy of the
    # field, unstamped
    ev = states_seen[0]
    assert len(ev) == 20 and all("school_state" not in e for e in ev[10:])


def test_warm_up_never_raises_and_says_what_ran(monkeypatch):
    """A worker that cannot warm up still serves: every step is optional."""
    def boom(*a, **k):
        raise RuntimeError("no database here")
    monkeypatch.setattr(P, "_loadModel", boom)
    monkeypatch.setattr(P, "_fx", boom)
    monkeypatch.setattr(P, "_fc", lambda: None)
    import conversions
    monkeypatch.setattr(conversions, "_norm_from_rating", boom)
    import pool_view
    monkeypatch.setattr(pool_view, "stampBoardRows", lambda rows, **k: None)
    done = P.warm()
    assert "model" not in done and "features" not in done
    assert "forecast" in done and "race_sim" in done
    assert "board_scale" in done


def test_the_model_loads_once_under_two_threads(monkeypatch):
    import threading
    import time
    loads = []

    def slowLoad():
        loads.append(1)
        time.sleep(0.05)
        P._model = object()
        return P._model

    monkeypatch.setattr(P, "_model", None)
    monkeypatch.setattr(P, "_load_error", None)
    monkeypatch.setattr(P, "_loadModelLocked", slowLoad)
    ts = [threading.Thread(target=P._loadModel) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(loads) == 1


def test_the_app_warms_only_under_gunicorn_or_when_asked():
    src = open(os.path.join(_ROOT, "racecast", "app.py"),
               encoding="utf-8").read()
    i = src.index("_PREWARM = ")
    block = src[i:i + 400]
    assert 'XCP_PREWARM' in block and '"gunicorn" in' in block
    assert "daemon=True" in block


def test_the_cache_peeks_and_drops_the_expired_before_the_old(monkeypatch):
    """The squads share ttlcache with /projections: five-minute squads must
    not push out a six-hour page while their own dead entries sit there."""
    import ttlcache
    ttlcache.clear()
    monkeypatch.setattr(ttlcache, "_MAX_KEYS", 10)
    clock = [1000.0]
    now = lambda: clock[0]                                    # noqa: E731
    ttlcache.get("page", lambda: "projections", ttl=6 * 3600, now=now)
    assert ttlcache.peek("page", now=now) == "projections"
    assert ttlcache.peek("absent", now=now) is None
    clock[0] += 1
    for i in range(9):
        ttlcache.get(("squad", i), lambda: i, ttl=300, now=now)
    clock[0] += 301                         # every squad is now expired
    assert ttlcache.peek(("squad", 0), now=now) is None
    ttlcache.get("new", lambda: "n", ttl=300, now=now)
    assert ttlcache.peek("page", now=now) == "projections"
    assert ttlcache.peek("new", now=now) == "n"
    ttlcache.clear()
