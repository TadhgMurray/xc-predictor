"""09c's four scans on side connections (2026-11-20) fold to the same
rosters and team states, key order included, as the one-connection read.

    python -m pytest -q tests/test_school_names_side.py
"""
import contextlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import merge_school_names as M                   # noqa: E402

_ROWS = {
    "results": [("Oak", 1), ("Oak", 2), ("Elm", 3), ("Oak", 9), ("Pine", 4)],
    "results_tf": [("Elm", 5), ("Ash", 6), ("Ash", 7), ("Oak", 1), ("Pine", 8)],
}
_TEAMS = {"results": [("Oak", "CA"), ("Elm", "OR")], "results_tf": [("Oak", "NV")]}


class _Cur:
    def __init__(self):
        self.q = ""

    def execute(self, sql, params=None):
        self.q = (sql, params)

    def fetchone(self):
        return (1,)                               # _hasColumn: team_id is there

    def fetchall(self):
        sql = self.q[0]
        table = "results_tf" if "results_tf" in sql else "results"
        return list((_TEAMS if "anet_team" in sql else _ROWS)[table])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def cursor(self):
        return _Cur()


def test_side_reads_fold_like_the_serial_read(monkeypatch):
    import database
    opened = []

    @contextlib.contextmanager
    def fake_conn():
        opened.append(1)
        yield _Conn()
    monkeypatch.setattr(database, "getConn", fake_conn)
    serial_r, serial_t = M.rosters(_Cur()), M.teamStates(_Cur())
    side = M.sideReads()
    try:
        got_r, got_t = M.rosters(_Cur(), side=side), M.teamStates(_Cur(), side=side)
    finally:
        side.close()
    assert len(opened) == 4                       # one connection per scan
    assert list(got_r.items()) == list(serial_r.items())
    assert [list(v) for v in got_r.values()] == [list(v) for v in serial_r.values()]
    assert list(got_t.items()) == list(serial_t.items())
    monkeypatch.setenv("XCP_SCHOOL_NAMES_SIDE", "0")
    assert M.sideReads() is None
