# Project: xc-predictor / tests
# File:    test_read_routing.py
# Purpose: the site's reads go to a hot standby and its writes to the primary
#          (scripts/read_routing.py, docs/REPLICA-PLAN.md). No database: both
#          pools are fakes that log which side ran what.
#
#   python -m pytest -q tests/test_read_routing.py
import os
import sys
import threading

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

import psycopg2
import psycopg2.errors
import psycopg2.pool
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database as D                                              # noqa: E402
import read_routing as RR                                         # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _ReadOnly(psycopg2.errors.ReadOnlySqlTransaction):
    pgcode = "25006"


class _Unlogged(psycopg2.errors.FeatureNotSupported):
    pgcode = "0A000"


class _Conflict(psycopg2.errors.SerializationFailure):
    pgcode = "40001"


class Cur:
    def __init__(self, conn):
        self.conn, self.closed, self._row = conn, False, None

    def execute(self, sql, params=None):
        if sql is RR._LAG_SQL:
            self._row = (self.conn.lag,)
            return
        if sql == "SELECT 1":
            if self.conn.dead:
                raise psycopg2.OperationalError("server closed the connection")
            return
        self.conn.log.append((self.conn.side, sql))
        self.conn.pool.log.append((self.conn.side, sql))
        for needle, exc in self.conn.raises.items():
            if needle in sql:
                raise exc

    def executemany(self, sql, seq):
        self.execute(sql, list(seq))

    def fetchone(self):
        return self._row or ("row",)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Conn:
    def __init__(self, pool):
        self.pool, self.side = pool, pool.side
        self.log, self.closed, self.dead = [], 0, False
        self.commits = self.rollbacks = 0
        self.lag = pool.lag
        self.raises = dict(pool.raises)
        self.autocommit = False
        self.encoding = "UTF8"

    def cursor(self, *a, **k):
        return Cur(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Pool:
    def __init__(self, side, lag=0.0, raises=None, refuse=False):
        self.side, self.lag, self.raises, self.refuse = side, lag, raises or {}, refuse
        self.out = self.made = 0
        self.log, self.cfg = [], None

    def getconn(self):
        if self.refuse:
            raise psycopg2.OperationalError(f"could not connect to the {self.side}")
        self.out += 1
        self.made += 1
        return Conn(self)

    def putconn(self, conn, close=False):
        self.out -= 1

    def closeall(self):
        pass


@pytest.fixture
def env(monkeypatch):
    """Routing on, as the site's service unit would have it, with a fake
    primary pool and a fake read pool."""
    monkeypatch.delenv("XCP_DB_QUIET", raising=False)
    monkeypatch.setenv("XCP_DB_APP", "site")
    monkeypatch.setenv("XCP_DB_READ_DSN", "host=10.77.0.2 port=5432")
    for k in ("XCP_DB_READ_APPS", "XCP_DB_READ_MAX_LAG_S", "XCP_DB_PRIMARY_COOKIES"):
        monkeypatch.delenv(k, raising=False)
    primary, replica = Pool("p"), Pool("r")
    monkeypatch.setattr(D, "_pool", primary)
    monkeypatch.setattr(D, "_slots", threading.BoundedSemaphore(3))

    def makeReadPool(minc, maxc, **cfg):
        if replica.refuse:
            raise psycopg2.OperationalError("could not connect to the replica")
        replica.cfg = cfg
        return replica
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", makeReadPool)
    monkeypatch.setattr(RR, "_POOL", {"pool": None, "slots": None, "key": None})
    monkeypatch.setattr(RR, "_STATE", {"down_until": 0.0, "lag": 0.0, "checked": 0.0,
                                       "p_down_until": 0.0})
    return primary, replica


# --------------------------------------------------------------------------- #
# Off unless asked for
# --------------------------------------------------------------------------- #
def test_unset_is_todays_single_dsn_behaviour(env, monkeypatch):
    primary, replica = env
    monkeypatch.delenv("XCP_DB_READ_DSN")
    with D.getConn() as conn:
        assert isinstance(conn, Conn) and conn.side == "p"      # the raw pooled connection
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM athletes")
    assert primary.log == [("p", "SELECT name FROM athletes")]
    assert replica.cfg is None and replica.made == 0             # no read pool was made
    assert primary.out == 0 and D._slots._value == 3


@pytest.mark.parametrize("setting", [
    {"XCP_DB_QUIET": "1"},            # the pipeline: it reads what it just wrote
    {"XCP_DB_APP": "xcp"},            # a script that sourced the env file
    {"XCP_DB_READ_APPS": "other"},
])
def test_only_the_site_routes(env, monkeypatch, setting):
    primary, replica = env
    for k, v in setting.items():
        monkeypatch.setenv(k, v)
    assert not RR.active()
    with D.getConn() as conn:
        assert isinstance(conn, Conn)
        conn.cursor().execute("SELECT 1 FROM meets")
    assert replica.made == 0 and primary.log[-1] == ("p", "SELECT 1 FROM meets")
    assert all(side == "p" for side, _ in primary.log)


# --------------------------------------------------------------------------- #
# Reads to the replica, writes to the primary
# --------------------------------------------------------------------------- #
def test_reads_go_to_the_read_pool_and_never_open_the_primary(env):
    primary, replica = env
    with D.getConn() as conn:
        assert isinstance(conn, RR.RoutedConn)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ranking_results WHERE person_id = %s", (1,))
            assert cur.fetchone() == ("row",)
            cur.execute("WITH t AS (SELECT 1) SELECT * FROM t")
        conn.commit()
    assert [s for s, _ in replica.log] == ["r", "r"]
    assert primary.made == 0 and primary.log == []
    assert replica.out == 0 and primary.out == 0 and D._slots._value == 3
    # the read pool connects to the DSN's host, read-only even if that is wrong
    assert replica.cfg["host"] == "10.77.0.2"
    assert "default_transaction_read_only=on" in replica.cfg["options"]
    assert replica.cfg["dbname"] == D._PG_CONFIG_WITH_TIMEOUT["dbname"]   # inherited


def test_a_write_goes_to_the_primary_and_takes_the_rest_of_the_block(env):
    primary, replica = env
    with D.getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM account WHERE email = %s", ("a@b.c",))
            cur.execute("SET LOCAL statement_timeout = 10000")
            cur.execute("INSERT INTO usage_daily (day, n) VALUES (%s, %s)", (1, 2))
            cur.execute("SELECT n FROM usage_daily")             # reads its own write
        conn.commit()
        conn.cursor().execute("SELECT 2 FROM account")           # still the primary
    assert replica.log == [("r", "SELECT id FROM account WHERE email = %s"),
                           ("r", "SET LOCAL statement_timeout = 10000")]
    # the SET LOCAL the replica ran is replayed on the primary before the write
    assert primary.log == [("p", "SET LOCAL statement_timeout = 10000"),
                           ("p", "INSERT INTO usage_daily (day, n) VALUES (%s, %s)"),
                           ("p", "SELECT n FROM usage_daily"),
                           ("p", "SELECT 2 FROM account")]
    assert primary.out == 0 and replica.out == 0 and D._slots._value == 3


def test_executemany_and_cursor_connection(env):
    primary, replica = env
    with D.getConn() as conn:
        cur = conn.cursor()
        assert cur.connection is conn                            # rollback ends both sides
        cur.executemany("INSERT INTO t VALUES (%s)", iter([(1,), (2,)]))
        assert conn.encoding == "UTF8"
    assert primary.log and replica.log == []


def test_a_write_the_classifier_missed_is_refused_by_the_replica_and_rerun(env):
    primary, replica = env
    replica.raises = {"bump_counter": _ReadOnly("cannot execute UPDATE in a read-only transaction")}
    with D.getConn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT bump_counter(%s)", (7,))             # a function that writes
        cur.execute("SELECT 1 FROM meets")
    assert replica.log == [("r", "SELECT bump_counter(%s)")]
    assert primary.log == [("p", "SELECT bump_counter(%s)"), ("p", "SELECT 1 FROM meets")]
    assert replica.out == 0 and primary.out == 0


@pytest.mark.parametrize("exc", [
    _Unlogged("cannot access temporary or unlogged relations during recovery"),
    _Conflict("canceling statement due to conflict with recovery"),
])
def test_unlogged_tables_and_recovery_conflicts_read_the_primary(env, exc):
    primary, replica = env
    replica.raises = {"scratch": exc}
    with D.getConn() as conn:
        conn.cursor().execute("SELECT * FROM scratch")
    assert primary.log == [("p", "SELECT * FROM scratch")]


def test_a_real_error_on_the_replica_is_not_retried(env):
    primary, replica = env
    replica.raises = {"nope": psycopg2.errors.UndefinedTable("relation nope does not exist")}
    with pytest.raises(psycopg2.errors.UndefinedTable):
        with D.getConn() as conn:
            conn.cursor().execute("SELECT * FROM nope")
    assert primary.made == 0 and replica.out == 0


# --------------------------------------------------------------------------- #
# Whole requests on the primary
# --------------------------------------------------------------------------- #
def _app():
    import flask
    return flask.Flask("t")


def test_a_post_reads_the_primary(env):
    primary, replica = env
    with _app().test_request_context("/account/follow", method="POST"):
        with D.getConn() as conn:
            conn.cursor().execute("SELECT 1 FROM account_follow")
    assert primary.log == [("p", "SELECT 1 FROM account_follow")] and replica.made == 0


def test_a_signed_in_get_reads_the_primary_and_an_anonymous_one_the_replica(env):
    primary, replica = env
    app = _app()
    with app.test_request_context("/athlete/1", headers={"Cookie": "xcp_session=abc"}):
        with D.getConn() as conn:
            conn.cursor().execute("SELECT 1 FROM account_session")
    with app.test_request_context("/athlete/1"):
        with D.getConn() as conn:
            conn.cursor().execute("SELECT 2 FROM athletes")
    assert primary.log == [("p", "SELECT 1 FROM account_session")]
    assert replica.log == [("r", "SELECT 2 FROM athletes")]


def test_primary_only_pins_a_block(env):
    primary, replica = env
    with RR.primaryOnly():
        with D.getConn() as conn:
            conn.cursor().execute("SELECT 1 FROM meets")
    with D.getConn() as conn:
        conn.cursor().execute("SELECT 2 FROM meets")
        with RR.primaryOnly():                                   # a parked connection too
            conn.cursor().execute("SELECT 3 FROM meets")
    assert primary.log == [("p", "SELECT 1 FROM meets"), ("p", "SELECT 3 FROM meets")]
    assert replica.log == [("r", "SELECT 2 FROM meets")]


# --------------------------------------------------------------------------- #
# Either side down, or the replica far behind
# --------------------------------------------------------------------------- #
def test_a_replica_that_is_down_is_skipped_for_a_while(env):
    primary, replica = env
    replica.refuse = True
    with D.getConn() as conn:
        conn.cursor().execute("SELECT 1 FROM meets")
    assert primary.log == [("p", "SELECT 1 FROM meets")]
    assert RR._STATE["down_until"] > 0
    replica.refuse = False
    with D.getConn() as conn:                                    # inside the retry window
        conn.cursor().execute("SELECT 2 FROM meets")
    assert replica.log == [] and primary.log[-1] == ("p", "SELECT 2 FROM meets")
    RR._STATE["down_until"] = 0.0                                # the window passes
    with D.getConn() as conn:
        conn.cursor().execute("SELECT 3 FROM meets")
    assert replica.log == [("r", "SELECT 3 FROM meets")]


def test_a_primary_that_is_down_still_serves_reads_and_a_write_is_a_503(env):
    primary, replica = env
    primary.refuse = True
    D._pool = None                                               # its pool cannot open either

    def refuse(*a, **k):
        raise psycopg2.OperationalError("could not connect to the primary")
    app = _app()
    import psycopg2.pool as pp
    real = pp.ThreadedConnectionPool

    def pools(minc, maxc, **cfg):
        if cfg.get("host") == "10.77.0.2":
            return real(minc, maxc, **cfg)                       # the read pool fake
        return refuse()
    pp.ThreadedConnectionPool = pools
    try:
        # signed in, so it starts on the primary -- and reads the replica instead
        with app.test_request_context("/me", headers={"Cookie": "xcp_session=abc"}):
            with D.getConn() as conn:
                conn.cursor().execute("SELECT 1 FROM account_session")
                with pytest.raises(psycopg2.OperationalError):   # the site's 503 handler
                    conn.cursor().execute("UPDATE account_session SET last_seen_at = now()")
        assert replica.log == [("r", "SELECT 1 FROM account_session")]
        assert RR._STATE["p_down_until"] > 0
        # the next block does not wait on the dead primary again
        with D.getConn() as conn:
            with pytest.raises(D.PoolUnavailable):
                conn.cursor().execute("INSERT INTO issue_reports VALUES (1)")
    finally:
        pp.ThreadedConnectionPool = real
    assert replica.out == 0


def test_a_replica_too_far_behind_gives_way_to_the_primary_until_it_catches_up(env, monkeypatch):
    primary, replica = env
    monkeypatch.setenv("XCP_DB_READ_MAX_LAG_S", "60")
    replica.lag = 900.0                                          # a go-live's WAL still replaying
    with D.getConn() as conn:
        conn.cursor().execute("SELECT 1 FROM ranking_results")   # first checkout reads the lag
    assert replica.log == [("r", "SELECT 1 FROM ranking_results")]
    assert RR._STATE["lag"] == 900.0
    with D.getConn() as conn:
        conn.cursor().execute("SELECT 2 FROM ranking_results")
    assert primary.log == [("p", "SELECT 2 FROM ranking_results")]
    replica.lag = 0.0
    RR._STATE["checked"] = 0.0                                   # the reading goes stale
    with D.getConn() as conn:
        conn.cursor().execute("SELECT 3 FROM ranking_results")
    assert replica.log[-1] == ("r", "SELECT 3 FROM ranking_results")


def test_db_timing_parks_one_routed_connection_per_request(env):
    """The site's per-request connection (racecast/db_timing.py) wraps the
    routed one: a write in one block keeps the request's later blocks on the
    primary; a read-only request never opens the primary."""
    primary, replica = env
    import importlib
    real = D.getConn
    dt = importlib.import_module("db_timing")
    saved = dt._REAL_GETCONN
    dt._REAL_GETCONN = real
    try:
        app = _app()
        with app.test_request_context("/athlete/1"):
            with dt.getConn() as c:
                c.cursor().execute("SELECT 1 FROM athletes")
            with dt.getConn() as c:
                c.cursor().execute("SELECT 2 FROM athletes")
            dt.releaseRequestConn()
        assert primary.made == 0 and [s for s, _ in replica.log] == ["r", "r"]
        with app.test_request_context("/athlete/1"):
            with dt.getConn() as c:
                c.cursor().execute("INSERT INTO prediction_snapshot VALUES (1)")
                c.commit()
            with dt.getConn() as c:
                c.cursor().execute("SELECT 3 FROM prediction_snapshot")
            dt.releaseRequestConn()
        assert primary.log[-1] == ("p", "SELECT 3 FROM prediction_snapshot")
    finally:
        dt._REAL_GETCONN = saved
    assert primary.out == 0 and replica.out == 0 and D._slots._value == 3


# --------------------------------------------------------------------------- #
# The classifier
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sql,kind", [
    ("SELECT * FROM meets", "read"),
    ("  (SELECT 1) UNION (SELECT 2)", "read"),
    ("-- INSERT INTO nothing\nSELECT 1", "read"),
    ("/* UPDATE */ SELECT updated_at, created_at FROM account", "read"),
    ("SELECT 'please update me' AS note", "read"),
    ("WITH x AS (SELECT 1) SELECT * FROM x", "read"),
    ("EXPLAIN SELECT 1", "read"),
    ("SHOW server_version", "read"),
    (b"SELECT 1", "read"),
    ("SET LOCAL statement_timeout = 10000", "set"),
    ("RESET work_mem", "set"),
    ("SELECT set_config('rc.venue_rows', %s, true)", "set"),
    ("SELECT current_setting('rc.venue_rows', true) AS t", "read"),
    ("COMMIT", "txn"),
    ("INSERT INTO t VALUES (1)", "write"),
    ("update t set a = 1", "write"),
    ("DELETE FROM t", "write"),
    ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "write"),
    ("SELECT * FROM fix_request WHERE id = 1 FOR UPDATE", "write"),
    ("SELECT 1 FROM app_error_notify FOR UPDATE SKIP LOCKED", "write"),
    ("SELECT nextval('s')", "write"),
    ("SELECT pg_advisory_unlock(42)", "write"),
    ("CREATE TEMP TABLE venue_rows ON COMMIT DROP AS SELECT 1", "write"),
    ("CREATE TABLE IF NOT EXISTS shared_prediction (id int)", "write"),
    ("SELECT * INTO new_table FROM t", "write"),
    ("COPY t FROM STDIN", "write"),
    ("TRUNCATE t", "write"),
    ("DROP TABLE x", "write"),
    ("", "write"),
    (object(), "write"),
])
def test_classify(sql, kind):
    assert RR.classify(sql) == kind
