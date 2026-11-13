"""racecast/predict._historyShared (2026-11-12; run 20261112_173033's
10f2_projection: KeyError 'norm_unshifted'): a runner whose rows do not all
carry the same keys is returned as read and not cached; the others are
cached and come back the same."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "racecast"), os.path.join(ROOT, "scripts"),
                os.path.join(ROOT, "engine")]
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
P = pytest.importorskip("predict")


def test_mixed_keys_are_returned_and_not_cached(monkeypatch):
    import sharedcache
    stored = {}
    monkeypatch.setattr(P, "_historyStamp", lambda cur, sport: "s1")
    import types
    monkeypatch.setattr(P, "_fx", lambda: types.SimpleNamespace(MIN_NORMALIZED_TIME=0))
    monkeypatch.setattr(sharedcache, "peekKeys",
                        lambda base, keys, ttl=None: {k: stored[k] for k in keys if k in stored})
    monkeypatch.setattr(sharedcache, "putKeys", lambda base, vals: stored.update(vals))
    rows = [{"person_id": 1, "t": 1.0, "norm_unshifted": True}, {"person_id": 1, "t": 2.0},
            {"person_id": 2, "t": 3.0}, {"person_id": 2, "t": 4.0}]
    got = P._historyShared(None, "XC", 0, [1, 2], False, "pool", lambda c, s, h, ids: list(rows))
    assert got == rows
    assert set(stored) == {2}
    again = P._historyShared(None, "XC", 0, [2], False, "pool", lambda *a: pytest.fail("read again"))
    assert again == rows[2:]
