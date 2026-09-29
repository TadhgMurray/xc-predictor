"""A division drop reaches the other feed's copy of the same race when that
copy carries the same label (owner, 2026-09-29: "a drop or fix on one doesn't
reach the other"; NCAA DI 2025's tfrrs division dropped, its anet copy rated)."""
import os
import sys
import types

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: {}
    sys.modules["corrections"] = _c
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import pytest                                                     # noqa: E402
import backfill_normalize as B                                    # noqa: E402


def _row(rid, src, meet, div, person, canon):
    r = [None] * 13
    r[B._ID], r[B._SRC], r[B._MEET], r[B._DIV] = rid, src, meet, div
    r[B._TIME], r[B._PERSON], r[B._CANON] = 1790.0, person, canon
    r[B._AID], r[B._DATE] = 5, "2025-11-22"
    return tuple(r)


def test_the_copy_on_the_same_label_is_dropped_and_a_different_label_is_not(monkeypatch):
    # step 2a answers "insane" for every row, so a row that gets PAST the twin
    # check comes back INSANE_DISTANCE and one stopped by it NO_DISTANCE
    monkeypatch.setattr(B, "_distanceSane", lambda _d: False)
    monkeypatch.setattr(B, "_makeTrace", lambda *a, **k: None)
    for name in ("_RESULT_DROP_BY_SPORT", "_GENDER_OVERRIDES_BY_SPORT",
                 "_RESULT_OVERRIDE_BY_SPORT", "_DISTANCE_DROP_BY_SPORT",
                 "_DISTANCE_OVERRIDES_BY_SPORT"):
        monkeypatch.setattr(B, name, {"XC": {}, "TF": {}}, raising=False)
    cfg = B._configFor("XC", "write") if hasattr(B, "_configFor") else None
    if cfg is None:
        pytest.skip("no XC config")
    fn = B._makeRowFn(cfg, None, {}, ({}, {}, {}), {900: 10000.0, 901: 8000.0}, {},
                      set(), {}, None, dropped_twins={(7, 55): 10000.0})
    rid, nt, why, _t = fn(_row(1, "anet", 27000, 900, 7, 55))
    assert nt is None and why == B._SkipReason.NO_DISTANCE
    # a different label is its own evidence; another runner is untouched
    assert fn(_row(2, "anet", 27000, 901, 7, 55))[2] == B._SkipReason.INSANE_DISTANCE
    assert fn(_row(3, "anet", 27000, 900, 8, 55))[2] == B._SkipReason.INSANE_DISTANCE


def test_the_loader_on_a_scratch_database(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS results;
        CREATE TABLE results (result_id bigint, person_id bigint, canon_meet_id bigint,
                              meet_id bigint, div_id bigint, source text);
        INSERT INTO results VALUES
          (1, 7, 55, 27301, 1, 'tfrrs'), (2, 7, 55, 253200, 900, 'anet'),
          (3, 8, 55, 27301, 1, 'tfrrs'), (4, 9, NULL, 27301, 1, 'tfrrs');
    """)
    monkeypatch.setattr(B, "_DISTANCE_DROP_BY_SPORT", {"XC": {(27301, 1)}}, raising=False)
    got = B._loadDroppedTwins(cur, "results", "XC", {900: 10000.0},
                              {(27301, 1): {"distance": 10000.0}})
    conn.rollback()
    assert got == {(7, 55): 10000.0, (8, 55): 10000.0}
