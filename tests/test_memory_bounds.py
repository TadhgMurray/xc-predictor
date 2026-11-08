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


# ---- the front page and /scale rendered once for the box (2026-11-08) ----

def _app():
    os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
    import app as A
    return A


def test_shared_page_key_carries_what_the_templates_read():
    A = _app()
    keys = {}
    for url, host in (("/", "racecast.co"), ("/?utm_source=x", "racecast.co"),
                      ("/?r=1", "racecast.co"), ("/", "127.0.0.1:8000")):
        with A.app.test_request_context(url, base_url=f"https://{host}"):
            keys[(url, host)] = A.sharedPageKey("home")
    assert keys[("/", "racecast.co")] == keys[("/?utm_source=x", "racecast.co")]
    assert keys[("/?r=1", "racecast.co")] != keys[("/", "racecast.co")]
    assert keys[("/", "127.0.0.1:8000")] != keys[("/", "racecast.co")]


def test_the_front_page_renders_once_and_is_shared(monkeypatch, tmp_path):
    A = _app()
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    calls = []

    def fake():
        calls.append(1)
        return "<html>front page %d</html>" % len(calls)
    monkeypatch.setattr(A, "_renderHome", fake)
    c = A.app.test_client()
    a = c.get("/").get_data(as_text=True)
    b = c.get("/").get_data(as_text=True)
    assert a == b == "<html>front page 1</html>" and len(calls) == 1


def test_the_shared_directory_is_swept_of_dead_files(tmp_path, monkeypatch):
    import sharedcache
    d = str(tmp_path / "c")
    sharedcache._safeDir(d)
    old = os.path.join(d, "search-dead.pkl")
    fresh = os.path.join(d, "search-live.pkl")
    for p in (old, fresh):
        open(p, "wb").close()
    t = 10_000_000.0
    os.utime(old, (t - sharedcache.SWEEP_AGE_ANY - 10,) * 2)
    os.utime(fresh, (t - 60,) * 2)
    monkeypatch.setattr(sharedcache, "_last_sweep", [0.0])
    sharedcache.get("k", lambda: 1, ttl=60, now=lambda: t, path=d)
    assert not os.path.exists(old) and os.path.exists(fresh)
