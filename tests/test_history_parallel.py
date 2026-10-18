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
