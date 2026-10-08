# Project: xc-predictor / tests
# File:    test_empty_meet_requeue.py
# Purpose: a meet asked before it had divisions is asked again; a tfrrs id
#          deleted as "not a meet" can be put back; tfrrs marks only its own
#          queue rows done; and the missing-meet diagnostic writes nothing.
#
# ★ OWNER, 2026-10-08: "I see no Purple Valley classic, or woodbridge", after
#   a full scrape. An anet XC meet with no divisions yet comes back (0, True)
#   from scrapeMeetBySport and is written state 1 with NOTHING saved -- the
#   meets row is per division -- and every re-ask pass in queue_meets started
#   from that row. Once the watermark passed the id, the meet was done for
#   ever.
#
# Needs a scratch Postgres, like test_newest_first_scrape.py:
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import ast
import contextlib
import datetime
import io
import os
import sys

import pytest

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import queue_meets as Q                                          # noqa: E402

DB = "xcp_empty_meet_requeue_test"
TODAY = datetime.date.today()
RECENT = (TODAY - datetime.timedelta(days=Q.RECENT_DAYS // 2)).isoformat()

# anet XC: real meets with results up to the watermark W
W = Q.UNDATED_SPAN + 1000
STRANDED = W - 10          # done, no meet row, no results: THE BUG
SCHEDULED = W - 20         # done, meet row, no results: emptyRecent's job
HAS_RESULTS = W - 30       # done with results: leave alone
NOT_A_MEET = W - 40        # state 4: leave alone
TOO_OLD = W - Q.UNDATED_SPAN - 5   # stranded, but outside the span


@pytest.fixture
def conn(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
        c.execute(f"CREATE DATABASE {DB}")
    test_dsn = " ".join(p for p in dsn.split()
                        if not p.startswith("dbname=")) + f" dbname={DB}"
    cx = psycopg2.connect(test_dsn)
    with cx.cursor() as c:
        c.execute("""
            CREATE TABLE meet_queue (meet_id bigint, sport text, source text,
                                     scraped int,
                                     PRIMARY KEY (meet_id, sport, source));
            CREATE TABLE results (meet_id bigint, source text, date text);
            CREATE TABLE results_tf (meet_id bigint, source text, date text);
            CREATE TABLE meets (div_id bigint, meet_id bigint, source text,
                                meet_name text, meet_date text, state text);
            CREATE TABLE meets_tf (meet_id bigint, source text, meet_name text);
            CREATE TABLE meets_tf_meta (meet_id bigint, meet_name text,
                                        meet_date text, state text,
                                        has_results int, finalized int);
            CREATE TABLE meets_tfrrs (meet_id bigint, sport text,
                                      meet_name text, date text, state text,
                                      venue_name text);
        """)
        # anet XC corpus: the dense block below the watermark, dated recently
        c.execute("""INSERT INTO meets SELECT g, g, 'anet', 'Dual ' || g, %s, 'CA'
                     FROM generate_series(%s, %s) g""", (RECENT, W - 999, W))
        c.execute("""INSERT INTO results SELECT g, 'anet', %s
                     FROM generate_series(%s, %s) g""", (RECENT, W - 999, W))
        c.execute("""INSERT INTO meet_queue SELECT g, 'XC', 'anet', 1
                     FROM generate_series(%s, %s) g""", (W - 999, W))
        for mid in (STRANDED, SCHEDULED, NOT_A_MEET):
            c.execute("DELETE FROM results WHERE meet_id = %s", (mid,))
        for mid in (STRANDED, NOT_A_MEET):
            c.execute("DELETE FROM meets WHERE meet_id = %s", (mid,))
        c.execute("UPDATE meet_queue SET scraped = 4 WHERE meet_id = %s",
                  (NOT_A_MEET,))
        c.execute("INSERT INTO meet_queue VALUES (%s, 'XC', 'anet', 1)",
                  (TOO_OLD,))

        # tfrrs XC: meets 100..140 dated this season; 120 was deleted as
        # "not a meet" (no row at all), 125 failed
        c.execute("""INSERT INTO meets_tfrrs SELECT g, 'XC', 'Meet ' || g, %s,
                     'MA', 'Field' FROM generate_series(100, 140) g
                     WHERE g NOT IN (120, 125)""", (RECENT,))
        c.execute("""INSERT INTO results SELECT g, 'tfrrs', %s
                     FROM generate_series(100, 140) g
                     WHERE g NOT IN (120, 125)""", (RECENT,))
        c.execute("""INSERT INTO meet_queue SELECT g, 'XC', 'tfrrs', 1
                     FROM generate_series(100, 140) g
                     WHERE g NOT IN (120, 125)""")
        c.execute("INSERT INTO meet_queue VALUES (125, 'XC', 'tfrrs', 2)")
    cx.commit()

    @contextlib.contextmanager
    def _get():
        yield cx
    monkeypatch.setattr(Q, "getConn", _get)
    yield cx, _get
    cx.close()
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
    admin.close()


def _state(cx, mid, source="anet"):
    with cx.cursor() as c:
        c.execute("SELECT scraped FROM meet_queue WHERE meet_id = %s "
                  "AND source = %s AND sport = 'XC'", (mid, source))
        r = c.fetchone()
    cx.rollback()
    return r[0] if r else None


def test_the_stranded_meet_is_found_and_nothing_else(conn):
    cx, _ = conn
    with cx.cursor() as c:
        got = Q.emptyUnrecorded(c, "XC", Q.watermark(c, "XC"))
    assert got == [STRANDED]


def test_the_launch_seed_requeues_it(conn):
    """The launcher's own path: seedAll at start-up, recent pass on."""
    cx, _ = conn
    out = Q.seedAll(cx, source="anet", write=True, sports=["XC"],
                    do_new=False, verbose=False)
    assert out[0]["unrecorded"] == 1
    assert _state(cx, STRANDED) == 0
    assert _state(cx, HAS_RESULTS) == 1
    assert _state(cx, NOT_A_MEET) == 4
    assert _state(cx, TOO_OLD) == 1


def test_the_forward_walk_still_does_not_run_the_recent_pass():
    src = io.open(os.path.join(ROOT, "scripts", "queue_meets.py"),
                  encoding="utf-8").read()
    walk = src[src.index("class ForwardWalk"):src.index("\ndef main(")]
    assert "do_recent=False" in walk


def test_the_tfrrs_season_range_and_deleted_ids(conn):
    cx, _ = conn
    with cx.cursor() as c:
        lo, hi, _how = Q.seasonRange(c, "XC", "tfrrs")
        assert (lo, hi) == (100, 140)
        assert Q.deletedIds(c, "XC", lo, hi, "tfrrs") == [120]
    cx.rollback()


def test_requeue_is_a_dry_run_until_apply(conn, monkeypatch, capsys):
    cx, get = conn
    import requeue_empty_meets as R
    monkeypatch.setattr(R, "getConn", get)
    monkeypatch.setattr(sys, "argv", ["requeue_empty_meets.py"])
    R.main()
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert _state(cx, STRANDED) == 1
    assert _state(cx, 120, "tfrrs") is None
    assert _state(cx, 125, "tfrrs") == 2

    monkeypatch.setattr(sys, "argv", ["requeue_empty_meets.py", "--apply"])
    R.main()
    assert _state(cx, STRANDED) == 0
    assert _state(cx, 120, "tfrrs") == 0
    assert _state(cx, 125, "tfrrs") == 0
    assert _state(cx, HAS_RESULTS) == 1


def test_the_diagnostic_finds_names_and_writes_nothing(conn, monkeypatch,
                                                       capsys):
    cx, get = conn
    import diag_missing_meet as D
    monkeypatch.setattr(D, "getConn", get)
    with cx.cursor() as c:
        c.execute("SELECT md5(string_agg(meet_id || sport || source || scraped,"
                  " ',' ORDER BY meet_id, sport, source)) FROM meet_queue")
        before = c.fetchone()[0]
    cx.rollback()

    year = TODAY.isoformat()[:4]
    for argv in (["dual %d" % SCHEDULED, "--year", year],
                 ["--id", str(STRANDED), str(120), "--summary"],
                 ["no such meet anywhere", "--year", year]):
        monkeypatch.setattr(sys, "argv", ["diag_missing_meet.py"] + argv)
        D.main()
    out = capsys.readouterr().out
    assert "DONE WITH 0 RESULTS, meet row present" in out          # SCHEDULED
    assert "DONE WITH 0 RESULTS AND NO MEET ROW" in out            # STRANDED
    assert "NO ROW for any feed or sport" in out                   # 120
    assert "NO MATCH in any meet table" in out
    assert "==== tfrrs/XC" in out and "==== anet/XC" in out

    with cx.cursor() as c:
        c.execute("SELECT md5(string_agg(meet_id || sport || source || scraped,"
                  " ',' ORDER BY meet_id, sport, source)) FROM meet_queue")
        assert c.fetchone()[0] == before
    cx.rollback()


def test_tfrrs_marks_only_its_own_rows_done():
    """One _markMeetDone in run_tfrrs, and it names source = 'tfrrs'. A later
    def of the same name silently replaces the earlier one at import."""
    path = os.path.join(ROOT, "tfrrs", "driver", "run_tfrrs.py")
    src = io.open(path, encoding="utf-8").read()
    defs = [n for n in ast.parse(src).body
            if isinstance(n, ast.FunctionDef)]
    names = [n.name for n in defs]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"redefined at module level: {sorted(dupes)}"
    for n in defs:
        if n.name in ("_markMeetDone", "_markMeetFailed", "_deleteQueueRow"):
            assert "source = 'tfrrs'" in ast.get_source_segment(src, n), n.name
