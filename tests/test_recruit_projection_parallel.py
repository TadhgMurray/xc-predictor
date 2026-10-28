# Project: xc-predictor / tests
# File:    test_recruit_projection_parallel.py
# Purpose: 10f2 on several processes (owner, 2026-10-25: "you can
#          multithread/core as necessary. You have freedom as long as it
#          remains accurate and working"). The chunks go to spawned workers
#          and come back in order; this runs the SAME candidates through the
#          in-process loop and through real worker processes and asks for the
#          same rows, the same values, in the same order. No model, no
#          database: _predictTimes and getConn are stand-ins, in this process
#          and -- through _workerInit's hook -- in every worker.
#
#   python -m pytest -q tests/test_recruit_projection_parallel.py
import contextlib
import os
import sys
import types

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model"),
           os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import datetime as dt                                        # noqa: E402

import pytest                                                # noqa: E402

import build_recruit_projection as B                         # noqa: E402

_HOOK = "test_recruit_projection_parallel:_childHook"
_CALLS = {"n": 0}            # stand-in predictions made in THIS process


def _fakePredictTimes(cur, person_ids, target):
    """A stand-in with _predictTimes' shape. Deterministic in the ids -- and,
    like the real one (which sorts and pads WITHIN a call), in which ids
    share the call: `normalized` moves with the chunk's first id and size,
    so a worker handed a different chunk would give different numbers."""
    assert cur is not None and cur.kind == "fake"
    _CALLS["n"] += 1
    ids = list(person_ids)
    if any(int(p) == 666 for p in ids):
        raise RuntimeError("a chunk that fails")
    batch = (int(ids[0]) % 13) * 1e-3 + len(ids) * 1e-4
    hist = 0.0 if not target.get("history_before") else 0.5
    out = []
    for p in ids:
        p = int(p)
        if p % 11 == 0:
            out.append({"seconds": None, "reason": "No rated races."})
            continue
        base = 900.0 + (p * 37 % 400) + hist
        norm = round(base * (1.0 - ((p * 7919) % 60 - 20) / 1000.0) + batch, 1)
        sig = round(2.0 + (p * 31 % 300) / 10.0, 2)    # some past MAX_SIGMA_PCT
        out.append({"baseline": base, "normalized": norm, "sigma_pct": sig})
    return out


class _FakeCursor:
    kind = "fake"

    def __init__(self):
        self.connection = types.SimpleNamespace(commit=lambda: None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def cursor(self, cursor_factory=None):
        return _FakeCursor()


@contextlib.contextmanager
def _fakeGetConn():
    yield _FakeConn()


def _stubModules():
    pred = types.ModuleType("predict")
    pred._predictTimes = _fakePredictTimes
    pred.READ_WORKERS = {("hist", "XC"): 2, ("hist", "TF"): 2,
                         ("rate", "XC"): 1, ("rate", "TF"): 2}
    nd = types.ModuleType("normalize_distance")
    nd.LOADS = 0

    def loadGraphLevels():
        nd.LOADS += 1
        return {}
    nd.loadGraphLevels = loadGraphLevels
    return pred, nd


def _childHook():
    """Runs first in every spawned worker (_workerInit's hook): the same
    stand-ins this process uses, in place of the model and the database."""
    pred, nd = _stubModules()
    sys.modules["predict"] = pred
    sys.modules["normalize_distance"] = nd
    B.getConn = _fakeGetConn


def _workerState():
    """Read back, in a worker, what _workerInit set there."""
    import database
    return {"levels_loaded": sys.modules["normalize_distance"].LOADS,
            "read_workers": dict(sys.modules["predict"].READ_WORKERS),
            "min_conn": database.MIN_CONN,
            "pid": os.getpid()}


def _rows(n):
    # candidates() order: rating descending, then person_id
    rows = [{"person_id": 1000 + i, "mean_rating": round(130.0 - i * 0.017, 3),
             "n_races": 3 + i % 9} for i in range(n)]
    return rows


@pytest.fixture
def stubbed(monkeypatch):
    pred, nd = _stubModules()
    monkeypatch.setitem(sys.modules, "predict", pred)
    monkeypatch.setitem(sys.modules, "normalize_distance", nd)
    return pred


def _build(rows, monkeypatch, workers, chunk):
    monkeypatch.setattr(B, "candidates", lambda *a, **k: list(rows))
    return B.buildOne(_FakeCursor(), "hs_m", "TF", 2025, None, 52, None,
                      chunk=chunk, verbose=True, as_of=dt.date(2026, 8, 1),
                      past=True, workers=workers)


def test_the_workers_give_the_same_rows_values_and_order(stubbed, monkeypatch,
                                                          capsys):
    """★ THE WHOLE POINT. 2,345 candidates in chunks of 100 (24 chunks, the
    last one short) through the in-process loop, then through three spawned
    workers: every tuple equal -- row, projected rating, gain, percentage,
    sigma, residual, band, band size -- in the same order, and the progress
    lines the log shows are the same lines."""
    rows = _rows(2345)
    _CALLS["n"] = 0
    serial = _build(rows, monkeypatch, None, 100)
    serial_log = capsys.readouterr().out
    assert _CALLS["n"] == 24                     # it ran here, chunk by chunk
    assert len(serial) > 1000                    # and most rows were usable
    assert any(g[5] is not None for g in serial)

    ex = B.startWorkers(3, hook=_HOOK)
    try:
        _CALLS["n"] = 0
        parallel = _build(rows, monkeypatch, ex, 100)
        parallel_log = capsys.readouterr().out
        assert _CALLS["n"] == 0                  # none of it ran here
        states = [ex.submit(_workerState).result() for _ in range(6)]
    finally:
        ex.shutdown(wait=True)

    assert parallel == serial
    # exact, not approximately: the same bits in every float
    assert repr(parallel) == repr(serial)
    assert [g[0]["person_id"] for g in parallel] == \
        [g[0]["person_id"] for g in serial]
    assert parallel_log == serial_log
    assert "2,000/2,345" in serial_log

    for st in states:
        assert st["pid"] != os.getpid()
        # the school-level lookup loaded once, before any chunk's readers
        assert st["levels_loaded"] == 1
        # one history reader per sport in a worker; the ratings untouched
        assert st["read_workers"][("hist", "XC")] == 1
        assert st["read_workers"][("hist", "TF")] == 1
        assert st["read_workers"][("rate", "TF")] == 2
        assert st["min_conn"] == B.PROJ_WORKER_CONNS


def test_one_chunk_and_an_empty_slice_are_the_same_too(stubbed, monkeypatch):
    ex = B.startWorkers(2, hook=_HOOK)
    try:
        for n in (0, 1, 7):
            rows = _rows(n)
            assert _build(rows, monkeypatch, ex, 400) == \
                _build(rows, monkeypatch, None, 400)
    finally:
        ex.shutdown(wait=True)


def test_a_failing_chunk_fails_the_step_rather_than_hanging(stubbed,
                                                            monkeypatch):
    """! A chunk that raises in a worker raises here, as it did in line."""
    rows = _rows(50) + [{"person_id": 666, "mean_rating": 90.0, "n_races": 4}]
    with pytest.raises(RuntimeError, match="a chunk that fails"):
        _build(rows, monkeypatch, None, 10)
    ex = B.startWorkers(2, hook=_HOOK)
    try:
        with pytest.raises(RuntimeError, match="a chunk that fails"):
            _build(rows, monkeypatch, ex, 10)
    finally:
        ex.shutdown(wait=True)


def test_one_worker_is_the_old_loop_in_this_process(monkeypatch):
    assert B.startWorkers(1) is None
    assert B.startWorkers(0) is None
    monkeypatch.setenv("XCP_PROJ_WORKERS", "1")
    assert B.projWorkers() == 1
    monkeypatch.setenv("XCP_PROJ_WORKERS", "12")
    assert B.projWorkers() == 12
    # unset or unreadable: the cores less four, at most PROJ_WORKERS_MAX
    for raw in ("", "x", "0"):
        monkeypatch.setenv("XCP_PROJ_WORKERS", raw)
        monkeypatch.setattr(B.os, "cpu_count", lambda: 24)
        assert B.projWorkers() == B.PROJ_WORKERS_MAX == 6
        monkeypatch.setattr(B.os, "cpu_count", lambda: 4)
        assert B.projWorkers() == 1


def test_the_chunks_are_the_loops_chunks():
    """! THE SAME CHUNKS, SO THE SAME NUMBERS -- _predictTimes batches the
    network within a call, so a worker must get exactly rows[i:i + CHUNK].
    And spawn, not fork: a forked child running torch's threads after the
    parent had hung in the 2026-10-25 measurement."""
    src = open(os.path.join(_ROOT, "racecast",
                            "build_recruit_projection.py")).read()
    body = src[src.index("def buildOne"):]
    body = body[:body.index("\ndef ")]
    assert "parts = [rows[i:i + chunk] for i in range(0, len(rows), chunk)]" in body
    assert "workers.map(_workerChunk, parts" in body
    assert 'get_context("spawn")' in src
    assert B.CHUNK == 400
