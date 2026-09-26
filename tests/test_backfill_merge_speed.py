"""The backfill merge's speed-ups must not change what a failed run leaves.

★ 2026-09-26: the secondary indexes of <table>_new are built several at a
  time on their own connections, so <table>_new is committed before them.
  A failed build must still leave the live table untouched and no
  <table>_new behind -- the state a rolled-back merge always left.

  No database: the builders and the connection are fakes that record.

    python -m pytest -q tests/test_backfill_merge_speed.py
"""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: {}
    sys.modules["corrections"] = _c

import pytest                                                   # noqa: E402

# ! THE REAL database MODULE, NOT ANOTHER TEST'S STAND-IN (2026-09-26). In a
#   whole-suite run test_accounts.py has already put a two-function stub in
#   sys.modules["database"], and backfill_normalize now also imports dbJobs
#   and dbSetting from it. Load the real one for this import, then put the
#   stub back so the tests after this one see the world as they left it.
_stub = sys.modules.get("database")
if _stub is not None and not hasattr(_stub, "dbJobs"):
    del sys.modules["database"]
import backfill_normalize as B                                  # noqa: E402
if _stub is not None:
    sys.modules["database"] = _stub


class _Conn:
    def __init__(self, log):
        self.log = log

    def commit(self):
        self.log.append("COMMIT")

    def rollback(self):
        self.log.append("ROLLBACK")


class _Cur:
    def __init__(self, log):
        self.log = log
        self.connection = _Conn(log)

    def execute(self, sql, params=None):
        self.log.append(" ".join(sql.split()))


_DEFS = [(f"idx_t_{i}", f"CREATE INDEX idx_t_{i}_new ON public.t_new USING "
          f"btree (c{i})") for i in range(6)]


def test_the_heap_and_pk_commit_before_any_builder_runs(monkeypatch):
    log = []
    cur = _Cur(log)

    def build(job):
        log.append(f"build {job[0]}")
        return job[0], 0.0

    monkeypatch.setattr(B, "_buildOneIndex", build)
    B._buildIndexes(cur, "t", _DEFS)
    pk = next(i for i, e in enumerate(log) if "ADD PRIMARY KEY" in e)
    first_build = next(i for i, e in enumerate(log) if e.startswith("build"))
    assert "COMMIT" in log[pk:first_build], log
    assert sorted(e for e in log if e.startswith("build")) == \
        sorted(f"build {n}" for n, _ in _DEFS)
    assert not any("DROP" in e for e in log)


def test_a_failed_build_drops_the_new_table_and_raises(monkeypatch):
    log = []
    cur = _Cur(log)

    def build(job):
        if job[0] == "idx_t_2":
            raise RuntimeError("disk full")
        return job[0], 0.0

    monkeypatch.setattr(B, "_buildOneIndex", build)
    with pytest.raises(RuntimeError, match="disk full"):
        B._buildIndexes(cur, "t", _DEFS)
    assert "DROP TABLE IF EXISTS t_new" in log
    assert not any("DROP TABLE IF EXISTS t " in e or e.endswith("DROP TABLE t")
                   for e in log)                     # the live table: never


def test_the_builders_ask_no_more_than_the_quiet_caps(monkeypatch):
    monkeypatch.setenv("XCP_DB_QUIET", "1")
    log = []
    cur = _Cur(log)
    monkeypatch.setattr(B, "_buildOneIndex", lambda job: (job[0], 0.0))
    B._buildIndexes(cur, "t", _DEFS)
    assert "SET maintenance_work_mem = '2GB'" in log
    assert "SET max_parallel_maintenance_workers = 2" in log


# ---- the rewrite-only-changed path (2026-09-26) ---------------------------
# Its SQL was checked against the rebuild on a scratch PG16 cluster (the same
# bytes on every row, 0/-0 and NaN included); these pin the control flow: any
# doubt hands over to the rebuild with nothing written.

class _RwCur(_Cur):
    def __init__(self, log, answers, fail_on=None):
        super().__init__(log)
        self.answers, self.fail_on, self.rowcount = answers, fail_on, 0

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.log.append(flat)
        if self.fail_on and self.fail_on in flat:
            import psycopg2.errors
            raise psycopg2.errors.CheckViolation("nt_pos")
        if flat.startswith("CREATE TEMP TABLE bf_changed"):
            self.rowcount = self.answers["exact"]
        elif flat.startswith("UPDATE"):
            self.rowcount = self.answers.get("updated", self.answers["exact"])

    def fetchone(self):
        if "pg_trigger" in self.log[-1]:
            return (self.answers.get("triggers", 0),)
        return (self.answers["sample_hits"],)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RwConn(_Conn):
    def __init__(self, log, cur):
        super().__init__(log)
        self._cur = cur

    def cursor(self):
        return self._cur


def _rewrite(answers, fail_on=None):
    log = []
    cur = _RwCur(log, answers, fail_on)
    ok = B._rewriteChangedRows(_RwConn(log, cur), "results", "bf_staging_xc")
    return ok, log


def test_few_changes_are_updated_in_place_and_staging_dropped():
    ok, log = _rewrite({"sample_hits": 3, "exact": 543})
    assert ok
    assert any(e.startswith("UPDATE results r SET normalized_time = c.nt")
               for e in log)
    assert log.index("COMMIT") < log.index("DROP TABLE IF EXISTS bf_staging_xc")
    assert "ROLLBACK" not in log


def test_the_diff_is_bitwise_and_null_aware():
    _ok, log = _rewrite({"sample_hits": 0, "exact": 1})
    diff = next(e for e in log if e.startswith("CREATE TEMP TABLE bf_changed"))
    assert ("float4send(r.normalized_time) IS DISTINCT FROM float4send(s.nt)"
            in diff)
    assert "LEFT JOIN bf_staging_xc s" in diff        # unstaged -> NULL
    assert f"LIMIT {B._REWRITE_MAX_ROWS + 1}" in diff


def test_a_big_estimate_rebuilds_without_the_exact_diff():
    ok, log = _rewrite({"sample_hits": B._REWRITE_MAX_ROWS, "exact": 1})
    assert not ok
    assert not any(e.startswith(("CREATE TEMP", "UPDATE", "DROP")) for e in log)
    assert log[-1] == "ROLLBACK"


def test_a_big_exact_count_rebuilds_with_nothing_written():
    ok, log = _rewrite({"sample_hits": 0, "exact": B._REWRITE_MAX_ROWS + 1})
    assert not ok
    assert not any(e.startswith(("UPDATE", "DROP")) for e in log)
    assert "COMMIT" not in log


def test_a_trigger_or_an_error_hands_over_to_the_rebuild():
    ok, log = _rewrite({"sample_hits": 0, "exact": 5, "triggers": 1})
    assert not ok and not any(e.startswith("UPDATE") for e in log)
    ok, log = _rewrite({"sample_hits": 0, "exact": 5}, fail_on="UPDATE results")
    assert not ok and "COMMIT" not in log and log[-1] == "ROLLBACK"
    ok, log = _rewrite({"sample_hits": 0, "exact": 5, "updated": 4})
    assert not ok and "COMMIT" not in log
