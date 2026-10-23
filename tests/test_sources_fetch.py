"""The shared fetcher for timing-company sources: parallel across hosts,
slow per host, robots.txt honoured, every page kept (sources/fetch.py).

    python -m pytest -q tests/test_sources_fetch.py

Owner, 2026-10-23: "a distributed system so we can spread load so we don't
get banned ... reducing load/speed-up by parallelizing".
"""
import asyncio
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sources import fetch as F                                    # noqa: E402


class Resp:
    def __init__(self, status, body=b"", headers=None, text=""):
        self.status_code, self.content = status, body
        self.headers = headers or {}
        self.text = text or body.decode("utf-8", "replace")


class AllowAll:
    def get(self, url):
        return True, None


def _fetcher(tmp_path, getter, interval=0.2, robots=None, in_flight=16):
    store = F.RawStore(str(tmp_path))
    lim = F.HostLimiter(interval=interval, rand=lambda: 0.5)   # no jitter
    return F.Fetcher(store, limiter=lim, robots=robots or AllowAll(),
                     getter=getter, max_in_flight=in_flight), store


def test_hosts_run_in_parallel_and_each_host_waits(tmp_path):
    starts = {}

    def getter(url, headers):
        starts.setdefault(F.hostOf(url), []).append(time.monotonic())
        return Resp(200, url.encode())

    f, _ = _fetcher(tmp_path, getter)
    urls = [f"https://timer{h}.example/meet/{i}" for h in range(4) for i in range(3)]
    t0 = time.monotonic()
    out = asyncio.run(f.fetchAll(urls))
    took = time.monotonic() - t0
    assert all(r.status == 200 for r in out)
    # 3 requests a host at 0.2 s apart: ~0.4 s, not 12 x 0.2 = 2.4 s serial
    assert took < 1.2
    for host, ts in starts.items():
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        assert all(g >= 0.18 for g in gaps), (host, gaps)


def test_a_second_visit_is_conditional_and_a_304_reuses_the_stored_page(tmp_path):
    seen = []

    def getter(url, headers):
        seen.append(dict(headers))
        if "If-None-Match" in headers:
            return Resp(304)
        return Resp(200, b"<pre>results</pre>", {"ETag": '"v1"'})

    f, store = _fetcher(tmp_path, getter, interval=0.0)
    first = asyncio.run(f.fetch("https://t.example/m/1"))
    second = asyncio.run(f.fetch("https://t.example/m/1"))
    assert first.status == 200 and first.changed
    assert second.status == 304 and second.body == b"<pre>results</pre>"
    assert seen[1]["If-None-Match"] == '"v1"'
    assert store.body("https://t.example/m/1") == b"<pre>results</pre>"


def test_a_fresh_stored_page_is_not_fetched_again(tmp_path):
    calls = []

    def getter(url, headers):
        calls.append(url)
        return Resp(200, b"x")

    f, _ = _fetcher(tmp_path, getter, interval=0.0)
    asyncio.run(f.fetch("https://t.example/m/2"))
    again = asyncio.run(f.fetch("https://t.example/m/2", max_age=3600))
    assert calls == ["https://t.example/m/2"] and again.note == "fresh in store"


def test_robots_disallow_is_honoured_and_crawl_delay_slows_the_host(tmp_path):
    robots_txt = "User-agent: *\nDisallow: /private/\nCrawl-delay: 9\n"
    robots = F.Robots(getter=lambda u: Resp(200, robots_txt.encode()))
    calls = []
    f, _ = _fetcher(tmp_path, lambda u, h: (calls.append(u), Resp(200, b"x"))[1],
                    interval=0.0, robots=robots)
    blocked = asyncio.run(f.fetch("https://t.example/private/meet"))
    ok = asyncio.run(f.fetch("https://t.example/results/meet"))
    assert blocked.status == 0 and "robots" in blocked.note
    assert ok.status == 200 and calls == ["https://t.example/results/meet"]
    assert f.limiter.intervalFor("t.example") == 9.0


def test_robots_unreachable_or_5xx(tmp_path):
    assert F.Robots(getter=lambda u: Resp(404)).get("https://a.example/x") == (True, None)
    assert F.Robots(getter=lambda u: Resp(503)).get("https://a.example/x") == (False, None)


def test_a_429_doubles_that_hosts_interval(tmp_path):
    f, _ = _fetcher(tmp_path, lambda u, h: Resp(429), interval=0.05)
    asyncio.run(f.fetch("https://busy.example/m"))
    assert f.limiter.intervalFor("busy.example") == 0.1
    assert f.limiter.intervalFor("other.example") == 0.05


def test_a_host_on_the_error_allow_list_is_crawled_gently(tmp_path):
    r = F.Robots(getter=lambda u: Resp(500), allow_on_error={"results.yentiming.com"})
    assert r.get("https://results.yentiming.com/2026/cross/") == (True, 2 * F.DEFAULT_INTERVAL)
    assert r.get("https://other.example/x") == (False, None)
    assert "results.yentiming.com" in F.ROBOTS_ERROR_ALLOW


def test_gentle_defaults():
    assert F.DEFAULT_INTERVAL >= 10
