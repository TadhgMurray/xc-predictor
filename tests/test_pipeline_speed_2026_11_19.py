"""The 2026-11-19 speed-ups change when things run, never what comes out.

  * 05_backfill: the stream read ahead and the write behind
    (_drainOverlapped) hand row_fn the same rows in the same order and
    _flush the same batches in the same order as the plain loop.
  * 09d: the rules are submitted longest first, and collect() still joins
    their answers in EXTRA_RULES order.

    python -m pytest -q tests/test_pipeline_speed_2026_11_19.py
"""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backfill", "engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: (
        {} if not name.startswith("__") else getattr(object(), name))
    sys.modules["corrections"] = _c
_stub = sys.modules.get("database")             # see test_backfill_lookup_locks
if _stub is not None and not hasattr(_stub, "dbJobs"):
    del sys.modules["database"]
import backfill_normalize as B                   # noqa: E402
import rating_outliers as RO                     # noqa: E402
if _stub is not None:
    sys.modules["database"] = _stub


class _Stream:
    def __init__(self, rows):
        self.rows, self.i = rows, 0

    def __iter__(self):
        return iter(self.rows)

    def fetchmany(self, n):
        out = self.rows[self.i:self.i + n]
        self.i += len(out)
        return out


class _Census:
    def __init__(self):
        self.seen = []

    def record(self, value, reason, trace):
        self.seen.append((value, reason))


def _run(monkeypatch, overlap, apply):
    monkeypatch.setenv("XCP_BACKFILL_OVERLAP", overlap)
    monkeypatch.setattr(B, "_Census", _Census)
    flushed = []
    monkeypatch.setattr(B, "_flush", lambda conn, target, u, mode:
                        flushed.append(list(u)) or len(u))
    monkeypatch.setattr(B, "_printProgress", lambda *a: None)
    rows = [(i,) for i in range(1, 24)]
    row_fn = lambda r: (r[0], None if r[0] % 4 == 0 else r[0] * 1.5,  # noqa: E731
                        "skip" if r[0] % 4 == 0 else "written", None)
    cfg = types.SimpleNamespace(sport="XC", batch=5, write_mode="update")
    p, w, s, census = B._drainStream(_Stream(rows), None, cfg, row_fn, apply, 0, "results")
    return p, w, s, census.seen, flushed


def test_the_overlapped_drain_is_the_loop(monkeypatch):
    for apply in (True, False):
        old = _run(monkeypatch, "0", apply)
        new = _run(monkeypatch, "1", apply)
        assert new == old
        assert old[0] == 23 and old[2] == 5


def test_rules_start_longest_first_and_join_in_table_order(monkeypatch):
    started = []
    monkeypatch.setattr(RO.ExtraRules, "_work",
                        lambda self, label, fn: started.append(label) or [(label,)])
    rules = RO.ExtraRules(cur=None, connect=lambda: None, streams=1 + 1, verbose=False)
    got = rules.collect()
    assert [r[0] for r in got] == [label for label, _fn in RO.EXTRA_RULES]
    assert sorted(started) == sorted(label for label, _fn in RO.EXTRA_RULES)
    assert RO._longFirst(RO.EXTRA_RULES)[0][0] == "race"
    monkeypatch.setenv("XCP_OUTLIER_RULES_LONG_FIRST", "0")
    assert RO._longFirst(RO.EXTRA_RULES) == list(RO.EXTRA_RULES)
