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
    def toCommonScale(row, scale=None, memo=None):
        row["scaled"] = row.get("scaled", 0) + 1       # once per row, exactly
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
    monkeypatch.setenv("XCP_PREDICT_BASIS", "model")     # every contender modelled
    seen = {}
    hist = {1: [{"date": "2026-09-01"}], 2: [{"date": "2026-09-02"}]}

    class FakeRead:
        wait_s = merge_s = 0.0

        def __init__(self, cur, ids):
            seen["order"] = list(ids)

        def keep(self, wanted):
            seen["kept"] = set(wanted)

        def finish(self, cur):
            return hist
    monkeypatch.setattr(P, "_historyStart", FakeRead)
    monkeypatch.setattr(P, "_targetSpec", lambda cur, t: {"date": "2026-10-17"})
    monkeypatch.setattr(P, "_ratingTimes", lambda cur, ids, spec, cut: {
        1: {"seconds": 900.0}, 2: {"seconds": 910.0}, 3: {"seconds": 905.0}})

    def fake_predict(cur, ids, target, spec=None, history=None):
        seen["history"] = history
        return [{"seconds": 905.0 - i} for i, _ in enumerate(ids)]
    monkeypatch.setattr(P, "_predictTimes", fake_predict)
    squads = {"A": [{"person_id": 1}, {"person_id": 2}], "B": [{"person_id": 3}]}
    out, note = P.byPrediction(None, squads, {"A": 7, "B": 7}, {})
    assert note is None and seen["history"] is hist
    assert seen["order"] == [1, 3, 2]        # every team's first, then second
    assert seen["kept"] == {1, 2, 3}         # the contenders
    assert [e["person_id"] for e in out["A"]] == [2, 1]   # by the model's times


def test_keep_drops_runners_not_yet_read(monkeypatch):
    """Batches already taken are read whole; the rest only if kept."""
    import threading
    gate, calls = threading.Event(), []

    def read(c, batch):
        calls.append(list(batch))
        gate.wait(2)
        return [{"person_id": i} for i in batch]
    import database

    @contextlib.contextmanager
    def fake_getconn():
        yield _Conn()
    monkeypatch.setattr(database, "getConn", fake_getconn)
    q = P._IdQueue(range(1, 11))
    started = P._queueStart(q, read, 3, 1)
    import time
    for _ in range(100):                     # the worker has its first batch
        if calls:
            break
        time.sleep(0.01)
    q.keep({2, 9})
    gate.set()
    rows = P._queueFinish(_Cur(), started)
    assert calls == [[1, 2, 3], [9]]
    assert sorted(r["person_id"] for r in rows) == [1, 2, 3, 9]


# ---- a big field is read in shards: the same rows, runner by runner

class _ShardCur(_Cur):
    calls = []

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self.rows = [(None,)]
        elif "SET LOCAL" in sql:
            self.rows = []
        else:
            sport = "TF" if "TFSQL" in sql else "XC"
            want = set(params[2])
            _ShardCur.calls.append((sport, len(want)))
            self.rows = [(pid, None, f"2026-{m:02d}-{pid % 27 + 1:02d}")
                         for pid in sorted(want)
                         for m in ((4, 5) if sport == "TF" else (9,))]


class _ShardConn(_Conn):
    def cursor(self, *a, **k):
        return _ShardCur()


def _sharded(monkeypatch, chunk, second_conn_ok=True):
    import database
    monkeypatch.setattr(P, "_fx", lambda: _Fx)
    monkeypatch.setattr(P, "_normScale", lambda: None)
    monkeypatch.setattr(P, "READ_CHUNK", {("hist", "XC"): chunk, ("hist", "TF"): chunk})
    monkeypatch.setattr(P, "READ_WORKERS", {("hist", "XC"): 2, ("hist", "TF"): 3})

    @contextlib.contextmanager
    def fake_getconn():
        if not second_conn_ok:
            raise RuntimeError("no pool here")
        yield _ShardConn()
    monkeypatch.setattr(database, "getConn", fake_getconn)
    cur = _ShardCur()
    cur.connection = _ShardConn()
    _ShardCur.calls = []
    return P._historyRows(cur, list(range(1, 301)))


def test_pieces_read_the_same_rows(monkeypatch):
    """At most `chunk` runners a query (owner, 2026-10-18: a quarter of the
    field 0.27 s, all of it 4.46 s); the rows per runner never change."""
    one = _sharded(monkeypatch, 1000)
    assert sorted(_ShardCur.calls) == [("TF", 300), ("XC", 300)]
    pieces = _sharded(monkeypatch, 75)
    assert sorted(_ShardCur.calls) == [("TF", 75)] * 4 + [("XC", 75)] * 4
    assert pieces == one and len(pieces) == 300 and len(pieces[17]) == 3
    assert {r["scaled"] for rows in pieces.values() for r in rows} == {1}
    odd = _sharded(monkeypatch, 70)                  # 4 batches of 70, then 20
    assert sorted(_ShardCur.calls) == ([("TF", 20)] + [("TF", 70)] * 4
                                       + [("XC", 20)] + [("XC", 70)] * 4)
    assert odd == one
    assert _sharded(monkeypatch, 75, second_conn_ok=False) == one   # all in line
