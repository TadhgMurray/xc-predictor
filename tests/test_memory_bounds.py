"""Per-worker memory bounds (2026-11-08: on 11-07 at 23:28 the 24 site
workers held 80.5 GB and the OOM killer took Postgres)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import ttlcache                                                   # noqa: E402


def test_ttlcache_is_bounded_by_bytes(monkeypatch):
    ttlcache.clear()
    monkeypatch.setattr(ttlcache, "_MAX_BYTES", 3000)
    t = [100.0]
    now = lambda: t[0]                                          # noqa: E731
    for i in range(5):
        t[0] += 1
        ttlcache.get(("k", i), lambda: "x" * 900, ttl=3600, now=now)
    assert ttlcache._total[0] <= 3000
    held = [k for k in ttlcache._store]
    assert ("k", 4) in held and ("k", 0) not in held           # the oldest went
    assert ttlcache._total[0] == sum(ttlcache._sizes.values())
    assert set(ttlcache._sizes) == set(ttlcache._store)
    ttlcache.clear()


def test_ttlcache_hands_back_but_does_not_keep_a_huge_value(monkeypatch):
    ttlcache.clear()
    monkeypatch.setattr(ttlcache, "_MAX_ONE", 1000)
    v, _ = ttlcache.get("big", lambda: "y" * 5000)
    assert len(v) == 5000 and "big" not in ttlcache._store
    ttlcache.clear()


def test_ttlcache_replacing_a_key_does_not_double_count(monkeypatch):
    ttlcache.clear()
    t = [100.0]
    now = lambda: t[0]                                          # noqa: E731
    ttlcache.get("a", lambda: "z" * 500, ttl=1, now=now)
    one = ttlcache._total[0]
    t[0] += 5                                                   # expired: recomputed
    ttlcache.get("a", lambda: "z" * 500, ttl=1, now=now)
    assert ttlcache._total[0] == one
    ttlcache.clear()


def test_an_evicted_key_takes_its_lock_with_it(monkeypatch):
    ttlcache.clear()
    monkeypatch.setattr(ttlcache, "_MAX_BYTES", 1500)
    for i in range(10):
        ttlcache.get(("l", i), lambda: "w" * 600)
    assert len(ttlcache._locks) <= len(ttlcache._store) + 1
    ttlcache.clear()
