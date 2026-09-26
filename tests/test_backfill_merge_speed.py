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

import backfill_normalize as B                                  # noqa: E402


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
