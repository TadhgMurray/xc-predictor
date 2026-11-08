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
        # ⚠ encoding= IS THE FIX FOR A REAL 500. Without it Windows opens
        #   the log as cp1252, and the first slow query whose SQL contained
        #   a ★ comment blew up the WRITE -- inside cursor.execute, taking
        #   the page down. /search was "broken" for two sweeps because the
        #   PROFILER crashed logging the evidence that search was slow.
        # ⚠ except Exception, not OSError. UnicodeEncodeError is a
        #   ValueError, so the old guard let it through. A recorder is
        #   never allowed to hurt the page it measures.
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                         f"{ms:8.1f}ms  {path}  {_head(sql)}\n")
        except Exception:                # noqa: BLE001
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


try:
    from collections import OrderedDict as _OrderedDict
    import psycopg2.extensions as _pgext
    from psycopg2.extras import RealDictCursor as _RealDictCursor, RealDictRow as _RealDictRow
except Exception:                        # noqa: BLE001 -- no psycopg2: nothing to swap
    _RealDictCursor = None


if _RealDictCursor is not None:
    _odSet = _OrderedDict.__setitem__
    _newRow = _OrderedDict.__new__
    _cFetchone = _pgext.cursor.fetchone
    _cFetchmany = _pgext.cursor.fetchmany
    _cFetchall = _pgext.cursor.fetchall
    _cNext = _pgext.cursor.__next__

    class FastRealDictCursor(_RealDictCursor):
        """★ RealDictCursor's rows for half the CPU (2026-11-08).

        RealDictCursor has the C cursor call RealDictRow.__setitem__ -- a
        Python method that looks the column up in a mapping it keeps inside
        the row -- once per column of every row, after a Python __init__
        per row. Measured (client CPU, 15 columns): 2,000 rows 12.5 ->
        6.0 ms, 300 rows 2.0 -> 1.0 ms, 50 rows 0.36 -> 0.18 ms. Here the
        C cursor hands back tuples and each row is filled with
        OrderedDict.__setitem__ in column order. The rows are the SAME
        class, RealDictRow, with the same keys in the same order, the same
        values, the same duplicate-column rule (first position, last
        value), repr and pickle, so every page reads exactly what it read
        (tests/test_fast_dict_cursor.py compares them on Postgres).
        _TimedConn.cursor hands one out wherever RealDictCursor was asked
        for, so no call site changes; XCP_DB_FAST_ROWS=0 turns it off."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.row_factory = None          # the C cursor returns tuples

        def _rows(self, tuples):
            if self._query_executed:
                self._build_index()
            cols = self.column_mapping
            out = []
            if not cols:                     # as RealDictRow leaves it
                for _t in tuples:
                    r = _newRow(_RealDictRow)
                    _odSet(r, _RealDictRow, cols)
                    out.append(r)
                return out
            for t in tuples:
                # __new__ without __init__: RealDictRow() with no arguments
                # does nothing more (the same path pickle and copy take)
                r = _newRow(_RealDictRow)
                for k, v in zip(cols, t):
                    _odSet(r, k, v)
                out.append(r)
            return out

        def fetchone(self):
            t = _cFetchone(self)
            return None if t is None else self._rows((t,))[0]

        def fetchmany(self, *args, **kwargs):
            return self._rows(_cFetchmany(self, *args, **kwargs))

        def fetchall(self):
            return self._rows(_cFetchall(self))

        def __next__(self):
            return self._rows((_cNext(self),))[0]

        def __iter__(self):
            while True:
                try:
                    t = _cNext(self)
                except StopIteration:
                    return
                yield self._rows((t,))[0]


_FAST_ROWS = (_RealDictCursor is not None
              and os.environ.get("XCP_DB_FAST_ROWS", "1") != "0")


class _TimedConn:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        if _FAST_ROWS and kwargs.get("cursor_factory") is _RealDictCursor:
            kwargs["cursor_factory"] = FastRealDictCursor
        return _TimedCursor(self._conn.cursor(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ★★ ONE CONNECTION PER REQUEST, NOT ONE PER BLOCK (sweep 2026-10-10, D14).
#    An athlete view opens `with getConn()` four or more times, and each one
#    was a pool checkout plus database.getConn's liveness probe (SELECT 1,
#    a round trip) plus a putconn -- for a request a sync gunicorn worker
#    serves alone, start to finish. Now the first block of a request checks
#    a connection out and parks it on flask.g; later blocks of the same
#    request reuse it; teardown (releaseRequestConn, registered by app.py)
#    puts it back.
#
#  ! WHAT EACH BLOCK SEES IS UNCHANGED. Every block still ends in a
#    rollback (as database.getConn's own exit did), so no transaction, SET
#    LOCAL or savepoint leaks from one block into the next. The site sets no
#    session-level state (no bare SET, no autocommit), so nothing else could.
#  ! ONLY WHEN IT IS FREE. A block opened INSIDE another (school_identity's
#    lookups, the race page's second connection) gets its own pool
#    connection, exactly as before: sharing would let the inner block's
#    rollback undo the outer one's work. Same for another thread (an
#    executor inside a request), which never sees the parked one.
#  ! A BROKEN ONE IS NOT KEPT. InterfaceError/OperationalError inside a
#    block hands the exception to database.getConn's own exit, which closes
#    the connection rather than pooling it, and the slot is emptied -- so
#    app._db_blip's retry draws a fresh, probed connection.
#  ! XCP_DB_REQUEST_CONN=0 turns it off (every block straight to the pool).
_REQUEST_CONN = os.environ.get("XCP_DB_REQUEST_CONN", "1") != "0"


def _requestSlot():
    """This request's connection slot when this block may use it, else
    None (no request, turned off, held by an outer block, other thread)."""
    if not _REQUEST_CONN:
        return None
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return None
    except Exception:                    # noqa: BLE001 -- outside flask
        return None
    import threading
    slot = g.get("_db_slot")
    if slot is None:
        slot = {"cm": None, "conn": None, "busy": False,
                "tid": threading.get_ident()}
        g._db_slot = slot
    if slot["busy"] or slot["tid"] != threading.get_ident():
        return None
    return slot


def _dropSlot(slot, exc_info=(None, None, None)):
    """Hand the parked connection back through database.getConn's exit
    (which rolls back, or closes it when exc_info says it broke)."""
    cm, slot["cm"], slot["conn"] = slot["cm"], None, None
    if cm is not None:
        try:
            cm.__exit__(*exc_info)
        except Exception:                # noqa: BLE001 -- it re-raises exc
            pass


def releaseRequestConn(_exc=None):
    """teardown_appcontext: the request's connection goes back to the pool."""
    try:
        from flask import g
        slot = g.pop("_db_slot", None)
    except Exception:                    # noqa: BLE001
        return
    if slot:
        _dropSlot(slot)


@contextmanager
def getConn():
    slot = _requestSlot()
    if slot is None:
        with _REAL_GETCONN() as conn:
            yield _TimedConn(conn)
        return
    import sys
    import psycopg2
    if slot["conn"] is None:
        cm = _REAL_GETCONN()
        slot["conn"] = cm.__enter__()
        slot["cm"] = cm
    conn = slot["conn"]
    slot["busy"] = True
    try:
        yield _TimedConn(conn)
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        _dropSlot(slot, sys.exc_info())
        raise
    except BaseException:
        try:
            conn.rollback()          # the caller raised: leave no open txn
        except Exception:            # noqa: BLE001
            _dropSlot(slot, sys.exc_info())
        raise
    else:
        try:
            conn.rollback()          # as database.getConn's exit always did
        except Exception:            # noqa: BLE001
            _dropSlot(slot, sys.exc_info())
    finally:
        slot["busy"] = False


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
