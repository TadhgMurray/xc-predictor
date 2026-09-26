# Project: xc-predictor / tests
# File:    test_db_quiet.py
# Purpose: the pipeline yields to the site inside Postgres (owner,
#          2026-09-14): under XCP_DB_QUIET every pipeline connection is
#          capped, the builders' own SETs go through the caps, and the
#          pipeline runs fewer streams. No database.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_db_quiet.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database as db                                          # noqa: E402
import dbfast                                                  # noqa: E402
import merge_column as mc                                      # noqa: E402


def test_the_caps_apply_only_in_quiet_mode(monkeypatch):
    monkeypatch.delenv("XCP_DB_QUIET", raising=False)
    assert not db.dbQuiet()
    assert db.dbSetting("maintenance_work_mem", "8GB") == "8GB"
    assert db.dbJobs(3) == 3
    # the balanced default (2026-09-26): room to work, still bounded
    monkeypatch.setenv("XCP_DB_QUIET", "1")
    assert db.dbQuiet()
    assert db.dbSetting("maintenance_work_mem", "8GB") == "2GB"
    assert db.dbSetting("work_mem", "2GB") == "512MB"
    assert db.dbSetting("max_parallel_maintenance_workers", 6) == "2"
    assert db.dbSetting("max_parallel_workers_per_gather", 4) == "2"
    assert db.dbSetting("cursor_tuple_fraction", "0.1") == "1.0"
    assert db.dbSetting("lock_timeout", "5s") == "5s"        # not a cap: untouched
    assert db.dbJobs(3) == 2
    assert db.dbJobs(1) == 1
    # the old site-first caps, on request
    monkeypatch.setenv("XCP_DB_QUIET", "strict")
    assert db.dbQuiet()
    assert db.dbSetting("maintenance_work_mem", "8GB") == "512MB"
    assert db.dbSetting("work_mem", "2GB") == "256MB"
    assert db.dbSetting("max_parallel_maintenance_workers", 6) == "0"
    assert db.dbSetting("max_parallel_workers_per_gather", 4) == "0"
    assert db.dbJobs(3) == 1


class _Cur:
    def __init__(self, log): self.log = log
    def execute(self, sql, params=None): self.log.append((sql, params))
    def fetchone(self): return (4242,)
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self): self.log = []; self.commits = 0
    def cursor(self): return _Cur(self.log)
    def commit(self): self.commits += 1
    def rollback(self): pass


def test_a_quiet_connection_is_capped_once_and_the_backend_is_reniced_best_effort(monkeypatch):
    monkeypatch.setenv("XCP_DB_QUIET", "1")
    monkeypatch.setattr(db, "_QUIET_SAID", False)
    monkeypatch.setattr(db, "_localServer", lambda: True)
    seen = {}
    monkeypatch.setattr(os, "setpriority", lambda which, pid, nice: seen.setdefault("nice", (pid, nice)))
    conn = _Conn()
    db._quietTune(conn)
    sets = [p[0] for sql, p in conn.log if sql.startswith("SET ") and p]
    assert "512MB" in sets and "2GB" in sets and "2" in sets and "off" in sets
    assert "1.0" in sets                               # cursor_tuple_fraction
    assert any("application_name" in sql for sql, _ in conn.log)
    assert seen["nice"] == (4242, db._QUIET_NICE)
    assert conn.commits == 1


def test_a_denied_renice_is_survived(monkeypatch):
    monkeypatch.setenv("XCP_DB_QUIET", "1")
    monkeypatch.setattr(db, "_QUIET_SAID", False)
    monkeypatch.setattr(db, "_localServer", lambda: True)
    def deny(*a):
        raise PermissionError("no CAP_SYS_NICE")
    monkeypatch.setattr(os, "setpriority", deny)
    db._quietTune(_Conn())                       # no raise


def test_the_builders_ask_through_the_caps(monkeypatch):
    monkeypatch.setenv("XCP_DB_QUIET", "1")
    assert mc._dbSetting("maintenance_work_mem", "8GB") == "2GB"
    conn = _Conn()
    applied = dbfast.tuneSession(conn, quiet=True)
    assert "work_mem=512MB" in applied and "max_parallel_maintenance_workers=2" in applied
    monkeypatch.delenv("XCP_DB_QUIET")
    assert mc._dbSetting("maintenance_work_mem", "8GB") == "8GB"
    src = open(os.path.join(_ROOT, "engine", "merge_column.py")).read()
    assert "SET LOCAL maintenance_work_mem = '8GB'" not in src
    src = open(os.path.join(_ROOT, "racecast", "build_ranking_results.py")).read()
    assert "SET maintenance_work_mem = '2GB'" not in src
    assert "_INDEX_JOBS = dbJobs(3)" in src


def test_the_pipeline_turns_quiet_on_and_runs_four_streams():
    sh = open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")).read()
    assert 'export XCP_DB_QUIET="${XCP_DB_QUIET:-1}"' in sh
    assert 'XCP_STREAMS="${XCP_STREAMS:-4}"' in sh
    assert '"$inflight" -ge "$XCP_STREAMS"' in sh
    assert 'shards 12b_course_pages "$XCP_COURSE_SHARDS"' in sh
    assert sh.index('export XCP_DB_QUIET') < sh.index('step 01_season_year')


def test_the_school_identity_builder_reads_seasons_not_the_boards_table():
    # 2026-09-15: seven full scans of the 23 GB boards table were the
    # gateway timeouts; the per-season table answers the same questions
    src = open(os.path.join(_ROOT, "racecast", "build_school_identity.py")).read()
    body = src[src.index("def mergeCoRacingClusters"):]
    scans = [ln for ln in body.splitlines() if "FROM   ranking_results" in ln or "FROM ranking_results" in ln]
    assert len(scans) <= 2, scans                     # the two meet reads, both filtered by school
    assert "FROM   athlete_season" in body
    assert src.count("conn.commit()") >= 5


def test_an_interrupted_run_ends_its_backends_and_the_boards_are_vacuumed_last():
    sh = open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")).read()
    assert "trap _cleanup INT TERM" in sh
    assert "db_activity.py --kill-pipeline" in sh
    assert sh.index("trap _cleanup") < sh.index("step 01_season_year")
    assert sh.index("step 17_checklist") < sh.index("step 18_vacuum") < sh.rindex("\nsummarise")
    src = open(os.path.join(_ROOT, "racecast", "build_ranking_results.py")).read()
    assert "WITH (autovacuum_enabled = false)" in src
    import db_activity
    assert db_activity.PIPELINE_APP == "xcp-pipeline"
    assert "'xcp-pipeline'" in open(os.path.join(_ROOT, "scripts", "database.py")).read()
