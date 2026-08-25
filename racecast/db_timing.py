"""
db_timing.py -- every query the website runs, timed.

★ ONE CHOKE-POINT, PATCHED IN, NOT SPRINKLED. app.py imports this before
  any other racecast module, and it replaces database.getConn with a
  timing wrapper -- so rankings, teams, panels, school, everything that
  does `from database import getConn` after this import is timed with
  zero call-site changes. The scrapers and the engine never import this
  module and stay untouched.

What it records, per execute(): elapsed ms, a collapsed head of the SQL,
and the request path (so /debug/queries can say WHICH page hurt). The
last 500 land in a ring buffer served by /debug/queries; anything over
SLOW_MS also appends to slow_queries.log next to this file, so a slow
page leaves evidence even after a restart.
"""

import os
import time
from collections import deque
from contextlib import contextmanager

import database as _database

_REAL_GETCONN = _database.getConn

SLOW_MS = 250
RECENT = deque(maxlen=500)
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "slow_queries.log")


def _requestPath():
    try:
        from flask import has_request_context, request
        if has_request_context():
            return request.full_path if request.args else request.path
    except Exception:                    # noqa: BLE001 -- outside flask
        pass
    return "-"


def _head(sql, n=240):
    return " ".join(str(sql).split())[:n]


def _record(sql, ms):
    path = _requestPath()
    RECENT.append({"ts": time.time(), "ms": ms, "sql": _head(sql),
                   "path": path})
    if ms >= SLOW_MS:
        try:
            with open(_LOG_PATH, "a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                         f"{ms:8.1f}ms  {path}  {_head(sql)}\n")
        except OSError:
            pass


class _TimedCursor:
    """Delegating proxy: times execute/executemany, passes through the
    rest (fetches, iteration, context management)."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=None):
        t0 = time.perf_counter()
        try:
            return self._cur.execute(sql, params)
        finally:
            _record(sql, (time.perf_counter() - t0) * 1000.0)

    def executemany(self, sql, seq):
        t0 = time.perf_counter()
        try:
            return self._cur.executemany(sql, seq)
        finally:
            _record(sql, (time.perf_counter() - t0) * 1000.0)

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def __iter__(self):
        return iter(self._cur)

    def __enter__(self):
        self._cur.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cur.__exit__(*exc)


class _TimedConn:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return _TimedCursor(self._conn.cursor(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._conn, name)


@contextmanager
def getConn():
    with _REAL_GETCONN() as conn:
        yield _TimedConn(conn)


def summary(limit=40):
    """(slowest recent, aggregate by statement) for /debug/queries."""
    rows = sorted(RECENT, key=lambda r: -r["ms"])[:limit]
    agg = {}
    for r in RECENT:
        a = agg.setdefault(r["sql"][:120], {"n": 0, "total": 0.0, "max": 0.0})
        a["n"] += 1
        a["total"] += r["ms"]
        a["max"] = max(a["max"], r["ms"])
    by_total = sorted(agg.items(), key=lambda kv: -kv[1]["total"])[:limit]
    return rows, by_total


# ★ THE PATCH. Everything imported after this sees the timed getConn.
_database.getConn = getConn
