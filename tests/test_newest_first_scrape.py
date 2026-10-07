# Project: xc-predictor / tests
# File:    test_newest_first_scrape.py
# Purpose: the anet scrape takes the newest meets first, and the forward walk
#          re-seeds its block as soon as that block is scraped -- not after the
#          whole queue (93k old name repairs) drains.
#
# ★ OWNER, 2026-10-07: "is this the scrape that will add new meet ids?" It
#   was, last: the claim was ORDER BY meet_id, the walk seeded only on an
#   empty queue, and the queue held 98,780 due ids from 3 up.
#
# Needs a scratch Postgres, like test_twin_flag.py:
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import contextlib
import os
import sys

import pytest

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import database                                                  # noqa: E402
import queue_meets as Q                                          # noqa: E402

DB = "xcp_newest_first_test"


@pytest.fixture
def conn(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the claim")
    import psycopg2
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
        c.execute(f"CREATE DATABASE {DB}")
    test_dsn = " ".join(p for p in dsn.split() if not p.startswith("dbname=")) + f" dbname={DB}"
    cx = psycopg2.connect(test_dsn)
    with cx.cursor() as c:
        c.execute("""
            CREATE TABLE meet_queue (meet_id bigint, sport text, source text,
                                     scraped int, PRIMARY KEY (meet_id, sport, source));
            CREATE TABLE results (meet_id bigint, source text);
            CREATE TABLE meets (meet_id bigint, source text, meet_date text);
        """)
        # the corpus: real XC meets 1..1000 (the dense block guard needs them)
        c.execute("INSERT INTO meets SELECT g, 'anet', NULL FROM generate_series(1, 1000) g")
        c.execute("INSERT INTO results SELECT g, 'anet' FROM generate_series(1, 1000) g")
        # old meets requeued for their names, and the walk's first block
        c.execute("INSERT INTO meet_queue SELECT g, 'XC', 'anet', 0 FROM generate_series(10, 20) g")
        c.execute("INSERT INTO meet_queue SELECT g, 'XC', 'anet', 0 FROM generate_series(1001, 1005) g")
    cx.commit()

    @contextlib.contextmanager
    def _get():
        yield cx
    monkeypatch.setattr(Q, "getConn", _get)
    yield cx
    cx.close()
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
    admin.close()


def test_the_claim_is_newest_first(conn):
    assert database._CLAIM_ORDER == "DESC"
    with conn.cursor() as c:
        got = {m for m, _s in database._claimOneSport(c, 3, "XC")}
    assert got == {1005, 1004, 1003}


def test_the_walk_reseeds_when_its_block_is_scraped_not_when_the_queue_drains(conn):
    walk = Q.ForwardWalk(source="anet", sports=["XC"], ahead=5, dry_blocks=2)
    with conn.cursor() as c:
        database._claimOneSport(c, 5, "XC")                 # the block, in flight
    conn.commit()
    assert walk.frontierDrained() == []                     # claimed, not done

    with conn.cursor() as c:                                # scraped: all five were meets
        c.execute("UPDATE meet_queue SET scraped = 1 WHERE meet_id > 1000")
        c.execute("INSERT INTO meets SELECT g, 'anet', NULL FROM generate_series(1001, 1005) g")
        c.execute("INSERT INTO results SELECT g, 'anet' FROM generate_series(1001, 1005) g")
    conn.commit()
    assert walk.frontierDrained() == ["XC"]                 # old meets still due

    more, _lines = walk.extend(["XC"])
    assert more
    with conn.cursor() as c:
        c.execute("SELECT meet_id FROM meet_queue WHERE scraped = 0 AND meet_id > 1000 ORDER BY 1")
        seeded = [r[0] for r in c.fetchall()]
        c.execute("SELECT count(*) FROM meet_queue WHERE scraped = 0 AND meet_id <= 20")
        old = c.fetchone()[0]
    assert seeded == [1006, 1007, 1008, 1009, 1010]        # from the new watermark
    assert old == 11                                        # the repairs wait their turn
    with conn.cursor() as c:                                # and come after the new block
        got = {m for m, _s in database._claimOneSport(c, 6, "XC")}
    assert got == {1006, 1007, 1008, 1009, 1010, 20}


def test_a_block_with_no_new_meet_counts_toward_the_stop(conn):
    walk = Q.ForwardWalk(source="anet", sports=["XC"], ahead=5, dry_blocks=2)
    with conn.cursor() as c:
        c.execute("UPDATE meet_queue SET scraped = 4 WHERE meet_id > 1000")
    conn.commit()
    walk.extend(["XC"])                                     # first look: sets the mark
    with conn.cursor() as c:
        c.execute("UPDATE meet_queue SET scraped = 4 WHERE meet_id > 1000")
    conn.commit()
    walk.extend(["XC"])
    assert walk.dry["XC"] == 1
    with conn.cursor() as c:
        c.execute("UPDATE meet_queue SET scraped = 4 WHERE meet_id > 1000")
    conn.commit()
    walk.extend(["XC"])
    assert "XC" in walk.done                                # two dry blocks: finished
    assert walk.frontierDrained() == []                     # and no longer polled


def test_the_launcher_checks_the_frontier_before_each_claim():
    src = open(os.path.join(ROOT, "scripts", "launcher.py"), encoding="utf-8").read()
    i = src.index("await _maybeExtendFrontier(config[\"label\"])")
    j = src.index("batch = await runDbCall(getBatchUnscrapedMeets", i)
    assert j - i < 200
