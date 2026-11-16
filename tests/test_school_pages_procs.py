"""racecast/build_school_pages.computeAllProcs (2026-11-16): the schools
built in forked processes give the rows the threaded computeAll gives, a
failing school is reported the same way, and the parent's own connection
still works afterwards (the inherited pool is parked, not closed). buildOne
is stood in for by a query per school. Needs XCP_TEST_PG_DSN."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, d) for d in ("racecast", "scripts", "engine")]
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def test_processes_build_what_threads_build(monkeypatch):
    import database
    cfg = dict(database._PG_CONFIG_WITH_TIMEOUT)
    cfg.update(dict(kv.split("=", 1) for kv in DSN.split()))
    monkeypatch.setattr(database, "_PG_CONFIG_WITH_TIMEOUT", cfg)
    database._pool = None
    database._slots = None
    import build_school_pages as B

    def fake(conn, name, oids):
        if name == "Bad":
            raise ValueError("no such school")
        with conn.cursor() as cur:
            cur.execute("SELECT md5(%s), length(%s)", (name, name))
            h, n = cur.fetchone()
        conn.rollback()
        return [(f"k:{name}", f"call({name})", f"{h}:{n}:{oids}", 0.0)]
    monkeypatch.setattr(B, "buildOne", fake)
    schools = [f"School {i}" for i in range(37)] + ["Bad"]
    with database.getConn() as parent:                 # a live parent connection
        threads = B.computeAll(schools, 4, database.getConn, (1, 2), log=lambda *_: None)
        procs = B.computeAllProcs(schools, 4, (1, 2), log=lambda *_: None)
        with parent.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
    assert threads[0] == procs[0] and len(procs[0]) == 37
    assert sorted(threads[1]) == sorted(procs[1]) == ["Bad"]
    with database.getConn() as again:                  # the pool still serves
        with again.cursor() as cur:
            cur.execute("SELECT 2")
            assert cur.fetchone()[0] == 2
    database._pool = None
