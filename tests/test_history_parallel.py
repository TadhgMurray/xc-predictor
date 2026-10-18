"""_historyRows runs the XC and track histories at once (the track query on
its own pooled connection) and falls back to one connection when that
fails; both give the same rows, as plain dicts (2026-10-18).

    python -m pytest -q tests/test_history_parallel.py
"""
import contextlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "model", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import predict as P                                              # noqa: E402

ROWS = {"XC": [(7, None, "2026-09-01"), (8, None, "2026-09-02")],
        "TF": [(7, None, "2026-04-01")]}


class _Cur:
    description = [("person_id",), ("athlete_id",), ("date",)]

    def __init__(self):
        self.rows, self.conn = [], None

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self.rows = [(None,)]
        elif "SET LOCAL" in sql:
            self.rows = []
        else:
            self.rows = ROWS["TF" if "TFSQL" in sql else "XC"]

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def cursor(self, *a, **k):
        return _Cur()

    def rollback(self):
        pass


class _Fx:
    XC_DEFAULT_HOUR, TF_DEFAULT_HOUR, MIN_NORMALIZED_TIME = 9, 15, 100.0

    @staticmethod
    def personResultsSql(sport, has_weather=True):
        return "TFSQL" if sport == "TF" else "XCSQL"

    @staticmethod
    def toCommonScale(row, scale=None):
        return row


def _run(monkeypatch, second_conn_ok):
    import database
    monkeypatch.setattr(P, "_fx", lambda: _Fx)
    monkeypatch.setattr(P, "_normScale", lambda: None)

    @contextlib.contextmanager
    def fake_getconn():
        if not second_conn_ok:
            raise RuntimeError("no pool here")
        yield _Conn()
    monkeypatch.setattr(database, "getConn", fake_getconn)
    cur = _Cur()
    cur.connection = _Conn()
    return P._historyRows(cur, [7, 8])


def test_parallel_and_fallback_agree(monkeypatch):
    a = _run(monkeypatch, True)
    b = _run(monkeypatch, False)
    assert a == b
    assert sorted(a) == [7, 8] and len(a[7]) == 2 and isinstance(a[7][0], dict)
    assert {r["date"] for r in a[7]} == {"2026-09-01", "2026-04-01"}


# ---- _ratingRows: the track read beside the XC one, same rows either way

RATED = {"results": [(7, "2026-09-01", 120.0, "hs_m", None, None)],
         "results_tf": [(7, "2026-04-01", 118.0, "hs_m", "1600", 55),
                        (8, "2026-05-01", 101.0, "hs_m", "3200", 56)]}


class _RCur(_Cur):
    def execute(self, sql, params=None):
        if "to_regclass" in sql or "SET LOCAL" in sql:
            self.rows = [(None,)]
        else:
            self.rows = RATED["results_tf" if "results_tf" in sql else "results"]


class _RConn(_Conn):
    def cursor(self, *a, **k):
        return _RCur()


def _rated(monkeypatch, second_conn_ok):
    import database
    monkeypatch.setattr(P, "_HAS_OUTLIERS", False)

    @contextlib.contextmanager
    def fake_getconn():
        if not second_conn_ok:
            raise RuntimeError("no pool here")
        yield _RConn()
    monkeypatch.setattr(database, "getConn", fake_getconn)
    return P._ratingRows(_RCur(), [7, 8], None, "2026-10-18")


def test_rating_rows_parallel_and_fallback_agree(monkeypatch):
    a = _rated(monkeypatch, True)
    b = _rated(monkeypatch, False)
    assert a == b
    assert [r[3] for r in a[7]] == ["XC", "TF"]          # newest first
    assert a[8][0][5] == 56


# ---- byPrediction hands the model the history it read beside the ratings

def test_by_prediction_passes_the_prefetched_history(monkeypatch):
    from concurrent.futures import Future
    seen = {}
    hist = {1: [{"date": "2026-09-01"}], 2: [{"date": "2026-09-02"}]}

    def now(fn):
        f = Future()
        f.set_result(fn(None))
        return f
    monkeypatch.setattr(P, "_ownConnFuture", now)
    monkeypatch.setattr(P, "_historyRows", lambda c, ids: hist)
    monkeypatch.setattr(P, "_targetSpec", lambda cur, t: {"date": "2026-10-17"})
    monkeypatch.setattr(P, "_ratingTimes", lambda cur, ids, spec, cut: {
        1: {"seconds": 900.0}, 2: {"seconds": 910.0}})

    def fake_predict(cur, ids, target, spec=None, history=None):
        seen["history"] = history
        return [{"seconds": 905.0 - i} for i, _ in enumerate(ids)]
    monkeypatch.setattr(P, "_predictTimes", fake_predict)
    squads = {"A": [{"person_id": 1}, {"person_id": 2}]}
    out, note = P.byPrediction(None, squads, {"A": 7}, {})
    assert note is None and seen["history"] is hist
    assert [e["person_id"] for e in out["A"]] == [2, 1]   # by the model's times
