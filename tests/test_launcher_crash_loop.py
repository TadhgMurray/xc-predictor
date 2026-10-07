# Project: xc-predictor / tests
# File:    test_launcher_crash_loop.py
# Purpose: a crashed anet session hands its batch back, drops its browser,
#          backs off, and stops after SESSION_MAX_CRASHES in a row.
#
# ⚠ OWNER, 2026-10-07: every session failed "BrowserType.launch: Target page,
#   context or browser has been closed", and each crash went straight back for
#   another 50 meets -- 104,680 rows claimed, none scraped, 0 due.
#
# The release runs on a scratch Postgres, like test_newest_first_scrape.py:
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import asyncio
import contextlib
import os
import sys
import types

import pytest

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import database                                                  # noqa: E402


def _launcher(monkeypatch):
    # launcher imports playwright and the scrapers at module level; none of
    # them run here
    for name, attrs in {
        "playwright": {}, "playwright.async_api": {"async_playwright": None},
        "playwright_stealth": {"Stealth": None},
        "scrape_results": {"scrapeMeetBySport": None, "scrapeMeetTFMetaOnly": None},
        "scraper": {"CloudflareException": type("CF", (Exception,), {})},
        "vpn_rotation": {"VPNRotator": object},
    }.items():
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attrs))
    import importlib
    return importlib.import_module("launcher")


def test_after_a_crash_the_batch_goes_back_and_the_browser_is_dropped(monkeypatch):
    L = _launcher(monkeypatch)
    released, killed, slept = [], [], []

    async def fake_db(fn, *a):
        released.append((fn.__name__, a))
        return len(a[0])

    async def fake_kill(b, label):
        killed.append(b)

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(L, "runDbCall", fake_db)
    monkeypatch.setattr(L, "_killOldBrowser", fake_kill)
    monkeypatch.setattr(L.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(L, "CLAIM_STATES", (0,))
    monkeypatch.setattr(L, "CRASH_BACKOFF_S", 30.0)
    monkeypatch.setattr(L, "SESSION_MAX_CRASHES", 5)

    crashes, stops = 0, []
    for _ in range(5):
        browser, crashes, stop = asyncio.run(
            L._afterCrash("dead-browser", {(7, "XC"), (8, "TF")}, crashes, "[S1]"))
        assert browser is None
        stops.append(stop)
    assert released[0][0] == "releaseClaims"
    assert sorted(released[0][1][0]) == [(7, "XC"), (8, "TF")] and released[0][1][1] == 0
    assert killed == ["dead-browser"] * 5
    assert slept == [30, 60, 120, 240]                          # doubling, then stop
    assert stops == [False] * 4 + [True]


def test_a_retry_run_releases_to_failed_not_due(monkeypatch):
    L = _launcher(monkeypatch)
    seen = []

    async def fake_db(fn, pairs, to_state):
        seen.append(to_state)
        return 0

    async def nothing(*a):
        return None

    monkeypatch.setattr(L, "runDbCall", fake_db)
    monkeypatch.setattr(L, "_killOldBrowser", nothing)
    monkeypatch.setattr(L.asyncio, "sleep", nothing)
    monkeypatch.setattr(L, "CLAIM_STATES", (2, 3))
    asyncio.run(L._afterCrash(None, {(1, "XC")}, 0, "[S1]"))
    assert seen == [2]


def test_the_session_loop_wires_it():
    src = open(os.path.join(ROOT, "scripts", "launcher.py"), encoding="utf-8").read()
    body = src[src.index("async def runSession("):src.index("# Entry point")]
    assert "pending = {(m, sp) for m, sports in batch.items() for sp in sports}" in body
    assert "pending.discard((meet_id, sport))" in body
    assert "browser, crashes, stop = await _afterCrash(browser, pending, crashes, label)" in body


DB = "xcp_crash_loop_test"


@pytest.fixture
def conn(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the release")
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
            INSERT INTO meet_queue VALUES
              (1,'XC','anet',3), (1,'TF','anet',3),  -- claimed, never reached
              (2,'XC','anet',1),                     -- finished before the crash
              (3,'XC','anet',3),                     -- another session's claim
              (1,'XC','tfrrs',3);                    -- another source
        """)
    cx.commit()

    @contextlib.contextmanager
    def _get():
        yield cx
    monkeypatch.setattr(database, "getConn", _get)
    yield cx
    cx.close()
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
    admin.close()


def test_release_touches_only_this_batchs_unreached_rows(conn):
    n = database.releaseClaims([(1, "XC"), (1, "TF"), (2, "XC")], 0)
    assert n == 2
    with conn.cursor() as c:
        c.execute("SELECT meet_id, sport, source, scraped FROM meet_queue ORDER BY 1, 2, 3")
        got = c.fetchall()
    assert got == [(1, "TF", "anet", 0), (1, "XC", "anet", 0), (1, "XC", "tfrrs", 3),
                   (2, "XC", "anet", 1), (3, "XC", "anet", 3)]
    assert database.releaseClaims([], 0) == 0
