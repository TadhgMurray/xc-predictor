"""
read_routing.py -- the site reads a hot standby; every write goes to the
primary (docs/REPLICA-PLAN.md, deploy/replica/).

    XCP_DB_READ_DSN="host=10.77.0.2"     # libpq conninfo; unset = off
    XCP_DB_READ_APPS=site                # which XCP_DB_APP values route
    XCP_DB_READ_MAX_CONN=3               # read pool size per process
    XCP_DB_READ_MAX_LAG_S=300            # older than this: read the primary
    XCP_DB_PRIMARY_COOKIES=xcp_session   # requests carrying one: primary

database.getConn() hands out a routed connection when this module says
active(); everything else in the tree is unchanged. Nothing here is imported
while XCP_DB_READ_DSN is unset.

★ WHAT GOES WHERE, PER STATEMENT, NEVER PER GUESS ABOUT A PAGE.
  - A statement that reads (SELECT/WITH/VALUES/SHOW/EXPLAIN... with no
    write word anywhere in it) runs on the replica.
  - Anything else -- INSERT/UPDATE/DELETE/MERGE, DDL (a TEMP table too),
    COPY, SELECT ... FOR UPDATE, nextval, advisory locks -- runs on the
    primary, and from then on the WHOLE BLOCK does (db_timing parks one
    connection per request, so: the rest of the request). A page that
    writes and then reads its own write reads the primary.
  - SET / SET LOCAL run where the block is, and the ones a block ran on the
    replica are replayed on the primary when it moves there (usage.flush:
    SET LOCAL statement_timeout, then the upsert).
  ! A false "write" costs one primary round trip; a false "read" is caught:
    the replica refuses it (read-only transaction, unlogged table, "during
    recovery") having done nothing, and the statement runs again on the
    primary. So the classifier leans to "write" and the refusal is the net.
★ WHOLE REQUESTS ON THE PRIMARY. A POST/PUT/PATCH/DELETE, or a request that
  carries the session cookie (XCP_DB_PRIMARY_COOKIES): a visitor who just
  signed in or followed someone must read what they wrote, and the replica
  may be a second behind. Signed-in visitors are few; the anonymous rest,
  nearly all the traffic, read the replica.
★ A STANDBY THAT IS DOWN OR FAR BEHIND IS SKIPPED, A PRIMARY THAT IS DOWN
  STILL SERVES READS. Per process: a replica that refused a connection is
  left alone XCP_DB_READ_RETRY_S (30 s); one more than XCP_DB_READ_MAX_LAG_S
  behind (checked every XCP_DB_READ_CHECK_S, on a connection being handed
  out) gives way to the primary. A primary that cannot be reached is left
  alone XCP_DB_PRIMARY_RETRY_S (15 s): a block that wanted it for READS
  reads the replica instead (the site stays up, read-only, while the main
  box reboots), and a WRITE raises database.PoolUnavailable -- an
  OperationalError, so the site answers it with its 503 page.
! STALE IS CONSISTENT. Physical replication replays the primary's commits
  in order, so a lagging replica shows exactly what the primary showed a
  moment ago -- after a go-live swap, the old boards whole, then the new
  ones whole. Never a half-swapped table.
"""
import os
import re
import sys
import threading
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extensions
import psycopg2.pool

import database as _db

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

_STATE = {"down_until": 0.0, "lag": 0.0, "checked": 0.0, "p_down_until": 0.0}
_POOL = {"pool": None, "slots": None, "key": None}
_LOCK = threading.Lock()
_LOCAL = threading.local()


def _num(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


# --------------------------------------------------------------------------- #
# Is it on?
# --------------------------------------------------------------------------- #
def readDsn():
    """The read DSN for this process, or None. None unless XCP_DB_READ_DSN is
    set, this process is one of XCP_DB_READ_APPS (default: the site) and it
    is not the pipeline (XCP_DB_QUIET)."""
    dsn = (os.environ.get("XCP_DB_READ_DSN") or "").strip()
    if not dsn or _db.dbQuiet():
        return None
    apps = {a.strip() for a in (os.environ.get("XCP_DB_READ_APPS") or "site").split(",")
            if a.strip()}
    if (os.environ.get("XCP_DB_APP") or "xcp") not in apps:
        return None
    return dsn


def active():
    return readDsn() is not None


@contextmanager
def primaryOnly():
    """Every getConn() this thread opens inside the block reads the primary
    (a page that must see the newest commit, a script's consistency check)."""
    before = getattr(_LOCAL, "primary", 0)
    _LOCAL.primary = before + 1
    try:
        yield
    finally:
        _LOCAL.primary = before


def status():
    """This process's view of the replica, for a health page or a log line."""
    now = time.time()
    return {"enabled": active(),
            "replica_down_for_s": max(0.0, round(_STATE["down_until"] - now, 1)),
            "primary_down_for_s": max(0.0, round(_STATE["p_down_until"] - now, 1)),
            "lag_s": round(_STATE["lag"], 1),
            "lag_checked_s_ago": round(now - _STATE["checked"], 1) if _STATE["checked"] else None}


# --------------------------------------------------------------------------- #
# The classifier
# --------------------------------------------------------------------------- #
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_LITERALS = re.compile(r"'(?:[^']|'')*'")
_HEAD = re.compile(r"[\s(]*([A-Za-z]+)")
_READ_HEADS = {"SELECT", "WITH", "VALUES", "TABLE", "SHOW", "EXPLAIN", "FETCH", "MOVE", "CLOSE"}
_SET_HEADS = {"SET", "RESET"}
_TXN_HEADS = {"BEGIN", "START", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "ABORT"}
_SET_CONFIG = re.compile(r"\bset_config\s*\(", re.I)
_WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|LOCK|COPY"
    r"|VACUUM|ANALYZE|REFRESH|CLUSTER|REINDEX|NOTIFY|LISTEN|INTO|CALL|DO"
    r"|NEXTVAL|SETVAL|PG_ADVISORY_\w+|PG_TRY_ADVISORY_\w+|PG_NOTIFY"
    r"|FOR\s+(?:NO\s+KEY\s+)?UPDATE|FOR\s+(?:KEY\s+)?SHARE)\b", re.I)


def classify(sql):
    """'read', 'set', 'txn' or 'write'. Anything not plainly a read is a
    write: the cost of a wrong 'write' is one primary round trip."""
    if isinstance(sql, (bytes, bytearray, memoryview)):
        sql = bytes(sql).decode("utf-8", "replace")
    if not isinstance(sql, str):
        return "write"                   # psycopg2.sql.Composed and friends
    text = _LITERALS.sub("''", _COMMENTS.sub(" ", sql))
    m = _HEAD.match(text)
    head = m.group(1).upper() if m else ""
    if head in _SET_HEADS:
        return "set"
    if head == "SELECT" and _SET_CONFIG.search(text) and not _WRITE_WORDS.search(text):
        return "set"                     # SELECT set_config(...): a SET, replayed if the block moves
    if head in _TXN_HEADS:
        return "txn"
    if head in _READ_HEADS and not _WRITE_WORDS.search(text):
        return "read"
    return "write"


def _replicaRefused(exc):
    """The replica turned the statement down having done nothing with it:
    run it on the primary instead."""
    code = getattr(exc, "pgcode", None)
    msg = str(exc).lower()
    if code == "25006":                                   # read-only transaction
        return True
    if code in ("0A000", "55000") and "recovery" in msg:  # unlogged/temp, "during recovery"
        return True
    if code == "40001" and "conflict with recovery" in msg:
        return True                                       # cancelled by a replayed swap
    return False


# --------------------------------------------------------------------------- #
# The read pool
# --------------------------------------------------------------------------- #
def _readConfig(dsn):
    cfg = dict(_db._PG_CONFIG_WITH_TIMEOUT)
    cfg.update(psycopg2.extensions.parse_dsn(dsn))
    cfg["connect_timeout"] = int(_num("XCP_DB_READ_CONNECT_TIMEOUT", 3))
    opts = (cfg.get("options") or "").strip()
    # ! READ-ONLY EVEN IF THE DSN IS WRONG. A standby refuses writes anyway;
    #   this makes a read DSN pointed at a primary by mistake refuse them too.
    cfg["options"] = (opts + " -c default_transaction_read_only=on").strip()
    cfg["application_name"] = (cfg.get("application_name") or "xcp") + "-read"
    return cfg


def _readPool():
    dsn = readDsn()
    with _LOCK:
        if _POOL["pool"] is not None and _POOL["key"] == dsn:
            return _POOL["pool"], _POOL["slots"]
        old = _POOL["pool"]
        n = int(_num("XCP_DB_READ_MAX_CONN", _db.MAX_CONN))
        try:
            pool = psycopg2.pool.ThreadedConnectionPool(min(1, n), n, **_readConfig(dsn))
        except psycopg2.Error as exc:
            raise _db.PoolUnavailable(f"[DB] read replica unavailable: {exc}") from exc
        _POOL.update(pool=pool, slots=threading.BoundedSemaphore(n), key=dsn)
    if old is not None:
        try:
            old.closeall()
        except Exception:                                    # noqa: BLE001
            pass
    return pool, _POOL["slots"]


_LAG_SQL = """SELECT CASE WHEN NOT pg_is_in_recovery() THEN 0
                          WHEN pg_last_wal_receive_lsn() IS NOT DISTINCT FROM pg_last_wal_replay_lsn() THEN 0
                          ELSE COALESCE(EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp()), 0)
                     END"""


def _readCheckout():
    """(pool, slots, conn): a live replica connection, probed; the lag is
    re-read on it when the last reading is older than XCP_DB_READ_CHECK_S."""
    pool, slots = _readPool()
    if not slots.acquire(timeout=_db.POOL_WAIT):
        raise psycopg2.pool.PoolError(
            f"read pool exhausted (all in use for {_db.POOL_WAIT:g} s)")
    try:
        for _attempt in range(3):
            cand = pool.getconn()
            if cand.closed:
                pool.putconn(cand, close=True)
                continue
            try:
                with cand.cursor() as probe:
                    if time.time() - _STATE["checked"] >= _num("XCP_DB_READ_CHECK_S", 5):
                        probe.execute(_LAG_SQL)
                        _STATE["lag"] = float(probe.fetchone()[0] or 0)
                        _STATE["checked"] = time.time()
                    else:
                        probe.execute("SELECT 1")
                cand.rollback()
                return pool, slots, cand
            except (psycopg2.InterfaceError, psycopg2.OperationalError):
                pool.putconn(cand, close=True)
        raise _db.PoolUnavailable("[DB] no live replica connection after three tries")
    except BaseException:
        slots.release()
        raise


def _readGiveBack(pool, slots, conn, broken):
    try:
        if not broken and not conn.closed:
            try:
                conn.rollback()
            except Exception:                                # noqa: BLE001
                broken = True
        pool.putconn(conn, close=broken or bool(conn.closed))
    finally:
        slots.release()


# --------------------------------------------------------------------------- #
# Where a block starts
# --------------------------------------------------------------------------- #
def _requestWantsPrimary():
    try:
        from flask import has_request_context, request
        if not has_request_context():
            return False
        if request.method not in SAFE_METHODS:
            return True
        names = os.environ.get("XCP_DB_PRIMARY_COOKIES")
        names = "xcp_session" if names is None else names
        return any(request.cookies.get(n.strip()) for n in names.split(",") if n.strip())
    except Exception:                                        # noqa: BLE001 -- no flask
        return False


def _lagTooHigh():
    """Is the replica further behind than XCP_DB_READ_MAX_LAG_S? A high
    reading is re-taken once it is stale -- a block that starts on the
    primary never checks a replica connection out, so without this one
    slow go-live would keep the process on the primary for good."""
    limit = _num("XCP_DB_READ_MAX_LAG_S", 300)
    if _STATE["lag"] <= limit:
        return False
    if time.time() - _STATE["checked"] >= _num("XCP_DB_READ_CHECK_S", 5):
        try:
            pool, slots, conn = _readCheckout()      # re-reads the lag on the way
            _readGiveBack(pool, slots, conn, False)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            _STATE["down_until"] = time.time() + _num("XCP_DB_READ_RETRY_S", 30)
            return True
        except psycopg2.pool.PoolError:
            return True
    return _STATE["lag"] > limit


def _startsOnPrimary():
    if getattr(_LOCAL, "primary", 0):
        return True
    if time.time() < _STATE["down_until"]:
        return True
    if _lagTooHigh():
        return True
    return _requestWantsPrimary()


# --------------------------------------------------------------------------- #
# The routed connection
# --------------------------------------------------------------------------- #
class RoutedConn:
    """Looks like a psycopg2 connection to the code that uses it. Opens the
    replica and the primary lazily, so a read-only block never touches the
    primary and a pinned one never touches the replica."""

    def __init__(self, primary_cm, pinned):
        self._primary_cm = primary_cm        # database._primaryConn
        self.pinned = bool(pinned)           # reads go to the primary too
        self.wrote = False                   # a write ran: the block stays on the primary
        self._p = self._p_cm = None
        self._r = None                       # (pool, slots, conn)
        self._no_primary = False             # this block found the primary down
        self._sets = []                      # SETs run on the replica this transaction
        self._autocommit = False

    # -- the two sides ------------------------------------------------------ #
    def _primary(self, for_write):
        if self._p is not None:
            return self._p
        if self._no_primary or time.time() < _STATE["p_down_until"]:
            self._no_primary = True
            raise _db.PoolUnavailable("[DB] the primary is unreachable (recently failed)")
        cm = self._primary_cm()
        try:
            conn = cm.__enter__()
        except psycopg2.OperationalError:
            _STATE["p_down_until"] = time.time() + _num("XCP_DB_PRIMARY_RETRY_S", 15)
            self._no_primary = True
            raise
        self._p, self._p_cm = conn, cm
        if self._autocommit:
            conn.autocommit = True
        if self._sets:
            with conn.cursor() as cur:
                for sql, params in self._sets:
                    cur.execute(sql, params)
        return conn

    def _replica(self):
        if self._r is None:
            try:
                self._r = _readCheckout()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                _STATE["down_until"] = time.time() + _num("XCP_DB_READ_RETRY_S", 30)
                raise
        return self._r[2]

    def side(self, kind):
        """('p'|'r', connection) for a statement of this kind."""
        if kind == "write" or self.wrote:
            conn = self._primary(True)
            self.wrote = self.pinned = True
            return "p", conn
        if getattr(_LOCAL, "primary", 0):
            self.pinned = True            # primaryOnly() around a parked connection
        if self.pinned and not self._no_primary:
            try:
                return "p", self._primary(False)
            except psycopg2.OperationalError:
                if self._r is None and time.time() < _STATE["down_until"]:
                    raise                 # neither side: the site's 503
        try:
            return "r", self._replica()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            if self._no_primary:
                raise
            self.pinned = True
            return "p", self._primary(False)

    def reroute(self):
        """The replica refused a statement: end its transaction there and pin
        the block to the primary."""
        if self._r is not None:
            pool, slots, conn = self._r
            broken = False
            try:
                conn.rollback()
            except Exception:                                # noqa: BLE001
                broken = True
            if broken or conn.closed:
                self._r = None
                _readGiveBack(pool, slots, conn, True)
        self.pinned = True

    # -- the psycopg2 surface the site uses -------------------------------- #
    def cursor(self, *args, **kwargs):
        return RoutedCursor(self, args, kwargs)

    def commit(self):
        try:
            if self._p is not None:
                self._p.commit()
        finally:
            self._sets = []
            if self._r is not None:
                try:
                    self._r[2].rollback()    # a read-only transaction: nothing to keep
                except Exception:                            # noqa: BLE001
                    self.reroute()           # broken: give it back, the block goes on

    def rollback(self):
        self._sets = []
        err = None
        for conn in (self._p, self._r[2] if self._r else None):
            if conn is None:
                continue
            try:
                conn.rollback()
            except Exception as exc:                         # noqa: BLE001
                err = err or exc
        if err is not None:
            raise err

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._autocommit = bool(value)
        if value:
            self.wrote = self.pinned = True  # autocommit is for VACUUM and friends
            self._primary(True).autocommit = True
        elif self._p is not None:
            self._p.autocommit = False

    @property
    def closed(self):
        return int(any(c is not None and c.closed for c in (self._p, self._r[2] if self._r else None)))

    def __getattr__(self, name):
        # encoding, server_version, get_transaction_status... from the side
        # the block reads now
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.side("read")[1], name)

    # -- the end of the block ---------------------------------------------- #
    def release(self, exc_info=(None, None, None)):
        broken = exc_info[0] is not None and issubclass(
            exc_info[0], (psycopg2.InterfaceError, psycopg2.OperationalError))
        p_cm, self._p_cm, self._p = self._p_cm, None, None
        r, self._r = self._r, None
        try:
            if p_cm is not None:
                p_cm.__exit__(*exc_info)     # database._lend: rollback, or close if broken
        finally:
            if r is not None:
                _readGiveBack(r[0], r[1], r[2], broken)


class RoutedCursor:
    """A cursor that lives on whichever side its last statement ran."""

    def __init__(self, rconn, args, kwargs):
        self._rc = rconn
        self._args, self._kwargs = args, kwargs
        self._cur = None
        self._on = None

    @property
    def connection(self):
        return self._rc                       # cur.connection.rollback() ends BOTH sides

    def _real(self, where, conn):
        if self._cur is not None and self._on == where and not self._cur.closed:
            return self._cur
        if self._cur is not None:
            try:
                self._cur.close()
            except Exception:                                # noqa: BLE001
                pass
        self._cur, self._on = conn.cursor(*self._args, **self._kwargs), where
        return self._cur

    def _run(self, method, sql, params):
        kind = classify(sql)
        where, conn = self._rc.side(kind)
        cur = self._real(where, conn)
        if where == "p":
            return getattr(cur, method)(sql, params)
        if method == "executemany" and not isinstance(params, (list, tuple)):
            params = list(params)            # it may have to run twice
        try:
            out = getattr(cur, method)(sql, params)
        except psycopg2.Error as exc:
            if not _replicaRefused(exc):
                raise
            self._rc.reroute()
            try:
                where, conn = self._rc.side("write" if kind == "write" else "read")
            except psycopg2.OperationalError:
                raise exc from None          # the primary is gone too: the original error
            if where != "p":
                raise
            return getattr(self._real("p", conn), method)(sql, params)
        if kind == "set":
            self._rc._sets.append((sql, params))
        return out

    def execute(self, sql, params=None):
        return self._run("execute", sql, params)

    def executemany(self, sql, seq):
        return self._run("executemany", sql, seq)

    def _cursor(self):
        if self._cur is None:
            where, conn = self._rc.side("read")
            self._real(where, conn)
        return self._cur

    def mogrify(self, *a, **k):
        return self._cursor().mogrify(*a, **k)

    def close(self):
        if self._cur is not None:
            self._cur.close()

    @property
    def closed(self):
        return self._cur.closed if self._cur is not None else False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._cursor(), name)

    def __iter__(self):
        return iter(self._cursor())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@contextmanager
def routedConn(primary_cm):
    """database.getConn's body when routing is on."""
    rc = RoutedConn(primary_cm, _startsOnPrimary())
    try:
        yield rc
    except BaseException:
        rc.release(sys.exc_info())
        raise
    else:
        rc.release()
