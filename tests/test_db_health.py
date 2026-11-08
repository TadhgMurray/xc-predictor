# Project: xc-predictor / tests
# File:    test_db_health.py
# Purpose: scripts/db_health.py (the read-only database report) and the
#          site-only connection settings in scripts/database.py (2026-11-08,
#          database tuning track).
#
#   python -m pytest -q tests/test_db_health.py
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres" \
#       python -m pytest -q tests/test_db_health.py      # + the report on Postgres
import io
import os
import re
import shutil
import subprocess
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db_health as H                                          # noqa: E402


# ---------------------------------------------------------------- the site's knobs

def test_site_knobs_are_sent_only_when_set_and_well_formed(capsys):
    import database as db
    assert db._siteKnobs({}) == []
    got = db._siteKnobs({"XCP_DB_WORK_MEM": "64MB", "XCP_DB_HASH_MEM_MULTIPLIER": "1.5",
                         "XCP_DB_PARALLEL_GATHER": "0", "XCP_DB_IDLE_TXN_TIMEOUT_MS": "60000",
                         "XCP_DB_TEMP_FILE_LIMIT": "2GB"})
    assert got == ["-c work_mem=64MB", "-c hash_mem_multiplier=1.5",
                   "-c max_parallel_workers_per_gather=0",
                   "-c idle_in_transaction_session_timeout=60000", "-c temp_file_limit=2GB"]
    # a value that is not the setting's shape never reaches the startup packet
    # (a bad -c is a FATAL at connect: every page would fail)
    bad = db._siteKnobs({"XCP_DB_WORK_MEM": "64MB -c fsync=off", "XCP_DB_PARALLEL_GATHER": "two",
                         "XCP_DB_TEMP_FILE_LIMIT": "-1"})
    assert bad == ["-c temp_file_limit=-1"]
    assert "XCP_DB_WORK_MEM" in capsys.readouterr().err


def test_unset_knobs_leave_the_connection_options_as_they_were():
    """The service unit's timeouts and jit=off come out exactly as before when
    no knob is set, and the knobs follow them when they are."""
    code = ("import sys; sys.path[:0] = ['scripts', 'tests']; import _env; "
            "import database; print(database._PG_CONFIG_WITH_TIMEOUT.get('options'))")

    def opts(**env):
        e = {k: v for k, v in os.environ.items()
             if not k.startswith("XCP_DB_") or k == "XCP_DB_PASSWORD"}
        e.update(env)
        out = subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=_ROOT,
                             env=e, capture_output=True, text=True, timeout=120)
        return out.stdout.strip().splitlines()[-1]
    assert opts(XCP_DB_STATEMENT_TIMEOUT_MS="55000", XCP_DB_JIT="off") == \
        "-c statement_timeout=55000 -c jit=off"
    assert opts() == "None"
    assert opts(XCP_DB_JIT="off", XCP_DB_WORK_MEM="128MB", XCP_DB_PARALLEL_GATHER="0") == \
        "-c jit=off -c work_mem=128MB -c max_parallel_workers_per_gather=0"


# ---------------------------------------------------------------- what the code lists

def test_index_signature_matches_the_builders_rule():
    assert H.indexSig("CREATE INDEX x ON public.t USING btree (pool, speed_rating DESC)") == \
        H.indexSig("(pool, speed_rating DESC)") == "pool,speed_ratingdesc"
    # pg_get_indexdef wraps a predicate in parentheses; the list writes it bare
    assert H.indexSig("CREATE INDEX x ON t USING btree (event_kind, mark DESC) WHERE (event_kind IS NOT NULL)") == \
        H.indexSig("(event_kind, mark DESC) WHERE event_kind IS NOT NULL")


def test_protected_indexes_come_from_both_lists():
    prot = H.protectedIndexes()
    names = {(t, n) for t, n, _s in prot}
    assert ("ranking_results", "rr_person_idx") in names                  # _CANONICAL_INDEXES
    assert ("athlete_season", "as_board_mean_idx") in names
    assert ("ranking_results", "idx_rr_person_cover") in names            # both lists
    assert ("results", "idx_results_div") in names                        # add_page_indexes.WANTED
    assert all(s and " " not in s for _t, _n, s in prot)


def test_the_site_closure_borrows_helpers_from_builders_without_becoming_them():
    mods = H.siteModules()
    assert mods["app"][1] is None and mods["rankings"][1] is None and mods["database"][1] is None
    # app.py imports one helper from the ranking builder; the builder is not the site
    if "build_ranking_results" in mods:
        assert mods["build_ranking_results"][1] is not None
        assert "timedEventKind" in mods["build_ranking_results"][1]
    words, _pre, other = H.codeReferences()
    assert "ranking_results" in words and "athlete_season" in words
    assert any(f.endswith(".sh") for fs in other.values() for f in fs)


def test_unread_tables_verdicts():
    rows = H.unreadTables(
        [("ranking_results", 30 * H.GB, 61_000_000, 0, 9),
         ("board_cache_xc", 2 * H.GB, 10, 5, 5),
         ("pipeline_scratch", 3 * H.GB, 1000, 1000, 2),
         ("orphan", 1 * H.GB, 10, 0, 0),
         ("ranking_results_old", 1024, 1, 0, 0),
         ("tiny", 1024, 1, 0, 0)],
        site_words={"ranking_results"}, site_prefixes={"board_cache_"},
        other={"pipeline_scratch": ["racecast/build_x.py"]}, min_bytes=64 * H.MB)
    by = {r[0]: r for r in rows}
    assert "ranking_results" not in by and "tiny" not in by
    assert by["board_cache_xc"][6].startswith("maybe read")
    assert by["pipeline_scratch"][6].startswith("pipeline-only") and "build_x" in by["pipeline_scratch"][7]
    assert by["orphan"][6] == "named by no code at all"
    assert by["ranking_results_old"][6].startswith("leftover")
    assert [r[0] for r in rows][0] == "pipeline_scratch"                  # biggest first


def test_memory_budget_counts_the_worst_case():
    rows = dict(H.memoryBudget({"shared_buffers": "16GB", "work_mem": "256MB",
                                "hash_mem_multiplier": "2", "autovacuum_max_workers": "3",
                                "maintenance_work_mem": "4GB"}))
    site = [v for k, v in rows.items() if k.startswith("site")][0]
    assert site == 72 * 2 * 256 * H.MB * 2                                # 72 GB at today's 256MB
    assert rows["total (every budget at once)"] == sum(v for k, v in rows.items() if not k.startswith("total"))


@pytest.mark.parametrize("major", [14, 15, 16, 17, 18])
def test_the_psql_script_renders_for_every_version(major):
    sql = H.sqlScript(major * 10000, H.protectedIndexes())
    for sec in "abcdefg":
        assert f"== ({sec})" in sql
    assert "{" not in re.sub(r"'[^']*'", "", sql)           # no unformatted placeholder outside strings
    assert "default_transaction_read_only = on" in sql
    assert ("pg_stat_checkpointer" in sql) == (major >= 17)
    assert ("read_bytes" in sql) == (major >= 18)
    assert ("last_idx_scan" in sql) == (major >= 16)


def test_nothing_in_the_report_writes():
    src = open(os.path.join(_ROOT, "scripts", "db_health.py")).read()
    sql = H.sqlScript(180000, H.protectedIndexes())
    # every statement is a SELECT (WITH ... SELECT) or a session SET; psql
    # meta-commands aside
    for stmt in re.sub(r"'[^']*'", "''", sql).split(";"):
        lines = [ln for ln in stmt.strip().splitlines() if ln.strip() and not ln.lstrip().startswith("\\")]
        if not lines:
            continue
        first = lines[0].split()[0].upper()
        assert first in ("SELECT", "WITH", "SET"), lines[0]
        if first == "SET":
            assert re.fullmatch(r"SET (default_transaction_read_only|statement_timeout|lock_timeout|jit) = .*",
                                lines[0].strip()), lines[0]
    assert "pgstattuple" not in src.split('"""', 2)[2]      # no full-scan functions past the docstring


# ---------------------------------------------------------------- on a real Postgres

@pytest.fixture
def pg():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the report")
    import psycopg2
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS t_dbhealth CASCADE; CREATE SCHEMA t_dbhealth")
    cur.execute("""
        CREATE TABLE t_dbhealth.dbh_rr (id bigint PRIMARY KEY, pool text, school text, sport text,
                                        rating real, pad text) WITH (autovacuum_enabled = false);
        INSERT INTO t_dbhealth.dbh_rr
        SELECT g, CASE WHEN g % 3 = 0 THEN 'hs_m' ELSE 'hs_f' END, 'S' || (g % 500),
               CASE WHEN g % 5 = 0 THEN 'TF' ELSE 'XC' END, random() * 1000, md5(g::text)
        FROM generate_series(1, 20000) g;
        CREATE INDEX dbh_school ON t_dbhealth.dbh_rr (school);
        CREATE INDEX dbh_school_sport ON t_dbhealth.dbh_rr (school, sport, rating DESC);
        CREATE INDEX dbh_pool_a ON t_dbhealth.dbh_rr (pool, rating);
        CREATE INDEX dbh_pool_b ON t_dbhealth.dbh_rr (pool, rating);
        CREATE UNIQUE INDEX dbh_uniq_pool ON t_dbhealth.dbh_rr (pool, id);
        ANALYZE t_dbhealth.dbh_rr;
        CREATE TABLE t_dbhealth.dbh_never (id int);
        INSERT INTO t_dbhealth.dbh_never SELECT generate_series(1, 1000);
    """)
    yield dsn, conn
    cur.execute("DROP SCHEMA IF EXISTS t_dbhealth CASCADE")
    conn.close()


def test_the_report_runs_read_only_on_postgres(pg):
    dsn, _conn = pg
    import psycopg2
    conn = psycopg2.connect(dsn)
    out = io.StringIO()
    rep = H.Report(conn, out=out, min_mb=0, rare=0)
    for s in H.SECTIONS:
        getattr(rep, s)()
    text = out.getvalue()
    # (b) the prefix and the exact duplicate, and never the unique index
    assert re.search(r"dbh_rr\s+dbh_school\s+t_dbhealth\.dbh_school_sport\s+prefix", text)
    assert re.search(r"dbh_pool_[ab]\s+t_dbhealth\.dbh_pool_[ab]\s+same columns", text)
    assert not re.search(r"dbh_uniq_pool\s+\S+\s+(prefix|same)", text)
    # (a) an unscanned plain index is a drop candidate; PK/unique are not listed
    assert re.search(r"dbh_rr\s+dbh_pool_a\s.*DROP CANDIDATE", text)
    assert "dbh_rr_pkey" not in text.split("== (b)")[0] and "dbh_uniq_pool" not in text.split("== (b)")[0]
    # (c) the reloption and the missing statistics are flagged
    assert re.search(r"dbh_rr\s.*autovacuum OFF", text)
    assert re.search(r"dbh_never\s.*NO PLANNER STATS", text)
    # (g) the recommendations joined with the server's own values
    assert re.search(r"wal_compression\s+\S+\s+lz4", text)
    assert "XCP_DB_WORK_MEM" in text
    skipped = re.findall(r"\(skipped: ([^)]*)\)", text)
    assert not skipped, skipped
    # and the session could not have written
    with conn.cursor() as cur:
        with pytest.raises(psycopg2.Error, match="read-only"):
            cur.execute("CREATE TABLE t_dbhealth.nope (x int)")
    conn.close()


def test_the_psql_script_runs_clean_on_postgres(pg):
    dsn, conn = pg
    psql = shutil.which("psql")
    if not psql:
        pytest.skip("no psql")
    with conn.cursor() as cur:
        cur.execute("SHOW server_version_num")
        ver = int(cur.fetchone()[0])
    sql = H.sqlScript(ver // 10000 * 10000, H.protectedIndexes(), min_mb=0, rare=0)
    res = subprocess.run([psql, "-X", "-P", "pager=off", "-v", "ON_ERROR_STOP=1", dsn],
                         input=sql, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[-2000:]
    assert "ERROR" not in res.stderr
    assert "dbh_school" in res.stdout and "wal_compression" in res.stdout
